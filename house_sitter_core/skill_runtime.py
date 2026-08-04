"""Deterministic synchronous runtime for review-only skill plans."""

from __future__ import annotations

import copy
import math
from typing import Any

from .home_simulation_state import (
    ACTION_ENERGY_COST_PERCENT,
    FULL_BATTERY_PERCENT,
    MAX_RETRY_ATTEMPTS,
    NAVIGATION_ENERGY_COST_PERCENT,
    HomeSimulationStateError,
    HomeSimulationState,
    validate_checkpoint_id,
)
from .skill_planner import SkillRequest


class SkillRuntimeError(ValueError):
    """Raised when a compiled simulation plan or injected control is invalid."""


STEP_STATUSES = ("pending", "running", "succeeded", "failed", "timed_out", "cancelled", "skipped")
OVERALL_STATUSES = ("succeeded", "failed", "timed_out", "cancelled")


def _event_integer(events: dict[str, Any], key: str) -> int | None:
    value = events.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SkillRuntimeError(f"injected event {key} must be a positive integer.")
    return value


def _event_number(events: dict[str, Any], key: str, *, positive: bool = False) -> float | None:
    value = events.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SkillRuntimeError(f"injected event {key} must be a finite number.")
    number = float(value)
    if (positive and number <= 0) or (not positive and number < 0):
        comparator = "positive" if positive else "non-negative"
        raise SkillRuntimeError(f"injected event {key} must be {comparator}.")
    return number


def _strict_bool_event(events: dict[str, Any], key: str) -> bool:
    value = events.get(key, False)
    if value is not False and value is not True:
        raise SkillRuntimeError(f"injected event {key} must be a boolean.")
    return value


def _validate_plan(plan: dict[str, Any], request: SkillRequest) -> None:
    if not isinstance(plan, dict) or plan.get("request_id") != request.request_id or plan.get("skill_name") != request.skill_name:
        raise SkillRuntimeError("plan and request identity mismatch.")
    if plan.get("demo_only") is not True or plan.get("synthetic_semantics") is not True or plan.get("ground_truth") is not False:
        raise SkillRuntimeError("plan must remain an explicit synthetic demo plan.")
    if plan.get("simulation_only") is not True or plan.get("review_only") is not True or plan.get("executable") is not False:
        raise SkillRuntimeError("plan must remain simulation-only, review-only, and non-executable.")
    steps = plan.get("steps")
    if not isinstance(steps, list) or plan.get("total_steps") != len(steps):
        raise SkillRuntimeError("plan steps are malformed.")
    for order, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("step_order") != order or step.get("status") != "pending":
            raise SkillRuntimeError("plan step order and pending state must be continuous.")
        if step.get("demo_only") is not True or step.get("synthetic_semantics") is not True or step.get("ground_truth") is not False:
            raise SkillRuntimeError("plan step has invalid synthetic-demo flags.")
        if step.get("simulation_only") is not True or step.get("review_only") is not True or step.get("executable") is not False:
            raise SkillRuntimeError("plan step has invalid simulation-only flags.")


