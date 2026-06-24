#!/usr/bin/env python3
"""Run the phase-1 natural-language to verified dry-run pipeline."""

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.executor import DryRunExecutor  # noqa: E402
from house_sitter_core.planner import MockPlanner  # noqa: E402
from house_sitter_core.reporting import build_task_report  # noqa: E402
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="House Sitter v2 phase-1 dry run")
    parser.add_argument("prompt", help="Natural-language house-sitter task")
    args = parser.parse_args()

    planner = MockPlanner()
    verifier = PlanVerifier(
        PROJECT_ROOT / "config" / "allowed_actions.json",
        PROJECT_ROOT / "config" / "waypoints.json",
    )
    executor = DryRunExecutor()

    try:
        generated_plan = planner.generate(args.prompt)
        print("=== Generated plan ===")
        print(json.dumps(generated_plan, indent=2))

        verified_plan = verifier.verify(generated_plan)
        print("\n=== Verified plan ===")
        print(json.dumps(verified_plan, indent=2))

        print("\n=== Dry-run execution ===")
        records = executor.execute(verified_plan)

        print("\n=== Task report ===")
        print(json.dumps(build_task_report(verified_plan["task_name"], records), indent=2))
        print("\nNo ROS 2 commands were sent.")
        return 0
    except (ValueError, PlanVerificationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
