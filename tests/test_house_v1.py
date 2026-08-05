"""Read-only contract tests for the local primitive-only house_v1 environment."""
from __future__ import annotations

import json
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from house_sitter_core.map_metadata import load_ros_map, map_identity


ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "worlds" / "house_v1.sdf"
MAP = ROOT / "maps" / "house_v1.yaml"
REGIONS = ROOT / "local_annotations" / "house_v1" / "semantic_regions.json"
GOALS = ROOT / "local_annotations" / "house_v1" / "safe_goals.json"


def contains(vertices: list[list[float]], x: float, y: float) -> bool:
    inside = False
    for index, (x1, y1) in enumerate(vertices):
        x2, y2 = vertices[(index + 1) % len(vertices)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


class HouseV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.regions = json.loads(REGIONS.read_text(encoding="utf-8"))
        self.goals = json.loads(GOALS.read_text(encoding="utf-8"))
        self.metadata = load_ros_map(MAP)

    def test_house_v1_files_are_complete_and_world_is_local_primitives_only(self):
        for path in (WORLD, MAP, MAP.with_suffix(".pgm"), REGIONS, GOALS, ROOT / "scripts" / "bringup_house_v1_headless.sh", ROOT / "scripts" / "bringup_house_v1_gui.sh"):
            self.assertTrue(path.is_file(), path)
        source = WORLD.read_text(encoding="utf-8")
        self.assertNotIn("fuel.gazebosim.org", source.casefold())
        self.assertNotIn("<include>", source)
        self.assertEqual(ET.parse(WORLD).getroot().find("world").attrib["name"], "house_v1")
        for name in ("living_room_sofa", "kitchen_counter_south", "bedroom_bed", "bathroom_toilet", "charging_dock_marker"):
            self.assertIn(f'name="{name}"', source)

    def test_occupancy_map_is_readable_and_matches_house_dimensions(self):
        self.assertEqual((self.metadata.image.width, self.metadata.image.height), (240, 200))
        self.assertEqual(self.metadata.resolution, 0.05)
        self.assertEqual(self.metadata.origin, (0.0, 0.0, 0.0))
        self.assertEqual(self.metadata.bounds, (0.0, 0.0, 12.0, 10.0))
        self.assertEqual(map_identity(self.metadata).as_dict(), self.regions["map_identity"])
        self.assertEqual(self.regions["map_identity"], self.goals["map_identity"])

    def test_six_unique_room_semantics_are_real_house_regions(self):
        records = self.regions["regions"]
        labels = [record["canonical_label"] for record in records]
        self.assertEqual(labels, ["living_room", "kitchen", "bedroom", "bathroom", "hallway", "charging_area"])
        self.assertEqual(len(labels), len(set(labels)))
        for record in records:
            vertices = record["polygon"]["vertices"]
            self.assertEqual(record["map_id"], "house_v1")
            self.assertGreaterEqual(len(vertices), 3)
            self.assertGreater(abs(sum(vertices[index][0] * vertices[(index + 1) % len(vertices)][1] - vertices[(index + 1) % len(vertices)][0] * vertices[index][1] for index in range(len(vertices)))), 0.01)

    def test_each_accepted_safe_goal_is_in_its_region_and_free_map_cell(self):
        regions = {record["canonical_label"]: record for record in self.regions["regions"]}
        goals = self.goals["goals"]
        self.assertEqual(len(goals), 6)
        self.assertEqual({goal["proposal_id"] for goal in goals}, {f"house_v1_{label}_safe_goal" for label in regions})
        for goal in goals:
            label, point = goal["canonical_label"], goal["goal"]
            self.assertEqual(goal["status"], "accepted")
            self.assertTrue(contains(regions[label]["polygon"]["vertices"], point["map_x"], point["map_y"]))
            self.assertEqual(self.metadata.image.pixels[point["pixel_row"] * self.metadata.image.width + point["pixel_column"]], 254)
            self.assertGreaterEqual(point["clearance_m"], 0.45)

    def test_map_identity_and_simulation_only_boundaries_are_explicit(self):
        for document in (self.regions, self.goals):
            self.assertEqual(document["map_id"], "house_v1")
            self.assertFalse(document["real_robot_supported"])
            self.assertTrue(document["review_only"])
            self.assertFalse(document["executable"])
        for goal in self.goals["goals"]:
            self.assertEqual(goal["map_id"], "house_v1")
            self.assertTrue(goal["simulation_only"])
            self.assertFalse(goal["real_robot_supported"])

    def test_bringup_scripts_only_request_world_clock_and_turtlebot_basics(self):
        for script in (ROOT / "scripts" / "bringup_house_v1_headless.sh", ROOT / "scripts" / "bringup_house_v1_gui.sh"):
            source = script.read_text(encoding="utf-8")
            self.assertIn("house_v1.sdf", source)
            self.assertIn("turtlebot4_spawn.launch.py", source)
            self.assertIn("world:=house_v1", source)
            self.assertNotIn("model:=lite", source)
            self.assertIn("localization:=false", source)
            self.assertIn("nav2:=false", source)
            self.assertNotIn("topic pub /cmd_vel", source)
            self.assertNotIn("NavigateToPose", source)

    def test_final_presentation_is_gazebo_independent_and_does_not_fabricate_execution(self):
        documentation = (ROOT / "docs" / "house_v1.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("二维", documentation)
        self.assertIn("静态预览", documentation)
        self.assertIn("不伪造", documentation)
        self.assertIn("house_v1 final residence presentation", readme)
        self.assertIn("static layout preview", readme)
        self.assertIn("exit code 139", documentation)
        self.assertIn("run_simulation_sequence.py", documentation)
        self.assertEqual(list((ROOT / "local_annotations" / "house_v1").glob("execution_*.json")), [])


if __name__ == "__main__":
    unittest.main()
