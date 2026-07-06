"""JSON-only planner providers with mandatory verifier integration.

Providers generate text only. They have no executor, ROS, Nav2, or velocity
interfaces, so model output cannot directly control the robot.
"""

from abc import ABC, abstractmethod
import json
import os
from typing import Any, Callable, Dict, Mapping, Optional, TextIO
from urllib import error, parse, request

from .planner import MockPlanner
from .schemas import SCHEMA_VERSION, TaskPlan
from .verifier import PlanVerifier


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


class MockPlannerProvider(PlannerProvider):
    """JSON adapter around the existing deterministic mock planner."""

    def __init__(self, planner: Optional[MockPlanner] = None) -> None:
        self._planner = planner or MockPlanner()

    def generate_json(self, prompt: str) -> str:
        return json.dumps(self._planner.generate(prompt))


def build_json_only_prompt(user_prompt: str) -> str:
    """Constrain a real LLM to the current allow-listed plan schema."""

    return (
        "Return JSON only. Do not include markdown, code fences, comments, or "
        "natural-language explanation.\n"
        "The JSON object must contain exactly these top-level fields: "
        "schema_version, task_name, source, steps.\n"
        f"schema_version must be {SCHEMA_VERSION!r}. source must be "
        "\"gemini_planner\".\n"
        "steps must be a list of objects with exactly action and parameters.\n"
        "Allowed actions and parameters:\n"
        "- navigate_to_waypoint: parameters {\"waypoint\": one of the "
        "user-labelled semantic registry labels: \"start\", \"hallway\", "
        "\"living_room\", \"nearby_test\"}\n"
        "- rotate: parameters {\"angle_degrees\": number from -180 to 180}\n"
        "- wait: parameters {\"duration_seconds\": number from 0 to 30}\n"
        "- report_status: parameters {\"detail\": \"brief\" or \"full\"}\n"
        "Do not output coordinates. Do not output cmd_vel. Do not invent "
        "actions, topics, ROS commands, or waypoints.\n"
        f"User request: {user_prompt}"
    )


class GeminiPlannerProvider(PlannerProvider):
    """Gemini transport for JSON-only task plans.

    The API key is supplied by the caller or GEMINI_API_KEY. This class still
    has no executor, ROS, Nav2, or velocity interface.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3.1-flash-lite",
        transport: Optional[Callable[[str, str, str], str]] = None,
    ) -> None:
        if not api_key:
            raise PlannerProviderError("Gemini API key is required.")
        self.api_key = api_key
        self.model = model
        self._transport = transport or _call_gemini_api

    def generate_json(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise PlannerProviderError("Prompt must not be empty.")

        raw_output = self._transport(
            build_json_only_prompt(prompt),
            self.api_key,
            self.model,
        )
        _parse_json_plan(raw_output)
        return raw_output


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


def _call_gemini_api(prompt: str, api_key: str, model: str) -> str:
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{parse.quote(model, safe='')}:generateContent"
        f"?key={parse.quote(api_key, safe='')}"
    )
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
    ).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, error.HTTPError, json.JSONDecodeError) as exc:
        raise PlannerProviderError(f"Gemini planner request failed: {exc}") from exc

    try:
        return payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise PlannerProviderError("Gemini planner response did not contain text.") from exc


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
