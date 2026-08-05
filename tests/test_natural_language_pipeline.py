"""Tests for the thin offline natural-language simulation pipeline."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.natural_language_pipeline import (
    NaturalLanguagePipelineError, render_pipeline_artifacts, run_natural_language_pipeline, write_pipeline_artifacts,
)
from house_sitter_core.nav2_sim_bridge import FakeNavigationExecutor, NavigationOutcome
from tests.skill_test_support import ROOT, demo_artifacts, write_artifacts


SCRIPT = ROOT / "scripts" / "run_natural_language_skill.py"


class NaturalLanguagePipelineTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def test_inspect_and_patrol_compile_to_existing_plans_with_dry_run(self):
        for text, capability in (("检查厨房", "inspect_area"), ("巡逻整个房子", "patrol_home")):
            with self.subTest(text=text):
                request, parsed, plan, result = run_natural_language_pipeline(text, self.regions, self.goals)
                self.assertEqual((parsed["status"], request["skill_name"], plan["skill_name"]), ("accepted", capability, capability))
                self.assertEqual((result["execution_mode"], result["action_goals_sent"], result["final_status"]), ("dry_run", 0, "not_executed"))
                self.assertTrue(plan["steps"])
                report = render_pipeline_artifacts(request, parsed, plan, result)["pipeline_report.md"]
                self.assertIn("simulation_only: true", report); self.assertIn("real_robot_supported: false", report)

    def test_rejected_parse_stops_before_planning_or_execution(self):
        for text, status in (("检查房间", "needs_clarification"), ("打开客厅的灯", "unsupported_intent"), ("让真实机器人返回充电区", "unsupported_intent")):
            with self.subTest(text=text):
                _, parsed, plan, result = run_natural_language_pipeline(text, self.regions, self.goals)
                self.assertEqual((parsed["status"], plan["planning_status"], result["execution_mode"], result["action_goals_sent"]), (status, "not_started", "not_started", 0))
                report = render_pipeline_artifacts({"original_text": text}, parsed, plan, result)["pipeline_report.md"]
                self.assertIn("simulation_only: true", report); self.assertIn("real_robot_supported: false", report)

    def test_explicit_simulation_uses_injected_executor_only(self):
        request, parsed, plan, result = run_natural_language_pipeline(
            "检查厨房", self.regions, self.goals,
            executor=FakeNavigationExecutor([NavigationOutcome("succeeded")]), execute_simulation=True,
        )
        self.assertEqual((parsed["status"], plan["planning_status"], result["execution_mode"], result["action_goals_sent"], result["final_status"]), ("accepted", "ready", "gazebo_nav2_simulation", 1, "succeeded"))
        self.assertEqual(request["simulation_only"], True)

    def test_untrusted_artifacts_and_invalid_execution_fail_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bad_goals = json.loads(json.dumps(self.goals)); bad_goals["goals"][0]["review_only"] = False
            output = root / "output"
            with self.assertRaisesRegex(NaturalLanguagePipelineError, "planner rejected"):
                run_natural_language_pipeline("检查厨房", self.regions, bad_goals)
            self.assertFalse(output.exists())
            request, parsed, plan, result = run_natural_language_pipeline("检查厨房", self.regions, self.goals)
            with mock.patch.object(Path, "write_text", side_effect=OSError("write failure")):
                with self.assertRaisesRegex(OSError, "write failure"):
                    write_pipeline_artifacts(output, render_pipeline_artifacts(request, parsed, plan, result))
            self.assertFalse(output.exists())
            output.mkdir()
            with self.assertRaisesRegex(NaturalLanguagePipelineError, "already exists"):
                write_pipeline_artifacts(output, render_pipeline_artifacts(request, parsed, plan, result))

    def test_cli_errors_default_dry_run_and_hash_seed_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions, goals = write_artifacts(root)
            conflict = subprocess.run([sys.executable, str(SCRIPT), "--text", "检查厨房", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(root / "conflict"), "--dry-run", "--execute-simulation"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(conflict.returncode, 2); self.assertNotIn("Traceback", conflict.stderr)
            rejected_target = root / "rejected"
            rejected = subprocess.run([sys.executable, str(SCRIPT), "--text", "让真实机器人返回充电区", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(rejected_target), "--execute-simulation"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(rejected.returncode, 0, rejected.stderr)
            self.assertEqual(json.loads((rejected_target / "pipeline_result.json").read_text(encoding="utf-8"))["action_goals_sent"], 0)
            outputs = []
            for seed in ("1", "777"):
                target = root / f"seed-{seed}"
                run = subprocess.run([sys.executable, str(SCRIPT), "--text", "检查厨房", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(target)], cwd=ROOT, text=True, capture_output=True, env={**os.environ, "PYTHONHASHSEED": seed}, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                result = json.loads((target / "pipeline_result.json").read_text(encoding="utf-8"))
                self.assertEqual((result["execution_mode"], result["action_goals_sent"]), ("dry_run", 0))
                outputs.append(tuple((target / name).read_bytes() for name in ("natural_language_request.json", "natural_language_parse.json", "skill_plan.json", "pipeline_result.json", "pipeline_report.md")))
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
