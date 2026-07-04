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
    PlannerProviderError,
    VerifiedPlannerAdapter,
    provider_from_env,
)
from house_sitter_core.sim_execution_request import (  # noqa: E402
    build_sim_nav2_execution_request,
)
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier  # noqa: E402


def build_verifier() -> PlanVerifier:
    return PlanVerifier(
        PROJECT_ROOT / "config" / "allowed_actions.json",
        PROJECT_ROOT / "config" / "waypoints.json",
    )


def run_demo(command: str, *, execute_sim: bool = False) -> int:
    print("=== Simulation-only LLM Nav2 demo ===")
    print("No direct /cmd_vel is published.")
    print("LLM output must pass the JSON verifier.")
    print("Navigation maps to the micro-smoke safety layer, not LLM coordinates.")

    try:
        provider = provider_from_env(stream=sys.stdout)
        adapter = VerifiedPlannerAdapter(provider, build_verifier())
        verified_plan = adapter.generate(command)
        request = build_sim_nav2_execution_request(verified_plan)
    except (PlannerProviderError, PlanVerificationError, ValueError) as exc:
        print("\n=== Rejected request ===")
        print(f"Error: {exc}")
        return 1

    print("\n=== Verified JSON plan ===")
    print(json.dumps(verified_plan, indent=2))
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
