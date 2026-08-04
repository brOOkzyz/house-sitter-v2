"""Behavior tests for the static, non-navigation Gazebo demo world."""

import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from house_sitter_core.gazebo_static_demo import (
    GazeboStaticDemoError,
    generate_world,
    write_demo,
)


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = ROOT / "local_annotations"
SCRIPT = ROOT / "scripts/create_gazebo_static_demo.py"
SUPPORT = ROOT / "tests/test_demo_semantic_map.py"
spec = importlib.util.spec_from_file_location("demo_support", SUPPORT)
demo_support = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(demo_support)


class GazeboStaticDemoTests(unittest.TestCase):
    def artifacts(self):
        case = demo_support.DemoSemanticMapTests()
        metadata = case.metadata()
        return demo_support.demo.create_demo(metadata, case.document(metadata))[:2]

    def test_world_contains_four_labels_goals_and_map_coordinates(self):
        regions, goals = self.artifacts()
        original_regions, original_goals = copy.deepcopy(regions), copy.deepcopy(goals)
        with mock.patch("house_sitter_core.gazebo_static_demo.MODEL_XACRO", Path("/opt/ros/jazzy/share/turtlebot4_description/urdf/standard/turtlebot4.urdf.xacro")):
            world, manifest = generate_world_from_documents(regions, goals)
        root = ET.fromstring(world)
        names = [model.get("name", "") for model in root.findall("./world/model")]
        manifest_goals = {item["label"]: item for item in manifest["goals"]}
        for label in ("living_room", "kitchen", "bedroom", "charging_area"):
            self.assertTrue(any(label in name for name in names))
            goal = goals["goals"][[g["canonical_label"] for g in goals["goals"]].index(label)]
            matching = [m for m in root.findall("./world/model") if m.get("name") == f"synthetic_goal_{label}_{goal['goal_order']:02d}"]
            self.assertEqual(len(matching), 1)
            pose = matching[0].findtext("pose").split()
            visual = manifest_goals[label]["visual_gazebo_coordinates"]
            self.assertAlmostEqual(float(pose[0]), visual["x"], places=8)
            self.assertAlmostEqual(float(pose[1]), visual["y"], places=8)
            self.assertEqual(manifest_goals[label]["original_map_coordinates"], {"x": goal["goal"]["map_x"], "y": goal["goal"]["map_y"]})
            self.assertLessEqual(abs(visual["x"]), 5.0 + 1e-9)
            self.assertLessEqual(abs(visual["y"]), 5.0 + 1e-9)
        self.assertEqual([item["label"] for item in manifest["goals"]], ["living_room", "kitchen", "bedroom", "charging_area"])
        self.assertTrue(manifest["visualization_only"])
        self.assertGreater(manifest["visualization_scale"], 0)
        self.assertEqual(regions, original_regions)
        self.assertEqual(goals, original_goals)

    def test_transform_is_uniform_and_world_is_static_without_motion_plugins(self):
        regions, goals = self.artifacts()
        world, manifest = generate_world_from_documents(regions, goals)
        scale, ox, oy = manifest["visualization_scale"], manifest["visualization_offset_x"], manifest["visualization_offset_y"]
        for region in manifest["regions"]:
            for original, visual in zip(region["original_map_coordinates"], region["visual_gazebo_coordinates"]):
                self.assertAlmostEqual(visual[0], original[0] * scale + ox)
                self.assertAlmostEqual(visual[1], original[1] * scale + oy)
                self.assertLessEqual(abs(visual[0]), 5.0 + 1e-9)
                self.assertLessEqual(abs(visual[1]), 5.0 + 1e-9)
        for goal in manifest["goals"]:
            original, visual = goal["original_map_coordinates"], goal["visual_gazebo_coordinates"]
            self.assertAlmostEqual(visual["x"], original["x"] * scale + ox)
            self.assertAlmostEqual(visual["y"], original["y"] * scale + oy)
        root = ET.fromstring(world)
        robot = root.find("./world/model[@name='turtlebot4']")
        self.assertIsNotNone(robot)
        self.assertEqual(robot.findtext("static"), "true")
        plugins = [(node.get("name") or "") + " " + (node.get("filename") or "") for node in root.findall(".//plugin")]
        self.assertEqual(plugins, ["gz::sim::systems::SceneBroadcaster gz-sim-scene-broadcaster-system", "3D View GzScene3D"])
        forbidden = ("ros", "nav2", "cmd_vel", "navigate", "dock", "control")
        self.assertFalse(any(word in plugin.lower() for plugin in plugins for word in forbidden))

    def test_goal_and_region_colors_match(self):
        regions, goals = self.artifacts()
        world, manifest = generate_world_from_documents(regions, goals)
        root = ET.fromstring(world)
        for label, color in manifest["colors"].items():
            expected = "%.6f %.6f %.6f %.6f" % tuple(color)
            edge = root.find(f"./world/model[@name='synthetic_region_{label}_edge_001']/link/visual/material/diffuse")
            goal = next(model for model in root.findall("./world/model") if (model.get("name") or "").startswith(f"synthetic_goal_{label}_"))
            self.assertEqual(edge.text, expected)
            self.assertEqual(goal.findtext("link/visual/material/diffuse"), expected)

    def test_malformed_inputs_fail_closed(self):
        regions, goals = self.artifacts()
        for broken in (
            {**regions, "map_identity": {**regions["map_identity"], "width": 999}},
            {**regions, "regions": regions["regions"][:-1]},
        ):
            with self.subTest(broken=broken), self.assertRaises(GazeboStaticDemoError):
                generate_world_from_documents(broken, goals)
        broken_goals = copy.deepcopy(goals)
        broken_goals["goals"][0]["simulation_only"] = False
        with self.assertRaises(GazeboStaticDemoError):
            generate_world_from_documents(regions, broken_goals)

    def test_source_mismatch_duplicate_goal_and_bad_polygon_fail_closed(self):
        regions, goals = self.artifacts()
        broken = copy.deepcopy(goals)
        broken["goals"][0]["candidate_partition_id"] = "wrong"
        with self.assertRaises(GazeboStaticDemoError):
            generate_world_from_documents(regions, broken)
        duplicate = copy.deepcopy(goals)
        duplicate["goals"].append(copy.deepcopy(duplicate["goals"][0]))
        duplicate["accepted_goal_count"] += 1
        with self.assertRaises(GazeboStaticDemoError):
            generate_world_from_documents(regions, duplicate)
        bad = copy.deepcopy(regions)
        bad["regions"][0]["polygon"]["vertices"][1] = bad["regions"][0]["polygon"]["vertices"][0]
        with self.assertRaises(GazeboStaticDemoError):
            generate_world_from_documents(bad, goals)

    def test_atomic_output_failures_leave_no_artifacts(self):
        regions, goals = self.artifacts()
        world, manifest = generate_world_from_documents(regions, goals)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for failure in ("first", "second"):
                output = root / failure
                original = Path.write_text
                calls = []
                def fail(path, data, *args, **kwargs):
                    calls.append(path)
                    if len(calls) == (1 if failure == "first" else 2):
                        raise OSError("injected write failure")
                    return original(path, data, *args, **kwargs)
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail), self.assertRaises(OSError):
                    write_demo(output, world, manifest)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{failure}.tmp-*")), [])
            output = root / "rename"
            with mock.patch("house_sitter_core.gazebo_static_demo.os.replace", side_effect=OSError("injected rename")), self.assertRaises(OSError):
                write_demo(output, world, manifest)
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".rename.tmp-*")), [])

    def test_cli_outputs_are_byte_deterministic_in_independent_processes(self):
        regions, goals = self.artifacts()
        with tempfile.TemporaryDirectory(dir=LOCAL_ROOT) as directory, tempfile.TemporaryDirectory() as inputs:
            root, source = Path(directory), Path(inputs)
            (source / "regions.json").write_text(json.dumps(regions), encoding="utf-8")
            (source / "goals.json").write_text(json.dumps(goals), encoding="utf-8")
            outputs = []
            for seed in ("1", "777"):
                output = root / seed
                run = subprocess.run(["python3", str(SCRIPT), "--semantic-regions", str(source / "regions.json"), "--safe-goals", str(source / "goals.json"), "--output-dir", str(output)], env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append(output)
            for name in ("synthetic_demo.sdf", "gazebo_demo_manifest.json"):
                self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes())


def generate_world_from_documents(regions, goals):
    """Use the public path while keeping tests independent of filesystem inputs."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        regions_path, goals_path = root / "regions.json", root / "goals.json"
        regions_path.write_text(json.dumps(regions), encoding="utf-8")
        goals_path.write_text(json.dumps(goals), encoding="utf-8")
        return generate_world(regions_path, goals_path)


if __name__ == "__main__":
    unittest.main()
