"""ROS-independent tests for the simulation-only LLM Nav2 demo CLI."""

import contextlib
import importlib.util
import io
import json
import sys
import unittest
from pathlib import Path

from house_sitter_core.schemas import make_plan


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_demo_module():
    path = PROJECT_ROOT / "scripts" / "run_llm_sim_nav2_demo.py"
    spec = importlib.util.spec_from_file_location("run_llm_sim_nav2_demo", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class StaticProvider:
    def __init__(self, output: str) -> None:
        self.output = output

    def generate_json(self, prompt: str) -> str:
        return self.output


class LLMSimNav2DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_demo_module()

    def test_mock_planner_hallway_summary_does_not_claim_gemini(self):
        plan = make_plan(
            "visit_hallway",
            "mock_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = self.demo.run_demo(
                "visit the hallway",
                execute_sim=False,
                provider=StaticProvider(json.dumps(plan)),
            )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("structured intent source: mock_planner", output)
        self.assertIn("Planner source 'mock_planner' produced the structured intent.", output)
        self.assertIn("original semantic input: hallway", output)
        self.assertIn("matched alias: none (canonical input)", output)
        self.assertIn("canonical semantic label: hallway", output)
        self.assertIn("request canonical label: hallway", output)
        self.assertIn("canonical semantic label: hallway", output)
        self.assertNotIn("Gemini SDK produced the structured intent.", output)

    def test_front_door_summary_shows_alias_and_canonical_entrance(self):
        plan = make_plan(
            "visit_front_door",
            "mock_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "front door"}}],
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = self.demo.run_demo(
                "go to the front door",
                execute_sim=False,
                provider=StaticProvider(json.dumps(plan)),
            )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("original semantic input: front door", output)
        self.assertIn("matched alias: front door", output)
        self.assertIn("canonical semantic label: entrance", output)
        self.assertIn('"semantic_label": "entrance"', output)
        self.assertIn('"canonical_label": "entrance"', output)
        self.assertIn("simulation-only: yes", output)
        self.assertIn("navigation execution started: no", output)
        self.assertIn("direct /cmd_vel used: no", output)

    def test_lounge_summary_shows_alias_and_canonical_living_room(self):
        plan = make_plan(
            "visit_lounge",
            "mock_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "lounge"}}],
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = self.demo.run_demo(
                "visit the lounge",
                execute_sim=False,
                provider=StaticProvider(json.dumps(plan)),
            )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("original semantic input: lounge", output)
        self.assertIn("matched alias: lounge", output)
        self.assertIn("canonical semantic label: living_room", output)
        self.assertIn('"semantic_label": "living_room"', output)

    def test_gemini_planner_hallway_summary_can_claim_gemini_sdk(self):
        plan = make_plan(
            "visit_hallway",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = self.demo.run_demo(
                "visit the hallway",
                execute_sim=False,
                provider=StaticProvider(json.dumps(plan)),
            )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("structured intent source: gemini_planner", output)
        self.assertIn("Gemini SDK produced the structured intent.", output)
        self.assertIn("canonical semantic label: hallway", output)

    def test_balcony_is_rejected_before_simulation_request(self):
        plan = make_plan(
            "visit_balcony",
            "mock_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "balcony"}}],
        )
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            exit_code = self.demo.run_demo(
                "visit the balcony",
                execute_sim=False,
                provider=StaticProvider(json.dumps(plan)),
            )
        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Rejected request", output)
        self.assertIn("unknown semantic waypoint", output)
        self.assertNotIn("=== Simulation execution request ===", output)


if __name__ == "__main__":
    unittest.main()
