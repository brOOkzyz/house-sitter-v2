"""Mocks/fakes-only tests for the stable offline supervisor demonstration."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.skill_test_support import ROOT, write_artifacts

SCRIPT = ROOT / "scripts" / "run_supervisor_demo.py"
SPEC = importlib.util.spec_from_file_location("supervisor_demo", SCRIPT)
assert SPEC and SPEC.loader
demo_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo_module
SPEC.loader.exec_module(demo_module)


class SupervisorDemoTests(unittest.TestCase):
    def _demo(self, root: Path = ROOT, **kwargs):
        return demo_module.SupervisorDemo(root, interactive=False, **kwargs)

    def test_runs_from_any_directory_and_finds_script_root(self):
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run([sys.executable, str(SCRIPT), "--offline-only", "--non-interactive"], cwd=directory, text=True, capture_output=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"仓库根目录：{ROOT}", run.stdout)
        self.assertNotIn("Traceback", run.stdout + run.stderr)

    def test_artifact_discovery_skips_directories_bad_json_and_uses_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); artifacts = root / "artifacts"; artifacts.mkdir()
            regions, goals = write_artifacts(artifacts)
            regions.rename(artifacts / "semantic_regions.json"); goals.rename(artifacts / "safe_goals.json")
            (artifacts / "broken_semantic_regions.json").write_text("{broken", encoding="utf-8")
            (artifacts / "safe_goal_directory.json").mkdir()
            pairs = demo_module.discover_artifact_pairs(root)
            self.assertTrue(pairs)
            self.assertTrue(all(pair.semantic_regions.is_file() and pair.safe_goals.is_file() for pair in pairs))

    def test_multiple_candidates_are_numbered_and_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); first, second = root / "first", root / "second"; first.mkdir(); second.mkdir()
            r1, g1 = write_artifacts(first); r2, g2 = write_artifacts(second)
            r1.rename(first / "semantic_regions.json"); g1.rename(first / "safe_goals.json")
            r2.rename(second / "semantic_regions.json"); g2.rename(second / "safe_goals.json")
            pairs = demo_module.discover_artifact_pairs(root)
            output = io.StringIO()
            with redirect_stdout(output):
                selected = demo_module.SupervisorDemo(root, interactive=True, input_func=lambda _: "2").choose_pair(pairs)
        self.assertEqual(selected, pairs[1])
        self.assertIn("发现多个", output.getvalue())

    def test_offline_only_never_calls_ros_probe_or_starts_a_process(self):
        demo = self._demo(offline_only=True, text="检查厨房")
        with mock.patch.object(demo, "_ros_probe") as probe:
            self.assertEqual(demo.run(), 0)
        probe.assert_not_called()

    def test_default_offline_main_flow_completes_without_ros(self):
        demo = self._demo(text="检查厨房")
        with mock.patch.object(demo, "_ros_probe") as probe:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(demo.run(), 0)
        probe.assert_not_called()
        self.assertIn("Previously validated Gazebo/Nav2 run", output.getvalue())
        result = json.loads((demo.pipeline_dir / "pipeline_result.json").read_text(encoding="utf-8"))
        self.assertEqual((result["execution_mode"], result["action_goals_sent"]), ("dry_run", 0))

    def test_menu_continue_skip_retry_and_quit(self):
        responses = iter(["r", "s"])
        demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: next(responses))
        calls: list[int] = []
        self.assertFalse(demo.run_step(6, "attach", "English", lambda: calls.append(1) or True, optional=True))
        self.assertEqual(len(calls), 2)
        quit_demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: "q")
        with self.assertRaises(demo_module.DemoExit):
            quit_demo.run_step(0, "test", "English", lambda: True)

    def test_attach_only_never_starts_or_stops_external_processes(self):
        demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: "y")
        with mock.patch.object(demo, "attach_readiness", return_value=(False, ["/navigate_to_pose"])), mock.patch.object(demo_module.subprocess, "Popen") as popen:
            self.assertFalse(demo.step_attach())
        popen.assert_not_called()

    def test_attach_readiness_is_bounded_to_ten_seconds_and_each_probe_to_three(self):
        demo = self._demo()
        calls: list[float] = []
        current = [0.0]
        def monotonic():
            current[0] += 2.9
            return current[0]
        def probe(_command: str, timeout: float = 3.0):
            calls.append(timeout); return True, ""
        with mock.patch.object(demo_module.time, "monotonic", side_effect=monotonic), mock.patch.object(demo, "_ros_probe", side_effect=probe):
            ready, failed = demo.attach_readiness()
        self.assertFalse(ready); self.assertIn("attach-only 总时限", failed)
        self.assertTrue(calls and all(0 < value <= 3.0 for value in calls))

    def test_not_ready_attach_falls_back_without_retry_loop_or_traceback(self):
        demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: "y")
        with mock.patch.object(demo, "attach_readiness", return_value=(False, ["map→odom"])):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertFalse(demo.step_attach())
        self.assertIn("外部仿真环境未就绪，本次跳过实时执行", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_ready_attach_only_allows_pipeline_after_readiness(self):
        responses = iter(["1", "y", "y", "c"])
        demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: next(responses))
        self.assertTrue(demo.step_preflight())
        fake_node = mock.Mock()
        fake_rclpy = mock.Mock(); fake_rclpy.create_node.return_value = fake_node
        fake_parameter = mock.Mock(); fake_parameter.Parameter.return_value = object()
        fake_bridge = mock.Mock(); fake_bridge.Nav2SimulationExecutor.return_value = object()
        with mock.patch.object(demo, "attach_readiness", return_value=(True, [])), mock.patch.dict(sys.modules, {"rclpy": fake_rclpy, "rclpy.parameter": fake_parameter, "house_sitter_core.nav2_sim_bridge": fake_bridge}), mock.patch.object(demo_module, "run_natural_language_pipeline_detailed") as pipeline:
            # The test verifies gating only: no pipeline call occurs before readiness has succeeded.
            pipeline.side_effect = demo_module.DemoError("fake executor unavailable")
            self.assertFalse(demo.step_attach())
        pipeline.assert_called_once()

    def test_external_processes_are_not_stopped(self):
        demo = self._demo()
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("Popen(", source)
        self.assertNotIn("killpg", source)
        self.assertFalse(hasattr(demo, "cleanup"))

    def test_ros_probe_failure_is_chinese_and_has_no_traceback(self):
        demo = self._demo()
        with mock.patch.object(demo_module, "run_capture", side_effect=demo_module.DemoError("模拟超时")):
            ok, message = demo._ros_probe("ros2 node list")
        self.assertFalse(ok); self.assertIn("模拟超时", message); self.assertNotIn("Traceback", message)


if __name__ == "__main__":
    unittest.main()
