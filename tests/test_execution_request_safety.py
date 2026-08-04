"""Core API safety tests for simulation execution request construction."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.schemas import make_plan
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_requests
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ExecutionRequestSafetyTests(unittest.TestCase):
    def setUp(self):
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )

    @staticmethod
    def plan(labels):
        return make_plan(
            "navigation_test",
            "test_provider",
            [
                {"action": "navigate_to_waypoint", "parameters": {"waypoint": label}}
                for label in labels
            ],
        )

    def test_builder_requires_explicit_verifier(self):
        with self.assertRaises(TypeError):
            build_sim_nav2_execution_requests(self.plan(["hallway"]))

    def test_plain_dictionary_is_mandatorily_verified(self):
        candidate = self.plan(["hallway"])
        with mock.patch.object(
            self.verifier,
            "verify_with_grounding",
            wraps=self.verifier.verify_with_grounding,
        ) as verify:
            result = build_sim_nav2_execution_requests(candidate, verifier=self.verifier)
        self.assertEqual(verify.call_count, 1)
        self.assertEqual(result.execution_requests[0]["parameters"]["waypoint"], "hallway")

    def test_illegal_action_returns_no_requests(self):
        candidate = self.plan(["hallway"])
        candidate["steps"][0]["action"] = "move_forward"
        requests = []
        with self.assertRaisesRegex(PlanVerificationError, "disallowed action"):
            requests = build_sim_nav2_execution_requests(candidate, verifier=self.verifier)
        self.assertEqual(requests, [])

    def test_unknown_second_label_returns_no_requests(self):
        requests = []
        with self.assertRaisesRegex(PlanVerificationError, "Step 2.*balcony"):
            requests = build_sim_nav2_execution_requests(
                self.plan(["hallway", "balcony"]), verifier=self.verifier
            )
        self.assertEqual(requests, [])

    def test_third_step_failure_cannot_leave_partial_requests(self):
        requests = []
        with self.assertRaisesRegex(PlanVerificationError, "Step 3.*balcony"):
            requests = build_sim_nav2_execution_requests(
                self.plan(["hallway", "kitchen", "balcony"]), verifier=self.verifier
            )
        self.assertEqual(requests, [])

    def test_nested_coordinate_field_is_rejected(self):
        candidate = self.plan(["hallway"])
        candidate["steps"][0]["parameters"]["metadata"] = {"x": 1.0}
        with self.assertRaisesRegex(PlanVerificationError, "forbidden.*x"):
            build_sim_nav2_execution_requests(candidate, verifier=self.verifier)

    def test_waypoint_object_is_rejected(self):
        candidate = self.plan(["hallway"])
        candidate["steps"][0]["parameters"]["waypoint"] = {"label": "hallway"}
        with self.assertRaisesRegex(PlanVerificationError, "waypoint must be a string"):
            build_sim_nav2_execution_requests(candidate, verifier=self.verifier)

    def test_custom_registry_is_reused_without_default_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "semantic_waypoints.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "simulation_only": True,
                        "labels": {
                            "custom_hall": {
                                "label": "custom_hall",
                                "aliases": ["private passage"],
                                "description": "Custom registry-only destination.",
                                "simulation_only": True,
                                "validated": True,
                                "grounding_mode": "simulation_safe_nearby_goal",
                                "mapping_status": "unmapped",
                                "frame_id": None,
                                "geometry": None,
                                "source": None,
                                "execution_target": {
                                    "type": "simulation_safe_nearby_goal",
                                    "script": "scripts/run_sim_nav2_micro_smoke.py",
                                },
                                "explanation": "Test-only semantic target.",
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            verifier = PlanVerifier(
                PROJECT_ROOT / "config" / "allowed_actions.json",
                PROJECT_ROOT / "config" / "waypoints.json",
                registry_path,
            )
            result = build_sim_nav2_execution_requests(
                self.plan(["private passage"]), verifier=verifier
            )
        self.assertEqual(result.execution_requests[0]["parameters"]["waypoint"], "custom_hall")
        self.assertEqual(result.execution_requests[0]["original_input"], "private passage")

    def test_alias_diagnostics_and_canonical_execution_targets(self):
        result = build_sim_nav2_execution_requests(
            self.plan(["corridor", "charging station"]), verifier=self.verifier
        )
        requests = result.execution_requests
        self.assertEqual(
            [request["original_input"] for request in requests],
            ["corridor", "charging station"],
        )
        self.assertEqual(
            [request["matched_alias"] for request in requests],
            ["corridor", "charging station"],
        )
        self.assertEqual(
            [request["canonical_label"] for request in requests],
            ["hallway", "charging_area"],
        )
        self.assertEqual(
            [request["parameters"]["waypoint"] for request in requests],
            ["hallway", "charging_area"],
        )

    def test_canonical_input_has_no_matched_alias(self):
        request = build_sim_nav2_execution_requests(
            self.plan(["hallway"]), verifier=self.verifier
        ).execution_requests[0]
        self.assertEqual(request["original_input"], "hallway")
        self.assertIsNone(request["matched_alias"])
        self.assertEqual(request["canonical_label"], "hallway")


if __name__ == "__main__":
    unittest.main()
