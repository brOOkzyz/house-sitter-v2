#!/usr/bin/env python3
"""Safely dry-run, or explicitly execute, one named Nav2 waypoint."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.executor import DryRunExecutor, Nav2WaypointExecutor  # noqa: E402
from house_sitter_core.nav2_client import Nav2ActionClient, WaypointConfigError, WaypointStore  # noqa: E402
from house_sitter_core.schemas import make_plan  # noqa: E402
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier  # noqa: E402

DEFAULT_WAYPOINT = "nearby_test"
REAL_WAYPOINT_ALLOWLIST = frozenset({DEFAULT_WAYPOINT})


class RealWaypointSafetyError(ValueError):
    """Raised when a real-navigation safety gate is not satisfied."""


def build_plan(waypoint: str) -> dict[str, Any]:
    """Build exactly one verifier-compatible waypoint action."""
    return make_plan(
        task_name=f"single_waypoint_{waypoint}",
        source="run_real_waypoint",
        steps=[{"action": "navigate_to_waypoint", "parameters": {"waypoint": waypoint}}],
    )


def execute_request(
    waypoint: str,
    execute_real: bool,
    verifier: PlanVerifier,
    waypoint_store: WaypointStore,
    dry_executor: Optional[DryRunExecutor] = None,
    real_dispatch: Optional[
        Callable[[dict[str, Any], PlanVerifier, WaypointStore], Any]
    ] = None,
) -> Any:
    """Verify one action, then select dry-run or the explicitly enabled real path."""
    verified_plan = verifier.verify(build_plan(waypoint))

    if not execute_real:
        return (dry_executor or DryRunExecutor()).execute(verified_plan)

    if waypoint not in REAL_WAYPOINT_ALLOWLIST:
        raise RealWaypointSafetyError(
            "Real execution is restricted to waypoint 'nearby_test'."
        )
    if waypoint_store.mock_only:
        raise WaypointConfigError(
            "Real execution is disabled because waypoints.json has mock_only=true. "
            "Update nearby_test from the current /amcl_pose before changing this flag."
        )

    return (real_dispatch or dispatch_to_nav2)(
        verified_plan, verifier, waypoint_store
    )


def dispatch_to_nav2(
    verified_plan: dict[str, Any],
    verifier: PlanVerifier,
    waypoint_store: WaypointStore,
) -> Any:
    """Initialize ROS only after all static safety gates have passed."""
    import rclpy

    rclpy.init(args=None)
    node = rclpy.create_node("house_sitter_real_waypoint_test")
    try:
        client = Nav2ActionClient(node, waypoint_store)
        if not client.wait_for_server(timeout_sec=5.0):
            raise RuntimeError("Nav2 /navigate_to_pose action server is not available.")

        # Nav2WaypointExecutor verifies again immediately before dispatch.
        records = Nav2WaypointExecutor(verifier, client).execute(verified_plan)
        goal_future = records[0]["future"]
        rclpy.spin_until_future_complete(node, goal_future)
        goal_handle = goal_future.result()
        if goal_handle is None or not goal_handle.accepted:
            raise RuntimeError("Nav2 rejected the nearby_test goal.")

        result_future = goal_handle.get_result_async()
        rclpy.spin_until_future_complete(node, result_future)
        return result_future.result()
    finally:
        node.destroy_node()
        rclpy.shutdown()


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run one safe named waypoint; real execution is opt-in."
    )
    parser.add_argument(
        "waypoint",
        nargs="?",
        default=DEFAULT_WAYPOINT,
        help="single waypoint name (default: nearby_test)",
    )
    parser.add_argument(
        "--execute-real",
        action="store_true",
        help="explicitly allow dispatch to Nav2 after all safety checks",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    verifier = PlanVerifier(
        PROJECT_ROOT / "config" / "allowed_actions.json",
        PROJECT_ROOT / "config" / "waypoints.json",
    )
    waypoint_store = WaypointStore(PROJECT_ROOT / "config" / "waypoints.json")

    try:
        result = execute_request(
            args.waypoint, args.execute_real, verifier, waypoint_store
        )
        if args.execute_real:
            print(f"Nav2 result: {result}")
        else:
            print("Dry-run only. No Nav2 goal was sent.")
        return 0
    except (
        PlanVerificationError,
        WaypointConfigError,
        RealWaypointSafetyError,
        RuntimeError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
