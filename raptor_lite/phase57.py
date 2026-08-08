"""Trusted temporal Twin history and deterministic resource admission policy."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .house2d import legal_room_route
from .models import TaskSpec


_STATE_FIELDS = {
    "layout_object_state": "visible_object_identifiers",
    "obstacle_present": "obstacle_present",
    "temperature_state": "temperature_c",
    "humidity_state": "humidity_percent",
    "accessibility_state": "transition_accessibility",
}
_INSPECTION_BATTERY_COST = 0.2
_BATTERY_PER_DOOR = 4.0
_SAFETY_MARGIN = 2.0


class TwinHistory:
    """Session-persistent projection built only from valid onboard observations."""

    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []

    def reset(self) -> None:
        self.rooms = {}
        self.runs = []

    @staticmethod
    def _state(observation: dict[str, Any]) -> dict[str, Any]:
        return {name: deepcopy(observation[source]) for name, source in _STATE_FIELDS.items()}

    def record(self, run_id: str, observations: list[dict[str, Any]]) -> dict[str, Any]:
        """Record only room-local, valid observations made while physically present."""
        trusted: dict[str, dict[str, Any]] = {}
        ignored: list[dict[str, str]] = []
        for observation in observations:
            room = observation.get("room")
            robot_room = observation.get("robot_state", {}).get("room")
            if not isinstance(room, str) or room != robot_room:
                ignored.append({"room": str(room or "unknown"), "reason": "observation was not made in its claimed room"})
            elif observation.get("observation_valid") is not True:
                ignored.append({"room": room, "reason": "invalid observation is not trusted"})
            else:
                trusted[room] = observation
        changes: list[dict[str, Any]] = []
        unchanged: list[str] = []
        initialized: list[str] = []
        for room in sorted(trusted):
            observation, state = trusted[room], self._state(trusted[room])
            previous = self.rooms.get(room)
            if previous is None:
                self.rooms[room] = {"state": state, "revision": 0, "provenance": {"run_id": run_id, "observation_id": observation["observation_id"]}}
                initialized.append(room)
                continue
            fields = {field: {"before": deepcopy(previous["state"][field]), "after": deepcopy(state[field])} for field in _STATE_FIELDS if previous["state"][field] != state[field]}
            previous["provenance"] = {"run_id": run_id, "observation_id": observation["observation_id"]}
            if not fields:
                unchanged.append(room)
                continue
            previous["state"] = state
            previous["revision"] += 1
            changes.append({"room": room, "revision": previous["revision"], "observation_id": observation["observation_id"], "changed_fields": fields, "decision_basis": "consecutive valid onboard observations"})
        if changes:
            summary = "Confirmed changes: " + "; ".join(f"{item['room'].replace('_', ' ')} ({', '.join(item['changed_fields'])})" for item in changes) + "."
        elif initialized:
            summary = "Trusted history initialized from valid observations in " + ", ".join(room.replace("_", " ") for room in initialized) + "."
        else:
            summary = "No confirmed temporal changes from valid visited-room observations."
        diff = {"schema_version": "1.0", "run_id": run_id, "trusted_rooms": sorted(trusted), "initialized_rooms": initialized, "unchanged_rooms": unchanged, "ignored_observations": ignored, "confirmed_changes": changes, "change_summary": summary, "simulation_only": True, "physical_robot_validated": False}
        self.runs.append(deepcopy(diff))
        return diff

    def snapshot(self) -> dict[str, Any]:
        return {"schema_version": "1.0", "rooms": deepcopy(self.rooms), "runs": deepcopy(self.runs), "simulation_only": True, "physical_robot_validated": False}


def resource_decision(task: TaskSpec, robot_state: dict[str, Any]) -> dict[str, Any]:
    """Fail closed before movement when the idle robot cannot safely return."""
    activity = str(robot_state.get("activity", "idle"))
    battery, current = robot_state.get("battery"), robot_state.get("room")
    base = {"schema_version": "1.0", "activity": activity, "battery_percent": battery, "estimated_task_cost": 0.0, "safe_return_reserve": 0.0, "safety_margin": _SAFETY_MARGIN, "required_battery": None, "simulation_only": True, "physical_robot_validated": False}
    if activity != "idle":
        return {**base, "decision": "DEFER", "reason": "The robot is not idle.", "recommended_action": "Wait for the active task to finish before starting a House-Sitter patrol."}
    if not isinstance(battery, (int, float)) or not isinstance(current, str):
        return {**base, "decision": "REJECT", "reason": "A usable robot battery or location is unavailable.", "recommended_action": "Restore a valid idle robot state before execution."}
    try:
        cost = 0.0
        last_return_cost: float | None = None
        for step in task.steps:
            if step.skill in {"move_to_room", "revisit_active_event_rooms"}:
                target = step.parameters.get("room")
                if not isinstance(target, str):
                    raise ValueError("A movement step has no room target.")
                cost += _BATTERY_PER_DOOR * (len(legal_room_route(current, target)) - 1)
                current = target
            elif step.skill == "return_to_start":
                last_return_cost = _BATTERY_PER_DOOR * (len(legal_room_route(current, "charging_area")) - 1)
                cost += last_return_cost
                current = "charging_area"
            elif step.skill == "inspect_room":
                cost += _INSPECTION_BATTERY_COST
    except Exception as exc:
        return {**base, "decision": "REJECT", "reason": f"The verified task has no legal resource estimate: {exc}", "recommended_action": "Revise the task through the verifier."}
    if last_return_cost is None:
        return {**base, "decision": "REJECT", "reason": "The task has no planned safe return to the charging area.", "recommended_action": "Add a verifier-approved return-to-start step."}
    required = round(cost + _SAFETY_MARGIN, 3)
    values = {**base, "estimated_task_cost": round(cost, 3), "safe_return_reserve": round(last_return_cost, 3), "required_battery": required}
    if float(battery) < required:
        return {**values, "decision": "DEFER", "reason": f"Battery {float(battery):.1f}% is below the {required:.1f}% needed for the estimated task, safe return, and margin.", "recommended_action": "Recharge at the charging area before starting the patrol."}
    return {**values, "decision": "APPROVE", "reason": "The idle robot has enough estimated battery for the verified task and safe return reserve.", "recommended_action": "Execute the confirmed simulation-only task."}
