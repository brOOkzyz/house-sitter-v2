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
    FinalDemoError, SafeGoal, SCENARIO_ID, displacement_between, extract_gazebo_model_pose,
    kitchen_obstacle_spec, make_observation, parse_ros_topic_list_types, qos_settings_from_endpoint,
    preflight, run_demo, safe_goals, select_odometry_interface,
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
    def ready(self, timeout_seconds): return {name: True for name in ("/clock", "/scan", "/odom", "/tf", "/tf_static", "/map", "/amcl_pose", "/navigate_to_pose", "/robot_power", "/e_stop")}
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
    def run_motion_smoke_test(self):
        self.motion_called = getattr(self, "motion_called", 0) + 1
        return {"attempted": True, "requested_topic": "/cmd_vel", "resolved_command_topic": "/cmd_vel", "command_message_type": "geometry_msgs/msg/TwistStamped", "speed": 0.05, "duration": 1.0, "command_publish_count": 20, "zero_velocity_publish_count": 4, "control_command_published": True, "zero_velocity_sent": True, "initial_odom": {"x": 5.35, "y": 3.1}, "final_odom": {"x": 5.41, "y": 3.1}, "initial_gazebo_pose": None, "final_gazebo_pose": None, "odometry_received": True, "gazebo_pose_received": False, "displacement": 0.06, "movement_threshold": 0.01, "displacement_verified": True, "stop_verified": True, "robot_moved": True, "robot_stopped": True, "verification_source": "ros_odometry", "dds_warnings": [], "success": True, "failure_reason": None}
    def send_zero_velocity(self): self.safety_stops = getattr(self, "safety_stops", 0) + 1; return 4


