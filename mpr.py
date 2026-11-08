"""Pure MPR image-processing and coordinate conversion functions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from numpy.typing import NDArray

Plane = Literal["axial", "coronal", "sagittal"]
PLANES: tuple[Plane, ...] = ("axial", "coronal", "sagittal")


def extract_slice(
    volume: NDArray[np.floating], plane: Plane, z_idx: int, y_idx: int, x_idx: int
) -> NDArray[np.floating]:
    """Return a view into volume[z, y, x] for the requested anatomical plane."""
    if volume.ndim != 3:
        raise ValueError("volume must be a 3-D array in [z, y, x] order")
    if plane == "axial":
        return volume[z_idx, :, :]
    if plane == "coronal":
        return volume[:, y_idx, :]
    if plane == "sagittal":
        return volume[:, :, x_idx]
    raise ValueError(f"unknown plane: {plane}")


def window_to_uint8(image_hu: NDArray[np.floating], wl: float, ww: float) -> NDArray[np.uint8]:
    """Apply the assignment's simple linear WL/WW mapping."""
    if not np.isfinite(wl) or not np.isfinite(ww):
        raise ValueError("WL and WW must be finite")
    if ww <= 0:
        raise ValueError("WW must be greater than zero")
    low = wl - ww / 2.0
    scaled = np.clip((image_hu.astype(np.float32, copy=False) - low) / ww, 0.0, 1.0)
    return np.rint(scaled * 255.0).astype(np.uint8)


def crosshair_indices(plane: Plane, z_idx: int, y_idx: int, x_idx: int) -> tuple[int, int]:
    """Return (vertical-column index, horizontal-row index) for a plane."""
    if plane == "axial":
        return x_idx, y_idx
    if plane == "coronal":
        return x_idx, z_idx
    if plane == "sagittal":
        return y_idx, z_idx
    raise ValueError(f"unknown plane: {plane}")


def plane_pixel_spacing(plane: Plane, spacing_zyx: tuple[float, float, float]) -> tuple[float, float]:
    """Return (row spacing, column spacing) in mm for a rendered plane."""
    z_spacing, y_spacing, x_spacing = spacing_zyx
    if plane == "axial":
        return y_spacing, x_spacing
    if plane == "coronal":
        return z_spacing, x_spacing
    if plane == "sagittal":
        return z_spacing, y_spacing
    raise ValueError(f"unknown plane: {plane}")


def fit_image_rect(
    canvas_width: int,
    canvas_height: int,
    rows: int,
    columns: int,
    row_spacing: float,
    column_spacing: float,
) -> tuple[float, float, float, float]:
    """Fit physical image extent into a canvas, returning x, y, width, height."""
    if min(canvas_width, canvas_height, rows, columns) <= 0:
        raise ValueError("canvas and image dimensions must be positive")
    if row_spacing <= 0 or column_spacing <= 0:
        raise ValueError("pixel spacing must be positive")
    physical_width = columns * column_spacing
    physical_height = rows * row_spacing
    scale = min(canvas_width / physical_width, canvas_height / physical_height)
    width = physical_width * scale
    height = physical_height * scale
    return (canvas_width - width) / 2.0, (canvas_height - height) / 2.0, width, height


def image_index_to_canvas(
    column: int,
    row: int,
    columns: int,
    rows: int,
    rect: tuple[float, float, float, float],
) -> tuple[float, float]:
    """Map an image pixel index to the same fitted rectangle used by the bitmap."""
    if not (0 <= column < columns and 0 <= row < rows):
        raise ValueError("image index is outside the image")
    x0, y0, width, height = rect
    x = x0 + (column / max(columns - 1, 1)) * width
    y = y0 + (row / max(rows - 1, 1)) * height
    return x, y


@dataclass
class ViewerState:
    """GUI-independent shared indices and per-plane window settings."""

    z_idx: int
    y_idx: int
    x_idx: int
    windows: dict[Plane, tuple[int, int]] = field(default_factory=dict)

    def set_position(self, plane: Plane, value: int) -> None:
        if plane == "axial":
            self.z_idx = value
        elif plane == "coronal":
            self.y_idx = value
        elif plane == "sagittal":
            self.x_idx = value
        else:
            raise ValueError(f"unknown plane: {plane}")

    def set_window(self, plane: Plane, wl: int, ww: int) -> None:
        if plane not in PLANES:
            raise ValueError(f"unknown plane: {plane}")
        if ww <= 0:
            raise ValueError("WW must be greater than zero")
        self.windows[plane] = (wl, ww)
