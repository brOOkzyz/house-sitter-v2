"""Pure-function tests for the simulation-only Nav2 micro smoke helper."""

from __future__ import annotations

import importlib.util
import math
import sys
import types
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class AttrObject:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def install_ros_stubs() -> None:
    def module(name: str) -> types.ModuleType:
        value = types.ModuleType(name)
        sys.modules[name] = value
        return value

    rclpy = module("rclpy")
    rclpy.action = module("rclpy.action")
    rclpy.node = module("rclpy.node")
    rclpy.qos = module("rclpy.qos")
    geometry_msgs = module("geometry_msgs")
    geometry_msgs.msg = module("geometry_msgs.msg")
    irobot_create_msgs = module("irobot_create_msgs")
    irobot_create_msgs.msg = module("irobot_create_msgs.msg")
    nav2_msgs = module("nav2_msgs")
    nav2_msgs.action = module("nav2_msgs.action")
    nav_msgs = module("nav_msgs")
    nav_msgs.msg = module("nav_msgs.msg")
    sensor_msgs = module("sensor_msgs")
    sensor_msgs.msg = module("sensor_msgs.msg")

    class Node:
        pass

    class ActionClient:
        pass

    class QoSProfile:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ReliabilityPolicy:
        BEST_EFFORT = object()
        RELIABLE = object()

    class DurabilityPolicy:
        VOLATILE = object()
        TRANSIENT_LOCAL = object()

    class PoseStamped:
        def __init__(self):
            self.header = AttrObject(frame_id="", stamp=None)
            self.pose = AttrObject(
                position=AttrObject(x=0.0, y=0.0, z=0.0),
                orientation=AttrObject(x=0.0, y=0.0, z=0.0, w=1.0),
            )

    for msg_module, names in (
        (geometry_msgs.msg, ("PoseWithCovarianceStamped",)),
        (irobot_create_msgs.msg, ("DockStatus",)),
        (nav_msgs.msg, ("OccupancyGrid", "Odometry")),
        (sensor_msgs.msg, ("LaserScan",)),
    ):
        for name in names:
            setattr(msg_module, name, type(name, (), {}))

    geometry_msgs.msg.PoseStamped = PoseStamped
    nav2_msgs.action.ComputePathToPose = type("ComputePathToPose", (), {})
    nav2_msgs.action.NavigateToPose = type("NavigateToPose", (), {})
    rclpy.action.ActionClient = ActionClient
    rclpy.node.Node = Node
    rclpy.qos.QoSProfile = QoSProfile
    rclpy.qos.ReliabilityPolicy = ReliabilityPolicy
    rclpy.qos.DurabilityPolicy = DurabilityPolicy


def load_helper_module():
    install_ros_stubs()
    path = PROJECT_ROOT / "scripts" / "run_sim_nav2_micro_smoke.py"
    spec = importlib.util.spec_from_file_location("run_sim_nav2_micro_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_map(width=20, height=20, resolution=0.1, origin_x=-1.0, origin_y=-1.0):
    return AttrObject(
        info=AttrObject(
            width=width,
            height=height,
            resolution=resolution,
            origin=AttrObject(
                position=AttrObject(x=origin_x, y=origin_y),
                orientation=AttrObject(z=0.0, w=1.0),
            ),
        ),
        data=[0 for _ in range(width * height)],
    )


class SimNav2MicroSmokePureFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_helper_module()

    def test_yaw_quaternion_round_trip(self):
        yaw = 1.25
        z, w = self.helper.quaternion_from_yaw(yaw)
        self.assertAlmostEqual(self.helper.yaw_from_quaternion(z, w), yaw)

    def test_world_to_map_uses_origin_and_resolution(self):
        map_msg = make_map()
        self.assertEqual(self.helper.world_to_map(map_msg, -1.0, -1.0), (0, 0))
        self.assertEqual(self.helper.world_to_map(map_msg, 0.0, 0.0), (10, 10))
        self.assertIsNone(self.helper.world_to_map(map_msg, 2.0, 2.0))

    def test_check_free_space_accepts_clear_footprint(self):
        candidate = self.helper.check_free_space(make_map(), 0.0, 0.0)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.occupied_cells, 0)
        self.assertEqual(candidate.unknown_cells, 0)
        self.assertGreater(candidate.free_cells, 0)

    def test_check_free_space_rejects_obstacle_in_footprint(self):
        map_msg = make_map()
        mx, my = self.helper.world_to_map(map_msg, 0.0, 0.0)
        map_msg.data[my * map_msg.info.width + mx] = 100
        self.assertIsNone(self.helper.check_free_space(map_msg, 0.0, 0.0))

    def test_choose_candidate_keeps_micro_distance_limit(self):
        current = self.helper.Pose2D(x=0.0, y=0.0, yaw=0.0)
        candidate = self.helper.choose_candidate(current, make_map())
        self.assertIsNotNone(candidate)
        self.assertGreaterEqual(candidate.distance, self.helper.MIN_GOAL_DISTANCE_M)
        self.assertLessEqual(candidate.distance, self.helper.MAX_GOAL_DISTANCE_M)
        self.assertAlmostEqual(candidate.distance, 0.20)


if __name__ == "__main__":
    unittest.main()
