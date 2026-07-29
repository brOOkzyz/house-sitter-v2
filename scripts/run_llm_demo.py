#!/usr/bin/env python3
"""Pure software demo for the JSON planner -> verifier -> dry-run pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.executor import DryRunExecutor  # noqa: E402
from house_sitter_core.llm_provider import (  # noqa: E402
    MockPlannerProvider,
    PlannerProvider,
    PlannerProviderError,
    VerifiedPlannerAdapter,
    provider_from_env,
)
from house_sitter_core.reporting import build_task_report  # noqa: E402
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
    output: TextIO,
) -> None:
    raw_labels = _semantic_labels(raw_plan)
    verified_labels = _semantic_labels(verified_plan)
    if not raw_labels or not verified_labels:
        return
    original_label = raw_labels[0]
    canonical_label = verified_labels[0]
    resolved = resolve_semantic_label(original_label)
    source = verified_plan["source"]
    print("\n=== Semantic grounding summary ===", file=output)
    print(f"structured intent source: {source}", file=output)
    if source == "gemini_planner":
        print("Gemini SDK produced the structured intent.", file=output)
    else:
        print(f"Planner source '{source}' produced the structured intent.", file=output)
    print(f"original semantic expression: {resolved['original_input']}", file=output)
    print(f"matched alias: {resolved['matched_alias']}", file=output)
    print(f"canonical semantic label: {resolved['canonical_label']}", file=output)
    print(
        f"{original_label} is resolved from a user-labelled semantic waypoint/area registry.",
        file=output,
    )
    print(
        f"semantic labels remain simulation-only metadata; canonical label is {canonical_label}.",
        file=output,
    )
    print("semantic grounding was resolved by the registry.", file=output)
    print("Nav2 goal was selected by the simulation safety layer.", file=output)
    print("Gemini did not provide coordinates.", file=output)
    print(f"grounding_mode: {resolved['grounding_mode']}", file=output)


def run_demo(
    command: str,
    *,
    provider: Optional[PlannerProvider] = None,
    verifier: Optional[PlanVerifier] = None,
    executor: Optional[DryRunExecutor] = None,
    stream: Optional[TextIO] = None,
) -> int:
    """Run the full demo pipeline without touching ROS or any external API."""

    output = stream or sys.stdout
    provider = provider or provider_from_env(stream=output)
    verifier = verifier or build_verifier()
    executor = executor or DryRunExecutor()

    print("=== User command ===", file=output)
    print(command, file=output)

    try:
        raw_json = provider.generate_json(command)
        print("\n=== Generated JSON plan ===", file=output)
        print(raw_json, file=output)
        raw_plan = json.loads(raw_json)

        adapter = VerifiedPlannerAdapter(_StaticJsonProvider(raw_json), verifier)
        verified_plan = adapter.generate(command)
        print("\n=== Verification result ===", file=output)
        print("passed", file=output)
        print(json.dumps(verified_plan, indent=2), file=output)

        _print_semantic_grounding_summary(raw_plan, verified_plan, output)

        print("\n=== Dry-run execution steps ===", file=output)
        records = executor.execute(verified_plan)

        print("\n=== Final task report ===", file=output)
        print(
            json.dumps(build_task_report(verified_plan["task_name"], records), indent=2),
            file=output,
        )
        print("\nNo ROS 2 commands were sent.", file=output)
        return 0
    except (PlannerProviderError, PlanVerificationError, ValueError) as exc:
        print("\n=== Rejected request ===", file=output)
        print(f"Error: {exc}", file=output)
        return 1


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pure software demo for natural-language planning and dry-run execution."
    )
    parser.add_argument(
        "command",
        help="Natural-language command to plan",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    return run_demo(args.command)


if __name__ == "__main__":
    sys.exit(main())
