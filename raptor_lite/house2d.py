"""Seeded, headless household simulator; it is not a physics or ROS simulator."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import random
from typing import Any

from .backends import BackendError, RobotBackend
from .models import TaskSpec


ROOMS = {
    "charging_area": {"center": [1.0, 1.0], "bounds": [0.0, 0.0, 2.0, 2.0]},
    "living_room": {"center": [4.0, 2.5], "bounds": [2.0, 0.0, 6.0, 5.0]},
    "kitchen": {"center": [8.0, 2.5], "bounds": [6.0, 0.0, 10.0, 5.0]},
    "bedroom": {"center": [4.0, 7.5], "bounds": [2.0, 5.0, 6.0, 10.0]},
    "bathroom": {"center": [8.0, 7.5], "bounds": [6.0, 5.0, 10.0, 10.0]},
}
DOORS = (("charging_area", "living_room"), ("living_room", "kitchen"), ("living_room", "bedroom"), ("bedroom", "bathroom"), ("kitchen", "bathroom"))
EVENTS = {"unexpected_obstacle", "high_temperature", "high_humidity", "blocked_transition", "observation_dropout", "low_initial_battery"}
ALL_SKILLS = {"move_to_room", "inspect_room", "record_baseline", "detect_environment_change", "update_digital_twin", "generate_alert", "generate_monitoring_report", "return_to_start", "stop"}


class House2DBackend(RobotBackend):
    name = "house2d"
    version = "1.0"

    def __init__(self, seed: int | None = None, events: list[str] | None = None, initial_battery: float | None = None):
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.requested_events = list(events or [])
        unknown = set(self.requested_events) - EVENTS
        if unknown:
            raise ValueError(f"Unknown house2d event: {sorted(unknown)[0]}")
        self.requested_battery = initial_battery
        self._rng = random.Random(self.seed)
        self._state: dict[str, Any] = {}
        self._ground_truth: dict[str, Any] = {}
        self._initial: dict[str, Any] = {}
        self._observations: list[dict[str, Any]] = []
        self._routes: list[dict[str, Any]] = []

    def initialize(self, task: TaskSpec) -> None:
        self._rng = random.Random(self.seed)
        battery = self.requested_battery if self.requested_battery is not None else float(self._rng.randint(82, 96))
        if "low_initial_battery" in self.requested_events:
            battery = min(battery, 5.0)
        rooms = {}
        for room in ROOMS:
            rooms[room] = {
                "temperature_c": round(19.0 + self._rng.random() * 3.0, 2),
                "humidity_percent": round(38.0 + self._rng.random() * 12.0, 2),
                "static_objects": [f"{room}_furniture_{self._rng.randint(1, 99)}"],
            }
        event_records = []
        for index, kind in enumerate(self.requested_events, 1):
            room = "kitchen" if kind in {"unexpected_obstacle", "high_temperature"} else "bathroom" if kind == "high_humidity" else "living_room"
            parameters = {"doors": [["living_room", "kitchen"], ["bathroom", "kitchen"]]} if kind == "blocked_transition" else {}
            event_records.append({"event_id": f"event_{index:03d}", "type": kind, "room": room, "timestamp": 0.0, "parameters": parameters})
        self._ground_truth = {"seed": self.seed, "rooms": rooms, "events": event_records, "doors": [list(door) for door in DOORS]}
        self._state = {"room": "charging_area", "pose": list(ROOMS["charging_area"]["center"]), "battery": float(battery), "time": 0.0, "stopped": False, "visit_history": ["charging_area"], "baselines": {}, "twin": {}, "alerts": []}
        self._initial = deepcopy(self.current_robot_state())
        self._observations, self._routes = [], []

    def available_capabilities(self) -> set[str]:
        return set(ALL_SKILLS)

    def current_robot_state(self) -> dict[str, Any]:
        return deepcopy(self._state)

    def simulation_time(self) -> float:
        return float(self._state.get("time", 0.0))

    def active_events(self) -> list[str]:
        return [item["event_id"] for item in self._ground_truth.get("events", [])]

    def observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._observations)

    def _event_records(self, room: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        return [item for item in self._ground_truth["events"] if (room is None or item["room"] == room) and (kind is None or item["type"] == kind)]

    def _blocked_doors(self) -> set[frozenset[str]]:
        return {frozenset(door) for item in self._event_records(kind="blocked_transition") for door in item["parameters"]["doors"]}

    def _route(self, start: str, target: str) -> list[str]:
        queue: deque[list[str]] = deque([[start]])
        blocked = self._blocked_doors()
        while queue:
            path = queue.popleft()
            current = path[-1]
            if current == target:
                return path
            for a, b in DOORS:
                neighbor = b if a == current else a if b == current else None
                if neighbor is not None and neighbor not in path and frozenset((current, neighbor)) not in blocked:
                    queue.append(path + [neighbor])
        raise BackendError(f"No legal route from '{start}' to '{target}'.")

    def _move(self, target: str, timeout_seconds: float) -> dict[str, Any]:
        if target not in ROOMS:
            raise BackendError(f"Unknown room '{target}'.")
        route = self._route(self._state["room"], target)
        edges = len(route) - 1
        duration, battery_cost = edges * 5.0, edges * 4.0
        if duration > timeout_seconds:
            raise BackendError(f"Movement to '{target}' requires {duration:.1f}s, exceeding its {timeout_seconds:.1f}s timeout.")
        if battery_cost > self._state["battery"]:
            raise BackendError(f"Battery {self._state['battery']:.1f}% is insufficient for route to '{target}'.")
        self._state["time"] += duration
        self._state["battery"] -= battery_cost
        self._state["room"], self._state["pose"] = target, list(ROOMS[target]["center"])
        self._state["visit_history"].append(target)
        route_record = {"timestamp": self._state["time"], "from_room": route[0], "to_room": target, "rooms": route, "duration_seconds": duration, "battery_consumed": battery_cost}
        self._routes.append(route_record)
        return {"entered_room": target, "route": route, "simulation_time": self._state["time"], "battery": self._state["battery"], "simulation_only": True}

    def _inspect(self, room: str, timeout_seconds: float) -> dict[str, Any]:
        if room != self._state["room"]:
            raise BackendError(f"Robot is in '{self._state['room']}', not '{room}'; inspect requires room entry first.")
        if timeout_seconds < 2.0:
            raise BackendError("Room inspection requires 2.0s of bounded simulation time.")
        self._state["time"] += 2.0
        self._state["battery"] = max(0.0, self._state["battery"] - 0.2)
        events = self._event_records(room)
        dropout = any(item["type"] == "observation_dropout" for item in events)
        truth = self._ground_truth["rooms"][room]
        visible = list(truth["static_objects"])
        if any(item["type"] == "unexpected_obstacle" for item in events):
            visible.append(f"{room}_unexpected_obstacle")
        observation = {
            "room": room, "timestamp": self._state["time"], "robot_state": {"room": room, "pose": list(self._state["pose"]), "battery": self._state["battery"]}, "visit_index": len(self._state["visit_history"]),
            "visible_object_identifiers": [] if dropout else visible, "obstacle_present": None if dropout else any(item["type"] == "unexpected_obstacle" for item in events),
            "temperature_c": None if dropout else (75.0 if any(item["type"] == "high_temperature" for item in events) else truth["temperature_c"]),
            "humidity_percent": None if dropout else (92.0 if any(item["type"] == "high_humidity" for item in events) else truth["humidity_percent"]),
            "battery": self._state["battery"], "active_event_identifiers": [item["event_id"] for item in events], "observation_valid": not dropout,
            "synthetic": True, "simulated_onboard_sensor": True, "simulation_only": True, "physical_robot_validated": False, "scenario_seed": self.seed,
        }
        self._observations.append(observation)
        return deepcopy(observation)

    def execute(self, skill: str, parameters: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        if self._state.get("stopped") and skill != "stop":
            raise BackendError("Robot is stopped; reset is required before another action.")
        if skill == "move_to_room":
            return self._move(str(parameters["room"]), timeout_seconds)
        if skill == "return_to_start":
            result = self._move("charging_area", timeout_seconds); result["returned_to_start"] = True; return result
        if skill == "inspect_room":
            return self._inspect(str(parameters["room"]), timeout_seconds)
        if skill == "record_baseline":
            room = str(parameters["room"]); latest = next((item for item in reversed(self._observations) if item["room"] == room), None)
            if latest is None or not latest["observation_valid"]:
                raise BackendError(f"A valid observation is required before recording '{room}' baseline.")
            self._state["baselines"][room] = latest; self._state["time"] += 1.0; return {"baseline_recorded": room, "simulation_only": True}
        if skill == "detect_environment_change":
            return {"room": parameters.get("room", self._state["room"]), "changes": [], "simulation_only": True}
        if skill == "update_digital_twin":
            room = str(parameters["room"]); self._state["twin"][room] = "updated"; return {"room": room, "digital_twin_updated": True, "simulation_only": True}
        if skill == "generate_alert":
            alert = {"room": parameters.get("room", self._state["room"]), "severity": parameters.get("severity", "warning"), "simulation_only": True}; self._state["alerts"].append(alert); return alert
        if skill == "generate_monitoring_report":
            return {"markdown": f"# Monitoring report\n\nRoom: {self._state['room']}\n", "simulation_only": True}
        if skill == "stop":
            return self.emergency_stop()
        raise BackendError(f"House2D has no implementation for '{skill}'.")

    def emergency_stop(self) -> dict[str, Any]:
        self._state["stopped"] = True
        return {"stopped": True, "emergency_stop": True, "simulation_time": self._state["time"]}

    def cleanup(self) -> None:
        return None

    def artifact_bundle(self) -> dict[str, Any]:
        return {
            "simulator_config": {"backend_name": self.name, "backend_version": self.version, "rooms": ROOMS, "doors": [list(item) for item in DOORS], "movement_seconds_per_door": 5.0, "battery_per_door": 4.0, "simulation_only": True, "physical_robot_validated": False},
            "scenario_seed": {"seed": self.seed}, "scenario_ground_truth": deepcopy(self._ground_truth), "initial_world_state": deepcopy(self._initial), "final_world_state": self.current_robot_state(),
            "sensor_observations": self.observations(), "route_trace": deepcopy(self._routes),
        }
