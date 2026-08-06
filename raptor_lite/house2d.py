"""Seeded, headless household simulator; it is not a physics or ROS simulator."""
from __future__ import annotations

from collections import deque
from copy import deepcopy
import random
from typing import Any

from .backends import BackendError, RobotBackend
from .house_sitter import HouseSitterApplication
from .models import TaskSpec


ROOMS = {
    "charging_area": {"center": [1.0, 1.0], "bounds": [0.0, 0.0, 2.0, 2.0]},
    "living_room": {"center": [4.0, 2.5], "bounds": [2.0, 0.0, 6.0, 5.0]},
    "kitchen": {"center": [8.0, 2.5], "bounds": [6.0, 0.0, 10.0, 5.0]},
    "bedroom": {"center": [4.0, 7.5], "bounds": [2.0, 5.0, 6.0, 10.0]},
    "bathroom": {"center": [8.0, 7.5], "bounds": [6.0, 5.0, 10.0, 10.0]},
}
DOORS = (("charging_area", "living_room"), ("living_room", "kitchen"), ("living_room", "bedroom"), ("bedroom", "bathroom"), ("kitchen", "bathroom"))
# Presentation-only geometry.  The backend keeps the compact room graph above;
# this shared layout expands each legal graph edge through actual doorways.
HOUSE_LAYOUT = {
    "bounds": [0.0, 0.0, 12.0, 10.0],
    "rooms": {
        "charging_area": {"bounds": [0.5, 0.5, 2.5, 2.5], "label": "Charging area"},
        "hallway": {"bounds": [2.5, 0.5, 4.0, 9.5], "label": "Entrance hall"},
        "living_room": {"bounds": [4.0, 0.5, 8.0, 5.0], "label": "Living room"},
        "kitchen": {"bounds": [8.0, 0.5, 11.5, 5.0], "label": "Kitchen"},
        "bedroom": {"bounds": [4.0, 5.0, 8.0, 9.5], "label": "Bedroom"},
        "bathroom": {"bounds": [8.0, 5.0, 11.5, 9.5], "label": "Bathroom"},
    },
    "checkpoints": {
        "charging_area": [1.4, 1.4], "living_room": [6.0, 2.8], "kitchen": [9.8, 2.8],
        "bedroom": [6.0, 7.5], "bathroom": [9.8, 7.5],
    },
    "doors": [[2.5, 1.5, "vertical"], [4.0, 2.5, "vertical"], [8.0, 2.5, "vertical"], [6.0, 5.0, "horizontal"], [9.8, 5.0, "horizontal"], [8.0, 7.5, "vertical"]],
    "furniture": [["living_room", "sofa", [4.6, 1.1, 6.1, 1.7]], ["living_room", "table", [6.7, 3.4, 7.4, 4.1]], ["kitchen", "counter", [10.4, 1.0, 11.1, 3.5]], ["bedroom", "bed", [4.6, 6.1, 6.4, 7.3]], ["bathroom", "bath", [10.4, 6.0, 11.0, 7.8]]],
}
LAYOUT_WAYPOINTS = {
    "charging_area": [1.4, 1.4], "charging_door": [2.5, 1.5], "hall_lower": [3.25, 1.5], "living_hall_door": [4.0, 2.5],
    "living_room": [6.0, 2.8], "living_kitchen_door": [8.0, 2.5], "kitchen": [9.8, 2.8], "living_bedroom_door": [6.0, 5.0],
    "bedroom": [6.0, 7.5], "kitchen_bathroom_door": [9.8, 5.0], "bathroom": [9.8, 7.5], "bedroom_bathroom_door": [8.0, 7.5],
}
LAYOUT_CONNECTIONS = {
    frozenset(("charging_area", "living_room")): ("charging_area", "charging_door", "hall_lower", "living_hall_door", "living_room"),
    frozenset(("living_room", "kitchen")): ("living_room", "living_kitchen_door", "kitchen"),
    frozenset(("living_room", "bedroom")): ("living_room", "living_bedroom_door", "bedroom"),
    frozenset(("kitchen", "bathroom")): ("kitchen", "kitchen_bathroom_door", "bathroom"),
    frozenset(("bedroom", "bathroom")): ("bedroom", "bedroom_bathroom_door", "bathroom"),
}


