"""ROS-independent safety tests for JSON planner providers."""

import io
import json
from unittest import mock
import unittest
from pathlib import Path

from house_sitter_core.llm_provider import (
    GEMINI_PLAN_JSON_SCHEMA,
    GeminiPlannerProvider,
    MockPlannerProvider,
    PlannerProvider,
    PlannerProviderError,
    RealLLMPlannerProvider,
    VerifiedPlannerAdapter,
    build_structured_planner_prompt,
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


class FakeResponse:
    def __init__(self, parsed=None):
        self.parsed = parsed


class FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.response


class FakeClient:
    def __init__(self, response):
        self.models = FakeModels(response)


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

    def test_mock_provider_normalizes_corridor_to_hallway(self):
        adapter = VerifiedPlannerAdapter(MockPlannerProvider(), self.verifier)
        plan = adapter.generate("go through the corridor")
        self.assertEqual(plan["steps"][0]["action"], "navigate_to_waypoint")
        self.assertEqual(plan["steps"][0]["parameters"], {"waypoint": "hallway"})

    def test_mock_provider_normalizes_lounge_to_living_room(self):
        adapter = VerifiedPlannerAdapter(MockPlannerProvider(), self.verifier)
        plan = adapter.generate("visit the lounge")
        self.assertEqual(plan["steps"][0]["parameters"], {"waypoint": "living_room"})

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

    def test_gemini_provider_requires_sdk_when_no_transport_or_client(self):
        with self.assertRaisesRegex(
            PlannerProviderError,
            "google-genai package is required for Gemini SDK provider",
        ):
            with mock.patch(
                "house_sitter_core.llm_provider._load_google_genai_sdk",
                side_effect=PlannerProviderError(
                    "google-genai package is required for Gemini SDK provider"
                ),
            ):
                GeminiPlannerProvider(api_key="test-key")

    def test_gemini_provider_uses_sdk_structured_output_schema(self):
        response = FakeResponse(
            {
                "schema_version": "1.0",
                "task_name": "visit_hallway",
                "source": "gemini_planner",
                "steps": [
                    {
                        "action": "navigate_to_waypoint",
                        "parameters": {"waypoint": "hallway"},
                    }
                ],
            }
        )
        client = FakeClient(response)
        provider = GeminiPlannerProvider(api_key="test-key", client=client)
        raw = provider.generate_json("visit the hallway")
        plan = json.loads(raw)
        self.assertEqual(plan["source"], "gemini_planner")
        self.assertEqual(plan["steps"][0]["parameters"]["waypoint"], "hallway")
        self.assertEqual(client.models.calls[0]["config"]["response_mime_type"], "application/json")
        self.assertEqual(client.models.calls[0]["config"]["response_json_schema"], GEMINI_PLAN_JSON_SCHEMA)

    def test_gemini_provider_rejects_coordinate_like_fields(self):
        response = FakeResponse(
            {
                "schema_version": "1.0",
                "task_name": "visit_hallway",
                "source": "gemini_planner",
                "steps": [
                    {
                        "action": "navigate_to_waypoint",
                        "parameters": {"waypoint": "hallway", "x": 1.0},
                    }
                ],
            }
        )
        provider = GeminiPlannerProvider(api_key="test-key", client=FakeClient(response))
        with self.assertRaisesRegex(PlannerProviderError, "coordinate-like field"):
            provider.generate_json("visit the hallway")

    def test_gemini_provider_rejects_missing_parsed_output(self):
        provider = GeminiPlannerProvider(api_key="test-key", client=FakeClient(FakeResponse(None)))
        with self.assertRaisesRegex(
            PlannerProviderError,
            "Gemini SDK response did not contain parsed structured output",
        ):
            provider.generate_json("visit the hallway")

    def test_gemini_provider_prompt_rejects_coordinates_and_alias_invention(self):
        prompt = build_structured_planner_prompt("move somewhere")
        self.assertIn("Do not output x, y, yaw, pose, coordinates, cmd_vel", prompt)
        self.assertIn("Gemini does not define aliases or coordinates", prompt)
        self.assertIn("Gemini only outputs structured intent", prompt)
        self.assertIn("user-labelled semantic waypoint/area registry", prompt)

    def test_valid_gemini_json_creates_simulation_execution_request(self):
        response = FakeResponse(
            {
                "schema_version": "1.0",
                "task_name": "visit_hallway",
                "source": "gemini_planner",
                "steps": [
                    {
                        "action": "navigate_to_waypoint",
                        "parameters": {"waypoint": "hallway"},
                    }
                ],
            }
        )
        provider = GeminiPlannerProvider(api_key="test-key", client=FakeClient(response))
        verified = VerifiedPlannerAdapter(provider, self.verifier).generate("visit hallway")
        request = build_sim_nav2_execution_request(verified, verifier=self.verifier)
        self.assertTrue(request["requires_navigation"])
        self.assertEqual(request["script"], "scripts/run_sim_nav2_micro_smoke.py")
        self.assertFalse(request["uses_llm_coordinates"])
        self.assertFalse(request["uses_direct_cmd_vel"])

    def test_verifier_remains_mandatory_for_unknown_semantic_label(self):
        response = FakeResponse(
            {
                "schema_version": "1.0",
                "task_name": "visit_garage",
                "source": "gemini_planner",
                "steps": [
                    {
                        "action": "navigate_to_waypoint",
                        "parameters": {"waypoint": "garage"},
                    }
                ],
            }
        )
        provider = GeminiPlannerProvider(api_key="test-key", client=FakeClient(response))
        with self.assertRaises(PlanVerificationError):
            VerifiedPlannerAdapter(provider, self.verifier).generate("visit the garage")

    def test_verifier_rejects_direct_coordinate_target_even_when_navigation_action_is_used(self):
        plan = make_plan(
            "visit_hallway",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway", "x": 1.0}}],
        )
        with self.assertRaises(PlanVerificationError):
            self.adapter_for_plan(plan).generate("visit the hallway")

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
        for label in ("garage", "balcony", "office"):
            with self.subTest(label=label):
                plan = make_plan(
                    "unknown_waypoint",
                    "test_provider",
                    [{"action": "navigate_to_waypoint", "parameters": {"waypoint": label}}],
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
