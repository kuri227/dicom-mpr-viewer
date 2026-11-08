import io
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from dicom_loader import DicomLoadError, build_volume, load_folder, load_zip


def make_dataset(
    position: float,
    raw_value: int,
    *,
    series_uid: str,
    rows: int = 3,
    columns: int = 4,
    pixel_spacing: tuple[float, float] = (0.75, 0.75),
    orientation: tuple[float, ...] = (1, 0, 0, 0, 1, 0),
) -> FileDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = "CT"
    ds.SeriesInstanceUID = series_uid
    ds.Rows = rows
    ds.Columns = columns
    ds.PixelSpacing = list(pixel_spacing)
    ds.ImageOrientationPatient = list(orientation)
    ds.ImagePositionPatient = [0.0, 0.0, position]
    ds.InstanceNumber = int(100 - position)
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 1
    ds.RescaleSlope = 1
    ds.RescaleIntercept = -1024
    ds.WindowCenter = 55
    ds.WindowWidth = 500
    ds.PixelData = np.full((rows, columns), raw_value, dtype="<i2").tobytes()
    return ds


def dataset_bytes(ds: FileDataset) -> bytes:
    stream = io.BytesIO()
    ds.save_as(stream, enforce_file_format=True)
    return stream.getvalue()


class DicomLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.series_uid = generate_uid()
        self.datasets = [
            make_dataset(5.0, 300, series_uid=self.series_uid),
            make_dataset(0.0, 100, series_uid=self.series_uid),
            make_dataset(2.5, 200, series_uid=self.series_uid),
        ]

    def test_build_volume_sorts_by_projected_position_and_converts_hu(self) -> None:
        result = build_volume(self.datasets)
        self.assertEqual(result.volume.shape, (3, 3, 4))
        np.testing.assert_array_equal(result.slice_positions, [0.0, 2.5, 5.0])
        np.testing.assert_array_equal(result.volume[:, 0, 0], [-924.0, -824.0, -724.0])
        self.assertEqual(result.spacing_zyx, (2.5, 0.75, 0.75))
        self.assertEqual((result.initial_wl, result.initial_ww), (55, 500))

    def test_folder_and_nested_zip_are_equivalent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "series"
            folder.mkdir()
            names = ["c.DCM", "a.dcm", "b.dcm"]
            for name, ds in zip(names, self.datasets):
                ds.save_as(folder / name, enforce_file_format=True)
            archive_path = root / "series.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                for name, ds in zip(names, self.datasets):
                    archive.writestr(f"nested/series/{name}", dataset_bytes(ds))
            from_folder = load_folder(root)
            from_zip = load_zip(archive_path)
            np.testing.assert_array_equal(from_folder.volume, from_zip.volume)
            self.assertEqual(from_folder.spacing_zyx, from_zip.spacing_zyx)
            np.testing.assert_array_equal(from_folder.slice_positions, from_zip.slice_positions)

    def test_empty_folder_bad_zip_and_non_dicom_fail_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(DicomLoadError, "見つかりません"):
                load_folder(root)
            bad_zip = root / "bad.zip"
            bad_zip.write_bytes(b"not a zip")
            with self.assertRaisesRegex(DicomLoadError, "ZIPファイル"):
                load_zip(bad_zip)
            (root / "bad.dcm").write_bytes(b"not dicom")
            with self.assertRaisesRegex(DicomLoadError, "読み取れません"):
                load_folder(root)

    def test_mixed_series_is_rejected(self) -> None:
        self.datasets[0].SeriesInstanceUID = generate_uid()
        with self.assertRaisesRegex(DicomLoadError, "複数"):
            build_volume(self.datasets)

    def test_mixed_shape_is_rejected(self) -> None:
        self.datasets[0] = make_dataset(5.0, 300, series_uid=self.series_uid, rows=4)
        with self.assertRaisesRegex(DicomLoadError, "画像サイズ"):
            build_volume(self.datasets)

    def test_duplicate_and_irregular_positions_are_rejected(self) -> None:
        duplicate = [
            make_dataset(0.0, 1, series_uid=self.series_uid),
            make_dataset(0.0, 2, series_uid=self.series_uid),
        ]
        with self.assertRaisesRegex(DicomLoadError, "重複"):
            build_volume(duplicate)
        irregular = [
            make_dataset(0.0, 1, series_uid=self.series_uid),
            make_dataset(2.5, 2, series_uid=self.series_uid),
            make_dataset(7.5, 3, series_uid=self.series_uid),
        ]
        with self.assertRaisesRegex(DicomLoadError, "不均一"):
            build_volume(irregular)

    def test_unsupported_orientation_is_rejected(self) -> None:
        rotated = [
            make_dataset(0.0, 1, series_uid=self.series_uid, orientation=(0, 1, 0, 1, 0, 0)),
            make_dataset(2.5, 2, series_uid=self.series_uid, orientation=(0, 1, 0, 1, 0, 0)),
        ]
        with self.assertRaisesRegex(DicomLoadError, "軸平行"):
            build_volume(rotated)


@unittest.skipUnless(os.environ.get("DICOM_SAMPLE_ZIP"), "DICOM_SAMPLE_ZIP is not set")
class SampleZipTests(unittest.TestCase):
    def test_assignment_sample(self) -> None:
        result = load_zip(os.environ["DICOM_SAMPLE_ZIP"])
        self.assertEqual(result.volume.shape, (133, 512, 512))
        np.testing.assert_allclose(result.spacing_zyx, (2.5, 0.78125, 0.78125))
        self.assertTrue(np.all(np.diff(result.slice_positions) > 0))


if __name__ == "__main__":
    unittest.main()
