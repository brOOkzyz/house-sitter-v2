#!/usr/bin/env python3
"""Canonical scientific replay comparison for Phase 6 formal runs.

Wall-clock provenance is retained separately.  Simulation timestamps remain
scientific data and are never normalized away.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any


SCIENTIFIC_FIELDS = (
    "seed", "condition", "scenario_input", "task_spec", "verifier_decision",
    "resource_policy_decision", "execution_guard", "execution_result", "trace",
    "ground_truth", "observations", "route", "detections", "twin",
)
VOLATILE_EXECUTION_FIELDS = ("start_timestamp", "end_timestamp")
PROVENANCE_ONLY_FIELDS = ("run_id", "artifact_path", "output_path", "wall_clock_started_at", "wall_clock_finished_at")


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def scientific_payload(run: dict[str, Any]) -> dict[str, Any]:
    """Return the exhaustive scientific payload, rejecting hidden volatility."""
    missing = set(SCIENTIFIC_FIELDS) - set(run)
    if missing:
        raise ValueError(f"Replay input lacks scientific fields: {sorted(missing)}")
    if any(key in run for key in PROVENANCE_ONLY_FIELDS):
        raise ValueError("Provenance-only fields must not be embedded in a scientific replay input.")
    execution = deepcopy(run["execution_result"])
    if not isinstance(execution, dict):
        raise ValueError("execution_result must be an object.")
    provenance = {key: execution.pop(key) for key in VOLATILE_EXECUTION_FIELDS if key in execution}
    unknown_volatile = [key for key in execution if key in PROVENANCE_ONLY_FIELDS or key.endswith("_path") or key.endswith("_run_id")]
    if unknown_volatile:
        raise ValueError(f"Unclassified volatile execution fields: {sorted(unknown_volatile)}")
    payload = {key: deepcopy(run[key]) for key in SCIENTIFIC_FIELDS if key != "execution_result"}
    payload["execution_result"] = execution
    payload["scientific_schema"] = "phase6-scientific-replay-v1"
    payload["excluded_volatile_provenance"] = sorted(provenance)
    return payload


def scientific_hash(run: dict[str, Any]) -> str:
    return canonical_hash(scientific_payload(run))


def _diff(left: Any, right: Any, path: str = "$") -> list[str]:
    if type(left) is not type(right):
        return [path]
    if isinstance(left, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            next_path = f"{path}.{key}"
            if key not in left or key not in right:
                paths.append(next_path)
            else:
                paths.extend(_diff(left[key], right[key], next_path))
        return paths
    if isinstance(left, list):
        paths: list[str] = []
        for index in range(max(len(left), len(right))):
            next_path = f"{path}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(next_path)
            else:
                paths.extend(_diff(left[index], right[index], next_path))
        return paths
    return [] if left == right else [path]


def compare_replays(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    """Audit raw differences and fail if any non-provenance science differs."""
    first_science, second_science = scientific_payload(first), scientific_payload(second)
    raw_differences = _diff(first, second)
    scientific_differences = _diff(first_science, second_science)
    allowed = {f"$.execution_result.{name}" for name in VOLATILE_EXECUTION_FIELDS}
    unexpected_raw_differences = sorted(set(raw_differences) - allowed)
    return {
        "scientific_schema": "phase6-scientific-replay-v1",
        "first_scientific_hash": canonical_hash(first_science),
        "second_scientific_hash": canonical_hash(second_science),
        "raw_difference_paths": raw_differences,
        "allowed_volatile_difference_paths": sorted(set(raw_differences) & allowed),
        "unexpected_raw_difference_paths": unexpected_raw_differences,
        "scientific_difference_paths": scientific_differences,
        "scientifically_reproducible": not scientific_differences and not unexpected_raw_differences,
        "preserved_volatile_provenance": {"first": {key: first["execution_result"].get(key) for key in VOLATILE_EXECUTION_FIELDS}, "second": {key: second["execution_result"].get(key) for key in VOLATILE_EXECUTION_FIELDS}},
    }
