import os
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from dicom_loader import load_zip
from lung_mask import LungMaskError, align_mask_to_ct, load_lung_mask


def write_tiff(path: Path, mask: np.ndarray) -> None:
    images = [Image.fromarray((frame.astype(np.uint8) * 255), mode="L") for frame in mask]
    images[0].save(path, save_all=True, append_images=images[1:], compression="raw")


def synthetic_volume_and_mask() -> tuple[np.ndarray, np.ndarray]:
    volume = np.full((5, 8, 10), 50.0, dtype=np.float32)
    mask = np.zeros_like(volume, dtype=bool)
    mask[0:2, 2:6, 2:8] = True
    volume[mask] = -800.0
    return volume, mask


class LungMaskTests(unittest.TestCase):
    def test_direct_order_is_selected(self) -> None:
        volume, expected = synthetic_volume_and_mask()
        aligned, reversed_z, score = align_mask_to_ct(expected, volume)
        np.testing.assert_array_equal(aligned, expected)
        self.assertFalse(reversed_z)
        self.assertEqual(score, 1.0)

    def test_reversed_tiff_is_aligned_to_ct(self) -> None:
        volume, expected = synthetic_volume_and_mask()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mask.tif"
            write_tiff(path, expected[::-1])
            result = load_lung_mask(path, volume)
        np.testing.assert_array_equal(result.mask, expected)
        self.assertTrue(result.reversed_z)
        self.assertEqual(result.air_fraction, 1.0)
        self.assertEqual(result.voxel_count, int(expected.sum()))
        self.assertEqual(result.source_name, "mask.tif")

    def test_shape_mismatch_is_rejected(self) -> None:
        volume, expected = synthetic_volume_and_mask()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "wrong.tif"
            write_tiff(path, expected[:, :, :-1])
            with self.assertRaisesRegex(LungMaskError, "512|ページ目|shape"):
                load_lung_mask(path, volume)

    def test_nonbinary_empty_and_full_masks_are_rejected(self) -> None:
        volume, expected = synthetic_volume_and_mask()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            nonbinary = expected.astype(np.uint8) * 255
            nonbinary[0, 0, 0] = 128
            nonbinary_path = root / "nonbinary.tif"
            images = [Image.fromarray(frame, mode="L") for frame in nonbinary]
            images[0].save(nonbinary_path, save_all=True, append_images=images[1:])
            with self.assertRaisesRegex(LungMaskError, "二値"):
                load_lung_mask(nonbinary_path, volume)

            for name, mask, message in (
                ("empty.tif", np.zeros_like(expected), "空"),
                ("full.tif", np.ones_like(expected), "全体"),
            ):
                path = root / name
                write_tiff(path, mask)
                with self.assertRaisesRegex(LungMaskError, message):
                    load_lung_mask(path, volume)

    def test_ambiguous_order_is_rejected(self) -> None:
        volume, mask = synthetic_volume_and_mask()
        volume[:] = -800.0
        with self.assertRaisesRegex(LungMaskError, "安全に判定"):
            align_mask_to_ct(mask, volume)


@unittest.skipUnless(
    os.environ.get("DICOM_SAMPLE_ZIP") and os.environ.get("LUNG_MASK_TIFF"),
    "DICOM_SAMPLE_ZIP and LUNG_MASK_TIFF are not set",
)
class RealLungMaskTests(unittest.TestCase):
    def test_assignment_tiff_aligns_to_sample_ct(self) -> None:
        data = load_zip(os.environ["DICOM_SAMPLE_ZIP"])
        result = load_lung_mask(os.environ["LUNG_MASK_TIFF"], data.volume)
        self.assertEqual(result.mask.shape, (133, 512, 512))
        self.assertEqual(result.voxel_count, 2_790_512)
        self.assertTrue(result.reversed_z)
        self.assertGreater(result.air_fraction, 0.99)


if __name__ == "__main__":
    unittest.main()