def _apply_success_state(step: dict[str, Any], state: HomeSimulationState, request: SkillRequest) -> None:
    action_type = step["action_type"]
    label = step.get("label")
    if action_type in {"navigate_to_region", "navigate_to_safe_goal", "return_to_charger"}:
        if label is not None:
            state.current_region = label
        state.battery_percent = max(0.0, state.battery_percent - NAVIGATION_ENERGY_COST_PERCENT)
    else:
        state.battery_percent = max(0.0, state.battery_percent - ACTION_ENERGY_COST_PERCENT)
    details = step.get("details", {})
    if action_type == "charge_simulated":
        state.battery_percent = FULL_BATTERY_PERCENT
        state.charging = True
    elif action_type == "checkpoint":
        try:
            checkpoint_id = validate_checkpoint_id(details.get("checkpoint_id", f"checkpoint-{step['step_order']:03d}"))
            state.create_checkpoint(checkpoint_id, step["step_order"] + 1)
        except HomeSimulationStateError as exc:
            raise SkillRuntimeError(str(exc)) from exc
    elif action_type == "restore_checkpoint":
        try:
            checkpoint_id = validate_checkpoint_id(details.get("checkpoint_id"))
        except HomeSimulationStateError as exc:
            raise SkillRuntimeError(str(exc)) from exc
        checkpoint = state.checkpoints.get(checkpoint_id)
        if checkpoint is None:
            raise SkillRuntimeError(f"checkpoint not found: {checkpoint_id}")
        state.current_region = checkpoint["current_region"]
        if details.get("preserve_battery") is not True:
            state.battery_percent = checkpoint["battery_percent"]
        state.charging = checkpoint["charging"]
    elif action_type == "switch_device_simulated":
        state.device_states[details.get("device", "device")] = "simulated_toggled"
    elif action_type == "pick_item_simulated":
        state.simulated_items[str(details.get("item"))] = "simulated_carried"
    elif action_type in {"place_item_simulated", "handover_item_simulated"}:
        state.simulated_items[str(details.get("item"))] = label or "simulated_destination"
    elif request.skill_name == "queue_task" and action_type == "report_status":
        queued = state.enqueue(
            request.request_id,
            details["queued_skill"],
            details["priority"],
            expected_task_id=details["task_id"],
        )
        details["task_id"] = queued["task_id"]
    elif request.skill_name == "change_task_priority" and action_type == "report_status":
        task_id = details["task_id"]
        matching = [task for task in state.queued_tasks if task.get("task_id") == task_id]
        if len(matching) != 1:
            raise SkillRuntimeError("TASK_NOT_FOUND: no unique queued task matches task_id.")
        task = matching[0]
        before = [dict(item) for item in state.ordered_queue()]
        task["priority"] = details["new_priority"]
        after = [dict(item) for item in state.ordered_queue()]
        details["queue_order_before"] = before
        details["queue_order_after"] = after
    state.completed_actions.append(f"{request.request_id}:{step['step_order']}:{action_type}")


def _preview_result(plan: dict[str, Any], state: HomeSimulationState) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    return ({
        "schema_version": "1.0",
        "request_id": plan["request_id"],
        "skill_name": plan["skill_name"],
        "execution_mode": "preview_only",
        "overall_status": None,
        "terminal_reason": None,
        "total_steps": plan["total_steps"],
        "succeeded_steps": 0,
        "failed_steps": 0,
        "timed_out_steps": 0,
        "cancelled_steps": 0,
        "skipped_steps": 0,
        "steps": copy.deepcopy(plan["steps"]),
        "state": state.snapshot(),
        "demo_only": True,
        "synthetic_semantics": True,
        "ground_truth": False,
        "simulation_only": True,
        "review_only": True,
        "executable": False,
    }, [])


