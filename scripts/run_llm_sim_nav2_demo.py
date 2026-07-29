#!/usr/bin/env python3
"""Simulation-only LLM plan to Nav2 micro-smoke request demo.

The LLM only generates JSON. The JSON must pass PlanVerifier before any
simulation execution request is created. This script never publishes /cmd_vel
and never uses LLM-provided coordinates.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.llm_provider import (  # noqa: E402
    PlannerProvider,
    PlannerProviderError,
    VerifiedPlannerAdapter,
    provider_from_env,
)
from house_sitter_core.sim_execution_request import (  # noqa: E402
    build_sim_nav2_execution_request,
)
from house_sitter_core.semantic_waypoints import resolve_semantic_label  # noqa: E402
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier  # noqa: E402


class _StaticJsonProvider(PlannerProvider):
    """Adapter helper that replays one JSON string through the verifier."""

    def __init__(self, raw_json: str) -> None:
        self._raw_json = raw_json

    def generate_json(self, prompt: str) -> str:
        return self._raw_json


def build_verifier() -> PlanVerifier:
    return PlanVerifier(
        PROJECT_ROOT / "config" / "allowed_actions.json",
        PROJECT_ROOT / "config" / "waypoints.json",
    )


def _semantic_labels(plan: dict) -> list[str]:
    return [
        step["parameters"]["waypoint"]
        for step in plan["steps"]
        if step["action"] == "navigate_to_waypoint"
    ]


def _print_semantic_grounding_summary(
    raw_plan: dict,
    verified_plan: dict,
    request: dict,
) -> None:
    raw_labels = _semantic_labels(raw_plan)
    verified_labels = _semantic_labels(verified_plan)
    if not raw_labels or not verified_labels or not request["navigation_intents"]:
        return

    original_label = raw_labels[0]
    canonical_label = verified_labels[0]
    resolved = resolve_semantic_label(original_label)
    intent = request["navigation_intents"][0]
    source = verified_plan["source"]

    matched_alias_display = (
        "none (canonical input)"
        if resolved["matched_alias"] == resolved["canonical_label"]
        else resolved["matched_alias"]
    )

    print("\n=== Semantic grounding summary ===")
    print(f"structured intent source: {source}")
    if source == "gemini_planner":
        print("Gemini SDK produced the structured intent.")
    else:
        print(f"Planner source '{source}' produced the structured intent.")
    print(f"original semantic input: {resolved['original_input']}")
    print(f"matched alias: {matched_alias_display}")
    print(f"canonical semantic label: {resolved['canonical_label']}")
    print(f"registry grounding result: {intent['canonical_label']}")
    print(f"execution target: {json.dumps(intent['execution_target'])}")
    print("simulation-only: yes")
    print("navigation execution started: no")
    print("direct /cmd_vel used: no")
    print("semantic grounding was resolved by the registry.")
    print("Gemini did not provide coordinates.")
    print(f"grounding_mode: {resolved['grounding_mode']}")
    print(f"request canonical label: {canonical_label}")


def run_demo(
    command: str,
    *,
    execute_sim: bool = False,
    provider: Optional[PlannerProvider] = None,
    verifier: Optional[PlanVerifier] = None,
) -> int:
    print("=== Simulation-only LLM Nav2 demo ===")
    print("No direct /cmd_vel is published.")
    print("LLM output must pass the JSON verifier.")
    print("Navigation maps to the micro-smoke safety layer, not LLM coordinates.")

    try:
        provider = provider or provider_from_env(stream=sys.stdout)
        verifier = verifier or build_verifier()
        raw_json = provider.generate_json(command)
        raw_plan = json.loads(raw_json)
        adapter = VerifiedPlannerAdapter(_StaticJsonProvider(raw_json), verifier)
        verified_plan = adapter.generate(command)
        request = build_sim_nav2_execution_request(
            verified_plan,
            semantic_resolver=verifier.semantic_waypoints.resolve,
        )
    except (PlannerProviderError, PlanVerificationError, ValueError) as exc:
        print("\n=== Rejected request ===")
        print(f"Error: {exc}")
        return 1

    print("\n=== Verified JSON plan ===")
    print(json.dumps(verified_plan, indent=2))
    _print_semantic_grounding_summary(raw_plan, verified_plan, request)
    print("\n=== Simulation execution request ===")
    print(json.dumps(request, indent=2))

    if not request["requires_navigation"]:
        print("\nNo Nav2 movement is required by this verified plan.")
        return 0

    if not execute_sim:
        print("\nSimulation execution was not started. Pass --execute-sim to run the micro smoke helper.")
        return 0

    print("\n=== Running simulation-only micro smoke helper ===")
    completed = subprocess.run(
        ["timeout", "140", "python3", str(PROJECT_ROOT / request["script"])],
        cwd=PROJECT_ROOT,
        check=False,
    )
    return completed.returncode


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a verified LLM plan and simulation-only Nav2 execution request."
    )
    parser.add_argument("command", help="Natural-language command to plan")
    parser.add_argument(
        "--execute-sim",
        action="store_true",
        help="Run scripts/run_sim_nav2_micro_smoke.py after verifier passes.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return run_demo(args.command, execute_sim=args.execute_sim)


if __name__ == "__main__":
    sys.exit(main())
