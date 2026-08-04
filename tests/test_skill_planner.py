"""Behavior tests for simulation skill request compilation."""

import copy
import unittest

from house_sitter_core.home_simulation_state import HomeSimulationState
from house_sitter_core.skill_planner import (
    ROUTINE_ROUTES,
    SkillPlanningError,
    SkillPolicyError,
    SkillRequest,
    compile_skill_plan,
    create_skill_request,
    resolve_region_alias,
    validate_skill_artifacts,
)
from tests.skill_test_support import demo_artifacts


class SkillPlannerTests(unittest.TestCase):
    def setUp(self):
        self.regions, self.goals = demo_artifacts()

    def compile(self, name, parameters=None, **request_kwargs):
        request = create_skill_request(name, parameters or {}, **request_kwargs)
        return compile_skill_plan(request, self.regions, self.goals)

    def test_patrol_uses_real_goal_references_and_fixed_route(self):
        plan = self.compile("patrol_home")
        navigations = [step for step in plan["steps"] if step["action_type"] == "navigate_to_safe_goal"]
        self.assertEqual([step["label"] for step in navigations], list(ROUTINE_ROUTES["patrol_home"]))
        goals_by_label = {goal["canonical_label"]: goal for goal in self.goals["goals"]}
        for step in navigations:
            source = goals_by_label[step["label"]]
            reference = step["goal_reference"]
            self.assertEqual((reference["proposal_id"], reference["partition_id"]), (source["proposal_id"], source["candidate_partition_id"]))
            self.assertEqual(reference["goal_map"], {"x": source["goal"]["map_x"], "y": source["goal"]["map_y"]})
            self.assertEqual(reference["goal_pixel"], {"row": source["goal"]["pixel_row"], "column": source["goal"]["pixel_column"]})

    def test_input_array_reordering_does_not_change_label_goal_binding(self):
        request = create_skill_request("patrol_home")
        original = compile_skill_plan(request, self.regions, self.goals)
        reordered_regions = copy.deepcopy(self.regions)
        reordered_goals = copy.deepcopy(self.goals)
        reordered_regions["regions"].reverse()
        reordered_goals["goals"].reverse()
        reordered = compile_skill_plan(request, reordered_regions, reordered_goals)
        self.assertEqual(reordered, original)

    def test_routine_orders_are_fixed_and_energy_reordering_is_explicit(self):
        for name, expected in ROUTINE_ROUTES.items():
            with self.subTest(skill=name):
                plan = self.compile(name)
                route = [step["label"] for step in plan["steps"] if step["action_type"] == "navigate_to_safe_goal"]
                self.assertEqual(route, list(expected))
                self.assertFalse(plan["policy"]["reorder_allowed"])
        energy = self.compile("energy_efficient_order", current_region="charging_area")
        self.assertTrue(energy["policy"]["reorder_allowed"])
        self.assertEqual(len(energy["steps"]), 3)

    def test_check_all_rooms_has_a_distinct_synthetic_summary_step(self):
        patrol = self.compile("patrol_home")
        checks = self.compile("check_all_rooms")
        self.assertNotEqual(checks["steps"], patrol["steps"])
        summary = checks["steps"][-1]
        self.assertEqual(summary["action_type"], "report_home_check_summary")
        self.assertEqual(summary["details"]["checked_regions"], list(ROUTINE_ROUTES["check_all_rooms"]))
        for field, value in (
            ("synthetic", True),
            ("synthetic_room_check_summary", True),
            ("real_sensor_detection", False),
            ("review_only", True),
            ("simulation_only", True),
            ("executable", False),
        ):
            self.assertIs(type(summary[field]), bool)
            self.assertIs(summary[field], value)
        self.assertIs(summary["details"]["synthetic_room_check_summary"], True)
        self.assertIs(summary["details"]["real_sensor_detection"], False)
        self.assertNotIn("report_home_check_summary", [step["action_type"] for step in patrol["steps"]])

    def test_preview_and_explain_compile_real_non_recursive_target_plans(self):
        state = HomeSimulationState(current_region="charging_area", battery_percent=77.0)
        before = state.snapshot()
        preview_request = create_skill_request("preview_skill_plan", {"target_skill": "deliver_item", "item": "medicine", "source": "kitchen", "destination": "bedroom"})
        preview = compile_skill_plan(preview_request, self.regions, self.goals, state)
        self.assertEqual([step["action_type"] for step in preview["steps"]], ["navigate_to_safe_goal", "pick_item_simulated", "navigate_to_safe_goal", "handover_item_simulated"])
        self.assertEqual(preview["target_plan"]["target_skill"], "deliver_item")
        self.assertEqual(preview["target_plan"]["target_parameters"], {"item": "medicine", "source": "kitchen", "destination": "bedroom"})
        self.assertEqual(state.snapshot(), before)
        explain_request = create_skill_request("explain_skill_plan", {"target_skill": "deliver_item", "item": "medicine", "source": "kitchen", "destination": "bedroom"})
        explain = compile_skill_plan(explain_request, self.regions, self.goals, state)
        self.assertNotEqual(explain["steps"], preview["steps"])
        self.assertEqual(explain["steps"][0]["step_explanation"]["target_step_order"], 1)
        self.assertEqual(explain["steps"][0]["step_explanation"]["target_region"], "kitchen")
        for name in ("preview_skill_plan", "explain_skill_plan"):
            with self.subTest(wrapper=name), self.assertRaisesRegex(SkillPlanningError, "missing required"):
                create_skill_request(name, {"target_skill": "deliver_item"})
            with self.subTest(recursive=name), self.assertRaisesRegex(SkillPlanningError, "cannot recursively"):
                create_skill_request(name, {"target_skill": name})

    def test_item_workflow_is_explicitly_non_physical(self):
        plan = self.compile("deliver_item", {"item": "medicine", "source": "kitchen", "destination": "bedroom"})
        self.assertTrue(plan["physical_capability_required"])
        manipulation = [step for step in plan["steps"] if "item_simulated" in step["action_type"]]
        self.assertEqual([step["action_type"] for step in manipulation], ["pick_item_simulated", "handover_item_simulated"])
        for step in manipulation:
            self.assertIs(step["details"]["physical_manipulation"], False)
            self.assertIs(step["details"]["simulated_manipulation"], True)
            self.assertIs(step["simulation_only"], True)
            self.assertIs(step["executable"], False)

    def test_chinese_and_english_aliases_resolve_without_guessing(self):
        for alias, expected in (("客厅", "living_room"), ("living room", "living_room"), ("厨房", "kitchen"), ("卧室", "bedroom"), ("充电区", "charging_area")):
            with self.subTest(alias=alias):
                canonical, candidates = resolve_region_alias(alias, self.regions_by_label)
                self.assertEqual(canonical, expected)
                self.assertEqual(candidates, (expected,))
        ambiguous = self.compile("confirm_ambiguous_target", {"target": "房间"})
        self.assertEqual(ambiguous["planning_status"], "confirmation_required")
        self.assertEqual(ambiguous["reason_code"], "AMBIGUOUS_TARGET")
        self.assertEqual(ambiguous["steps"][0]["details"]["candidates"], ["living_room", "bedroom"])

    @property
    def regions_by_label(self):
        return {region["canonical_label"] for region in self.regions["regions"]}

    def test_restricted_region_and_blocked_goal_fail_closed(self):
        restricted = HomeSimulationState(restricted_regions=("bedroom",))
        request = create_skill_request("inspect_area", {"area": "bedroom"})
        with self.assertRaisesRegex(SkillPolicyError, "RESTRICTED_REGION"):
            compile_skill_plan(request, self.regions, self.goals, restricted)
        blocked = HomeSimulationState(blocked_goals=("living_room",))
        request = create_skill_request("fallback_to_safe_goal", {"area": "living_room"})
        with self.assertRaisesRegex(SkillPolicyError, "NO_ALTERNATE_SAFE_GOAL"):
            compile_skill_plan(request, self.regions, self.goals, blocked)

    def test_unblocked_replan_reuses_existing_goal_and_never_generates_coordinates(self):
        plan = self.compile("blocked_goal_replan", {"area": "living_room"})
        reference = plan["steps"][0]["goal_reference"]
        source = self.goals["goals"][0]
        self.assertEqual(reference["goal_map"], {"x": source["goal"]["map_x"], "y": source["goal"]["map_y"]})
        self.assertEqual(plan["steps"][0]["action_type"], "select_nearest_safe_goal")

    def test_nearest_area_uses_goal_map_distance_with_deterministic_tie_break(self):
        plan = self.compile("visit_nearest_area", current_region="living_room")
        source = next(goal for goal in self.goals["goals"] if goal["canonical_label"] == "living_room")["goal"]
        expected = min(
            (goal for goal in self.goals["goals"] if goal["canonical_label"] != "living_room"),
            key=lambda goal: (((goal["goal"]["map_x"] - source["map_x"]) ** 2 + (goal["goal"]["map_y"] - source["map_y"]) ** 2) ** .5, goal["demo_assignment_order"]),
        )["canonical_label"]
        self.assertEqual(plan["steps"][0]["label"], expected)
        restricted = HomeSimulationState(current_region="living_room", restricted_regions=(expected,))
        request = create_skill_request("visit_nearest_area", current_region="living_room")
        alternate = compile_skill_plan(request, self.regions, self.goals, restricted)
        self.assertNotEqual(alternate["steps"][0]["label"], expected)

    def test_emergency_requires_an_injected_simulated_alarm(self):
        with self.assertRaisesRegex(SkillPolicyError, "SIMULATED_ALARM_REQUIRED"):
            self.compile("emergency_response")
        plan = self.compile("emergency_response", injected_events={"alarm_region": "厨房", "alarm_type": "simulated_smoke"})
        self.assertEqual(plan["steps"][0]["label"], "kitchen")
        alarm = next(step for step in plan["steps"] if step["action_type"] == "check_alarm_simulated")
        self.assertIs(alarm["details"]["real_sensor_detection"], False)

    def test_strict_identity_source_flags_and_evidence_are_reused(self):
        variants = []
        bad = copy.deepcopy(self.goals); bad["map_identity"]["width"] += 1; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["candidate_partition_id"] = "wrong"; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["review_only"] = 1; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["simulation_only"] = "true"; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["executable"] = 0; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["raster_safety_evidence"]["occupied_count"] = 1; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["polygon_validation_passed"] = False; variants.append(bad)
        bad = copy.deepcopy(self.goals); bad["goals"][0]["faster_safety_passed"] = False; variants.append(bad)
        for document in variants:
            with self.subTest(document=document), self.assertRaises(SkillPlanningError):
                validate_skill_artifacts(self.regions, document)

    def test_self_claimed_trust_fields_do_not_override_invalid_evidence(self):
        bad = copy.deepcopy(self.goals)
        bad["authenticated"] = True
        bad["trusted"] = True
        bad["goals"][0]["raster_safety_evidence"]["passed"] = False
        with self.assertRaisesRegex(SkillPlanningError, "passed"):
            validate_skill_artifacts(self.regions, bad)

    def test_duplicate_source_goal_order_and_pixel_are_rejected(self):
        for mutate in ("source", "order", "pixel"):
            broken = copy.deepcopy(self.goals)
            broken_regions = copy.deepcopy(self.regions)
            if mutate == "source":
                broken["goals"][1]["proposal_id"] = broken["goals"][0]["proposal_id"]
                broken["goals"][1]["candidate_partition_id"] = broken["goals"][0]["candidate_partition_id"]
                matching = next(region for region in broken_regions["regions"] if region["canonical_label"] == broken["goals"][1]["canonical_label"])
                matching["proposal_id"] = broken["goals"][0]["proposal_id"]
                matching["partition_id"] = broken["goals"][0]["candidate_partition_id"]
            elif mutate == "order":
                broken["goals"][1]["goal_order"] = broken["goals"][0]["goal_order"]
            else:
                broken["goals"][1]["goal"]["pixel_row"] = broken["goals"][0]["goal"]["pixel_row"]
                broken["goals"][1]["goal"]["pixel_column"] = broken["goals"][0]["goal"]["pixel_column"]
            with self.subTest(mutate=mutate), self.assertRaises(SkillPlanningError):
                validate_skill_artifacts(broken_regions, broken)

    def test_request_numbers_and_priority_use_strict_types(self):
        for battery in (True, -1, 101, float("nan"), float("inf")):
            with self.subTest(battery=battery), self.assertRaises(SkillPlanningError):
                create_skill_request("patrol_home", battery_percent=battery)
        for priority in (True, -1, 100, 101, 1.5, "50"):
            with self.subTest(priority=priority), self.assertRaises(SkillPlanningError):
                create_skill_request("patrol_home", priority=priority)
        emergency = create_skill_request("emergency_response", injected_events={"alarm_region": "kitchen"}, priority=1)
        self.assertEqual(emergency.priority, 1)

    def test_unknown_injections_policy_overrides_and_direct_requests_fail_closed(self):
        with self.assertRaisesRegex(SkillPlanningError, "unknown injected event"):
            create_skill_request("patrol_home", injected_events={"typo_event": 1})
        with self.assertRaisesRegex(SkillPlanningError, "recovery_action"):
            create_skill_request("patrol_home", injected_events={"recovery_action": "forever"})
        with self.assertRaisesRegex(SkillPlanningError, "maximum_retry_attempts"):
            create_skill_request("patrol_home", policy_overrides={"maximum_retry_attempts": 2})
        direct = SkillRequest(request_id="direct", skill_name="go_to_safe_waiting_area", parameters={})
        with self.assertRaisesRegex(SkillPlanningError, "not normalized"):
            compile_skill_plan(direct, self.regions, self.goals)

    def test_checkpoint_ids_are_strictly_validated_before_runtime_dict_access(self):
        for name, parameters in (
            ("task_checkpoint", {"checkpoint_id": []}),
            ("task_checkpoint", {"checkpoint_id": {}}),
            ("task_checkpoint", {"checkpoint_id": ()}),
            ("task_checkpoint", {"checkpoint_id": 1}),
            ("task_checkpoint", {"checkpoint_id": True}),
            ("task_checkpoint", {"checkpoint_id": None}),
            ("task_checkpoint", {"checkpoint_id": ""}),
            ("resume_current_task", {"checkpoint_id": "   "}),
            ("resume_interrupted_task", {"checkpoint_id": []}),
        ):
            with self.subTest(skill=name, parameters=parameters), self.assertRaisesRegex(SkillPlanningError, "checkpoint_id"):
                self.compile(name, parameters)

    def test_queue_task_cannot_accept_a_caller_supplied_internal_task_id(self):
        with self.assertRaisesRegex(SkillPlanningError, "unknown parameter"):
            create_skill_request("queue_task", {"queued_skill": "patrol_home", "task_id": "task-999999"})


if __name__ == "__main__":
    unittest.main()
