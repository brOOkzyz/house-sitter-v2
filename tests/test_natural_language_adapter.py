"""Tests for the deterministic, offline bilingual skill-request adapter."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.natural_language_adapter import NaturalLanguageAdapterError, parse_skill_request, validate_with_planner
from tests.skill_test_support import ROOT, demo_artifacts, write_artifacts


SCRIPT = ROOT / "scripts" / "parse_skill_request.py"


class NaturalLanguageAdapterTests(unittest.TestCase):
    def test_representative_chinese_and_english_requests(self):
        cases = {
            "巡逻整个房子": "patrol_home", "patrol the whole house": "patrol_home",
            "检查所有房间": "check_all_rooms", "check all rooms": "check_all_rooms",
            "检查厨房": "inspect_area", "inspect kitchen": "inspect_area",
            "去安全等待区": "go_to_safe_waiting_area", "go to safe waiting area": "go_to_safe_waiting_area",
            "返回充电区": "return_to_charger", "return to charging area": "return_to_charger",
            "暂停当前任务": "pause_current_task", "pause current task": "pause_current_task",
            "继续任务": "resume_current_task", "resume task": "resume_current_task",
            "取消当前任务": "cancel_current_task", "cancel current task": "cancel_current_task",
        }
        for text, capability in cases.items():
            with self.subTest(text=text):
                result = parse_skill_request(text)
                self.assertEqual(result["selected_capability"], capability)
                self.assertEqual((result["simulation_only"], result["real_robot_supported"]), (True, False))
        self.assertEqual(parse_skill_request("检查厨房")["parameters"], {"area": "kitchen"})
        self.assertEqual(parse_skill_request("继续任务")["status"], "needs_clarification")
        resumed = parse_skill_request("resume task checkpoint-001")
        self.assertEqual((resumed["status"], resumed["parameters"]), ("accepted", {"checkpoint_id": "checkpoint-001"}))

    def test_region_aliases_whitespace_and_case_are_deterministic(self):
        expected = {"客厅": "living_room", "living room": "living_room", "厨房": "kitchen", "kitchen": "kitchen", "卧室": "bedroom", "bedroom": "bedroom", "充电区": "charging_area", "charging area": "charging_area"}
        for alias, label in expected.items():
            with self.subTest(alias=alias):
                result = parse_skill_request(f"  INSPECT   {alias.upper()}  ")
                self.assertEqual((result["status"], result["parameters"]), ("accepted", {"area": label}))
        self.assertEqual(parse_skill_request("检查厨房和卧室")["status"], "needs_clarification")

    def test_conflicts_unsupported_and_invalid_input_fail_closed(self):
        self.assertEqual(parse_skill_request("巡逻整个房子然后检查厨房")["status"], "needs_clarification")
        for text in ("打开客厅的灯", "让真实机器人返回充电区", "让实体机器人巡逻整个房子", "控制硬件去厨房", "return the real robot to charging area", "patrol with a physical robot"):
            with self.subTest(text=text):
                rejected = parse_skill_request(text)
                self.assertEqual((rejected["status"], rejected["selected_capability"]), ("unsupported_intent", None))
                self.assertIn("simulation-only", rejected["explanation"])
        self.assertEqual(parse_skill_request("返回充电区")["status"], "accepted")
        self.assertEqual(parse_skill_request("让机器人返回充电区")["status"], "accepted")
        self.assertEqual(parse_skill_request("在仿真中返回充电区")["status"], "accepted")
        for invalid in ("", " " * 3, "x" * 501, None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(NaturalLanguageAdapterError):
                    parse_skill_request(invalid)  # type: ignore[arg-type]
        accepted = parse_skill_request("检查厨房")
        rendered = json.dumps(accepted, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("goal_map", rendered); self.assertNotIn('"x"', rendered); self.assertNotIn('"y"', rendered)

    def test_planner_validation_is_required_and_cannot_be_bypassed(self):
        regions, goals = demo_artifacts()
        accepted = parse_skill_request("检查厨房")
        validation = validate_with_planner(accepted, regions, goals)
        self.assertEqual((validation["status"], validation["planning_status"]), ("accepted", "ready"))
        bad = json.loads(json.dumps(goals)); bad["goals"][0]["review_only"] = False
        with self.assertRaisesRegex(NaturalLanguageAdapterError, "planner validation failed"):
            validate_with_planner(accepted, regions, bad)
        with self.assertRaisesRegex(NaturalLanguageAdapterError, "only an accepted"):
            validate_with_planner(parse_skill_request("检查房间"), regions, goals)

    def test_cli_validation_errors_and_hash_seed_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); regions, goals = write_artifacts(root)
            validated = subprocess.run([sys.executable, str(SCRIPT), "--text", "检查厨房", "--validate-plan", "--semantic-regions", str(regions), "--safe-goals", str(goals)], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(validated.returncode, 0, validated.stderr)
            self.assertEqual(json.loads(validated.stdout)["planner_validation"]["planning_status"], "ready")
            bad = subprocess.run([sys.executable, str(SCRIPT), "--text", ""], cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(bad.returncode, 2); self.assertNotIn("Traceback", bad.stderr)
            outputs = []
            for seed in ("1", "777"):
                run = subprocess.run([sys.executable, str(SCRIPT), "--text", "  INSPECT   KITCHEN "], cwd=ROOT, text=True, capture_output=True, env={**os.environ, "PYTHONHASHSEED": seed}, check=False)
                self.assertEqual(run.returncode, 0, run.stderr); outputs.append(run.stdout)
            self.assertEqual(outputs[0], outputs[1])


if __name__ == "__main__":
    unittest.main()
