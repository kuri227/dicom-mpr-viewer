import unittest

import numpy as np

from volume3d import mask_to_vtk_image


class Volume3dTests(unittest.TestCase):
    def test_mask_to_vtk_preserves_dimensions_spacing_and_values(self) -> None:
        mask = np.zeros((3, 4, 5), dtype=bool)
        mask[1, 2, 3] = True
        image = mask_to_vtk_image(mask, (2.5, 0.75, 0.8))
        self.assertEqual(image.GetDimensions(), (5, 4, 3))
        self.assertEqual(image.GetSpacing(), (0.8, 0.75, 2.5))
        self.assertEqual(image.GetNumberOfPoints(), 60)
        self.assertEqual(image.GetScalarRange(), (0.0, 255.0))
        scalars = image.GetPointData().GetScalars()
        self.assertEqual(scalars.GetNumberOfTuples(), 60)
        self.assertEqual(sum(scalars.GetValue(i) == 255 for i in range(60)), 1)

    def test_invalid_mask_or_spacing_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mask_to_vtk_image(np.zeros((3, 4), dtype=bool), (1.0, 1.0, 1.0))
        with self.assertRaises(ValueError):
            mask_to_vtk_image(np.zeros((3, 4, 5), dtype=bool), (0.0, 1.0, 1.0))


if __name__ == "__main__":
    unittest.main()
