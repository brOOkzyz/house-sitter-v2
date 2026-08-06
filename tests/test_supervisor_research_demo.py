"""Headless tests for the supervisor research-demo orchestration only."""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_supervisor_research_demo.py"
SPEC = importlib.util.spec_from_file_location("supervisor_research_demo", SCRIPT)
assert SPEC and SPEC.loader
demo_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = demo_module
SPEC.loader.exec_module(demo_module)


def args(**overrides):
    values = {"skip_3d": False, "skip_2d": False, "paper_results_dir": None, "output_dir": None,
              "start_at": 0, "non_interactive": False, "list_steps": False, "prepare_paper_results": False}
    values.update(overrides)
    return Namespace(**values)


class SupervisorResearchDemoTests(unittest.TestCase):
    def test_root_and_thirteen_ordered_steps_are_independent_of_cwd(self):
        self.assertEqual(demo_module.locate_repo(SCRIPT), ROOT)
        self.assertEqual(len(demo_module.STEPS), 13)
        self.assertEqual(demo_module.STEPS[0], "Pre-flight Check")
        self.assertEqual(demo_module.STEPS[-1], "Final Summary")

    def test_menu_supports_continue_skip_retry_and_quit(self):
        for response, expected in (("", "c"), ("s", "s"), ("r", "r"), ("q", "q")):
            instance = demo_module.ResearchDemo(args(), input_func=lambda _: response)
            self.assertEqual(instance.menu(optional=True), expected)

    def test_non_interactive_skips_optional_gui_without_launching(self):
        instance = demo_module.ResearchDemo(args(non_interactive=True))
        with mock.patch.object(instance, "launch") as launch:
            self.assertTrue(instance.step2())
            self.assertTrue(instance.step3())
        launch.assert_not_called()

    def test_paper_result_discovery_and_missing_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ("results_summary.json", "results_chapter_draft.md", "limitations_and_threats.md"):
                (path / name).write_text("{}", encoding="utf-8")
            (path / "figures").mkdir()
            self.assertEqual(demo_module.find_paper_results(path), path.resolve())
        fallback = demo_module.find_paper_results(Path("/tmp/not-a-paper-result"))
        self.assertTrue(fallback is None or demo_module.valid_paper_results(fallback))

    def test_twin_diff_only_contains_changed_rooms_and_fields(self):
        before = {"rooms": [{"room_id": "kitchen", "obstacle_count": 0, "anomaly_status": "normal"}, {"room_id": "bedroom", "obstacle_count": 0}]}
        after = {"rooms": [{"room_id": "kitchen", "obstacle_count": 1, "anomaly_status": "anomaly"}, {"room_id": "bedroom", "obstacle_count": 0}]}
        changed = demo_module.twin_changes(before, after)
        self.assertEqual(set(changed), {"kitchen"})
        self.assertEqual(set(changed["kitchen"]), {"anomaly_status", "obstacle_count"})

    def test_monitoring_command_and_artifact_contract_are_fixed(self):
        self.assertEqual(len(demo_module.REQUIRED_MONITORING), 8)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('"--scenario", "kitchen_unexpected_obstacle"', source)
        self.assertIn('"--output-dir", str(self.monitoring_dir)', source)

    def test_list_steps_does_not_construct_or_run_demo(self):
        with mock.patch.object(demo_module, "ResearchDemo") as research:
            self.assertEqual(demo_module.main(["--list-steps"]), 0)
        research.assert_not_called()

    def test_error_log_and_english_text_have_no_template_labels(self):
        instance = demo_module.ResearchDemo(args())
        path = instance.log_failure(4, RuntimeError("test"))
        self.assertTrue(path.is_file())
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("Suggested explanation", "Speaker notes", "Recommended script", "AI-generated explanation", "requests", "urllib", "llm_provider"):
            self.assertNotIn(forbidden, source)

    def test_public_cli_output_is_english_only(self):
        han = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
        for command in ([sys.executable, str(SCRIPT), "--help"], [sys.executable, str(SCRIPT), "--list-steps"]):
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertIsNone(han.search(result.stdout + result.stderr))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo"
            result = subprocess.run([sys.executable, str(SCRIPT), "--non-interactive", "--skip-3d", "--skip-2d",
                                     "--paper-results-dir", "/tmp/house_sitter_paper_results_final", "--output-dir", str(output)],
                                    cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertIsNone(han.search(result.stdout + result.stderr))
            self.assertIn("Patrol order:", result.stdout)
            self.assertIn("Changed room:", result.stdout)
            self.assertIn("Generated artefacts are available at:", result.stdout)

    def test_menu_and_step_labels_are_english(self):
        instance = demo_module.ResearchDemo(args(), input_func=lambda _: "")
        with mock.patch("builtins.print") as printed:
            instance.menu(optional=True)
        self.assertIn("Pre-flight Check", demo_module.STEPS)
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotRegex(source, r"[\u3400-\u4dbf\u4e00-\u9fff]")


if __name__ == "__main__":
    unittest.main()
