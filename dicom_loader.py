"""Read, validate, sort, and convert an axis-aligned CT DICOM series."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Iterable
from zipfile import BadZipFile, ZipFile

import numpy as np
from numpy.typing import NDArray
import pydicom
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError


class DicomLoadError(ValueError):
    """A user-facing error raised when a series cannot be safely displayed."""


@dataclass(frozen=True)
class DicomVolume:
    volume: NDArray[np.float32]
    spacing_zyx: tuple[float, float, float]
    slice_positions: NDArray[np.float64]
    series_uid: str
    initial_wl: int
    initial_ww: int


def _first_number(value: object, default: float) -> float:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        value = value[0]
    try:
        return float(value)  # pydicom MultiValue also supports indexing only
    except (TypeError, ValueError):
        try:
            return float(value[0])  # type: ignore[index]
        except (TypeError, ValueError, IndexError, KeyError):
            return default


def _read_dataset(source: str | Path | BinaryIO) -> Dataset:
    try:
        ds = pydicom.dcmread(source)
    except (InvalidDicomError, OSError, ValueError) as exc:
        raise DicomLoadError(f"DICOMファイルを読み取れません: {exc}") from exc
    if "PixelData" not in ds:
        raise DicomLoadError("画像ピクセルを含まないDICOMです")
    return ds


def _load_datasets(entries: Iterable[tuple[str, BinaryIO | str | Path]]) -> list[Dataset]:
    datasets: list[Dataset] = []
    for name, source in entries:
        try:
            datasets.append(_read_dataset(source))
        except DicomLoadError as exc:
            raise DicomLoadError(f"{name}: {exc}") from exc
    if not datasets:
        raise DicomLoadError("DICOMファイル（.dcm）が見つかりません")
    return datasets


def load_folder(folder: str | Path) -> DicomVolume:
    root = Path(folder)
    if not root.is_dir():
        raise DicomLoadError("選択されたフォルダーが存在しません")
    paths = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".dcm")
    return build_volume(_load_datasets((str(path), path) for path in paths))


def load_zip(zip_path: str | Path) -> DicomVolume:
    try:
        with ZipFile(zip_path) as archive:
            names = sorted(
                info.filename
                for info in archive.infolist()
                if not info.is_dir() and Path(info.filename).suffix.lower() == ".dcm"
            )
            datasets = []
            for name in names:
                with archive.open(name) as stream:
                    datasets.append(_read_dataset(BytesIO(stream.read())))
            if not datasets:
                raise DicomLoadError("ZIP内にDICOMファイル（.dcm）が見つかりません")
    except (BadZipFile, OSError) as exc:
        raise DicomLoadError(f"ZIPファイルを読み取れません: {exc}") from exc
    return build_volume(datasets)


def build_volume(datasets: list[Dataset]) -> DicomVolume:
    if not datasets:
        raise DicomLoadError("DICOM画像がありません")

    required = ("SeriesInstanceUID", "Rows", "Columns", "PixelSpacing", "ImageOrientationPatient", "ImagePositionPatient")
    for ds in datasets:
        missing = [name for name in required if not hasattr(ds, name)]
        if missing:
            raise DicomLoadError(f"必須タグがありません: {', '.join(missing)}")
        transfer_syntax = getattr(getattr(ds, "file_meta", None), "TransferSyntaxUID", None)
        if transfer_syntax is None:
            raise DicomLoadError("TransferSyntaxUIDがありません")
        if transfer_syntax.is_compressed:
            raise DicomLoadError("圧縮DICOMには対応していません")

    series = {str(ds.SeriesInstanceUID) for ds in datasets}
    if len(series) != 1:
        raise DicomLoadError("複数のDICOMシリーズが混在しています")
    shapes = {(int(ds.Rows), int(ds.Columns)) for ds in datasets}
    if len(shapes) != 1:
        raise DicomLoadError("画像サイズが混在しています")

    reference_orientation = np.asarray(datasets[0].ImageOrientationPatient, dtype=np.float64)
    if reference_orientation.shape != (6,):
        raise DicomLoadError("ImageOrientationPatientが不正です")
    # This viewer deliberately rejects rotations/flips it cannot label reliably.
    if not np.allclose(reference_orientation, [1, 0, 0, 0, 1, 0], atol=1e-4):
        raise DicomLoadError("このViewerは軸平行CT（標準方向）のみに対応しています")
    normal = np.cross(reference_orientation[:3], reference_orientation[3:])

    pixel_spacing = np.asarray(datasets[0].PixelSpacing, dtype=np.float64)
    if pixel_spacing.shape != (2,) or np.any(pixel_spacing <= 0):
        raise DicomLoadError("PixelSpacingが不正です")

    positioned: list[tuple[float, Dataset]] = []
    for ds in datasets:
        orientation = np.asarray(ds.ImageOrientationPatient, dtype=np.float64)
        spacing = np.asarray(ds.PixelSpacing, dtype=np.float64)
        position = np.asarray(ds.ImagePositionPatient, dtype=np.float64)
        if position.shape != (3,):
            raise DicomLoadError("ImagePositionPatientが不正です")
        if not np.allclose(orientation, reference_orientation, atol=1e-4):
            raise DicomLoadError("画像方向がスライス間で一致しません")
        if not np.allclose(spacing, pixel_spacing, rtol=1e-4, atol=1e-5):
            raise DicomLoadError("PixelSpacingがスライス間で一致しません")
        positioned.append((float(np.dot(position, normal)), ds))

    positioned.sort(key=lambda item: item[0])
    positions = np.asarray([item[0] for item in positioned], dtype=np.float64)
    if len(positions) < 2:
        raise DicomLoadError("MPR表示には2枚以上のDICOMスライスが必要です")
    differences = np.diff(positions)
    if np.any(differences <= 1e-5):
        raise DicomLoadError("スライス位置が重複しています")
    z_spacing = float(np.median(differences))
    if not np.allclose(differences, z_spacing, rtol=0.01, atol=1e-3):
        raise DicomLoadError("スライス間隔が不均一、またはスライスが欠損しています")

    images: list[NDArray[np.float32]] = []
    for _, ds in positioned:
        try:
            pixels = ds.pixel_array.astype(np.float32)
        except Exception as exc:
            raise DicomLoadError(f"ピクセルデータを展開できません: {exc}") from exc
        slope = _first_number(getattr(ds, "RescaleSlope", 1), 1.0)
        intercept = _first_number(getattr(ds, "RescaleIntercept", 0), 0.0)
        images.append(pixels * slope + intercept)

    volume = np.stack(images).astype(np.float32, copy=False)
    initial_wl = round(_first_number(getattr(positioned[0][1], "WindowCenter", None), 40.0))
    initial_ww = max(1, round(_first_number(getattr(positioned[0][1], "WindowWidth", None), 400.0)))
    return DicomVolume(
        volume=volume,
        spacing_zyx=(z_spacing, float(pixel_spacing[0]), float(pixel_spacing[1])),
        slice_positions=positions,
        series_uid=next(iter(series)),
        initial_wl=initial_wl,
        initial_ww=initial_ww,
    )
