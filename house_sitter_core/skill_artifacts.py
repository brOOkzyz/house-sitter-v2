"""Stable artifact rendering and atomic publication for simulation skills."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .home_simulation_state import HomeSimulationState
from .simulation_sequence import SimulationSequenceError, load_sequence_inputs
from .skill_planner import (
    SkillPlanningError,
    SkillRequest,
    compile_skill_plan,
    create_skill_request,
)
from .skill_runtime import SkillRuntimeError, execute_skill_plan


class SkillArtifactError(ValueError):
    """Raised when deterministic skill artifacts cannot be loaded or published."""


ARTIFACT_NAMES = (
    "skill_request.json",
    "skill_plan.json",
    "skill_result.json",
    "skill_events.jsonl",
    "skill_report.md",
)


def _json(document: dict[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n" for event in events)


def _report(request: SkillRequest, plan: dict[str, Any], result: dict[str, Any]) -> str:
    lines = [
        "# Simulation Skill Report",
        "",
        "SIMULATION ONLY",
        "REVIEW ONLY",
        "NOT REAL ROBOT EXECUTION",
        "NO ROS / NAV2 COMMANDS SENT",
        "NO PHYSICAL MANIPULATION",
        "NO REAL SENSOR DETECTION",
        "",
        "## Request",
        "",
        f"- Skill: `{request.skill_name}`",
        f"- Request ID: `{request.request_id}`",
        f"- Planning status: `{plan['planning_status']}`",
        f"- Overall status: `{result['overall_status']}`",
        f"- Terminal reason: `{result['terminal_reason']}`",
        "",
        "## Plan and Safety Basis",
        "",
        "The plan uses only accepted safe-goal references that passed the existing local artifact schema, map-identity, provenance, and selector-evidence checks.",
        "This layer does not re-run polygon or raster safety and does not authenticate file provenance. Complete internally consistent imitations are outside this local review pipeline threat model.",
        "",
        "| step | action | region | why | final status | terminal reason |",
        "| ---: | --- | --- | --- | --- | --- |",
    ]
    result_by_order = {step["step_order"]: step for step in result["steps"]}
    for step in plan["steps"]:
        final = result_by_order.get(step["step_order"], step)
        reason = str(step["reason"]).replace("|", "\\|")
        lines.append(f"| {step['step_order']} | `{step['action_type']}` | `{step.get('label') or ''}` | {reason} | `{final['status']}` | `{final.get('terminal_reason') or ''}` |")
    if "target_plan" in plan:
        target = plan["target_plan"]
        lines.extend([
            "",
            "## Target-plan wrapper",
            "",
            f"- Wrapper skill: `{target['requested_wrapper_skill']}`",
            f"- Target skill: `{target['target_skill']}`",
            "- Target steps were compiled from the same validated local artifacts and were not executed.",
        ])
    lines.extend([
        "",
        "## Boundary",
        "",
        "Synthetic labels are not ground truth. Item handling, device switching, alarms, visitors, escorts, battery values, charging, failure, timeout, cancellation, retry, and recovery are deterministic simulation records only.",
        "No motion, navigation, docking, manipulation, IoT, sensor, ROS, Nav2, Gazebo, or RViz command is produced by this runtime.",
        "",
    ])
    return "\n".join(lines)


def render_skill_artifacts(
    request: SkillRequest,
    plan: dict[str, Any],
    result: dict[str, Any],
    events: list[dict[str, Any]],
) -> dict[str, str]:
    return {
        "skill_request.json": _json(request.as_dict()),
        "skill_plan.json": _json(plan),
        "skill_result.json": _json(result),
        "skill_events.jsonl": _jsonl(events),
        "skill_report.md": _report(request, plan, result),
    }


def load_skill_inputs(regions_path: Path, goals_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        return load_sequence_inputs(Path(regions_path), Path(goals_path))
    except SimulationSequenceError as exc:
        raise SkillArtifactError(str(exc)) from exc


def build_skill_run(
    request: SkillRequest,
    regions_document: dict[str, Any],
    goals_document: dict[str, Any],
    *,
    state: HomeSimulationState | None = None,
    preview_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    simulation_state = state or HomeSimulationState(
        current_region=request.current_region,
        battery_percent=request.simulated_battery_percent,
    )
    plan = compile_skill_plan(request, regions_document, goals_document, simulation_state)
    emergency_plan = None
    if request.injected_events.get("preempt_at_step") is not None:
        emergency_request = create_skill_request(
            "emergency_task_preemption",
            request_id=f"{request.request_id}-emergency",
            requested_by=request.requested_by,
            priority=50,
            battery_percent=simulation_state.battery_percent,
            current_region=simulation_state.current_region,
            injected_events={
                "alarm_region": request.injected_events.get("alarm_region"),
                "alarm_type": request.injected_events.get("alarm_type", "simulated_alarm"),
            },
        )
        emergency_plan = compile_skill_plan(emergency_request, regions_document, goals_document, simulation_state)
    effective_preview = preview_only or request.skill_name in {"preview_skill_plan", "explain_skill_plan"}
    result, events = execute_skill_plan(plan, request, simulation_state, preview_only=effective_preview, emergency_plan=emergency_plan)
    return plan, result, events, render_skill_artifacts(request, plan, result, events)


def build_skill_run_from_paths(
    request: SkillRequest,
    regions_path: Path,
    goals_path: Path,
    *,
    state: HomeSimulationState | None = None,
    preview_only: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], dict[str, str]]:
    regions, goals = load_skill_inputs(regions_path, goals_path)
    try:
        return build_skill_run(request, regions, goals, state=state, preview_only=preview_only)
    except (SkillPlanningError, SkillRuntimeError) as exc:
        raise SkillArtifactError(str(exc)) from exc


def _add_cleanup_note(primary_error: BaseException, cleanup_error: BaseException) -> None:
    """Attach cleanup context without ever replacing the primary exception."""
    try:
        primary_error.add_note(
            f"temporary cleanup failed: {type(cleanup_error).__name__}: {cleanup_error}"
        )
    except BaseException:
        pass


def write_skill_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    if set(contents) != set(ARTIFACT_NAMES):
        raise SkillArtifactError("skill artifact output set is incomplete or contains extra files.")
    output = Path(output_dir)
    if output.exists():
        raise SkillArtifactError(f"skill output directory already exists: {output}")
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    primary_error: BaseException | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_directory = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        temporary = Path(temporary_directory.name)
        for name in ARTIFACT_NAMES:
            (temporary / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary, output)
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        if temporary_directory is not None:
            # The standard-library cleanup is deliberately best-effort: do not
            # traverse or force-remove a path that may have changed concurrently.
            try:
                temporary_directory.cleanup()
            except BaseException as cleanup_error:
                if primary_error is not None:
                    _add_cleanup_note(primary_error, cleanup_error)
                else:
                    raise cleanup_error
    return {name: output / name for name in ARTIFACT_NAMES}
