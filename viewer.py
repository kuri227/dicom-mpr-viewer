"""Tkinter user interface for the DICOM MPR Viewer."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from PIL import Image, ImageTk

from dicom_loader import DicomLoadError, DicomVolume, load_folder, load_zip
from mpr import (
    PLANES,
    Plane,
    ViewerState,
    crosshair_indices,
    extract_slice,
    fit_image_rect,
    image_index_to_canvas,
    plane_pixel_spacing,
    window_to_uint8,
)

PLANE_TITLES = {"axial": "Axial", "coronal": "Coronal", "sagittal": "Sagittal"}


class MprViewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DICOM MPR Viewer")
        self.geometry("1280x760")
        self.minsize(960, 650)
        self.data: DicomVolume | None = None
        self.state_model: ViewerState | None = None
        self.panels: dict[Plane, dict[str, Any]] = {}
        self.status = tk.StringVar(value="ファイルメニューからDICOMデータを開いてください")
        self._resize_job: str | None = None
        self._build_menu()
        self._build_layout()

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="DICOMフォルダーを開く...", command=self._choose_folder)
        file_menu.add_command(label="DICOM ZIPを開く...", command=self._choose_zip)
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.destroy)
        menu.add_cascade(label="ファイル", menu=file_menu)
        self.config(menu=menu)

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=8)
        container.pack(fill="both", expand=True)
        for column, plane in enumerate(PLANES):
            container.columnconfigure(column, weight=1, uniform="panels")
            panel = ttk.LabelFrame(container, text=PLANE_TITLES[plane], padding=6)
            panel.grid(row=0, column=column, sticky="nsew", padx=4)
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(0, weight=1)

            canvas = tk.Canvas(panel, bg="#111318", highlightthickness=0, width=380, height=430)
            canvas.grid(row=0, column=0, columnspan=3, sticky="nsew")
            canvas.create_text(190, 210, text="未読み込み", fill="#c7cbd1", tags="placeholder")
            canvas.bind("<Configure>", self._on_canvas_resize)

            info = tk.StringVar(value="位置: -")
            ttk.Label(panel, textvariable=info).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 0))
            variables: dict[str, tk.IntVar] = {
                "position": tk.IntVar(value=0), "wl": tk.IntVar(value=40), "ww": tk.IntVar(value=400)
            }
            scales: dict[str, ttk.Scale] = {}
            value_labels: dict[str, ttk.Label] = {}
            for row, (key, label, lower, upper) in enumerate(
                (("position", "位置", 0, 1), ("wl", "WL", -1200, 1500), ("ww", "WW", 1, 3000)), start=2
            ):
                ttk.Label(panel, text=label, width=5).grid(row=row, column=0, sticky="w")
                scale = ttk.Scale(
                    panel, from_=lower, to=upper, variable=variables[key],
                    command=lambda value, p=plane, k=key: self._on_slider(p, k, value),
                )
                scale.state(["disabled"])
                scale.grid(row=row, column=1, sticky="ew", padx=4, pady=2)
                value_label = ttk.Label(panel, text="-", width=7, anchor="e")
                value_label.grid(row=row, column=2, sticky="e")
                scales[key] = scale
                value_labels[key] = value_label
            panel.columnconfigure(1, weight=1)
            self.panels[plane] = {
                "canvas": canvas, "info": info, "variables": variables,
                "scales": scales, "value_labels": value_labels, "photo": None,
            }
        container.rowconfigure(0, weight=1)
        ttk.Separator(self).pack(fill="x")
        ttk.Label(self, textvariable=self.status, padding=(10, 6)).pack(fill="x")

    def _choose_folder(self) -> None:
        path = filedialog.askdirectory(title="DICOMフォルダーを選択")
        if path:
            self._load(path, is_zip=False)

    def _choose_zip(self) -> None:
        path = filedialog.askopenfilename(title="DICOM ZIPを選択", filetypes=[("ZIPファイル", "*.zip")])
        if path:
            self._load(path, is_zip=True)

    def _load(self, path: str, is_zip: bool) -> None:
        self.status.set("DICOMデータを読み込んでいます...")
        self.update_idletasks()
        try:
            new_data = load_zip(path) if is_zip else load_folder(path)
        except (DicomLoadError, OSError) as exc:
            self.status.set(f"読み込み失敗: {exc}")
            messagebox.showerror("DICOM読み込みエラー", str(exc), parent=self)
            return
        self.data = new_data
        z_count, y_count, x_count = new_data.volume.shape
        self.state_model = ViewerState(
            z_idx=z_count // 2, y_idx=y_count // 2, x_idx=x_count // 2,
            windows={plane: (new_data.initial_wl, new_data.initial_ww) for plane in PLANES},
        )
        maxima = {"axial": z_count - 1, "coronal": y_count - 1, "sagittal": x_count - 1}
        positions = {"axial": self.state_model.z_idx, "coronal": self.state_model.y_idx, "sagittal": self.state_model.x_idx}
        for plane in PLANES:
            panel = self.panels[plane]
            panel["scales"]["position"].configure(from_=0, to=maxima[plane])
            panel["variables"]["position"].set(positions[plane])
            panel["variables"]["wl"].set(new_data.initial_wl)
            panel["variables"]["ww"].set(new_data.initial_ww)
            for scale in panel["scales"].values():
                scale.state(["!disabled"])
        self.status.set(f"読み込み完了: {Path(path).name} / {z_count}枚 / {z_count}×{y_count}×{x_count}")
        self._render_all()

    def _on_slider(self, plane: Plane, key: str, raw_value: str) -> None:
        if self.data is None or self.state_model is None:
            return
        value = int(round(float(raw_value)))
        panel = self.panels[plane]
        panel["variables"][key].set(value)
        if key == "position":
            self.state_model.set_position(plane, value)
            self._render_plane(plane)
            self._draw_all_crosshairs()
        else:
            wl = panel["variables"]["wl"].get()
            ww = max(1, panel["variables"]["ww"].get())
            panel["variables"]["ww"].set(ww)
            self.state_model.set_window(plane, wl, ww)
            self._render_plane(plane)

    def _on_canvas_resize(self, _event: tk.Event[Any]) -> None:
        if self.data is None:
            return
        if self._resize_job is not None:
            self.after_cancel(self._resize_job)
        self._resize_job = self.after(80, self._render_all)

    def _render_all(self) -> None:
        self._resize_job = None
        for plane in PLANES:
            self._render_plane(plane, draw_crosshair=False)
        self._draw_all_crosshairs()

    def _render_plane(self, plane: Plane, draw_crosshair: bool = True) -> None:
        if self.data is None or self.state_model is None:
            return
        panel = self.panels[plane]
        canvas: tk.Canvas = panel["canvas"]
        canvas.delete("image")
        canvas.delete("placeholder")
        image = extract_slice(
            self.data.volume, plane,
            self.state_model.z_idx, self.state_model.y_idx, self.state_model.x_idx,
        )
        wl, ww = self.state_model.windows[plane]
        image8 = window_to_uint8(image, wl, ww)
        row_spacing, column_spacing = plane_pixel_spacing(plane, self.data.spacing_zyx)
        canvas_width = max(canvas.winfo_width(), 2)
        canvas_height = max(canvas.winfo_height(), 2)
        rect = fit_image_rect(canvas_width, canvas_height, image.shape[0], image.shape[1], row_spacing, column_spacing)
        x0, y0, width, height = rect
        resized = Image.fromarray(image8, mode="L").resize(
            (max(1, round(width)), max(1, round(height))), Image.Resampling.BILINEAR
        )
        photo = ImageTk.PhotoImage(resized)
        canvas.create_image(x0, y0, image=photo, anchor="nw", tags="image")
        panel["photo"] = photo
        panel["rect"] = rect
        panel["shape"] = image.shape
        position = {"axial": self.state_model.z_idx, "coronal": self.state_model.y_idx, "sagittal": self.state_model.x_idx}[plane]
        total = {"axial": self.data.volume.shape[0], "coronal": self.data.volume.shape[1], "sagittal": self.data.volume.shape[2]}[plane]
        if plane == "axial":
            panel["info"].set(f"位置: {position + 1}/{total}  ({self.data.slice_positions[position]:.2f} mm)")
        else:
            panel["info"].set(f"位置: {position + 1}/{total}")
        panel["value_labels"]["position"].configure(text=str(position))
        panel["value_labels"]["wl"].configure(text=str(wl))
        panel["value_labels"]["ww"].configure(text=str(ww))
        if draw_crosshair:
            self._draw_crosshair(plane)

    def _draw_all_crosshairs(self) -> None:
        for plane in PLANES:
            self._draw_crosshair(plane)

    def _draw_crosshair(self, plane: Plane) -> None:
        if self.state_model is None:
            return
        panel = self.panels[plane]
        if "rect" not in panel:
            return
        canvas: tk.Canvas = panel["canvas"]
        canvas.delete("crosshair")
        rows, columns = panel["shape"]
        column, row = crosshair_indices(
            plane, self.state_model.z_idx, self.state_model.y_idx, self.state_model.x_idx
        )
        x, y = image_index_to_canvas(column, row, columns, rows, panel["rect"])
        x0, y0, width, height = panel["rect"]
        canvas.create_line(x, y0, x, y0 + height, fill="#ff4d4d", width=2, tags="crosshair")
        canvas.create_line(x0, y, x0 + width, y, fill="#35d6ff", width=2, tags="crosshair")


def run() -> None:
    MprViewer().mainloop()
