"""Strict allow-list verifier that runs before any executor."""

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from .schemas import SCHEMA_VERSION, TaskPlan
from .semantic_waypoints import SemanticWaypointError, SemanticWaypointRegistry


FORBIDDEN_PLAN_FIELDS = frozenset(
    {
        "x",
        "y",
        "yaw",
        "pose",
        "position",
        "orientation",
        "quaternion",
        "coordinates",
        "cmd_vel",
        "linear_velocity",
        "angular_velocity",
        "ros_topic",
        "ros_action_name",
        "frame_id",
    }
)


class PlanVerificationError(ValueError):
    pass


@dataclass(frozen=True)
class VerifiedPlanBundle:
    """A whole-plan verification result plus immutable per-step grounding data."""

    plan: TaskPlan
    grounding_snapshots: List[Dict[str, Any]]


class PlanVerifier:
    def __init__(
        self,
        allowed_actions_path: Path,
        waypoints_path: Path,
        semantic_waypoints_path: Path | None = None,
    ) -> None:
        self.rules = self._load_json(allowed_actions_path)
        self.waypoint_config = self._load_json(waypoints_path)
        try:
            self.semantic_waypoints = SemanticWaypointRegistry(
                semantic_waypoints_path
                or allowed_actions_path.parent / "semantic_waypoints.json"
            )
        except SemanticWaypointError as exc:
            raise PlanVerificationError(str(exc)) from exc

    @staticmethod
    def _load_json(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise PlanVerificationError(f"Cannot load configuration {path}: {exc}") from exc

    def _reject_forbidden_fields(self, value: Any, *, path: str = "Plan") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = str(key).strip().lower().replace("-", "_").replace(" ", "_")
                if normalized in FORBIDDEN_PLAN_FIELDS:
                    raise PlanVerificationError(
                        f"{path} contains forbidden coordinate or control field: {key}"
                    )
                self._reject_forbidden_fields(nested, path=f"{path}.{key}")
        elif isinstance(value, list):
            for index, nested in enumerate(value, start=1):
                self._reject_forbidden_fields(nested, path=f"Step {index}")

    def verify(self, plan: Dict[str, Any]) -> TaskPlan:
        """Verify and canonicalize a plan for consumers that only need the plan."""

        return self.verify_with_grounding(plan).plan

    def verify_with_grounding(self, plan: Dict[str, Any]) -> VerifiedPlanBundle:
        """Mandatory whole-plan verification with per-step registry snapshots.

        No bundle is returned until every step has passed structure validation
        and semantic grounding through this verifier's registry instance.
        """
        if not isinstance(plan, dict):
            raise PlanVerificationError("Plan must be a JSON object.")

        self._reject_forbidden_fields(plan)
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

        verified_plan = copy.deepcopy(plan)
        # Pass 1 checks every step's schema, action, parameters, and scalar bounds.
        for index, step in enumerate(verified_plan["steps"], start=1):
            self._verify_step_structure(index, step)
        # Pass 2 performs registry grounding only after the complete structure passes.
        grounding_snapshots: List[Dict[str, Any]] = []
        for index, step in enumerate(verified_plan["steps"], start=1):
            snapshot = self._ground_step_parameters(index, step)
            if snapshot is not None:
                grounding_snapshots.append(snapshot)

        return VerifiedPlanBundle(
            plan=verified_plan,  # type: ignore[arg-type]
            grounding_snapshots=grounding_snapshots,
        )

    def _verify_step_structure(self, index: int, step: Any) -> None:
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
            self._verify_scalar_rules(index, name, value, parameter_rules[name])

    def _ground_step_parameters(
        self, index: int, step: Dict[str, Any]
    ) -> Dict[str, Any] | None:
        action_rules = self.rules["actions"][step["action"]]
        for name, value in list(step["parameters"].items()):
            rules = action_rules["parameters"][name]
            if rules.get("source") == "waypoints":
                known_waypoints = self.waypoint_config["waypoints"]
                if value not in known_waypoints:
                    raise PlanVerificationError(
                        f"Step {index} references unknown waypoint: {value}"
                    )
            if rules.get("source") == "semantic_waypoints":
                try:
                    resolved = self.semantic_waypoints.resolve(value)
                except SemanticWaypointError as exc:
                    raise PlanVerificationError(
                        f"Step {index} references unknown semantic waypoint: {value}"
                    ) from exc
                step["parameters"][name] = resolved["canonical_label"]
                return {
                    "step_index": index,
                    "original_input": resolved["original_input"],
                    "matched_alias": resolved["matched_alias"],
                    "canonical_label": resolved["canonical_label"],
                    "description": resolved["description"],
                    "grounding_mode": resolved["grounding_mode"],
                    "execution_target": resolved["execution_target"],
                    "verification_result": "passed",
                }
        return None

    @staticmethod
    def _verify_scalar_rules(
        index: int, name: str, value: Any, rules: Dict[str, Any]
    ) -> None:
        expected_type = rules["type"]
        if expected_type == "string" and not isinstance(value, str):
            raise PlanVerificationError(f"Step {index} parameter {name} must be a string.")
        if expected_type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            raise PlanVerificationError(f"Step {index} parameter {name} must be a number.")
        if "allowed_values" in rules and value not in rules["allowed_values"]:
            raise PlanVerificationError(f"Step {index} parameter {name} is not allowed.")
        if "minimum" in rules and value < rules["minimum"]:
            raise PlanVerificationError(f"Step {index} parameter {name} is below minimum.")
        if "maximum" in rules and value > rules["maximum"]:
            raise PlanVerificationError(f"Step {index} parameter {name} exceeds maximum.")
