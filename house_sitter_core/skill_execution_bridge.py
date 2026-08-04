"""Simulation-only bridge from compiled skill plans to optional Nav2 actions."""

from __future__ import annotations

import copy
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .home_simulation_state import HomeSimulationState
from .nav2_sim_bridge import FALLBACK_TIMEOUT_SECONDS, NavigationError, NavigationExecutor, NavigationGoal, adaptive_timeout_from_feedback
from .skill_planner import SkillRequest
from .skill_runtime import _apply_success_state


EXECUTION_ARTIFACT_NAMES = (
    "execution_request.json", "execution_plan.json", "execution_events.jsonl",
    "execution_result.json", "execution_report.md",
)
NAVIGATION_ACTIONS = frozenset({"navigate_to_region", "navigate_to_safe_goal", "return_to_charger", "select_nearest_safe_goal"})


class SkillExecutionBridgeError(ValueError):
    """Raised for an invalid simulation-only execution request or plan."""


def _goal_from_step(step: dict[str, Any]) -> NavigationGoal:
    reference = step.get("goal_reference")
    if not isinstance(reference, dict) or step.get("label") != reference.get("canonical_label"):
        raise SkillExecutionBridgeError("navigation step must retain its accepted safe-goal reference.")
    point = reference.get("goal_map")
    if (not isinstance(point, dict) or isinstance(point.get("x"), bool) or isinstance(point.get("y"), bool)
            or not isinstance(point.get("x"), (int, float)) or not isinstance(point.get("y"), (int, float))):
        raise SkillExecutionBridgeError("accepted safe-goal map coordinates are malformed.")
    required = ("proposal_id", "partition_id", "goal_order", "goal_pixel")
    if any(key not in reference for key in required):
        raise SkillExecutionBridgeError("navigation step has incomplete accepted safe-goal evidence.")
    return NavigationGoal(str(step["label"]), float(point["x"]), float(point["y"]), copy.deepcopy(reference))


def _flags() -> dict[str, Any]:
    return {"simulation_only": True, "review_only": True, "real_robot_supported": False, "executable": True}


