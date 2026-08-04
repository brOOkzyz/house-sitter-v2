"""Behavior tests for deterministic simulation skill state transitions."""

import unittest

from house_sitter_core.home_simulation_state import HomeSimulationState
from house_sitter_core.skill_artifacts import build_skill_run
from house_sitter_core.skill_planner import SkillPlanningError, compile_skill_plan, create_skill_request
from house_sitter_core.skill_runtime import SkillRuntimeError, execute_skill_plan, resume_skill_plan
from tests.skill_test_support import demo_artifacts


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def run_skill(self, name="patrol_home", parameters=None, *, state=None, preview=False, **request_kwargs):
        request = create_skill_request(name, parameters or {}, **request_kwargs)
        plan = compile_skill_plan(request, self.regions, self.goals, state)
        result, events = execute_skill_plan(plan, request, state, preview_only=preview)
        return request, plan, result, events

    def test_success_path_and_logical_events_are_continuous(self):
        _, plan, result, events = self.run_skill()
        self.assertEqual(result["overall_status"], "succeeded")
        self.assertEqual(result["succeeded_steps"], plan["total_steps"])
        self.assertEqual(result["failed_steps"], 0)
        self.assertEqual([event["logical_event_order"] for event in events], list(range(1, len(events) + 1)))
        for step in result["steps"]:
            self.assertEqual(step["status"], "succeeded")
            self.assertIs(step["simulation_only"], True)
            self.assertIs(step["review_only"], True)
            self.assertIs(step["executable"], False)
        for event in events:
            self.assertIs(event["synthetic"], True)
            self.assertIs(event["review_only"], True)
            self.assertIs(event["simulation_only"], True)
            self.assertIs(event["executable"], False)

    def test_representative_routines_and_item_services_complete_logically(self):
        for name in ("bedtime_routine", "security_check_routine"):
            with self.subTest(skill=name):
                _, _, result, _ = self.run_skill(name)
                self.assertEqual(result["overall_status"], "succeeded")
        _, _, delivered, _ = self.run_skill("deliver_item", {"item": "medicine", "source": "kitchen", "destination": "bedroom"})
        self.assertEqual(delivered["state"]["simulated_items"]["medicine"], "bedroom")
        _, _, returned, _ = self.run_skill("fetch_and_return", {"item": "keys", "source": "kitchen"}, current_region="living_room")
        self.assertEqual(returned["state"]["current_region"], "living_room")
        self.assertEqual(returned["state"]["simulated_items"]["keys"], "living_room")

    def test_simulated_failure_cancels_downstream_steps(self):
        _, _, result, _ = self.run_skill(injected_events={"fail_step_order": 3})
        self.assertEqual([step["status"] for step in result["steps"]], ["succeeded", "succeeded", "failed", "cancelled", "cancelled", "cancelled", "cancelled", "cancelled"])
        self.assertEqual(result["overall_status"], "failed")
        self.assertEqual(result["failed_steps"], 1)
        self.assertEqual(result["cancelled_steps"], 5)

    def test_timeout_and_equality_boundary_do_not_use_real_time(self):
        controls = {"timeout_step_order": 3, "timeout_seconds": 5.0, "simulated_duration_seconds": 8.0}
        _, _, result, events = self.run_skill(injected_events=controls)
        self.assertEqual(result["steps"][2]["status"], "timed_out")
        self.assertEqual(result["steps"][2]["terminal_reason"], "timeout_exceeded")
        for step in result["steps"][3:]:
            self.assertEqual(step["status"], "cancelled")
            self.assertEqual(step["terminal_reason"], "upstream_timeout")
            self.assertEqual(step["interruption_reason"], "upstream_timeout")
        self.assertTrue(all(event["reason"] == "upstream_timeout" for event in events if event["status"] == "cancelled"))
        self.assertEqual(result["overall_status"], "timed_out")
        self.assertFalse(any("time" in event and event["time"] for event in events))
        controls["simulated_duration_seconds"] = 5.0
        _, _, equal_result, _ = self.run_skill(injected_events=controls)
        self.assertEqual(equal_result["overall_status"], "succeeded")

    def test_timeout_suffix_reason_is_correct_at_first_and_last_step(self):
        for step_order in (1, 8):
            controls = {"timeout_step_order": step_order, "timeout_seconds": 5.0, "simulated_duration_seconds": 8.0}
            _, _, result, events = self.run_skill(injected_events=controls)
            with self.subTest(step_order=step_order):
                self.assertEqual(result["overall_status"], "timed_out")
                self.assertEqual(result["steps"][step_order - 1]["terminal_reason"], "timeout_exceeded")
                for suffix in result["steps"][step_order:]:
                    self.assertEqual(suffix["terminal_reason"], "upstream_timeout")
                    self.assertEqual(suffix["interruption_reason"], "upstream_timeout")
                self.assertTrue(all(event["reason"] == "upstream_timeout" for event in events if event["status"] == "cancelled"))

    def test_every_emitted_event_path_keeps_strict_boundary_flags(self):
        runs = [
            self.run_skill(),
            self.run_skill(injected_events={"fail_step_order": 3}),
            self.run_skill(injected_events={"timeout_step_order": 3, "timeout_seconds": 5, "simulated_duration_seconds": 8}),
            self.run_skill(injected_events={"cancel_before_step": 3}),
            self.run_skill("retry_failed_step"),
            self.run_skill("skip_failed_step"),
        ]
        preempt = create_skill_request("patrol_home", injected_events={"preempt_at_step": 3, "alarm_region": "kitchen"})
        _, _, preempt_events, _ = build_skill_run(preempt, self.regions, self.goals)
        for _, _, _, events in runs:
            self.assertTrue(events)
            for event in events:
                self.assertIs(event["synthetic"], True)
                self.assertIs(event["review_only"], True)
                self.assertIs(event["simulation_only"], True)
                self.assertIs(event["executable"], False)
        for event in preempt_events:
            self.assertIs(event["synthetic"], True)
            self.assertIs(event["review_only"], True)
            self.assertIs(event["simulation_only"], True)
            self.assertIs(event["executable"], False)

    def test_user_cancel_before_step_cancels_that_step_and_suffix(self):
        _, _, result, _ = self.run_skill(injected_events={"cancel_before_step": 5})
        self.assertEqual([step["status"] for step in result["steps"][:4]], ["succeeded"] * 4)
        self.assertEqual([step["status"] for step in result["steps"][4:]], ["cancelled"] * 4)
        self.assertEqual(result["terminal_reason"], "user_requested_cancel")

    def test_emergency_preemption_cancels_primary_and_runs_emergency(self):
        request = create_skill_request("patrol_home", injected_events={"preempt_at_step": 3, "alarm_region": "kitchen", "alarm_type": "simulated_alarm"})
        _, result, events, _ = build_skill_run(request, self.regions, self.goals)
        self.assertEqual(result["overall_status"], "cancelled")
        self.assertEqual(result["terminal_reason"], "emergency_preemption")
        self.assertEqual(result["steps"][2]["status"], "cancelled")
        self.assertEqual(result["emergency_task"]["overall_status"], "succeeded")
        self.assertEqual(result["emergency_task"]["skill_name"], "emergency_task_preemption")
        self.assertEqual([event["logical_event_order"] for event in events], list(range(1, len(events) + 1)))
        self.assertIn("emergency", {event["task_scope"] for event in events})

    def test_one_retry_recovers_and_retry_limit_is_enforced(self):
        _, _, recovered, recovered_events = self.run_skill("retry_failed_step")
        self.assertEqual(recovered["overall_status"], "succeeded")
        self.assertEqual(recovered["steps"][0]["attempt"], 2)
        self.assertEqual(recovered["steps"][0]["terminal_reason"], "recovered_after_retry")
        self.assertEqual(max(event["attempt"] for event in recovered_events), 2)
        _, _, exhausted, _ = self.run_skill("retry_failed_step", injected_events={"retry_exhausted": True})
        self.assertEqual(exhausted["overall_status"], "failed")
        self.assertEqual(exhausted["steps"][0]["terminal_reason"], "retry_limit_exceeded")
        self.assertEqual(exhausted["steps"][1]["status"], "cancelled")
        disabled_request = create_skill_request("retry_failed_step", policy_overrides={"maximum_retry_attempts": 0})
        disabled_plan = compile_skill_plan(disabled_request, self.regions, self.goals)
        disabled, _ = execute_skill_plan(disabled_plan, disabled_request)
        self.assertEqual(disabled["overall_status"], "failed")
        self.assertEqual(disabled["steps"][0]["attempt"], 1)

    def test_skip_is_allowed_only_for_noncritical_steps(self):
        _, _, skipped, _ = self.run_skill("skip_failed_step")
        self.assertEqual(skipped["overall_status"], "succeeded")
        self.assertEqual(skipped["steps"][0]["status"], "skipped")
        self.assertEqual(skipped["skipped_steps"], 1)
        _, _, critical, _ = self.run_skill(injected_events={"fail_step_order": 1, "recovery_action": "skip"})
        self.assertEqual(critical["overall_status"], "failed")
        self.assertEqual(critical["steps"][0]["terminal_reason"], "critical_step_failure")

    def test_initial_and_mid_task_low_battery_abort_return_to_charger(self):
        low_state = HomeSimulationState(current_region="living_room", battery_percent=10.0)
        _, _, initial, _ = self.run_skill(state=low_state, battery_percent=10.0)
        self.assertEqual(initial["overall_status"], "cancelled")
        self.assertEqual(initial["terminal_reason"], "low_battery_abort")
        self.assertEqual(len(initial["recovery_steps"]), 1)
        self.assertEqual(initial["recovery_steps"][0]["label"], "charging_area")
        self.assertEqual(initial["state"]["current_region"], "charging_area")
        _, _, middle, _ = self.run_skill(injected_events={"low_battery_at_step": 3})
        self.assertEqual(middle["overall_status"], "cancelled")
        self.assertEqual(middle["steps"][2]["terminal_reason"], "low_battery_abort")
        self.assertEqual(middle["recovery_steps"][0]["status"], "succeeded")

    def test_charge_then_resume_uses_checkpoint_and_simulated_battery_only(self):
        state = HomeSimulationState(current_region="bedroom", battery_percent=12.0)
        _, _, result, _ = self.run_skill("charge_then_resume", state=state, battery_percent=12.0)
        self.assertEqual(result["overall_status"], "succeeded")
        self.assertGreaterEqual(result["state"]["battery_percent"], 99.0)
        self.assertIn("charge-resume-001", result["state"]["checkpoints"])

    def test_pause_checkpoint_and_resume_execute_only_remaining_suffix(self):
        state = HomeSimulationState()
        request = create_skill_request("patrol_home", injected_events={"pause_after_step": 2})
        plan = compile_skill_plan(request, self.regions, self.goals, state)
        paused, _ = execute_skill_plan(plan, request, state)
        self.assertEqual(paused["overall_status"], "cancelled")
        self.assertEqual(paused["checkpoint"]["next_step_order"], 3)
        resumed, events = resume_skill_plan(plan, request, paused["checkpoint"], state)
        self.assertEqual(resumed["overall_status"], "succeeded")
        self.assertEqual(resumed["total_steps"], plan["total_steps"] - 2)
        self.assertEqual([step["original_step_order"] for step in resumed["steps"]], list(range(3, plan["total_steps"] + 1)))
        self.assertEqual([event["logical_event_order"] for event in events], list(range(1, len(events) + 1)))

    def test_task_queue_uses_allowlisted_emergency_priority_unique_ids_and_fifo(self):
        state = HomeSimulationState()
        for request_id, queued_skill, priority in (
            ("normal-1", "patrol_home", 99),
            ("normal-2", "inspect_area", 99),
            ("emergency", "emergency_response", 0),
            ("low", "task_status_report", 10),
        ):
            request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id=request_id, priority=priority)
            plan = compile_skill_plan(request, self.regions, self.goals, state)
            execute_skill_plan(plan, request, state)
        queue = state.ordered_queue()
        self.assertEqual([task["request_id"] for task in queue], ["emergency", "normal-1", "normal-2", "low"])
        self.assertEqual([task["task_id"] for task in queue], ["task-000003", "task-000001", "task-000002", "task-000004"])
        self.assertEqual(queue[0]["priority"], 100)
        self.assertEqual(queue[1]["insertion_order"], 1)
        self.assertEqual(queue[2]["insertion_order"], 2)
        with self.assertRaisesRegex(SkillPlanningError, "0 through 99"):
            create_skill_request("queue_task", {"queued_skill": "patrol_home"}, priority=100)

        for request_id, queued_skill, priority in (
            ("safe-wait", "safe_wait", 0),
            ("guard", "restricted_area_guard", 50),
            ("reject", "unsafe_goal_rejection", 99),
            ("nearest", "find_nearest_safe_zone", 0),
        ):
            request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id=request_id, priority=priority)
            plan = compile_skill_plan(request, self.regions, self.goals, state)
            execute_skill_plan(plan, request, state)
        by_request = {task["request_id"]: task for task in state.queued_tasks}
        self.assertEqual(by_request["safe-wait"]["priority"], 0)
        self.assertEqual(by_request["guard"]["priority"], 50)
        self.assertEqual(by_request["reject"]["priority"], 99)
        self.assertEqual(by_request["nearest"]["priority"], 0)

    def test_queue_task_ids_are_generated_uniquely_even_for_duplicate_request_ids(self):
        state = HomeSimulationState()
        for queued_skill in ("patrol_home", "task_status_report", "inspect_area"):
            request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id="same-request", priority=50)
            plan = compile_skill_plan(request, self.regions, self.goals, state)
            execute_skill_plan(plan, request, state)
        self.assertEqual([task["task_id"] for task in state.queued_tasks], ["task-000001", "task-000002", "task-000003"])
        self.assertTrue(all(task["request_id"] == "same-request" for task in state.queued_tasks))

        default_state = HomeSimulationState()
        for _ in range(3):
            request = create_skill_request("queue_task", {"queued_skill": "patrol_home"})
            plan = compile_skill_plan(request, self.regions, self.goals, default_state)
            execute_skill_plan(plan, request, default_state)
        self.assertEqual([task["task_id"] for task in default_state.queued_tasks], ["task-000001", "task-000002", "task-000003"])

    def test_emergency_queue_allowlist_and_normal_safety_priority_changes(self):
        state = HomeSimulationState()
        for index, queued_skill in enumerate(("emergency_response", "emergency_task_preemption"), start=1):
            for priority in (0, 50, 99):
                request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id=f"emergency-{index}-{priority}", priority=priority)
                plan = compile_skill_plan(request, self.regions, self.goals, state)
                execute_skill_plan(plan, request, state)
                self.assertEqual(state.queued_tasks[-1]["priority"], 100)
        for request_id, queued_skill, priority in (
            ("safe-wait-changeable", "safe_wait", 0),
            ("guard-changeable", "restricted_area_guard", 50),
            ("reject-changeable", "unsafe_goal_rejection", 99),
            ("nearest-changeable", "find_nearest_safe_zone", 20),
            ("alarm-source-changeable", "go_to_alarm_source", 30),
            ("waiting-changeable", "go_to_safe_waiting_area", 40),
        ):
            request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id=request_id, priority=priority)
            plan = compile_skill_plan(request, self.regions, self.goals, state)
            execute_skill_plan(plan, request, state)
            task_id = state.queued_tasks[-1]["task_id"]
            self.assertEqual(state.queued_tasks[-1]["priority"], priority)
            change = create_skill_request("change_task_priority", {"task_id": task_id, "new_priority": 1}, request_id=f"change-{request_id}")
            changed_plan = compile_skill_plan(change, self.regions, self.goals, state)
            changed, _ = execute_skill_plan(changed_plan, change, state)
            self.assertEqual(changed["overall_status"], "succeeded")
            self.assertEqual(next(task for task in state.queued_tasks if task["task_id"] == task_id)["priority"], 1)
        emergency_id = next(task["task_id"] for task in state.queued_tasks if task["skill_name"] == "emergency_response")
        immutable = create_skill_request("change_task_priority", {"task_id": emergency_id, "new_priority": 1})
        immutable_plan = compile_skill_plan(immutable, self.regions, self.goals, state)
        self.assertEqual(immutable_plan["reason_code"], "EMERGENCY_PRIORITY_IMMUTABLE")

    def test_change_task_priority_mutates_only_one_queued_normal_task(self):
        state = HomeSimulationState()
        for request_id, queued_skill, priority in (("one", "patrol_home", 20), ("two", "inspect_area", 50), ("three", "task_status_report", 20)):
            request = create_skill_request("queue_task", {"queued_skill": queued_skill}, request_id=request_id, priority=priority)
            plan = compile_skill_plan(request, self.regions, self.goals, state)
            execute_skill_plan(plan, request, state)
        one_id = state.queued_tasks[0]["task_id"]
        two_id = state.queued_tasks[1]["task_id"]
        request = create_skill_request("change_task_priority", {"task_id": one_id, "new_priority": 75}, request_id="change")
        plan = compile_skill_plan(request, self.regions, self.goals, state)
        result, _ = execute_skill_plan(plan, request, state)
        self.assertEqual(result["overall_status"], "succeeded")
        self.assertEqual([task["request_id"] for task in result["state"]["queued_tasks"]], ["one", "two", "three"])
        changed = next(task for task in result["state"]["queued_tasks"] if task["task_id"] == one_id)
        self.assertEqual(changed["priority"], 75)
        self.assertEqual(plan["steps"][0]["details"]["old_priority"], 20)
        self.assertEqual(plan["steps"][0]["details"]["queue_order_before"][0]["request_id"], "two")
        self.assertEqual(result["steps"][0]["details"]["queue_order_after"][0]["task_id"], one_id)
        missing = create_skill_request("change_task_priority", {"task_id": "missing", "new_priority": 1}, request_id="change-missing")
        missing_plan = compile_skill_plan(missing, self.regions, self.goals, state)
        missing_result, _ = execute_skill_plan(missing_plan, missing, state)
        self.assertEqual(missing_result["overall_status"], "failed")
        self.assertEqual(missing_plan["reason_code"], "TASK_NOT_FOUND")
        emergency = create_skill_request("queue_task", {"queued_skill": "emergency_task_preemption"}, request_id="emergency", priority=0)
        emergency_plan = compile_skill_plan(emergency, self.regions, self.goals, state)
        execute_skill_plan(emergency_plan, emergency, state)
        emergency_id = next(task["task_id"] for task in state.queued_tasks if task["request_id"] == "emergency")
        for new_priority in (0, 50, 99):
            rejected = create_skill_request("change_task_priority", {"task_id": emergency_id, "new_priority": new_priority}, request_id=f"change-emergency-{new_priority}")
            rejected_plan = compile_skill_plan(rejected, self.regions, self.goals, state)
            rejected_result, _ = execute_skill_plan(rejected_plan, rejected, state)
            self.assertEqual(rejected_result["overall_status"], "failed")
            self.assertEqual(rejected_plan["reason_code"], "EMERGENCY_PRIORITY_IMMUTABLE")
        state.active_task = two_id
        active = create_skill_request("change_task_priority", {"task_id": two_id, "new_priority": 1}, request_id="change-active")
        active_plan = compile_skill_plan(active, self.regions, self.goals, state)
        active_result, _ = execute_skill_plan(active_plan, active, state)
        self.assertEqual(active_plan["reason_code"], "ACTIVE_TASK_PRIORITY_IMMUTABLE")
        self.assertEqual(active_result["overall_status"], "failed")

    def test_target_plan_wrappers_preview_without_state_or_runtime_mutation(self):
        state = HomeSimulationState(current_region="charging_area", battery_percent=66.0)
        before = state.snapshot()
        request = create_skill_request("preview_skill_plan", {"target_skill": "patrol_home"})
        plan, result, events, _ = build_skill_run(request, self.regions, self.goals, state=state)
        self.assertEqual(plan["target_plan"]["target_skill"], "patrol_home")
        self.assertEqual(result["execution_mode"], "preview_only")
        self.assertIsNone(result["overall_status"])
        self.assertEqual(events, [])
        self.assertEqual([step["status"] for step in result["steps"]], ["pending"] * plan["total_steps"])
        self.assertEqual(state.snapshot(), before)

    def test_room_check_summary_is_cancelled_after_an_upstream_failure(self):
        request = create_skill_request("check_all_rooms", injected_events={"fail_step_order": 3})
        plan = compile_skill_plan(request, self.regions, self.goals)
        result, _ = execute_skill_plan(plan, request)
        self.assertEqual(plan["steps"][-1]["action_type"], "report_home_check_summary")
        self.assertEqual(result["steps"][-1]["status"], "cancelled")
        self.assertEqual(result["steps"][-1]["terminal_reason"], "upstream_failure")

    def test_preview_only_keeps_steps_pending_and_emits_no_events(self):
        _, plan, result, events = self.run_skill(preview=True)
        self.assertIsNone(result["overall_status"])
        self.assertEqual(result["execution_mode"], "preview_only")
        self.assertEqual(events, [])
        self.assertEqual([step["status"] for step in result["steps"]], ["pending"] * plan["total_steps"])

    def test_invalid_injected_controls_use_specific_runtime_error(self):
        for events in (
            {"fail_step_order": True},
            {"cancel_before_step": 999},
            {"timeout_step_order": 1},
            {"timeout_step_order": 1, "timeout_seconds": float("nan"), "simulated_duration_seconds": 2},
            {"retry_exhausted": 1},
        ):
            request = create_skill_request("patrol_home", injected_events=events)
            plan = compile_skill_plan(request, self.regions, self.goals)
            with self.subTest(events=events), self.assertRaises(SkillRuntimeError):
                execute_skill_plan(plan, request)


if __name__ == "__main__":
    unittest.main()
