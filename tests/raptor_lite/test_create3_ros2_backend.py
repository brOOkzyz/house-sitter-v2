from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from raptor_lite import issue_codes as codes
from raptor_lite.backends import BackendError
from raptor_lite.create3_ros2 import ACTIONS, SERVICES, TOPICS, CapabilityDiscovery, Create3ROS2Backend
from raptor_lite.executor import BackendExecutor
from raptor_lite.task_schema import load_task
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "examples/raptor_lite/create3_ros2_dry_run_plan.json"


class FakeGraph:
    def __init__(self, *, missing: set[str] = set(), action_result: dict | Exception | None = None):
        self.closed, self.action_result, self.calls = False, action_result or {"status": "succeeded"}, []
        self.graph = {
            "topics": {name: {message_type} for capability, (name, message_type) in TOPICS.items() if capability not in missing},
            "actions": {name: {action_type} for capability, (name, action_type) in ACTIONS.items() if capability not in missing},
            "services": {name: {service_type} for capability, (name, service_type) in SERVICES.items() if capability not in missing},
        }
        self.messages = {
            "/battery_state": NS(percentage=0.8, header=NS(stamp=NS(sec=10, nanosec=0))),
            "/odom": NS(header=NS(stamp=NS(sec=11, nanosec=0)), pose=NS(pose=NS(position=NS(x=1.5, y=-0.5), orientation=NS(x=0.0, y=0.0, z=0.0, w=1.0)))),
            "/imu": NS(header=NS(stamp=NS(sec=12, nanosec=0)), angular_velocity=NS(z=0.2), linear_acceleration=NS(z=9.8)),
            "/hazard_detection": NS(detections=[]), "/dock_status": NS(is_docked=True),
        }

    def snapshot(self): return self.graph
    def wait_for_message(self, topic, message_type, timeout_seconds):
        self.calls.append(("read", topic, message_type, timeout_seconds))
        value = self.messages.get(topic)
        if isinstance(value, Exception): raise value
        if value is None: raise TimeoutError(topic)
        return value
    def send_action(self, name, action_type, goal, timeout_seconds):
        self.calls.append(("action", name, action_type, goal, timeout_seconds))
        if isinstance(self.action_result, Exception): raise self.action_result
        return self.action_result
    def call_service(self, name, service_type, request, timeout_seconds):
        self.calls.append(("service", name, service_type, request, timeout_seconds)); return {"success": True}
    def close(self): self.closed = True


def complete_discovery():
    return CapabilityDiscovery.from_graph(FakeGraph())


def test_discovery_generates_capability_profile_and_verifies_read_only_json_plan():
    discovery = complete_discovery()
    assert {"battery", "odometry", "imu", "hazards", "dock_status", "drive_distance", "dock", "undock"} <= discovery.available
    assert "velocity_command" in discovery.available
    assert "named_room_navigation" not in discovery.available
    assert "no NavigationProvider" in discovery.unavailable["named_room_navigation"]
    task = load_task(PLAN); report = verify_task(task, discovery.registry())
    assert report.approved


def test_missing_graph_interface_is_unavailable_and_verifier_rejects_plan():
    discovery = CapabilityDiscovery.from_graph(FakeGraph(missing={"imu"}))
    assert "imu" not in discovery.available and "/imu" in discovery.unavailable["imu"]
    report = verify_task(load_task(PLAN), discovery.registry())
    assert not report.approved and codes.UNSUPPORTED_CAPABILITY in [item.issue_code for item in report.issues]
    backend = Create3ROS2Backend(FakeGraph(missing={"dock"}), allow_motion=True); backend.discover()
    with pytest.raises(BackendError, match="dock.*unavailable"):
        backend.execute("dock", {}, 1)


def test_ros_unavailable_is_explicit(monkeypatch):
    import raptor_lite.create3_ros2 as module
    monkeypatch.setattr(module, "RclpyRosGraph", lambda: (_ for _ in ()).throw(BackendError("ROS unavailable: test environment")))
    with pytest.raises(BackendError, match="ROS unavailable"):
        Create3ROS2Backend().discover()


def test_fake_graph_executes_only_verified_read_plan_and_adapts_without_ground_truth():
    graph = FakeGraph(); backend = Create3ROS2Backend(graph)
    discovery = backend.discover(); task = load_task(PLAN); report = verify_task(task, discovery.registry()); assert report.approved
    result, _ = BackendExecutor(backend).run(task, report, discovery.registry())
    observation = backend.observations()[0]
    assert result.success and graph.closed
    assert observation["synthetic"] is False and observation["physical_robot_validated"] is False
    assert observation["robot_state"]["battery"] == 80.0 and observation["obstacle_present"] is False
    assert observation["observation_valid"] is False and "temperature" in observation["unavailable_fields"]
    assert "ground_truth" not in json.dumps(observation)


