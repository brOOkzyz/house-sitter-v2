"""Tests for the optional, simulation-only Nav2 skill bridge."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import ModuleType, SimpleNamespace

from house_sitter_core.nav2_sim_bridge import FakeNavigationExecutor, NavigationGoal, NavigationOutcome, Nav2SimulationExecutor
from house_sitter_core.skill_execution_bridge import SkillExecutionBridgeError, execute_skill_in_simulation, render_execution_artifacts, write_execution_artifacts
from house_sitter_core.skill_planner import compile_skill_plan, create_skill_request
from tests.skill_test_support import ROOT, demo_artifacts, write_artifacts


SCRIPT = ROOT / "scripts" / "run_skill_in_gazebo.py"


class SkillExecutionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def plan(self, **kwargs):
        request = create_skill_request("patrol_home", **kwargs)
        return request, compile_skill_plan(request, self.regions, self.goals)

    def test_dry_run_requires_no_ros_and_sends_no_goal(self):
        request, plan = self.plan()
        fake = FakeNavigationExecutor()
        result, events = execute_skill_in_simulation(plan, request, None, dry_run=True)
        self.assertEqual(result["execution_mode"], "dry_run"); self.assertEqual(events, []); self.assertEqual(result["overall_status"], None)
        self.assertEqual(fake.sent_goals, [])

    def test_fake_success_binds_accepted_goal_and_feedback_order(self):
        request, plan = self.plan()
        fake = FakeNavigationExecutor([NavigationOutcome("succeeded", ({"distance_remaining": 2.0}, {"distance_remaining": 0.0}))])
        result, events = execute_skill_in_simulation(plan, request, fake)
        self.assertEqual(result["overall_status"], "succeeded")
        self.assertEqual(fake.sent_goals[0].goal_reference, plan["steps"][0]["goal_reference"])
        feedback = [event["feedback"] for event in events if event["status"] == "feedback"]
        self.assertEqual(feedback, [{"distance_remaining": 2.0}, {"distance_remaining": 0.0}])
        self.assertEqual(fake.sent_goals[0].frame_id, "map")

    def test_real_executor_collects_ordered_normalized_feedback_with_mock_ros(self):
        class Goal:
            def __init__(self):
                self.pose = SimpleNamespace(header=SimpleNamespace(frame_id=None), pose=SimpleNamespace(position=SimpleNamespace(x=None, y=None), orientation=SimpleNamespace(w=None)))

        class Handle:
            accepted = True
            def get_result_async(self): return SimpleNamespace(done=lambda: True, result=lambda: SimpleNamespace(status=4))
            def cancel_goal_async(self): pass

        class Client:
            def __init__(self, *args): self.callback = None
            def wait_for_server(self, **kwargs): return True
            def send_goal_async(self, message, *, feedback_callback):
                self.callback = feedback_callback
                feedback_callback(SimpleNamespace(feedback=SimpleNamespace(distance_remaining=2.0, number_of_recoveries=0)))
                feedback_callback(SimpleNamespace(feedback=SimpleNamespace(distance_remaining=0.0, number_of_recoveries=1)))
                return SimpleNamespace(result=lambda: Handle())

        client = Client()
        action = ModuleType("rclpy.action"); action.ActionClient = lambda *args: client
        rclpy = ModuleType("rclpy"); rclpy.spin_until_future_complete = lambda *args, **kwargs: None
        nav2 = ModuleType("nav2_msgs.action"); nav2.NavigateToPose = SimpleNamespace(Goal=Goal)
        node = SimpleNamespace(get_parameter=lambda name: SimpleNamespace(value=True))
        with mock.patch.dict(sys.modules, {"rclpy": rclpy, "rclpy.action": action, "nav2_msgs.action": nav2}):
            executor = Nav2SimulationExecutor(node)
            goal = NavigationGoal("living_room", 1.0, 2.0, {"canonical_label": "living_room"})
            outcome = executor.wait_for_result(executor.send_goal(goal), 5.0)
        self.assertIsNotNone(client.callback)
        self.assertEqual(outcome.feedback, ({"distance_remaining": 2.0, "number_of_recoveries": 0}, {"distance_remaining": 0.0, "number_of_recoveries": 1}))

    def test_real_executor_returns_empty_feedback_without_callbacks(self):
        class Goal:
            def __init__(self): self.pose = SimpleNamespace(header=SimpleNamespace(frame_id=None), pose=SimpleNamespace(position=SimpleNamespace(x=None, y=None), orientation=SimpleNamespace(w=None)))
        class Handle:
            accepted = True
            def get_result_async(self): return SimpleNamespace(done=lambda: True, result=lambda: SimpleNamespace(status=4))
            def cancel_goal_async(self): pass
        class Client:
            def wait_for_server(self, **kwargs): return True
            def send_goal_async(self, message, *, feedback_callback): return SimpleNamespace(result=lambda: Handle())
        action = ModuleType("rclpy.action"); action.ActionClient = lambda *args: Client()
        rclpy = ModuleType("rclpy"); rclpy.spin_until_future_complete = lambda *args, **kwargs: None
        nav2 = ModuleType("nav2_msgs.action"); nav2.NavigateToPose = SimpleNamespace(Goal=Goal)
        node = SimpleNamespace(get_parameter=lambda name: SimpleNamespace(value=True))
        with mock.patch.dict(sys.modules, {"rclpy": rclpy, "rclpy.action": action, "nav2_msgs.action": nav2}):
            executor = Nav2SimulationExecutor(node)
            outcome = executor.wait_for_result(executor.send_goal(NavigationGoal("living_room", 1.0, 2.0, {"canonical_label": "living_room"})), 5.0)
        self.assertEqual(outcome.status, "succeeded")
        self.assertEqual(outcome.feedback, ())

    def test_rejected_timeout_and_cancelled_goals_fail_closed(self):
        request, plan = self.plan()
        for outcome, status in ((NavigationOutcome("failed", reason="rejected"), "failed"), (NavigationOutcome("timed_out"), "timed_out"), (NavigationOutcome("cancelled"), "cancelled")):
            with self.subTest(status=status):
                result, _ = execute_skill_in_simulation(plan, request, FakeNavigationExecutor([outcome]))
                self.assertEqual(result["steps"][0]["status"], status)
                self.assertEqual(result["steps"][1]["status"], "cancelled")

    def test_preemption_and_bad_or_missing_goal_evidence_fail_closed(self):
        request, plan = self.plan(injected_events={"preempt_at_step": 1})
        result, _ = execute_skill_in_simulation(plan, request, FakeNavigationExecutor())
        self.assertEqual(result["terminal_reason"], "emergency_preemption")
        request, plan = self.plan(); del plan["steps"][0]["goal_reference"]
        with self.assertRaisesRegex(SkillExecutionBridgeError, "accepted safe-goal"):
            execute_skill_in_simulation(plan, request, FakeNavigationExecutor())

    def test_checkpoint_and_no_direct_velocity_or_hardware_path(self):
        request, plan = self.plan(injected_events={"pause_after_step": 1})
        result, _ = execute_skill_in_simulation(plan, request, FakeNavigationExecutor())
        self.assertEqual(result["checkpoint"]["next_step_order"], 2)
        self.assertEqual(result["steps"][1]["terminal_reason"], "paused_at_checkpoint")
        source = (ROOT / "house_sitter_core" / "nav2_sim_bridge.py").read_text(encoding="utf-8")
        self.assertNotIn("create_publisher", source); self.assertNotIn("sensor_msgs", source)

    def test_atomic_outputs_and_cli_dry_run(self):
        request, plan = self.plan(); result, events = execute_skill_in_simulation(plan, request, None, dry_run=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / "output"
            paths = write_execution_artifacts(output, render_execution_artifacts(request, plan, result, events))
            self.assertEqual(set(path.name for path in paths.values()), {"execution_request.json", "execution_plan.json", "execution_events.jsonl", "execution_result.json", "execution_report.md"})
            regions, goals = write_artifacts(root); cli = root / "cli"
            run = subprocess.run([sys.executable, str(SCRIPT), "--skill", "patrol_home", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(cli), "--dry-run"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 0, run.stderr); self.assertTrue((cli / "execution_result.json").exists())
            failed = root / "failed"
            with mock.patch.object(Path, "write_text", side_effect=OSError("write failure")):
                with self.assertRaisesRegex(OSError, "write failure"):
                    write_execution_artifacts(failed, render_execution_artifacts(request, plan, result, events))
            self.assertFalse(failed.exists())

    def test_cli_errors_no_partial_output_and_hash_seed_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions, goals = write_artifacts(root)
            bad = root / "bad"
            run = subprocess.run([sys.executable, str(SCRIPT), "--skill", "patrol_home", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(bad), "--timeout-seconds", "0"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 2); self.assertFalse(bad.exists()); self.assertNotIn("Traceback", run.stderr)
            untrusted = json.loads(goals.read_text(encoding="utf-8")); untrusted["goals"][0]["review_only"] = False
            goals.write_text(json.dumps(untrusted), encoding="utf-8")
            rejected = root / "rejected"
            run = subprocess.run([sys.executable, str(SCRIPT), "--skill", "patrol_home", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(rejected), "--dry-run"], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(run.returncode, 2); self.assertFalse(rejected.exists())
            write_artifacts(root)
            outputs = []
            for seed in ("1", "777"):
                target = root / f"seed-{seed}"; env = dict(os.environ, PYTHONHASHSEED=seed)
                run = subprocess.run([sys.executable, str(SCRIPT), "--skill", "patrol_home", "--semantic-regions", str(regions), "--safe-goals", str(goals), "--output-dir", str(target), "--dry-run"], cwd=ROOT, text=True, capture_output=True, env=env, check=False)
                self.assertEqual(run.returncode, 0, run.stderr); outputs.append((target / "execution_plan.json").read_bytes())
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