def execute_skill_plan(
    plan: dict[str, Any],
    request: SkillRequest,
    state: HomeSimulationState | None = None,
    *,
    preview_only: bool = False,
    emergency_plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run logical transitions synchronously; no clock, process, or robot API exists."""
    _validate_plan(plan, request)
    simulation_state = state or HomeSimulationState(current_region=request.current_region, battery_percent=request.simulated_battery_percent)
    if preview_only:
        return _preview_result(plan, simulation_state)
    simulation_state.active_task = request.request_id

    controls = request.injected_events
    fail_step = _event_integer(controls, "fail_step_order")
    timeout_step = _event_integer(controls, "timeout_step_order")
    cancel_before = _event_integer(controls, "cancel_before_step")
    preempt_at = _event_integer(controls, "preempt_at_step")
    pause_after = _event_integer(controls, "pause_after_step")
    low_battery_at = _event_integer(controls, "low_battery_at_step")
    timeout_seconds = _event_number(controls, "timeout_seconds", positive=True)
    simulated_duration = _event_number(controls, "simulated_duration_seconds")
    retry_exhausted = _strict_bool_event(controls, "retry_exhausted")
    if preempt_at is not None and emergency_plan is None:
        raise SkillRuntimeError("preempt_at_step requires a compiled emergency plan.")
    for name, value in (("fail_step_order", fail_step), ("timeout_step_order", timeout_step), ("cancel_before_step", cancel_before), ("preempt_at_step", preempt_at), ("pause_after_step", pause_after), ("low_battery_at_step", low_battery_at)):
        if value is not None and value > plan["total_steps"]:
            raise SkillRuntimeError(f"injected event {name} exceeds the plan step count.")
    if timeout_step is not None and (timeout_seconds is None or simulated_duration is None):
        raise SkillRuntimeError("timeout injection requires timeout_seconds and simulated_duration_seconds.")

    events: list[dict[str, Any]] = []
    next_event_order = 1

    def emit(step: dict[str, Any], status: str, attempt: int, reason: str | None = None, *, scope: str = "primary") -> None:
        nonlocal next_event_order
        events.append({
            "logical_event_order": next_event_order,
            "task_scope": scope,
            "request_id": request.request_id,
            "step_order": step["step_order"],
            "action_type": step["action_type"],
            "status": status,
            "attempt": attempt,
            "reason": reason,
            "demo_only": True,
            "synthetic_semantics": True,
            "ground_truth": False,
            "synthetic": True,
            "simulation_only": True,
            "review_only": True,
            "executable": False,
        })
        next_event_order += 1

    result_steps: list[dict[str, Any]] = []
    upstream_status: str | None = None
    upstream_reason: str | None = None
    overall_status = "succeeded"
    terminal_reason: str | None = None
    checkpoint: dict[str, Any] | None = None
    recovery_steps: list[dict[str, Any]] = []
    low_battery_initial = not plan["policy"]["battery_precheck_passed"]

    for source_step in plan["steps"]:
        step = copy.deepcopy(source_step)
        order = step["step_order"]
        emit(step, "pending", 1)
        if upstream_status is not None:
            step.update(status="cancelled", terminal_reason=upstream_reason, interruption_reason=upstream_reason)
            emit(step, "cancelled", 1, upstream_reason)
            result_steps.append(step)
            continue
        automatic_low_battery = not plan["policy"]["low_battery_exempt"] and simulation_state.battery_percent < plan["policy"]["low_battery_threshold_percent"]
        if low_battery_initial or automatic_low_battery or low_battery_at == order:
            upstream_status, upstream_reason = "cancelled", "low_battery_abort"
            overall_status, terminal_reason = "cancelled", "low_battery_abort"
            step.update(status="cancelled", terminal_reason=upstream_reason, interruption_reason=upstream_reason, recovery_action="return_to_charger")
            emit(step, "cancelled", 1, upstream_reason)
            result_steps.append(step)
            continue
        if preempt_at == order:
            upstream_status, upstream_reason = "cancelled", "emergency_preemption"
            overall_status, terminal_reason = "cancelled", "emergency_preemption"
            step.update(status="cancelled", terminal_reason=upstream_reason, interruption_reason=upstream_reason, recovery_action="emergency_task_preemption")
            emit(step, "cancelled", 1, upstream_reason)
            result_steps.append(step)
            continue
        if cancel_before == order:
            upstream_status, upstream_reason = "cancelled", "user_requested_cancel"
            overall_status, terminal_reason = "cancelled", "user_requested_cancel"
            step.update(status="cancelled", terminal_reason=upstream_reason, interruption_reason=upstream_reason)
            emit(step, "cancelled", 1, upstream_reason)
            result_steps.append(step)
            continue

        emit(step, "running", 1)
        is_failure = fail_step == order
        if request.skill_name in {"retry_failed_step", "skip_failed_step"} and order == 1:
            is_failure = True
        if timeout_step == order and simulated_duration is not None and timeout_seconds is not None and simulated_duration > timeout_seconds:
            upstream_status, upstream_reason = "timed_out", "upstream_timeout"
            overall_status, terminal_reason = "timed_out", "timeout_exceeded"
            step.update(status="timed_out", terminal_reason="timeout_exceeded")
            step["simulated_duration_seconds"] = simulated_duration
            step["timeout_seconds"] = timeout_seconds
            emit(step, "timed_out", 1, "timeout_exceeded")
            result_steps.append(step)
            continue
        if is_failure:
            emit(step, "failed", 1, "simulated_failure")
            retry_requested = request.skill_name == "retry_failed_step" or controls.get("recovery_action") == "retry"
            skip_requested = request.skill_name == "skip_failed_step" or controls.get("recovery_action") == "skip"
            if retry_requested and plan["policy"]["maximum_retry_attempts"] > 0:
                step["recovery_action"] = "retry_step"
                emit(step, "running", 2, "bounded_retry")
                if not retry_exhausted:
                    _apply_success_state(step, simulation_state, request)
                    step.update(status="succeeded", terminal_reason="recovered_after_retry", attempt=2)
                    emit(step, "succeeded", 2, "recovered_after_retry")
                    result_steps.append(step)
                    if pause_after == order:
                        checkpoint = simulation_state.create_checkpoint(f"{request.request_id}-pause", order + 1)
                        upstream_status, upstream_reason = "cancelled", "paused_at_checkpoint"
                        overall_status, terminal_reason = "cancelled", "paused_at_checkpoint"
                    continue
                step.update(status="failed", terminal_reason="retry_limit_exceeded", attempt=2)
                emit(step, "failed", 2, "retry_limit_exceeded")
                upstream_status, upstream_reason = "failed", "upstream_failure"
                overall_status, terminal_reason = "failed", "retry_limit_exceeded"
                result_steps.append(step)
                continue
            if skip_requested and step.get("non_critical") is True:
                step.update(status="skipped", terminal_reason="non_critical_step_skipped", recovery_action="skip_step")
                emit(step, "skipped", 1, "non_critical_step_skipped")
                result_steps.append(step)
                continue
            step.update(status="failed", terminal_reason="critical_step_failure" if step.get("critical") is True and skip_requested else "simulated_failure")
            upstream_status, upstream_reason = "failed", "upstream_failure"
            overall_status, terminal_reason = "failed", step["terminal_reason"]
            result_steps.append(step)
            continue
        try:
            _apply_success_state(step, simulation_state, request)
        except SkillRuntimeError as exc:
            step.update(status="failed", terminal_reason="runtime_policy_failure")
            emit(step, "failed", 1, str(exc))
            result_steps.append(step)
            upstream_status, upstream_reason = "failed", "upstream_failure"
            overall_status, terminal_reason = "failed", "runtime_policy_failure"
            continue
        step.update(status="succeeded", terminal_reason=None)
        emit(step, "succeeded", 1)
        result_steps.append(step)
        if pause_after == order:
            checkpoint = simulation_state.create_checkpoint(f"{request.request_id}-pause", order + 1)
            upstream_status, upstream_reason = "cancelled", "paused_at_checkpoint"
            overall_status, terminal_reason = "cancelled", "paused_at_checkpoint"

    if (low_battery_initial or low_battery_at is not None) and terminal_reason == "low_battery_abort":
        recovery = copy.deepcopy(plan["policy"]["return_to_charger_action"])
        recovery["step_order"] = 1
        emit(recovery, "pending", 1, scope="recovery")
        emit(recovery, "running", 1, scope="recovery")
        _apply_success_state(recovery, simulation_state, request)
        recovery.update(status="succeeded", terminal_reason=None, recovery_action="low_battery_return")
        emit(recovery, "succeeded", 1, scope="recovery")
        recovery_steps.append(recovery)

    emergency_result: dict[str, Any] | None = None
    if preempt_at is not None and emergency_plan is not None and terminal_reason == "emergency_preemption":
        emergency_request = SkillRequest(
            request_id=emergency_plan["request_id"],
            skill_name=emergency_plan["skill_name"],
            parameters={},
            requested_by=request.requested_by,
            priority=50,
            simulated_battery_percent=simulation_state.battery_percent,
            current_region=simulation_state.current_region,
            injected_events={key: value for key, value in request.injected_events.items() if key not in {"preempt_at_step", "fail_step_order", "timeout_step_order", "cancel_before_step", "pause_after_step", "low_battery_at_step"}},
            policy_overrides={},
        )
        emergency_result, emergency_events = execute_skill_plan(emergency_plan, emergency_request, simulation_state)
        for event in emergency_events:
            copied = dict(event)
            copied["logical_event_order"] = next_event_order
            copied["task_scope"] = "emergency"
            events.append(copied)
            next_event_order += 1

    if plan["planning_status"] == "confirmation_required":
        overall_status, terminal_reason = "cancelled", "confirmation_required"
    elif plan["planning_status"] == "rejected":
        overall_status, terminal_reason = "failed", plan["reason_code"]
    elif request.skill_name == "cancel_current_task":
        overall_status, terminal_reason = "cancelled", "user_requested_cancel"
    elif request.skill_name == "pause_current_task":
        overall_status, terminal_reason = "cancelled", "paused_at_checkpoint"
    counts = {status: sum(step["status"] == status for step in result_steps) for status in ("succeeded", "failed", "timed_out", "cancelled", "skipped")}
    simulation_state.active_task = None
    result = {
        "schema_version": "1.0",
        "request_id": request.request_id,
        "skill_name": request.skill_name,
        "execution_mode": "simulation_only",
        "planning_status": plan["planning_status"],
        "overall_status": overall_status,
        "terminal_reason": terminal_reason,
        "total_steps": len(result_steps),
        "succeeded_steps": counts["succeeded"],
        "failed_steps": counts["failed"],
        "timed_out_steps": counts["timed_out"],
        "cancelled_steps": counts["cancelled"],
        "skipped_steps": counts["skipped"],
        "steps": result_steps,
        "recovery_steps": recovery_steps,
        "checkpoint": checkpoint,
        "emergency_task": emergency_result,
        "state": simulation_state.snapshot(),
        "demo_only": True,
        "synthetic_semantics": True,
        "ground_truth": False,
        "simulation_only": True,
        "review_only": True,
        "executable": False,
    }
    return result, events


def resume_skill_plan(
    plan: dict[str, Any],
    request: SkillRequest,
    checkpoint: dict[str, Any],
    state: HomeSimulationState,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute only the deterministic suffix identified by a prior checkpoint."""
    if not isinstance(checkpoint, dict) or isinstance(checkpoint.get("next_step_order"), bool) or not isinstance(checkpoint.get("next_step_order"), int):
        raise SkillRuntimeError("resume checkpoint must contain an integer next_step_order.")
    start = checkpoint["next_step_order"]
    if start < 1 or start > plan["total_steps"] + 1:
        raise SkillRuntimeError("resume checkpoint next_step_order is outside the plan.")
    resumed = copy.deepcopy(plan)
    resumed["steps"] = [dict(step, original_step_order=step["step_order"], step_order=index) for index, step in enumerate(plan["steps"][start - 1 :], start=1)]
    resumed["total_steps"] = len(resumed["steps"])
    clean_request = SkillRequest(
        request_id=request.request_id,
        skill_name=request.skill_name,
        parameters=request.parameters,
        requested_by=request.requested_by,
        priority=request.priority,
        simulated_battery_percent=state.battery_percent,
        current_region=state.current_region,
        injected_events={},
        policy_overrides=request.policy_overrides,
    )
    result, events = execute_skill_plan(resumed, clean_request, state)
    result["resumed_from_checkpoint"] = dict(checkpoint)
    return result, events
