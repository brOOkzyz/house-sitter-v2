"""Behavior tests for the declaration-driven simulation skill catalog."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from house_sitter_core.skill_catalog import (
    CATEGORIES,
    SkillCatalogError,
    catalog_document,
    get_skill_definition,
    list_skill_definitions,
    skill_names,
    validate_skill_parameters,
)
from house_sitter_core.skill_planner import compile_skill_plan, create_skill_request
from house_sitter_core.home_simulation_state import HomeSimulationState
from tests.skill_test_support import SAMPLE_PARAMETERS, demo_artifacts


ROOT = Path(__file__).resolve().parents[1]


class SkillCatalogTests(unittest.TestCase):
    def test_catalog_contains_exactly_fifty_unique_named_capabilities(self):
        definitions = list_skill_definitions()
        self.assertEqual(len(definitions), 50)
        self.assertEqual(len(set(skill_names(definitions))), 50)
        self.assertEqual(definitions[0].skill_name, "patrol_home")
        self.assertEqual(definitions[-1].skill_name, "export_task_trace")

    def test_every_definition_has_valid_category_description_flags_and_policy(self):
        for definition in list_skill_definitions():
            with self.subTest(skill=definition.skill_name):
                self.assertIn(definition.category, CATEGORIES)
                self.assertTrue(definition.description)
                self.assertTrue(definition.allowed_action_types)
                self.assertTrue(definition.safety_policy)
                self.assertTrue(definition.interruption_policy)
                self.assertTrue(definition.recovery_policy)
                self.assertIs(definition.simulation_only, True)
                self.assertIs(definition.review_only, True)
                self.assertIs(definition.executable, False)
                self.assertIs(definition.supported, True)
                self.assertIsNone(definition.unsupported_reason)

    def test_required_parameters_and_unknown_parameters_fail_closed(self):
        definition = get_skill_definition("deliver_item")
        with self.assertRaisesRegex(SkillCatalogError, "missing required"):
            validate_skill_parameters(definition, {"item": "medicine"})
        with self.assertRaisesRegex(SkillCatalogError, "unknown parameter"):
            validate_skill_parameters(definition, {"item": "medicine", "source": "kitchen", "destination": "bedroom", "surprise": True})
        with self.assertRaisesRegex(SkillCatalogError, "unknown simulation skill"):
            get_skill_definition("not_a_skill")

    def test_every_capability_compiles_through_shared_builders(self):
        regions, goals = demo_artifacts()
        observed_actions = set()
        for definition in list_skill_definitions():
            parameters = {name: SAMPLE_PARAMETERS[name] for name in definition.required_parameters}
            if definition.skill_name == "confirm_ambiguous_target":
                parameters["target"] = "房间"
            injected = {"alarm_region": "kitchen"} if definition.builder == "emergency" else {}
            with self.subTest(skill=definition.skill_name):
                request = create_skill_request(definition.skill_name, parameters, injected_events=injected)
                state = HomeSimulationState()
                if definition.skill_name == "change_task_priority":
                    state.enqueue("queued-task-001", "patrol_home", 50)
                    request = create_skill_request(definition.skill_name, {**parameters, "task_id": "task-000001"}, injected_events=injected)
                plan = compile_skill_plan(request, regions, goals, state)
                self.assertEqual(plan["skill_name"], definition.skill_name)
                self.assertGreaterEqual(plan["total_steps"], 1)
                self.assertTrue(all(step["action_type"] in definition.allowed_action_types for step in plan["steps"]))
                observed_actions.update(step["action_type"] for step in plan["steps"])
        self.assertEqual(observed_actions, {
            "navigate_to_region", "navigate_to_safe_goal", "inspect_region", "wait_simulated",
            "pick_item_simulated", "place_item_simulated", "handover_item_simulated",
            "switch_device_simulated", "report_status", "check_alarm_simulated", "charge_simulated",
            "select_nearest_safe_goal", "checkpoint", "restore_checkpoint", "retry_step", "skip_step",
            "abort_task", "return_to_charger", "request_confirmation", "explain_rejection",
            "report_home_check_summary",
        })

    def test_catalog_document_reports_missing_parameters_and_category_filter(self):
        document = catalog_document("item_service", {"item": "medicine"})
        self.assertEqual(document["capability_count"], 6)
        deliver = next(item for item in document["capabilities"] if item["skill_name"] == "deliver_item")
        self.assertEqual(deliver["missing_parameters"], ["source", "destination"])
        self.assertEqual(deliver["name"], "deliver_item")
        self.assertEqual(deliver["classification"], "user_skill")
        self.assertIs(deliver["user_callable"], True)
        self.assertEqual(deliver["implementation_kind"], "item_flow")
        with self.assertRaisesRegex(SkillCatalogError, "unknown skill category"):
            catalog_document("invalid")

    def test_list_capabilities_skill_returns_the_complete_catalog_not_only_a_count(self):
        regions, goals = demo_artifacts()
        plan = compile_skill_plan(create_skill_request("list_capabilities"), regions, goals)
        details = plan["steps"][0]["details"]
        document = catalog_document()
        self.assertEqual(details["capability_count"], 50)
        self.assertEqual(details["capabilities"], document["capabilities"])
        self.assertEqual([item["name"] for item in details["capabilities"]], list(skill_names()))
        for item in details["capabilities"]:
            self.assertIn("user_callable", item)
            self.assertIn("classification", item)
            self.assertIn("implementation_kind", item)

    def test_list_cli_human_and_json_modes_are_stable(self):
        script = ROOT / "scripts" / "list_simulation_skills.py"
        human = subprocess.run([sys.executable, str(script)], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn("capability_count: 50", human.stdout)
        lines = [line for line in human.stdout.splitlines() if "\tcategory=" in line]
        self.assertEqual(len(lines), 50)
        for line in (lines[0], lines[len(lines) // 2], lines[-1]):
            self.assertIn("classification=", line)
            self.assertIn("user_callable=", line)
            self.assertIn("builder=", line)
            self.assertIn("flags=", line)
            self.assertIn("policies=", line)
        machine = subprocess.run([sys.executable, str(script), "--json"], cwd=ROOT, text=True, capture_output=True, check=False)
        self.assertEqual(machine.returncode, 0, machine.stderr)
        self.assertEqual(json.loads(machine.stdout)["capability_count"], 50)


if __name__ == "__main__":
    unittest.main()
