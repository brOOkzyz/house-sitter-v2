"""Small backend boundary for verified RaPToR-Lite tasks."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import TaskSpec


class BackendError(RuntimeError):
    """A bounded backend action could not be completed."""


class RobotBackend(ABC):
    """The intentionally small contract needed by the House-Sitter case study."""

    name = "backend"
    version = "1.0"

    @abstractmethod
    def initialize(self, task: TaskSpec) -> None: ...

    @abstractmethod
    def available_capabilities(self) -> set[str]: ...

    @abstractmethod
    def current_robot_state(self) -> dict[str, Any]: ...

    @abstractmethod
    def execute(self, skill: str, parameters: dict[str, Any], timeout_seconds: float) -> dict[str, Any]: ...

    @abstractmethod
    def emergency_stop(self) -> dict[str, Any]: ...

    @abstractmethod
    def simulation_time(self) -> float: ...

    @abstractmethod
    def active_events(self) -> list[str]: ...

    @abstractmethod
    def observations(self) -> list[dict[str, Any]]: ...

    @abstractmethod
    def cleanup(self) -> None: ...

    def move_to_room(self, room: str, timeout_seconds: float) -> dict[str, Any]:
        return self.execute("move_to_room", {"room": room}, timeout_seconds)

    def inspect_room(self, room: str, timeout_seconds: float) -> dict[str, Any]:
        return self.execute("inspect_room", {"room": room}, timeout_seconds)

    def return_to_start(self, timeout_seconds: float) -> dict[str, Any]:
        return self.execute("return_to_start", {}, timeout_seconds)

    def stop(self) -> dict[str, Any]:
        return self.emergency_stop()

    def record_failure(self, message: str) -> None:
        """Allow a report-capable backend to retain an earlier continued failure."""
        return None

    def artifact_bundle(self) -> dict[str, Any]:
        return {}
