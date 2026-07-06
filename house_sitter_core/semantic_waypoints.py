"""Semantic waypoint registry for user-labelled simulation-only areas."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "semantic_waypoints.json"
)


class SemanticWaypointError(ValueError):
    """Raised when a semantic waypoint label is missing or unsafe."""


class SemanticWaypointRegistry:
    """Load and resolve user-labelled semantic areas before execution."""

    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = path
        self.config = self._load_json(path)
        self.labels = self._validate_config(self.config)

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
    def _validate_config(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        if config.get("schema_version") != "1.0":
            raise SemanticWaypointError("Unsupported semantic waypoint schema_version.")
        if config.get("simulation_only") is not True:
            raise SemanticWaypointError("Semantic waypoint registry must be simulation_only.")

        labels = config.get("labels")
        if not isinstance(labels, dict) or not labels:
            raise SemanticWaypointError("Semantic waypoint registry labels must be non-empty.")

        validated: Dict[str, Dict[str, Any]] = {}
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
            if entry.get("grounding_mode") != "simulation_safe_nearby_goal":
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} has unsupported grounding_mode."
                )
            forbidden_coordinate_fields = {"x", "y", "yaw", "pose", "coordinates"}
            if forbidden_coordinate_fields.intersection(entry):
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} must not contain coordinates."
                )

            execution_target = entry.get("execution_target")
            if not isinstance(execution_target, dict):
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} execution_target must be an object."
                )
            if execution_target.get("type") != "simulation_safe_nearby_goal":
                raise SemanticWaypointError(
                    f"Semantic waypoint {key} has unsupported execution_target."
                )

            validated[key] = entry
        return validated

    def has_label(self, label: str) -> bool:
        return label in self.labels

    def resolve(self, label: str) -> Dict[str, Any]:
        try:
            entry = self.labels[label]
        except KeyError as exc:
            raise SemanticWaypointError(f"Unknown semantic waypoint label: {label}") from exc
        return copy.deepcopy(entry)


def load_semantic_waypoint_registry(
    path: Optional[Path] = None,
) -> SemanticWaypointRegistry:
    return SemanticWaypointRegistry(path or DEFAULT_REGISTRY_PATH)


def semantic_label_exists(label: str, path: Optional[Path] = None) -> bool:
    return load_semantic_waypoint_registry(path).has_label(label)


def resolve_semantic_label(label: str, path: Optional[Path] = None) -> Dict[str, Any]:
    return load_semantic_waypoint_registry(path).resolve(label)
