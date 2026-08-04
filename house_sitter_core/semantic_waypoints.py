"""Semantic waypoint registry for user-labelled simulation-only areas."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[1] / "config" / "semantic_waypoints.json"
)
FORBIDDEN_COORDINATE_FIELDS = frozenset({"x", "y", "yaw", "pose", "coordinates"})


class SemanticWaypointError(ValueError):
    """Raised when a semantic waypoint label is missing or unsafe."""


class SemanticWaypointRegistry:
    """Load and resolve user-labelled semantic areas before execution."""

    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH) -> None:
        self.path = path
        self.config = self._load_json(path)
        self.labels, self.alias_lookup = self._validate_config(self.config)

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

    @classmethod
    def _validate_config(
        cls, config: Dict[str, Any]
    ) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Optional[str]]]]:
        if config.get("schema_version") != "1.0":
            raise SemanticWaypointError("Unsupported semantic waypoint schema_version.")
        if config.get("simulation_only") is not True:
            raise SemanticWaypointError("Semantic waypoint registry must be simulation_only.")

        labels = config.get("labels")
        if not isinstance(labels, dict) or not labels:
            raise SemanticWaypointError("Semantic waypoint registry labels must be non-empty.")

        validated: Dict[str, Dict[str, Any]] = {}
        alias_lookup: Dict[str, Dict[str, Optional[str]]] = {}
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
            if FORBIDDEN_COORDINATE_FIELDS.intersection(entry):
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
        return validated, alias_lookup

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
