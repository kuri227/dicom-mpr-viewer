"""VTK-based 3D volume rendering for a validated binary lung mask."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import numpy as np
from numpy.typing import NDArray


class VolumeRenderingError(RuntimeError):
    """A user-facing error raised when VTK rendering cannot be initialized."""


def mask_to_vtk_image(
    mask: NDArray[np.bool_], spacing_zyx: tuple[float, float, float]
):
    """Convert mask[z,y,x] into vtkImageData with matching physical spacing."""
    try:
        from vtkmodules.util.numpy_support import numpy_to_vtk
        from vtkmodules.vtkCommonDataModel import vtkImageData
    except ImportError as exc:
        raise VolumeRenderingError(
            "VTKが見つかりません。pip install -r requirements.txt を実行してください。"
        ) from exc

    if mask.ndim != 3:
        raise ValueError("mask must be a 3-D array in [z, y, x] order")
    if len(spacing_zyx) != 3 or min(spacing_zyx) <= 0:
        raise ValueError("spacing must contain positive z, y, x values")

    z_count, y_count, x_count = mask.shape
    z_spacing, y_spacing, x_spacing = spacing_zyx
    image = vtkImageData()
    image.SetDimensions(x_count, y_count, z_count)
    image.SetSpacing(x_spacing, y_spacing, z_spacing)
    image.SetOrigin(0.0, 0.0, 0.0)
    values = np.ascontiguousarray(mask, dtype=np.uint8).ravel(order="C") * 255
    scalars = numpy_to_vtk(values, deep=True)
    scalars.SetName("lung_mask")
    image.GetPointData().SetScalars(scalars)
    return image


class LungVolumeWindow:
    """A VTK render window managed from a compact Tkinter control window."""

    def __init__(
        self,
        parent: tk.Misc,
        mask: NDArray[np.bool_],
        spacing_zyx: tuple[float, float, float],
        *,
        source_name: str,
        voxel_count: int,
        reversed_z: bool,
        air_fraction: float,
    ) -> None:
        try:
            import vtkmodules.vtkRenderingOpenGL2  # noqa: F401 - registers OpenGL overrides
            import vtkmodules.vtkRenderingVolumeOpenGL2  # noqa: F401
            from vtkmodules.vtkCommonDataModel import vtkPiecewiseFunction
            from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
            from vtkmodules.vtkRenderingCore import (
                vtkColorTransferFunction,
                vtkRenderer,
                vtkRenderWindow,
                vtkRenderWindowInteractor,
                vtkVolume,
                vtkVolumeProperty,
            )
            from vtkmodules.vtkRenderingVolume import vtkGPUVolumeRayCastMapper
        except ImportError as exc:
            raise VolumeRenderingError(
                "VTKの3D描画機能を読み込めません。requirements.txtから再インストールしてください。"
            ) from exc

        self.window = tk.Toplevel(parent)
        self.window.title("肺領域 3D操作")
        self.window.geometry(self._control_geometry(parent))
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._closed = False
        self._event_job: str | None = None

        header = ttk.Frame(self.window, padding=(12, 10, 12, 8))
        header.pack(fill="x")
        ttk.Label(header, text="肺領域 3D Volume Rendering", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        order_text = "あり" if reversed_z else "なし"
        ttk.Label(
            header,
            text=(
                f"{source_name}  |  {voxel_count:,} voxels  |  "
                f"Z反転: {order_text}  |  肺野整合率: {air_fraction:.1%}"
            ),
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(header, text="3D画像は別ウィンドウに表示されます。").pack(anchor="w", pady=(4, 0))

        footer = ttk.Frame(self.window, padding=(12, 8, 12, 10))
        footer.pack(fill="x")
        ttk.Button(footer, text="視点をリセット", command=self.reset_camera).pack(side="left")
        ttk.Label(
            footer,
            text="左ドラッグ: 回転  /  右ドラッグ: ズーム  /  中ドラッグ: 平行移動",
        ).pack(side="left", padx=14)
        ttk.Button(footer, text="閉じる", command=self.close).pack(side="right")

        image = mask_to_vtk_image(mask, spacing_zyx)
        mapper = vtkGPUVolumeRayCastMapper()
        mapper.SetInputData(image)
        mapper.SetBlendModeToComposite()
        mapper.AutoAdjustSampleDistancesOn()

        color = vtkColorTransferFunction()
        color.AddRGBPoint(0.0, 0.0, 0.0, 0.0)
        color.AddRGBPoint(255.0, 0.92, 0.28, 0.30)
        opacity = vtkPiecewiseFunction()
        opacity.AddPoint(0.0, 0.0)
        opacity.AddPoint(254.0, 0.0)
        opacity.AddPoint(255.0, 0.35)

        volume_property = vtkVolumeProperty()
        volume_property.SetColor(color)
        volume_property.SetScalarOpacity(opacity)
        volume_property.SetInterpolationTypeToLinear()
        volume_property.ShadeOn()
        volume_property.SetAmbient(0.30)
        volume_property.SetDiffuse(0.70)
        volume_property.SetSpecular(0.20)

        volume = vtkVolume()
        volume.SetMapper(mapper)
        volume.SetProperty(volume_property)

        self.renderer = vtkRenderer()
        self.renderer.SetBackground(0.051, 0.067, 0.090)
        self.renderer.AddVolume(volume)
        self.render_window = vtkRenderWindow()
        self.render_window.SetWindowName("肺領域 3D Volume Rendering")
        self.render_window.SetSize(800, 700)
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        self.render_window.SetPosition(max(0, (screen_width - 800) // 2), max(0, (screen_height - 700) // 3))
        self.render_window.AddRenderer(self.renderer)
        self.interactor = vtkRenderWindowInteractor()
        self.interactor.SetRenderWindow(self.render_window)
        self.interactor.SetInteractorStyle(vtkInteractorStyleTrackballCamera())
        self.interactor.AddObserver("ExitEvent", lambda *_args: self.close())

        # Retain VTK objects for the lifetime of the Toplevel.
        self._vtk_objects = (image, mapper, color, opacity, volume_property, volume)
        try:
            self.interactor.Initialize()
            self.reset_camera()
            self._event_job = self.window.after(10, self._process_events)
        except Exception as exc:
            self.close()
            raise VolumeRenderingError(
                "3D描画を開始できません。GPUドライバーとVTK環境を確認してください。"
            ) from exc

    @staticmethod
    def _control_geometry(parent: tk.Misc) -> str:
        screen_width = parent.winfo_screenwidth()
        screen_height = parent.winfo_screenheight()
        width = min(640, max(520, screen_width - 240))
        height = 160
        x = max(0, (screen_width - width) // 2)
        y = max(0, screen_height - height - 80)
        return f"{width}x{height}+{x}+{y}"

    def _process_events(self) -> None:
        if self._closed:
            return
        try:
            self.interactor.ProcessEvents()
        except Exception:
            self.close()
            return
        self._event_job = self.window.after(10, self._process_events)

    def reset_camera(self) -> None:
        self.renderer.ResetCamera()
        camera = self.renderer.GetActiveCamera()
        camera.Azimuth(20)
        camera.Elevation(10)
        camera.Zoom(1.15)
        self.render_window.Render()

    def focus(self) -> None:
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self.render_window.Render()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._event_job is not None:
            try:
                self.window.after_cancel(self._event_job)
            except tk.TclError:
                pass
            self._event_job = None
        try:
            self.interactor.TerminateApp()
            self.render_window.Finalize()
        finally:
            if self.window.winfo_exists():
                self.window.destroy()
