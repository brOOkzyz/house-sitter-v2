"""Mocks/fakes-only coverage for the interactive supervisor demonstration."""
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

    def test_runs_from_any_working_directory_and_finds_own_root(self):
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run(
                [sys.executable, str(SCRIPT), "--preflight-only", "--non-interactive"],
                cwd=directory, text=True, capture_output=True, check=False,
                env={**os.environ, "PYTHONHASHSEED": "1"},
            )
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn(f"仓库根目录：{ROOT}", run.stdout)
        self.assertNotIn("Traceback", run.stdout + run.stderr)

    def test_artifact_discovery_skips_directories_invalid_json_and_uses_existing_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"; artifacts.mkdir()
            regions, goals = write_artifacts(artifacts)
            (artifacts / "semantic_regions.json").write_text(regions.read_text(encoding="utf-8"), encoding="utf-8")
            (artifacts / "safe_goals.json").write_text(goals.read_text(encoding="utf-8"), encoding="utf-8")
            (artifacts / "broken_semantic_regions.json").write_text("{broken", encoding="utf-8")
            (artifacts / "safe_goal_directory.json").mkdir()
            pairs = demo_module.discover_artifact_pairs(root)
            self.assertTrue(pairs)
            self.assertTrue(all(pair.semantic_regions.is_file() and pair.safe_goals.is_file() for pair in pairs))
            self.assertTrue(any(pair.semantic_regions.name == "semantic_regions.json" for pair in pairs))

    def test_multiple_valid_candidates_are_numbered_and_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); root.mkdir(exist_ok=True)
            one = root / "one"; two = root / "two"; one.mkdir(); two.mkdir()
            r1, g1 = write_artifacts(one); r2, g2 = write_artifacts(two)
            r1.rename(one / "semantic_regions.json"); g1.rename(one / "safe_goals.json")
            r2.rename(two / "semantic_regions.json"); g2.rename(two / "safe_goals.json")
            pairs = demo_module.discover_artifact_pairs(root)
            self.assertGreaterEqual(len(pairs), 2)
            output = io.StringIO()
            interactive = demo_module.SupervisorDemo(root, interactive=True, input_func=lambda _: "2")
            with redirect_stdout(output):
                chosen = interactive.choose_pair(pairs)
            self.assertEqual(chosen, pairs[1])
            self.assertIn("发现多个", output.getvalue())

    def test_ros_missing_still_completes_steps_zero_to_three_and_dry_run_sends_no_goal(self):
        demo = self._demo(text="检查厨房")
        with mock.patch.object(demo, "_check_command", return_value=False):
            output = io.StringIO()
            with redirect_stdout(output):
                code = demo.run(dry_run_only=True)
        self.assertEqual(code, 0)
        self.assertIn("action_goals_sent: 0", output.getvalue())
        result = json.loads((demo.pipeline_dir / "pipeline_result.json").read_text(encoding="utf-8"))
        self.assertEqual((result["execution_mode"], result["action_goals_sent"]), ("dry_run", 0))

    def test_menu_continue_skip_retry_and_exit(self):
        responses = iter(["r", "s"])
        demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: next(responses))
        calls = []
        self.assertFalse(demo.run_step(4, "测试", "English", lambda: calls.append(1) or True, optional=True))
        self.assertEqual(len(calls), 2)
        exit_demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: "q")
        with self.assertRaises(demo_module.DemoExit):
            exit_demo.run_step(0, "测试", "English", lambda: True)
        continue_demo = demo_module.SupervisorDemo(ROOT, interactive=True, input_func=lambda _: "")
        self.assertTrue(continue_demo.run_step(0, "测试", "English", lambda: True))

    def test_subprocess_failure_is_chinese_and_has_no_traceback(self):
        demo = self._demo()
        demo.preflight = {"ros_stack": True, "workspace_setup": True}
        with mock.patch.object(demo, "start_managed", side_effect=demo_module.DemoError("模拟失败")):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertFalse(demo.step_bringup())
        self.assertIn("仿真启动失败", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_ctrl_c_calls_cleanup_and_only_own_process_group_is_terminated(self):
        demo = self._demo()
        own = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        demo.processes.append(demo_module.ManagedProcess("own", own, Path(tempfile.gettempdir()) / "unused.log"))
        try:
            demo.cleanup()
            self.assertIsNotNone(own.poll())
            self.assertIsNone(other.poll())
            interrupt_demo = self._demo()
            with mock.patch.object(interrupt_demo, "run_step", side_effect=KeyboardInterrupt), mock.patch.object(interrupt_demo, "cleanup") as cleaned:
                self.assertEqual(interrupt_demo.run(), 130)
                cleaned.assert_called_once()
        finally:
            if other.poll() is None:
                os.killpg(other.pid, 15)
                other.wait(timeout=5)

    def test_hash_seed_is_deterministic_and_summary_paths_are_reported(self):
        outputs = []
        for seed in ("1", "777"):
            run = subprocess.run(
                [sys.executable, str(SCRIPT), "--dry-run-only", "--non-interactive", "--text", "检查厨房"],
                cwd=ROOT, text=True, capture_output=True, check=False, env={**os.environ, "PYTHONHASHSEED": seed},
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("artifact 总目录：", run.stdout)
            self.assertNotIn("Traceback", run.stdout + run.stderr)
            # Temp paths differ; the structured dry-run itself remains deterministic.
            outputs.append("\n".join(line for line in run.stdout.splitlines() if "/tmp/house-sitter-supervisor-" not in line))
        self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
