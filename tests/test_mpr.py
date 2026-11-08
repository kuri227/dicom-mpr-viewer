import unittest

import numpy as np

from mpr import (
    PLANES,
    ViewerState,
    crosshair_indices,
    extract_slice,
    fit_image_rect,
    image_index_to_canvas,
    plane_pixel_spacing,
    window_to_uint8,
)


class MprTests(unittest.TestCase):
    def setUp(self) -> None:
        self.volume = np.arange(3 * 4 * 5, dtype=np.float32).reshape(3, 4, 5)

    def test_extract_all_three_planes(self) -> None:
        np.testing.assert_array_equal(extract_slice(self.volume, "axial", 1, 2, 3), self.volume[1, :, :])
        np.testing.assert_array_equal(extract_slice(self.volume, "coronal", 1, 2, 3), self.volume[:, 2, :])
        np.testing.assert_array_equal(extract_slice(self.volume, "sagittal", 1, 2, 3), self.volume[:, :, 3])

    def test_crosshair_formulas_at_center_and_edges(self) -> None:
        for z, y, x in ((1, 2, 3), (0, 0, 0), (2, 3, 4)):
            self.assertEqual(crosshair_indices("axial", z, y, x), (x, y))
            self.assertEqual(crosshair_indices("coronal", z, y, x), (x, z))
            self.assertEqual(crosshair_indices("sagittal", z, y, x), (y, z))

    def test_fit_and_index_mapping_share_endpoints_and_center(self) -> None:
        rect = fit_image_rect(500, 300, rows=101, columns=201, row_spacing=2.0, column_spacing=1.0)
        x0, y0, width, height = rect
        self.assertAlmostEqual(width / height, 201.0 / 202.0)
        self.assertEqual(image_index_to_canvas(0, 0, 201, 101, rect), (x0, y0))
        self.assertEqual(image_index_to_canvas(200, 100, 201, 101, rect), (x0 + width, y0 + height))
        self.assertEqual(image_index_to_canvas(100, 50, 201, 101, rect), (x0 + width / 2, y0 + height / 2))

    def test_window_level_mapping(self) -> None:
        result = window_to_uint8(np.asarray([-200.0, 0.0, 200.0], dtype=np.float32), 0, 400)
        np.testing.assert_array_equal(result, [0, 128, 255])

    def test_window_clips_extremes(self) -> None:
        result = window_to_uint8(np.asarray([-10000.0, 10000.0], dtype=np.float32), -200, 1)
        np.testing.assert_array_equal(result, [0, 255])

    def test_invalid_window_width_is_rejected(self) -> None:
        for ww in (0, -1):
            with self.assertRaises(ValueError):
                window_to_uint8(self.volume, 0, ww)

    def test_physical_spacing_for_planes(self) -> None:
        spacing = (2.5, 0.78, 0.79)
        self.assertEqual(plane_pixel_spacing("axial", spacing), (0.78, 0.79))
        self.assertEqual(plane_pixel_spacing("coronal", spacing), (2.5, 0.79))
        self.assertEqual(plane_pixel_spacing("sagittal", spacing), (2.5, 0.78))

    def test_position_state_updates_shared_coordinate(self) -> None:
        state = ViewerState(1, 2, 3, {plane: (40, 400) for plane in PLANES})
        state.set_position("axial", 8)
        state.set_position("coronal", 9)
        state.set_position("sagittal", 10)
        self.assertEqual((state.z_idx, state.y_idx, state.x_idx), (8, 9, 10))
        self.assertEqual(crosshair_indices("coronal", state.z_idx, state.y_idx, state.x_idx), (10, 8))

    def test_window_state_is_independent_per_plane(self) -> None:
        state = ViewerState(1, 2, 3, {plane: (40, 400) for plane in PLANES})
        state.set_window("coronal", -600, 1500)
        self.assertEqual(state.windows["coronal"], (-600, 1500))
        self.assertEqual(state.windows["axial"], (40, 400))
        self.assertEqual(state.windows["sagittal"], (40, 400))
        self.assertEqual((state.z_idx, state.y_idx, state.x_idx), (1, 2, 3))


if __name__ == "__main__":
    unittest.main()