def execute_skill_in_simulation(
    plan: dict[str, Any], request: SkillRequest, executor: NavigationExecutor | None,
    *, timeout_seconds: float | None = None, dry_run: bool = False,
    state: HomeSimulationState | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute only navigation through the injected Nav2 interface; never use hardware APIs."""
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise SkillExecutionBridgeError("timeout_seconds must be a finite positive number.")
    if plan.get("request_id") != request.request_id or plan.get("skill_name") != request.skill_name:
        raise SkillExecutionBridgeError("plan and request identity mismatch.")
    if plan.get("simulation_only") is not True or plan.get("review_only") is not True or plan.get("executable") is not False:
        raise SkillExecutionBridgeError("bridge accepts only the existing non-executable simulation plan.")
    if plan.get("planning_status") != "ready":
        raise SkillExecutionBridgeError("only a ready planner result may enter the simulation bridge.")
    if not dry_run and executor is None:
        raise SkillExecutionBridgeError("an executor is required for explicit simulation execution.")
    simulation_state = state or HomeSimulationState(request.current_region, request.simulated_battery_percent)
    steps, events = [], []
    next_event = 1
    upstream: str | None = None
    terminal_reason: str | None = None
    overall_status = "succeeded"
    checkpoint: dict[str, Any] | None = None
    controls = request.injected_events
    timeout_policy = "explicit" if timeout_seconds is not None else "adaptive"
    effective_timeout_seconds = timeout_seconds if timeout_seconds is not None else FALLBACK_TIMEOUT_SECONDS
    timeout_basis = "user" if timeout_seconds is not None else "fallback"

    def emit(step: dict[str, Any], status: str, reason: str | None = None, feedback: dict[str, Any] | None = None) -> None:
        nonlocal next_event
        event = {"logical_event_order": next_event, "step_order": step["step_order"], "action_type": step["action_type"], "status": status, "reason": reason, **_flags()}
        if feedback is not None:
            event["feedback"] = feedback
        events.append(event); next_event += 1

    for original in plan.get("steps", []):
        step = copy.deepcopy(original)
        order = step["step_order"]
        if dry_run:
            steps.append(step)
            continue
        emit(step, "pending")
        if upstream is not None:
            step.update(status="cancelled", terminal_reason=upstream); emit(step, "cancelled", upstream); steps.append(step); continue
        if controls.get("preempt_at_step") == order:
            upstream = terminal_reason = "emergency_preemption"; overall_status = "cancelled"
            step.update(status="cancelled", terminal_reason=upstream); emit(step, "cancelled", upstream); steps.append(step); continue
        if controls.get("cancel_before_step") == order:
            upstream = terminal_reason = "user_requested_cancel"; overall_status = "cancelled"
            step.update(status="cancelled", terminal_reason=upstream); emit(step, "cancelled", upstream); steps.append(step); continue
        emit(step, "running")
        if step["action_type"] in NAVIGATION_ACTIONS:
            goal = _goal_from_step(step)
            try:
                handle = executor.send_goal(goal)
                outcome = executor.wait_for_result(handle, timeout_seconds)
            except NavigationError as exc:
                outcome_status, outcome_reason, feedback = "failed", str(exc), ()
            else:
                outcome_status, outcome_reason, feedback = outcome.status, outcome.reason, outcome.feedback
                if timeout_seconds is None:
                    effective_timeout_seconds, timeout_basis = (
                        (outcome.effective_timeout_seconds, outcome.timeout_basis)
                        if outcome.effective_timeout_seconds is not None and outcome.timeout_basis is not None
                        else adaptive_timeout_from_feedback(feedback, effective_timeout_seconds)
                    )
            for item in feedback:
                emit(step, "feedback", feedback=item)
            if outcome_status != "succeeded":
                reason = "timeout_exceeded" if outcome_status == "timed_out" else outcome_reason or f"navigation_{outcome_status}"
                step.update(status=outcome_status, terminal_reason=reason); emit(step, outcome_status, reason); steps.append(step)
                upstream = "upstream_timeout" if outcome_status == "timed_out" else "upstream_failure"
                terminal_reason, overall_status = reason, outcome_status
                continue
        _apply_success_state(step, simulation_state, request)
        step.update(status="succeeded", terminal_reason=None); emit(step, "succeeded"); steps.append(step)
        if controls.get("pause_after_step") == order:
            checkpoint = simulation_state.create_checkpoint(f"{request.request_id}-pause", order + 1)
            upstream = terminal_reason = "paused_at_checkpoint"; overall_status = "cancelled"

    if dry_run:
        overall_status = terminal_reason = None
    counts = {name: sum(step.get("status") == name for step in steps) for name in ("succeeded", "failed", "timed_out", "cancelled")}
    result = {"schema_version": "1.0", "request_id": request.request_id, "skill_name": request.skill_name,
              "execution_mode": "dry_run" if dry_run else "gazebo_nav2_simulation", "overall_status": overall_status,
              "terminal_reason": terminal_reason, "steps": steps, "total_steps": len(steps), **{f"{key}_steps": value for key, value in counts.items()},
              "checkpoint": checkpoint, "state": simulation_state.snapshot(), "timeout_policy": timeout_policy,
              "effective_timeout_seconds": effective_timeout_seconds, "timeout_basis": timeout_basis, **_flags()}
    return result, events


def render_execution_artifacts(request: SkillRequest, plan: dict[str, Any], result: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, str]:
    execution_plan = {"skill_plan": plan, "navigation_interface": "NavigateToPose", "frame_id": "map", **_flags()}
    report = "# Gazebo Nav2 Simulation Execution\n\nSIMULATION ONLY\n\nThis project is simulation-only and does not support real-robot deployment.\n\n## Timeout\n\n" + f"- Policy: `{result['timeout_policy']}`\n- Effective timeout: `{result['effective_timeout_seconds']}` seconds\n- Basis: `{result['timeout_basis']}`\n"
    return {
        "execution_request.json": json.dumps({**request.as_dict(), **_flags()}, indent=2, sort_keys=True) + "\n",
        "execution_plan.json": json.dumps(execution_plan, indent=2, sort_keys=True) + "\n",
        "execution_events.jsonl": "".join(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n" for event in events),
        "execution_result.json": json.dumps(result, indent=2, sort_keys=True) + "\n",
        "execution_report.md": report,
    }


def write_execution_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    if set(contents) != set(EXECUTION_ARTIFACT_NAMES):
        raise SkillExecutionBridgeError("execution artifact output set is incomplete or contains extra files.")
    output = Path(output_dir)
    if output.exists():
        raise SkillExecutionBridgeError(f"execution output directory already exists: {output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    primary: BaseException | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        for name, content in contents.items():
            (Path(temporary.name) / name).write_text(content, encoding="utf-8", newline="")
        os.replace(temporary.name, output)
    except BaseException as exc:
        primary = exc; raise
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except BaseException as cleanup:
                if primary is not None:
                    primary.add_note(f"temporary cleanup failed: {type(cleanup).__name__}: {cleanup}")
                else:
                    raise
    return {name: output / name for name in EXECUTION_ARTIFACT_NAMES}
