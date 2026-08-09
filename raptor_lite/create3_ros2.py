"""Fail-closed ROS 2 / iRobot Create 3 deployment boundary.

This module intentionally does not import ROS until ``RclpyRosGraph`` is
created.  That keeps House2D and the fake-graph tests independent of a ROS
installation, while the live implementation discovers the actual ROS graph.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from math import cos, sin
from time import monotonic
from typing import Any, Callable, Protocol

from .backends import BackendError, RobotBackend
from .capability_registry import CapabilityRegistry
from .models import CapabilitySpec, ParameterSpec, TaskSpec


TOPICS = {
    "battery": ("/battery_state", "sensor_msgs/msg/BatteryState"),
    "odometry": ("/odom", "nav_msgs/msg/Odometry"),
    "imu": ("/imu", "sensor_msgs/msg/Imu"),
    "hazards": ("/hazard_detection", "irobot_create_msgs/msg/HazardDetectionVector"),
    "dock_status": ("/dock_status", "irobot_create_msgs/msg/DockStatus"),
    "velocity_command": ("/cmd_vel", "geometry_msgs/msg/Twist"),
}
ACTIONS = {
    "drive_distance": ("/drive_distance", "irobot_create_msgs/action/DriveDistance"),
    "rotate_angle": ("/rotate_angle", "irobot_create_msgs/action/RotateAngle"),
    "drive_arc": ("/drive_arc", "irobot_create_msgs/action/DriveArc"),
    "navigate_to_position": ("/navigate_to_position", "irobot_create_msgs/action/NavigateToPosition"),
    "dock": ("/dock", "irobot_create_msgs/action/Dock"),
    "undock": ("/undock", "irobot_create_msgs/action/Undock"),
}
SERVICES = {"emergency_stop": ("/e_stop", "irobot_create_msgs/srv/EStop")}
NAV2 = ("/navigate_to_pose", "nav2_msgs/action/NavigateToPose")
SENSOR_SKILLS = {"read_battery", "read_odometry", "read_imu", "read_hazards", "read_dock_status", "observe_robot_state"}
MOTION_SKILLS = set(ACTIONS) | {"move_to_room", "return_to_start", "stop"}


class RosGraph(Protocol):
    """Small fakeable ROS graph; no shelling out and no implicit motion."""

    def snapshot(self) -> dict[str, dict[str, set[str]]]: ...
    def wait_for_message(self, topic: str, message_type: str, timeout_seconds: float) -> Any: ...
    def send_action(self, name: str, action_type: str, goal: dict[str, Any], timeout_seconds: float) -> dict[str, Any]: ...
    def call_service(self, name: str, service_type: str, request: dict[str, Any], timeout_seconds: float) -> dict[str, Any]: ...
    def close(self) -> None: ...


class NavigationProvider(Protocol):
    """Optional semantic layer.  Create 3 itself does not know room names."""

    def available_rooms(self) -> set[str]: ...
    def move_to_room(self, room: str, timeout_seconds: float) -> dict[str, Any]: ...
    def return_to_start(self, timeout_seconds: float) -> dict[str, Any]: ...


def _value(value: Any, *path: str, default: Any = None) -> Any:
    for key in path:
        if value is None:
            return default
        value = value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)
    return value


def _stamp(message: Any, fallback: float) -> float:
    sec = _value(message, "header", "stamp", "sec", default=None)
    nanosec = _value(message, "header", "stamp", "nanosec", default=None)
    if isinstance(sec, (int, float)):
        return float(sec) + float(nanosec or 0) / 1_000_000_000
    return fallback


@dataclass(frozen=True)
class CapabilityDiscovery:
    """The exact graph evidence used to make a verifier profile."""

    graph: dict[str, dict[str, list[str]]]
    available: set[str]
    unavailable: dict[str, str]
    nav2_available: bool
    room_navigation_available: bool

    @classmethod
    def from_graph(cls, graph: RosGraph, navigation: NavigationProvider | None = None) -> "CapabilityDiscovery":
        raw = graph.snapshot()
        normalized = {kind: {name: sorted(types) for name, types in raw.get(kind, {}).items()} for kind in ("topics", "actions", "services")}
        available: set[str] = set()
        unavailable: dict[str, str] = {}
        for kind, definitions in (("topics", TOPICS), ("actions", ACTIONS), ("services", SERVICES)):
            for capability, (name, expected_type) in definitions.items():
                types = set(raw.get(kind, {}).get(name, set()))
                if expected_type in types:
                    available.add(capability)
                else:
                    observed = ", ".join(sorted(types)) or "not advertised"
                    unavailable[capability] = f"{kind[:-1]} {name} requires {expected_type}; observed {observed}"
        nav_types = set(raw.get("actions", {}).get(NAV2[0], set()))
        nav2_available = NAV2[1] in nav_types
        if navigation is not None and nav2_available:
            available.add("named_room_navigation")
        else:
            reason = "no NavigationProvider configured" if navigation is None else f"action {NAV2[0]} requires {NAV2[1]}; observed {', '.join(sorted(nav_types)) or 'not advertised'}"
            unavailable["named_room_navigation"] = reason
        return cls(normalized, available, unavailable, nav2_available, "named_room_navigation" in available)

    def profile(self) -> dict[str, Any]:
        return {
            "backend": "create3_ros2", "physical_robot_validated": False,
            "implemented": True, "interface_mock_tested": True,
            "graph": deepcopy(self.graph), "available": sorted(self.available),
            "unavailable": dict(sorted(self.unavailable.items())),
            "nav2_available": self.nav2_available, "named_room_navigation_available": self.room_navigation_available,
        }

    def registry(self) -> CapabilityRegistry:
        specs = [
            _spec("read_battery", "Read the latest Create 3 battery message.", "battery"),
            _spec("read_odometry", "Read the latest Create 3 odometry message.", "odometry"),
            _spec("read_imu", "Read the latest Create 3 IMU message.", "imu"),
            _spec("read_hazards", "Read the latest Create 3 hazard vector.", "hazards"),
            _spec("read_dock_status", "Read the latest Create 3 dock status.", "dock_status"),
            _spec("observe_robot_state", "Adapt live robot sensors into the RaPToR-Lite observation schema.", "battery", "odometry", "hazards"),
            _spec("drive_distance", "Run the bounded Create 3 DriveDistance action.", "drive_distance", motion=True, parameters=[ParameterSpec(name="distance", type="number", required=True), ParameterSpec(name="max_translation_speed", type="number", required=True, minimum=0.01)]),
            _spec("rotate_angle", "Run the bounded Create 3 RotateAngle action.", "rotate_angle", motion=True, parameters=[ParameterSpec(name="angle", type="number", required=True), ParameterSpec(name="max_rotation_speed", type="number", required=True, minimum=0.01)]),
            _spec("drive_arc", "Run the bounded Create 3 DriveArc action.", "drive_arc", motion=True, parameters=[ParameterSpec(name="angle", type="number", required=True), ParameterSpec(name="radius", type="number", required=True, minimum=0.01), ParameterSpec(name="translate_direction", type="integer", required=True, allowed_values=[-1, 1]), ParameterSpec(name="max_translation_speed", type="number", required=True, minimum=0.01)]),
            _spec("navigate_to_position", "Run Create 3 odometry-frame NavigateToPosition.", "navigate_to_position", motion=True, parameters=[ParameterSpec(name="x", type="number", required=True), ParameterSpec(name="y", type="number", required=True), ParameterSpec(name="yaw", type="number", required=True), ParameterSpec(name="achieve_goal_heading", type="boolean", required=False, default=True)]),
            _spec("dock", "Run the Create 3 Dock action.", "dock", motion=True),
            _spec("undock", "Run the Create 3 Undock action.", "undock", motion=True),
            _spec("move_to_room", "Navigate to a provider-owned named room; never native Create 3 semantics.", "named_room_navigation", motion=True, parameters=[ParameterSpec(name="room", type="string", required=True)]),
            _spec("return_to_start", "Return using a provider-owned start waypoint.", "named_room_navigation", motion=True),
            _spec("stop", "Use the exposed Create 3 E-Stop service.", "emergency_stop", motion=True),
        ]
        return CapabilityRegistry(specs, self.available)


def _spec(name: str, description: str, *required: str, motion: bool = False, parameters: list[ParameterSpec] | None = None) -> CapabilitySpec:
    return CapabilitySpec(name=name, description=description, parameters=parameters or [], required_capabilities=list(required), timeout_seconds=30.0,
                          safety_constraints=["bounded_timeout", *( ["motion"] if motion else ["read_only"] )], execution_adapter="ros2",
                          simulation_supported=False, physical_robot_supported=True)


class Create3ObservationAdapter:
    """Adapt messages only; it never reads simulator state or scenario labels."""

    @staticmethod
    def adapt(messages: dict[str, tuple[Any, float]], *, room: str | None = None) -> dict[str, Any]:
        battery, received = messages.get("battery", (None, monotonic()))
        odometry, _ = messages.get("odometry", (None, received))
        hazards, _ = messages.get("hazards", (None, received))
        dock, _ = messages.get("dock_status", (None, received))
        imu, _ = messages.get("imu", (None, received))
        percentage = _value(battery, "percentage", default=None)
        if isinstance(percentage, (int, float)) and percentage <= 1.0:
            percentage *= 100.0
        detections = _value(hazards, "detections", default=[])
        detections = list(detections or [])
        pose = _value(odometry, "pose", "pose", default=None)
        position = _value(pose, "position", default=None)
        orientation = _value(pose, "orientation", default=None)
        missing = [name for name, item in (("battery", battery), ("odometry", odometry), ("hazards", hazards)) if item is None]
        # Create 3 has no native room-labelled vision or temperature/humidity feed.
        missing.extend(["room_semantics", "object_detection", "temperature", "humidity", "transition_accessibility"])
        timestamp = _stamp(odometry or battery or hazards, received)
        return {
            "observation_id": f"ros2:{timestamp:.9f}", "room": room or "unlabelled", "timestamp": timestamp,
            "robot_state": {"pose": {"x": _value(position, "x"), "y": _value(position, "y"), "orientation": {"x": _value(orientation, "x"), "y": _value(orientation, "y"), "z": _value(orientation, "z"), "w": _value(orientation, "w")}}, "battery": percentage, "is_docked": _value(dock, "is_docked")},
            "visible_object_identifiers": [], "obstacle_present": bool(detections) if hazards is not None else None,
            "temperature_c": None, "humidity_percent": None, "transition_accessibility": {}, "battery": percentage,
            "hazards": [{"type": _value(item, "type"), "frame_id": _value(item, "header", "frame_id")} for item in detections],
            "imu": {"angular_velocity_z": _value(imu, "angular_velocity", "z"), "linear_acceleration_z": _value(imu, "linear_acceleration", "z")} if imu is not None else None,
            "observation_valid": False, "unavailable_fields": missing, "synthetic": False, "simulated_onboard_sensor": False,
            "simulation_only": False, "physical_robot_validated": False,
        }


class Create3ROS2Backend(RobotBackend):
    """Create 3 executor with explicit authorization for every motion action."""

    name = "create3_ros2"
    version = "5.10"

    def __init__(self, graph: RosGraph | None = None, *, navigation: NavigationProvider | None = None, allow_motion: bool = False,
                 min_battery_percent: float = 15.0, stale_after_seconds: float = 2.0, clock: Callable[[], float] = monotonic):
        self._graph = graph
        self.navigation, self.allow_motion = navigation, allow_motion
        self.min_battery_percent, self.stale_after_seconds, self._clock = float(min_battery_percent), float(stale_after_seconds), clock
        self.discovery: CapabilityDiscovery | None = None
        self._samples: dict[str, tuple[Any, float]] = {}
        self._observations: list[dict[str, Any]] = []
        self._started = self._clock()
        self._failures: list[str] = []

    def _ensure_graph(self) -> RosGraph:
        if self._graph is None:
            self._graph = RclpyRosGraph()
        return self._graph

    def discover(self) -> CapabilityDiscovery:
        self.discovery = CapabilityDiscovery.from_graph(self._ensure_graph(), self.navigation)
        return self.discovery

    def initialize(self, task: TaskSpec) -> None:
        self._started, self._failures = self._clock(), []
        self.discover()

    def available_capabilities(self) -> set[str]:
        return set((self.discovery or self.discover()).available)

    def current_robot_state(self) -> dict[str, Any]:
        observation = Create3ObservationAdapter.adapt(self._samples)
        return {"backend": self.name, "observation": observation, "physical_robot_validated": False}

    def simulation_time(self) -> float:
        return self._clock() - self._started

    def active_events(self) -> list[str]:
        hazards = self._samples.get("hazards", (None, 0))[0]
        return [str(_value(item, "type", default="hazard")) for item in (_value(hazards, "detections", default=[]) or [])]

    def observations(self) -> list[dict[str, Any]]:
        return deepcopy(self._observations)

    def _require(self, capability: str) -> tuple[str, str]:
        discovery = self.discovery or self.discover()
        if capability not in discovery.available:
            raise BackendError(f"Create3 capability '{capability}' is unavailable: {discovery.unavailable.get(capability, 'not discovered')}.")
        return TOPICS.get(capability) or ACTIONS.get(capability) or SERVICES.get(capability) or ("", "")

    def _read(self, capability: str, timeout_seconds: float) -> Any:
        name, message_type = self._require(capability)
        try:
            message = self._ensure_graph().wait_for_message(name, message_type, timeout_seconds)
        except TimeoutError as exc:
            raise BackendError(f"Timed out after {timeout_seconds:.1f}s waiting for {name}.") from exc
        except Exception as exc:
            raise BackendError(f"Communication failure reading {name}: {exc}") from exc
        if message is None:
            raise BackendError(f"Communication failure reading {name}: no message returned.")
        self._samples[capability] = (message, self._clock())
        return message

    def _fresh(self, capability: str) -> Any:
        message, received = self._samples.get(capability, (None, 0.0))
        age = self._clock() - received
        if message is None or age > self.stale_after_seconds:
            raise BackendError(f"Stale sensor '{capability}' (age {age:.2f}s; limit {self.stale_after_seconds:.2f}s).")
        return message

    def _motion_guard(self) -> None:
        if not self.allow_motion:
            raise BackendError("Motion is disabled for this backend instance; explicit operator authorization is required.")
        battery = self._fresh("battery")
        percentage = _value(battery, "percentage", default=None)
        if not isinstance(percentage, (int, float)):
            raise BackendError("Battery message has no usable percentage; motion is denied.")
        percent = percentage * 100.0 if percentage <= 1.0 else percentage
        if percent < self.min_battery_percent:
            raise BackendError(f"Battery {percent:.1f}% is below the configured {self.min_battery_percent:.1f}% motion threshold.")
        hazards = self._fresh("hazards")
        if _value(hazards, "detections", default=[]):
            raise BackendError("Active hazard detection blocks motion; inspect and clear the hazard before retrying.")

    def _action(self, capability: str, parameters: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        name, action_type = self._require(capability)
        self._motion_guard()
        if capability == "navigate_to_position":
            yaw = float(parameters["yaw"])
            parameters = {"achieve_goal_heading": parameters.get("achieve_goal_heading", True), "goal_pose": {"pose": {"position": {"x": float(parameters["x"]), "y": float(parameters["y"]), "z": 0.0}, "orientation": {"x": 0.0, "y": 0.0, "z": sin(yaw / 2), "w": cos(yaw / 2)}}}}
        try:
            result = self._ensure_graph().send_action(name, action_type, parameters, timeout_seconds)
        except TimeoutError as exc:
            raise BackendError(f"Action {name} timed out after {timeout_seconds:.1f}s and was cancelled.") from exc
        except Exception as exc:
            raise BackendError(f"Communication failure running {name}: {exc}") from exc
        if result.get("status") != "succeeded":
            raise BackendError(f"Action {name} failed: {result.get('message', result.get('status', 'unknown status'))}.")
        return {"action": name, "status": "succeeded", "result": result, "physical_robot_validated": False}

    def execute(self, skill: str, parameters: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        if timeout_seconds <= 0:
            raise BackendError("A positive bounded timeout is required.")
        readers = {"read_battery": "battery", "read_odometry": "odometry", "read_imu": "imu", "read_hazards": "hazards", "read_dock_status": "dock_status"}
        if skill in readers:
            message = self._read(readers[skill], timeout_seconds)
            return {"skill": skill, "message_received": True, "timestamp": _stamp(message, self._clock()), "physical_robot_validated": False}
        if skill == "observe_robot_state":
            for capability in ("battery", "odometry", "hazards"):
                self._read(capability, timeout_seconds)
            for capability in ("dock_status", "imu"):
                if capability in self.available_capabilities():
                    self._read(capability, timeout_seconds)
            observation = Create3ObservationAdapter.adapt(self._samples, room=parameters.get("room"))
            self._observations.append(observation)
            return deepcopy(observation)
        if skill in ACTIONS:
            return self._action(skill, parameters, timeout_seconds)
        if skill == "move_to_room":
            self._require("named_room_navigation"); self._motion_guard()
            assert self.navigation is not None
            room = str(parameters.get("room", ""))
            if room not in self.navigation.available_rooms():
                raise BackendError(f"NavigationProvider has no waypoint for room '{room}'.")
            return self.navigation.move_to_room(room, timeout_seconds)
        if skill == "return_to_start":
            self._require("named_room_navigation"); self._motion_guard()
            assert self.navigation is not None
            return self.navigation.return_to_start(timeout_seconds)
        if skill == "stop":
            return self.emergency_stop()
        raise BackendError(f"Create3ROS2Backend has no implementation for '{skill}'.")

    def emergency_stop(self) -> dict[str, Any]:
        name, service_type = self._require("emergency_stop")
        if not self.allow_motion:
            raise BackendError("E-stop service invocation is disabled until explicit operator authorization is provided.")
        try:
            result = self._ensure_graph().call_service(name, service_type, {"e_stop_on": True}, 5.0)
        except Exception as exc:
            raise BackendError(f"Communication failure invoking {name}: {exc}") from exc
        return {"stopped": bool(result.get("success", True)), "service": name, "physical_robot_validated": False}

    def record_failure(self, message: str) -> None:
        self._failures.append(message)

    def cleanup(self) -> None:
        if self._graph is not None:
            self._graph.close()

    def artifact_bundle(self) -> dict[str, Any]:
        return {"deployment_readiness": (self.discovery or self.discover()).profile(), "sensor_observations": self.observations(),
                "execution_failures": list(self._failures), "physical_robot_validated": False}


class RclpyRosGraph:
    """Thin real ROS implementation used only after explicit backend creation."""

    def __init__(self) -> None:
        try:
            import rclpy
        except Exception as exc:
            raise BackendError(f"ROS unavailable: cannot import rclpy ({exc}).") from exc
        self.rclpy = rclpy
        self._owns_context = not rclpy.ok()
        rclpy.init(args=None) if self._owns_context else None
        self.node = rclpy.create_node("raptor_lite_create3_readiness")

    def snapshot(self) -> dict[str, dict[str, set[str]]]:
        services = {name: set(types) for name, types in self.node.get_service_names_and_types()}
        # Jazzy rclpy exposes no Node.get_action_names_and_types().  Every ROS
        # action server advertises its send_goal service, which carries the
        # generated ``<Action>_SendGoal`` type, so derive only that graph fact.
        actions: dict[str, set[str]] = {}
        for name, types in services.items():
            if not name.endswith("/_action/send_goal"):
                continue
            action_name = name.removesuffix("/_action/send_goal")
            action_types = {item.removesuffix("_SendGoal") for item in types if item.endswith("_SendGoal")}
            if action_types:
                actions[action_name] = action_types
        return {"topics": {name: set(types) for name, types in self.node.get_topic_names_and_types()}, "actions": actions, "services": services}

    def wait_for_message(self, topic: str, message_type: str, timeout_seconds: float) -> Any:
        from rosidl_runtime_py.utilities import get_message
        received: list[Any] = []
        subscription = self.node.create_subscription(get_message(message_type), topic, received.append, 10)
        deadline = monotonic() + timeout_seconds
        try:
            while not received and monotonic() < deadline:
                self.rclpy.spin_once(self.node, timeout_sec=min(0.1, max(0.0, deadline - monotonic())))
        finally:
            self.node.destroy_subscription(subscription)
        if not received:
            raise TimeoutError(topic)
        return received[-1]

    def send_action(self, name: str, action_type: str, goal: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        from rclpy.action import ActionClient
        from rosidl_runtime_py.utilities import get_action
        client = ActionClient(self.node, get_action(action_type), name)
        if not client.wait_for_server(timeout_sec=timeout_seconds):
            raise TimeoutError(f"action server {name}")
        request = get_action(action_type).Goal()
        _assign(request, goal)
        future = client.send_goal_async(request)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout_seconds)
        handle = future.result()
        if handle is None or not handle.accepted:
            return {"status": "rejected"}
        result_future = handle.get_result_async()
        self.rclpy.spin_until_future_complete(self.node, result_future, timeout_sec=timeout_seconds)
        if result_future.result() is None:
            handle.cancel_goal_async()
            raise TimeoutError(name)
        return {"status": "succeeded" if result_future.result().status == 4 else "failed", "result": str(result_future.result().result)}

    def call_service(self, name: str, service_type: str, request: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
        from rosidl_runtime_py.utilities import get_service
        service = get_service(service_type)
        client = self.node.create_client(service, name)
        if not client.wait_for_service(timeout_sec=timeout_seconds):
            raise TimeoutError(f"service {name}")
        payload = service.Request(); _assign(payload, request)
        future = client.call_async(payload)
        self.rclpy.spin_until_future_complete(self.node, future, timeout_sec=timeout_seconds)
        if future.result() is None:
            raise TimeoutError(name)
        return {"success": bool(getattr(future.result(), "success", True))}

    def close(self) -> None:
        if getattr(self, "node", None) is not None:
            self.node.destroy_node(); self.node = None
        if self._owns_context and self.rclpy.ok():
            self.rclpy.shutdown()


def _assign(target: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        current = getattr(target, key)
        if isinstance(value, dict):
            _assign(current, value)
        else:
            setattr(target, key, value)
