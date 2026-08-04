#!/usr/bin/env python3
"""Compile and synchronously run one deterministic simulation-only skill."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.home_simulation_state import HomeSimulationState  # noqa: E402
from house_sitter_core.skill_artifacts import (  # noqa: E402
    SkillArtifactError,
    build_skill_run_from_paths,
    write_skill_artifacts,
)
from house_sitter_core.skill_catalog import SkillCatalogError  # noqa: E402
from house_sitter_core.skill_planner import SkillPlanningError, create_skill_request  # noqa: E402
from house_sitter_core.skill_runtime import SkillRuntimeError  # noqa: E402


def _value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _assignments(values: list[str], name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for specification in values:
        if "=" not in specification:
            raise SkillPlanningError(f"{name} must use KEY=VALUE format: {specification}")
        key, raw = specification.split("=", 1)
        if not key or key != key.strip():
            raise SkillPlanningError(f"{name} key must be a non-empty trimmed string.")
        if key in result:
            raise SkillPlanningError(f"{name} supplied more than once for key: {key}")
        value = _value(raw)
        if name == "param" and key == "checkpoint_id" and raw.strip() in {"[]", "{}", "()", "null", "None", "true", "false", "True", "False", "0", "1", '\"\"'}:
            raise SkillPlanningError("checkpoint_id must be a non-empty trimmed string, not a typed literal.")
        result[key] = value
    return result


def _single_value(values: list[str], name: str) -> tuple[str, ...]:
    if len(values) > 1:
        raise SkillPlanningError(f"{name} contains a duplicate value: option may appear only once.")
    return tuple(values)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a deterministic local smart-home skill; no ROS, Nav2, Gazebo, IoT, or robot command is used.")
    parser.add_argument("--skill", required=True)
    parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path)
    parser.add_argument("--param", action="append", default=[])
    parser.add_argument("--request-id")
    parser.add_argument("--current-region", default="charging_area")
    parser.add_argument("--battery-percent", type=float, default=100.0)
    parser.add_argument("--priority", type=int, default=50)
    parser.add_argument("--restricted-region", action="append", default=[], help="one restricted region at most")
    parser.add_argument("--blocked-goal", action="append", default=[], help="one blocked accepted-goal identifier at most")
    parser.add_argument("--inject-event", action="append", default=[])
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--preview-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        parameters = _assignments(args.param, "param")
        injected_events = _assignments(args.inject_event, "inject-event")
        restricted_regions = _single_value(args.restricted_region, "restricted-region")
        blocked_goals = _single_value(args.blocked_goal, "blocked-goal")
        request = create_skill_request(
            args.skill,
            parameters,
            request_id=args.request_id,
            priority=args.priority,
            battery_percent=args.battery_percent,
            current_region=args.current_region,
            injected_events=injected_events,
        )
        state = HomeSimulationState(
            current_region=args.current_region,
            battery_percent=args.battery_percent,
            restricted_regions=restricted_regions,
            blocked_goals=blocked_goals,
            simulated_alarms=(dict(injected_events),) if "alarm_region" in injected_events else (),
        )
        plan, result, _, contents = build_skill_run_from_paths(
            request,
            args.semantic_regions,
            args.safe_goals,
            state=state,
            preview_only=args.preview_only,
        )
        paths = write_skill_artifacts(args.output_dir, contents)
        print("SIMULATION ONLY")
        print("REVIEW ONLY")
        print("NOT REAL ROBOT EXECUTION")
        print("NO ROS / NAV2 COMMANDS SENT")
        print("NO PHYSICAL MANIPULATION")
        print(f"skill: {request.skill_name}")
        print(f"planning_status: {plan['planning_status']}")
        print(f"overall_status: {result['overall_status']}")
        print(f"result: {paths['skill_result.json']}")
        return 0
    except (SkillArtifactError, SkillPlanningError, SkillRuntimeError, SkillCatalogError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
