"""Behavior tests for deterministic offline simulation-only evaluation."""

import copy
import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from house_sitter_core.simulation_evaluation import (
    SimulationEvaluationError,
    evaluate,
    evaluate_paths,
    write_evaluation,
)


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/run_simulation_evaluation.py"
support_spec = importlib.util.spec_from_file_location("demo_support", ROOT / "tests/test_demo_semantic_map.py")
demo_support = importlib.util.module_from_spec(support_spec)
assert support_spec and support_spec.loader
support_spec.loader.exec_module(demo_support)


class SimulationEvaluationTests(unittest.TestCase):
    def artifacts(self):
        case = demo_support.DemoSemanticMapTests(); metadata = case.metadata()
        return demo_support.demo.create_demo(metadata, case.document(metadata))[:2]

    def contents(self, trials=3):
        regions, goals = self.artifacts()
        return evaluate(regions, goals, trials_per_scenario=trials)

    def test_all_four_scenarios_match_expected_patterns_and_counts(self):
        contents = self.contents(3)
        trials = list(csv.DictReader(io.StringIO(contents["evaluation_trials.csv"])))
        summary = list(csv.DictReader(io.StringIO(contents["evaluation_summary.csv"])))
        self.assertEqual([row["scenario"] for row in summary], ["baseline_success", "simulated_failure", "simulated_timeout", "user_cancel"])
        expected = {
            "baseline_success": ["succeeded", "succeeded", "succeeded", "succeeded"],
            "simulated_failure": ["succeeded", "failed", "cancelled", "cancelled"],
            "simulated_timeout": ["succeeded", "succeeded", "timed_out", "cancelled"],
            "user_cancel": ["succeeded", "succeeded", "cancelled", "cancelled"],
        }
        for scenario, pattern in expected.items():
            rows = [row for row in trials if row["scenario"] == scenario]
            self.assertEqual([row["trial_index"] for row in rows], ["1", "2", "3"])
            for row in rows:
                self.assertEqual([row[f"{label}_status"] for label in ("living_room", "kitchen", "bedroom", "charging_area")], pattern)
        self.assertTrue(all(row["matching_trials"] == "3" and row["mismatching_trials"] == "0" and row["deterministic"] == "true" for row in summary))
        document = json.loads(contents["evaluation_summary.json"])
        self.assertTrue(document["overall_contract_passed"])
        self.assertEqual(document["total_trials"], 12)

    def test_one_trial_fixed_fields_svg_and_report_boundary(self):
        contents = self.contents(1)
        rows = list(csv.reader(io.StringIO(contents["evaluation_trials.csv"])))
        self.assertEqual(rows[0], ["scenario", "trial_index", "overall_status", "total_steps", "succeeded_steps", "failed_steps", "timed_out_steps", "cancelled_steps", "terminal_label", "terminal_reason", "living_room_status", "kitchen_status", "bedroom_status", "charging_area_status"])
        root = ET.fromstring(contents["evaluation_status_counts.svg"])
        labels = {node.text for node in root.findall("{http://www.w3.org/2000/svg}text")}
        self.assertTrue({"baseline_success", "simulated_failure", "simulated_timeout", "user_cancel", "succeeded", "failed", "timed_out", "cancelled"}.issubset(labels))
        report = contents["evaluation_report.md"]
        self.assertIn("simulation/review only", report)
        self.assertIn("No ROS/Nav2 command execution", report)
        self.assertNotIn("real robot navigation success", report)

    def test_invalid_trials_fail_closed(self):
        regions, goals = self.artifacts()
        for value in (0, -1, True, "1"):
            with self.subTest(value=value), self.assertRaises(SimulationEvaluationError):
                evaluate(regions, goals, trials_per_scenario=value)

    def test_existing_sequence_artifact_validation_is_not_weakened(self):
        regions, goals = self.artifacts()
        variants = []
        bad = copy.deepcopy(goals); bad["goals"][0]["review_only"] = 1; variants.append(bad)
        for value in (False, None, 0, 1, "true", [], {}):
            bad = copy.deepcopy(goals); bad["goals"][0]["simulation_only"] = value; variants.append(bad)
        bad = copy.deepcopy(goals); bad["goals"][0]["executable"] = 0; variants.append(bad)
        bad = copy.deepcopy(goals); del bad["goals"][0]["polygon_validation_passed"]; variants.append(bad)
        bad = copy.deepcopy(goals); bad["goals"][0]["faster_safety_passed"] = False; variants.append(bad)
        bad = copy.deepcopy(goals); del bad["goals"][0]["raster_safety_evidence"]; variants.append(bad)
        bad = copy.deepcopy(goals); bad["goals"][0]["raster_safety_evidence"]["occupied_count"] = 1; variants.append(bad)
        bad = copy.deepcopy(goals); bad["goals"][0]["raster_safety_evidence"]["rasterized_pixel_count"] = 1; variants.append(bad)
        bad = copy.deepcopy(goals); bad["map_identity"] = {**bad["map_identity"], "width": "bad"}; variants.append(bad)
        bad = copy.deepcopy(goals); del bad["map_identity"]["fingerprint"]; variants.append(bad)
        bad_regions = copy.deepcopy(regions); bad_regions["map_identity"] = {**bad_regions["map_identity"], "width": 1}; variants.append((bad_regions, goals))
        for variant in variants:
            source_regions, source_goals = variant if isinstance(variant, tuple) else (regions, variant)
            with self.subTest(variant=variant), self.assertRaises(SimulationEvaluationError):
                evaluate(source_regions, source_goals)

    def test_source_mismatch_and_malformed_json_fail_closed(self):
        regions, goals = self.artifacts()
        bad = copy.deepcopy(goals); bad["goals"][0]["candidate_partition_id"] = "wrong"
        with self.assertRaises(SimulationEvaluationError):
            evaluate(regions, bad)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); bad_path, goal_path = root / "bad.json", root / "goals.json"
            bad_path.write_text("{", encoding="utf-8"); goal_path.write_text(json.dumps(goals), encoding="utf-8")
            with self.assertRaises(SimulationEvaluationError):
                evaluate_paths(bad_path, goal_path)

    def test_non_demo_json_cannot_be_consumed_as_an_evaluation_artifact(self):
        regions, goals = self.artifacts()
        forged = copy.deepcopy(goals)
        forged.pop("demo_only")
        with self.assertRaises(SimulationEvaluationError):
            evaluate(regions, forged)
        forged = copy.deepcopy(goals)
        forged["goals"][0]["synthetic_semantics"] = False
        with self.assertRaises(SimulationEvaluationError):
            evaluate(regions, forged)

    def test_atomic_output_failures_leave_no_artifacts(self):
        contents = self.contents(1)
        original = Path.write_text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, failure_at in (("first", 1), ("second", 2), ("middle", 3), ("fourth", 4), ("svg", 5)):
                calls = []
                def fail(path, data, *args, **kwargs):
                    calls.append(path)
                    if len(calls) == failure_at:
                        raise OSError("injected write failure")
                    return original(path, data, *args, **kwargs)
                output = root / name
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail), self.assertRaises(OSError):
                    write_evaluation(output, contents)
                self.assertFalse(output.exists()); self.assertEqual(list(root.glob(f".{name}.tmp-*")), [])
            output = root / "rename"
            with mock.patch("house_sitter_core.simulation_evaluation.os.replace", side_effect=OSError("injected rename")), self.assertRaises(OSError):
                write_evaluation(output, contents)
            self.assertFalse(output.exists()); self.assertEqual(list(root.glob(".rename.tmp-*")), [])
            output = root / "mkdtemp"
            with mock.patch("house_sitter_core.simulation_evaluation.tempfile.mkdtemp", side_effect=OSError("injected temporary creation")):
                with self.assertRaisesRegex(OSError, "temporary creation"):
                    write_evaluation(output, contents)
            self.assertFalse(output.exists()); self.assertEqual(list(root.glob(".mkdtemp.tmp-*")), [])
            existing = root / "existing"; existing.mkdir(); (existing / "preserved").write_text("x", encoding="utf-8")
            with self.assertRaises(SimulationEvaluationError):
                write_evaluation(existing, contents)
            self.assertEqual((existing / "preserved").read_text(encoding="utf-8"), "x")

    def test_cleanup_failure_preserves_original_write_exception_and_cleans_directory(self):
        contents = self.contents(1)
        original_write = Path.write_text
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cleanup"
            def fail_write(path, data, *args, **kwargs):
                raise OSError("primary write failure")
            with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail_write), \
                 mock.patch("house_sitter_core.simulation_evaluation.shutil.rmtree", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(OSError, "primary write failure") as captured:
                    write_evaluation(output, contents)
            self.assertIn("temporary cleanup failed", "\n".join(getattr(captured.exception, "__notes__", [])))
            self.assertFalse(output.exists())
            self.assertEqual(list(Path(directory).glob(".cleanup.tmp-*")), [])

    def test_keyboard_interrupt_and_system_exit_propagate_and_clean_up(self):
        contents = self.contents(1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, error in (("interrupt", KeyboardInterrupt()), ("exit", SystemExit(7))):
                output = root / name
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=error):
                    with self.assertRaises(type(error)):
                        write_evaluation(output, contents)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{name}.tmp-*")), [])

    def test_two_process_outputs_are_byte_identical(self):
        regions, goals = self.artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions_path, goals_path = root / "regions.json", root / "goals.json"
            regions_path.write_text(json.dumps(regions), encoding="utf-8"); goals_path.write_text(json.dumps(goals), encoding="utf-8")
            outputs = []
            for seed in ("1", "777"):
                output = root / f"output-{seed}"
                run = subprocess.run([sys.executable, str(SCRIPT), "--semantic-regions", str(regions_path), "--safe-goals", str(goals_path), "--output-dir", str(output), "--trials-per-scenario", "2"], cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, text=True, capture_output=True, check=False)
                self.assertEqual(run.returncode, 0, run.stderr); outputs.append(output)
            for name in ("evaluation_trials.csv", "evaluation_summary.csv", "evaluation_summary.json", "evaluation_report.md", "evaluation_status_counts.svg"):
                self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes())

    def test_cli_invalid_input_has_no_traceback_or_output(self):
        regions, goals = self.artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions_path, goals_path, output = root / "regions.json", root / "goals.json", root / "output"
            regions_path.write_text(json.dumps(regions), encoding="utf-8"); goals_path.write_text(json.dumps(goals), encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), "--semantic-regions", str(regions_path), "--safe-goals", str(goals_path), "--output-dir", str(output), "--trials-per-scenario", "0"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 2); self.assertNotIn("Traceback", run.stderr); self.assertFalse(output.exists())

    def test_cli_has_no_custom_sequence_argument(self):
        regions, goals = self.artifacts()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions_path, goals_path, output = root / "regions.json", root / "goals.json", root / "output"
            regions_path.write_text(json.dumps(regions), encoding="utf-8"); goals_path.write_text(json.dumps(goals), encoding="utf-8")
            run = subprocess.run([sys.executable, str(SCRIPT), "--semantic-regions", str(regions_path), "--safe-goals", str(goals_path), "--output-dir", str(output), "--sequence", "living_room,kitchen,bedroom,charging_area"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 2); self.assertIn("unrecognized arguments", run.stderr); self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
