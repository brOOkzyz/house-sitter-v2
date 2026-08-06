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
    if skills and skills[-1] not in {"stop", "return_to_start"}:
        issues.append(_issue(codes.MISSING_SAFE_RETURN, "The task must end with stop or return_to_start.", "Add stop or return_to_start as the final step.", field="steps"))
    return VerificationReport(approved=not issues, issues=issues, resolved_capabilities=sorted(set(resolved)), safety_summary=["simulation_only=true", "execution is denied until verification is approved"])
