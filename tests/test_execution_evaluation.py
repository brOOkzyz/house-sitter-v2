"""Tests for offline evaluation of completed simulation-only execution artifacts."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.execution_evaluation import ExecutionEvaluationError, evaluate_execution_artifacts, write_execution_evaluation
from house_sitter_core.nav2_sim_bridge import FakeNavigationExecutor, NavigationOutcome
from house_sitter_core.skill_execution_bridge import execute_skill_in_simulation, render_execution_artifacts, write_execution_artifacts
from house_sitter_core.skill_planner import compile_skill_plan, create_skill_request
from tests.skill_test_support import ROOT, demo_artifacts


SCRIPT = ROOT / "scripts" / "evaluate_skill_execution.py"


class ExecutionEvaluationTests(unittest.TestCase):
    def _execution_directory(self, root: Path, name: str, outcomes: list[NavigationOutcome]) -> Path:
        regions, goals = demo_artifacts()
        request = create_skill_request("patrol_home")
        plan = compile_skill_plan(request, regions, goals)
        result, events = execute_skill_in_simulation(plan, request, FakeNavigationExecutor(outcomes))
        target = root / name
        write_execution_artifacts(target, render_execution_artifacts(request, plan, result, events))
        return target

    def test_single_success_reports_navigation_metrics_and_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._execution_directory(root, "success", [
                NavigationOutcome("succeeded", ({"navigation_time_seconds": 1.0, "number_of_recoveries": 0},)),
                NavigationOutcome("succeeded", ({"navigation_time_seconds": 2.0, "number_of_recoveries": 1},)),
                NavigationOutcome("succeeded", ({"navigation_time_seconds": 3.0, "number_of_recoveries": 2},)),
                NavigationOutcome("succeeded", ({"navigation_time_seconds": 4.0, "number_of_recoveries": 0},)),
            ])
            contents = evaluate_execution_artifacts([run])
            summary = json.loads(contents["execution_summary.json"])
            trial = summary["trials"][0]
            self.assertEqual((summary["simulation_only"], summary["real_robot_supported"], summary["executable"]), (True, False, False))
            self.assertEqual((trial["overall_status"], trial["goal_count"], trial["feedback_count"], trial["recovery_count"]), ("succeeded", 4, 4, 3))
            self.assertEqual(trial["total_duration_seconds"], 10.0)
            self.assertIn("execution_trials.csv", contents); self.assertIn("Simulation-only", contents["execution_summary.md"])

    def test_multiple_trials_include_partial_timeout_and_deterministic_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeout = self._execution_directory(root, "a-timeout", [
                NavigationOutcome("succeeded", ({"navigation_time_seconds": 2.0},)),
                NavigationOutcome("timed_out", ({"navigation_time_seconds": 9.0, "number_of_recoveries": 2},)),
            ])
            success = self._execution_directory(root, "z-success", [NavigationOutcome("succeeded")] * 4)
            summary = json.loads(evaluate_execution_artifacts([success, timeout])["execution_summary.json"])
            self.assertEqual([trial["overall_status"] for trial in summary["trials"]], ["timed_out", "succeeded"])
            first = summary["trials"][0]
            self.assertEqual((first["goal_count"], first["timed_out_steps"], first["cancelled_steps"], first["failure_reason"]), (2, 1, 5, "timeout_exceeded"))
            self.assertEqual(summary["status_counts"], {"cancelled": 0, "failed": 0, "succeeded": 1, "timed_out": 1})

    def test_missing_corrupt_or_non_simulation_inputs_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ExecutionEvaluationError, "missing"):
                evaluate_execution_artifacts([root / "missing"])
            run = self._execution_directory(root, "run", [NavigationOutcome("succeeded")] * 4)
            (run / "execution_events.jsonl").write_text("{broken\n", encoding="utf-8")
            with self.assertRaisesRegex(ExecutionEvaluationError, "invalid JSONL"):
                evaluate_execution_artifacts([run])
            self._execution_directory(root, "replacement", [NavigationOutcome("succeeded")] * 4)
            run = root / "replacement"
            request = json.loads((run / "execution_request.json").read_text(encoding="utf-8"))
            request["simulation_only"] = False
            (run / "execution_request.json").write_text(json.dumps(request), encoding="utf-8")
            with self.assertRaisesRegex(ExecutionEvaluationError, "simulation-only"):
                evaluate_execution_artifacts([run])
            output = root / "output"
            with self.assertRaisesRegex(ExecutionEvaluationError, "already exists"):
                output.mkdir(); write_execution_evaluation(output, {"execution_trials.csv": "", "execution_summary.json": "{}", "execution_summary.md": ""})

    def test_cross_artifact_step_identity_tampering_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            mutations = (
                ("result-label", "execution_result.json", lambda document: document["steps"][0].update(label="tampered")),
                ("result-action", "execution_result.json", lambda document: document["steps"][0].update(action_type="tampered_action")),
                ("event-label", "execution_events.jsonl", lambda event: event.update(label="tampered")),
                ("event-action", "execution_events.jsonl", lambda event: event.update(action_type="tampered_action")),
                ("event-order", "execution_events.jsonl", lambda event: event.update(step_order=999)),
            )
            for name, artifact, mutate in mutations:
                with self.subTest(name=name):
                    run = self._execution_directory(root, name, [NavigationOutcome("succeeded")] * 4)
                    path = run / artifact
                    if artifact.endswith(".jsonl"):
                        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                        mutate(events[0])
                        path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
                    else:
                        document = json.loads(path.read_text(encoding="utf-8")); mutate(document)
                        path.write_text(json.dumps(document), encoding="utf-8")
                    output = root / f"{name}-output"
                    with self.assertRaises(ExecutionEvaluationError):
                        evaluate_execution_artifacts([run])
                    self.assertFalse(output.exists())

    def test_atomic_write_and_hash_seed_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = self._execution_directory(root, "run", [NavigationOutcome("succeeded")] * 4)
            contents = evaluate_execution_artifacts([run])
            failed = root / "failed"
            with mock.patch.object(Path, "write_text", side_effect=OSError("write failure")):
                with self.assertRaisesRegex(OSError, "write failure"):
                    write_execution_evaluation(failed, contents)
            self.assertFalse(failed.exists())
            output_bytes = []
            for seed in ("1", "777"):
                target = root / f"seed-{seed}"
                run_process = subprocess.run([sys.executable, str(SCRIPT), str(run), "--output-dir", str(target)], cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, text=True, capture_output=True, check=False)
                self.assertEqual(run_process.returncode, 0, run_process.stderr)
                output_bytes.append(tuple((target / name).read_bytes() for name in ("execution_trials.csv", "execution_summary.json", "execution_summary.md")))
            self.assertEqual(output_bytes[0], output_bytes[1])


if __name__ == "__main__":
    unittest.main()
