"""Validation tests for local user-annotated semantic map areas."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.schemas import make_plan
from house_sitter_core.semantic_waypoints import SemanticWaypointError, SemanticWaypointRegistry
from house_sitter_core.verifier import PlanVerificationError, PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "semantic_areas_test.json"


class SemanticAreaTests(unittest.TestCase):
    def fixture(self):
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def load_fixture(self, config):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "semantic_areas.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            return SemanticWaypointRegistry(path)

    def invalid_geometry(self, vertices):
        config = self.fixture()
        config["labels"]["hallway"]["geometry"]["vertices"] = vertices
        return config

    def mapped_entry(self, config):
        return config["labels"]["hallway"]

    def test_valid_triangle_and_rectangle_load(self):
        registry = SemanticWaypointRegistry(FIXTURE_PATH)
        self.assertEqual(registry.areas["hallway"].geometry.vertices.__len__(), 3)
        self.assertEqual(registry.areas["kitchen"].geometry.vertices.__len__(), 4)

    def test_too_few_polygon_vertices_are_rejected(self):
        with self.assertRaisesRegex(SemanticWaypointError, "three distinct vertices"):
            self.load_fixture(self.invalid_geometry([[0.0, 0.0], [1.0, 0.0]]))

    def test_repeated_points_that_leave_too_few_distinct_vertices_are_rejected(self):
        with self.assertRaisesRegex(SemanticWaypointError, "three distinct vertices"):
            self.load_fixture(self.invalid_geometry([[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]]))

    def test_collinear_and_zero_area_polygons_are_rejected(self):
        with self.assertRaisesRegex(SemanticWaypointError, "area must be greater than zero"):
            self.load_fixture(self.invalid_geometry([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]))

    def test_non_finite_polygon_coordinates_are_rejected(self):
        for value in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SemanticWaypointError, "finite numbers"):
                    self.load_fixture(self.invalid_geometry([[value, 0.0], [1.0, 0.0], [0.0, 1.0]]))

    def test_string_and_boolean_coordinates_are_rejected(self):
        for value in ("1.0", True):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SemanticWaypointError, "finite numbers"):
                    self.load_fixture(self.invalid_geometry([[value, 0.0], [1.0, 0.0], [0.0, 1.0]]))

    def test_unsupported_geometry_type_is_rejected(self):
        config = self.fixture()
        config["labels"]["hallway"]["geometry"]["type"] = "circle"
        with self.assertRaisesRegex(SemanticWaypointError, "geometry.type must be polygon"):
            self.load_fixture(config)

    def test_empty_frame_and_map_id_are_rejected(self):
        config = self.fixture()
        config["labels"]["hallway"]["frame_id"] = ""
        with self.assertRaisesRegex(SemanticWaypointError, "frame_id"):
            self.load_fixture(config)
        config = self.fixture()
        config["labels"]["hallway"]["source"]["map_id"] = ""
        with self.assertRaisesRegex(SemanticWaypointError, "map_id"):
            self.load_fixture(config)

    def test_non_user_annotation_sources_are_rejected(self):
        for source_type in ("llm", "gemini", "planner"):
            with self.subTest(source_type=source_type):
                config = self.fixture()
                config["labels"]["hallway"]["source"]["type"] = source_type
                with self.assertRaisesRegex(SemanticWaypointError, "user_annotation"):
                    self.load_fixture(config)

    def test_annotation_source_requires_exact_schema(self):
        for source in (
            {"map_id": "test_house_map"},
            {"type": "user_annotation"},
            {"type": "user_annotation", "map_id": "test_house_map", "note": "local"},
            {"type": "user_annotation", "map_id": "test_house_map", "metadata": {"x": 1}},
            {"type": "user_annotation", "map_id": "test_house_map", "nested": {"x": 1, "y": 2}},
        ):
            with self.subTest(source=source):
                config = self.fixture()
                self.mapped_entry(config)["source"] = source
                with self.assertRaisesRegex(SemanticWaypointError, "source fields"):
                    self.load_fixture(config)

    def test_annotation_source_type_and_map_id_are_strict(self):
        for source_type in (
            "User_Annotation",
            "USER_ANNOTATION",
            "user_annotation ",
            " user_annotation",
            "user annotation",
            "gemini",
            "planner",
            "llm",
        ):
            with self.subTest(source_type=source_type):
                config = self.fixture()
                self.mapped_entry(config)["source"]["type"] = source_type
                with self.assertRaisesRegex(SemanticWaypointError, "source.type"):
                    self.load_fixture(config)
        for map_id in ("", "   ", " test_house_map", "test_house_map ", True, 1, None):
            with self.subTest(map_id=map_id):
                config = self.fixture()
                self.mapped_entry(config)["source"]["map_id"] = map_id
                with self.assertRaisesRegex(SemanticWaypointError, "source.map_id"):
                    self.load_fixture(config)

    def test_direct_registry_coordinate_fields_are_rejected(self):
        config = self.fixture()
        config["labels"]["hallway"]["x"] = 1.0
        with self.assertRaisesRegex(SemanticWaypointError, "direct coordinate"):
            self.load_fixture(config)

    def test_self_intersecting_polygon_is_rejected(self):
        with self.assertRaisesRegex(SemanticWaypointError, "self-intersect"):
            self.load_fixture(self.invalid_geometry([[0.0, 0.0], [2.0, 2.0], [0.0, 2.0], [2.0, 0.0]]))

    def test_polygon_closing_vertex_is_normalized_once(self):
        config = self.fixture()
        vertices = self.mapped_entry(config)["geometry"]["vertices"]
        vertices.append(vertices[0])
        registry = self.load_fixture(config)
        self.assertEqual(len(registry.areas["hallway"].geometry.vertices), 3)

    def test_duplicate_vertices_and_near_closing_vertex_have_explicit_behavior(self):
        for vertices in (
            [[0.0, 0.0], [2.0, 0.0], [2.0, 0.0], [1.0, 1.0]],
            [[0.0, 0.0], [2.0, 0.0], [1.0, 1.0], [2.0, 0.0]],
            [[0.0, 0.0], [1.0, 0.0], [0.0, 0.0]],
        ):
            with self.subTest(vertices=vertices):
                with self.assertRaisesRegex(SemanticWaypointError, "vertices"):
                    self.load_fixture(self.invalid_geometry(vertices))
        config = self.fixture()
        self.mapped_entry(config)["geometry"]["vertices"] = [
            [0.0, 0.0], [2.0, 0.0], [1.0, 1.0], [0.0, 1e-9]
        ]
        registry = self.load_fixture(config)
        self.assertEqual(len(registry.areas["hallway"].geometry.vertices), 4)

    def test_non_simple_polygon_contacts_and_overlaps_are_rejected(self):
        polygons = {
            "collinear_overlap": [
                [0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0],
                [0.0, 1.0], [3.0, 1.0], [3.0, 0.0], [1.0, 0.0],
            ],
            "adjacent_complete_overlap": [
                [0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0],
                [0.0, 1.0], [3.0, 1.0], [3.0, 0.0], [0.0, 0.0],
            ],
            "t_junction": [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [2.0, 0.0]],
        }
        for name, vertices in polygons.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(SemanticWaypointError, "polygon"):
                    self.load_fixture(self.invalid_geometry(vertices))

    def test_non_adjacent_endpoint_touch_is_rejected_as_a_repeated_vertex(self):
        # An endpoint contact between non-adjacent edges necessarily repeats a vertex.
        vertices = [[0.0, 0.0], [3.0, 0.0], [3.0, 3.0], [0.0, 3.0], [0.0, 1.0], [3.0, 0.0]]
        with self.assertRaisesRegex(SemanticWaypointError, "vertices must not repeat"):
            self.load_fixture(self.invalid_geometry(vertices))

    def test_concave_and_both_winding_orders_are_accepted(self):
        concave = [[0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [2.0, 2.0], [0.0, 4.0]]
        clockwise = [[0.0, 0.0], [0.0, 2.0], [2.0, 0.0]]
        counter_clockwise = list(reversed(clockwise))
        for vertices in (concave, clockwise, counter_clockwise):
            with self.subTest(vertices=vertices):
                registry = self.load_fixture(self.invalid_geometry(vertices))
                self.assertIsNotNone(registry.areas["hallway"].geometry)

    def test_polygon_area_tolerance_is_explicit(self):
        accepted = [[0.0, 0.0], [2e-6, 0.0], [0.0, 2e-6]]
        rejected = [[0.0, 0.0], [1e-6, 0.0], [0.0, 1e-6]]
        self.assertIsNotNone(self.load_fixture(self.invalid_geometry(accepted)).areas["hallway"].geometry)
        with self.assertRaisesRegex(SemanticWaypointError, "area must be greater than zero"):
            self.load_fixture(self.invalid_geometry(rejected))

    def test_mapped_and_unmapped_state_combinations_are_strict(self):
        mutations = (
            ("mapped_geometry_null", "hallway", "geometry", None),
            ("mapped_source_null", "hallway", "source", None),
            ("mapped_frame_null", "hallway", "frame_id", None),
            ("mapped_frame_empty", "hallway", "frame_id", ""),
            ("mapped_wrong_mode", "hallway", "grounding_mode", "simulation_safe_nearby_goal"),
            ("unmapped_polygon", "charging_area", "geometry", {"type": "polygon", "vertices": [[0, 0], [1, 0], [0, 1]]}),
            ("unmapped_source", "charging_area", "source", {"type": "user_annotation", "map_id": "test_house_map"}),
            ("unmapped_frame", "charging_area", "frame_id", "map"),
            ("unmapped_user_mode", "charging_area", "grounding_mode", "user_labelled_map_area"),
        )
        for name, label, field, value in mutations:
            with self.subTest(name=name):
                config = self.fixture()
                config["labels"][label][field] = value
                with self.assertRaises(SemanticWaypointError):
                    self.load_fixture(config)

    def test_unmapped_placeholder_loads_without_geometry(self):
        registry = SemanticWaypointRegistry(FIXTURE_PATH)
        area = registry.areas["charging_area"]
        self.assertEqual(area.mapping_status, "unmapped")
        self.assertIsNone(area.geometry)

    def test_aliases_and_unknown_labels_remain_safe(self):
        registry = SemanticWaypointRegistry(FIXTURE_PATH)
        self.assertEqual(registry.resolve("corridor")["canonical_label"], "hallway")
        with self.assertRaisesRegex(SemanticWaypointError, "Unknown"):
            registry.resolve("balcony")

    def test_default_production_registry_has_no_polygons_or_fixture_marker(self):
        config = json.loads(
            (PROJECT_ROOT / "config" / "semantic_waypoints.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("test_fixture", config)
        self.assertTrue(all(entry["geometry"] is None for entry in config["labels"].values()))

    def test_fixture_is_not_the_default_registry(self):
        default_registry = SemanticWaypointRegistry()
        self.assertNotIn("test_fixture", default_registry.config)
        self.assertIsNone(default_registry.areas["hallway"].geometry)

    def test_coordinate_like_plan_fields_remain_rejected(self):
        verifier = PlanVerifier(
            PROJECT_ROOT / "config" / "allowed_actions.json",
            PROJECT_ROOT / "config" / "waypoints.json",
        )
        plan = make_plan(
            "unsafe_metadata",
            "test_provider",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway", "x": 1.0}}],
        )
        with self.assertRaisesRegex(PlanVerificationError, "forbidden.*x"):
            verifier.verify(plan)

    def test_inspect_invalid_registry_path_is_concise(self):
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "inspect_semantic_areas.py"),
                "--registry",
                str(PROJECT_ROOT / "tests" / "fixtures" / "does_not_exist.json"),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot load semantic waypoint registry", result.stderr)
        self.assertNotIn("Traceback (most recent call last)", result.stderr)

    def test_inspect_invalid_registry_content_is_concise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            registry_path = Path(tmpdir) / "invalid_registry.json"
            registry_path.write_text("{not valid json", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "inspect_semantic_areas.py"),
                    "--registry",
                    str(registry_path),
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Cannot load semantic waypoint registry", result.stderr)
        self.assertNotIn("Traceback (most recent call last)", result.stderr)


if __name__ == "__main__":
    unittest.main()
