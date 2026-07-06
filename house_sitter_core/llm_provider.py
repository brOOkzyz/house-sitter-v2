"""JSON-only planner providers with mandatory verifier integration.

Providers generate structured plans only. They have no executor, ROS, Nav2,
or velocity interfaces, so model output cannot directly control the robot.
"""

from abc import ABC, abstractmethod
import json
import os
from typing import Any, Callable, Dict, Mapping, Optional, TextIO

from .planner import MockPlanner
from .schemas import SCHEMA_VERSION, TaskPlan
from .verifier import PlanVerifier


ALLOWED_ACTIONS = {
    "navigate_to_waypoint",
    "rotate",
    "wait",
    "report_status",
}
FORBIDDEN_COORDINATE_KEYS = frozenset({"x", "y", "yaw", "pose", "coordinates"})
GEMINI_PLAN_JSON_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "task_name", "source", "steps"],
    "properties": {
        "schema_version": {"type": "string", "enum": [SCHEMA_VERSION]},
        "task_name": {"type": "string", "minLength": 1},
        "source": {"type": "string", "enum": ["gemini_planner"]},
        "steps": {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "parameters"],
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["navigate_to_waypoint"],
                            },
                            "parameters": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["waypoint"],
                                "properties": {
                                    "waypoint": {"type": "string", "minLength": 1}
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "parameters"],
                        "properties": {
                            "action": {"type": "string", "enum": ["rotate"]},
                            "parameters": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["angle_degrees"],
                                "properties": {
                                    "angle_degrees": {"type": "number"}
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "parameters"],
                        "properties": {
                            "action": {"type": "string", "enum": ["wait"]},
                            "parameters": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["duration_seconds"],
                                "properties": {
                                    "duration_seconds": {"type": "number"}
                                },
                            },
                        },
                    },
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["action", "parameters"],
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["report_status"],
                            },
                            "parameters": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["detail"],
                                "properties": {
                                    "detail": {
                                        "type": "string",
                                        "enum": ["brief", "full"],
                                    }
                                },
                            },
                        },
                    },
                ]
            },
        },
    },
}


class PlannerProviderError(ValueError):
    """Raised when a provider is disabled or returns invalid output."""


class PlannerProvider(ABC):
    """Interface for providers that return one task plan as JSON text."""

    @abstractmethod
    def generate_json(self, prompt: str) -> str:
        """Return a JSON-encoded task plan without executing it."""


def _parse_json_plan(raw_output: str) -> Dict[str, Any]:
    if not isinstance(raw_output, str):
        raise PlannerProviderError("Planner provider output must be JSON text.")
    try:
        plan = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise PlannerProviderError("Planner provider output is not valid JSON.") from exc
    if not isinstance(plan, dict):
        raise PlannerProviderError("Planner provider output must be a JSON object.")
    return plan


def _load_google_genai_sdk() -> Any:
    try:
        from google import genai  # type: ignore
    except (ImportError, ModuleNotFoundError) as exc:
        raise PlannerProviderError(
            "google-genai package is required for Gemini SDK provider"
        ) from exc
    return genai


