"""Thin, simulation-only orchestration for natural language skill requests."""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .home_simulation_state import HomeSimulationState
from .natural_language_adapter import NaturalLanguageAdapterError, parse_skill_request
from .nav2_sim_bridge import NavigationExecutor
from .skill_execution_bridge import NAVIGATION_ACTIONS, SkillExecutionBridgeError, execute_skill_in_simulation
from .skill_planner import SkillPlanningError, compile_skill_plan, create_skill_request


PIPELINE_ARTIFACT_NAMES = (
    "natural_language_request.json", "natural_language_parse.json", "skill_plan.json",
    "pipeline_result.json", "pipeline_report.md",
)


class NaturalLanguagePipelineError(ValueError):
    """Raised when safe local pipeline construction or publication cannot continue."""


def _flags() -> dict[str, Any]:
    return {"simulation_only": True, "review_only": True, "real_robot_supported": False, "executable": False}


def run_natural_language_pipeline(
    text: str,
    regions_document: dict[str, Any],
    goals_document: dict[str, Any],
    *,
    executor: NavigationExecutor | None = None,
    execute_simulation: bool = False,
    timeout_seconds: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Parse, plan, and optionally execute only through the existing simulation bridge."""
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise NaturalLanguagePipelineError("timeout_seconds must be a finite positive number.")
    if execute_simulation and executor is None:
        raise NaturalLanguagePipelineError("explicit simulation execution requires a Nav2 simulation executor.")
    parsed = parse_skill_request(text)
    request_document = {"original_text": text, **_flags()}
    if parsed["status"] != "accepted":
        plan = {"planning_status": "not_started", "reason": parsed["status"], **_flags()}
        result = {
            "original_text": text, "selected_capability": parsed["selected_capability"], "parse_status": parsed["status"],
            "planner_status": "not_started", "execution_mode": "not_started", "action_goals_sent": 0,
            "final_status": parsed["status"], "explanation": parsed["explanation"], **_flags(),
        }
        return request_document, parsed, plan, result
    capability, parameters = parsed["selected_capability"], parsed["parameters"]
    if not isinstance(capability, str) or not isinstance(parameters, dict):
        raise NaturalLanguagePipelineError("accepted natural-language parse is malformed.")
    try:
        request = create_skill_request(capability, parameters)
        state = HomeSimulationState(current_region=request.current_region, battery_percent=request.simulated_battery_percent)
        plan = compile_skill_plan(request, regions_document, goals_document, state)
    except (SkillPlanningError, ValueError) as exc:
        raise NaturalLanguagePipelineError(f"planner rejected natural-language request: {exc}") from exc
    try:
        execution, events = execute_skill_in_simulation(
            plan, request, executor if execute_simulation else None,
            timeout_seconds=timeout_seconds, dry_run=not execute_simulation, state=state,
        )
    except SkillExecutionBridgeError as exc:
        raise NaturalLanguagePipelineError(f"simulation execution bridge rejected request: {exc}") from exc
    action_goals_sent = sum(
        event.get("status") == "running" and event.get("action_type") in NAVIGATION_ACTIONS
        for event in events
    )
    execution_mode = "gazebo_nav2_simulation" if execute_simulation else "dry_run"
    result = {
        "original_text": text, "selected_capability": capability, "parse_status": "accepted",
        "planner_status": plan["planning_status"], "execution_mode": execution_mode,
        "action_goals_sent": action_goals_sent,
        "final_status": execution["overall_status"] if execute_simulation else "not_executed",
        "execution_summary": {
            "overall_status": execution["overall_status"], "terminal_reason": execution["terminal_reason"],
            "total_steps": execution["total_steps"], "timeout_policy": execution["timeout_policy"],
            "effective_timeout_seconds": execution["effective_timeout_seconds"], "timeout_basis": execution["timeout_basis"],
        },
        **_flags(),
    }
    return {**request.as_dict(), **_flags()}, parsed, plan, result


def render_pipeline_artifacts(
    request_document: dict[str, Any], parsed: dict[str, Any], plan: dict[str, Any], result: dict[str, Any],
) -> dict[str, str]:
    """Render stable pipeline records without adding new navigation data."""
    report = "\n".join((
        "# Natural-language Simulation Skill Pipeline", "", "SIMULATION ONLY", "",
        "This project is simulation-only and does not support real-robot deployment.",
        "Natural-language parsing is offline and deterministic. Navigation can occur only through the existing optional Gazebo/Nav2 bridge.", "",
        "## Boundary", "", "- simulation_only: true", "- real_robot_supported: false", "",
        "## Result", "", f"- Parse status: `{result['parse_status']}`",
        f"- Planner status: `{result['planner_status']}`", f"- Execution mode: `{result['execution_mode']}`",
        f"- Action goals sent: {result['action_goals_sent']}", f"- Final status: `{result['final_status']}`", "",
    ))
    return {
        "natural_language_request.json": json.dumps(request_document, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        "natural_language_parse.json": json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        "skill_plan.json": json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        "pipeline_result.json": json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        "pipeline_report.md": report,
    }


def write_pipeline_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    """Atomically publish all pipeline records; existing output directories fail closed."""
    if set(contents) != set(PIPELINE_ARTIFACT_NAMES):
        raise NaturalLanguagePipelineError("pipeline artifact output set is incomplete or contains extra files.")
    output = Path(output_dir)
    if output.exists():
        raise NaturalLanguagePipelineError(f"pipeline output directory already exists: {output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    primary: BaseException | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        for name in PIPELINE_ARTIFACT_NAMES:
            (Path(temporary.name) / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary.name, output)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except BaseException as cleanup:
                if primary is not None:
                    primary.add_note(f"temporary cleanup failed: {type(cleanup).__name__}: {cleanup}")
                else:
                    raise
    return {name: output / name for name in PIPELINE_ARTIFACT_NAMES}
