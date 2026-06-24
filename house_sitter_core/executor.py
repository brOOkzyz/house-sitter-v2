"""Dry-run and explicitly verified execution adapters."""

from typing import Any, Dict, List

from .schemas import TaskPlan
from .verifier import PlanVerifier


class DryRunExecutor:
    def execute(self, verified_plan: TaskPlan) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        for index, step in enumerate(verified_plan["steps"], start=1):
            record = {
                "step": index,
                "action": step["action"],
                "parameters": step["parameters"],
                "status": "dry_run_only",
            }
            records.append(record)
            print(
                f"[{index}] DRY-RUN action={step['action']} "
                f"parameters={step['parameters']}"
            )
        return records


class Nav2WaypointExecutor:
    """Verify a plan, then dispatch only navigate_to_waypoint actions.

    The injected client keeps this class mock-testable. It deliberately has no
    velocity-topic interface, so a planner or LLM cannot publish /cmd_vel.
    """

    def __init__(self, verifier: PlanVerifier, nav2_client: Any) -> None:
        self._verifier = verifier
        self._nav2_client = nav2_client

    def execute(self, plan: Dict[str, Any]) -> List[Dict[str, Any]]:
        verified_plan = self._verifier.verify(plan)
        for step in verified_plan["steps"]:
            if step["action"] != "navigate_to_waypoint":
                raise ValueError(
                    "Nav2WaypointExecutor accepts only navigate_to_waypoint actions."
                )

        records: List[Dict[str, Any]] = []
        for index, step in enumerate(verified_plan["steps"], start=1):
            waypoint = step["parameters"]["waypoint"]
            future = self._nav2_client.send_waypoint_async(waypoint)
            records.append(
                {
                    "step": index,
                    "action": "navigate_to_waypoint",
                    "parameters": {"waypoint": waypoint},
                    "status": "goal_requested",
                    "future": future,
                }
            )
        return records
