"""Focused policy tests for the simulation-only skill layer."""

import unittest

from house_sitter_core.home_simulation_state import (
    CRITICAL_BATTERY_THRESHOLD_PERCENT,
    LOW_BATTERY_THRESHOLD_PERCENT,
    MAX_RETRY_ATTEMPTS,
    HomeSimulationState,
)
from house_sitter_core.skill_planner import SkillPlanningError, SkillPolicyError, compile_skill_plan, create_skill_request
from house_sitter_core.skill_runtime import execute_skill_plan
from tests.skill_test_support import demo_artifacts


class SkillPolicyTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def build(self, name, parameters=None, *, state=None, **kwargs):
        request = create_skill_request(name, parameters or {}, **kwargs)
        plan = compile_skill_plan(request, self.regions, self.goals, state)
        return request, plan

    def test_restricted_area_guard_returns_machine_and_human_rejection(self):
        state = HomeSimulationState(restricted_regions=("bedroom",))
        request, plan = self.build("restricted_area_guard", {"area": "bedroom"}, state=state)
        self.assertEqual(plan["planning_status"], "rejected")
        details = plan["steps"][0]["details"]
        self.assertEqual(details["reason_code"], "RESTRICTED_REGION")
        self.assertIn("restricted", details["explanation"])
        result, _ = execute_skill_plan(plan, request, state)
        self.assertEqual(result["overall_status"], "failed")

    def test_unsafe_goal_rejection_cannot_relabel_an_accepted_goal(self):
        request, plan = self.build("unsafe_goal_rejection", {"goal": "invented-goal"})
        self.assertEqual(plan["planning_status"], "rejected")
        self.assertEqual(plan["reason_code"], "UNACCEPTED_GOAL")
        with self.assertRaisesRegex(SkillPolicyError, "GOAL_IS_ACCEPTED"):
            self.build("unsafe_goal_rejection", {"goal": "living_room"})

    def test_fallback_fails_closed_when_current_artifact_has_no_alternative(self):
        state = HomeSimulationState(blocked_goals=("1",))
        with self.assertRaisesRegex(SkillPolicyError, "NO_ALTERNATE_SAFE_GOAL"):
            self.build("fallback_to_safe_goal", {"area": "living_room"}, state=state)

    def test_battery_thresholds_and_retry_limit_are_central_constants(self):
        self.assertEqual(LOW_BATTERY_THRESHOLD_PERCENT, 20.0)
        self.assertEqual(CRITICAL_BATTERY_THRESHOLD_PERCENT, 10.0)
        self.assertEqual(MAX_RETRY_ATTEMPTS, 1)
        _, plan = self.build("patrol_home")
        self.assertEqual(plan["policy"]["low_battery_threshold_percent"], LOW_BATTERY_THRESHOLD_PERCENT)
        self.assertEqual(plan["policy"]["maximum_retry_attempts"], MAX_RETRY_ATTEMPTS)

    def test_fixed_routine_cannot_be_silently_reordered(self):
        for name in ("bedtime_routine", "leave_home_routine", "morning_routine", "security_check_routine"):
            with self.subTest(skill=name):
                _, plan = self.build(name)
                self.assertIs(plan["policy"]["reorder_allowed"], False)
        _, reorderable = self.build("energy_efficient_order")
        self.assertIs(reorderable["policy"]["reorder_allowed"], True)

    def test_request_priority_is_always_a_normal_range_input(self):
        with self.assertRaisesRegex(SkillPlanningError, "0 through 99"):
            create_skill_request("patrol_home", priority=100)
        emergency = create_skill_request("emergency_response", priority=1, injected_events={"alarm_region": "kitchen"})
        self.assertEqual(emergency.priority, 1)

    def test_change_task_priority_rejects_invalid_normal_priority_values(self):
        state = HomeSimulationState()
        queued = create_skill_request("queue_task", {"queued_skill": "patrol_home"}, request_id="normal", priority=50)
        queued_plan = compile_skill_plan(queued, self.regions, self.goals, state)
        execute_skill_plan(queued_plan, queued, state)
        task_id = state.queued_tasks[0]["task_id"]
        for new_priority in (True, -1, 100, 1.5, "50"):
            request = create_skill_request("change_task_priority", {"task_id": task_id, "new_priority": new_priority})
            with self.subTest(new_priority=new_priority), self.assertRaisesRegex(SkillPlanningError, "PRIORITY_POLICY"):
                compile_skill_plan(request, self.regions, self.goals, state)

    def test_cancel_pause_and_resume_use_cancel_or_checkpoint_metadata(self):
        cancel_request, cancel_plan = self.build("cancel_current_task")
        cancelled, _ = execute_skill_plan(cancel_plan, cancel_request)
        self.assertEqual(cancelled["overall_status"], "cancelled")
        self.assertEqual(cancelled["terminal_reason"], "user_requested_cancel")
        state = HomeSimulationState()
        pause_request, pause_plan = self.build("pause_current_task", state=state)
        paused, _ = execute_skill_plan(pause_plan, pause_request, state)
        self.assertEqual(paused["overall_status"], "cancelled")
        self.assertIn("pause-001", paused["state"]["checkpoints"])
        resume_request, resume_plan = self.build("resume_current_task", {"checkpoint_id": "pause-001"}, state=state)
        resumed, _ = execute_skill_plan(resume_plan, resume_request, state)
        self.assertEqual(resumed["overall_status"], "succeeded")

    def test_alias_ambiguity_never_selects_first_candidate(self):
        request, plan = self.build("confirm_ambiguous_target", {"target": "room"})
        self.assertEqual(plan["planning_status"], "confirmation_required")
        result, _ = execute_skill_plan(plan, request)
        self.assertEqual(result["overall_status"], "cancelled")
        self.assertEqual(result["terminal_reason"], "confirmation_required")


if __name__ == "__main__":
    unittest.main()
