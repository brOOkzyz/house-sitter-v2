"""JSON-only planner providers with mandatory verifier integration.

Providers generate text only. They have no executor, ROS, Nav2, or velocity
interfaces, so model output cannot directly control the robot.
"""

from abc import ABC, abstractmethod
import json
from typing import Any, Callable, Dict, Optional

from .planner import MockPlanner
from .schemas import TaskPlan
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


class RealLLMPlannerProvider(PlannerProvider):
    """Disabled-by-default placeholder for a future external LLM transport.

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


class VerifiedPlannerAdapter:
    """Convert provider JSON into a plan and verify it before returning it."""

    def __init__(self, provider: PlannerProvider, verifier: PlanVerifier) -> None:
        self._provider = provider
        self._verifier = verifier

    def generate(self, prompt: str) -> TaskPlan:
        plan = _parse_json_plan(self._provider.generate_json(prompt))
        return self._verifier.verify(plan)
