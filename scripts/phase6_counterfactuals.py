#!/usr/bin/env python3
"""Read-only Phase 6 Revision 2 counterfactual ablations; never invokes an executor."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.models import TaskSpec
from raptor_lite.phase57 import resource_decision
from raptor_lite.planner import CODE_REQUESTS, SAFETY_BYPASSES, OfflineHouseSitterPlanner, normalize_text
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
RQ1 = ROOT / "configs/raptor_lite/phase6_rq1_cases.json"
RQ3 = ROOT / "configs/raptor_lite/phase6_rq3_seed_manifest.json"


def outcome(decision: str, issues: list[str], expected: str) -> dict[str, Any]:
    return {"would_accept": decision == "accept", "would_reject": decision == "reject", "would_clarify": decision == "clarify", "counterfactual_issues": issues, "ground_truth_correct": decision == expected, "executor_invoked": False}


def full_decision(case: dict[str, Any], planner: OfflineHouseSitterPlanner, registry: CapabilityRegistry) -> dict[str, Any]:
    source = case["task_input"]
    if source["kind"] == "taskspec":
        report = verify_task(source["value"], registry)
        return outcome("accept" if report.approved else "reject", [item.issue_code for item in report.issues], case["expected_decision"])
    plan = planner.plan(source["value"])
    if plan.status == "needs_clarification":
        return outcome("clarify", ["PLANNER_CLARIFICATION"], case["expected_decision"])
    if plan.status != "planned" or plan.candidate_task is None:
        return outcome("reject", plan.unsupported_elements or ["PLANNER_REJECT"], case["expected_decision"])
    report = verify_task(plan.candidate_task, registry)
    return outcome("accept" if report.approved else "reject", [item.issue_code for item in report.issues], case["expected_decision"])


def grounding_ablation_decision(case: dict[str, Any], planner: OfflineHouseSitterPlanner, registry: CapabilityRegistry) -> dict[str, Any]:
    """Remove only declared-capability/room rejection; retain safety and structural checks."""
    source = case["task_input"]
    if source["kind"] == "taskspec":
        report = verify_task(source["value"], registry)
        retained = [item.issue_code for item in report.issues if item.issue_code not in {"UNKNOWN_SKILL", "UNSUPPORTED_CAPABILITY", "UNSUPPORTED_EXECUTION_MODE"}]
        return outcome("accept" if not retained else "reject", retained, case["expected_decision"])
    text = normalize_text(source["value"])
    if any(phrase in text for phrase in (*SAFETY_BYPASSES, *CODE_REQUESTS)):
        return outcome("reject", ["SAFETY_BOUNDARY_RETAINED"], case["expected_decision"])
    plan = planner.plan(source["value"])
    if plan.status == "needs_clarification":
        return outcome("clarify", ["PLANNER_CLARIFICATION"], case["expected_decision"])
    if plan.status == "unsupported" and any(word in text for word in ("inspect", "check", "patrol", "monitor", "camera", "arm", "door", "robot", "garage", "office")):
        return outcome("accept", ["CAPABILITY_GROUNDING_DISABLED"], case["expected_decision"])
    if plan.status != "planned" or plan.candidate_task is None:
        return outcome("reject", plan.unsupported_elements or ["PLANNER_REJECT"], case["expected_decision"])
    report = verify_task(plan.candidate_task, registry)
    retained = [item.issue_code for item in report.issues if item.issue_code not in {"UNKNOWN_SKILL", "UNSUPPORTED_CAPABILITY", "UNSUPPORTED_EXECUTION_MODE"}]
    return outcome("accept" if not retained else "reject", retained, case["expected_decision"])


def verifier_ablation_decision(case: dict[str, Any], planner: OfflineHouseSitterPlanner, registry: CapabilityRegistry) -> dict[str, Any]:
    """Remove verifier checks after parsing while retaining parser safety boundaries."""
    source = case["task_input"]
    if source["kind"] == "taskspec":
        try:
            TaskSpec.model_validate(source["value"])
        except Exception:
            return outcome("reject", ["TASK_SCHEMA_INVALID"], case["expected_decision"])
        return outcome("accept", ["VERIFIER_DISABLED"], case["expected_decision"])
    plan = planner.plan(source["value"])
    if plan.status == "needs_clarification":
        return outcome("clarify", ["PLANNER_CLARIFICATION"], case["expected_decision"])
    if plan.status != "planned" or plan.candidate_task is None:
        return outcome("reject", plan.unsupported_elements or ["PLANNER_REJECT"], case["expected_decision"])
    return outcome("accept", ["VERIFIER_DISABLED"], case["expected_decision"])


def rq1_conditions(case: dict[str, Any], registry: CapabilityRegistry | None = None) -> dict[str, dict[str, Any]]:
    registry = registry or CapabilityRegistry.from_yaml(PROFILE)
    planner = OfflineHouseSitterPlanner(registry)
    return {"full_system": full_decision(case, planner, registry), "capability_grounding_ablation": grounding_ablation_decision(case, planner, registry), "verifier_ablation": verifier_ablation_decision(case, planner, registry)}


def resource_policy_counterfactual(task: dict[str, Any], robot_state: dict[str, Any]) -> dict[str, Any]:
    """No-policy attempt is a counterfactual record, never a real execution request."""
    policy = resource_decision(TaskSpec.model_validate(task), robot_state)
    violates = policy["decision"] != "APPROVE"
    safe_defer = policy["decision"] == "DEFER" and violates
    return {"full_policy_decision": policy["decision"], "would_attempt_execution_without_resource_policy": True, "counterfactual_unsafe_attempt": violates, "safe_defer": safe_defer, "actual_unsafe_execution": "N/A (non-executing counterfactual)", "counterfactual_executor_invoked": False, "resource_policy_unsafe_attempt_prevented_pp_component": int(violates)}


def self_test() -> dict[str, Any]:
    cases = json.loads(RQ1.read_text(encoding="utf-8"))["cases"]
    seeds = json.loads(RQ3.read_text(encoding="utf-8"))
    registry = CapabilityRegistry.from_yaml(PROFILE)
    invalid = next(item for item in cases if item["stratum"] == "invalid")
    unsupported = next(item for item in cases if item["stratum"] == "unsupported")
    unsafe = next(item for item in cases if item["stratum"] == "unsafe")
    invalid_rows, unsupported_rows, unsafe_rows = rq1_conditions(invalid, registry), rq1_conditions(unsupported, registry), rq1_conditions(unsafe, registry)
    assert invalid_rows["full_system"]["would_reject"] and invalid_rows["verifier_ablation"]["would_accept"]
    assert unsupported_rows["full_system"]["would_reject"] and unsupported_rows["capability_grounding_ablation"]["would_accept"]
    assert unsafe_rows["full_system"]["would_reject"] and all(not row["executor_invoked"] for row in unsafe_rows.values())
    all_rows = [rq1_conditions(case, registry) for case in cases]
    assert all(set(row) == {"full_system", "capability_grounding_ablation", "verifier_ablation"} for row in all_rows)
    assert any(row["full_system"] != row["capability_grounding_ablation"] for row in all_rows)
    assert any(row["full_system"] != row["verifier_ablation"] for row in all_rows)
    assert all(not row[condition]["executor_invoked"] for case, row in zip(cases, all_rows) if case["stratum"] == "unsafe" for condition in row)
    paired_seeds = [item["seed"] for item in [*seeds["development"], *seeds["held_out"]]]
    schedules = {condition: list(paired_seeds) for condition in ("full_system", "route_optimization_disabled", "resource_policy_counterfactual")}
    assert len(set(paired_seeds)) == 400 and all(values == paired_seeds for values in schedules.values())
    task = invalid["task_input"]["value"]
    task["steps"][0]["timeout_seconds"] = 30
    counterfactual = resource_policy_counterfactual(task, {"activity": "idle", "battery": 1.0, "room": "charging_area"})
    assert counterfactual["safe_defer"] and counterfactual["counterfactual_unsafe_attempt"] and not counterfactual["counterfactual_executor_invoked"]
    return {"rq1_ablation_decisions_differ": True, "paired_seed_alignment": 400, "unsafe_executor_invocations": 0, "resource_counterfactual_non_executing": True, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only counterfactual checks for Phase 6 Revision 2.")
    parser.add_argument("command", choices=("self-test",))
    args = parser.parse_args()
    print(json.dumps(self_test(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
