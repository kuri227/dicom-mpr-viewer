"""Load and safely align a binary multi-page TIFF lung mask to a CT volume."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageSequence, UnidentifiedImageError


class LungMaskError(ValueError):
    """A user-facing error raised when a lung mask cannot be displayed safely."""


@dataclass(frozen=True)
class LungMask:
    mask: NDArray[np.bool_]
    source_name: str
    reversed_z: bool
    air_fraction: float
    voxel_count: int


def _alignment_score(volume: NDArray[np.float32], mask: NDArray[np.bool_]) -> float:
    values = volume[mask]
    if values.size == 0:
        return 0.0
    return float(np.count_nonzero(values < -300.0) / values.size)


def align_mask_to_ct(
    mask: NDArray[np.bool_],
    volume: NDArray[np.float32],
    *,
    minimum_air_fraction: float = 0.80,
    minimum_score_margin: float = 0.10,
) -> tuple[NDArray[np.bool_], bool, float]:
    """Choose direct or reversed Z order only when CT evidence is unambiguous."""
    if mask.shape != volume.shape:
        raise LungMaskError(
            f"肺マスクのshape {mask.shape} がCTのshape {volume.shape} と一致しません。"
            "同じCTから作成した連続TIFFを選択してください。"
        )
    direct_score = _alignment_score(volume, mask)
    reversed_score = _alignment_score(volume, mask[::-1])
    if reversed_score > direct_score:
        best_mask = mask[::-1].copy()
        reversed_z = True
        best_score = reversed_score
        other_score = direct_score
    else:
        best_mask = mask
        reversed_z = False
        best_score = direct_score
        other_score = reversed_score
    if best_score < minimum_air_fraction or best_score - other_score < minimum_score_margin:
        raise LungMaskError(
            "肺マスクとCTのスライス順を安全に判定できません。"
            "同一CT由来か、TIFFの順序・回転・反転を確認してください。"
        )
    return best_mask, reversed_z, best_score


def load_lung_mask(path: str | Path, volume: NDArray[np.float32]) -> LungMask:
    """Read a binary TIFF stack and align its page order to volume[z,y,x]."""
    source = Path(path)
    try:
        with source.open("rb") as stream:
            content = stream.read()
        with Image.open(BytesIO(content)) as image:
            arrays = [np.asarray(frame.copy()) for frame in ImageSequence.Iterator(image)]
    except (OSError, UnidentifiedImageError) as exc:
        raise LungMaskError(
            "TIFFを読み取れません。ファイルが破損していないか確認してください。"
        ) from exc

    if len(arrays) < 2:
        raise LungMaskError("肺マスクには2ページ以上の連続TIFFが必要です。")
    expected_yx = volume.shape[1:]
    unique_values: set[int] = set()
    for index, array in enumerate(arrays, start=1):
        if array.ndim != 2 or array.shape != expected_yx:
            raise LungMaskError(
                f"TIFFの{index}ページ目が{expected_yx[1]}×{expected_yx[0]}ではありません。"
            )
        unique_values.update(int(value) for value in np.unique(array))
        if len(unique_values) > 2:
            raise LungMaskError("肺マスクは背景0と肺領域だけの二値TIFFにしてください。")
    mask = np.stack([array > 0 for array in arrays])
    if mask.shape != volume.shape:
        raise LungMaskError(
            f"肺マスクのshape {mask.shape} がCTのshape {volume.shape} と一致しません。"
            "同じCTから作成した連続TIFFを選択してください。"
        )
    voxel_count = int(np.count_nonzero(mask))
    if voxel_count == 0:
        raise LungMaskError("肺領域が空です。第5回の抽出結果を確認してください。")
    if voxel_count / mask.size >= 0.95:
        raise LungMaskError("肺領域が画像全体を占めています。二値化結果を確認してください。")
    if 0 not in unique_values:
        raise LungMaskError("肺マスクに背景値0がありません。二値化結果を確認してください。")

    aligned, reversed_z, air_fraction = align_mask_to_ct(mask, volume)
    return LungMask(
        mask=aligned,
        source_name=source.name,
        reversed_z=reversed_z,
        air_fraction=air_fraction,
        voxel_count=voxel_count,
    )
