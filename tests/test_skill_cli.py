"""Artifact publication, CLI, and determinism tests for simulation skills."""

import builtins
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from house_sitter_core.skill_artifacts import (
    ARTIFACT_NAMES,
    SkillArtifactError,
    build_skill_run,
    render_skill_artifacts,
    write_skill_artifacts,
)
from house_sitter_core.skill_planner import create_skill_request
from tests.skill_test_support import ROOT, demo_artifacts, write_artifacts


RUN_SCRIPT = ROOT / "scripts" / "run_simulation_skill.py"
PREVIEW_SCRIPT = ROOT / "scripts" / "preview_simulation_skill.py"


class SkillCliTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def contents(self):
        request = create_skill_request("patrol_home")
        plan, result, events, contents = build_skill_run(request, self.regions, self.goals)
        return request, plan, result, events, contents

    def test_rendered_artifacts_are_exact_parseable_and_safety_labeled(self):
        request, plan, result, events, contents = self.contents()
        self.assertEqual(tuple(contents), ARTIFACT_NAMES)
        self.assertEqual(json.loads(contents["skill_request.json"])["request_id"], request.request_id)
        self.assertEqual(json.loads(contents["skill_plan.json"])["total_steps"], plan["total_steps"])
        self.assertEqual(json.loads(contents["skill_result.json"])["overall_status"], result["overall_status"])
        parsed_events = [json.loads(line) for line in contents["skill_events.jsonl"].splitlines()]
        self.assertEqual(parsed_events, events)
        for event in parsed_events:
            self.assertIs(event["synthetic"], True)
            self.assertIs(event["review_only"], True)
            self.assertIs(event["simulation_only"], True)
            self.assertIs(event["executable"], False)
        report = contents["skill_report.md"]
        for warning in ("SIMULATION ONLY", "REVIEW ONLY", "NOT REAL ROBOT EXECUTION", "NO ROS / NAV2 COMMANDS SENT", "NO PHYSICAL MANIPULATION", "NO REAL SENSOR DETECTION"):
            self.assertIn(warning, report)
        self.assertIn("does not authenticate file provenance", report)

    def test_timeout_report_and_events_distinguish_terminal_and_upstream_reasons(self):
        request = create_skill_request("patrol_home", injected_events={"timeout_step_order": 3, "timeout_seconds": 5, "simulated_duration_seconds": 8})
        _, result, events, contents = build_skill_run(request, self.regions, self.goals)
        self.assertEqual(result["steps"][2]["terminal_reason"], "timeout_exceeded")
        self.assertTrue(all(step["terminal_reason"] == "upstream_timeout" for step in result["steps"][3:]))
        self.assertTrue(all(event["reason"] == "upstream_timeout" for event in events if event["status"] == "cancelled"))
        self.assertIn("timeout_exceeded", contents["skill_report.md"])
        self.assertIn("upstream_timeout", contents["skill_report.md"])

    def test_atomic_failures_for_each_file_and_publication_leave_no_artifacts(self):
        contents = self.contents()[-1]
        original = Path.write_text
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for failure_at, artifact_name in enumerate(ARTIFACT_NAMES, start=1):
                calls = []

                def fail(path, data, *args, **kwargs):
                    calls.append(path)
                    if len(calls) == failure_at:
                        raise OSError(f"injected write failure: {artifact_name}")
                    return original(path, data, *args, **kwargs)

                output = root / f"write-{failure_at}"
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=fail):
                    with self.assertRaisesRegex(OSError, artifact_name):
                        write_skill_artifacts(output, contents)
                self.assertEqual(len(calls), failure_at)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{output.name}.tmp-*")), [])
            output = root / "replace"
            with mock.patch("house_sitter_core.skill_artifacts.os.replace", side_effect=OSError("publication rename failure")):
                with self.assertRaisesRegex(OSError, "publication rename failure"):
                    write_skill_artifacts(output, contents)
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob(".replace.tmp-*")), [])

    def test_temp_creation_existing_output_and_incomplete_set_fail_closed(self):
        contents = self.contents()[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "temp-create"
            with mock.patch("house_sitter_core.skill_artifacts.tempfile.TemporaryDirectory", side_effect=OSError("temporary creation failure")):
                with self.assertRaisesRegex(OSError, "temporary creation failure"):
                    write_skill_artifacts(output, contents)
            self.assertFalse(output.exists())
            existing = root / "existing"
            existing.mkdir()
            preserved = existing / "preserved.txt"
            preserved.write_text("unchanged", encoding="utf-8")
            with self.assertRaisesRegex(SkillArtifactError, "already exists"):
                write_skill_artifacts(existing, contents)
            self.assertEqual(preserved.read_text(encoding="utf-8"), "unchanged")
            with self.assertRaisesRegex(SkillArtifactError, "output set"):
                write_skill_artifacts(root / "incomplete", {"skill_request.json": "{}"})

    def test_cleanup_os_error_does_not_mask_primary_write_error(self):
        contents = self.contents()[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "cleanup-os"
            with mock.patch.object(Path, "write_text", autospec=True, side_effect=OSError("primary-write-failure")), \
                 mock.patch("house_sitter_core.skill_artifacts.tempfile.TemporaryDirectory.cleanup", side_effect=OSError("cleanup failure")):
                with self.assertRaisesRegex(OSError, "primary-write-failure") as captured:
                    write_skill_artifacts(output, contents)
            self.assertIn("temporary cleanup failed: OSError", "\n".join(getattr(captured.exception, "__notes__", [])))
            self.assertFalse(output.exists())

    def test_cleanup_base_exceptions_do_not_mask_primary_write_error(self):
        contents = self.contents()[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, cleanup_error in (
                ("interrupt", KeyboardInterrupt()),
                ("exit", SystemExit(7)),
                ("runtime", RuntimeError("cleanup-runtime-failure")),
            ):
                output = root / f"cleanup-{name}"
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=OSError("primary-write-failure")), \
                     mock.patch("house_sitter_core.skill_artifacts.tempfile.TemporaryDirectory.cleanup", side_effect=cleanup_error):
                    with self.subTest(cleanup=name), self.assertRaisesRegex(OSError, "primary-write-failure") as captured:
                        write_skill_artifacts(output, contents)
                self.assertIn(type(cleanup_error).__name__, "\n".join(getattr(captured.exception, "__notes__", [])))
                self.assertFalse(output.exists())

    def test_cleanup_of_replaced_temporary_symlink_never_follows_target(self):
        contents = self.contents()[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "symlink"
            target = root / "sentinel-target"
            target.mkdir()
            preserved = target / "preserved.txt"
            preserved.write_text("keep", encoding="utf-8")
            temporary_paths = []

            def replace_temporary_with_symlink(path, *args, **kwargs):
                temporary = path.parent
                temporary_paths.append(temporary)
                temporary.rmdir()
                os.symlink(target, temporary, target_is_directory=True)
                raise OSError("primary-write-failure")

            with mock.patch.object(Path, "write_text", autospec=True, side_effect=replace_temporary_with_symlink):
                with self.assertRaisesRegex(OSError, "primary-write-failure"):
                    write_skill_artifacts(output, contents)
            self.assertEqual(len(temporary_paths), 1)
            self.assertEqual(preserved.read_text(encoding="utf-8"), "keep")
            self.assertTrue(os.path.islink(temporary_paths[0]))
            self.assertFalse(output.exists())

    def test_check_all_rooms_upstream_failure_cancels_summary(self):
        request = create_skill_request("check_all_rooms", injected_events={"fail_step_order": 1})
        _, result, _, _ = build_skill_run(request, self.regions, self.goals)
        summary = result["steps"][-1]
        self.assertEqual(summary["action_type"], "report_home_check_summary")
        self.assertEqual(summary["status"], "cancelled")
        self.assertEqual(summary["terminal_reason"], "upstream_failure")

    def test_keyboard_interrupt_and_system_exit_propagate_and_cleanup(self):
        contents = self.contents()[-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, error in (("interrupt", KeyboardInterrupt()), ("system-exit", SystemExit(7))):
                output = root / name
                with mock.patch.object(Path, "write_text", autospec=True, side_effect=error):
                    with self.assertRaises(type(error)):
                        write_skill_artifacts(output, contents)
                self.assertFalse(output.exists())
                self.assertEqual(list(root.glob(f".{name}.tmp-*")), [])

    def test_two_independent_processes_and_hash_seeds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions, goals = write_artifacts(root)
            outputs = []
            for seed in ("1", "777"):
                output = root / f"run-{seed}"
                command = [
                    sys.executable, str(RUN_SCRIPT), "--skill", "deliver_item",
                    "--semantic-regions", str(regions), "--safe-goals", str(goals),
                    "--param", "item=medicine", "--param", "source=kitchen", "--param", "destination=bedroom",
                    "--output-dir", str(output),
                ]
                run = subprocess.run(command, cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, text=True, capture_output=True, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                outputs.append(output)
            for name in ARTIFACT_NAMES:
                self.assertEqual((outputs[0] / name).read_bytes(), (outputs[1] / name).read_bytes(), name)

    def test_queue_task_ids_and_priority_reordering_are_cross_process_deterministic(self):
        program = """
import json
from house_sitter_core.home_simulation_state import HomeSimulationState
from house_sitter_core.skill_planner import compile_skill_plan, create_skill_request
from house_sitter_core.skill_runtime import execute_skill_plan
from tests.skill_test_support import demo_artifacts
regions, goals = demo_artifacts()
state = HomeSimulationState()
for request_id, skill, priority in ((\"same\", \"patrol_home\", 50), (\"same\", \"task_status_report\", 50), (\"emergency\", \"emergency_response\", 0)):
    request = create_skill_request(\"queue_task\", {\"queued_skill\": skill}, request_id=request_id, priority=priority)
    plan = compile_skill_plan(request, regions, goals, state)
    execute_skill_plan(plan, request, state)
target = state.queued_tasks[1][\"task_id\"]
request = create_skill_request(\"change_task_priority\", {\"task_id\": target, \"new_priority\": 99})
plan = compile_skill_plan(request, regions, goals, state)
result, events = execute_skill_plan(plan, request, state)
print(json.dumps({\"queue\": state.snapshot()[\"queued_tasks\"], \"result\": result, \"events\": events}, sort_keys=True, separators=(\",\", \":\")))
"""
        outputs = []
        for seed in ("1", "777"):
            run = subprocess.run([sys.executable, "-c", program], cwd=ROOT, env={**os.environ, "PYTHONHASHSEED": seed}, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            outputs.append(run.stdout.encode("utf-8"))
        self.assertEqual(outputs[0], outputs[1])

    def test_cli_invalid_inputs_return_two_without_traceback_or_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions, goals = write_artifacts(root)
            cases = (
                ["--skill", "unknown_skill"],
                ["--skill", "deliver_item", "--param", "item=medicine"],
                ["--skill", "patrol_home", "--param", "broken"],
                ["--skill", "patrol_home", "--battery-percent", "nan"],
                ["--skill", "change_task_priority", "--param", "task_id=normal", "--param", "new_priority=100"],
            )
            for index, prefix in enumerate(cases, start=1):
                output = root / f"invalid-{index}"
                command = [sys.executable, str(RUN_SCRIPT), *prefix, "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(output)]
                run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
                with self.subTest(case=prefix):
                    self.assertEqual(run.returncode, 2)
                    self.assertNotIn("Traceback", run.stderr)
                    self.assertIn("Error:", run.stderr)
                    self.assertFalse(output.exists())

    def test_cli_rejects_repeated_state_options_and_invalid_checkpoint_literals(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions, goals = write_artifacts(root)
            cases = (
                ["--skill", "task_status_report", "--restricted-region", "living_room", "--restricted-region", "living_room"],
                ["--skill", "task_status_report", "--restricted-region", "living_room", "--restricted-region", "kitchen"],
                ["--skill", "task_status_report", "--blocked-goal", "living_room", "--blocked-goal", "living_room"],
                ["--skill", "task_status_report", "--blocked-goal", "living_room", "--blocked-goal", "kitchen"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=[]"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id={}"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=()"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=null"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=None"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=1"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=0"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=true"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=false"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=True"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=False"],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id="],
                ["--skill", "task_checkpoint", "--param", "checkpoint_id=   "],
            )
            for index, prefix in enumerate(cases, start=1):
                output = root / f"rejected-{index}"
                command = [sys.executable, str(RUN_SCRIPT), *prefix, "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(output)]
                run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
                with self.subTest(case=prefix):
                    self.assertEqual(run.returncode, 2)
                    self.assertNotIn("Traceback", run.stderr)
                    self.assertIn("Error:", run.stderr)
                    self.assertFalse(output.exists())
                    self.assertEqual(list(root.glob(f".{output.name}.tmp-*")), [])

            for name, prefix in (
                ("single-restricted", ["--skill", "task_status_report", "--restricted-region", "living_room"]),
                ("single-blocked", ["--skill", "task_status_report", "--blocked-goal", "living_room"]),
                ("distinct-param", ["--skill", "deliver_item", "--param", "item=medicine", "--param", "source=kitchen", "--param", "destination=bedroom"]),
                ("distinct-event", ["--skill", "patrol_home", "--inject-event", "fail_step_order=1", "--inject-event", "recovery_action=retry"]),
            ):
                output = root / name
                command = [sys.executable, str(RUN_SCRIPT), *prefix, "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(output)]
                run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
                with self.subTest(accepted=name):
                    self.assertEqual(run.returncode, 0, run.stderr)
                    self.assertTrue(output.exists())

    def test_wrapper_clis_compile_target_steps_without_running_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions, goals = write_artifacts(root)
            missing = root / "missing"
            missing_run = subprocess.run([sys.executable, str(PREVIEW_SCRIPT), "--skill", "preview_skill_plan", "--param", "target_skill=deliver_item", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(missing)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(missing_run.returncode, 2)
            self.assertNotIn("Traceback", missing_run.stderr)
            self.assertFalse(missing.exists())
            preview = root / "preview"
            preview_run = subprocess.run([sys.executable, str(PREVIEW_SCRIPT), "--skill", "preview_skill_plan", "--param", "target_skill=deliver_item", "--param", "item=medicine", "--param", "source=kitchen", "--param", "destination=bedroom", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(preview)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(preview_run.returncode, 0, preview_run.stderr)
            preview_plan = json.loads((preview / "skill_plan.json").read_text(encoding="utf-8"))
            preview_result = json.loads((preview / "skill_result.json").read_text(encoding="utf-8"))
            self.assertEqual(preview_plan["target_plan"]["target_skill"], "deliver_item")
            self.assertEqual([step["action_type"] for step in preview_plan["steps"]], ["navigate_to_safe_goal", "pick_item_simulated", "navigate_to_safe_goal", "handover_item_simulated"])
            self.assertEqual([step["status"] for step in preview_result["steps"]], ["pending"] * 4)
            self.assertEqual((preview / "skill_events.jsonl").read_text(encoding="utf-8"), "")

    def test_preview_cli_writes_pending_plan_without_execution_events(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regions, goals = write_artifacts(root)
            output = root / "preview"
            run = subprocess.run([sys.executable, str(PREVIEW_SCRIPT), "--skill", "patrol_home", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(output)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            result = json.loads((output / "skill_result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["execution_mode"], "preview_only")
            self.assertIsNone(result["overall_status"])
            self.assertEqual((output / "skill_events.jsonl").read_text(encoding="utf-8"), "")

    def test_normal_path_import_guard_observes_no_robot_runtime_import(self):
        request = create_skill_request("security_check_routine")
        forbidden = ("rclpy", "nav2", "gazebo", "ros_gz", "ros_ign")
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.startswith(forbidden):
                raise AssertionError(f"forbidden runtime import: {name}")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=guarded):
            _, result, _, _ = build_skill_run(request, self.regions, self.goals)
        self.assertEqual(result["overall_status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
