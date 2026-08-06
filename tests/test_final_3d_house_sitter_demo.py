"""Headless tests for Phase-1 3D demo orchestration; no ROS or Gazebo starts."""
from __future__ import annotations

import json
import importlib.util
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.final_3d_demo import (  # noqa: E402
    FinalDemoError, SafeGoal, SCENARIO_ID, kitchen_obstacle_spec, make_observation, preflight, run_demo, safe_goals,
)
from house_sitter_core.nav2_sim_bridge import NavigationOutcome  # noqa: E402

SCRIPT = ROOT / "scripts" / "run_final_3d_house_sitter_demo.py"
SPEC = importlib.util.spec_from_file_location("final_3d_demo_cli", SCRIPT)
assert SPEC and SPEC.loader
cli_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cli_module
SPEC.loader.exec_module(cli_module)


def query_result(command=None, *, stdout="", stderr="", exit_code=0, timed_out=False, error=None):
    from house_sitter_core.final_3d_demo import CommandResult
    return CommandResult(command or ["query"], True, True, exit_code, timed_out, stdout, stderr, 0.01, error)


class FakeRuntime:
    def __init__(self, outcomes=("succeeded", "succeeded")):
        self.outcomes = list(outcomes); self.started = False; self.closed = False; self.goals = []; self.deleted = []

    def start(self, **kwargs): self.started = True
    def ready(self, timeout_seconds): return {name: True for name in ("/clock", "/scan", "/odom", "/tf", "/tf_static", "/map", "/amcl_pose", "/navigate_to_pose", "/stop_motor")}
    def navigate(self, goal, timeout_seconds):
        self.goals.append(goal); return {"goal": {"label": goal.label}, "result": self.outcomes.pop(0), "final_pose": {"x": goal.x, "y": goal.y}}
    def spawn_obstacle(self, obstacle): return {"entity_name": obstacle.entity_name, "pose": {"x": obstacle.x, "y": obstacle.y, "z": obstacle.z}, "success": True}
    def entity_exists(self, entity_name): return True
    def remove_entity(self, entity_name): self.deleted.append(entity_name); return True
    def close(self): self.closed = True


class FakePreviewRuntime:
    def __init__(self, *, ready=True, spawned=True, control_ready=True, query_timed_out=False):
        self.ready, self.spawned, self.control_ready, self.query_timed_out = ready, spawned, control_ready, query_timed_out; self.calls = []; self.closed = False

    def preflight(self): return {"checked": True}
    def commands(self, goal, *, headless=False): return {"gazebo": ["ros2", "launch", "ros_gz_sim"], "turtlebot4_spawn": ["ros2", "launch", "turtlebot4_gz_bringup", f"x:={goal.x}"]}
    def launch_house_and_robot(self, goal, *, headless=False):
        self.calls.append((goal, headless))
        verification = {"entity_creation_reported_success": self.spawned, "entity_verification_attempted": True, "entity_verification_method": "creation_acknowledgement" if self.query_timed_out else "world_scene_info_service", "entity_query_confirmed": self.spawned and not self.query_timed_out, "entity_query_timed_out": self.query_timed_out, "entity_query_command": ["gz", "service"], "entity_query_log": "fake.log"}
        robot_spawn = {"requested_entity_name": "turtlebot4", "robot_entity_spawned": self.spawned, **verification}
        return {"house_world_started": True, "house_world_ready": self.ready, "robot_spawn_requested": True, "robot_spawned": self.spawned, "robot_entity_spawned": self.spawned, **verification, "robot_simulation_interfaces_ready": self.control_ready, "robot_control_stack_ready": self.control_ready, "stop_motor_service_ready": self.control_ready, "control_readiness_warnings": [] if self.control_ready else ["Service stop_motor unavailable."], "preview_available": self.ready and self.spawned, "navigation_available": self.control_ready, "robot_spawn": robot_spawn, "charging_area_pose": {"x": goal.x, "y": goal.y, "yaw": goal.yaw, "z": 0.05, "source": goal.reference["proposal_id"]}}
    def shutdown(self): self.closed = True; return []
    def wait_for_house_close(self): return None