def test_no_navigation_provider_means_room_navigation_is_unavailable():
    backend = Create3ROS2Backend(FakeGraph(), allow_motion=True)
    backend.discover()
    with pytest.raises(BackendError, match="named_room_navigation.*unavailable"):
        backend.execute("move_to_room", {"room": "kitchen"}, 1)


def test_motion_is_explicitly_denied_then_low_battery_hazard_stale_and_timeout_fail_closed():
    graph = FakeGraph(); backend = Create3ROS2Backend(graph); backend.discover()
    with pytest.raises(BackendError, match="Motion is disabled"):
        backend.execute("drive_distance", {"distance": 0.2, "max_translation_speed": 0.1}, 1)
    low = FakeGraph(); low.messages["/battery_state"] = NS(percentage=0.05)
    backend = Create3ROS2Backend(low, allow_motion=True); backend.discover()
    backend.execute("read_battery", {}, 1); backend.execute("read_hazards", {}, 1)
    with pytest.raises(BackendError, match="below"):
        backend.execute("drive_distance", {"distance": 0.2, "max_translation_speed": 0.1}, 1)
    hazard = FakeGraph(); hazard.messages["/hazard_detection"] = NS(detections=[NS(type=1)])
    backend = Create3ROS2Backend(hazard, allow_motion=True); backend.discover()
    backend.execute("read_battery", {}, 1); backend.execute("read_hazards", {}, 1)
    with pytest.raises(BackendError, match="Active hazard"):
        backend.execute("rotate_angle", {"angle": 0.2, "max_rotation_speed": 0.1}, 1)
    now = [0.0]; stale = Create3ROS2Backend(FakeGraph(), allow_motion=True, stale_after_seconds=1, clock=lambda: now[0]); stale.discover()
    stale.execute("read_battery", {}, 1); stale.execute("read_hazards", {}, 1); now[0] = 2.0
    with pytest.raises(BackendError, match="Stale sensor"):
        stale.execute("dock", {}, 1)
    timeout = Create3ROS2Backend(FakeGraph(action_result=TimeoutError("lost link")), allow_motion=True); timeout.discover()
    timeout.execute("read_battery", {}, 1); timeout.execute("read_hazards", {}, 1)
    with pytest.raises(BackendError, match="timed out.*cancelled"):
        timeout.execute("undock", {}, 1)


def test_bad_action_status_and_sensor_communication_failure_are_explained():
    graph = FakeGraph(action_result={"status": "rejected", "message": "dock already occupied"})
    backend = Create3ROS2Backend(graph, allow_motion=True); backend.discover()
    backend.execute("read_battery", {}, 1); backend.execute("read_hazards", {}, 1)
    with pytest.raises(BackendError, match="dock already occupied"):
        backend.execute("dock", {}, 1)
    broken = FakeGraph(); broken.messages["/odom"] = ConnectionError("DDS lost")
    backend = Create3ROS2Backend(broken); backend.discover()
    with pytest.raises(BackendError, match="Communication failure reading /odom"):
        backend.execute("read_odometry", {}, 1)


def test_motion_action_parameters_are_verifiable_and_navigation_goal_is_real_ros_shape():
    discovery = complete_discovery()
    task = {
        "task_id": "position", "name": "Bounded position", "description": "Explicit test only.", "robot_profile": "create3",
        "metadata": {"execution_mode": "ros2"},
        "steps": [{"step_id": "position", "skill": "navigate_to_position", "parameters": {"x": 1.0, "y": 2.0, "yaw": 1.57, "achieve_goal_heading": True}, "timeout_seconds": 3, "on_failure": "abort"}, {"step_id": "stop", "skill": "stop", "parameters": {}, "timeout_seconds": 3, "on_failure": "stop"}],
    }
    assert verify_task(task, discovery.registry()).approved
    graph = FakeGraph(); backend = Create3ROS2Backend(graph, allow_motion=True); backend.discover()
    backend.execute("read_battery", {}, 1); backend.execute("read_hazards", {}, 1)
    backend.execute("navigate_to_position", task["steps"][0]["parameters"], 1)
    goal = next(call for call in graph.calls if call[0] == "action")[3]
    assert goal["goal_pose"]["pose"]["position"] == {"x": 1.0, "y": 2.0, "z": 0.0}
    assert goal["goal_pose"]["pose"]["orientation"]["w"] > 0
