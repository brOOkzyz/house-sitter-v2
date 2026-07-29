"""Deterministic mock planner. No external LLM API is used in phase 1."""

import re

from .schemas import ActionStep, TaskPlan, make_plan


class MockPlanner:
    def generate(self, prompt: str) -> TaskPlan:
        normalized = re.sub(r"\s+", " ", prompt.strip().lower())
        if not normalized:
            raise ValueError("Prompt must not be empty.")

        destination = self._detect_destination(normalized)
        should_return = "return" in normalized or "back to start" in normalized
        is_patrol = "patrol" in normalized or "inspect" in normalized

        steps: list[ActionStep] = []
        if destination != "start":
            steps.append(
                {"action": "navigate_to_waypoint", "parameters": {"waypoint": destination}}
            )

        if is_patrol:
            steps.extend(
                [
                    {"action": "rotate", "parameters": {"angle_degrees": 90.0}},
                    {"action": "wait", "parameters": {"duration_seconds": 2.0}},
                    {"action": "report_status", "parameters": {"detail": "brief"}},
                ]
            )
        else:
            steps.append(
                {"action": "report_status", "parameters": {"detail": "brief"}}
            )

        if should_return and destination != "start":
            steps.append(
                {"action": "navigate_to_waypoint", "parameters": {"waypoint": "start"}}
            )
            steps.append(
                {"action": "report_status", "parameters": {"detail": "brief"}}
            )

        return make_plan(
            task_name=self._task_name(is_patrol, destination, should_return),
            source="mock_planner",
            steps=steps,
        )

    @staticmethod
    def _detect_destination(prompt: str) -> str:
        label_aliases = {
            "living_room": ("living room", "living_room"),
            "hallway": ("hallway", "corridor"),
            "kitchen": ("kitchen",),
            "bedroom": ("bedroom",),
            "entrance": ("entrance", "entryway", "entry"),
            "charging_area": ("charging area", "charging_area", "charger area"),
            "garage": ("garage",),
        }
        for label, aliases in label_aliases.items():
            if any(alias in prompt for alias in aliases):
                return label
        return "start"

    @staticmethod
    def _task_name(is_patrol: bool, destination: str, should_return: bool) -> str:
        prefix = "patrol" if is_patrol else "visit"
        suffix = "_and_return" if should_return else ""
        return f"{prefix}_{destination}{suffix}"
