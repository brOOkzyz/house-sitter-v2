"""ROS-independent tests for atomic multi-step semantic navigation planning."""

import io
import json
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.llm_provider import MockPlannerProvider, PlannerProvider, VerifiedPlannerAdapter
from house_sitter_core.schemas import make_plan
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_requests
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier
from scripts.run_llm_demo import run_demo


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticProvider(PlannerProvider):
    def __init__(self, plan):
        self.plan = plan

    def generate_json(self, prompt: str) -> str:
        return json.dumps(self.plan)


class RecordingExecutor:
    def __init__(self):
        self.plans = []

    def execute(self, plan):
        self.plans.append(plan)
        return []


class MultiStepPlanningTests(unittest.TestCase):
    def setUp(self):
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )

    def plan(self, waypoints):
        return make_plan(
            "multi_step",
            "test_provider",
            [
                {"action": "navigate_to_waypoint", "parameters": {"waypoint": value}}
                for value in waypoints
            ],
        )

    def verified_mock(self, command):
        return VerifiedPlannerAdapter(MockPlannerProvider(), self.verifier).generate(command)

    def test_hallway_then_kitchen_builds_two_requests(self):
        verified = self.verified_mock("Go to the hallway, then visit the kitchen")
        result = build_sim_nav2_execution_requests(verified, verifier=self.verifier)
        requests = result.execution_requests
        self.assertEqual([s["parameters"]["waypoint"] for s in verified["steps"]], ["hallway", "kitchen"])
        self.assertEqual([r["parameters"]["waypoint"] for r in requests], ["hallway", "kitchen"])
        self.assertTrue(all(r["verification_result"] == "passed" for r in requests))

    def test_corridor_lounge_charging_station_normalizes_each_step(self):
        verified = self.verified_mock(
            "Go through the corridor, then visit the lounge, and finally return to the charging station"
        )
        self.assertEqual(
            [s["parameters"]["waypoint"] for s in verified["steps"]],
            ["hallway", "living_room", "charging_area"],
        )

    def test_front_door_bedroom_charger_normalizes_each_step(self):
        verified = self.verified_mock("Go to the front door, then bedroom, and finally return to charger")
        self.assertEqual(
            [s["parameters"]["waypoint"] for s in verified["steps"]],
            ["entrance", "bedroom", "charging_area"],
        )

    def test_single_step_remains_compatible(self):
        verified = self.verified_mock("visit the hallway")
        self.assertEqual(len(verified["steps"]), 1)
        self.assertEqual(verified["steps"][0]["parameters"]["waypoint"], "hallway")

    def test_hallway_then_balcony_rejects_whole_plan(self):
        executor = RecordingExecutor()
        stream = io.StringIO()
        code = run_demo(
            "hallway then balcony",
            provider=MockPlannerProvider(),
            verifier=self.verifier,
            executor=executor,
            stream=stream,
        )
        self.assertEqual(code, 1)
        self.assertIn("Step 2 references unknown semantic waypoint: balcony", stream.getvalue())
        self.assertIn("execution request count: 0", stream.getvalue())
        self.assertEqual(executor.plans, [])

    def test_garage_then_kitchen_fails_on_first_step(self):
        with self.assertRaisesRegex(PlanVerificationError, "Step 1.*garage"):
            self.verifier.verify(self.plan(["garage", "kitchen"]))

    def test_empty_steps_are_rejected(self):
        with self.assertRaisesRegex(PlanVerificationError, "between 1 and 5"):
            self.verifier.verify(self.plan([]))

    def test_six_steps_are_rejected(self):
        with self.assertRaisesRegex(PlanVerificationError, "between 1 and 5"):
            self.verifier.verify(self.plan(["hallway"] * 6))

    def test_second_step_with_x_is_rejected(self):
        plan = self.plan(["hallway", "kitchen"])
        plan["steps"][1]["parameters"]["x"] = 1.0
        with self.assertRaisesRegex(PlanVerificationError, "Step 2.*forbidden.*x"):
            self.verifier.verify(plan)

    def test_third_step_with_y_and_yaw_is_rejected(self):
        plan = self.plan(["hallway", "kitchen", "bedroom"])
        plan["steps"][2]["parameters"].update({"y": 2.0, "yaw": 0.0})
        with self.assertRaisesRegex(PlanVerificationError, "Step 3.*forbidden"):
            self.verifier.verify(plan)

    def test_move_forward_action_is_rejected(self):
        plan = self.plan(["hallway", "kitchen"])
        plan["steps"][1] = {"action": "move_forward", "parameters": {"waypoint": "kitchen"}}
        with mock.patch.object(self.verifier.semantic_waypoints, "resolve") as resolve:
            with self.assertRaisesRegex(PlanVerificationError, "Step 2 uses disallowed action"):
                self.verifier.verify(plan)
        self.assertEqual(resolve.call_count, 0)

    def test_cmd_vel_action_is_rejected(self):
        plan = self.plan(["hallway"])
        plan["steps"][0] = {"action": "cmd_vel", "parameters": {}}
        with self.assertRaisesRegex(PlanVerificationError, "Step 1 uses disallowed action"):
            self.verifier.verify(plan)

    def test_third_step_failure_leaves_no_partial_requests(self):
        executor = RecordingExecutor()
        stream = io.StringIO()
        code = run_demo(
            "hallway then kitchen then balcony",
            provider=MockPlannerProvider(),
            verifier=self.verifier,
            executor=executor,
            stream=stream,
        )
        self.assertEqual(code, 1)
        self.assertIn("failed step index: 3", stream.getvalue())
        self.assertIn("execution request count: 0", stream.getvalue())
        self.assertEqual(executor.plans, [])

    def test_verifier_checks_every_step(self):
        original_resolve = self.verifier.semantic_waypoints.resolve
        with mock.patch.object(
            self.verifier.semantic_waypoints, "resolve", wraps=original_resolve
        ) as resolve:
            verified = self.verifier.verify(self.plan(["hallway", "kitchen", "bedroom"]))
        self.assertEqual(resolve.call_count, 3)
        self.assertEqual(len(verified["steps"]), 3)

    def test_execution_requests_contain_only_canonical_waypoints(self):
        result = build_sim_nav2_execution_requests(
            self.plan(["corridor", "lounge", "charger"]), verifier=self.verifier
        )
        requests = result.execution_requests
        self.assertEqual(
            [r["semantic_label"] for r in requests],
            ["hallway", "living_room", "charging_area"],
        )
        self.assertEqual(
            [request["parameters"]["waypoint"] for request in requests],
            ["hallway", "living_room", "charging_area"],
        )


if __name__ == "__main__":
    unittest.main()
