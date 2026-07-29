"""ROS-independent tests for the pure software LLM demo CLI."""

import io
import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from house_sitter_core.llm_provider import MockPlannerProvider  # noqa: E402
from house_sitter_core.schemas import make_plan  # noqa: E402
from run_llm_demo import parse_args, run_demo  # noqa: E402


class StaticProvider:
    def __init__(self, output: str) -> None:
        self.output = output

    def generate_json(self, prompt: str) -> str:
        return self.output


class RecordingExecutor:
    def __init__(self) -> None:
        self.plans = []

    def execute(self, verified_plan):
        self.plans.append(verified_plan)
        return [{"status": "dry_run_only"}]


class LLMDemoTests(unittest.TestCase):
    def test_defaults_to_mock_provider_flow(self):
        args = parse_args(["patrol the living room and return to start"])
        self.assertEqual(args.command, "patrol the living room and return to start")

        stream = io.StringIO()
        executor = RecordingExecutor()
        exit_code = run_demo(
            "patrol the living room and return to start",
            provider=MockPlannerProvider(),
            executor=executor,
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("=== User command ===", output)
        self.assertIn("=== Generated JSON plan ===", output)
        self.assertIn("=== Verification result ===", output)
        self.assertIn("=== Dry-run execution steps ===", output)
        self.assertIn("=== Final task report ===", output)
        self.assertIn('"mode": "dry_run"', output)
        self.assertEqual(len(executor.plans), 1)

    def test_illegal_action_is_rejected(self):
        plan = make_plan(
            "illegal_action",
            "demo",
            [{"action": "open_door", "parameters": {}}],
        )
        stream = io.StringIO()
        exit_code = run_demo(
            "open the door",
            provider=StaticProvider(json.dumps(plan)),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Rejected request", output)
        self.assertIn("disallowed action", output)

    def test_unknown_waypoint_is_rejected(self):
        plan = make_plan(
            "unknown_waypoint",
            "demo",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "garage"}}],
        )
        stream = io.StringIO()
        exit_code = run_demo(
            "go to the garage",
            provider=StaticProvider(json.dumps(plan)),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Rejected request", output)
        self.assertIn("unknown semantic waypoint", output)

    def test_cmd_vel_like_request_is_rejected(self):
        plan = make_plan(
            "direct_velocity",
            "demo",
            [{"action": "cmd_vel", "parameters": {"linear_x": 0.2}}],
        )
        stream = io.StringIO()
        exit_code = run_demo(
            "move forward at 0.2 m/s",
            provider=StaticProvider(json.dumps(plan)),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Rejected request", output)
        self.assertIn("disallowed action", output)

    def test_mock_planner_hallway_summary_does_not_claim_gemini(self):
        stream = io.StringIO()
        exit_code = run_demo(
            "visit the hallway",
            provider=MockPlannerProvider(),
            executor=RecordingExecutor(),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("structured intent source: mock_planner", output)
        self.assertIn("Planner source 'mock_planner' produced the structured intent.", output)
        self.assertIn("hallway is resolved from a user-labelled semantic waypoint/area registry.", output)
        self.assertNotIn("Gemini SDK produced the structured intent.", output)

    def test_mock_planner_kitchen_summary_uses_registry(self):
        stream = io.StringIO()
        exit_code = run_demo(
            "visit the kitchen",
            provider=MockPlannerProvider(),
            executor=RecordingExecutor(),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("kitchen is resolved from a user-labelled semantic waypoint/area registry.", output)
        self.assertIn("semantic labels remain simulation-only metadata.", output)
        self.assertIn('"waypoint": "kitchen"', output)

    def test_mock_planner_garage_is_rejected_by_verifier(self):
        stream = io.StringIO()
        exit_code = run_demo(
            "visit the garage",
            provider=MockPlannerProvider(),
            executor=RecordingExecutor(),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Rejected request", output)
        self.assertIn("unknown semantic waypoint", output)

    def test_gemini_planner_hallway_summary_can_claim_gemini_sdk(self):
        plan = make_plan(
            "visit_hallway",
            "gemini_planner",
            [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
        )
        stream = io.StringIO()
        exit_code = run_demo(
            "visit the hallway",
            provider=StaticProvider(json.dumps(plan)),
            executor=RecordingExecutor(),
            stream=stream,
        )
        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("structured intent source: gemini_planner", output)
        self.assertIn("Gemini SDK produced the structured intent.", output)
        self.assertIn(
            "hallway is resolved from a user-labelled semantic waypoint/area registry.",
            output,
        )


if __name__ == "__main__":
    unittest.main()
