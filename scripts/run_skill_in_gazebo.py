#!/usr/bin/env python3
"""Run a compiled skill through Nav2 only when explicitly enabled for simulation."""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.home_simulation_state import HomeSimulationState  # noqa: E402
from house_sitter_core.nav2_sim_bridge import Nav2SimulationExecutor, NavigationError  # noqa: E402
from house_sitter_core.skill_artifacts import load_skill_inputs  # noqa: E402
from house_sitter_core.skill_execution_bridge import (  # noqa: E402
    SkillExecutionBridgeError, execute_skill_in_simulation, render_execution_artifacts, write_execution_artifacts,
)
from house_sitter_core.skill_planner import SkillPlanningError, compile_skill_plan, create_skill_request  # noqa: E402


def _assignments(values: list[str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if "=" not in value:
            raise SkillPlanningError(f"param must use KEY=VALUE format: {value}")
        key, raw = value.split("=", 1)
        if not key or key != key.strip() or key in result:
            raise SkillPlanningError("param keys must be unique non-empty trimmed strings.")
        try:
            result[key] = json.loads(raw)
        except json.JSONDecodeError:
            result[key] = raw
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulation-only Gazebo/Nav2 skill bridge; never supports a real robot.")
    parser.add_argument("--skill", required=True); parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path); parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--output-dir", required=True, type=Path); parser.add_argument("--timeout-seconds", type=float)
    mode = parser.add_mutually_exclusive_group(); mode.add_argument("--dry-run", action="store_true"); mode.add_argument("--execute-simulation", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.timeout_seconds is not None and (not math.isfinite(args.timeout_seconds) or args.timeout_seconds <= 0):
            raise ValueError("timeout-seconds must be a finite positive number.")
        parameters = _assignments(args.param)
        request = create_skill_request(args.skill, parameters)
        regions, goals = load_skill_inputs(args.semantic_regions, args.safe_goals)
        state = HomeSimulationState(current_region=request.current_region, battery_percent=request.simulated_battery_percent)
        plan = compile_skill_plan(request, regions, goals, state)
        executor = None
        if args.execute_simulation:
            # Importing rclpy here keeps default and dry-run modes ROS-free.
            import rclpy
            rclpy.init(); node = rclpy.create_node("simulation_skill_nav2_bridge", parameter_overrides=[rclpy.parameter.Parameter("use_sim_time", value=True)])
            try:
                executor = Nav2SimulationExecutor(node)
                result, events = execute_skill_in_simulation(plan, request, executor, timeout_seconds=args.timeout_seconds, state=state)
            finally:
                node.destroy_node(); rclpy.shutdown()
        else:
            result, events = execute_skill_in_simulation(plan, request, None, timeout_seconds=args.timeout_seconds, dry_run=True, state=state)
        paths = write_execution_artifacts(args.output_dir, render_execution_artifacts(request, plan, result, events))
        print("SIMULATION ONLY\nThis project is simulation-only and does not support real-robot deployment.")
        print(f"result: {paths['execution_result.json']}")
        return 0
    except (SkillPlanningError, SkillExecutionBridgeError, NavigationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
