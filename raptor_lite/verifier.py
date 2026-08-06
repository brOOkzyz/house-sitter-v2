"""Deterministic, explainable verification for declared mock capabilities."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .capability_registry import CapabilityRegistry
from . import issue_codes as codes
from .models import TaskSpec, VerificationIssue, VerificationReport


FAILURE_POLICIES = {"abort", "continue", "stop"}
SUPPORTED_ADAPTERS = {"mock"}


def _issue(code: str, message: str, fix: str, *, step_id: str | None = None, field: str | None = None) -> VerificationIssue:
    return VerificationIssue(issue_code=code, severity="error", step_id=step_id, field=field, message=message, suggested_fix=fix)


def _matches(value: Any, kind: str) -> bool:
    return {
        "string": isinstance(value, str), "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool), "boolean": isinstance(value, bool),
    }.get(kind, False)


def verify_task(task: TaskSpec | dict[str, Any], registry: CapabilityRegistry) -> VerificationReport:
    """Validate all declared task facts without executing any robot interface."""
    try:
        parsed = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
    except ValidationError as exc:
        return VerificationReport(approved=False, issues=[_issue(codes.TASK_SCHEMA_INVALID, "Task schema is invalid.", "Provide all required task fields with the documented JSON types.", field="task")], safety_summary=[str(exc.errors()[0]["msg"])])
    issues: list[VerificationIssue] = []
    resolved: list[str] = []
    seen: set[str] = set()
    physical_requested = bool(parsed.metadata.get("physical_robot_supported", False))
    for step in parsed.steps:
        if step.step_id in seen:
            issues.append(_issue(codes.DUPLICATE_STEP_ID, f"Step id '{step.step_id}' is duplicated.", "Use a unique step_id for every step.", step_id=step.step_id, field="step_id"))
        seen.add(step.step_id)
        capability = registry.get(step.skill)
        if capability is None:
            issues.append(_issue(codes.UNKNOWN_SKILL, f"Skill '{step.skill}' is not declared by the robot profile.", "Choose a skill from the capability profile.", step_id=step.step_id, field="skill"))
            continue
        resolved.append(capability.name)
        if capability.execution_adapter not in SUPPORTED_ADAPTERS:
            issues.append(_issue(codes.UNSUPPORTED_EXECUTION_MODE, f"Execution adapter '{capability.execution_adapter}' is not supported.", "Use the mock adapter in Phase 1.", step_id=step.step_id, field="skill"))
        if physical_requested and not capability.physical_robot_supported:
            issues.append(_issue(codes.UNSUPPORTED_EXECUTION_MODE, f"Skill '{step.skill}' is simulation-only.", "Set metadata.physical_robot_supported to false for this profile.", step_id=step.step_id, field="metadata.physical_robot_supported"))
        if not capability.simulation_supported:
            issues.append(_issue(codes.UNSUPPORTED_EXECUTION_MODE, f"Skill '{step.skill}' does not support simulation.", "Choose a simulation-supported skill.", step_id=step.step_id, field="skill"))
        if step.timeout_seconds is None or step.timeout_seconds <= 0:
            issues.append(_issue(codes.MISSING_TIMEOUT, "Every executable step needs a positive bounded timeout.", "Set timeout_seconds to a positive value.", step_id=step.step_id, field="timeout_seconds"))
        if step.on_failure not in FAILURE_POLICIES:
            issues.append(_issue(codes.INVALID_FAILURE_POLICY, f"Failure policy '{step.on_failure}' is invalid.", "Use abort, continue, or stop.", step_id=step.step_id, field="on_failure"))
        specs = {item.name: item for item in capability.parameters}
        for name, spec in specs.items():
            if spec.required and name not in step.parameters and spec.default is None:
                issues.append(_issue(codes.MISSING_PARAMETER, f"Required parameter '{name}' is missing.", f"Provide '{name}' for skill '{step.skill}'.", step_id=step.step_id, field=f"parameters.{name}"))
        for name, value in step.parameters.items():
            spec = specs.get(name)
            if spec is None:
                issues.append(_issue(codes.UNKNOWN_FIELD, f"Parameter '{name}' is not declared for skill '{step.skill}'.", "Remove it or use a declared parameter.", step_id=step.step_id, field=f"parameters.{name}"))
                continue
            if not _matches(value, spec.type):
                issues.append(_issue(codes.INVALID_PARAMETER_TYPE, f"Parameter '{name}' must be a {spec.type}.", f"Use a JSON {spec.type} value.", step_id=step.step_id, field=f"parameters.{name}"))
                continue
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                if spec.minimum is not None and value < spec.minimum or spec.maximum is not None and value > spec.maximum:
                    issues.append(_issue(codes.PARAMETER_OUT_OF_RANGE, f"Parameter '{name}' is outside its safe range.", f"Use a value between {spec.minimum} and {spec.maximum}.", step_id=step.step_id, field=f"parameters.{name}"))
            if spec.allowed_values is not None and value not in spec.allowed_values:
                issues.append(_issue(codes.PARAMETER_OUT_OF_RANGE, f"Parameter '{name}' is not an allowed value.", f"Use one of: {', '.join(map(str, spec.allowed_values))}.", step_id=step.step_id, field=f"parameters.{name}"))
        missing = [item for item in capability.required_capabilities if item not in registry.available_capabilities]
        if missing:
            issues.append(_issue(codes.UNSUPPORTED_CAPABILITY, f"Skill '{step.skill}' requires unavailable capabilities: {', '.join(missing)}.", "Select a compatible robot profile or remove this skill.", step_id=step.step_id, field="skill"))
    skills = [item.skill for item in parsed.steps]
    patrol = "patrol" in parsed.name.casefold() or "house-sitter" in parsed.description.casefold() or "move_to_room" in skills
    if patrol and "return_to_start" not in skills:
        issues.append(_issue(codes.MISSING_SAFE_RETURN, "A patrol task must include return_to_start.", "Add a bounded return_to_start step before stop.", field="steps"))
    if skills and skills[-1] not in {"stop", "return_to_start"} and not (skills[-1] == "generate_monitoring_report" and "stop" in skills[:-1]):
        issues.append(_issue(codes.MISSING_SAFE_RETURN, "The task must end with stop or return_to_start.", "Add stop or return_to_start as the final step.", field="steps"))
    if "inject_household_events" in skills or "establish_household_baseline" in skills:
        baselines: set[str] = set(); observed_after_events: set[str] = set(); detected: set[str] = set(); twin_updated: set[str] = set()
        events_injected = False; report_index: int | None = None
        for index, step in enumerate(parsed.steps):
            room = step.parameters.get("room")
            observation_dependent = step.skill in {"inspect_room", "establish_household_baseline", "detect_environment_change", "update_digital_twin", "generate_alert"}
            if observation_dependent and step.on_failure == "continue":
                issues.append(_issue(codes.OBSERVATION_FAILURE_POLICY, "Observation-dependent House-Sitter steps cannot continue after failure.", "Use abort or stop to preserve evidence integrity.", step_id=step.step_id, field="on_failure"))
            if step.skill in {"record_baseline", "establish_household_baseline"} and isinstance(room, str): baselines.add(room)
            elif step.skill == "inject_household_events":
                required_rooms = {"living_room", "kitchen", "bedroom", "bathroom"}
                if not required_rooms <= baselines:
                    issues.append(_issue(codes.BASELINE_REQUIRED, "A complete House-Sitter task must baseline all household rooms before event injection.", "Inspect and establish baselines for living_room, kitchen, bedroom, and bathroom first.", step_id=step.step_id, field="steps"))
                events_injected = True; observed_after_events.clear()
            elif step.skill == "inspect_room" and events_injected and isinstance(room, str): observed_after_events.add(room)
            elif step.skill == "detect_environment_change" and isinstance(room, str):
                if room not in baselines:
                    issues.append(_issue(codes.BASELINE_REQUIRED, f"Detection for '{room}' has no prior baseline.", "Establish a baseline observation before detection.", step_id=step.step_id, field="parameters.room"))
                if not events_injected:
                    issues.append(_issue(codes.EVENT_INJECTION_REQUIRED, "Change detection requires the controlled-event stage.", "Add inject_household_events after the baseline patrol.", step_id=step.step_id, field="steps"))
                if room not in observed_after_events:
                    issues.append(_issue(codes.OBSERVATION_REQUIRED, f"Detection for '{room}' requires a post-event inspection.", "Inspect the room after event injection before detection.", step_id=step.step_id, field="parameters.room"))
                detected.add(room)
            elif step.skill == "update_digital_twin" and isinstance(room, str):
                if room not in detected:
                    issues.append(_issue(codes.DETECTION_REQUIRED, f"Digital Twin update for '{room}' has no prior detection step.", "Run detect_environment_change for this room first.", step_id=step.step_id, field="parameters.room"))
                else: twin_updated.add(room)
            elif step.skill == "generate_alert" and isinstance(room, str):
                if room not in detected:
                    issues.append(_issue(codes.DETECTION_REQUIRED, f"Alert for '{room}' has no prior detection step.", "Run detect_environment_change for this room first.", step_id=step.step_id, field="parameters.room"))
                elif room not in twin_updated:
                    issues.append(_issue(codes.TWIN_UPDATE_REQUIRED, f"Alert for '{room}' must follow its Digital Twin update.", "Update the Digital Twin for this room before generating its alert.", step_id=step.step_id, field="parameters.room"))
            if step.skill == "generate_monitoring_report": report_index = index
        if report_index is None:
            issues.append(_issue(codes.REPORT_ORDER_INVALID, "A complete House-Sitter task requires a monitoring report.", "Add generate_monitoring_report after return_to_start and stop.", field="steps"))
        elif any(step.skill != "stop" for step in parsed.steps[report_index + 1:]) or "return_to_start" not in skills[:report_index]:
            issues.append(_issue(codes.REPORT_ORDER_INVALID, "The monitoring report must follow the safe return and be the final task stage.", "Place return_to_start and stop before the final report.", field="steps"))
    return VerificationReport(approved=not issues, issues=issues, resolved_capabilities=sorted(set(resolved)), safety_summary=["simulation_only=true", "execution is denied until verification is approved"])
