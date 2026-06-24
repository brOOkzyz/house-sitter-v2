"""ROS-independent safety tests for JSON planner providers."""

import json
import unittest
from pathlib import Path

from house_sitter_core.llm_provider import (
    MockPlannerProvider,
    PlannerProvider,
    PlannerProviderError,
    RealLLMPlannerProvider,
    VerifiedPlannerAdapter,
)
from house_sitter_core.schemas import make_plan
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticProvider(PlannerProvider):
    def __init__(self, output):
        self.output = output

    def generate_json(self, prompt: str) -> str:
        return self.output


class LLMProviderTests(unittest.TestCase):
    def setUp(self):
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )

    def adapter_for_plan(self, plan):
        return VerifiedPlannerAdapter(StaticProvider(json.dumps(plan)), self.verifier)

    def test_mock_provider_generates_verified_json_plan(self):
        adapter = VerifiedPlannerAdapter(MockPlannerProvider(), self.verifier)
        plan = adapter.generate("visit the hallway")
        self.assertEqual(plan["source"], "mock_planner")
        self.assertEqual(plan["steps"][0]["action"], "navigate_to_waypoint")

    def test_real_provider_is_disabled_by_default(self):
        calls = []
        provider = RealLLMPlannerProvider(lambda prompt: calls.append(prompt))
        with self.assertRaises(PlannerProviderError):
            provider.generate_json("visit the hallway")
        self.assertEqual(calls, [])

    def test_non_json_provider_output_is_rejected(self):
        adapter = VerifiedPlannerAdapter(StaticProvider("navigate to hallway"), self.verifier)
        with self.assertRaises(PlannerProviderError):
            adapter.generate("ignored")

    def test_illegal_action_is_rejected(self):
        plan = make_plan(
            "illegal_action",
            "test_provider",
            [{"action": "open_door", "parameters": {}}],
        )
        with self.assertRaises(PlanVerificationError):
            self.adapter_for_plan(plan).generate("ignored")

    def test_unknown_waypoint_is_rejected(self):
        plan = make_plan(
            "unknown_waypoint",
            "test_provider",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "garage"}}],
        )
        with self.assertRaises(PlanVerificationError):
            self.adapter_for_plan(plan).generate("ignored")

    def test_direct_velocity_command_is_rejected(self):
        plan = make_plan(
            "direct_velocity",
            "test_provider",
            [{"action": "cmd_vel", "parameters": {"linear_x": 0.2}}],
        )
        with self.assertRaises(PlanVerificationError):
            self.adapter_for_plan(plan).generate("ignored")


if __name__ == "__main__":
    unittest.main()
