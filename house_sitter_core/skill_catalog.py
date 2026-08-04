"""Declaration-driven catalog for local simulation-only smart-home skills."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .home_simulation_state import EMERGENCY_QUEUE_SKILLS


class SkillCatalogError(ValueError):
    """Raised when a skill name or parameter set is invalid."""


CATEGORIES = (
    "area_access",
    "item_service",
    "smart_home_routine",
    "safety_emergency",
    "reliability_recovery",
    "battery_resource",
    "human_interaction",
    "task_management",
)


@dataclass(frozen=True)
class SkillDefinition:
    skill_name: str
    category: str
    description: str
    required_parameters: tuple[str, ...]
    optional_parameters: tuple[str, ...]
    default_parameters: tuple[tuple[str, Any], ...]
    allowed_action_types: tuple[str, ...]
    safety_policy: str
    interruption_policy: str
    recovery_policy: str
    builder: str
    simulation_only: bool = True
    review_only: bool = True
    executable: bool = False
    physical_capability_required: bool = False
    supported: bool = True
    unsupported_reason: str | None = None
    reorder_allowed: bool = False

    def defaults(self) -> dict[str, Any]:
        return dict(self.default_parameters)

    def as_dict(self, supplied_parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        supplied = supplied_parameters or {}
        classification = (
            "management"
            if self.category == "task_management"
            else "recovery_policy"
            if self.category == "reliability_recovery"
            else "user_skill"
        )
        return {
            "name": self.skill_name,
            "skill_name": self.skill_name,
            "category": self.category,
            "classification": classification,
            "user_callable": True,
            "description": self.description,
            "required_parameters": list(self.required_parameters),
            "optional_parameters": list(self.optional_parameters),
            "default_parameters": self.defaults(),
            "missing_parameters": [name for name in self.required_parameters if name not in supplied],
            "allowed_action_types": list(self.allowed_action_types),
            "safety_policy": self.safety_policy,
            "interruption_policy": self.interruption_policy,
            "recovery_policy": self.recovery_policy,
            "implementation_kind": self.builder,
            "simulation_only": self.simulation_only,
            "review_only": self.review_only,
            "executable": self.executable,
            "physical_capability_required": self.physical_capability_required,
            "supported": self.supported,
            "unsupported_reason": self.unsupported_reason,
            "reorder_allowed": self.reorder_allowed,
            "queue_priority_policy": "fixed_emergency_100" if self.skill_name in EMERGENCY_QUEUE_SKILLS else "ordinary_0_99",
        }


def _definition(
    name: str,
    category: str,
    description: str,
    builder: str,
    actions: tuple[str, ...],
    *,
    required: tuple[str, ...] = (),
    optional: tuple[str, ...] = (),
    defaults: tuple[tuple[str, Any], ...] = (),
    physical: bool = False,
    reorder: bool = False,
    safety: str = "validated_accepted_safe_goals_only",
    interruption: str = "deterministic_cancel_remaining_steps",
    recovery: str = "bounded_no_implicit_retry",
) -> SkillDefinition:
    return SkillDefinition(
        skill_name=name,
        category=category,
        description=description,
        required_parameters=required,
        optional_parameters=optional,
        default_parameters=defaults,
        allowed_action_types=actions,
        safety_policy=safety,
        interruption_policy=interruption,
        recovery_policy=recovery,
        builder=builder,
        physical_capability_required=physical,
        reorder_allowed=reorder,
    )


NAV = ("navigate_to_region", "navigate_to_safe_goal", "report_status")
INSPECT = ("navigate_to_region", "navigate_to_safe_goal", "inspect_region", "report_status")
ITEM = ("navigate_to_safe_goal", "pick_item_simulated", "place_item_simulated", "handover_item_simulated", "report_status")
ROUTINE = ("navigate_to_safe_goal", "inspect_region", "switch_device_simulated", "check_alarm_simulated", "report_status")
RECOVERY = ("inspect_region", "navigate_to_safe_goal", "checkpoint", "restore_checkpoint", "retry_step", "skip_step", "abort_task", "return_to_charger", "select_nearest_safe_goal", "report_status")
INTERACTION = ("request_confirmation", "explain_rejection", "report_status")
SUMMARY = "report_home_check_summary"
ALL_ACTION_TYPES = tuple(dict.fromkeys(NAV + INSPECT + ITEM + ROUTINE + RECOVERY + INTERACTION + ("wait_simulated", "charge_simulated", SUMMARY)))


_DEFINITIONS = (
    # Area access (1-7)
    _definition("patrol_home", "area_access", "Visit the four synthetic demo regions in fixed order.", "route", INSPECT),
    _definition("inspect_area", "area_access", "Visit and simulate inspection of one region.", "single_area", INSPECT, required=("area",)),
    _definition("check_all_rooms", "area_access", "Inspect every synthetic region and publish a deterministic synthetic room-check summary.", "route", INSPECT + (SUMMARY,)),
    _definition("visit_nearest_area", "area_access", "Choose the nearest accepted region goal by deterministic map distance.", "nearest", NAV),
    _definition("go_to_safe_waiting_area", "area_access", "Visit a configured safe waiting region.", "single_area", NAV, defaults=(("area", "charging_area"),)),
    _definition("escort_to_area", "area_access", "Simulate an escort workflow without person tracking.", "single_area", NAV, required=("destination",), physical=True),
    _definition("visitor_greeting", "area_access", "Simulate greeting at an accepted safe point without recognition.", "single_area", NAV, defaults=(("area", "living_room"),), physical=True),
    # Item and service (8-13)
    _definition("deliver_item", "item_service", "Simulate item pickup and delivery between safe regions.", "item_flow", ITEM, required=("item", "source", "destination"), physical=True),
    _definition("fetch_item", "item_service", "Simulate fetching an item from a safe region.", "item_flow", ITEM, required=("item", "source"), physical=True),
    _definition("fetch_and_return", "item_service", "Simulate fetching an item and returning to the starting region.", "item_flow", ITEM, required=("item", "source"), physical=True),
    _definition("handover_at_safe_point", "item_service", "Simulate handover at a validated safe goal.", "item_flow", ITEM, required=("item", "destination"), physical=True),
    _definition("collect_items", "item_service", "Simulate collecting named items at a source region.", "item_flow", ITEM, required=("items", "source"), physical=True),
    _definition("move_item_to_storage", "item_service", "Simulate moving an item to a storage region.", "item_flow", ITEM, required=("item", "source", "destination"), physical=True),
    # Smart-home routines (14-17)
    _definition("bedtime_routine", "smart_home_routine", "Fixed bedtime review routine over four synthetic regions.", "routine", ROUTINE),
    _definition("leave_home_routine", "smart_home_routine", "Fixed leave-home review routine over four synthetic regions.", "routine", ROUTINE),
    _definition("morning_routine", "smart_home_routine", "Fixed morning review routine over four synthetic regions.", "routine", ROUTINE),
    _definition("security_check_routine", "smart_home_routine", "Fixed simulated security-check routine.", "routine", ROUTINE),
    # Safety and emergency (18-24)
    _definition("emergency_response", "safety_emergency", "Respond only to an injected simulated alarm.", "emergency", ("navigate_to_safe_goal", "check_alarm_simulated", "report_status"), safety="injected_alarm_and_validated_safe_goal_required", interruption="preempt_lower_priority_simulated_task", recovery="abort_on_invalid_alarm"),
    _definition("go_to_alarm_source", "safety_emergency", "Visit an injected simulated alarm region.", "emergency", ("navigate_to_safe_goal", "check_alarm_simulated"), safety="injected_alarm_and_validated_safe_goal_required"),
    _definition("find_nearest_safe_zone", "safety_emergency", "Select the nearest unrestricted accepted safe goal.", "nearest", ("select_nearest_safe_goal", "navigate_to_safe_goal")),
    _definition("restricted_area_guard", "safety_emergency", "Reject simulated access to a restricted region.", "policy", INTERACTION, required=("area",), safety="restricted_regions_fail_closed"),
    _definition("unsafe_goal_rejection", "safety_emergency", "Explain rejection of a goal absent from accepted artifacts.", "policy", INTERACTION, required=("goal",), safety="unknown_or_unaccepted_goals_fail_closed"),
    _definition("safe_wait", "safety_emergency", "Wait logically at an accepted safe goal.", "single_area", ("navigate_to_safe_goal", "wait_simulated"), defaults=(("area", "charging_area"), ("duration_seconds", 0.0))),
    _definition("emergency_task_preemption", "safety_emergency", "Preempt a lower-priority simulated task for an injected alarm.", "emergency", ("checkpoint", "abort_task", "navigate_to_safe_goal", "check_alarm_simulated"), safety="injected_alarm_and_validated_safe_goal_required", interruption="emergency_priority_preemption"),
    # Reliability and recovery (25-31)
    _definition("retry_failed_step", "reliability_recovery", "Retry one simulated non-safety action with a fixed upper bound.", "recovery", RECOVERY, defaults=(("area", "living_room"),), recovery="maximum_one_retry"),
    _definition("skip_failed_step", "reliability_recovery", "Skip only an explicitly non-critical simulated step.", "recovery", RECOVERY, defaults=(("area", "living_room"),), recovery="non_critical_steps_only"),
    _definition("fallback_to_safe_goal", "reliability_recovery", "Fail closed unless the accepted artifact exposes a distinct same-region alternative.", "recovery", RECOVERY, required=("area",), safety="same_region_accepted_alternative_only"),
    _definition("resume_interrupted_task", "reliability_recovery", "Restore a deterministic local checkpoint.", "recovery", RECOVERY, required=("checkpoint_id",)),
    _definition("abort_and_return", "reliability_recovery", "Abort a simulated task and return to charging_area.", "recovery", RECOVERY),
    _definition("blocked_goal_replan", "reliability_recovery", "Reuse only an existing accepted alternative; the current unique-goal contract fails closed.", "recovery", RECOVERY, required=("area",), safety="never_generate_new_coordinates"),
    _definition("task_checkpoint", "reliability_recovery", "Create a deterministic local task checkpoint.", "recovery", RECOVERY, defaults=(("checkpoint_id", "checkpoint-001"),)),
    # Battery and resources (32-36)
    _definition("return_to_charger", "battery_resource", "Visit the accepted charging_area goal.", "battery", ("return_to_charger", "navigate_to_safe_goal")),
    _definition("low_battery_abort", "battery_resource", "Cancel pending work and return to the simulated charger.", "battery", ("abort_task", "return_to_charger", "navigate_to_safe_goal")),
    _definition("battery_aware_planning", "battery_resource", "Estimate deterministic simulated energy before planning.", "battery", ("checkpoint", "report_status")),
    _definition("charge_then_resume", "battery_resource", "Checkpoint, simulate charging, and restore the task checkpoint.", "battery", ("checkpoint", "return_to_charger", "navigate_to_safe_goal", "charge_simulated", "restore_checkpoint")),
    _definition("energy_efficient_order", "battery_resource", "Greedily order regions by accepted-goal distance when reordering is allowed.", "energy_route", NAV, reorder=True),
    # Human interaction (37-42)
    _definition("confirm_ambiguous_target", "human_interaction", "Request confirmation when an alias matches multiple regions.", "interaction", INTERACTION, required=("target",)),
    _definition("semantic_alias_resolution", "human_interaction", "Resolve a deterministic English or Chinese region alias.", "interaction", INTERACTION, required=("target",)),
    _definition("explain_rejection", "human_interaction", "Return a machine-readable reason and human-readable explanation.", "interaction", INTERACTION, required=("reason_code",)),
    _definition("task_status_report", "human_interaction", "Report deterministic simulated task status.", "interaction", INTERACTION),
    _definition("cancel_current_task", "human_interaction", "Apply existing cancellation semantics to the active simulated task.", "interaction", ("abort_task", "report_status")),
    _definition("change_task_priority", "human_interaction", "Change one queued normal task priority without overriding emergency safety priority.", "interaction", INTERACTION, required=("task_id", "new_priority")),
    # Task management (43-50)
    _definition("pause_current_task", "task_management", "Save a checkpoint and pause through metadata only.", "management", ("checkpoint", "report_status")),
    _definition("resume_current_task", "task_management", "Restore a deterministic paused-task checkpoint.", "management", ("restore_checkpoint", "report_status"), required=("checkpoint_id",)),
    _definition("queue_task", "task_management", "Queue a simulated skill with stable priority and FIFO order.", "management", ("report_status",), required=("queued_skill",)),
    _definition("list_queued_tasks", "task_management", "List queued simulated tasks in stable priority/FIFO order.", "management", ("report_status",)),
    _definition("preview_skill_plan", "task_management", "Compile a target skill plan without running any target step.", "management", ALL_ACTION_TYPES, required=("target_skill",)),
    _definition("list_capabilities", "task_management", "Return all catalog capabilities, support metadata, and parameter requirements.", "management", ("report_status",)),
    _definition("explain_skill_plan", "task_management", "Compile and explain every target-plan step and its safety basis.", "management", ALL_ACTION_TYPES, required=("target_skill",)),
    _definition("export_task_trace", "task_management", "Export deterministic logical events without timestamps.", "management", ("report_status",)),
)


if len(_DEFINITIONS) != 50 or len({item.skill_name for item in _DEFINITIONS}) != 50:
    raise RuntimeError("The simulation skill catalog must contain exactly 50 unique capabilities.")
if any(item.category not in CATEGORIES for item in _DEFINITIONS):
    raise RuntimeError("The simulation skill catalog contains an invalid category.")

SKILL_CATALOG = {item.skill_name: item for item in _DEFINITIONS}


def list_skill_definitions(category: str | None = None) -> tuple[SkillDefinition, ...]:
    if category is not None and category not in CATEGORIES:
        raise SkillCatalogError(f"unknown skill category: {category}")
    return tuple(item for item in _DEFINITIONS if category is None or item.category == category)


def get_skill_definition(skill_name: str) -> SkillDefinition:
    if not isinstance(skill_name, str) or skill_name not in SKILL_CATALOG:
        raise SkillCatalogError(f"unknown simulation skill: {skill_name}")
    return SKILL_CATALOG[skill_name]


def validate_skill_parameters(definition: SkillDefinition, parameters: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        raise SkillCatalogError("skill parameters must be an object.")
    allowed = set(definition.required_parameters) | set(definition.optional_parameters) | set(definition.defaults())
    unknown = sorted(set(parameters) - allowed)
    if unknown:
        raise SkillCatalogError(f"unknown parameter(s) for {definition.skill_name}: {', '.join(unknown)}")
    merged = definition.defaults()
    merged.update(parameters)
    missing = [name for name in definition.required_parameters if name not in merged or merged[name] in (None, "")]
    if missing:
        raise SkillCatalogError(f"missing required parameter(s) for {definition.skill_name}: {', '.join(missing)}")
    return merged


def catalog_document(category: str | None = None, supplied_parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    definitions = list_skill_definitions(category)
    return {
        "schema_version": "1.0",
        "demo_only": True,
        "synthetic_semantics": True,
        "ground_truth": False,
        "simulation_only": True,
        "review_only": True,
        "executable": False,
        "capability_count": len(definitions),
        "capabilities": [definition.as_dict(supplied_parameters) for definition in definitions],
    }


def skill_names(definitions: Iterable[SkillDefinition] | None = None) -> tuple[str, ...]:
    return tuple(item.skill_name for item in (definitions or _DEFINITIONS))