def layout_route(rooms: list[str]) -> list[list[float]]:
    """Expand a backend-approved room route through the matching visible doors."""
    if not rooms: return []
    points = [list(LAYOUT_WAYPOINTS[rooms[0]])]
    for start, target in zip(rooms, rooms[1:]):
        names = LAYOUT_CONNECTIONS.get(frozenset((start, target)))
        if names is None:
            raise ValueError(f"No presentation route from '{start}' to '{target}'.")
        if names[0] != start: names = tuple(reversed(names))
        points.extend(list(LAYOUT_WAYPOINTS[name]) for name in names[1:])
    return points


def layout_location(point: list[float]) -> str:
    x, y = point
    for room, data in HOUSE_LAYOUT["rooms"].items():
        left, bottom, right, top = data["bounds"]
        if left < x < right and bottom < y < top: return room
    return "hallway"
EVENTS = {"unexpected_obstacle", "high_temperature", "high_humidity", "blocked_transition", "observation_dropout", "low_initial_battery", "transient_false_reading"}
ALL_SKILLS = {"move_to_room", "inspect_room", "record_baseline", "establish_household_baseline", "inject_household_events", "revisit_active_event_rooms", "detect_environment_change", "update_digital_twin", "generate_alert", "generate_monitoring_report", "return_to_start", "stop"}


