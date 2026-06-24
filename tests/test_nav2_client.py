"""ROS-independent tests for waypoint conversion and verified dispatch."""

import math
import unittest
from pathlib import Path

from house_sitter_core.executor import Nav2WaypointExecutor
from house_sitter_core.nav2_client import WaypointConfigError, WaypointStore
from house_sitter_core.schemas import make_plan
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class FakeNav2Client:
    def __init__(self) -> None:
        self.sent = []

    def send_waypoint_async(self, waypoint):
        self.sent.append(waypoint)
        return "mock-future"


class Nav2ClientTests(unittest.TestCase):
    def setUp(self):
        self.store = WaypointStore(PROJECT_ROOT / "config" / "waypoints.json")
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )

    def test_living_room_converts_to_map_pose(self):
        goal = self.store.goal_spec("living_room")
        self.assertEqual(goal.frame_id, "map")
        self.assertEqual((goal.x, goal.y, goal.yaw), (4.0, 1.5, 1.57))
        self.assertAlmostEqual(goal.orientation_z, math.sin(1.57 / 2.0))
        self.assertAlmostEqual(goal.orientation_w, math.cos(1.57 / 2.0))

    def test_unknown_waypoint_is_rejected(self):
        with self.assertRaises(WaypointConfigError):
            self.store.goal_spec("garage")

    def test_executor_verifies_before_dispatch(self):
        client = FakeNav2Client()
        executor = Nav2WaypointExecutor(self.verifier, client)
        plan = make_plan(
            "visit_hallway",
            "test",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        records = executor.execute(plan)
        self.assertEqual(client.sent, ["hallway"])
        self.assertEqual(records[0]["status"], "goal_requested")

    def test_disallowed_waypoint_never_reaches_client(self):
        client = FakeNav2Client()
        executor = Nav2WaypointExecutor(self.verifier, client)
        plan = make_plan(
            "visit_garage",
            "test",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "garage"}}],
        )
        with self.assertRaises(PlanVerificationError):
            executor.execute(plan)
        self.assertEqual(client.sent, [])

    def test_unsupported_step_prevents_partial_dispatch(self):
        client = FakeNav2Client()
        executor = Nav2WaypointExecutor(self.verifier, client)
        plan = make_plan(
            "mixed_plan",
            "test",
            [
                {
                    "action": "navigate_to_waypoint",
                    "parameters": {"waypoint": "hallway"},
                },
                {"action": "rotate", "parameters": {"angle_degrees": 10.0}},
            ],
        )
        with self.assertRaises(ValueError):
            executor.execute(plan)
        self.assertEqual(client.sent, [])


if __name__ == "__main__":
    unittest.main()
