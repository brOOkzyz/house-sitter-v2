"""Optional, simulation-only adapter for Nav2 ``NavigateToPose`` actions.

This module deliberately has no module-level ROS imports and never creates a
publisher.  It is usable by ordinary tests without a ROS installation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
import time
from typing import Any


NAVIGATION_STATUSES = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
FALLBACK_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 180.0
DISTANCE_FALLBACK_SPEED_MPS = 0.20


class NavigationError(RuntimeError):
    """Raised when the optional simulation Nav2 adapter cannot be used."""


@dataclass(frozen=True)
class NavigationGoal:
    """A map-frame goal copied verbatim from a validated accepted-goal reference."""

    label: str
    x: float
    y: float
    goal_reference: dict[str, Any]
    frame_id: str = "map"


@dataclass(frozen=True)
class NavigationOutcome:
    status: str
    feedback: tuple[dict[str, Any], ...] = ()
    reason: str | None = None
    effective_timeout_seconds: float | None = None
    timeout_basis: str | None = None

    def __post_init__(self) -> None:
        if self.status not in NAVIGATION_STATUSES:
            raise NavigationError(f"unsupported navigation status: {self.status}")


def adaptive_timeout_from_feedback(
    feedback: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    current_timeout_seconds: float = FALLBACK_TIMEOUT_SECONDS,
    current_basis: str = "fallback",
) -> tuple[float, str]:
    """Return an upward-only bounded timeout from normalized Nav2 feedback."""
    effective = min(MAX_TIMEOUT_SECONDS, max(FALLBACK_TIMEOUT_SECONDS, current_timeout_seconds))
    basis = current_basis
    for item in feedback:
        eta = item.get("estimated_time_remaining_seconds")
        distance = item.get("distance_remaining")
        if isinstance(eta, (int, float)) and not isinstance(eta, bool) and math.isfinite(eta) and eta > 0:
            candidate, candidate_basis = eta * 1.5 + 15.0, "estimated_time_remaining"
        elif isinstance(distance, (int, float)) and not isinstance(distance, bool) and math.isfinite(distance) and distance > 0:
            candidate, candidate_basis = distance / DISTANCE_FALLBACK_SPEED_MPS * 1.5 + 15.0, "distance_remaining"
        else:
            continue
        candidate = min(MAX_TIMEOUT_SECONDS, max(FALLBACK_TIMEOUT_SECONDS, candidate))
        if candidate > effective:
            effective, basis = candidate, candidate_basis
    return effective, basis


class NavigationExecutor(ABC):
    """Minimal synchronous action interface; implementations must not use cmd_vel."""

    @abstractmethod
    def send_goal(self, goal: NavigationGoal) -> object: ...

    @abstractmethod
    def wait_for_result(self, handle: object, timeout_seconds: float | None) -> NavigationOutcome: ...

    @abstractmethod
    def cancel_goal(self, handle: object) -> None: ...


@dataclass
class FakeNavigationExecutor(NavigationExecutor):
    """Injectable deterministic executor for tests; it has no ROS dependency."""

    outcomes: list[NavigationOutcome] = field(default_factory=lambda: [NavigationOutcome("succeeded")])
    sent_goals: list[NavigationGoal] = field(default_factory=list)
    cancelled_handles: list[object] = field(default_factory=list)

    def send_goal(self, goal: NavigationGoal) -> object:
        self.sent_goals.append(goal)
        return len(self.sent_goals)

    def wait_for_result(self, handle: object, timeout_seconds: float | None) -> NavigationOutcome:
        if timeout_seconds is not None and timeout_seconds <= 0:
            return NavigationOutcome("timed_out", reason="timeout_exceeded")
        return self.outcomes.pop(0) if self.outcomes else NavigationOutcome("succeeded")

    def cancel_goal(self, handle: object) -> None:
        self.cancelled_handles.append(handle)


class Nav2SimulationExecutor(NavigationExecutor):
    """Lazy ROS 2/Nav2 action client for an already-running Gazebo simulation."""

    def __init__(self, node: Any, *, action_name: str = "navigate_to_pose") -> None:
        try:
            from rclpy.action import ActionClient
            from nav2_msgs.action import NavigateToPose
        except ImportError as exc:  # Normal for dry-run and non-ROS test environments.
            raise NavigationError("rclpy and nav2_msgs are required only for --execute-simulation.") from exc
        use_sim_time = node.get_parameter("use_sim_time").value
        if use_sim_time is not True:
            raise NavigationError("Nav2 simulation requires use_sim_time=true.")
        self._node = node
        self._client = ActionClient(node, NavigateToPose, action_name)
        self._navigate_type = NavigateToPose
        self._feedback_by_handle: dict[int, list[dict[str, Any]]] = {}

    @staticmethod
    def _normalize_feedback(message: Any) -> dict[str, Any]:
        """Copy known Nav2 feedback scalars; never retain a ROS message object."""
        payload = getattr(message, "feedback", message)
        result: dict[str, Any] = {}
        for name in ("distance_remaining", "number_of_recoveries"):
            value = getattr(payload, name, None)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[name] = value
        for name in ("estimated_time_remaining", "navigation_time"):
            duration = getattr(payload, name, None)
            seconds = getattr(duration, "sec", None)
            nanoseconds = getattr(duration, "nanosec", None)
            if isinstance(seconds, int) and not isinstance(seconds, bool) and isinstance(nanoseconds, int) and not isinstance(nanoseconds, bool):
                result[f"{name}_seconds"] = seconds + nanoseconds / 1_000_000_000
        return result

    def send_goal(self, goal: NavigationGoal) -> object:
        if goal.frame_id != "map":
            raise NavigationError("Nav2 simulation goals must use the map frame.")
        if not self._client.wait_for_server(timeout_sec=5.0):
            raise NavigationError("simulation Nav2 NavigateToPose action server is unavailable.")
        message = self._navigate_type.Goal()
        message.pose.header.frame_id = "map"
        message.pose.pose.position.x = goal.x
        message.pose.pose.position.y = goal.y
        message.pose.pose.orientation.w = 1.0
        feedback: list[dict[str, Any]] = []

        def feedback_callback(feedback_message: Any) -> None:
            feedback.append(self._normalize_feedback(feedback_message))

        future = self._client.send_goal_async(message, feedback_callback=feedback_callback)
        import rclpy  # Delayed for the same reason as the constructor imports.
        rclpy.spin_until_future_complete(self._node, future)
        handle = future.result()
        if handle is None or not handle.accepted:
            raise NavigationError("simulation Nav2 rejected the accepted safe-goal request.")
        self._feedback_by_handle[id(handle)] = feedback
        return handle

    def wait_for_result(self, handle: object, timeout_seconds: float | None) -> NavigationOutcome:
        future = handle.get_result_async()
        import rclpy
        feedback_list = self._feedback_by_handle.get(id(handle), [])
        if timeout_seconds is None:
            effective_timeout, basis = FALLBACK_TIMEOUT_SECONDS, "fallback"
            started = time.monotonic()
            while not future.done():
                rclpy.spin_until_future_complete(self._node, future, timeout_sec=min(1.0, effective_timeout))
                effective_timeout, basis = adaptive_timeout_from_feedback(feedback_list, effective_timeout, basis)
                if time.monotonic() - started >= effective_timeout:
                    break
        else:
            effective_timeout, basis = timeout_seconds, "user"
            rclpy.spin_until_future_complete(self._node, future, timeout_sec=timeout_seconds)
        feedback = tuple(self._feedback_by_handle.pop(id(handle), ()))
        if not future.done():
            self.cancel_goal(handle)
            return NavigationOutcome("timed_out", feedback, "timeout_exceeded", effective_timeout, basis)
        result = future.result()
        status = int(result.status)
        # action_msgs/GoalStatus values: succeeded=4, canceled=5; all other
        # terminal statuses are fail-closed as failed.
        return NavigationOutcome("succeeded" if status == 4 else "cancelled" if status == 5 else "failed", feedback, None, effective_timeout, basis)

    def cancel_goal(self, handle: object) -> None:
        handle.cancel_goal_async()