class House2DBackend(RobotBackend):
    name = "house2d"
    version = "1.0"

    def __init__(self, seed: int | None = None, events: list[str] | None = None, initial_battery: float | None = None, sensor_noise_bound: float = 0.0):
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31)
        self.requested_events = list(events or [])
        unknown = set(self.requested_events) - EVENTS
        if unknown:
            raise ValueError(f"Unknown house2d event: {sorted(unknown)[0]}")
        self.requested_battery = initial_battery
        self.sensor_noise_bound = max(0.0, float(sensor_noise_bound))
        self._rng = random.Random(self.seed)
        self._state: dict[str, Any] = {}
        self._ground_truth: dict[str, Any] = {}
        self._initial: dict[str, Any] = {}
        self._observations: list[dict[str, Any]] = []
        self._routes: list[dict[str, Any]] = []
        self._events_active = False
        self._transient_used = False
        self._application: HouseSitterApplication | None = None
        self._execution_failures: list[str] = []

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
            room = "kitchen" if kind in {"unexpected_obstacle", "high_temperature", "observation_dropout", "transient_false_reading"} else "bathroom" if kind == "high_humidity" else "living_room"
            parameters = {"doors": [["living_room", "kitchen"], ["bathroom", "kitchen"]]} if kind == "blocked_transition" else {}
            event_records.append({"event_id": f"event_{index:03d}", "type": kind, "room": room, "timestamp": None, "parameters": parameters})
        self._ground_truth = {"seed": self.seed, "rooms": rooms, "events": event_records, "doors": [list(door) for door in DOORS]}
        self._state = {"room": "charging_area", "pose": list(ROOMS["charging_area"]["center"]), "battery": float(battery), "time": 0.0, "stopped": False, "visit_history": ["charging_area"], "baselines": {}, "twin": {}, "alerts": []}
        self._initial = deepcopy(self.current_robot_state())
        self._observations, self._routes = [], []
        self._events_active, self._transient_used = False, False
        self._execution_failures = []
        self._application = HouseSitterApplication(task.name, self.seed)

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

    def _active_event_records(self, room: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        return self._event_records(room, kind) if self._events_active else []

    def _blocked_doors(self) -> set[frozenset[str]]:
        return {frozenset(door) for item in self._active_event_records(kind="blocked_transition") for door in item["parameters"]["doors"]}

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
        events = self._active_event_records(room)
        dropout = any(item["type"] == "observation_dropout" for item in events)
        truth = self._ground_truth["rooms"][room]
        visible = list(truth["static_objects"])
        if any(item["type"] == "unexpected_obstacle" for item in events):
            visible.append(f"{room}_unexpected_obstacle")
        accessibility = {}
        for a, b in DOORS:
            neighbor = b if a == room else a if b == room else None
            if neighbor is not None: accessibility[neighbor] = frozenset((room, neighbor)) not in self._blocked_doors()
        noise = self._rng.uniform(-self.sensor_noise_bound, self.sensor_noise_bound)
        transient = self._events_active and bool(self._active_event_records(kind="transient_false_reading")) and not self._transient_used
        self._transient_used = self._transient_used or transient
        observation = {
            "observation_id": f"observation:{room}:{len(self._observations)+1}:{self._state['time']:.3f}",
            "room": room, "timestamp": self._state["time"], "robot_state": {"room": room, "pose": list(self._state["pose"]), "battery": self._state["battery"]}, "visit_index": len(self._state["visit_history"]),
            "visible_object_identifiers": [] if dropout else visible, "obstacle_present": None if dropout else any(item["type"] == "unexpected_obstacle" for item in events),
            "temperature_c": None if dropout else (75.0 if any(item["type"] == "high_temperature" for item in events) else truth["temperature_c"] + noise + (12.0 if transient else 0.0)),
            "humidity_percent": None if dropout else (92.0 if any(item["type"] == "high_humidity" for item in events) else truth["humidity_percent"] + noise),
            "transition_accessibility": {} if dropout else accessibility,
            "battery": self._state["battery"], "active_event_identifiers": [item["event_id"] for item in events], "observation_valid": not dropout,
            "synthetic": True, "simulated_onboard_sensor": True, "simulation_only": True, "physical_robot_validated": False, "scenario_seed": self.seed,
        }
        self._observations.append(observation)
        if self._application is not None:
            self._application.observe(observation)
        return deepcopy(observation)

    def execute(self, skill: str, parameters: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        if self._state.get("stopped") and skill not in {"stop", "generate_monitoring_report"}:
            raise BackendError("Robot is stopped; reset is required before another action.")
        if skill in {"move_to_room", "revisit_active_event_rooms"}:
            return self._move(str(parameters["room"]), timeout_seconds)
        if skill == "return_to_start":
            result = self._move("charging_area", timeout_seconds); result["returned_to_start"] = True; return result
        if skill == "inspect_room":
            return self._inspect(str(parameters["room"]), timeout_seconds)
        if skill in {"record_baseline", "establish_household_baseline"}:
            room = str(parameters["room"]); latest = next((item for item in reversed(self._observations) if item["room"] == room), None)
            if latest is None or not latest["observation_valid"]:
                raise BackendError(f"A valid observation is required before recording '{room}' baseline.")
            assert self._application is not None
            self._application.observe(latest, baseline=True); self._state["baselines"][room] = latest; self._state["time"] += 1.0; return {"baseline_recorded": room, "simulation_only": True}
        if skill == "inject_household_events":
            self._events_active = True
            assert self._application is not None
            self._application.capture_before()
            for item in self._ground_truth["events"]: item["timestamp"] = self._state["time"]
            return {"events_injected": self.active_events(), "simulation_only": True}
        if skill == "detect_environment_change":
            assert self._application is not None
            room = str(parameters.get("room", self._state["room"])); found = self._application.detect(room); return {"room": room, "anomalies": found, "simulation_only": True}
        if skill == "update_digital_twin":
            assert self._application is not None
            room = str(parameters["room"]); update = self._application.update_twin(room); self._state["twin"][room] = update; return update
        if skill == "generate_alert":
            assert self._application is not None
            alert = self._application.generate_alert(str(parameters["room"]), str(parameters["anomaly_type"]));
            if alert.get("generated", True): self._state["alerts"].append(alert)
            return alert
        if skill == "generate_monitoring_report":
            assert self._application is not None
            return {"markdown": self._application.render_report(self._state, execution_success=not self._execution_failures), "simulation_only": True}
        if skill == "stop":
            return self.emergency_stop()
        raise BackendError(f"House2D has no implementation for '{skill}'.")

    def emergency_stop(self) -> dict[str, Any]:
        self._state["stopped"] = True
        return {"stopped": True, "emergency_stop": True, "simulation_time": self._state["time"]}

    def record_failure(self, message: str) -> None:
        self._execution_failures.append(message)

    def cleanup(self) -> None:
        return None

    def artifact_bundle(self) -> dict[str, Any]:
        application = self._application.artifacts() if self._application is not None else {}
        return {
            "simulator_config": {"backend_name": self.name, "backend_version": self.version, "rooms": ROOMS, "doors": [list(item) for item in DOORS], "movement_seconds_per_door": 5.0, "battery_per_door": 4.0, "sensor_noise_bound": self.sensor_noise_bound, "simulation_only": True, "physical_robot_validated": False},
            "scenario_seed": {"seed": self.seed}, "scenario_ground_truth": deepcopy(self._ground_truth), "initial_world_state": deepcopy(self._initial), "final_world_state": self.current_robot_state(),
            "sensor_observations": self.observations(), "route_trace": deepcopy(self._routes), **application,
        }