def _reject_coordinate_like_fields(value: Any, *, path: str = "plan") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in FORBIDDEN_COORDINATE_KEYS:
                raise PlannerProviderError(
                    f"Gemini structured output must not contain coordinate-like field: {path}.{key}"
                )
            _reject_coordinate_like_fields(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_coordinate_like_fields(nested, path=f"{path}[{index}]")


def _validate_number(value: Any, *, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PlannerProviderError(
            f"Gemini structured output field {field_name} must be numeric."
        )


def _validate_gemini_structured_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    expected_top_level = {"schema_version", "task_name", "source", "steps"}
    if set(plan) != expected_top_level:
        raise PlannerProviderError(
            f"Gemini structured output must contain exactly {sorted(expected_top_level)}."
        )
    if plan.get("schema_version") != SCHEMA_VERSION:
        raise PlannerProviderError("Gemini structured output has unsupported schema_version.")
    if plan.get("source") != "gemini_planner":
        raise PlannerProviderError(
            "Gemini structured output source must be 'gemini_planner'."
        )
    if not isinstance(plan.get("task_name"), str) or not plan["task_name"].strip():
        raise PlannerProviderError(
            "Gemini structured output task_name must be a non-empty string."
        )

    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PlannerProviderError(
            "Gemini structured output steps must be a non-empty list."
        )

    _reject_coordinate_like_fields(plan)

    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {"action", "parameters"}:
            raise PlannerProviderError(
                f"Gemini structured output step {index} must contain only action and parameters."
            )
        action = step.get("action")
        if action not in ALLOWED_ACTIONS:
            raise PlannerProviderError(
                f"Gemini structured output step {index} uses unsupported action: {action}"
            )
        parameters = step.get("parameters")
        if not isinstance(parameters, dict):
            raise PlannerProviderError(
                f"Gemini structured output step {index} parameters must be an object."
            )
        if action == "navigate_to_waypoint":
            if set(parameters) != {"waypoint"}:
                raise PlannerProviderError(
                    f"Gemini structured output step {index} waypoint parameters must be exactly ['waypoint']."
                )
            if not isinstance(parameters["waypoint"], str) or not parameters["waypoint"].strip():
                raise PlannerProviderError(
                    f"Gemini structured output step {index} waypoint must be a non-empty string label."
                )
        elif action == "rotate":
            if set(parameters) != {"angle_degrees"}:
                raise PlannerProviderError(
                    f"Gemini structured output step {index} rotate parameters must be exactly ['angle_degrees']."
                )
            _validate_number(
                parameters["angle_degrees"],
                field_name=f"steps[{index}].parameters.angle_degrees",
            )
        elif action == "wait":
            if set(parameters) != {"duration_seconds"}:
                raise PlannerProviderError(
                    f"Gemini structured output step {index} wait parameters must be exactly ['duration_seconds']."
                )
            _validate_number(
                parameters["duration_seconds"],
                field_name=f"steps[{index}].parameters.duration_seconds",
            )
        elif action == "report_status":
            if set(parameters) != {"detail"}:
                raise PlannerProviderError(
                    f"Gemini structured output step {index} report_status parameters must be exactly ['detail']."
                )
            if parameters["detail"] not in {"brief", "full"}:
                raise PlannerProviderError(
                    f"Gemini structured output step {index} detail must be 'brief' or 'full'."
                )

    return plan


def _normalize_structured_plan_output(structured_output: Any) -> Dict[str, Any]:
    if hasattr(structured_output, "model_dump"):
        structured_output = structured_output.model_dump()
    elif hasattr(structured_output, "dict"):
        structured_output = structured_output.dict()

    if isinstance(structured_output, str):
        plan = _parse_json_plan(structured_output)
    elif isinstance(structured_output, dict):
        plan = structured_output
    else:
        raise PlannerProviderError(
            "Gemini structured output must be a JSON object or JSON text."
        )

    return _validate_gemini_structured_plan(plan)


class MockPlannerProvider(PlannerProvider):
    """JSON adapter around the existing deterministic mock planner."""

    def __init__(self, planner: Optional[MockPlanner] = None) -> None:
        self._planner = planner or MockPlanner()

    def generate_json(self, prompt: str) -> str:
        return json.dumps(self._planner.generate(prompt))


def build_structured_planner_prompt(user_prompt: str) -> str:
    """Constrain Gemini to the current allow-listed structured plan schema."""

    return (
        "Return one structured task plan for a simulation-only robot workflow. "
        "Do not include markdown, code fences, comments, or natural-language explanation.\n"
        f"schema_version must be {SCHEMA_VERSION!r}. source must be \"gemini_planner\".\n"
        "Use only these actions: navigate_to_waypoint, rotate, wait, report_status.\n"
        "For navigate_to_waypoint, waypoint must be a user-labelled semantic registry string such as "
        "\"start\", \"hallway\", \"living_room\", or \"nearby_test\".\n"
        "Do not output x, y, yaw, pose, coordinates, cmd_vel, ROS topics, or ROS commands.\n"
        "The hallway label is resolved later through a user-labelled semantic waypoint/area registry.\n"
        f"User request: {user_prompt}"
    )


class GeminiPlannerProvider(PlannerProvider):
    """Gemini Python SDK provider for structured task plans.

    The API key is supplied by the caller or GEMINI_API_KEY. This class still
    has no executor, ROS, Nav2, or velocity interface.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.1-flash-lite",
        transport: Optional[Callable[[str, str, str], Any]] = None,
        client: Any = None,
        sdk_loader: Optional[Callable[[], Any]] = None,
    ) -> None:
        if not api_key:
            raise PlannerProviderError("Gemini API key is required.")
        self.api_key = api_key
        self.model = model
        self._transport = transport
        self._client = client
        self._sdk_loader = sdk_loader or _load_google_genai_sdk
        if self._transport is None and self._client is None:
            genai = self._sdk_loader()
            self._client = genai.Client(api_key=api_key)

    def _generate_with_sdk(self, prompt: str) -> Any:
        response = self._client.models.generate_content(
            model=self.model,
            contents=build_structured_planner_prompt(prompt),
            config={
                "temperature": 0.0,
                "response_mime_type": "application/json",
                "response_json_schema": GEMINI_PLAN_JSON_SCHEMA,
            },
        )
        parsed = getattr(response, "parsed", None)
        if parsed is None:
            raise PlannerProviderError(
                "Gemini SDK response did not contain parsed structured output."
            )
        return parsed

    def generate_json(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise PlannerProviderError("Prompt must not be empty.")

        if self._transport is not None:
            structured_output = self._transport(
                build_structured_planner_prompt(prompt),
                self.api_key,
                self.model,
            )
        else:
            structured_output = self._generate_with_sdk(prompt)

        plan = _normalize_structured_plan_output(structured_output)
        return json.dumps(plan)


class RealLLMPlannerProvider(PlannerProvider):
    """Disabled-by-default placeholder for an external LLM transport.

    The transport is injected so this module neither imports an API SDK nor
    performs network calls by itself. Enabling it still does not grant access
    to an executor or any ROS interface.
    """

    def __init__(
        self,
        transport: Optional[Callable[[str], str]] = None,
        *,
        enabled: bool = False,
    ) -> None:
        self._transport = transport
        self.enabled = enabled

    def generate_json(self, prompt: str) -> str:
        if not self.enabled:
            raise PlannerProviderError("Real LLM planner provider is disabled.")
        if self._transport is None:
            raise PlannerProviderError("Real LLM planner transport is not configured.")
        if not isinstance(prompt, str) or not prompt.strip():
            raise PlannerProviderError("Prompt must not be empty.")

        raw_output = self._transport(prompt)
        _parse_json_plan(raw_output)
        return raw_output


def provider_from_env(
    env: Optional[Mapping[str, str]] = None,
    *,
    stream: Optional[TextIO] = None,
) -> PlannerProvider:
    """Select a planner provider from environment variables.

    The mock provider remains the safe default. If Gemini is requested without
    GEMINI_API_KEY, the function prints a clear warning and returns mock.
    """

    values = env or os.environ
    provider_name = values.get("LLM_PROVIDER", "mock").strip().lower()
    output = stream

    if provider_name in {"", "mock"}:
        return MockPlannerProvider()
    if provider_name == "gemini":
        api_key = values.get("GEMINI_API_KEY", "")
        if not api_key:
            if output is not None:
                print(
                    "Gemini provider requested but GEMINI_API_KEY is missing; "
                    "falling back to mock provider",
                    file=output,
                )
            return MockPlannerProvider()
        return GeminiPlannerProvider(
            api_key=api_key,
            model=values.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        )

    raise PlannerProviderError(f"Unknown LLM_PROVIDER: {provider_name}")


class VerifiedPlannerAdapter:
    """Convert provider JSON into a plan and verify it before returning it."""

    def __init__(self, provider: PlannerProvider, verifier: PlanVerifier) -> None:
        self._provider = provider
        self._verifier = verifier

    def generate(self, prompt: str) -> TaskPlan:
        plan = _parse_json_plan(self._provider.generate_json(prompt))
        return self._verifier.verify(plan)
