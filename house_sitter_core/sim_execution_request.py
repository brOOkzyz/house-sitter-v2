"""Pure simulation execution request builder for verified LLM plans."""

from __future__ import annotations

from typing import Any, Dict

from .schemas import TaskPlan


def build_sim_nav2_execution_request(verified_plan: TaskPlan) -> Dict[str, Any]:
    """Map a verified plan to a simulation-only execution request.

    Named waypoints from the LLM are treated only as intent. Navigation is
    delegated to the micro-smoke safety layer, which derives a nearby free-space
    candidate from live localization and map data.
    """

    navigation_steps = [
        step
        for step in verified_plan["steps"]
        if step["action"] == "navigate_to_waypoint"
    ]
    return {
        "mode": "simulation_only_nav2_micro_smoke",
        "script": "scripts/run_sim_nav2_micro_smoke.py",
        "requires_navigation": bool(navigation_steps),
        "navigation_intents": [
            {"waypoint": step["parameters"]["waypoint"]} for step in navigation_steps
        ],
        "uses_llm_coordinates": False,
        "uses_direct_cmd_vel": False,
        "requires_verifier": True,
    }
