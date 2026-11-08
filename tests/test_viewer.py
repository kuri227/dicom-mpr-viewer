import os
import tkinter as tk
import unittest

from mpr import PLANES
from viewer import MprViewer


@unittest.skipUnless(os.environ.get("DICOM_SAMPLE_ZIP"), "DICOM_SAMPLE_ZIP is not set")
class ViewerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.app = MprViewer()
        except tk.TclError as exc:
            self.skipTest(f"Tk display is unavailable: {exc}")
        self.app.geometry("1280x760")
        self.app.update()

    def tearDown(self) -> None:
        if hasattr(self, "app"):
            self.app.destroy()

    def test_sample_renders_three_images_nine_sliders_and_linked_crosshairs(self) -> None:
        self.app._load(os.environ["DICOM_SAMPLE_ZIP"], is_zip=True)
        self.app.update()

        self.assertEqual(set(self.app.panels), set(PLANES))
        self.assertEqual(sum(len(panel["scales"]) for panel in self.app.panels.values()), 9)
        self.assertTrue(all(panel["photo"] is not None for panel in self.app.panels.values()))
        self.assertTrue(
            all(len(panel["canvas"].find_withtag("crosshair")) == 2 for panel in self.app.panels.values())
        )
        self.assertTrue(
            all("disabled" not in scale.state() for panel in self.app.panels.values() for scale in panel["scales"].values())
        )

        coronal = self.app.panels["coronal"]["canvas"]
        before = [coronal.coords(item) for item in coronal.find_withtag("crosshair")]
        self.app._on_slider("axial", "position", "0")
        after = [coronal.coords(item) for item in coronal.find_withtag("crosshair")]
        self.assertNotEqual(before, after)
        self.assertEqual(self.app.state_model.z_idx, 0)

        original_axial_window = self.app.state_model.windows["axial"]
        self.app._on_slider("coronal", "wl", "-600")
        self.assertEqual(self.app.state_model.windows["coronal"][0], -600)
        self.assertEqual(self.app.state_model.windows["axial"], original_axial_window)
        self.assertEqual(self.app.state_model.z_idx, 0)


if __name__ == "__main__":
    unittest.main()
