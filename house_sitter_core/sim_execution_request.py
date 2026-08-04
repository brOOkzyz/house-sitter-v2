"""Pure simulation execution request builders for fully verified plans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from .schemas import TaskPlan
from .verifier import PlanVerifier


@dataclass(frozen=True)
class ExecutionRequestBuildResult:
    """One mandatory verification result and its atomically-built requests."""

    verified_plan: TaskPlan
    grounded_steps: list[Dict[str, Any]]
    execution_requests: list[Dict[str, Any]]

    def to_aggregate_request(self) -> Dict[str, Any]:
        """Present this already-verified bundle through the legacy aggregate shape."""

        navigation_intents = [
            {
                "semantic_label": request["canonical_label"],
                "original_input": request["original_input"],
                "matched_alias": request["matched_alias"],
                "canonical_label": request["canonical_label"],
                "description": request["description"],
                "grounding_mode": request["grounding_mode"],
                "execution_target": request["execution_target"],
                "gemini_provided_coordinates": False,
            }
            for request in self.execution_requests
        ]
        return {
            "mode": "simulation_only_nav2_micro_smoke",
            "script": "scripts/run_sim_nav2_micro_smoke.py",
            "requires_navigation": bool(self.execution_requests),
            "navigation_intents": navigation_intents,
            "execution_requests": self.execution_requests,
            "execution_request_count": len(self.execution_requests),
            "uses_llm_coordinates": False,
            "uses_direct_cmd_vel": False,
            "requires_verifier": True,
            "semantic_grounding": "user_labelled_registry",
        }


def build_sim_nav2_execution_requests(
    plan: TaskPlan,
    *,
    verifier: PlanVerifier,
) -> ExecutionRequestBuildResult:
    """Verify the full candidate plan, then atomically build execution requests."""

    verified = verifier.verify_with_grounding(plan)
    snapshots = verified.grounding_snapshots
    if len(snapshots) != len(verified.plan["steps"]):
        raise ValueError("Every execution step must have a verifier grounding snapshot.")

    requests = [
        {
            "step_index": index,
            "mode": "simulation_only_nav2_micro_smoke",
            "action": "navigate_to_waypoint",
            "parameters": {"waypoint": snapshot["canonical_label"]},
            "semantic_label": snapshot["canonical_label"],
            "original_input": snapshot["original_input"],
            "matched_alias": snapshot["matched_alias"],
            "canonical_label": snapshot["canonical_label"],
            "description": snapshot["description"],
            "grounding_mode": snapshot["grounding_mode"],
            "execution_target": snapshot["execution_target"],
            "verification_result": snapshot["verification_result"],
            "uses_llm_coordinates": False,
            "uses_direct_cmd_vel": False,
        }
        for index, snapshot in enumerate(snapshots, start=1)
    ]
    return ExecutionRequestBuildResult(
        verified_plan=verified.plan,
        grounded_steps=snapshots,
        execution_requests=requests,
    )


def build_sim_nav2_execution_request(
    plan: TaskPlan,
    *,
    verifier: PlanVerifier,
) -> Dict[str, Any]:
    """Backward-compatible aggregate around the atomic per-step requests."""

    return build_sim_nav2_execution_requests(plan, verifier=verifier).to_aggregate_request()
