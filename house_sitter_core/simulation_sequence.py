"""Deterministic, review-only sequencing of synthetic demo safe-goal artifacts."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .map_metadata import MapIdentity


class SimulationSequenceError(ValueError):
    """Raised when synthetic demo artifacts cannot form a safe local sequence."""


DEFAULT_SEQUENCE = ("living_room", "kitchen", "bedroom", "charging_area")


def _load_object(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SimulationSequenceError(f"Cannot load {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise SimulationSequenceError(f"{name} must contain a JSON object.")
    return value


def load_sequence_inputs(regions_path: Path, safe_goals_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load two local artifacts; this function never interprets map geometry."""
    return _load_object(regions_path, "semantic regions"), _load_object(safe_goals_path, "safe goals")


def _identifier(record: dict[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise SimulationSequenceError(f"{field} must be a non-empty trimmed string.")
    return value


def _integer(record: dict[str, Any], field: str, *, positive: bool = False) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or (positive and value <= 0):
        qualifier = "positive integer" if positive else "integer"
        raise SimulationSequenceError(f"{field} must be a {qualifier}.")
    return value


def _number(record: dict[str, Any], field: str) -> float:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SimulationSequenceError(f"{field} must be a finite number.")
    return float(value)


_MAP_IDENTITY_FIELDS = tuple(MapIdentity.__dataclass_fields__)
_MAP_IDENTITY_SCHEMA_VERSION = "1.0"
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _map_identity(document: dict[str, Any], name: str) -> dict[str, Any]:
    identity = document.get("map_identity")
    if not isinstance(identity, dict) or set(identity) != set(_MAP_IDENTITY_FIELDS):
        raise SimulationSequenceError(f"{name} map_identity has an invalid field set.")
    if identity["schema_version"] != _MAP_IDENTITY_SCHEMA_VERSION:
        raise SimulationSequenceError(f"{name} map_identity.schema_version is unsupported.")
    normalized: dict[str, Any] = {"schema_version": identity["schema_version"]}
    for field in ("width", "height"):
        value = identity[field]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise SimulationSequenceError(f"{name} map_identity.{field} must be a positive integer.")
        normalized[field] = value
    resolution = _finite_identity_number(identity["resolution"], f"{name} map_identity.resolution")
    if resolution <= 0:
        raise SimulationSequenceError(f"{name} map_identity.resolution must be greater than zero.")
    normalized["resolution"] = resolution
    origin = identity["origin"]
    if not isinstance(origin, list) or len(origin) != 3:
        raise SimulationSequenceError(f"{name} map_identity.origin must be a three-item list.")
    normalized["origin"] = [_finite_identity_number(value, f"{name} map_identity.origin") for value in origin]
    negate = identity["negate"]
    if isinstance(negate, bool) or not isinstance(negate, int) or negate not in {0, 1}:
        raise SimulationSequenceError(f"{name} map_identity.negate must be 0 or 1.")
    normalized["negate"] = negate
    for field in ("occupied_thresh", "free_thresh"):
        value = _finite_identity_number(identity[field], f"{name} map_identity.{field}")
        if not 0 <= value <= 1:
            raise SimulationSequenceError(f"{name} map_identity.{field} must be between 0 and 1.")
        normalized[field] = value
    for field in ("image_sha256", "fingerprint"):
        value = identity[field]
        if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
            raise SimulationSequenceError(f"{name} map_identity.{field} must be a lowercase SHA-256 string.")
        normalized[field] = value
    return normalized


def _finite_identity_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SimulationSequenceError(f"{name} must be a finite number.")
    return float(value)


def _require_demo_flags(record: dict[str, Any], name: str) -> None:
    if record.get("review_only") is not True:
        raise SimulationSequenceError(f"{name} must be review_only: true.")
    if record.get("executable") is not False:
        raise SimulationSequenceError(f"{name} must be executable: false.")


def _validate_sequence(sequence: Iterable[str]) -> tuple[str, ...]:
    labels = tuple(sequence)
    if not labels:
        raise SimulationSequenceError("requested sequence must contain at least one label.")
    if any(not isinstance(label, str) or not label or label != label.strip() for label in labels):
        raise SimulationSequenceError("requested sequence labels must be non-empty trimmed strings.")
    if len(set(labels)) != len(labels):
        raise SimulationSequenceError("requested sequence contains a duplicate label.")
    return labels


def _finite_control(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SimulationSequenceError(f"{name} must be a finite number.")
    numeric = float(value)
    if positive and numeric <= 0:
        raise SimulationSequenceError(f"{name} must be greater than zero.")
    if not positive and numeric < 0:
        raise SimulationSequenceError(f"{name} must be non-negative.")
    return numeric


def _validate_controls(
    requested: tuple[str, ...],
    *,
    fail_label: str | None,
    cancel_before_label: str | None,
    timeout_seconds: float | None,
    step_durations: dict[str, float] | None,
) -> tuple[float | None, dict[str, float]]:
    if fail_label is not None and fail_label not in requested:
        raise SimulationSequenceError("fail-label must belong to the requested sequence.")
    if cancel_before_label is not None and cancel_before_label not in requested:
        raise SimulationSequenceError("cancel-before-label must belong to the requested sequence.")
    if fail_label is not None and fail_label == cancel_before_label:
        raise SimulationSequenceError("fail-label and cancel-before-label cannot target the same step.")
    timeout = None if timeout_seconds is None else _finite_control(timeout_seconds, "timeout-seconds", positive=True)
    durations: dict[str, float] = {}
    for label, duration in (step_durations or {}).items():
        if label not in requested:
            raise SimulationSequenceError("step-duration label must belong to the requested sequence.")
        durations[label] = _finite_control(duration, f"step-duration {label}")
    return timeout, durations


def _region_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = document.get("regions")
    if not isinstance(records, list):
        raise SimulationSequenceError("semantic regions must contain a regions list.")
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise SimulationSequenceError("region entries must be objects.")
        _require_demo_flags(record, "region")
        label = _identifier(record, "canonical_label")
        if label in indexed:
            raise SimulationSequenceError(f"duplicate region label: {label}")
        _identifier(record, "proposal_id")
        _identifier(record, "partition_id")
        _integer(record, "source_candidate_order", positive=True)
        rank = record.get("source_selection_rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int)):
            raise SimulationSequenceError("source_selection_rank must be an integer or null.")
        _integer(record, "demo_assignment_order", positive=True)
        indexed[label] = record
    return indexed


def _goal_index(document: dict[str, Any], regions: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    goals = document.get("goals")
    accepted_count = document.get("accepted_goal_count")
    if not isinstance(goals, list) or isinstance(accepted_count, bool) or not isinstance(accepted_count, int):
        raise SimulationSequenceError("safe goals must contain goals and accepted_goal_count.")
    if accepted_count != len(goals):
        raise SimulationSequenceError("safe goals accepted_goal_count does not match goals.")
    _require_demo_flags(document, "safe-goal document")
    indexed: dict[str, dict[str, Any]] = {}
    for goal in goals:
        if not isinstance(goal, dict):
            raise SimulationSequenceError("safe goal entries must be objects.")
        _require_demo_flags(goal, "safe goal")
        if goal.get("simulation_only") is not True:
            raise SimulationSequenceError("safe goal must be simulation_only: true.")
        label = _identifier(goal, "canonical_label")
        if label in indexed:
            raise SimulationSequenceError(f"multiple safe goals for label: {label}")
        region = regions.get(label)
        if region is None:
            raise SimulationSequenceError(f"safe goal label has no matching region: {label}")
        proposal_id = _identifier(goal, "proposal_id")
        partition_id = _identifier(goal, "candidate_partition_id")
        if (proposal_id, partition_id) != (region["proposal_id"], region["partition_id"]):
            raise SimulationSequenceError(f"region and safe goal source mismatch for label: {label}")
        for field in ("source_candidate_order", "demo_assignment_order", "goal_order"):
            _integer(goal, field, positive=True)
        rank = goal.get("source_selection_rank")
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int)):
            raise SimulationSequenceError("source_selection_rank must be an integer or null.")
        for field in ("source_candidate_order", "source_selection_rank", "demo_assignment_order"):
            if goal.get(field) != region.get(field):
                raise SimulationSequenceError(f"region and safe goal provenance mismatch: {field}")
        goal_data = goal.get("goal")
        if not isinstance(goal_data, dict):
            raise SimulationSequenceError("safe goal goal must be an object.")
        _integer(goal_data, "pixel_row")
        _integer(goal_data, "pixel_column")
        _number(goal_data, "map_x")
        _number(goal_data, "map_y")
        clearance = _number(goal_data, "clearance_m")
        if clearance < 0:
            raise SimulationSequenceError("clearance_m must be non-negative.")
        _require_selector_evidence(goal)
        indexed[label] = goal
    return indexed


def _require_selector_evidence(goal: dict[str, Any]) -> None:
    """Require the complete evidence shape emitted by the local safe-goal selector.

    This validates artifact consistency only.  It never re-rasterizes polygons
    and cannot make a step executable.
    """
    if goal.get("polygon_validation_passed") is not True:
        raise SimulationSequenceError("safe goal polygon_validation_passed must be true.")
    if goal.get("faster_safety_passed") is not True:
        raise SimulationSequenceError("safe goal faster_safety_passed must be true.")
    evidence = goal.get("raster_safety_evidence")
    if not isinstance(evidence, dict):
        raise SimulationSequenceError("safe goal raster_safety_evidence must be an object.")
    for field in ("passed", "polygon_validation_passed", "bounds_validation_passed", "raster_evaluation_completed"):
        if evidence.get(field) is not True:
            raise SimulationSequenceError(f"raster_safety_evidence.{field} must be true.")
    counts: dict[str, int] = {}
    for field in ("rasterized_pixel_count", "free_count", "occupied_count", "unknown_count", "out_of_bounds_count"):
        value = evidence.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SimulationSequenceError(f"raster_safety_evidence.{field} must be a non-negative integer.")
        counts[field] = value
    if counts["occupied_count"] or counts["unknown_count"] or counts["out_of_bounds_count"]:
        raise SimulationSequenceError("raster_safety_evidence contains unsafe pixels.")
    if counts["rasterized_pixel_count"] != counts["free_count"] + counts["occupied_count"] + counts["unknown_count"]:
        raise SimulationSequenceError("raster_safety_evidence pixel counts are inconsistent.")
    ratio = _finite_identity_number(evidence.get("safe_free_ratio"), "raster_safety_evidence.safe_free_ratio")
    if ratio < 0 or ratio > 1 or ratio != 1.0:
        raise SimulationSequenceError("raster_safety_evidence.safe_free_ratio must be 1.0.")
    if evidence.get("failure_reasons") != []:
        raise SimulationSequenceError("raster_safety_evidence.failure_reasons must be an empty list.")


def build_simulation_sequence(
    regions_document: dict[str, Any],
    safe_goals_document: dict[str, Any],
    sequence: Iterable[str] = DEFAULT_SEQUENCE,
    *,
    fail_label: str | None = None,
    cancel_before_label: str | None = None,
    timeout_seconds: float | None = None,
    step_durations: dict[str, float] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Resolve labels and synchronously simulate deterministic state transitions."""
    requested = _validate_sequence(sequence)
    timeout, durations = _validate_controls(
        requested,
        fail_label=fail_label,
        cancel_before_label=cancel_before_label,
        timeout_seconds=timeout_seconds,
        step_durations=step_durations,
    )
    region_identity = _map_identity(regions_document, "semantic regions")
    goal_identity = _map_identity(safe_goals_document, "safe goals")
    if region_identity != goal_identity:
        raise SimulationSequenceError("map_identity mismatch between semantic regions and safe goals.")
    regions = _region_index(regions_document)
    goals = _goal_index(safe_goals_document, regions)
    plan_steps: list[dict[str, Any]] = []
    result_steps: list[dict[str, Any]] = []
    event_order = 1
    upstream_terminal: str | None = None
    overall_status = "succeeded"

    def step_base(step_order: int, label: str) -> dict[str, Any]:
        goal = goals[label]
        goal_data = goal["goal"]
        return {
            "step_order": step_order,
            "label": label,
            "proposal_id": goal["proposal_id"],
            "partition_id": goal["candidate_partition_id"],
            "source_candidate_order": goal["source_candidate_order"],
            "source_selection_rank": goal["source_selection_rank"],
            "demo_assignment_order": goal["demo_assignment_order"],
            "goal_order": goal["goal_order"],
            "goal_pixel": {"row": goal_data["pixel_row"], "column": goal_data["pixel_column"]},
            "goal_map": {"x": goal_data["map_x"], "y": goal_data["map_y"]},
            "clearance_m": goal_data["clearance_m"],
            "simulated_duration_seconds": durations.get(label, 0.0),
            "timeout_seconds": timeout,
            "review_only": True,
            "simulation_only": True,
            "executable": False,
        }

    def events(statuses: tuple[str, ...]) -> list[dict[str, Any]]:
        nonlocal event_order
        result_events = []
        for status in statuses:
            result_events.append({"logical_event_order": event_order, "status": status})
            event_order += 1
        return result_events

    for step_order, label in enumerate(requested, start=1):
        region = regions.get(label)
        goal = goals.get(label)
        if region is None or goal is None:
            raise SimulationSequenceError(f"requested label has no unique region and accepted safe goal: {label}")
        step = step_base(step_order, label)
        plan_steps.append({**step, "status": "pending", "terminal_reason": None})
        if upstream_terminal is not None:
            terminal_reason = {
                "failed": "upstream_failure",
                "timed_out": "upstream_timeout",
                "cancelled": "user_requested_cancel",
            }[upstream_terminal]
            result_steps.append({**step, "status": "cancelled", "terminal_reason": terminal_reason, "state_events": events(("pending", "cancelled"))})
            continue
        if cancel_before_label == label:
            upstream_terminal = "cancelled"
            overall_status = "cancelled"
            result_steps.append({**step, "status": "cancelled", "terminal_reason": "user_requested_cancel", "state_events": events(("pending", "cancelled"))})
            continue
        if fail_label == label:
            upstream_terminal = "failed"
            overall_status = "failed"
            result_steps.append({**step, "status": "failed", "terminal_reason": "simulated_failure", "state_events": events(("pending", "running", "failed"))})
            continue
        if timeout is not None and durations.get(label, 0.0) > timeout:
            upstream_terminal = "timed_out"
            overall_status = "timed_out"
            result_steps.append({**step, "status": "timed_out", "terminal_reason": "timeout_exceeded", "state_events": events(("pending", "running", "timed_out"))})
            continue
        result_steps.append({**step, "status": "succeeded", "terminal_reason": None, "state_events": events(("pending", "running", "succeeded"))})
    counts = {status: sum(step["status"] == status for step in result_steps) for status in ("succeeded", "failed", "timed_out", "cancelled")}
    plan = {
        "schema_version": "1.0", "simulation_only": True, "executable": False,
        "map_identity": region_identity, "requested_sequence": list(requested),
        "fail_label": fail_label, "cancel_before_label": cancel_before_label,
        "timeout_seconds": timeout, "step_durations": durations,
        "total_steps": len(plan_steps), "steps": plan_steps,
    }
    result = {
        "schema_version": "1.0", "simulation_only": True, "executable": False,
        "map_identity": region_identity, "requested_sequence": list(requested),
        "fail_label": fail_label, "cancel_before_label": cancel_before_label,
        "timeout_seconds": timeout, "step_durations": durations,
        "total_steps": len(result_steps), "succeeded_steps": counts["succeeded"], "failed_steps": counts["failed"],
        "timed_out_steps": counts["timed_out"], "cancelled_steps": counts["cancelled"],
        "overall_status": overall_status, "steps": result_steps,
    }
    return plan, result


def write_simulation_sequence_artifacts(output_dir: Path, plan: dict[str, Any], result: dict[str, Any]) -> dict[str, Path]:
    """Publish exactly two local JSON artifacts without overwriting an old run."""
    output = Path(output_dir)
    temporary: Path | None = None
    published = False
    if output.exists():
        raise SimulationSequenceError(f"simulation sequence output directory already exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        (temporary / "simulation_sequence_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
        (temporary / "simulation_sequence_result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, output)
        published = True
    finally:
        if not published and temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    return {"plan": output / "simulation_sequence_plan.json", "result": output / "simulation_sequence_result.json"}
