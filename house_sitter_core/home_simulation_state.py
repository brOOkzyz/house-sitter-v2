"""Deterministic in-memory state for the review-only smart-home skill demo."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LOW_BATTERY_THRESHOLD_PERCENT = 20.0
CRITICAL_BATTERY_THRESHOLD_PERCENT = 10.0
FULL_BATTERY_PERCENT = 100.0
NAVIGATION_ENERGY_COST_PERCENT = 2.0
ACTION_ENERGY_COST_PERCENT = 0.5
MAX_RETRY_ATTEMPTS = 1
NORMAL_PRIORITY = 50
EMERGENCY_PRIORITY = 100
EMERGENCY_QUEUE_SKILLS = frozenset({"emergency_response", "emergency_task_preemption"})
MAX_CHECKPOINT_ID_LENGTH = 128


class HomeSimulationStateError(ValueError):
    """Raised when deterministic local-state data violates its contract."""


def validate_checkpoint_id(value: Any) -> str:
    """Return one stable checkpoint identifier or reject before dict access."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise HomeSimulationStateError("checkpoint_id must be a non-empty trimmed string.")
    if len(value) > MAX_CHECKPOINT_ID_LENGTH:
        raise HomeSimulationStateError(
            f"checkpoint_id must not exceed {MAX_CHECKPOINT_ID_LENGTH} characters."
        )
    return value


@dataclass
class HomeSimulationState:
    """Local state only; none of these values are read from physical devices."""

    current_region: str = "charging_area"
    battery_percent: float = FULL_BATTERY_PERCENT
    charging: bool = False
    restricted_regions: tuple[str, ...] = ()
    blocked_goals: tuple[str, ...] = ()
    simulated_items: dict[str, str] = field(default_factory=dict)
    simulated_alarms: tuple[dict[str, Any], ...] = ()
    active_task: str | None = None
    queued_tasks: list[dict[str, Any]] = field(default_factory=list)
    checkpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    device_states: dict[str, str] = field(default_factory=lambda: {"lights": "unknown", "doors": "unknown"})
    completed_actions: list[str] = field(default_factory=list)
    next_queue_insertion_order: int = 1

    def snapshot(self) -> dict[str, Any]:
        """Return a stable JSON-ready snapshot without wall-clock metadata."""
        return {
            "current_region": self.current_region,
            "battery_percent": self.battery_percent,
            "charging": self.charging,
            "restricted_regions": list(self.restricted_regions),
            "blocked_goals": list(self.blocked_goals),
            "simulated_items": {key: self.simulated_items[key] for key in sorted(self.simulated_items)},
            "simulated_alarms": [dict(event) for event in self.simulated_alarms],
            "active_task": self.active_task,
            "queued_tasks": [dict(task) for task in self.ordered_queue()],
            "checkpoints": {key: dict(self.checkpoints[key]) for key in sorted(self.checkpoints)},
            "device_states": {key: self.device_states[key] for key in sorted(self.device_states)},
            "completed_actions": list(self.completed_actions),
        }

    def create_checkpoint(self, checkpoint_id: str, next_step_order: int) -> dict[str, Any]:
        checkpoint_id = validate_checkpoint_id(checkpoint_id)
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "next_step_order": next_step_order,
            "current_region": self.current_region,
            "battery_percent": self.battery_percent,
            "charging": self.charging,
        }
        self.checkpoints[checkpoint_id] = checkpoint
        return dict(checkpoint)

    def next_task_id(self) -> str:
        """Return the deterministic ID that the next owned queue entry will receive."""
        return f"task-{self.next_queue_insertion_order:06d}"

    def enqueue(self, request_id: str, skill_name: str, priority: int, *, expected_task_id: str | None = None) -> dict[str, Any]:
        """Add one owned queue entry with a generated, non-caller-controlled task ID."""
        task_id = self.next_task_id()
        if expected_task_id is not None and expected_task_id != task_id:
            raise HomeSimulationStateError("queue task_id no longer matches the deterministic insertion order.")
        if any(task.get("task_id") == task_id for task in self.queued_tasks):
            raise HomeSimulationStateError(f"duplicate internally generated task_id: {task_id}")
        task = {
            "task_id": task_id,
            "request_id": request_id,
            "skill_name": skill_name,
            "priority": priority,
            "insertion_order": self.next_queue_insertion_order,
        }
        self.next_queue_insertion_order += 1
        self.queued_tasks.append(task)
        return dict(task)

    def ordered_queue(self) -> list[dict[str, Any]]:
        """Higher priority first, stable FIFO for equal priorities."""
        return sorted(self.queued_tasks, key=lambda task: (-task["priority"], task["insertion_order"]))
