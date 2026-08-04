"""Compile declaration-driven simulation skills against validated demo goals."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from .home_simulation_state import (
    ACTION_ENERGY_COST_PERCENT,
    EMERGENCY_QUEUE_SKILLS,
    EMERGENCY_PRIORITY,
    LOW_BATTERY_THRESHOLD_PERCENT,
    NAVIGATION_ENERGY_COST_PERCENT,
    NORMAL_PRIORITY,
    HomeSimulationStateError,
    HomeSimulationState,
    validate_checkpoint_id,
)
from .simulation_sequence import DEFAULT_SEQUENCE, SimulationSequenceError, build_simulation_sequence
from .skill_catalog import SkillCatalogError, SkillDefinition, catalog_document, get_skill_definition, validate_skill_parameters


class SkillPlanningError(ValueError):
    """Raised when a request cannot be safely compiled from local artifacts."""


class SkillPolicyError(SkillPlanningError):
    """Raised for a deterministic fail-closed policy rejection."""


REGION_ALIASES: dict[str, tuple[str, ...]] = {
    "living_room": ("living_room",),
    "living room": ("living_room",),
    "客厅": ("living_room",),
    "kitchen": ("kitchen",),
    "厨房": ("kitchen",),
    "bedroom": ("bedroom",),
    "卧室": ("bedroom",),
    "charging_area": ("charging_area",),
    "charging area": ("charging_area",),
    "充电区": ("charging_area",),
    "room": ("living_room", "bedroom"),
    "房间": ("living_room", "bedroom"),
}

ROUTINE_ROUTES: dict[str, tuple[str, ...]] = {
    "patrol_home": DEFAULT_SEQUENCE,
    "check_all_rooms": DEFAULT_SEQUENCE,
    "bedtime_routine": ("living_room", "kitchen", "bedroom", "charging_area"),
    "leave_home_routine": ("kitchen", "living_room", "bedroom", "charging_area"),
    "morning_routine": ("charging_area", "bedroom", "kitchen", "living_room"),
    "security_check_routine": DEFAULT_SEQUENCE,
}

INJECTED_EVENT_FIELDS = {
    "alarm_region",
    "alarm_type",
    "fail_step_order",
    "timeout_step_order",
    "cancel_before_step",
    "preempt_at_step",
    "pause_after_step",
    "low_battery_at_step",
    "timeout_seconds",
    "simulated_duration_seconds",
    "retry_exhausted",
    "recovery_action",
}
POLICY_OVERRIDE_FIELDS = {"maximum_retry_attempts"}
PLAN_WRAPPER_SKILLS = frozenset({"preview_skill_plan", "explain_skill_plan"})


def _strict_float(value: Any, name: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise SkillPlanningError(f"{name} must be a finite number.")
    number = float(value)
    if minimum is not None and number < minimum:
        raise SkillPlanningError(f"{name} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise SkillPlanningError(f"{name} must be at most {maximum}.")
    return number


def _strict_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < EMERGENCY_PRIORITY:
        raise SkillPlanningError("priority must be an integer from 0 through 99.")
    return value


def _validated_injected_events(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillPlanningError("injected_events must be an object.")
    unknown = sorted(set(value) - INJECTED_EVENT_FIELDS)
    if unknown:
        raise SkillPlanningError(f"unknown injected event field(s): {', '.join(unknown)}")
    if "recovery_action" in value and value["recovery_action"] not in {"retry", "skip"}:
        raise SkillPlanningError("injected_events.recovery_action must be retry or skip.")
    return dict(value)


def _validated_policy_overrides(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillPlanningError("policy_overrides must be an object.")
    unknown = sorted(set(value) - POLICY_OVERRIDE_FIELDS)
    if unknown:
        raise SkillPlanningError(f"unknown policy override field(s): {', '.join(unknown)}")
    if "maximum_retry_attempts" in value:
        retries = value["maximum_retry_attempts"]
        if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 1:
            raise SkillPlanningError("policy_overrides.maximum_retry_attempts must be 0 or 1.")
    return dict(value)


def _validated_skill_parameters(definition: SkillDefinition, parameters: Any) -> dict[str, Any]:
    """Validate ordinary parameters or a non-recursive target-plan wrapper."""
    if definition.skill_name not in PLAN_WRAPPER_SKILLS:
        try:
            return validate_skill_parameters(definition, parameters)
        except SkillCatalogError as exc:
            raise SkillPlanningError(str(exc)) from exc
    if not isinstance(parameters, dict):
        raise SkillPlanningError("skill parameters must be an object.")
    target_name = parameters.get("target_skill")
    try:
        target_definition = get_skill_definition(target_name)
    except SkillCatalogError as exc:
        raise SkillPlanningError(str(exc)) from exc
    if target_definition.skill_name in PLAN_WRAPPER_SKILLS:
        raise SkillPlanningError("target_skill cannot recursively wrap preview_skill_plan or explain_skill_plan.")
    target_parameters = {name: value for name, value in parameters.items() if name != "target_skill"}
    try:
        normalized_target = validate_skill_parameters(target_definition, target_parameters)
    except SkillCatalogError as exc:
        raise SkillPlanningError(str(exc)) from exc
    return {"target_skill": target_definition.skill_name, **normalized_target}


def _validated_checkpoint_id(value: Any) -> str:
    try:
        return validate_checkpoint_id(value)
    except HomeSimulationStateError as exc:
        raise SkillPlanningError(str(exc)) from exc


@dataclass(frozen=True)
class SkillRequest:
    request_id: str
    skill_name: str
    parameters: dict[str, Any] = field(default_factory=dict)
    requested_by: str = "local_review"
    priority: int = NORMAL_PRIORITY
    simulated_battery_percent: float = 100.0
    current_region: str = "charging_area"
    injected_events: dict[str, Any] = field(default_factory=dict)
    policy_overrides: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "request_id": self.request_id,
            "skill_name": self.skill_name,
            "parameters": dict(self.parameters),
            "requested_by": self.requested_by,
            "priority": self.priority,
            "simulated_battery_percent": self.simulated_battery_percent,
            "current_region": self.current_region,
            "injected_events": dict(self.injected_events),
            "policy_overrides": dict(self.policy_overrides),
            "demo_only": True,
            "synthetic_semantics": True,
            "ground_truth": False,
            "simulation_only": True,
            "review_only": True,
            "executable": False,
        }


def create_skill_request(
    skill_name: str,
    parameters: dict[str, Any] | None = None,
    *,
    request_id: str | None = None,
    requested_by: str = "local_review",
    priority: int = NORMAL_PRIORITY,
    battery_percent: float = 100.0,
    current_region: str = "charging_area",
    injected_events: dict[str, Any] | None = None,
    policy_overrides: dict[str, Any] | None = None,
) -> SkillRequest:
    try:
        definition = get_skill_definition(skill_name)
    except SkillCatalogError as exc:
        raise SkillPlanningError(str(exc)) from exc
    validated_parameters = _validated_skill_parameters(definition, {} if parameters is None else parameters)
    identifier = request_id or f"skill-{skill_name}-001"
    if not isinstance(identifier, str) or not identifier or identifier != identifier.strip():
        raise SkillPlanningError("request_id must be a non-empty trimmed string.")
    if not isinstance(requested_by, str) or not requested_by or requested_by != requested_by.strip():
        raise SkillPlanningError("requested_by must be a non-empty trimmed string.")
    if not isinstance(current_region, str) or not current_region or current_region != current_region.strip():
        raise SkillPlanningError("current_region must be a non-empty trimmed region label or alias.")
    requested_priority = _strict_priority(priority)
    return SkillRequest(
        request_id=identifier,
        skill_name=skill_name,
        parameters=validated_parameters,
        requested_by=requested_by,
        priority=requested_priority,
        simulated_battery_percent=_strict_float(battery_percent, "battery-percent", minimum=0.0, maximum=100.0),
        current_region=current_region,
        injected_events=_validated_injected_events({} if injected_events is None else injected_events),
        policy_overrides=_validated_policy_overrides({} if policy_overrides is None else policy_overrides),
    )


def _validate_request(request: SkillRequest, definition: SkillDefinition) -> None:
    if not isinstance(request.request_id, str) or not request.request_id or request.request_id != request.request_id.strip():
        raise SkillPlanningError("request_id must be a non-empty trimmed string.")
    if not isinstance(request.requested_by, str) or not request.requested_by or request.requested_by != request.requested_by.strip():
        raise SkillPlanningError("requested_by must be a non-empty trimmed string.")
    normalized = _validated_skill_parameters(definition, request.parameters)
    if normalized != request.parameters:
        raise SkillPlanningError("SkillRequest parameters are not normalized for the selected definition.")
    priority = _strict_priority(request.priority)
    _strict_float(request.simulated_battery_percent, "simulated_battery_percent", minimum=0.0, maximum=100.0)
    if not isinstance(request.current_region, str) or not request.current_region or request.current_region != request.current_region.strip():
        raise SkillPlanningError("current_region must be a non-empty trimmed region label or alias.")
    _validated_injected_events(request.injected_events)
    _validated_policy_overrides(request.policy_overrides)


@dataclass(frozen=True)
class SkillPlanningContext:
    map_identity: dict[str, Any]
    regions: dict[str, dict[str, Any]]
    goals: dict[str, dict[str, Any]]
    goal_tokens: dict[str, str]
    regions_document: dict[str, Any]
    goals_document: dict[str, Any]


def _require_synthetic_flags(record: dict[str, Any], name: str, *, simulation_required: bool = False) -> None:
    if record.get("demo_only") is not True or record.get("synthetic_semantics") is not True:
        raise SkillPlanningError(f"{name} must be an explicit synthetic demo record.")
    if record.get("ground_truth") is not False or record.get("review_only") is not True or record.get("executable") is not False:
        raise SkillPlanningError(f"{name} has invalid review-only flags.")
    if simulation_required and record.get("simulation_only") is not True:
        raise SkillPlanningError(f"{name} must be simulation_only: true.")


def validate_skill_artifacts(regions_document: dict[str, Any], goals_document: dict[str, Any]) -> SkillPlanningContext:
    """Reuse the sequence preflight; do not recalculate map or raster safety."""
    if not isinstance(regions_document, dict) or not isinstance(goals_document, dict):
        raise SkillPlanningError("skill artifacts must be JSON objects.")
    _require_synthetic_flags(regions_document, "semantic-region document")
    _require_synthetic_flags(goals_document, "safe-goal document")
    region_records = regions_document.get("regions")
    goal_records = goals_document.get("goals")
    if not isinstance(region_records, list) or not isinstance(goal_records, list):
        raise SkillPlanningError("skill artifacts must contain region and goal lists.")
    for region in region_records:
        if not isinstance(region, dict):
            raise SkillPlanningError("semantic-region entries must be objects.")
        _require_synthetic_flags(region, "semantic region")
    for goal in goal_records:
        if not isinstance(goal, dict):
            raise SkillPlanningError("safe-goal entries must be objects.")
        _require_synthetic_flags(goal, "safe goal", simulation_required=True)
    try:
        plan, _ = build_simulation_sequence(regions_document, goals_document, DEFAULT_SEQUENCE)
    except SimulationSequenceError as exc:
        raise SkillPlanningError(str(exc)) from exc
    region_index = {region["canonical_label"]: region for region in region_records}
    goal_index = {goal["canonical_label"]: goal for goal in goal_records}
    source_keys = [(goal["proposal_id"], goal["candidate_partition_id"]) for goal in goal_records]
    goal_orders = [goal["goal_order"] for goal in goal_records]
    pixels = [(goal["goal"]["pixel_row"], goal["goal"]["pixel_column"]) for goal in goal_records]
    if len(source_keys) != len(set(source_keys)):
        raise SkillPlanningError("accepted safe goals contain a duplicate proposal/partition source key.")
    if len(goal_orders) != len(set(goal_orders)):
        raise SkillPlanningError("accepted safe goals contain a duplicate goal_order.")
    if len(pixels) != len(set(pixels)):
        raise SkillPlanningError("accepted safe goals contain a duplicate goal pixel.")
    tokens: dict[str, str] = {}
    for label, goal in goal_index.items():
        for token in (label, str(goal["goal_order"]), f"{goal['proposal_id']}:{goal['candidate_partition_id']}"):
            if token in tokens and tokens[token] != label:
                raise SkillPlanningError("accepted safe-goal identifiers are ambiguous.")
            tokens[token] = label
    return SkillPlanningContext(plan["map_identity"], region_index, goal_index, tokens, regions_document, goals_document)


def resolve_region_alias(value: Any, available_labels: Iterable[str]) -> tuple[str | None, tuple[str, ...]]:
    if not isinstance(value, str) or not value or value != value.strip():
        raise SkillPlanningError("region label or alias must be a non-empty trimmed string.")
    normalized = value.casefold()
    candidates = REGION_ALIASES.get(normalized, (value,) if value in available_labels else ())
    available = tuple(label for label in candidates if label in available_labels)
    if not available:
        raise SkillPlanningError(f"unknown semantic region label or alias: {value}")
    if len(available) > 1:
        return None, available
    return available[0], available


def _goal_reference(context: SkillPlanningContext, label: str) -> dict[str, Any]:
    goal = context.goals[label]
    data = goal["goal"]
    return {
        "canonical_label": label,
        "proposal_id": goal["proposal_id"],
        "partition_id": goal["candidate_partition_id"],
        "source_candidate_order": goal["source_candidate_order"],
        "source_selection_rank": goal["source_selection_rank"],
        "demo_assignment_order": goal["demo_assignment_order"],
        "goal_order": goal["goal_order"],
        "goal_pixel": {"row": data["pixel_row"], "column": data["pixel_column"]},
        "goal_map": {"x": data["map_x"], "y": data["map_y"]},
        "clearance_m": data["clearance_m"],
    }


def _base_action(action_type: str, reason: str, *, critical: bool, label: str | None = None, details: dict[str, Any] | None = None) -> dict[str, Any]:
    action = {
        "action_type": action_type,
        "label": label,
        "reason": reason,
        "critical": critical,
        "non_critical": not critical,
        "attempt": 1,
        "status": "pending",
        "terminal_reason": None,
        "interruption_reason": None,
        "recovery_action": None,
        "demo_only": True,
        "synthetic_semantics": True,
        "ground_truth": False,
        "simulation_only": True,
        "review_only": True,
        "executable": False,
    }
    if details:
        action["details"] = details
    return action


def _navigation(context: SkillPlanningContext, label: str, reason: str, state: HomeSimulationState, *, action_type: str = "navigate_to_safe_goal") -> dict[str, Any]:
    if label in state.restricted_regions:
        raise SkillPolicyError(f"RESTRICTED_REGION: access denied for {label}.")
    reference = _goal_reference(context, label)
    if _goal_is_blocked(reference, state):
        raise SkillPolicyError(f"BLOCKED_GOAL: accepted goal for {label} is blocked and no same-region accepted alternative exists.")
    action = _base_action(action_type, reason, critical=True, label=label)
    action["goal_reference"] = reference
    return action


def _goal_is_blocked(reference: dict[str, Any], state: HomeSimulationState) -> bool:
    tokens = {reference["canonical_label"], str(reference["goal_order"]), f"{reference['proposal_id']}:{reference['partition_id']}"}
    return bool(tokens.intersection(state.blocked_goals))


def _inspect(label: str, reason: str, *, critical: bool = False) -> dict[str, Any]:
    return _base_action("inspect_region", reason, critical=critical, label=label, details={"real_sensor_detection": False})


def _simulated_manipulation(action_type: str, label: str, item: Any, reason: str) -> dict[str, Any]:
    return _base_action(
        action_type,
        reason,
        critical=False,
        label=label,
        details={"item": item, "physical_manipulation": False, "simulated_manipulation": True},
    )


def _resolve_required_area(value: Any, context: SkillPlanningContext) -> str:
    canonical, candidates = resolve_region_alias(value, context.goals)
    if canonical is None:
        raise SkillPolicyError(f"CONFIRMATION_REQUIRED: ambiguous region alias matches {', '.join(candidates)}.")
    return canonical


def _route_actions(route: Iterable[str], context: SkillPlanningContext, state: HomeSimulationState, *, inspect: bool = True, routine: str | None = None) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for label in route:
        actions.append(_navigation(context, label, f"Reach the accepted safe goal for {label}.", state))
        if routine is None:
            if inspect:
                actions.append(_inspect(label, f"Simulate review of {label}; no sensor claim is made."))
            continue
        if routine in {"bedtime_routine", "leave_home_routine", "morning_routine"}:
            actions.append(_base_action("switch_device_simulated", f"Simulate the {routine} device-state step.", critical=False, label=label, details={"device": "lights", "real_device_control": False}))
        else:
            actions.append(_base_action("check_alarm_simulated", "Simulate a security status check; no real detection occurs.", critical=False, label=label, details={"real_sensor_detection": False}))
    return actions


def _room_check_summary(route: Iterable[str]) -> dict[str, Any]:
    checked_regions = list(route)
    summary = _base_action(
        "report_home_check_summary",
        "Summarize deterministic synthetic room checks; no sensor result is claimed.",
        critical=False,
        details={
            "checked_regions": checked_regions,
            "synthetic_room_check_summary": True,
            "real_sensor_detection": False,
        },
    )
    summary.update(
        synthetic=True,
        synthetic_room_check_summary=True,
        real_sensor_detection=False,
        review_only=True,
        simulation_only=True,
        executable=False,
    )
    return summary


def _nearest_route(context: SkillPlanningContext, state: HomeSimulationState, *, all_regions: bool, allow_current_fallback: bool = False) -> list[str]:
    current = _resolve_required_area(state.current_region, context)
    remaining = [label for label in DEFAULT_SEQUENCE if label != current]
    if not all_regions:
        remaining = [label for label in remaining if label not in state.restricted_regions and not _goal_is_blocked(_goal_reference(context, label), state)]
        if not remaining and allow_current_fallback and current not in state.restricted_regions and not _goal_is_blocked(_goal_reference(context, current), state):
            return [current]
        if not remaining:
            raise SkillPolicyError("NO_AVAILABLE_SAFE_ZONE: no unrestricted, unblocked accepted goal is available.")
    route: list[str] = []
    position = context.goals[current]["goal"]
    while remaining:
        next_label = min(
            remaining,
            key=lambda label: (
                math.hypot(context.goals[label]["goal"]["map_x"] - position["map_x"], context.goals[label]["goal"]["map_y"] - position["map_y"]),
                DEFAULT_SEQUENCE.index(label),
            ),
        )
        route.append(next_label)
        position = context.goals[next_label]["goal"]
        remaining.remove(next_label)
        if not all_regions:
            break
    return route


def _single_area_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> list[dict[str, Any]]:
    raw = request.parameters.get("area", request.parameters.get("destination"))
    label = _resolve_required_area(raw, context)
    navigation_type = "navigate_to_region" if definition.skill_name == "inspect_area" else "navigate_to_safe_goal"
    actions = [_navigation(context, label, f"Resolve {definition.skill_name} to an accepted safe goal.", state, action_type=navigation_type)]
    if definition.skill_name == "inspect_area":
        actions.append(_inspect(label, "Simulate the requested region inspection."))
    elif definition.skill_name == "safe_wait":
        duration = _strict_float(request.parameters["duration_seconds"], "duration_seconds", minimum=0.0)
        actions.append(_base_action("wait_simulated", "Advance logical wait state without sleeping.", critical=False, label=label, details={"simulated_duration_seconds": duration}))
    else:
        actions.append(_base_action("report_status", f"Report simulated {definition.skill_name} completion.", critical=False, label=label, details={"person_tracking": False, "person_recognition": False}))
    return actions


def _item_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> list[dict[str, Any]]:
    item = request.parameters.get("item", request.parameters.get("items"))
    source_raw = request.parameters.get("source", state.current_region)
    destination_raw = request.parameters.get("destination", state.current_region)
    source = _resolve_required_area(source_raw, context)
    destination = _resolve_required_area(destination_raw, context)
    actions: list[dict[str, Any]] = []
    if definition.skill_name != "handover_at_safe_point":
        actions.append(_navigation(context, source, "Reach the item's simulated source using an accepted goal.", state))
        actions.append(_simulated_manipulation("pick_item_simulated", source, item, "Record simulated pickup; no manipulator is used."))
    if definition.skill_name in {"deliver_item", "fetch_and_return", "handover_at_safe_point", "move_item_to_storage"}:
        actions.append(_navigation(context, destination, "Reach the simulated destination using an accepted goal.", state))
        action_type = "handover_item_simulated" if definition.skill_name in {"deliver_item", "fetch_and_return", "handover_at_safe_point"} else "place_item_simulated"
        actions.append(_simulated_manipulation(action_type, destination, item, "Record simulated placement or handover; no physical action occurs."))
    else:
        actions.append(_base_action("report_status", "Report simulated item collection only.", critical=False, label=source, details={"item": item, "physical_manipulation": False, "simulated_manipulation": True}))
    return actions


def _emergency_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> list[dict[str, Any]]:
    alarm_region = request.injected_events.get("alarm_region")
    if alarm_region is None:
        raise SkillPolicyError("SIMULATED_ALARM_REQUIRED: emergency skills require injected_events.alarm_region.")
    label = _resolve_required_area(alarm_region, context)
    actions: list[dict[str, Any]] = []
    if definition.skill_name == "emergency_task_preemption":
        actions.append(_base_action("checkpoint", "Checkpoint the lower-priority simulated task before preemption.", critical=True, details={"checkpoint_id": "emergency-preemption"}))
        actions.append(_base_action("abort_task", "Cancel remaining lower-priority simulated steps.", critical=True, details={"reason_code": "EMERGENCY_PREEMPTION"}))
    actions.append(_navigation(context, label, "Visit the accepted goal associated with the injected simulated alarm.", state))
    actions.append(_base_action("check_alarm_simulated", "Review the injected alarm event; no physical sensor reading occurs.", critical=True, label=label, details={"alarm_type": request.injected_events.get("alarm_type", "simulated_alarm"), "real_sensor_detection": False}))
    if definition.skill_name == "emergency_response":
        actions.append(_base_action("report_status", "Report simulated emergency workflow status.", critical=False, label=label))
    return actions


def _policy_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> tuple[list[dict[str, Any]], str, str | None]:
    if definition.skill_name == "restricted_area_guard":
        label = _resolve_required_area(request.parameters["area"], context)
        if label not in state.restricted_regions:
            return [_base_action("report_status", "Region is not in the simulated restricted set.", critical=False, label=label)], "ready", None
        return [_base_action("explain_rejection", "Explain fail-closed restricted-region policy.", critical=True, label=label, details={"reason_code": "RESTRICTED_REGION", "explanation": f"Access to {label} is restricted in local simulation state."})], "rejected", "RESTRICTED_REGION"
    token = request.parameters["goal"]
    if not isinstance(token, str) or not token or token != token.strip():
        raise SkillPlanningError("goal must be a non-empty trimmed identifier.")
    if token in context.goal_tokens:
        raise SkillPolicyError("GOAL_IS_ACCEPTED: unsafe_goal_rejection cannot relabel an accepted artifact goal as unsafe.")
    return [_base_action("explain_rejection", "Explain why an unknown goal cannot be authorized.", critical=True, details={"reason_code": "UNACCEPTED_GOAL", "explanation": "The goal is absent from the validated accepted-goal artifact."})], "rejected", "UNACCEPTED_GOAL"


def _recovery_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> list[dict[str, Any]]:
    name = definition.skill_name
    if name in {"fallback_to_safe_goal", "blocked_goal_replan"}:
        label = _resolve_required_area(request.parameters["area"], context)
        reference = _goal_reference(context, label)
        tokens = {label, str(reference["goal_order"]), f"{reference['proposal_id']}:{reference['partition_id']}"}
        if tokens.intersection(state.blocked_goals):
            raise SkillPolicyError(f"NO_ALTERNATE_SAFE_GOAL: {label} has no second accepted same-region goal; no coordinates were generated.")
        select = _base_action("select_nearest_safe_goal", "Select only from unblocked accepted goals; never generate coordinates.", critical=True, label=label)
        select["goal_reference"] = reference
        return [select, _navigation(context, label, "Use the selected existing accepted goal.", state)]
    if name == "retry_failed_step":
        label = _resolve_required_area(request.parameters["area"], context)
        return [_inspect(label, "Injectable non-critical inspection for bounded retry."), _base_action("retry_step", "Permit at most one retry of the preceding non-critical action.", critical=False, label=label, details={"maximum_retries": 1})]
    if name == "skip_failed_step":
        label = _resolve_required_area(request.parameters["area"], context)
        return [_inspect(label, "Injectable explicitly non-critical inspection."), _base_action("skip_step", "Permit skip only for the preceding non-critical action.", critical=False, label=label)]
    if name in {"resume_interrupted_task"}:
        checkpoint_id = _validated_checkpoint_id(request.parameters["checkpoint_id"])
        return [_base_action("restore_checkpoint", "Restore deterministic checkpoint metadata.", critical=True, details={"checkpoint_id": checkpoint_id})]
    if name == "abort_and_return":
        return [_base_action("abort_task", "Cancel remaining simulated work.", critical=True, details={"reason_code": "USER_ABORT"}), _navigation(context, "charging_area", "Return using the accepted charging_area goal.", state, action_type="return_to_charger")]
    checkpoint_id = _validated_checkpoint_id(request.parameters["checkpoint_id"])
    return [_base_action("checkpoint", "Save deterministic local simulation metadata.", critical=True, details={"checkpoint_id": checkpoint_id})]


def _battery_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> list[dict[str, Any]]:
    name = definition.skill_name
    charger = _navigation(context, "charging_area", "Use the accepted charging_area goal.", state, action_type="return_to_charger")
    if name == "return_to_charger":
        return [charger]
    if name == "low_battery_abort":
        return [_base_action("abort_task", "Cancel incomplete simulated work because battery is below policy threshold.", critical=True, details={"reason_code": "LOW_BATTERY"}), charger]
    if name == "battery_aware_planning":
        return [_base_action("report_status", "Report deterministic simulated battery budget.", critical=True, details={"battery_percent": state.battery_percent, "low_battery_threshold_percent": LOW_BATTERY_THRESHOLD_PERCENT})]
    if name == "charge_then_resume":
        return [
            _base_action("checkpoint", "Save a deterministic checkpoint before charging.", critical=True, details={"checkpoint_id": "charge-resume-001"}),
            charger,
            _base_action("charge_simulated", "Set only the local simulated battery state to full.", critical=True, label="charging_area", details={"real_battery_reading": False}),
            _base_action("restore_checkpoint", "Restore the deterministic task checkpoint while retaining simulated charge.", critical=True, details={"checkpoint_id": "charge-resume-001", "preserve_battery": True}),
        ]
    raise SkillPlanningError(f"unsupported battery builder: {name}")


def _interaction_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> tuple[list[dict[str, Any]], str, str | None]:
    name = definition.skill_name
    if name in {"confirm_ambiguous_target", "semantic_alias_resolution"}:
        canonical, candidates = resolve_region_alias(request.parameters["target"], context.goals)
        if canonical is None:
            action = _base_action("request_confirmation", "Do not guess between multiple matching semantic regions.", critical=True, details={"candidates": list(candidates), "reason_code": "AMBIGUOUS_TARGET"})
            return [action], "confirmation_required", "AMBIGUOUS_TARGET"
        return [_base_action("report_status", "Report deterministic alias resolution.", critical=False, label=canonical, details={"resolved_label": canonical})], "ready", None
    if name == "explain_rejection":
        reason_code = request.parameters["reason_code"]
        if not isinstance(reason_code, str) or not reason_code:
            raise SkillPlanningError("reason_code must be a non-empty string.")
        return [_base_action("explain_rejection", "Return structured local policy information.", critical=False, details={"reason_code": reason_code, "explanation": f"Simulation request was rejected: {reason_code}."})], "ready", None
    if name == "cancel_current_task":
        return [_base_action("abort_task", "Apply deterministic cancellation to the active local task.", critical=True, details={"reason_code": "USER_REQUESTED_CANCEL"})], "ready", None
    if name == "change_task_priority":
        task_id = request.parameters["task_id"]
        if not isinstance(task_id, str) or not task_id or task_id != task_id.strip():
            raise SkillPlanningError("task_id must be a non-empty trimmed string.")
        priority = request.parameters["new_priority"]
        if isinstance(priority, bool) or not isinstance(priority, int) or not 0 <= priority < EMERGENCY_PRIORITY:
            raise SkillPolicyError("PRIORITY_POLICY: normal task priority must be an integer from 0 through 99.")
        if state.active_task == task_id:
            return [_base_action("explain_rejection", "Reject priority changes for the active simulated task.", critical=True, details={"reason_code": "ACTIVE_TASK_PRIORITY_IMMUTABLE", "task_id": task_id, "explanation": "The active simulated task priority is immutable."})], "rejected", "ACTIVE_TASK_PRIORITY_IMMUTABLE"
        matching = [task for task in state.queued_tasks if task.get("task_id") == task_id]
        if len(matching) != 1:
            return [_base_action("explain_rejection", "Reject a priority change for a task absent from the deterministic queue.", critical=True, details={"reason_code": "TASK_NOT_FOUND", "task_id": task_id, "explanation": "No unique queued task matches task_id."})], "rejected", "TASK_NOT_FOUND"
        task = matching[0]
        try:
            queued_definition = get_skill_definition(task.get("skill_name"))
        except SkillCatalogError as exc:
            raise SkillPlanningError(str(exc)) from exc
        if queued_definition.skill_name in EMERGENCY_QUEUE_SKILLS:
            return [_base_action("explain_rejection", "Reject any priority change for a fixed emergency task.", critical=True, details={"reason_code": "EMERGENCY_PRIORITY_IMMUTABLE", "task_id": task_id, "explanation": "Emergency queued tasks always retain priority 100."})], "rejected", "EMERGENCY_PRIORITY_IMMUTABLE"
        before = [dict(item) for item in state.ordered_queue()]
        projected = [dict(item) for item in state.queued_tasks]
        next(item for item in projected if item["task_id"] == task_id)["priority"] = priority
        after = sorted(projected, key=lambda item: (-item["priority"], item["insertion_order"]))
        return [_base_action("report_status", "Apply a deterministic priority change to one queued normal task.", critical=False, details={"task_id": task_id, "old_priority": task["priority"], "new_priority": priority, "queue_order_before": before, "queue_order_after": after, "emergency_priority": EMERGENCY_PRIORITY})], "ready", None
    return [_base_action("report_status", "Report deterministic local task status.", critical=False, details={"active_task": state.active_task})], "ready", None


def _target_wrapper_plan(request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState, *, explain: bool) -> tuple[list[dict[str, Any]], str, str | None, dict[str, Any], dict[str, Any]]:
    target_name = request.parameters["target_skill"]
    target_parameters = {name: value for name, value in request.parameters.items() if name != "target_skill"}
    target_request = create_skill_request(
        target_name,
        target_parameters,
        request_id=f"{request.request_id}-target",
        requested_by=request.requested_by,
        priority=request.priority,
        battery_percent=state.battery_percent,
        current_region=state.current_region,
        injected_events=request.injected_events,
        policy_overrides=request.policy_overrides,
    )
    target_plan = compile_skill_plan(
        target_request,
        context.regions_document,
        context.goals_document,
        copy.deepcopy(state),
    )
    actions = copy.deepcopy(target_plan["steps"])
    for action in actions:
        target_step_order = action.pop("step_order")
        if explain:
            action["step_explanation"] = {
                "target_step_order": target_step_order,
                "action": action["action_type"],
                "target_region": action.get("label"),
                "goal_reference": action.get("goal_reference"),
                "why": action["reason"],
                "parameter_sources": dict(target_parameters),
                "safety_policy": target_plan["safety_policy"],
                "critical": action["critical"],
                "skippable": action["non_critical"],
                "retryable": action["non_critical"],
            }
    metadata = {
        "requested_wrapper_skill": request.skill_name,
        "target_skill": target_name,
        "target_parameters": target_parameters,
        "target_required_parameters": list(get_skill_definition(target_name).required_parameters),
        "target_optional_parameters": list(get_skill_definition(target_name).optional_parameters),
        "target_plan_metadata": {
            "category": target_plan["category"],
            "description": target_plan["description"],
            "planning_status": target_plan["planning_status"],
            "reason_code": target_plan["reason_code"],
            "safety_policy": target_plan["safety_policy"],
            "interruption_policy": target_plan["interruption_policy"],
            "recovery_policy": target_plan["recovery_policy"],
            "policy": target_plan["policy"],
        },
        "target_plan_steps": copy.deepcopy(_number_steps(actions)),
    }
    return actions, target_plan["planning_status"], target_plan["reason_code"], metadata, target_plan["policy"]


def _management_actions(definition: SkillDefinition, request: SkillRequest, context: SkillPlanningContext, state: HomeSimulationState) -> tuple[list[dict[str, Any]], str, str | None, dict[str, Any] | None, dict[str, Any] | None]:
    name = definition.skill_name
    if name == "pause_current_task":
        return [_base_action("checkpoint", "Save pause metadata without global background state.", critical=True, details={"checkpoint_id": "pause-001"}), _base_action("report_status", "Report paused metadata; no background task continues.", critical=False)], "ready", None, None, None
    if name == "resume_current_task":
        checkpoint_id = _validated_checkpoint_id(request.parameters["checkpoint_id"])
        return [_base_action("restore_checkpoint", "Restore deterministic paused-task metadata.", critical=True, details={"checkpoint_id": checkpoint_id})], "ready", None, None, None
    if name == "queue_task":
        queued_skill = request.parameters["queued_skill"]
        try:
            queued_definition = get_skill_definition(queued_skill)
        except SkillCatalogError as exc:
            raise SkillPlanningError(str(exc)) from exc
        queued_priority = EMERGENCY_PRIORITY if queued_skill in EMERGENCY_QUEUE_SKILLS else request.priority
        return [_base_action("report_status", "Queue a known simulated capability using the explicit emergency allowlist or stable normal priority/FIFO order.", critical=False, details={"queued_skill": queued_skill, "priority": queued_priority, "emergency_priority_applied": queued_skill in EMERGENCY_QUEUE_SKILLS, "task_id": state.next_task_id(), "source_request_id": request.request_id})], "ready", None, None, None
    if name == "list_queued_tasks":
        return [_base_action("report_status", "List queued tasks in priority/FIFO order.", critical=False, details={"queued_tasks": state.ordered_queue()})], "ready", None, None, None
    if name in {"preview_skill_plan", "explain_skill_plan"}:
        return _target_wrapper_plan(request, context, state, explain=name == "explain_skill_plan")
    if name == "list_capabilities":
        document = catalog_document()
        return [_base_action("report_status", "Return the complete deterministic catalog with support and parameter metadata.", critical=False, details={"capability_count": document["capability_count"], "capabilities": document["capabilities"]})], "ready", None, None, None
    return [_base_action("report_status", "Export logical task events without timestamps.", critical=False, details={"trace_format": "deterministic_jsonl"})], "ready", None, None, None


def _number_steps(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"step_order": order, **action} for order, action in enumerate(actions, start=1)]


def compile_skill_plan(
    request: SkillRequest,
    regions_document: dict[str, Any],
    goals_document: dict[str, Any],
    state: HomeSimulationState | None = None,
) -> dict[str, Any]:
    """Compile a deterministic plan without starting ROS or executing actions."""
    context = validate_skill_artifacts(regions_document, goals_document)
    definition = get_skill_definition(request.skill_name)
    _validate_request(request, definition)
    simulation_state = copy.deepcopy(state) if state is not None and definition.skill_name in PLAN_WRAPPER_SKILLS else state or HomeSimulationState(
        current_region=_resolve_required_area(request.current_region, context),
        battery_percent=request.simulated_battery_percent,
    )
    simulation_state.current_region = _resolve_required_area(simulation_state.current_region, context)
    simulation_state.battery_percent = _strict_float(simulation_state.battery_percent, "battery_percent", minimum=0.0, maximum=100.0)
    simulation_state.restricted_regions = tuple(_resolve_required_area(value, context) for value in simulation_state.restricted_regions)
    planning_status, reason_code = "ready", None
    wrapper_metadata: dict[str, Any] | None = None
    target_policy: dict[str, Any] | None = None

    if definition.builder == "route":
        actions = _route_actions(ROUTINE_ROUTES[definition.skill_name], context, simulation_state)
        if definition.skill_name == "check_all_rooms":
            actions.append(_room_check_summary(ROUTINE_ROUTES[definition.skill_name]))
    elif definition.builder == "routine":
        actions = _route_actions(ROUTINE_ROUTES[definition.skill_name], context, simulation_state, routine=definition.skill_name)
    elif definition.builder == "single_area":
        actions = _single_area_actions(definition, request, context, simulation_state)
    elif definition.builder == "nearest":
        route = _nearest_route(context, simulation_state, all_regions=False, allow_current_fallback=definition.skill_name == "find_nearest_safe_zone")
        if definition.skill_name == "find_nearest_safe_zone":
            actions = [
                _navigation(context, route[0], "Choose by accepted-goal Euclidean distance with fixed label tie-break.", simulation_state, action_type="select_nearest_safe_goal"),
                _navigation(context, route[0], "Visit the selected existing accepted safe goal.", simulation_state),
            ]
        else:
            actions = [_navigation(context, route[0], "Choose by accepted-goal Euclidean distance with fixed label tie-break.", simulation_state)]
    elif definition.builder == "item_flow":
        actions = _item_actions(definition, request, context, simulation_state)
    elif definition.builder == "emergency":
        actions = _emergency_actions(definition, request, context, simulation_state)
    elif definition.builder == "policy":
        actions, planning_status, reason_code = _policy_actions(definition, request, context, simulation_state)
    elif definition.builder == "recovery":
        actions = _recovery_actions(definition, request, context, simulation_state)
    elif definition.builder == "battery":
        actions = _battery_actions(definition, request, context, simulation_state)
    elif definition.builder == "energy_route":
        route = _nearest_route(context, simulation_state, all_regions=True)
        actions = _route_actions(route, context, simulation_state, inspect=False)
    elif definition.builder == "interaction":
        actions, planning_status, reason_code = _interaction_actions(definition, request, context, simulation_state)
    elif definition.builder == "management":
        actions, planning_status, reason_code, wrapper_metadata, target_policy = _management_actions(definition, request, context, simulation_state)
    else:
        raise SkillPlanningError(f"unsupported skill builder: {definition.builder}")

    numbered = _number_steps(actions)
    estimated_energy = sum(NAVIGATION_ENERGY_COST_PERCENT if action["action_type"] in {"navigate_to_region", "navigate_to_safe_goal", "return_to_charger"} else ACTION_ENERGY_COST_PERCENT for action in numbered)
    low_battery_exempt = definition.category == "battery_resource" or definition.category == "safety_emergency"
    required_battery = min(100.0, LOW_BATTERY_THRESHOLD_PERCENT + estimated_energy)
    policy = {
        "low_battery_threshold_percent": LOW_BATTERY_THRESHOLD_PERCENT,
        "estimated_energy_cost_percent": estimated_energy,
        "required_battery_percent": required_battery,
        "battery_precheck_passed": low_battery_exempt or simulation_state.battery_percent >= required_battery,
        "low_battery_exempt": low_battery_exempt,
        "reorder_allowed": definition.reorder_allowed,
        "maximum_retry_attempts": request.policy_overrides.get("maximum_retry_attempts", 1),
        "return_to_charger_action": _navigation(context, "charging_area", "Low-battery contingency uses the accepted charging_area goal.", simulation_state, action_type="return_to_charger"),
    }
    if target_policy is not None:
        policy = copy.deepcopy(target_policy)
    result = {
        "schema_version": "1.0",
        "request_id": request.request_id,
        "skill_name": request.skill_name,
        "category": definition.category,
        "description": definition.description,
        "planning_status": planning_status,
        "reason_code": reason_code,
        "demo_only": True,
        "synthetic_semantics": True,
        "ground_truth": False,
        "simulation_only": True,
        "review_only": True,
        "executable": False,
        "physical_capability_required": definition.physical_capability_required,
        "map_identity": context.map_identity,
        "safety_policy": definition.safety_policy,
        "interruption_policy": definition.interruption_policy,
        "recovery_policy": definition.recovery_policy,
        "policy": policy,
        "total_steps": len(numbered),
        "steps": numbered,
    }
    if wrapper_metadata is not None:
        result["target_plan"] = wrapper_metadata
    return result