class Final3DDemoTests(unittest.TestCase):
    def test_preflight_and_safe_goals_use_committed_house_v1_artifacts(self):
        check = preflight(ROOT); regions, goals = safe_goals(ROOT)
        self.assertEqual(goals["kitchen"].reference["canonical_label"], "kitchen")
        self.assertEqual(goals["charging_area"].reference["canonical_label"], "charging_area")
        self.assertTrue(Path(check["house_world"]).is_file())
        self.assertFalse(check["house_v1_dynamic_navigation_verified"])

    def test_obstacle_is_unique_and_inside_the_kitchen_region(self):
        regions, goals = safe_goals(ROOT)
        first = kitchen_obstacle_spec(regions, goals["kitchen"], "one")
        second = kitchen_obstacle_spec(regions, goals["kitchen"], "two")
        self.assertNotEqual(first.entity_name, second.entity_name)
        self.assertTrue(first.entity_name.startswith("house_sitter_final_demo_obstacle_"))

    def test_dry_run_is_fail_closed_and_creates_all_artifacts_without_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"; result = run_demo(ROOT, output, runtime=None, dry_run=True, headless=True, timeout_seconds=5)
            self.assertFalse(result["summary"]["kitchen_navigation_success"])
            self.assertEqual(len(list(output.iterdir())), 13)
            manifest = json.loads((output / "demo_manifest.json").read_text())
            self.assertEqual(manifest["scenario_id"], SCENARIO_ID)
            self.assertTrue(manifest["simulation_only"])

    def test_live_flow_uses_existing_detector_and_updates_only_kitchen(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = FakeRuntime(); output = Path(directory) / "result"
            result = run_demo(ROOT, output, runtime=runtime, dry_run=False, headless=True, timeout_seconds=5)
            self.assertTrue(result["summary"]["kitchen_navigation_success"])
            self.assertTrue(result["summary"]["anomaly_detection_success"])
            self.assertTrue(result["summary"]["digital_twin_update_success"])
            self.assertTrue(result["summary"]["alert_generation_success"])
            self.assertTrue(result["summary"]["return_to_charge_success"])
            self.assertEqual([goal.label for goal in runtime.goals], ["kitchen", "charging_area"])
            before = json.loads((output / "digital_twin_before.json").read_text()); after = json.loads((output / "digital_twin_after.json").read_text())
            changed = [item["room_id"] for item in after["rooms"] if item != next(row for row in before["rooms"] if row["room_id"] == item["room_id"])]
            self.assertEqual(changed, ["kitchen"])
            self.assertTrue(runtime.closed)

    def test_navigation_failure_does_not_inject_or_claim_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = FakeRuntime(("failed",)); result = run_demo(ROOT, Path(directory) / "result", runtime=runtime, dry_run=False, headless=True, timeout_seconds=5)
            self.assertFalse(result["summary"]["obstacle_spawn_success"])
            self.assertFalse(result["summary"]["anomaly_detection_success"])
            self.assertIn("Navigation to the kitchen failed", result["summary"]["failure_reason"])

    def test_observation_carries_the_strict_simulation_boundary_and_trace(self):
        regions, goals = safe_goals(ROOT); before = __import__("house_sitter_core.digital_twin", fromlist=["create_house_v1_baseline"]).create_house_v1_baseline(regions)
        observation = make_observation(before, kitchen_obstacle_spec(regions, goals["kitchen"], "trace"), {"x": 1})
        self.assertTrue(observation["synthetic"]); self.assertTrue(observation["simulated_onboard_sensor"])
        self.assertEqual(observation["observation_trace"]["robot_pose"], {"x": 1})

    def test_cli_dry_run_is_english_and_does_not_start_gui(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_final_3d_house_sitter_demo.py"), "--scenario", SCENARIO_ID, "--dry-run", "--output-dir", str(Path(directory) / "result")], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0); self.assertIn("Dry-run completed.", result.stdout)
            self.assertNotRegex(result.stdout + result.stderr, r"[\u3400-\u4dbf\u4e00-\u9fff]")

    def test_source_avoids_frozen_experiment_edits_and_global_process_cleanup(self):
        source = (ROOT / "house_sitter_core" / "final_3d_demo.py").read_text(encoding="utf-8")
        self.assertNotIn("pkill", source); self.assertNotIn("killall", source)
        self.assertIn("Nav2SimulationExecutor", source); self.assertIn("detect_anomalies", source)

    def test_house_only_preview_uses_a_real_local_runtime_contract_and_charging_goal(self):
        from house_sitter_core.final_3d_demo import launch_house_preview
        with tempfile.TemporaryDirectory() as directory:
            runtime = FakePreviewRuntime(); output = Path(directory) / "preview"
            result = launch_house_preview(ROOT, output, runtime=runtime, dry_run=False)
            self.assertTrue(result["summary"]["live_runtime_selected"])
            self.assertTrue(result["summary"]["house_world_ready"])
            self.assertTrue(result["summary"]["robot_spawned"])
            goal, _ = runtime.calls[0]
            self.assertEqual(goal.label, "charging_area")
            self.assertEqual(goal.x, 5.35); self.assertEqual(goal.y, 3.1)
            self.assertTrue((output / "runtime_commands.json").is_file())
            self.assertTrue((output / "robot_spawn.json").is_file())

    def test_normal_preview_selects_local_runtime_while_dry_run_remains_non_launching(self):
        module = __import__("house_sitter_core.final_3d_demo", fromlist=["launch_house_preview"])
        with tempfile.TemporaryDirectory() as directory:
            fake = FakePreviewRuntime()
            with mock.patch.object(module, "Local3DRuntime", return_value=fake) as runtime_class:
                result = module.launch_house_preview(ROOT, Path(directory) / "preview", dry_run=False)
            runtime_class.assert_called_once()
            self.assertTrue(result["summary"]["live_runtime_selected"])
            self.assertEqual(fake.calls[0][0].label, "charging_area")

    def test_house_only_preview_failure_is_recorded_without_claiming_spawn(self):
        class BrokenPreview(FakePreviewRuntime):
            def launch_house_and_robot(self, goal, *, headless=False):
                raise FinalDemoError("The TurtleBot4 entity did not appear in house_v1.")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"
            result = __import__("house_sitter_core.final_3d_demo", fromlist=["launch_house_preview"]).launch_house_preview(ROOT, output, runtime=BrokenPreview(), dry_run=False)
            self.assertFalse(result["summary"]["robot_spawned"])
            self.assertIn("did not appear", result["summary"]["failure_reason"])
            self.assertIn("First blocking condition", (output / "blocking_report.md").read_text())

    def test_entity_spawned_with_missing_control_is_a_preview_warning_not_a_spawn_failure(self):
        from house_sitter_core.final_3d_demo import launch_house_preview
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"
            result = launch_house_preview(ROOT, output, runtime=FakePreviewRuntime(control_ready=False), dry_run=False)
            summary = result["summary"]
            self.assertTrue(summary["robot_entity_spawned"])
            self.assertFalse(summary["robot_control_stack_ready"])
            self.assertTrue(summary["preview_available"])
            self.assertFalse(summary["navigation_available"])
            self.assertNotIn("failure_reason", summary)
            self.assertIn("stop_motor", (output / "control_readiness_blocking_report.md").read_text())

    def test_menu_option_two_invokes_the_live_preview_and_waits_for_close(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"; output.mkdir(); (output / "logs").mkdir()
            runtime = FakePreviewRuntime()
            result = {"summary": {"robot_spawned": True}, "runtime": runtime, "output_dir": output}
            arguments = cli_module.parse_args([])
            with mock.patch("builtins.input", return_value="2"), mock.patch.object(cli_module, "launch_house_preview", return_value=result) as launch:
                self.assertEqual(cli_module.main([]), 0)
            launch.assert_called_once()
            self.assertTrue(runtime.closed)

    def test_local_runtime_commands_do_not_start_nav2_or_anomaly_flow(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        _, goals = safe_goals(ROOT); commands = Local3DRuntime(ROOT, Path("/tmp/not-used")).commands(goals["charging_area"])
        text = " ".join(" ".join(command) for command in commands.values())
        self.assertIn("ros_gz_sim", text); self.assertIn("turtlebot4_spawn.launch.py", text)
        self.assertIn("world:=house_v1", text); self.assertIn("use_sim_time", text)
        self.assertNotIn("nav2.launch.py", text); self.assertNotIn("localization.launch.py", text)

    def test_entity_readiness_uses_creation_acknowledgement_and_world_scoped_query(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime, StartedProcess
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            (runtime.log_dir / "turtlebot4_spawn.log").write_text("[INFO] Entity creation successful.\n")
            process = mock.Mock(); process.poll.return_value = None
            runtime.processes = [StartedProcess(process, "gazebo"), StartedProcess(process, "turtlebot4_spawn")]
            scene = query_result(["gz", "service", "-s", "/world/house_v1/scene/info"], stdout="model { name: 'turtlebot4' }")
            with mock.patch.object(runtime, "_run", return_value=scene):
                self.assertTrue(runtime.wait_until_robot_spawned(0.01))
            self.assertEqual(runtime.detected_entity_name, "turtlebot4")
            self.assertEqual(runtime.entity_verification_method, "world_scene_info_service")

    def test_spawn_diagnostics_does_not_reclassify_a_control_warning_as_a_spawn_failure(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime, StartedProcess
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            (runtime.log_dir / "turtlebot4_spawn.log").write_text("[INFO] Entity creation successful.\n[turtlebot4_node] [ERROR] Service stop_motor unavailable.\n")
            process = mock.Mock(); process.poll.return_value = None
            runtime.processes = [StartedProcess(process, "turtlebot4_spawn")]
            runtime.detected_entity_name = "turtlebot4"
            _, goals = safe_goals(ROOT)
            with mock.patch.object(runtime, "wait_until_house_ready", return_value=True), mock.patch.object(runtime, "_run", side_effect=[query_result(stdout=""), query_result(stdout="/clock\n/scan\n/cmd_vel\n"), query_result(stdout="/model/turtlebot4/cmd_vel\n")]):
                record = runtime.spawn_diagnostics(goals["charging_area"])
            self.assertIsNone(record["first_error"])
            self.assertTrue(record["entity_creation_reported_success"])
            self.assertTrue(record["robot_entity_spawned"])
            self.assertFalse(record["robot_control_stack_ready"])
            self.assertIn("Service stop_motor unavailable.", record["control_readiness_warnings"])
            self.assertEqual(record["requested_entity_name"], "turtlebot4")

    def test_creation_acknowledgement_is_sufficient_when_the_auxiliary_query_is_unavailable(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime, StartedProcess
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            (runtime.log_dir / "turtlebot4_spawn.log").write_text("[INFO] Entity creation successful.\n")
            process = mock.Mock(); process.poll.return_value = None
            runtime.processes = [StartedProcess(process, "turtlebot4_spawn")]
            _, goals = safe_goals(ROOT)
            runtime.entity_verification_attempted = True
            runtime.entity_verification_method = "creation_acknowledgement"
            runtime.entity_query_timed_out = True
            runtime.entity_query_result = query_result(["gz", "service"], timed_out=True, exit_code=-15, error="timed out")
            with mock.patch.object(runtime, "wait_until_house_ready", return_value=True), mock.patch.object(runtime, "_run", side_effect=[query_result(stdout=""), query_result(stdout="/clock\n/scan\n/cmd_vel\n"), query_result(stdout="/model/turtlebot4/cmd_vel\n")]):
                record = runtime.spawn_diagnostics(goals["charging_area"])
            self.assertTrue(record["entity_creation_reported_success"])
            self.assertTrue(record["robot_entity_spawned"])
            self.assertFalse(record["entity_query_confirmed"])
            self.assertTrue(record["entity_query_timed_out"])
            self.assertEqual(record["entity_verification_method"], "creation_acknowledgement")

    def test_auxiliary_query_timeout_is_terminated_recorded_and_not_raised(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        class TimedOutProcess:
            pid = 12345
            returncode = -15
            calls = 0
            def communicate(self, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["gz", "service"], timeout, output="partial", stderr="waiting")
                return ("", "")
            def poll(self): return self.returncode
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            process = TimedOutProcess()
            with mock.patch("subprocess.Popen", return_value=process), mock.patch("os.killpg") as kill:
                result = runtime._run(["gz", "service"], 0.01)
            self.assertTrue(result.timed_out)
            self.assertTrue(result.completed)
            kill.assert_called_once()
            self.assertIn("partial", result.stdout)
            self.assertIn("timed_out", (runtime.log_dir / "auxiliary_queries.jsonl").read_text())

    def test_preview_retains_success_when_entity_query_times_out(self):
        from house_sitter_core.final_3d_demo import launch_house_preview
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"
            result = launch_house_preview(ROOT, output, runtime=FakePreviewRuntime(control_ready=False, query_timed_out=True), dry_run=False)
            summary = result["summary"]
            self.assertTrue(summary["robot_entity_spawned"])
            self.assertTrue(summary["entity_creation_reported_success"])
            self.assertTrue(summary["entity_query_timed_out"])
            self.assertTrue(summary["preview_available"])
            self.assertFalse(summary["navigation_available"])
            self.assertNotIn("failure_reason", summary)
            record = json.loads((output / "robot_spawn.json").read_text())
            self.assertTrue(record["robot_entity_spawned"])
            self.assertEqual(record["entity_verification_method"], "creation_acknowledgement")
            self.assertTrue(record["entity_query_timed_out"])

    def test_cli_default_error_guard_hides_timeout_tracebacks(self):
        output = io.StringIO(); errors = io.StringIO()
        with mock.patch("builtins.input", return_value="2"), mock.patch.object(cli_module, "launch_house_preview", side_effect=subprocess.TimeoutExpired(["gz", "model", "--list"], 5.0)), redirect_stdout(output), redirect_stderr(errors):
            self.assertEqual(cli_module.main([]), 2)
        self.assertNotIn("Traceback", output.getvalue() + errors.getvalue())
        self.assertIn("timed out", errors.getvalue())

    def test_full_demo_fails_closed_before_navigation_when_stop_motor_is_unavailable(self):
        class NoControlRuntime(FakeRuntime):
            def ready(self, timeout_seconds):
                return {"/clock": True, "/scan": True, "/odom": True, "/tf": True, "/tf_static": True, "/map": True, "/amcl_pose": True, "/navigate_to_pose": True, "/stop_motor": False}
        with tempfile.TemporaryDirectory() as directory:
            runtime = NoControlRuntime()
            result = run_demo(ROOT, Path(directory) / "result", runtime=runtime, dry_run=False, headless=True, timeout_seconds=5)
            self.assertIn("control services are not ready", result["summary"]["failure_reason"])
            self.assertEqual(runtime.goals, [])


if __name__ == "__main__":
    unittest.main()
