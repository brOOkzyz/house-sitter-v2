#!/usr/bin/env python3
"""Pure software multi-step planner -> verifier -> request dry-run pipeline."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.executor import DryRunExecutor  # noqa: E402
from house_sitter_core.llm_provider import (  # noqa: E402
    PlannerProvider,
    PlannerProviderError,
    provider_from_env,
)
from house_sitter_core.reporting import build_task_report  # noqa: E402
from house_sitter_core.sim_execution_request import (  # noqa: E402
    build_sim_nav2_execution_requests,
)
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier  # noqa: E402


def build_verifier() -> PlanVerifier:
    return PlanVerifier(
        PROJECT_ROOT / "config" / "allowed_actions.json",
        PROJECT_ROOT / "config" / "waypoints.json",
    )


def _failed_step_index(reason: str) -> str:
    match = re.search(r"(?:Step|step)\s+(\d+)", reason)
    return match.group(1) if match else "not available"


def _print_step_traces(
    raw_plan: dict,
    verified_plan: dict,
    requests: list[dict],
    output: TextIO,
) -> None:
    for index, (raw_step, verified_step, request) in enumerate(
        zip(raw_plan["steps"], verified_plan["steps"], requests), start=1
    ):
        original = raw_step["parameters"]["waypoint"]
        matched = (
            "none (canonical input)"
            if request["matched_alias"] is None
            else request["matched_alias"]
        )
        print(f"\nStep {index}:", file=output)
        print(f"original semantic expression: {request['original_input']}", file=output)
        print(f"canonical semantic label: {request['canonical_label']}", file=output)
        print(f"- original input: {request['original_input']}", file=output)
        print(f"- matched alias: {matched}", file=output)
        print(f"- canonical label: {verified_step['parameters']['waypoint']}", file=output)
        print(f"- registry grounding: {request['grounding_mode']}", file=output)
        print("- verification result: passed", file=output)
        print(f"{original} is resolved from a user-labelled semantic waypoint/area registry.", file=output)
        print(
            f"- execution target: {json.dumps(request['execution_target'], sort_keys=True)}",
            file=output,
        )


def run_demo(
    command: str,
    *,
    provider: Optional[PlannerProvider] = None,
    verifier: Optional[PlanVerifier] = None,
    executor: Optional[DryRunExecutor] = None,
    stream: Optional[TextIO] = None,
) -> int:
    """Validate the complete plan before creating any simulation request."""

    output = stream or sys.stdout
    provider = provider or provider_from_env(stream=output)
    verifier = verifier or build_verifier()
    executor = executor or DryRunExecutor()
    execution_requests: list[dict] = []

    print("=== User command ===", file=output)
    print("User command:", file=output)
    print(command, file=output)
    try:
        raw_json = provider.generate_json(command)
        print("\n=== Generated JSON plan ===", file=output)
        print(raw_json, file=output)
        raw_plan = json.loads(raw_json)

        result = build_sim_nav2_execution_requests(raw_plan, verifier=verifier)
        verified_plan = result.verified_plan
        execution_requests = result.execution_requests

        print(f"Planner source: {verified_plan['source']}", file=output)
        print(f"structured intent source: {verified_plan['source']}", file=output)
        if verified_plan["source"] == "gemini_planner":
            print("Gemini SDK produced the structured intent.", file=output)
        print(f"Step count: {len(verified_plan['steps'])}", file=output)
        print("Whole-plan verification result: passed", file=output)
        print("\n=== Verification result ===", file=output)
        print("passed", file=output)
        print(json.dumps(verified_plan, indent=2), file=output)
        _print_step_traces(raw_plan, verified_plan, execution_requests, output)

        print("\n=== Dry-run execution steps ===", file=output)
        records = executor.execute(verified_plan)
        print("\n=== Final task report ===", file=output)
        print(
            json.dumps(build_task_report(verified_plan["task_name"], records), indent=2),
            file=output,
        )
        print("\nFinal:", file=output)
        print("- plan accepted: yes", file=output)
        print("- simulation-only: yes", file=output)
        print(f"- execution request count: {len(execution_requests)}", file=output)
        print("- navigation execution started: no", file=output)
        print("- direct /cmd_vel used: no", file=output)
        print("- /navigate_to_pose sent: no", file=output)
        return 0
    except (PlannerProviderError, PlanVerificationError, ValueError) as exc:
        reason = str(exc)
        print("\n=== Rejected request ===", file=output)
        print("- plan accepted: no", file=output)
        print(f"- failed step index: {_failed_step_index(reason)}", file=output)
        print(f"- exact rejection reason: {reason}", file=output)
        print(f"- execution request count: {len(execution_requests)}", file=output)
        print("- navigation execution started: no", file=output)
        print("- direct /cmd_vel used: no", file=output)
        print("- /navigate_to_pose sent: no", file=output)
        return 1


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pure software multi-step semantic navigation planning dry-run."
    )
    parser.add_argument("command", help="Natural-language command to plan")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return run_demo(args.command)


if __name__ == "__main__":
    sys.exit(main())
