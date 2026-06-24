"""ROS-independent safety tests for the single-waypoint real test harness."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from house_sitter_core.executor import DryRunExecutor  # noqa: E402
from house_sitter_core.nav2_client import WaypointConfigError, WaypointStore  # noqa: E402
from house_sitter_core.verifier import PlanVerifier  # noqa: E402
from run_real_waypoint import RealWaypointSafetyError, execute_request, parse_args  # noqa: E402


class RecordingDryExecutor(DryRunExecutor):
    def __init__(self):
        self.plans = []

    def execute(self, verified_plan):
        self.plans.append(verified_plan)
        return [{"status": "dry_run_only"}]


class RealWaypointHarnessTests(unittest.TestCase):
    def setUp(self):
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )
        self.store = WaypointStore(PROJECT_ROOT / "config" / "waypoints.json")

    def test_cli_defaults_to_nearby_test_dry_run(self):
        args = parse_args([])
        self.assertEqual(args.waypoint, "nearby_test")
        self.assertFalse(args.execute_real)

    def test_without_execute_real_never_calls_real_dispatch(self):
        dry_executor = RecordingDryExecutor()
        calls = []
        result = execute_request(
            "nearby_test",
            False,
            self.verifier,
            self.store,
            dry_executor=dry_executor,
            real_dispatch=lambda *args: calls.append(args),
        )
        self.assertEqual(result, [{"status": "dry_run_only"}])
        self.assertEqual(calls, [])
        self.assertEqual(len(dry_executor.plans[0]["steps"]), 1)

    def test_mock_only_true_rejects_real_execution(self):
        calls = []
        with self.assertRaises(WaypointConfigError):
            execute_request(
                "nearby_test",
                True,
                self.verifier,
                self.store,
                real_dispatch=lambda *args: calls.append(args),
            )
        self.assertEqual(calls, [])

    def test_non_nearby_waypoint_is_rejected_for_real_execution(self):
        calls = []
        with self.assertRaises(RealWaypointSafetyError):
            execute_request(
                "hallway",
                True,
                self.verifier,
                SimpleNamespace(mock_only=False),
                real_dispatch=lambda *args: calls.append(args),
            )
        self.assertEqual(calls, [])

    def test_verifier_runs_before_real_dispatch(self):
        events = []

        class RecordingVerifier:
            def verify(self, plan):
                events.append("verify")
                return plan

        def dispatch(plan, verifier, store):
            events.append("dispatch")
            return "result"

        result = execute_request(
            "nearby_test",
            True,
            RecordingVerifier(),
            SimpleNamespace(mock_only=False),
            real_dispatch=dispatch,
        )
        self.assertEqual(result, "result")
        self.assertEqual(events, ["verify", "dispatch"])


if __name__ == "__main__":
    unittest.main()
