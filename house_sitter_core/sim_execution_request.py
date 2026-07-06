"""Pure simulation execution request builder for verified LLM plans."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .schemas import TaskPlan
from .semantic_waypoints import resolve_semantic_label


def build_sim_nav2_execution_request(
    verified_plan: TaskPlan,
    *,
    semantic_resolver: Optional[Callable[[str], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Map a verified plan to a simulation-only execution request.

    Named waypoints from the LLM are treated only as semantic intent. Labels
    must already exist in the user-labelled semantic registry. Navigation is
    delegated to the micro-smoke safety layer, which derives a nearby free-space
    candidate from live localization and map data.
    """

    resolver = semantic_resolver or resolve_semantic_label
    navigation_steps = [
        step
        for step in verified_plan["steps"]
        if step["action"] == "navigate_to_waypoint"
    ]
    navigation_intents = []
    for step in navigation_steps:
        label = step["parameters"]["waypoint"]
        resolved = resolver(label)
        navigation_intents.append(
            {
                "semantic_label": label,
                "description": resolved["description"],
                "grounding_mode": resolved["grounding_mode"],
                "execution_target": resolved["execution_target"],
                "gemini_provided_coordinates": False,
            }
        )

    return {
        "mode": "simulation_only_nav2_micro_smoke",
        "script": "scripts/run_sim_nav2_micro_smoke.py",
        "requires_navigation": bool(navigation_steps),
        "navigation_intents": navigation_intents,
        "uses_llm_coordinates": False,
        "uses_direct_cmd_vel": False,
        "requires_verifier": True,
        "semantic_grounding": "user_labelled_registry",
    }
