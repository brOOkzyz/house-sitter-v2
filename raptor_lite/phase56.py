"""System-level capability, verification, repair, and confirmation services."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from . import issue_codes as codes
from .capability_registry import CapabilityRegistry
from .models import PlanningResult, TaskSpec, VerificationReport
from .planner import OfflineHouseSitterPlanner, normalized_task
from .scenario import plan_scenario, verify_scenario
from .verifier import verify_task


def _hash(value: Any) -> str:
    encoded = value if isinstance(value, str) else json.dumps(value, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode("utf-8")).hexdigest()


def explain_verification(task: TaskSpec | dict[str, Any] | None, registry: CapabilityRegistry, report: VerificationReport | None = None) -> dict[str, Any]:
    """Turn verifier facts into user-facing explanations without changing them."""
    report = report or verify_task(task, registry) if task is not None else VerificationReport(approved=False, safety_summary=["No TaskSpec is available."])
    steps = {step.step_id: step for step in task.steps} if isinstance(task, TaskSpec) else {}
    explanations = []
    for issue in report.issues:
        step = steps.get(issue.step_id or "")
        capability = registry.get(step.skill) if step is not None else None
        explanations.append({"issue_code": issue.issue_code, "reason": issue.message, "safe_next_step": issue.suggested_fix, "step_id": issue.step_id, "field": issue.field, "related_capability": {"name": capability.name, "description": capability.description, "safety_constraints": capability.safety_constraints} if capability else None, "safety_rules": capability.safety_constraints if capability else report.safety_summary, "repairable": issue.issue_code in {codes.MISSING_TIMEOUT, codes.INVALID_FAILURE_POLICY, codes.UNKNOWN_FIELD, codes.DUPLICATE_STEP_ID, codes.MISSING_SAFE_RETURN}})
    return {"approved": report.approved, "safety_summary": report.safety_summary, "issues": explanations}


def safe_repair(task: TaskSpec | dict[str, Any], registry: CapabilityRegistry) -> dict[str, Any]:
    """Produce a new TaskSpec only for mechanical, safe repairs and re-verify it."""
    original = task if isinstance(task, TaskSpec) else TaskSpec.model_validate(task)
    before = verify_task(original, registry)
    repaired = original.model_copy(deep=True)
    actions: list[str] = []
    seen: set[str] = set()
    for index, step in enumerate(repaired.steps, 1):
        capability = registry.get(step.skill)
        if step.step_id in seen:
            step.step_id = f"{step.step_id}-repair-{index}"; actions.append(f"made duplicate step id unique: {step.step_id}")
        seen.add(step.step_id)
        if capability is None:
            continue  # Never replace an unsupported skill with a fabricated capability.
        allowed = {parameter.name for parameter in capability.parameters}
        unknown = sorted(set(step.parameters) - allowed)
        if unknown:
            for name in unknown: del step.parameters[name]
            actions.append(f"removed undeclared parameters from {step.step_id}")
        if step.timeout_seconds is None or step.timeout_seconds <= 0:
            step.timeout_seconds = capability.timeout_seconds; actions.append(f"restored bounded timeout for {step.step_id}")
        if step.on_failure not in {"abort", "continue", "stop"}:
            step.on_failure = "abort"; actions.append(f"restored safe failure policy for {step.step_id}")
    skills = [step.skill for step in repaired.steps]
    if repaired.steps and "move_to_room" in skills and "return_to_start" not in skills:
        capability = registry.get("return_to_start")
        if capability is not None:
            step = type(repaired.steps[0])(step_id="repair-return-to-start", skill="return_to_start", timeout_seconds=capability.timeout_seconds, on_failure="abort")
            stop_index = next((index for index, item in enumerate(repaired.steps) if item.skill == "stop"), len(repaired.steps))
            repaired.steps.insert(stop_index, step)
        if "stop" not in skills:
            capability = registry.get("stop")
            if capability is not None:
                repaired.steps.append(type(repaired.steps[0])(step_id="repair-stop", skill="stop", timeout_seconds=capability.timeout_seconds, on_failure="stop"))
        actions.append("added declared safe return and stop steps")
    after = verify_task(repaired, registry)
    unsupported = [issue.issue_code for issue in before.issues if issue.issue_code in {codes.UNKNOWN_SKILL, codes.UNSUPPORTED_CAPABILITY, codes.UNSUPPORTED_EXECUTION_MODE}]
    return {"original_task": original.model_dump(mode="json"), "repaired_task": repaired.model_dump(mode="json"), "actions": actions, "unsupported_capabilities": unsupported, "verification": after.model_dump(mode="json"), "explanation": explain_verification(repaired, registry, after), "approved": after.approved}


def confirmation_preview(task_text: str, scenario_text: str, seed: int, planner: OfflineHouseSitterPlanner, registry: CapabilityRegistry) -> dict[str, Any]:
    """Build the only execution candidate from current inputs and declared constraints."""
    planning: PlanningResult = planner.plan(task_text)
    scenario = plan_scenario(scenario_text, seed)
    scenario_report = verify_scenario(scenario)
    report = verify_task(planning.candidate_task, registry) if planning.candidate_task else VerificationReport(approved=False, safety_summary=["No planned TaskSpec is available."])
    approved = bool(scenario_report["approved"] and report.approved and planning.candidate_task)
    task = planning.candidate_task
    constraints = sorted({constraint for step in task.steps for constraint in (registry.get(step.skill).safety_constraints if registry.get(step.skill) else [])}) if task else []
    snapshot = {"task_text_hash": _hash(task_text), "scenario_text_hash": _hash(scenario_text), "structured_scenario_hash": _hash(scenario["candidate_scenario"]), "task_spec_hash": _hash(normalized_task(task)) if task else None, "seed": seed}
    return {"approved": approved, "snapshot": snapshot, "planning": planning, "scenario": scenario, "scenario_verification": scenario_report, "verification": report, "verification_explanation": explain_verification(task, registry, report), "route": {"visit_order": task.metadata.get("optimized_visit_order", []) if task else [], "cost": task.metadata.get("planned_route_cost") if task else None}, "safety_constraints": constraints}
