"""ROS-independent safety tests for JSON planner providers."""

import io
import json
import unittest
from pathlib import Path

from house_sitter_core.llm_provider import (
    GeminiPlannerProvider,
    MockPlannerProvider,
    PlannerProvider,
    PlannerProviderError,
    RealLLMPlannerProvider,
    VerifiedPlannerAdapter,
    provider_from_env,
)
from house_sitter_core.schemas import make_plan
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_request
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


    def test_gemini_missing_api_key_falls_back_to_mock(self):
        stream = io.StringIO()
        provider = provider_from_env({"LLM_PROVIDER": "gemini"}, stream=stream)
        self.assertIsInstance(provider, MockPlannerProvider)
        self.assertIn("GEMINI_API_KEY is missing", stream.getvalue())

    def test_mock_fallback_generates_verified_plan(self):
        stream = io.StringIO()
        provider = provider_from_env({"LLM_PROVIDER": "gemini"}, stream=stream)
        adapter = VerifiedPlannerAdapter(provider, self.verifier)
        plan = adapter.generate("visit the hallway")
        self.assertEqual(plan["source"], "mock_planner")
        self.assertEqual(plan["steps"][0]["parameters"]["waypoint"], "hallway")

    def test_gemini_malformed_json_is_rejected_before_verifier(self):
        provider = GeminiPlannerProvider(
            api_key="test-key",
            transport=lambda prompt, api_key, model: "not json",
        )
        adapter = VerifiedPlannerAdapter(provider, self.verifier)
        with self.assertRaises(PlannerProviderError):
            adapter.generate("visit the hallway")

    def test_valid_gemini_json_creates_simulation_execution_request(self):
        raw_plan = json.dumps(
            make_plan(
                "visit_hallway",
                "gemini_planner",
                [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
            )
        )
        provider = GeminiPlannerProvider(
            api_key="test-key",
            transport=lambda prompt, api_key, model: raw_plan,
        )
        verified = VerifiedPlannerAdapter(provider, self.verifier).generate("visit hallway")
        request = build_sim_nav2_execution_request(verified)
        self.assertTrue(request["requires_navigation"])
        self.assertEqual(request["script"], "scripts/run_sim_nav2_micro_smoke.py")
        self.assertFalse(request["uses_llm_coordinates"])
        self.assertFalse(request["uses_direct_cmd_vel"])

    def test_gemini_prompt_rejects_coordinates_and_cmd_vel(self):
        seen = {}

        def transport(prompt, api_key, model):
            seen["prompt"] = prompt
            return json.dumps(make_plan("status", "gemini_planner", [
                {"action": "report_status", "parameters": {"detail": "brief"}}
            ]))

        provider = GeminiPlannerProvider(api_key="test-key", transport=transport)
        provider.generate_json("move somewhere")
        self.assertIn("Do not output coordinates", seen["prompt"])
        self.assertIn("Do not output cmd_vel", seen["prompt"])

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
