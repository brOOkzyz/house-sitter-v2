"""ROS-independent tests for semantic waypoint grounding."""

import json
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.schemas import make_plan
from house_sitter_core.semantic_waypoints import (
    SemanticWaypointError,
    resolve_semantic_label,
    semantic_label_exists,
)
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_request
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SemanticWaypointTests(unittest.TestCase):
    def setUp(self):
        self.registry_path = PROJECT_ROOT / "config" / "semantic_waypoints.json"
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
            self.registry_path,
        )

    def test_known_hallway_label_is_accepted(self):
        self.assertTrue(semantic_label_exists("hallway", self.registry_path))
        plan = make_plan(
            "visit_hallway",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        verified = self.verifier.verify(plan)
        self.assertEqual(verified["steps"][0]["parameters"]["waypoint"], "hallway")

    def test_unknown_label_is_rejected(self):
        self.assertFalse(semantic_label_exists("garage", self.registry_path))
        with self.assertRaises(SemanticWaypointError):
            resolve_semantic_label("garage", self.registry_path)

    def test_gemini_cannot_introduce_arbitrary_waypoint_labels(self):
        plan = make_plan(
            "visit_new_room",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "attic"}}],
        )
        with self.assertRaises(PlanVerificationError):
            self.verifier.verify(plan)

    def test_semantic_label_resolves_to_simulation_safe_request(self):
        plan = make_plan(
            "visit_hallway",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        verified = self.verifier.verify(plan)
        request = build_sim_nav2_execution_request(verified)
        self.assertEqual(request["semantic_grounding"], "user_labelled_registry")
        self.assertFalse(request["uses_llm_coordinates"])
        self.assertEqual(request["navigation_intents"][0]["semantic_label"], "hallway")
        self.assertEqual(
            request["navigation_intents"][0]["grounding_mode"],
            "simulation_safe_nearby_goal",
        )
        self.assertEqual(
            request["navigation_intents"][0]["execution_target"]["type"],
            "simulation_safe_nearby_goal",
        )
        self.assertFalse(request["navigation_intents"][0]["gemini_provided_coordinates"])

    def test_request_builder_uses_same_custom_registry_as_verifier(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "semantic_waypoints.json"
            registry_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "simulation_only": True,
                        "labels": {
                            "hallway": {
                                "label": "hallway",
                                "description": "Custom hallway registry for verifier test.",
                                "simulation_only": True,
                                "validated": True,
                                "grounding_mode": "simulation_safe_nearby_goal",
                                "execution_target": {
                                    "type": "simulation_safe_nearby_goal",
                                    "script": "scripts/run_sim_nav2_micro_smoke.py",
                                },
                                "explanation": "Custom registry entry.",
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
            plan = make_plan(
                "visit_hallway",
                "gemini_planner",
                [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
            )
            verified = verifier.verify(plan)
            request = build_sim_nav2_execution_request(
                verified,
                semantic_resolver=verifier.semantic_waypoints.resolve,
            )
            self.assertEqual(
                request["navigation_intents"][0]["description"],
                "Custom hallway registry for verifier test.",
            )


if __name__ == "__main__":
    unittest.main()
