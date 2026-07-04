#!/usr/bin/env python3
"""Simulation-only Nav2 micro navigation smoke test.

This script never publishes velocity commands. It checks the current AMCL pose,
map occupancy, dock state, and Nav2 planning before sending one very small
NavigateToPose goal through the Nav2 action server.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import rclpy
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from irobot_create_msgs.msg import DockStatus
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import LaserScan


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
MAX_GOAL_DISTANCE_M = 0.35
MIN_GOAL_DISTANCE_M = 0.20
FOOTPRINT_RADIUS_M = 0.18
OCCUPIED_THRESHOLD = 50


@dataclass
class Pose2D:
    x: float
    y: float
    yaw: float


@dataclass
class Candidate:
    x: float
    y: float
    yaw: float
    distance: float
    checked_cells: int
    free_cells: int
    unknown_cells: int
    occupied_cells: int


class SimNav2MicroSmoke(Node):
    def __init__(self) -> None:
        super().__init__("house_sitter_v2_sim_nav2_micro_smoke")
        self.amcl_pose: Optional[PoseWithCovarianceStamped] = None
        self.map_msg: Optional[OccupancyGrid] = None
        self.dock_status: Optional[DockStatus] = None
        self.odom: Optional[Odometry] = None
        self.scan: Optional[LaserScan] = None

        live_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )
        map_qos = QoSProfile(
            depth=1,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )

        self.create_subscription(PoseWithCovarianceStamped, "/amcl_pose", self._amcl_cb, map_qos)
        self.create_subscription(OccupancyGrid, "/map", self._map_cb, map_qos)
        self.create_subscription(DockStatus, "/dock_status", self._dock_cb, live_qos)
        self.create_subscription(Odometry, "/odom", self._odom_cb, live_qos)
        self.create_subscription(LaserScan, "/scan", self._scan_cb, live_qos)

        self.compute_client = ActionClient(
            self, ComputePathToPose, "/compute_path_to_pose"
        )
        self.navigate_client = ActionClient(self, NavigateToPose, "/navigate_to_pose")

    def _amcl_cb(self, msg: PoseWithCovarianceStamped) -> None:
        self.amcl_pose = msg

    def _map_cb(self, msg: OccupancyGrid) -> None:
        self.map_msg = msg

    def _dock_cb(self, msg: DockStatus) -> None:
        self.dock_status = msg

    def _odom_cb(self, msg: Odometry) -> None:
        self.odom = msg

    def _scan_cb(self, msg: LaserScan) -> None:
        self.scan = msg

    def wait_for_inputs(self, timeout_sec: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            rclpy.spin_once(self, timeout_sec=0.1)
            if (
                self.amcl_pose is not None
                and self.map_msg is not None
                and self.dock_status is not None
                and self.odom is not None
                and self.scan is not None
            ):
                return True
        return False


def yaw_from_quaternion(z: float, w: float) -> float:
    return math.atan2(2.0 * w * z, 1.0 - 2.0 * z * z)


def quaternion_from_yaw(yaw: float) -> tuple[float, float]:
    return math.sin(yaw / 2.0), math.cos(yaw / 2.0)


def pose_from_amcl(msg: PoseWithCovarianceStamped) -> Pose2D:
    pose = msg.pose.pose
    return Pose2D(
        x=float(pose.position.x),
        y=float(pose.position.y),
        yaw=yaw_from_quaternion(float(pose.orientation.z), float(pose.orientation.w)),
    )


def map_origin_yaw(map_msg: OccupancyGrid) -> float:
    q = map_msg.info.origin.orientation
    return yaw_from_quaternion(float(q.z), float(q.w))


def world_to_map(map_msg: OccupancyGrid, x: float, y: float) -> Optional[tuple[int, int]]:
    origin = map_msg.info.origin.position
    resolution = float(map_msg.info.resolution)
    yaw = map_origin_yaw(map_msg)
    dx = x - float(origin.x)
    dy = y - float(origin.y)
    mx_f = (math.cos(yaw) * dx + math.sin(yaw) * dy) / resolution
    my_f = (-math.sin(yaw) * dx + math.cos(yaw) * dy) / resolution
    mx = int(math.floor(mx_f))
    my = int(math.floor(my_f))
    if mx < 0 or my < 0 or mx >= map_msg.info.width or my >= map_msg.info.height:
        return None
    return mx, my


def occupancy_at(map_msg: OccupancyGrid, mx: int, my: int) -> int:
    return int(map_msg.data[my * map_msg.info.width + mx])


def check_free_space(map_msg: OccupancyGrid, x: float, y: float) -> Optional[Candidate]:
    center = world_to_map(map_msg, x, y)
    if center is None:
        return None

    resolution = float(map_msg.info.resolution)
    radius_cells = max(1, int(math.ceil(FOOTPRINT_RADIUS_M / resolution)))
    checked = free = unknown = occupied = 0
    cx, cy = center

    for dy in range(-radius_cells, radius_cells + 1):
        for dx in range(-radius_cells, radius_cells + 1):
            if math.hypot(dx * resolution, dy * resolution) > FOOTPRINT_RADIUS_M:
                continue
            mx = cx + dx
            my = cy + dy
            if mx < 0 or my < 0 or mx >= map_msg.info.width or my >= map_msg.info.height:
                occupied += 1
                checked += 1
                continue
            value = occupancy_at(map_msg, mx, my)
            checked += 1
            if value < 0:
                unknown += 1
            elif value >= OCCUPIED_THRESHOLD:
                occupied += 1
            else:
                free += 1

    if checked == 0 or occupied > 0 or unknown > 0:
        return None
    return Candidate(x=x, y=y, yaw=0.0, distance=0.0, checked_cells=checked,
                     free_cells=free, unknown_cells=unknown, occupied_cells=occupied)


def choose_candidate(current: Pose2D, map_msg: OccupancyGrid) -> Optional[Candidate]:
    candidates: list[Candidate] = []
    radii = (0.20, 0.25, 0.30, 0.35)
    angle_offsets = (0.0, math.pi / 6.0, -math.pi / 6.0, math.pi / 3.0,
                     -math.pi / 3.0, math.pi / 2.0, -math.pi / 2.0, math.pi)

    for radius in radii:
        for offset in angle_offsets:
            heading = current.yaw + offset
            x = current.x + radius * math.cos(heading)
            y = current.y + radius * math.sin(heading)
            checked = check_free_space(map_msg, x, y)
            if checked is None:
                continue
            checked.yaw = current.yaw
            checked.distance = math.hypot(x - current.x, y - current.y)
            if MIN_GOAL_DISTANCE_M <= checked.distance <= MAX_GOAL_DISTANCE_M:
                candidates.append(checked)

    candidates.sort(key=lambda item: (item.distance, -item.free_cells))
    return candidates[0] if candidates else None


def make_goal_pose(node: Node, candidate: Candidate) -> PoseStamped:
    pose = PoseStamped()
    pose.header.frame_id = "map"
    pose.header.stamp = node.get_clock().now().to_msg()
    pose.pose.position.x = candidate.x
    pose.pose.position.y = candidate.y
    z, w = quaternion_from_yaw(candidate.yaw)
    pose.pose.orientation.z = z
    pose.pose.orientation.w = w
    return pose


def wait_for_action(client: ActionClient, node: Node, name: str, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if client.server_is_ready():
            return True
        rclpy.spin_once(node, timeout_sec=0.1)
    print(f"FAIL: action server unavailable: {name}")
    return False


def send_compute_path(node: SimNav2MicroSmoke, candidate: Candidate) -> tuple[bool, str]:
    goal = ComputePathToPose.Goal()
    goal.goal = make_goal_pose(node, candidate)
    if hasattr(goal, "use_start"):
        goal.use_start = False
    if hasattr(goal, "planner_id"):
        goal.planner_id = ""

    future = node.compute_client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    if not future.done():
        return False, "send_goal timeout"
    handle = future.result()
    if handle is None or not handle.accepted:
        return False, "goal rejected"

    result_future = handle.get_result_async()
    rclpy.spin_until_future_complete(node, result_future, timeout_sec=20.0)
    if not result_future.done():
        return False, "result timeout"
    result = result_future.result().result
    path_len = len(result.path.poses) if hasattr(result, "path") else 0
    if path_len <= 0:
        return False, "empty path"
    return True, f"path poses={path_len}"


def send_navigate(node: SimNav2MicroSmoke, candidate: Candidate) -> tuple[str, str]:
    goal = NavigateToPose.Goal()
    goal.pose = make_goal_pose(node, candidate)

    future = node.navigate_client.send_goal_async(goal)
    rclpy.spin_until_future_complete(node, future, timeout_sec=10.0)
    if not future.done():
        return "TIMEOUT", "send_goal timeout"
    handle = future.result()
    if handle is None or not handle.accepted:
        return "FAILED", "goal rejected"

    result_future = handle.get_result_async()
    rclpy.spin_until_future_complete(node, result_future, timeout_sec=60.0)
    if not result_future.done():
        return "TIMEOUT", "result timeout"
    wrapped = result_future.result()
    if wrapped.status == 4:
        return "SUCCEEDED", f"status={wrapped.status}"
    return "FAILED", f"status={wrapped.status}"


def main() -> int:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"sim_nav2_micro_smoke_{timestamp}.json"
    summary_path = LOG_DIR / "latest_sim_nav2_micro_smoke_summary.txt"

    result: dict[str, object] = {
        "start_time": timestamp,
        "simulation_confirmed": False,
        "navigate_to_pose_sent": False,
        "navigate_to_pose_result": "NOT_SENT",
        "log_file": str(log_path),
    }

    rclpy.init()
    node = SimNav2MicroSmoke()
    exit_code = 1

    try:
        if not node.wait_for_inputs():
            missing = [
                name
                for name, value in (
                    ("/amcl_pose", node.amcl_pose),
                    ("/map", node.map_msg),
                    ("/dock_status", node.dock_status),
                    ("/odom", node.odom),
                    ("/scan", node.scan),
                )
                if value is None
            ]
            result["missing_inputs"] = missing
            raise RuntimeError(f"required ROS inputs were not received: {missing}")
        result["simulation_confirmed"] = True

        if node.dock_status is None or node.dock_status.is_docked:
            result["final_is_docked"] = None if node.dock_status is None else bool(node.dock_status.is_docked)
            raise RuntimeError("robot is docked; navigation goal will not be sent")

        if not wait_for_action(node.compute_client, node, "/compute_path_to_pose", 10.0):
            raise RuntimeError("/compute_path_to_pose action server unavailable")
        if not wait_for_action(node.navigate_client, node, "/navigate_to_pose", 10.0):
            raise RuntimeError("/navigate_to_pose action server unavailable")

        current = pose_from_amcl(node.amcl_pose)
        candidate = choose_candidate(current, node.map_msg)
        result["current_pose"] = asdict(current)
        if candidate is None:
            result["map_free_space_check"] = "FAIL"
            raise RuntimeError("no nearby free-space candidate was found")

        result["selected_goal"] = asdict(candidate)
        result["goal_distance"] = candidate.distance
        result["map_free_space_check"] = "PASS"

        print(f"PASS: current pose x={current.x:.3f}, y={current.y:.3f}, yaw={current.yaw:.3f}")
        print(
            "PASS: selected candidate goal "
            f"x={candidate.x:.3f}, y={candidate.y:.3f}, yaw={candidate.yaw:.3f}"
        )
        print(f"PASS: goal distance {candidate.distance:.3f} m")
        print(
            "PASS: map occupancy check "
            f"free={candidate.free_cells}, unknown={candidate.unknown_cells}, "
            f"occupied={candidate.occupied_cells}, checked={candidate.checked_cells}"
        )
        print("INFO: plan-only action /compute_path_to_pose")

        path_ok, path_detail = send_compute_path(node, candidate)
        result["compute_path_to_pose"] = "PASS" if path_ok else "FAIL"
        result["compute_path_to_pose_detail"] = path_detail
        if not path_ok:
            raise RuntimeError(f"compute_path_to_pose failed: {path_detail}")
        print(f"PASS: compute_path_to_pose {path_detail}")

        print("INFO: navigation action /navigate_to_pose")
        nav_status, nav_detail = send_navigate(node, candidate)
        result["navigate_to_pose_sent"] = True
        result["navigate_to_pose_result"] = nav_status
        result["navigate_to_pose_detail"] = nav_detail
        print(f"{'PASS' if nav_status == 'SUCCEEDED' else 'FAIL'}: navigate_to_pose {nav_status} {nav_detail}")
        if nav_status != "SUCCEEDED":
            raise RuntimeError(f"navigate_to_pose failed: {nav_status} {nav_detail}")

        if not node.wait_for_inputs(timeout_sec=5.0):
            raise RuntimeError("post-navigation ROS inputs were not refreshed")
        result["final_is_docked"] = bool(node.dock_status.is_docked)
        result["final_odom"] = node.odom is not None
        result["final_scan"] = node.scan is not None
        result["final_amcl_pose"] = node.amcl_pose is not None
        exit_code = 0
    except Exception as exc:
        result["error"] = str(exc)
        print(f"FAIL: {exc}")
    finally:
        log_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary_lines = [
            f"start time: {result.get('start_time')}",
            f"current pose: {result.get('current_pose')}",
            f"selected goal: {result.get('selected_goal')}",
            f"movement distance: {result.get('goal_distance')}",
            f"compute_path_to_pose result: {result.get('compute_path_to_pose')}",
            f"navigate_to_pose result: {result.get('navigate_to_pose_result')}",
            f"final dock status: {result.get('final_is_docked')}",
            "final Nav2 readiness: pending external check",
            f"log file path: {log_path}",
        ]
        summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")
        print(f"INFO: wrote log {log_path}")
        print(f"INFO: wrote summary {summary_path}")
        node.destroy_node()
        rclpy.shutdown()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
