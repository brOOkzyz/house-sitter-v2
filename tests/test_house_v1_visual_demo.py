"""Headless contract tests for the offline house_v1 visual demonstration."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_house_v1_visual_demo.py"
SPEC = importlib.util.spec_from_file_location("house_v1_visual_demo", SCRIPT)
assert SPEC and SPEC.loader
visual = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = visual
SPEC.loader.exec_module(visual)


class HouseV1VisualDemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.inputs = visual.load_house_v1_inputs(ROOT)

    def test_local_house_map_six_regions_and_accepted_goals_load(self):
        self.assertEqual(self.inputs.metadata.image.width, 240)
        self.assertEqual(self.inputs.metadata.image.height, 200)
        self.assertEqual(set(self.inputs.regions), {"living_room", "kitchen", "bedroom", "bathroom", "hallway", "charging_area"})
        self.assertEqual(set(self.inputs.goals), set(self.inputs.regions))

    def test_kitchen_request_uses_existing_parser_planner_and_kitchen_goal(self):
        demo = visual.build_visual_demo("检查厨房", self.inputs)
        self.assertTrue(demo.accepted)
        self.assertEqual(demo.parsed["selected_capability"], "inspect_area")
        self.assertEqual(demo.planner_plan["planning_status"], "ready")
        self.assertEqual(demo.target_labels, ("kitchen",))
        self.assertEqual(self.inputs.goals["kitchen"]["status"], "accepted")

    def test_astar_route_stays_in_conservatively_free_cells(self):
        demo = visual.build_visual_demo("检查厨房", self.inputs)
        self.assertGreater(len(demo.route_cells), 2)
        self.assertTrue(all(self.inputs.inflated_free_cells[row, column] for row, column in demo.route_cells))
        self.assertEqual(demo.route_cells[-1], visual._nearest_free(self.inputs.inflated_free_cells, visual._goal_cell(self.inputs, "kitchen")))

    def test_animation_trajectory_reaches_the_planned_goal_without_teleporting(self):
        demo = visual.build_visual_demo("检查卧室", self.inputs)
        frames = visual.trajectory_points(demo)
        self.assertGreater(len(frames), 2)
        self.assertEqual(frames[-1], demo.route_points[-1])
        self.assertLessEqual(max(__import__("math").dist(a, b) for a, b in zip(frames, frames[1:])), 0.08)

    def test_patrol_uses_documented_house_v1_residential_order(self):
        demo = visual.build_visual_demo("Patrol the whole house", self.inputs)
        self.assertTrue(demo.accepted)
        self.assertEqual(demo.target_labels, visual.PATROL_ORDER)
        document = visual._visual_plan_document(demo, self.inputs)
        self.assertEqual(document["route_source"], "house_v1_formal_visualization_configuration")

    def test_ambiguous_and_real_robot_requests_do_not_start_animation(self):
        ambiguous = visual.build_visual_demo("检查房间", self.inputs)
        hardware = visual.build_visual_demo("让真实机器人返回充电区", self.inputs)
        self.assertFalse(ambiguous.accepted)
        self.assertEqual(ambiguous.parsed["status"], "needs_clarification")
        self.assertEqual(ambiguous.route_points, ())
        self.assertFalse(hardware.accepted)
        self.assertEqual(hardware.parsed["status"], "unsupported_intent")
        self.assertEqual(hardware.route_points, ())

    def test_artifacts_label_the_output_as_2d_not_gazebo_execution(self):
        demo = visual.build_visual_demo("check kitchen", self.inputs)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-demo"
            paths = visual.write_visual_artifacts(self.inputs, demo, output)
            report = paths["visual_demo_report.md"].read_text(encoding="utf-8")
            result = json.loads(paths["visual_demo_result.json"].read_text(encoding="utf-8"))
            self.assertIn(visual.TITLE, report)
            self.assertIn("not a Gazebo/Nav2 execution artifact", report)
            self.assertEqual(result["action_goals_sent"], 0)
            self.assertFalse(result["gazebo_nav2_execution"])
            self.assertTrue(paths["final_frame.png"].is_file())

    def test_script_has_no_ros_gazebo_or_nav2_command_dependency(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for prohibited in ("subprocess", "ros2 ", "gz ", "NavigateToPose", "cmd_vel"):
            self.assertNotIn(prohibited, source)

    def test_planning_output_is_hash_seed_deterministic(self):
        first = visual.build_visual_demo("检查厨房", self.inputs)
        second = visual.build_visual_demo("检查厨房", self.inputs)
        self.assertEqual(first.target_labels, second.target_labels)
        self.assertEqual(first.route_cells, second.route_cells)
        self.assertEqual(first.route_points, second.route_points)


if __name__ == "__main__":
    unittest.main()