class Final3DDemoTests(unittest.TestCase):
    def test_odom_resolution_uses_an_active_formal_source_not_a_dead_canonical_name(self):
        topic_types = parse_ros_topic_list_types(
            "/odom [nav_msgs/msg/Odometry]\n/sim_ground_truth_pose [nav_msgs/msg/Odometry]\n/robot/odom [nav_msgs/msg/Odometry]\n"
        )
        dead = "Type: nav_msgs/msg/Odometry\nPublisher count: 0\nSubscription count: 2\n"
        active = """Type: nav_msgs/msg/Odometry
Publisher count: 1
Node name: pose_republisher_node
Node namespace: /
Endpoint type: PUBLISHER
QoS profile:
  Reliability: BEST_EFFORT
  History (Depth): UNKNOWN
  Durability: VOLATILE
"""
        resolution = select_odometry_interface(topic_types, {"/odom": dead, "/sim_ground_truth_pose": active, "/robot/odom": dead})
        self.assertEqual(resolution["selected"]["topic"], "/sim_ground_truth_pose")
        self.assertEqual(len(resolution["candidates"]), 3)

    def test_qos_matches_best_effort_volatile_publisher_with_a_safe_unknown_history_default(self):
        settings = qos_settings_from_endpoint({"reliability": "BEST_EFFORT", "durability": "VOLATILE", "history": "UNKNOWN"})
        self.assertEqual(settings["reliability"], "BEST_EFFORT")
        self.assertEqual(settings["durability"], "VOLATILE")
        self.assertEqual(settings["history"], "KEEP_LAST")
        self.assertEqual(settings["depth"], 10)
        self.assertTrue(settings["history_fallback_used"])

    def test_gazebo_pose_extraction_and_noise_threshold_are_not_synthetic_motion(self):
        payload = '{"pose":[{"name":"wall","position":{"x":0,"y":0}},{"name":"turtlebot4","position":{"x":5.35,"y":3.1,"z":0.05}}]}'
        self.assertEqual(extract_gazebo_model_pose(payload), {"x": 5.35, "y": 3.1, "z": 0.05})
        self.assertLess(displacement_between({"x": 1.0, "y": 2.0}, {"x": 1.003, "y": 2.002}), 0.01)

    def test_gazebo_pose_reader_is_world_scoped_bounded_and_never_sets_pose(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            payload = '{"pose":[{"name":"turtlebot4","position":{"x":5.35,"y":3.1,"z":0.05}}]}'
            with mock.patch.object(runtime, "_run", return_value=query_result(stdout=payload)) as run:
                pose, topic = runtime._gazebo_pose_snapshot()
            self.assertEqual(topic, "/world/house_v1/pose/info")
            self.assertEqual(pose["x"], 5.35)
            command = run.call_args.args[0]
            self.assertIn("-n", command); self.assertIn("1", command)
            self.assertNotIn("set_pose", " ".join(command))

    def test_dds_raw_text_stays_in_logs_and_is_reduced_to_a_terminal_safe_summary(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            raw = "RTPS_TRANSPORT_SHM Error Failed init_port fastdds_port123 open_and_lock_file failed\n"
            (runtime.log_dir / "motion_verification.log").write_text(raw)
            warnings = runtime._dds_warnings()
            self.assertEqual(warnings[0]["summary"], "A DDS shared-memory transport warning was recorded.")
            self.assertNotIn("fastdds_port", warnings[0]["summary"])
            self.assertIn("fastdds_port", (runtime.log_dir / "motion_verification.log").read_text())

    def test_motion_cli_falls_closed_but_keeps_preview_open_until_enter(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"; output.mkdir(); (output / "logs").mkdir()
            runtime = FakePreviewRuntime(control_ready=True)
            runtime.run_motion_smoke_test = lambda: {**cli_module._empty_motion_record("Motion was commanded, but displacement could not be verified."), "attempted": True, "control_command_published": True, "zero_velocity_sent": True, "command_publish_count": 20, "zero_velocity_publish_count": 4}
            result = {"summary": {"robot_control_stack_ready": True}, "runtime": runtime, "output_dir": output}
            stream = io.StringIO()
            with mock.patch.object(cli_module, "launch_house_preview", return_value=result), mock.patch("builtins.input", return_value="") as wait, redirect_stdout(stream):
                self.assertEqual(cli_module._run_short_motion_test(cli_module.parse_args(["--motion-test"])), 2)
            wait.assert_called_once()
            self.assertIn("The Gazebo preview will remain open.", stream.getvalue())
            record = json.loads((output / "motion_test.json").read_text())
            self.assertFalse(record["success"])
            self.assertTrue(record["control_command_published"])

    def test_motion_cli_ctrl_c_publishes_an_additional_safety_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"; output.mkdir(); (output / "logs").mkdir()
            runtime = FakePreviewRuntime(control_ready=True)
            result = {"summary": {"robot_control_stack_ready": True}, "runtime": runtime, "output_dir": output}
            with mock.patch.object(cli_module, "launch_house_preview", return_value=result), mock.patch("builtins.input", side_effect=KeyboardInterrupt):
                self.assertEqual(cli_module._run_short_motion_test(cli_module.parse_args(["--motion-test"])), 0)
            self.assertEqual(runtime.safety_stops, 1)

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

    def test_preview_dry_run_needs_no_menu_input_and_writes_control_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result"
            result = subprocess.run([sys.executable, str(SCRIPT), "--dry-run", "--output-dir", str(output)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertIn("Dry-run completed.", result.stdout)
            self.assertTrue((output / "control_preflight.json").is_file())
            self.assertTrue((output / "control_interfaces.json").is_file())

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
            with mock.patch.object(runtime, "wait_until_house_ready", return_value=True), mock.patch.object(runtime, "_control_readiness", return_value={"robot_simulation_interfaces_ready": False, "stop_motor_service_ready": False, "robot_control_stack_ready": False, "control_topics": [], "control_services": [], "control_readiness_warnings": ["Optional TurtleBot4 HMI power service stop_motor is unavailable."]}):
                record = runtime.spawn_diagnostics(goals["charging_area"])
            self.assertIsNone(record["first_error"])
            self.assertTrue(record["entity_creation_reported_success"])
            self.assertTrue(record["robot_entity_spawned"])
            self.assertFalse(record["robot_control_stack_ready"])
            self.assertIn("Optional TurtleBot4 HMI power service stop_motor is unavailable.", record["control_readiness_warnings"])
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
            with mock.patch.object(runtime, "wait_until_house_ready", return_value=True), mock.patch.object(runtime, "_control_readiness", return_value={"robot_simulation_interfaces_ready": False, "stop_motor_service_ready": False, "robot_control_stack_ready": False, "control_topics": [], "control_services": [], "control_readiness_warnings": []}):
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

    def test_full_demo_fails_closed_before_navigation_when_required_control_is_unavailable(self):
        class NoControlRuntime(FakeRuntime):
            def ready(self, timeout_seconds):
                return {"/clock": True, "/scan": True, "/odom": True, "/tf": True, "/tf_static": True, "/map": True, "/amcl_pose": True, "/navigate_to_pose": True, "/robot_power": False, "/e_stop": True}
        with tempfile.TemporaryDirectory() as directory:
            runtime = NoControlRuntime()
            result = run_demo(ROOT, Path(directory) / "result", runtime=runtime, dry_run=False, headless=True, timeout_seconds=5)
            self.assertIn("control services are not ready", result["summary"]["failure_reason"])
            self.assertEqual(runtime.goals, [])

    def test_control_readiness_uses_real_create3_services_not_optional_stop_motor(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            def command_result_for(command, timeout):
                key = " ".join(command)
                if key == "ros2 topic list":
                    return query_result(command, stdout="/clock\n/tf\n/tf_static\n/odom\n/cmd_vel\n/diffdrive_controller/cmd_vel\n/joint_states\n")
                if key == "ros2 topic list -t":
                    return query_result(command, stdout="/clock [rosgraph_msgs/msg/Clock]\n/tf [tf2_msgs/msg/TFMessage]\n/tf_static [tf2_msgs/msg/TFMessage]\n/odom [nav_msgs/msg/Odometry]\n/cmd_vel [geometry_msgs/msg/TwistStamped]\n/diffdrive_controller/cmd_vel [geometry_msgs/msg/TwistStamped]\n")
                if key == "ros2 topic info /odom --verbose":
                    return query_result(command, stdout="Type: nav_msgs/msg/Odometry\n\nPublisher count: 1\n\nNode name: pose_republisher_node\nEndpoint type: PUBLISHER\nQoS profile:\n  Reliability: BEST_EFFORT\n  History (Depth): KEEP_LAST (5)\n  Durability: VOLATILE\n")
                if key == "gz topic -l":
                    return query_result(command, stdout="/model/turtlebot4/cmd_vel\n")
                if key == "ros2 service list":
                    return query_result(command, stdout="/robot_power\n/e_stop\n")
                if key == "ros2 node list":
                    return query_result(command, stdout="/robot_state_publisher\n/motion_control\n")
                if command[:3] == ["ros2", "service", "type"]:
                    return query_result(command, stdout={"/robot_power": "irobot_create_msgs/srv/RobotPower", "/e_stop": "irobot_create_msgs/srv/EStop"}[command[-1]])
                self.fail(f"unexpected command: {command}")
            with mock.patch.object(runtime, "_run", side_effect=command_result_for):
                result = runtime._control_readiness()
            self.assertTrue(result["robot_simulation_interfaces_ready"])
            self.assertTrue(result["robot_control_stack_ready"])
            self.assertFalse(result["stop_motor_service_ready"])
            self.assertEqual(result["missing_services"], [])
            self.assertIn("/robot_power", result["control_services"])

    def test_missing_required_create3_service_fails_control_readiness(self):
        from house_sitter_core.final_3d_demo import Local3DRuntime
        with tempfile.TemporaryDirectory() as directory:
            runtime = Local3DRuntime(ROOT, Path(directory) / "runtime"); runtime.output_dir.mkdir(); runtime.log_dir.mkdir()
            def command_result_for(command, timeout):
                key = " ".join(command)
                values = {
                    "ros2 topic list": "/clock\n/tf\n/tf_static\n/odom\n/cmd_vel\n/diffdrive_controller/cmd_vel\n/joint_states\n",
                    "ros2 topic list -t": "/odom [nav_msgs/msg/Odometry]\n/cmd_vel [geometry_msgs/msg/TwistStamped]\n/diffdrive_controller/cmd_vel [geometry_msgs/msg/TwistStamped]\n",
                    "ros2 topic info /odom --verbose": "Type: nav_msgs/msg/Odometry\nPublisher count: 1\nNode name: pose_republisher_node\nEndpoint type: PUBLISHER\nQoS profile:\n  Reliability: BEST_EFFORT\n  History (Depth): KEEP_LAST (5)\n  Durability: VOLATILE\n",
                    "gz topic -l": "/model/turtlebot4/cmd_vel\n",
                    "ros2 service list": "/robot_power\n",
                    "ros2 node list": "/robot_state_publisher\n",
                    "ros2 service type /robot_power": "irobot_create_msgs/srv/RobotPower\n",
                }
                return query_result(command, stdout=values.get(key, ""))
            with mock.patch.object(runtime, "_run", side_effect=command_result_for):
                result = runtime._control_readiness()
            self.assertFalse(result["robot_control_stack_ready"])
            self.assertIn("/e_stop", result["missing_services"])

    def test_motion_menu_runs_only_after_ready_and_records_zero_velocity(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"; output.mkdir(); (output / "logs").mkdir()
            runtime = FakePreviewRuntime(control_ready=True)
            result = {"summary": {"robot_control_stack_ready": True}, "runtime": runtime, "output_dir": output}
            arguments = cli_module.parse_args(["--motion-test"])
            with mock.patch.object(cli_module, "launch_house_preview", return_value=result), mock.patch("builtins.input", return_value=""):
                self.assertEqual(cli_module._run_short_motion_test(arguments), 0)
            record = json.loads((output / "motion_test.json").read_text())
            self.assertTrue(record["success"])
            self.assertTrue(record["zero_velocity_sent"])
            self.assertEqual(runtime.motion_called, 1)

    def test_motion_menu_rejects_unready_control_without_commanding_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "preview"; output.mkdir(); (output / "logs").mkdir()
            runtime = FakePreviewRuntime(control_ready=False)
            result = {"summary": {"robot_control_stack_ready": False}, "runtime": runtime, "output_dir": output}
            arguments = cli_module.parse_args(["--motion-test"])
            with mock.patch.object(cli_module, "launch_house_preview", return_value=result):
                self.assertEqual(cli_module._run_short_motion_test(arguments), 2)
            self.assertFalse(hasattr(runtime, "motion_called"))
            self.assertTrue(json.loads((output / "motion_test.json").read_text())["zero_velocity_sent"] is False)


if __name__ == "__main__":
    unittest.main()
