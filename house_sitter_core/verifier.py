"""Strict allow-list verifier that runs before any executor."""

import copy
import json
from pathlib import Path
from typing import Any, Dict

from .schemas import SCHEMA_VERSION, TaskPlan


class PlanVerificationError(ValueError):
    pass


class PlanVerifier:
    def __init__(self, allowed_actions_path: Path, waypoints_path: Path) -> None:
        self.rules = self._load_json(allowed_actions_path)
        self.waypoint_config = self._load_json(waypoints_path)

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanVerificationError(f"Cannot load configuration {path}: {exc}") from exc

    def verify(self, plan: Dict[str, Any]) -> TaskPlan:
        if not isinstance(plan, dict):
            raise PlanVerificationError("Plan must be a JSON object.")

        required_top_level = {"schema_version", "task_name", "source", "steps"}
        if set(plan) != required_top_level:
            raise PlanVerificationError(
                f"Plan fields must be exactly: {sorted(required_top_level)}"
            )
        if plan["schema_version"] != SCHEMA_VERSION:
            raise PlanVerificationError("Unsupported schema_version.")
        if not isinstance(plan["task_name"], str) or not plan["task_name"].strip():
            raise PlanVerificationError("task_name must be a non-empty string.")
        if not isinstance(plan["source"], str) or not plan["source"].strip():
            raise PlanVerificationError("source must be a non-empty string.")

        steps = plan["steps"]
        max_steps = self.rules["limits"]["max_steps"]
        if not isinstance(steps, list) or not 1 <= len(steps) <= max_steps:
            raise PlanVerificationError(f"steps must contain between 1 and {max_steps} items.")

        for index, step in enumerate(steps):
            self._verify_step(index, step)

        return copy.deepcopy(plan)  # type: ignore[return-value]

    def _verify_step(self, index: int, step: Any) -> None:
        if not isinstance(step, dict) or set(step) != {"action", "parameters"}:
            raise PlanVerificationError(
                f"Step {index} must contain only action and parameters."
            )

        action = step["action"]
        action_rules = self.rules["actions"].get(action)
        if action_rules is None:
            raise PlanVerificationError(f"Step {index} uses disallowed action: {action}")

        parameters = step["parameters"]
        if not isinstance(parameters, dict):
            raise PlanVerificationError(f"Step {index} parameters must be an object.")

        parameter_rules = action_rules["parameters"]
        required = set(action_rules["required_parameters"])
        if not required.issubset(parameters):
            missing = sorted(required - set(parameters))
            raise PlanVerificationError(f"Step {index} is missing parameters: {missing}")
        if not set(parameters).issubset(parameter_rules):
            extras = sorted(set(parameters) - set(parameter_rules))
            raise PlanVerificationError(f"Step {index} has unknown parameters: {extras}")

        for name, value in parameters.items():
            self._verify_parameter(index, name, value, parameter_rules[name])

    def _verify_parameter(
        self, index: int, name: str, value: Any, rules: Dict[str, Any]
    ) -> None:
        expected_type = rules["type"]
        if expected_type == "string" and not isinstance(value, str):
            raise PlanVerificationError(f"Step {index} parameter {name} must be a string.")
        if expected_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise PlanVerificationError(f"Step {index} parameter {name} must be a number.")

        if rules.get("source") == "waypoints":
            known_waypoints = self.waypoint_config["waypoints"]
            if value not in known_waypoints:
                raise PlanVerificationError(
                    f"Step {index} references unknown waypoint: {value}"
                )
        if "allowed_values" in rules and value not in rules["allowed_values"]:
            raise PlanVerificationError(f"Step {index} parameter {name} is not allowed.")
        if "minimum" in rules and value < rules["minimum"]:
            raise PlanVerificationError(f"Step {index} parameter {name} is below minimum.")
        if "maximum" in rules and value > rules["maximum"]:
            raise PlanVerificationError(f"Step {index} parameter {name} exceeds maximum.")

