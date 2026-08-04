"""Deterministic semantic navigation planner used as the offline fallback."""

import re

from .schemas import ActionStep, TaskPlan, make_plan
from .semantic_waypoints import load_semantic_waypoint_registry


class MockPlanner:
    _STEP_SEPARATOR = re.compile(
        r"\s*(?:,|->|\b(?:and\s+then|then|and\s+finally|finally)\b|\band\s+(?=return\b))\s*"
    )
    _LEADING_NAVIGATION_WORDS = re.compile(
        r"^(?:(?:please|and)\s+)*(?:go\s+(?:to|through)|visit|return\s+to|"
        r"back\s+to|patrol|inspect)\s+(?:the\s+)?"
    )

    def generate(self, prompt: str) -> TaskPlan:
        normalized = re.sub(r"\s+", " ", prompt.strip().lower())
        if not normalized:
            raise ValueError("Prompt must not be empty.")

        registry = load_semantic_waypoint_registry()
        destinations = [
            self._extract_destination(clause, registry)
            for clause in self._STEP_SEPARATOR.split(normalized)
            if clause.strip(" .,;")
        ]
        steps: list[ActionStep] = [
            {"action": "navigate_to_waypoint", "parameters": {"waypoint": destination}}
            for destination in destinations
            if destination
        ]
        return make_plan(
            task_name="navigate_" + "_then_".join(
                re.sub(r"[\s-]+", "_", destination) for destination in destinations
            ),
            source="mock_planner",
            steps=steps,
        )

    @classmethod
    def _extract_destination(cls, clause: str, registry) -> str:
        matched_expression = registry.match_prompt_expression(clause)
        if matched_expression is not None:
            return matched_expression
        candidate = cls._LEADING_NAVIGATION_WORDS.sub("", clause.strip(" .,;"))
        return candidate.strip(" .,;")
