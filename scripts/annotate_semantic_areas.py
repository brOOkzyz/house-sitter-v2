#!/usr/bin/env python3
"""Offline GUI for user-labelled semantic map areas; it never starts ROS or navigation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.map_metadata import MapMetadataError, RosMapMetadata, load_ros_map  # noqa: E402
from house_sitter_core.semantic_annotation import (  # noqa: E402
    SemanticAnnotationError,
    SemanticAnnotationSession,
)
from house_sitter_core.semantic_waypoints import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    SemanticWaypointError,
    SemanticWaypointRegistry,
)


DEFAULT_OUTPUT = PROJECT_ROOT / "local_annotations" / "semantic_areas_draft.json"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline semantic-area annotation only; no ROS, Nav2, or robot execution."
    )
    parser.add_argument("--map", required=True, type=Path, help="ROS map YAML path")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--inspect-map", action="store_true", help="Print read-only map metadata")
    return parser.parse_args(argv)


def _format_bounds(metadata: RosMapMetadata) -> str:
    minimum_x, minimum_y, maximum_x, maximum_y = metadata.bounds
    return f"x=[{minimum_x:.6f}, {maximum_x:.6f}], y=[{minimum_y:.6f}, {maximum_y:.6f}]"


def inspect_map(metadata: RosMapMetadata) -> None:
    print(f"YAML path: {metadata.yaml_path}")
    print(f"image path: {metadata.image_path}")
    print(f"PGM format: {metadata.image.format}")
    print(f"width: {metadata.image.width}")
    print(f"height: {metadata.image.height}")
    print(f"resolution: {metadata.resolution}")
    print(f"origin: {list(metadata.origin)}")
    print(f"map bounds: {_format_bounds(metadata)}")
    print(f"negate: {metadata.negate}")
    print(f"occupied_thresh: {metadata.occupied_thresh}")
    print(f"free_thresh: {metadata.free_thresh}")


def _require_local_output(path: Path) -> Path:
    output = path.resolve()
    drafts_root = (PROJECT_ROOT / "local_annotations").resolve()
    try:
        output.relative_to(drafts_root)
    except ValueError as exc:
        raise SemanticAnnotationError(
            f"Draft output must be inside {drafts_root}."
        ) from exc
    return output


class AnnotationGui:
    """Small Tkinter-only editor that maps display clicks back to original PGM pixels."""

    def __init__(self, metadata: RosMapMetadata, session: SemanticAnnotationSession, output: Path):
        try:
            import tkinter as tk
            from tkinter import ttk
            from PIL import Image, ImageTk
        except ImportError as exc:  # pragma: no cover - environment-dependent
            raise SemanticAnnotationError(
                "Tkinter is not available; annotation GUI cannot be started."
            ) from exc

        self.tk = tk
        self.ttk = ttk
        self.ImageTk = ImageTk
        self.metadata = metadata
        self.session = session
        self.output = output
        self.root = tk.Tk()
        self.root.title("Offline Semantic Area Annotation (no ROS/Nav2)")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        image = Image.frombytes("L", (metadata.image.width, metadata.image.height), metadata.image.pixels)
        self.scale = min(1.0, 900 / metadata.image.width, 700 / metadata.image.height)
        display_size = (
            max(1, round(metadata.image.width * self.scale)),
            max(1, round(metadata.image.height * self.scale)),
        )
        if display_size != image.size:
            image = image.resize(display_size, Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(image)

        controls = ttk.Frame(self.root, padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="Canonical label").grid(row=0, column=0, sticky="w")
        self.label_value = tk.StringVar(value=sorted(session.registry.labels)[0])
        ttk.Combobox(
            controls, textvariable=self.label_value, values=sorted(session.registry.labels), state="readonly"
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Label(controls, text="map_id (manual)").grid(row=0, column=2, sticky="w")
        self.map_id_value = tk.StringVar()
        ttk.Entry(controls, textvariable=self.map_id_value, width=24).grid(row=0, column=3, sticky="ew", padx=4)
        ttk.Label(controls, text="frame_id").grid(row=0, column=4, sticky="w")
        self.frame_id_value = tk.StringVar(value="map")
        ttk.Entry(controls, textvariable=self.frame_id_value, width=10).grid(row=0, column=5, sticky="ew", padx=4)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        self.canvas = tk.Canvas(self.root, width=display_size[0], height=display_size[1], highlightthickness=0)
        self.canvas.pack(padx=8, pady=(0, 8))
        self.canvas.create_image(0, 0, anchor="nw", image=self.photo, tags="map")
        self.canvas.bind("<Button-1>", self._add_vertex)
        self.root.bind("<BackSpace>", self._undo)
        self.root.bind("<Escape>", self._clear)
        self.root.bind("<Return>", self._validate)

        buttons = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Undo", command=self._undo).pack(side="left")
        ttk.Button(buttons, text="Clear", command=self._clear).pack(side="left", padx=4)
        ttk.Button(buttons, text="Validate", command=self._validate).pack(side="left")
        ttk.Button(buttons, text="Export Draft", command=self._export).pack(side="left", padx=4)
        self.status = tk.StringVar(
            value="Click boundary vertices. map_id is manually supplied in this prototype; map fingerprinting is not implemented yet."
        )
        ttk.Label(self.root, textvariable=self.status, padding=(8, 0, 8, 8), wraplength=900).pack(fill="x")

    def _apply_fields(self) -> None:
        self.session.select_label(self.label_value.get())
        self.session.set_map_id(self.map_id_value.get())
        self.session.set_frame_id(self.frame_id_value.get())

    def _display_to_pixel(self, event) -> tuple[int, int] | None:
        if not 0 <= event.x < self.canvas.winfo_width() or not 0 <= event.y < self.canvas.winfo_height():
            return None
        pixel_x = min(self.metadata.image.width - 1, int(event.x / self.scale))
        pixel_y = min(self.metadata.image.height - 1, int(event.y / self.scale))
        return pixel_x, pixel_y

    def _add_vertex(self, event) -> None:
        pixel = self._display_to_pixel(event)
        if pixel is None:
            self.status.set("Click is outside the displayed map.")
            return
        try:
            self.session.add_pixel_vertex(*pixel)
            self.status.set(f"{len(self.session.pixel_vertices)} vertices selected.")
            self._redraw_polygon()
        except SemanticAnnotationError as exc:
            self.status.set(str(exc))

    def _undo(self, _event=None) -> None:
        self.session.undo_vertex()
        self.status.set(f"{len(self.session.pixel_vertices)} vertices selected.")
        self._redraw_polygon()

    def _clear(self, _event=None) -> None:
        self.session.clear_vertices()
        self.status.set("Current polygon cleared; nothing was saved.")
        self._redraw_polygon()

    def _redraw_polygon(self) -> None:
        self.canvas.delete("annotation")
        points = [((x + 0.5) * self.scale, (y + 0.5) * self.scale) for x, y in self.session.pixel_vertices]
        for x, y in points:
            radius = 3
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill="red", tags="annotation")
        if len(points) >= 2:
            self.canvas.create_line(*[coordinate for point in points for coordinate in point], fill="red", width=2, tags="annotation")
        if len(points) >= 3:
            self.canvas.create_line(*points[-1], *points[0], fill="orange", width=2, dash=(4, 2), tags="annotation")

    def _validate(self, _event=None) -> None:
        try:
            self._apply_fields()
            self.session.build_draft()
            self.status.set("Polygon is valid. Export Draft writes only the ignored local draft file.")
        except SemanticAnnotationError as exc:
            self.status.set(str(exc))

    def _export(self) -> None:
        try:
            self._apply_fields()
            written = self.session.export_draft(self.output)
            self.status.set(f"Validated local draft exported: {written}")
        except SemanticAnnotationError as exc:
            self.status.set(str(exc))

    def run(self) -> None:
        self.root.mainloop()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        metadata = load_ros_map(args.map)
        if args.inspect_map:
            inspect_map(metadata)
            return 0
        output = _require_local_output(args.output)
        registry = SemanticWaypointRegistry(args.registry)
        session = SemanticAnnotationSession(metadata, registry)
        AnnotationGui(metadata, session, output).run()
        return 0
    except (MapMetadataError, SemanticWaypointError, SemanticAnnotationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # Tk may fail in a headless session; keep CLI errors concise.
        if exc.__class__.__name__ == "TclError":
            print("Error: Tkinter is not available; annotation GUI cannot be started.", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
