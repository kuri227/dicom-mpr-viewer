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

PLANE_TITLES = {
    "axial": "Axial（横断面）",
    "coronal": "Coronal（冠状断面）",
    "sagittal": "Sagittal（矢状断面）",
}

CANVAS_BACKGROUND = "#0d1117"
PLACEHOLDER_COLOR = "#c7d0d9"
VERTICAL_LINE_COLOR = "#ff5a5f"
HORIZONTAL_LINE_COLOR = "#35c8e6"


class MprViewer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("DICOM MPR Viewer")
        self._configure_window_size()
        self.data: DicomVolume | None = None
        self.state_model: ViewerState | None = None
        self.panels: dict[Plane, dict[str, Any]] = {}
        self.status = tk.StringVar(value="ファイルメニューからDICOMデータを開いてください")
        self._resize_job: str | None = None
        self._configure_styles()
        self._build_menu()
        self._build_layout()

    def _configure_window_size(self) -> None:
        """Keep the initial window inside the usable area on common laptops."""
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1280, max(900, screen_width - 80))
        height = min(760, max(600, screen_height - 120))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 3)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(min(900, screen_width - 40), min(600, screen_height - 80))

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("Plane.TLabelframe", padding=8)
        style.configure("Plane.TLabelframe.Label", font=("Segoe UI", 10, "bold"))
        style.configure("Info.TLabel", font=("Segoe UI", 9, "bold"))
        style.configure("Legend.TLabel", foreground="#4a5560")
        style.configure("Value.TLabel", font=("Segoe UI", 9, "bold"), anchor="e")

    def _build_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(
            label="DICOM ZIPを開く...", accelerator="Ctrl+O", command=self._choose_zip
        )
        file_menu.add_command(
            label="DICOMフォルダーを開く...", accelerator="Ctrl+Shift+O", command=self._choose_folder
        )
        file_menu.add_separator()
        file_menu.add_command(label="終了", command=self.destroy)
        menu.add_cascade(label="ファイル", menu=file_menu)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="操作方法", command=self._show_help)
        menu.add_cascade(label="ヘルプ", menu=help_menu)
        self.config(menu=menu)
        self.bind_all("<Control-o>", lambda _event: self._choose_zip())
        self.bind_all("<Control-Shift-O>", lambda _event: self._choose_folder())

    def _show_help(self) -> None:
        messagebox.showinfo(
            "操作方法",
            "1. ファイルメニューからDICOM ZIPまたはフォルダーを開きます。\n"
            "2. 各断面の［位置］でスライスを移動します。\n"
            "3. WL / WWで、その断面の濃淡を調整します。\n\n"
            "赤い縦線と水色の横線は、他の2断面の位置を示します。",
            parent=self,
        )

    def _build_layout(self) -> None:
        container = ttk.Frame(self, padding=(8, 8, 8, 6))
        container.pack(fill="both", expand=True)
        for column, plane in enumerate(PLANES):
            container.columnconfigure(column, weight=1, uniform="panels")
            panel = ttk.LabelFrame(container, text=PLANE_TITLES[plane], style="Plane.TLabelframe")
            panel.grid(row=0, column=column, sticky="nsew", padx=4)
            panel.columnconfigure(0, weight=1)
            panel.rowconfigure(0, weight=1)

            canvas = tk.Canvas(
                panel,
                bg=CANVAS_BACKGROUND,
                highlightthickness=0,
                width=360,
                height=360,
                takefocus=True,
            )
            canvas.grid(row=0, column=0, sticky="nsew")
            canvas.create_text(
                180,
                180,
                text="DICOMデータ未読み込み\nファイルメニューからZIPまたはフォルダーを開いてください",
                fill=PLACEHOLDER_COLOR,
                justify="center",
                width=300,
                tags="placeholder",
            )
            canvas.bind("<Configure>", self._on_canvas_resize)

            controls = ttk.Frame(panel, padding=(2, 8, 2, 0))
            controls.grid(row=1, column=0, sticky="ew")
            controls.columnconfigure(1, weight=1)
            info = tk.StringVar(value="位置: -")
            ttk.Label(controls, textvariable=info, style="Info.TLabel").grid(
                row=0, column=0, columnspan=2, sticky="w"
            )
            ttk.Label(
                controls,
                text="縦: 赤 / 横: 水色",
                style="Legend.TLabel",
            ).grid(row=0, column=2, sticky="e")
            ttk.Separator(controls).grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 4))
            variables: dict[str, tk.IntVar] = {
                "position": tk.IntVar(value=0), "wl": tk.IntVar(value=40), "ww": tk.IntVar(value=400)
            }
            scales: dict[str, ttk.Scale] = {}
            value_labels: dict[str, ttk.Label] = {}
            for row, (key, label, lower, upper) in enumerate(
                (("position", "位置", 0, 1), ("wl", "WL", -1200, 1500), ("ww", "WW", 1, 3000)), start=2
            ):
                ttk.Label(controls, text=label, width=5).grid(row=row, column=0, sticky="w", pady=2)
                scale = ttk.Scale(
                    controls, from_=lower, to=upper, variable=variables[key],
                    command=lambda value, p=plane, k=key: self._on_slider(p, k, value),
                )
                scale.state(["disabled"])
                scale.grid(row=row, column=1, sticky="ew", padx=(6, 8), pady=2)
                value_label = ttk.Label(controls, text="-", width=7, style="Value.TLabel")
                value_label.grid(row=row, column=2, sticky="e")
                scales[key] = scale
                value_labels[key] = value_label
            self.panels[plane] = {
                "canvas": canvas, "info": info, "variables": variables,
                "scales": scales, "value_labels": value_labels, "photo": None,
            }
        container.rowconfigure(0, weight=1)
        ttk.Separator(self).pack(fill="x")
        tk.Label(
            self,
            textvariable=self.status,
            anchor="w",
            bg="#26323d",
            fg="#f4f7fa",
            padx=10,
            pady=6,
        ).pack(fill="x")

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
        self.status.set(
            f"読み込み完了: {Path(path).name}  |  "
            f"{z_count}枚  |  ボリューム {z_count}×{y_count}×{x_count} (z×y×x)"
        )
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

    def _on_canvas_resize(self, event: tk.Event[Any]) -> None:
        if self.data is None:
            canvas = event.widget
            if isinstance(canvas, tk.Canvas):
                canvas.coords("placeholder", event.width / 2, event.height / 2)
                canvas.itemconfigure("placeholder", width=max(80, event.width - 40))
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
        panel["value_labels"]["position"].configure(text=str(position + 1))
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
        canvas.create_line(x, y0, x, y0 + height, fill=VERTICAL_LINE_COLOR, width=2, tags="crosshair")
        canvas.create_line(x0, y, x0 + width, y, fill=HORIZONTAL_LINE_COLOR, width=2, tags="crosshair")


def run() -> None:
    MprViewer().mainloop()
