"""ROS-independent tests for semantic waypoint grounding."""

import json
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.schemas import make_plan
from house_sitter_core.semantic_waypoints import (
    SemanticWaypointError,
    SemanticWaypointRegistry,
    resolve_semantic_label,
    semantic_label_exists,
)
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_request
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_CANONICAL_LABELS = (
    "hallway",
    "living_room",
    "kitchen",
    "bedroom",
    "entrance",
    "charging_area",
)
EXPECTED_ALIAS_CASES = {
    "corridor": "hallway",
    "lounge": "living_room",
    "sitting room": "living_room",
    "front door": "entrance",
    "charging station": "charging_area",
    "charger": "charging_area",
}


class SemanticWaypointTests(unittest.TestCase):
    def setUp(self):
        self.registry_path = PROJECT_ROOT / "config" / "semantic_waypoints.json"
        self.verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
            self.registry_path,
        )

    def test_expected_canonical_label_set_is_present(self):
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        labels = registry["labels"]
        for label in EXPECTED_CANONICAL_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, labels)

    def test_expected_canonical_labels_have_consistent_registry_metadata(self):
        registry = json.loads(self.registry_path.read_text(encoding="utf-8"))
        expected_keys = {
            "label",
            "aliases",
            "description",
            "simulation_only",
            "validated",
            "grounding_mode",
            "mapping_status",
            "frame_id",
            "geometry",
            "source",
            "execution_target",
            "explanation",
        }
        for label in EXPECTED_CANONICAL_LABELS:
            with self.subTest(label=label):
                entry = registry["labels"][label]
                self.assertEqual(set(entry), expected_keys)
                self.assertEqual(entry["label"], label)
                self.assertIsInstance(entry["aliases"], list)
                self.assertTrue(entry["simulation_only"])
                self.assertTrue(entry["validated"])
                self.assertEqual(entry["grounding_mode"], "simulation_safe_nearby_goal")
                self.assertEqual(entry["mapping_status"], "unmapped")
                self.assertIsNone(entry["frame_id"])
                self.assertIsNone(entry["geometry"])
                self.assertIsNone(entry["source"])
                self.assertEqual(
                    entry["execution_target"],
                    {
                        "type": "simulation_safe_nearby_goal",
                        "script": "scripts/run_sim_nav2_micro_smoke.py",
                    },
                )
                self.assertNotIn("x", entry)
                self.assertNotIn("y", entry)
                self.assertNotIn("yaw", entry)

    def test_canonical_labels_are_accepted(self):
        for label in EXPECTED_CANONICAL_LABELS:
            with self.subTest(label=label):
                self.assertTrue(semantic_label_exists(label, self.registry_path))
                plan = make_plan(
                    f"visit_{label}",
                    "gemini_planner",
                    [{"action": "navigate_to_waypoint", "parameters": {"waypoint": label}}],
                )
                verified = self.verifier.verify(plan)
                self.assertEqual(verified["steps"][0]["parameters"]["waypoint"], label)

    def test_aliases_resolve_to_canonical_labels(self):
        for alias, canonical in EXPECTED_ALIAS_CASES.items():
            with self.subTest(alias=alias):
                self.assertTrue(semantic_label_exists(alias, self.registry_path))
                resolved = resolve_semantic_label(alias, self.registry_path)
                self.assertEqual(resolved["original_input"], alias)
                self.assertEqual(resolved["matched_alias"], alias)
                self.assertEqual(resolved["canonical_label"], canonical)
                plan = make_plan(
                    f"visit_{canonical}",
                    "gemini_planner",
                    [{"action": "navigate_to_waypoint", "parameters": {"waypoint": alias}}],
                )
                verified = self.verifier.verify(plan)
                self.assertEqual(verified["steps"][0]["parameters"]["waypoint"], canonical)

    def test_alias_normalization_is_case_insensitive_and_separator_insensitive(self):
        resolved = resolve_semantic_label("Front-Door", self.registry_path)
        self.assertEqual(resolved["matched_alias"], "front door")
        self.assertEqual(resolved["canonical_label"], "entrance")
        resolved = resolve_semantic_label("charging_station", self.registry_path)
        self.assertEqual(resolved["matched_alias"], "charging station")
        self.assertEqual(resolved["canonical_label"], "charging_area")

    def test_unknown_label_is_rejected(self):
        for value in ("garage", "balcony", "office"):
            with self.subTest(value=value):
                self.assertFalse(semantic_label_exists(value, self.registry_path))
                with self.assertRaises(SemanticWaypointError):
                    resolve_semantic_label(value, self.registry_path)

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
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "corridor"}}],
        )
        request = build_sim_nav2_execution_request(plan, verifier=self.verifier)
        self.assertEqual(request["semantic_grounding"], "user_labelled_registry")
        self.assertFalse(request["uses_llm_coordinates"])
        self.assertEqual(request["navigation_intents"][0]["semantic_label"], "hallway")
        self.assertEqual(request["navigation_intents"][0]["matched_alias"], "corridor")
        self.assertEqual(request["navigation_intents"][0]["canonical_label"], "hallway")
        self.assertEqual(
            request["navigation_intents"][0]["grounding_mode"],
            "simulation_safe_nearby_goal",
        )
        self.assertEqual(
            request["navigation_intents"][0]["execution_target"]["type"],
            "simulation_safe_nearby_goal",
        )
        self.assertFalse(request["navigation_intents"][0]["gemini_provided_coordinates"])

    def test_registry_rejects_conflicting_aliases(self):
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
                                "aliases": ["shared alias"],
                                "description": "Custom hallway registry for verifier test.",
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
                                "explanation": "Custom registry entry.",
                            },
                            "entrance": {
                                "label": "entrance",
                                "aliases": ["shared alias"],
                                "description": "Custom entrance registry for verifier test.",
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
                                "explanation": "Custom registry entry.",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SemanticWaypointError, "alias conflict"):
                SemanticWaypointRegistry(registry_path)

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
                                "aliases": ["corridor"],
                                "description": "Custom hallway registry for verifier test.",
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
                [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "corridor"}}],
            )
            request = build_sim_nav2_execution_request(plan, verifier=verifier)
            self.assertEqual(
                request["navigation_intents"][0]["description"],
                "Custom hallway registry for verifier test.",
            )


if __name__ == "__main__":
    unittest.main()
