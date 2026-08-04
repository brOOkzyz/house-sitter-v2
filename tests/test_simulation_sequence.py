"""Behavior tests for deterministic, non-executable demo sequence artifacts."""

import importlib.util
import json
import os
import builtins
import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.simulation_sequence import (
    DEFAULT_SEQUENCE,
    SimulationSequenceError,
    build_simulation_sequence,
    load_sequence_inputs,
    write_simulation_sequence_artifacts,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOT = PROJECT_ROOT / "local_annotations"
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "run_simulation_sequence.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("sequence_runner", SCRIPT_PATH)
runner = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC and RUNNER_SPEC.loader
RUNNER_SPEC.loader.exec_module(runner)
DEMO_TEST_PATH = PROJECT_ROOT / "tests" / "test_demo_semantic_map.py"
DEMO_SPEC = importlib.util.spec_from_file_location("demo_test_support", DEMO_TEST_PATH)
demo_tests = importlib.util.module_from_spec(DEMO_SPEC)
assert DEMO_SPEC and DEMO_SPEC.loader
DEMO_SPEC.loader.exec_module(demo_tests)
demo = demo_tests.demo


class SimulationSequenceTests(unittest.TestCase):
    def artifacts(self):
        case = demo_tests.DemoSemanticMapTests()
        metadata = case.metadata()
        return demo.create_demo(metadata, case.document(metadata))[:2]

    def test_default_four_step_sequence_succeeds_with_ordered_state_events(self):
        regions, goals = self.artifacts()
        plan, result = build_simulation_sequence(regions, goals)
        self.assertEqual(plan["requested_sequence"], list(DEFAULT_SEQUENCE))
        self.assertEqual([step["step_order"] for step in plan["steps"]], [1, 2, 3, 4])
        self.assertEqual([step["status"] for step in plan["steps"]], ["pending"] * 4)
        self.assertEqual(result["overall_status"], "succeeded")
        self.assertEqual((result["total_steps"], result["succeeded_steps"], result["failed_steps"]), (4, 4, 0))
        self.assertEqual([step["status"] for step in result["steps"]], ["succeeded"] * 4)
        self.assertEqual(
            [event["status"] for step in result["steps"] for event in step["state_events"]],
            ["pending", "running", "succeeded"] * 4,
        )
        self.assertEqual(
            [event["logical_event_order"] for step in result["steps"] for event in step["state_events"]],
            list(range(1, 13)),
        )

    def test_simulated_failure_cancels_downstream_steps(self):
        regions, goals = self.artifacts()
        _, result = build_simulation_sequence(regions, goals, fail_label="kitchen")
        self.assertEqual([step["status"] for step in result["steps"]], ["succeeded", "failed", "cancelled", "cancelled"])
        self.assertEqual([step["terminal_reason"] for step in result["steps"]], [None, "simulated_failure", "upstream_failure", "upstream_failure"])
        self.assertEqual((result["overall_status"], result["succeeded_steps"], result["failed_steps"], result["timed_out_steps"], result["cancelled_steps"]), ("failed", 1, 1, 0, 2))

    def test_timeout_cancels_downstream_and_equal_duration_succeeds(self):
        regions, goals = self.artifacts()
        _, timed_out = build_simulation_sequence(regions, goals, timeout_seconds=5, step_durations={"bedroom": 8})
        self.assertEqual([step["status"] for step in timed_out["steps"]], ["succeeded", "succeeded", "timed_out", "cancelled"])
        self.assertEqual([step["terminal_reason"] for step in timed_out["steps"]], [None, None, "timeout_exceeded", "upstream_timeout"])
        self.assertEqual(timed_out["overall_status"], "timed_out")
        _, equal = build_simulation_sequence(regions, goals, timeout_seconds=5, step_durations={"bedroom": 5})
        self.assertEqual([step["status"] for step in equal["steps"]], ["succeeded"] * 4)

    def test_user_cancel_marks_target_and_downstream_cancelled(self):
        regions, goals = self.artifacts()
        _, result = build_simulation_sequence(regions, goals, cancel_before_label="bedroom")
        self.assertEqual([step["status"] for step in result["steps"]], ["succeeded", "succeeded", "cancelled", "cancelled"])
        self.assertEqual([step["terminal_reason"] for step in result["steps"]], [None, None, "user_requested_cancel", "user_requested_cancel"])
        self.assertEqual(result["overall_status"], "cancelled")

    def test_control_validation_rejects_invalid_labels_numbers_and_conflicts(self):
        regions, goals = self.artifacts()
        invalid_calls = [
            {"fail_label": "missing"}, {"cancel_before_label": "missing"},
            {"timeout_seconds": 0}, {"timeout_seconds": float("nan")}, {"timeout_seconds": float("inf")},
            {"step_durations": {"missing": 1}}, {"step_durations": {"living_room": -1}},
            {"step_durations": {"living_room": float("nan")}}, {"fail_label": "kitchen", "cancel_before_label": "kitchen"},
        ]
        for controls in invalid_calls:
            with self.subTest(controls=controls), self.assertRaises(SimulationSequenceError):
                build_simulation_sequence(regions, goals, **controls)

    def test_cli_rejects_duplicate_step_duration_without_traceback(self):
        args = ["--semantic-regions", "missing-regions.json", "--safe-goals", "missing-goals.json", "--output-dir", str(LOCAL_ROOT / "never-created"), "--step-duration", "kitchen=1", "--step-duration", "kitchen=2"]
        self.assertEqual(runner.main(args), 2)

    def test_proposal_partition_and_source_provenance_are_preserved(self):
        regions, goals = self.artifacts()
        plan, result = build_simulation_sequence(regions, goals)
        regions_by_label = {region["canonical_label"]: region for region in regions["regions"]}
        for step in [*plan["steps"], *result["steps"]]:
            region = regions_by_label[step["label"]]
            self.assertEqual((step["proposal_id"], step["partition_id"]), (region["proposal_id"], region["partition_id"]))
            for field in ("source_candidate_order", "source_selection_rank", "demo_assignment_order"):
                self.assertEqual(step[field], region[field])
            self.assertTrue(step["review_only"])
            self.assertTrue(step["simulation_only"])
            self.assertFalse(step["executable"])

    def test_reordered_goal_array_resolves_by_label_and_source_key(self):
        regions, goals = self.artifacts()
        goals["goals"] = list(reversed(goals["goals"]))
        plan, result = build_simulation_sequence(regions, goals)
        self.assertEqual([step["label"] for step in plan["steps"]], list(DEFAULT_SEQUENCE))
        self.assertEqual([step["label"] for step in result["steps"]], list(DEFAULT_SEQUENCE))

    def test_sequence_completes_with_ros_and_navigation_runtime_imports_forbidden(self):
        regions, goals = self.artifacts()
        original_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name.split(".")[0] in {"rclpy", "nav2", "gazebo_msgs"}:
                raise AssertionError(f"simulation sequence attempted forbidden runtime import: {name}")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded_import):
            _, result = build_simulation_sequence(regions, goals)
        self.assertEqual(result["overall_status"], "succeeded")

    def test_missing_duplicate_or_multiple_goal_labels_fail_closed(self):
        regions, goals = self.artifacts()
        with self.assertRaisesRegex(SimulationSequenceError, "no unique region"):
            build_simulation_sequence(regions, goals, ("living_room", "missing_label"))
        with self.assertRaisesRegex(SimulationSequenceError, "duplicate label"):
            build_simulation_sequence(regions, goals, ("living_room", "living_room"))
        duplicate = dict(goals["goals"][0])
        goals["goals"].append(duplicate)
        goals["accepted_goal_count"] += 1
        with self.assertRaisesRegex(SimulationSequenceError, "multiple safe goals"):
            build_simulation_sequence(regions, goals)

    def test_map_identity_mismatch_and_executable_goal_fail_closed(self):
        regions, goals = self.artifacts()
        goals["map_identity"] = {**goals["map_identity"], "width": 999}
        with self.assertRaisesRegex(SimulationSequenceError, "map_identity mismatch"):
            build_simulation_sequence(regions, goals)
        regions, goals = self.artifacts()
        goals["goals"][0]["executable"] = True
        with self.assertRaisesRegex(SimulationSequenceError, "executable"):
            build_simulation_sequence(regions, goals)

    def test_map_identity_requires_full_current_schema_and_valid_types(self):
        regions, goals = self.artifacts()
        for field in tuple(regions["map_identity"]):
            broken_regions, broken_goals = copy.deepcopy(regions), copy.deepcopy(goals)
            del broken_regions["map_identity"][field]
            with self.subTest(missing=field), self.assertRaisesRegex(SimulationSequenceError, "field set"):
                build_simulation_sequence(broken_regions, broken_goals)
        invalid_values = {
            "schema_version": 1,
            "width": True,
            "height": 0,
            "resolution": "0.1",
            "origin": [0.0, 0.0],
            "negate": True,
            "occupied_thresh": [],
            "free_thresh": 2.0,
            "image_sha256": "not-a-hash",
            "fingerprint": None,
        }
        for field, value in invalid_values.items():
            broken_regions, broken_goals = copy.deepcopy(regions), copy.deepcopy(goals)
            broken_regions["map_identity"][field] = value
            broken_goals["map_identity"][field] = value
            with self.subTest(invalid=field), self.assertRaises(SimulationSequenceError):
                build_simulation_sequence(broken_regions, broken_goals)

    def test_safe_goal_requires_strict_simulation_flags_and_selector_evidence(self):
        regions, goals = self.artifacts()
        for value in (False, None, 0, 1, "true", [], {}):
            broken = copy.deepcopy(goals); broken["goals"][0]["simulation_only"] = value
            with self.subTest(simulation_only=value), self.assertRaisesRegex(SimulationSequenceError, "simulation_only"):
                build_simulation_sequence(regions, broken)
        for field, value in (("review_only", 1), ("executable", 0)):
            broken = copy.deepcopy(goals); broken["goals"][0][field] = value
            with self.subTest(flag=field), self.assertRaises(SimulationSequenceError):
                build_simulation_sequence(regions, broken)
        for field in ("polygon_validation_passed", "faster_safety_passed", "raster_safety_evidence"):
            broken = copy.deepcopy(goals); del broken["goals"][0][field]
            with self.subTest(missing_evidence=field), self.assertRaises(SimulationSequenceError):
                build_simulation_sequence(regions, broken)
        evidence_changes = {
            "passed": False,
            "occupied_count": 1,
            "unknown_count": 1,
            "out_of_bounds_count": 1,
            "rasterized_pixel_count": 1,
            "safe_free_ratio": "1.0",
            "failure_reasons": ["unsafe"],
        }
        for field, value in evidence_changes.items():
            broken = copy.deepcopy(goals); broken["goals"][0]["raster_safety_evidence"][field] = value
            with self.subTest(evidence=field), self.assertRaises(SimulationSequenceError):
                build_simulation_sequence(regions, broken)

    def test_rejected_or_malformed_goal_document_fails_closed_with_specific_error(self):
        regions, goals = self.artifacts()
        goals["accepted_goal_count"] = 3
        with self.assertRaisesRegex(SimulationSequenceError, "accepted_goal_count"):
            build_simulation_sequence(regions, goals)
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.json"; valid = Path(directory) / "valid.json"
            bad.write_text("{", encoding="utf-8")
            valid.write_text(json.dumps(regions), encoding="utf-8")
            with self.assertRaisesRegex(SimulationSequenceError, "Cannot load semantic regions"):
                load_sequence_inputs(bad, valid)

    def test_existing_output_and_intermediate_write_failures_leave_no_artifacts(self):
        regions, goals = self.artifacts(); plan, result = build_simulation_sequence(regions, goals)
        original_write = Path.write_text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); existing = root / "existing"; existing.mkdir()
            with self.assertRaisesRegex(SimulationSequenceError, "already exists"):
                write_simulation_sequence_artifacts(existing, plan, result)
            for name, fail_at in (("first", 1), ("second", 2)):
                output = root / name; calls = []
                def write_then_fail(path, data, *args, **kwargs):
                    calls.append(path)
                    if len(calls) == fail_at:
                        raise OSError("write failure")
                    return original_write(path, data, *args, **kwargs)
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=write_then_fail):
                    with self.assertRaisesRegex(OSError, "write failure"):
                        write_simulation_sequence_artifacts(output, plan, result)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{name}.tmp-*")), [])
            output = root / "rename"
            with mock.patch("house_sitter_core.simulation_sequence.os.replace", side_effect=OSError("rename failure")):
                with self.assertRaisesRegex(OSError, "rename failure"):
                    write_simulation_sequence_artifacts(output, plan, result)
            self.assertFalse(output.exists())
            self.assertFalse((output / "simulation_sequence_plan.json").exists())
            self.assertFalse((output / "simulation_sequence_result.json").exists())
            self.assertEqual(list(root.glob(".rename.tmp-*")), [])

    def test_cross_process_artifacts_are_byte_identical(self):
        regions, goals = self.artifacts()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory(dir=LOCAL_ROOT) as local:
            root = Path(directory); region_path = root / "regions.json"; goal_path = root / "goals.json"
            region_path.write_text(json.dumps(regions), encoding="utf-8")
            goal_path.write_text(json.dumps(goals), encoding="utf-8")
            first, second = Path(local) / "first", Path(local) / "second"
            for seed, target in (("1", first), ("777", second)):
                run = subprocess.run(
                    [sys.executable, str(SCRIPT_PATH), "--semantic-regions", str(region_path), "--safe-goals", str(goal_path), "--output-dir", str(target)],
                    cwd=PROJECT_ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=False,
                )
                self.assertEqual(run.returncode, 0, run.stderr)
            for name in ("simulation_sequence_plan.json", "simulation_sequence_result.json"):
                self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
