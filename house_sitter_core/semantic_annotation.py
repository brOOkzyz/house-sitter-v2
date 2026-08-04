"""Offline user annotation sessions that export validated local registry drafts."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .map_coordinates import pixel_to_map
from .map_metadata import MapMetadataError, RosMapMetadata
from .semantic_waypoints import SemanticWaypointError, SemanticWaypointRegistry


class SemanticAnnotationError(ValueError):
    """Raised when an offline semantic-area draft is incomplete or unsafe."""


@dataclass
class SemanticAnnotationSession:
    """One local, unsaved user annotation; this class has no ROS or Nav2 dependency."""

    map_metadata: RosMapMetadata
    registry: SemanticWaypointRegistry
    canonical_label: str | None = None
    map_id: str = ""
    frame_id: str = "map"
    pixel_vertices: list[tuple[float, float]] = field(default_factory=list)

    def select_label(self, label: str) -> None:
        if not isinstance(label, str) or label not in self.registry.labels:
            raise SemanticAnnotationError(f"Unknown canonical semantic label: {label}")
        self.canonical_label = label

    def set_map_id(self, map_id: str) -> None:
        if not isinstance(map_id, str) or not map_id.strip() or map_id != map_id.strip():
            raise SemanticAnnotationError(
                "map_id must be a non-empty string without leading or trailing whitespace."
            )
        self.map_id = map_id

    def set_frame_id(self, frame_id: str) -> None:
        if not isinstance(frame_id, str) or not frame_id.strip() or frame_id != frame_id.strip():
            raise SemanticAnnotationError(
                "frame_id must be a non-empty string without leading or trailing whitespace."
            )
        self.frame_id = frame_id

    def add_pixel_vertex(self, pixel_x: float, pixel_y: float) -> tuple[float, float]:
        try:
            map_vertex = pixel_to_map(self.map_metadata, pixel_x, pixel_y)
        except MapMetadataError as exc:
            raise SemanticAnnotationError(str(exc)) from exc
        self.pixel_vertices.append((float(pixel_x), float(pixel_y)))
        return map_vertex

    def undo_vertex(self) -> None:
        if self.pixel_vertices:
            self.pixel_vertices.pop()

    def clear_vertices(self) -> None:
        self.pixel_vertices.clear()

    @property
    def map_vertices(self) -> list[tuple[float, float]]:
        try:
            return [pixel_to_map(self.map_metadata, x, y) for x, y in self.pixel_vertices]
        except MapMetadataError as exc:  # defensive: add_pixel_vertex already checks
            raise SemanticAnnotationError(str(exc)) from exc

    def build_draft(self) -> dict[str, Any]:
        if self.canonical_label is None:
            raise SemanticAnnotationError("A canonical semantic label must be selected.")
        if len(self.pixel_vertices) < 3:
            raise SemanticAnnotationError("At least three polygon vertices are required.")
        self.set_map_id(self.map_id)
        self.set_frame_id(self.frame_id)

        draft = copy.deepcopy(self.registry.config)
        entry = draft["labels"][self.canonical_label]
        entry.update(
            {
                "grounding_mode": "user_labelled_map_area",
                "mapping_status": "mapped",
                "frame_id": self.frame_id,
                "geometry": {
                    "type": "polygon",
                    "vertices": [[x, y] for x, y in self.map_vertices],
                },
                "source": {"type": "user_annotation", "map_id": self.map_id},
            }
        )
        try:
            SemanticWaypointRegistry.from_config(draft)
        except SemanticWaypointError as exc:
            raise SemanticAnnotationError(str(exc)) from exc
        return draft

    def export_draft(self, output_path: Path) -> Path:
        output = Path(output_path).resolve()
        if self.registry.path is not None and output == Path(self.registry.path).resolve():
            raise SemanticAnnotationError("Refusing to overwrite the production semantic registry.")
        draft = self.build_draft()
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            raise SemanticAnnotationError(f"Cannot write annotation draft {output}: {exc}") from exc
        return output
