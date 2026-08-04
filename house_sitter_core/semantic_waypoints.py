"""Semantic waypoint registry for user-labelled simulation-only areas."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "semantic_waypoints.json"
)
FORBIDDEN_COORDINATE_FIELDS = frozenset({"x", "y", "yaw", "pose", "coordinates"})
MAPPING_STATUSES = frozenset({"unmapped", "mapped"})
GEOMETRY_EPSILON = 1e-12
# Segment-contact tolerance is deliberately smaller than the minimum accepted
# polygon area so a valid, very small polygon is not mistaken for collinear edges.
INTERSECTION_EPSILON = 1e-15
ANNOTATION_SOURCE_FIELDS = frozenset({"type", "map_id"})


class SemanticWaypointError(ValueError):
    """Raised when a semantic waypoint label is missing or unsafe."""


@dataclass(frozen=True)
class PolygonGeometry:
    """A user-annotated polygon expressed in a named map frame."""

    frame_id: str
    vertices: Tuple[Tuple[float, float], ...]


@dataclass(frozen=True)
class SemanticArea:
    """Validated local semantic-area metadata; never supplied by an LLM."""

    label: str
    mapping_status: str
    grounding_mode: str
    geometry: Optional[PolygonGeometry]
    map_id: Optional[str]


class SemanticWaypointRegistry:
    """Load and resolve user-labelled semantic areas before execution."""

    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = path
        self.config = self._load_json(path)
        self.labels, self.alias_lookup, self.areas = self._validate_config(self.config)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "SemanticWaypointRegistry":
        """Validate an in-memory registry draft through the same production rules."""
        if not isinstance(config, dict):
            raise SemanticWaypointError("Semantic waypoint registry must be an object.")
        registry = cls.__new__(cls)
        registry.path = None
        registry.config = copy.deepcopy(config)
        registry.labels, registry.alias_lookup, registry.areas = registry._validate_config(
            registry.config
        )
        return registry

    @classmethod
    def validate_polygon_geometry(
        cls, label: str, frame_id: Any, geometry: Any
    ) -> PolygonGeometry:
        """Validate candidate geometry through the registry's sole polygon implementation."""
        return cls._validate_polygon(label, frame_id, geometry)

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise SemanticWaypointError(
                f"Cannot load semantic waypoint registry {path}: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise SemanticWaypointError("Semantic waypoint registry must be an object.")
        return value

    @staticmethod
    def _normalize_expression(value: str) -> str:
        return re.sub(r"[\s_-]+", " ", value.strip().lower())

    @classmethod
    def _validate_alias_value(cls, label: str, alias: Any) -> str:
        if not isinstance(alias, str) or not alias.strip():
            raise SemanticWaypointError(
                f"Semantic waypoint {label} aliases must be non-empty strings."
            )
        normalized = cls._normalize_expression(alias)
        tokens = normalized.split()
        if any(token in FORBIDDEN_COORDINATE_FIELDS for token in tokens):
            raise SemanticWaypointError(
                f"Semantic waypoint {label} alias must not contain coordinate-like fields: {alias}"
            )
        return normalized

    @classmethod
    def _register_expression(
        cls,
        *,
        expression: str,
        canonical_label: str,
        alias_lookup: Dict[str, Dict[str, Optional[str]]],
        matched_alias: Optional[str],
    ) -> None:
        normalized = cls._normalize_expression(expression)
        existing = alias_lookup.get(normalized)
        if existing is not None and existing["canonical_label"] != canonical_label:
            raise SemanticWaypointError(
                "Semantic waypoint alias conflict: "
                f"{matched_alias!r} maps to both {existing['canonical_label']} and {canonical_label}."
            )
        alias_lookup[normalized] = {
            "canonical_label": canonical_label,
            "matched_alias": matched_alias,
        }

    @staticmethod
    def _as_finite_coordinate(label: str, index: int, value: Any) -> Tuple[float, float]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise SemanticWaypointError(
                f"Semantic area {label} polygon vertex {index} must contain exactly two coordinates."
            )
        coordinates = []
        for coordinate in value:
            if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
                raise SemanticWaypointError(
                    f"Semantic area {label} polygon coordinates must be finite numbers."
                )
            numeric = float(coordinate)
            if not math.isfinite(numeric):
                raise SemanticWaypointError(
                    f"Semantic area {label} polygon coordinates must be finite numbers."
                )
            coordinates.append(numeric)
        return coordinates[0], coordinates[1]

    @staticmethod
    def _signed_area(vertices: Tuple[Tuple[float, float], ...]) -> float:
        return sum(
            x1 * y2 - x2 * y1
            for (x1, y1), (x2, y2) in zip(vertices, vertices[1:] + vertices[:1])
        ) / 2.0

    @staticmethod
    def _orientation(
        first: Tuple[float, float], second: Tuple[float, float], third: Tuple[float, float]
    ) -> float:
        return (second[0] - first[0]) * (third[1] - first[1]) - (
            second[1] - first[1]
        ) * (third[0] - first[0])

    @classmethod
    def _point_on_segment(
        cls,
        first: Tuple[float, float],
        second: Tuple[float, float],
        point: Tuple[float, float],
    ) -> bool:
        """Return whether point lies on the closed segment, within the shared tolerance."""
        return (
            abs(cls._orientation(first, second, point)) <= INTERSECTION_EPSILON
            and min(first[0], second[0]) - INTERSECTION_EPSILON
            <= point[0]
            <= max(first[0], second[0]) + INTERSECTION_EPSILON
            and min(first[1], second[1]) - INTERSECTION_EPSILON
            <= point[1]
            <= max(first[1], second[1]) + INTERSECTION_EPSILON
        )

    @classmethod
    def _segments_intersect_or_touch(
        cls,
        first: Tuple[float, float],
        second: Tuple[float, float],
        third: Tuple[float, float],
        fourth: Tuple[float, float],
    ) -> bool:
        """Detect every intersection, overlap, or endpoint touch between two segments."""
        first_orientation = cls._orientation(first, second, third)
        second_orientation = cls._orientation(first, second, fourth)
        third_orientation = cls._orientation(third, fourth, first)
        fourth_orientation = cls._orientation(third, fourth, second)

        if (
            (abs(first_orientation) <= INTERSECTION_EPSILON and cls._point_on_segment(first, second, third))
            or (abs(second_orientation) <= INTERSECTION_EPSILON and cls._point_on_segment(first, second, fourth))
            or (abs(third_orientation) <= INTERSECTION_EPSILON and cls._point_on_segment(third, fourth, first))
            or (abs(fourth_orientation) <= INTERSECTION_EPSILON and cls._point_on_segment(third, fourth, second))
        ):
            return True

        return (
            (first_orientation > INTERSECTION_EPSILON and second_orientation < -INTERSECTION_EPSILON)
            or (first_orientation < -INTERSECTION_EPSILON and second_orientation > INTERSECTION_EPSILON)
        ) and (
            (third_orientation > INTERSECTION_EPSILON and fourth_orientation < -INTERSECTION_EPSILON)
            or (third_orientation < -INTERSECTION_EPSILON and fourth_orientation > INTERSECTION_EPSILON)
        )

    @classmethod
    def _polygon_is_simple(cls, vertices: Tuple[Tuple[float, float], ...]) -> bool:
        """Ensure non-adjacent polygon edges neither cross, touch, nor overlap."""
        count = len(vertices)
        for first in range(count):
            first_end = (first + 1) % count
            for second in range(first + 1, count):
                second_end = (second + 1) % count
                shared_vertices = {first, first_end}.intersection({second, second_end})
                if shared_vertices:
                    # Adjacent edges may meet at their one expected vertex, but may
                    # not retrace or overlap beyond it.
                    first_other = next(index for index in (first, first_end) if index not in shared_vertices)
                    second_other = next(index for index in (second, second_end) if index not in shared_vertices)
                    if cls._point_on_segment(
                        vertices[first], vertices[first_end], vertices[second_other]
                    ) or cls._point_on_segment(
                        vertices[second], vertices[second_end], vertices[first_other]
                    ):
                        return False
                    continue
                if cls._segments_intersect_or_touch(
                    vertices[first], vertices[first_end], vertices[second], vertices[second_end]
                ):
                    return False
        return True

    @classmethod
    def _vertices_are_collinear(cls, vertices: Tuple[Tuple[float, float], ...]) -> bool:
        first, second = vertices[0], vertices[1]
        return all(
            abs(cls._orientation(first, second, vertex)) <= INTERSECTION_EPSILON
            for vertex in vertices[2:]
        )

    @classmethod
    def _validate_polygon(
        cls, label: str, frame_id: Any, geometry: Any
    ) -> PolygonGeometry:
        if not isinstance(frame_id, str) or not frame_id.strip():
            raise SemanticWaypointError(f"Semantic area {label} frame_id must be a non-empty string.")
        if not isinstance(geometry, dict) or geometry.get("type") != "polygon":
            raise SemanticWaypointError(f"Semantic area {label} geometry.type must be polygon.")
        raw_vertices = geometry.get("vertices")
        if not isinstance(raw_vertices, list):
            raise SemanticWaypointError(f"Semantic area {label} polygon vertices must be a list.")
        vertices = tuple(
            cls._as_finite_coordinate(label, index, value)
            for index, value in enumerate(raw_vertices, start=1)
        )
        # A single exact closing vertex is conventional JSON polygon syntax.  It is
        # removed before validation; near-equal vertices remain ordinary vertices.
        if len(vertices) > 1 and vertices[0] == vertices[-1]:
            vertices = vertices[:-1]
        if len(vertices) < 3 or len(set(vertices)) < 3:
            raise SemanticWaypointError(
                f"Semantic area {label} polygon must contain at least three distinct vertices."
            )
        if len(set(vertices)) != len(vertices):
            raise SemanticWaypointError(f"Semantic area {label} polygon vertices must not repeat.")
        if cls._vertices_are_collinear(vertices):
            raise SemanticWaypointError(f"Semantic area {label} polygon area must be greater than zero.")
        if not cls._polygon_is_simple(vertices):
            raise SemanticWaypointError(f"Semantic area {label} polygon must not self-intersect.")
        if abs(cls._signed_area(vertices)) <= GEOMETRY_EPSILON:
            raise SemanticWaypointError(f"Semantic area {label} polygon area must be greater than zero.")
        return PolygonGeometry(frame_id=frame_id, vertices=vertices)

    @staticmethod
    def _validate_annotation_source(label: str, source: Any) -> str:
        if not isinstance(source, dict) or set(source) != ANNOTATION_SOURCE_FIELDS:
            raise SemanticWaypointError(
                f"Semantic area {label} source fields must be exactly type and map_id."
            )
        if source["type"] != "user_annotation":
            raise SemanticWaypointError(
                f"Semantic area {label} source.type must be user_annotation."
            )
        map_id = source["map_id"]
        if (
            not isinstance(map_id, str)
            or not map_id.strip()
            or map_id != map_id.strip()
        ):
            raise SemanticWaypointError(
                f"Semantic area {label} source.map_id must be a non-empty string without leading or trailing whitespace."
            )
        return map_id

    @classmethod
    def _validate_config(
        cls, config: Dict[str, Any]
    ) -> tuple[
        Dict[str, Dict[str, Any]],
        Dict[str, Dict[str, Optional[str]]],
        Dict[str, SemanticArea],
    ]:
        if config.get("schema_version") != "1.0":
            raise SemanticWaypointError("Unsupported semantic waypoint schema_version.")
        if config.get("simulation_only") is not True:
            raise SemanticWaypointError("Semantic waypoint registry must be simulation_only.")

        labels = config.get("labels")
        if not isinstance(labels, dict) or not labels:
            raise SemanticWaypointError("Semantic waypoint registry labels must be non-empty.")

        validated: Dict[str, Dict[str, Any]] = {}
        alias_lookup: Dict[str, Dict[str, Optional[str]]] = {}
        areas: Dict[str, SemanticArea] = {}
        for key, entry in labels.items():
            if not isinstance(key, str) or not key.strip():
                raise SemanticWaypointError("Semantic waypoint label keys must be strings.")
            if not isinstance(entry, dict):
                raise SemanticWaypointError(f"Semantic waypoint {key} must be an object.")
            if entry.get("label") != key:
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} must repeat its label field."
                )
            if entry.get("simulation_only") is not True:
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} must be simulation_only."
                )
            if entry.get("validated") is not True:
                raise SemanticWaypointError(f"Semantic waypoint {key} is not validated.")
            grounding_mode = entry.get("grounding_mode")
            if grounding_mode not in {"simulation_safe_nearby_goal", "user_labelled_map_area"}:
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} has unsupported grounding_mode."
                )
            mapping_status = entry.get("mapping_status")
            if mapping_status not in MAPPING_STATUSES:
                raise SemanticWaypointError(
                    f"Semantic area {key} mapping_status must be mapped or unmapped."
                )
            if FORBIDDEN_COORDINATE_FIELDS.intersection(entry):
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} must not contain direct coordinate fields."
                )

            if mapping_status == "mapped":
                if grounding_mode != "user_labelled_map_area":
                    raise SemanticWaypointError(
                        f"Semantic area {key} mapped entries must use user_labelled_map_area."
                    )
                polygon = cls._validate_polygon(key, entry.get("frame_id"), entry.get("geometry"))
                map_id = cls._validate_annotation_source(key, entry.get("source"))
            else:
                if grounding_mode != "simulation_safe_nearby_goal":
                    raise SemanticWaypointError(
                        f"Semantic area {key} unmapped entries must use simulation_safe_nearby_goal."
                    )
                if any(entry.get(field) is not None for field in ("frame_id", "geometry", "source")):
                    raise SemanticWaypointError(
                        f"Semantic area {key} unmapped entries must not contain map geometry."
                    )
                polygon = None
                map_id = None

            execution_target = entry.get("execution_target")
            if not isinstance(execution_target, dict):
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} execution_target must be an object."
                )
            if execution_target.get("type") != "simulation_safe_nearby_goal":
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} has unsupported execution_target."
                )

            aliases = entry.get("aliases")
            if not isinstance(aliases, list):
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} aliases must be a list."
                )
            cls._register_expression(
                expression=key,
                canonical_label=key,
                alias_lookup=alias_lookup,
                matched_alias=None,
            )
            validated_aliases: list[str] = []
            for alias in aliases:
                cls._validate_alias_value(key, alias)
                cls._register_expression(
                    expression=alias,
                    canonical_label=key,
                    alias_lookup=alias_lookup,
                    matched_alias=alias,
                )
                validated_aliases.append(alias)

            validated_entry = copy.deepcopy(entry)
            validated_entry["aliases"] = validated_aliases
            validated[key] = validated_entry
            areas[key] = SemanticArea(
                label=key,
                mapping_status=mapping_status,
                grounding_mode=grounding_mode,
                geometry=polygon,
                map_id=map_id,
            )
        return validated, alias_lookup, areas

    def has_label(self, label: str) -> bool:
        if not isinstance(label, str) or not label.strip():
            return False
        return self._normalize_expression(label) in self.alias_lookup

    def resolve(self, label: str) -> Dict[str, Any]:
        if not isinstance(label, str) or not label.strip():
            raise SemanticWaypointError("Semantic waypoint label must be a non-empty string.")
        normalized = self._normalize_expression(label)
        match = self.alias_lookup.get(normalized)
        if match is None:
            raise SemanticWaypointError(f"Unknown semantic waypoint label: {label}")
        canonical_label = match["canonical_label"]
        entry = copy.deepcopy(self.labels[canonical_label])
        entry["original_input"] = label
        entry["matched_alias"] = match["matched_alias"]
        entry["canonical_label"] = canonical_label
        return entry

    def match_prompt_expression(self, prompt: str) -> Optional[str]:
        normalized_prompt = self._normalize_expression(prompt)
        matches = [
            data["matched_alias"] or data["canonical_label"]
            for expression, data in self.alias_lookup.items()
            if f" {expression} " in f" {normalized_prompt} "
        ]
        if not matches:
            return None
        return max(matches, key=len)


def load_semantic_waypoint_registry(
    path: Optional[Path] = None,
) -> SemanticWaypointRegistry:
    return SemanticWaypointRegistry(path or DEFAULT_REGISTRY_PATH)


def semantic_label_exists(label: str, path: Optional[Path] = None) -> bool:
    return load_semantic_waypoint_registry(path).has_label(label)


def resolve_semantic_label(label: str, path: Optional[Path] = None) -> Dict[str, Any]:
    return load_semantic_waypoint_registry(path).resolve(label)
