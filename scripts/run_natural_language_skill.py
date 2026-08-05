#!/usr/bin/env python3
"""Run the offline natural-language pipeline with opt-in simulation Nav2 only."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.natural_language_pipeline import (  # noqa: E402
    NaturalLanguagePipelineError, render_pipeline_artifacts, run_natural_language_pipeline, write_pipeline_artifacts,
)
from house_sitter_core.nav2_sim_bridge import Nav2SimulationExecutor, NavigationError  # noqa: E402
from house_sitter_core.skill_artifacts import SkillArtifactError, load_skill_inputs  # noqa: E402
from house_sitter_core.skill_planner import SkillPlanningError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline natural-language simulation skill pipeline; never supports a real robot.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--timeout-seconds", type=float)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute-simulation", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.timeout_seconds is not None and (not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0):
            raise NaturalLanguagePipelineError("timeout-seconds must be a finite positive number.")
        regions, goals = load_skill_inputs(args.semantic_regions, args.safe_goals)
        if args.execute_simulation:
            # Parse and plan before importing ROS so rejected input and unsafe
            # artifacts cannot cause even a simulated Nav2 connection.
            request_document, parsed, plan, result = run_natural_language_pipeline(args.text, regions, goals, timeout_seconds=args.timeout_seconds)
            if parsed["status"] != "accepted":
                paths = write_pipeline_artifacts(args.output_dir, render_pipeline_artifacts(request_document, parsed, plan, result))
                print("SIMULATION ONLY")
                print(f"result: {paths['pipeline_result.json']}")
                return 0
            import rclpy  # Delayed so default/dry-run operation remains ROS-free.
            rclpy.init()
            node = rclpy.create_node("natural_language_simulation_skill_pipeline", parameter_overrides=[rclpy.parameter.Parameter("use_sim_time", value=True)])
            try:
                request_document, parsed, plan, result = run_natural_language_pipeline(args.text, regions, goals, executor=Nav2SimulationExecutor(node), execute_simulation=True, timeout_seconds=args.timeout_seconds)
            finally:
                node.destroy_node(); rclpy.shutdown()
        else:
            request_document, parsed, plan, result = run_natural_language_pipeline(args.text, regions, goals, timeout_seconds=args.timeout_seconds)
        paths = write_pipeline_artifacts(args.output_dir, render_pipeline_artifacts(request_document, parsed, plan, result))
        print("SIMULATION ONLY")
        print(f"result: {paths['pipeline_result.json']}")
        return 0
    except (NaturalLanguagePipelineError, SkillArtifactError, SkillPlanningError, NavigationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
