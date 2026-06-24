"""Safe adapter from named waypoints to Nav2 NavigateToPose goals.

Importing this module does not initialize ROS 2 or contact the ROS graph. ROS
dependencies are imported only when Nav2ActionClient is constructed.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict


class WaypointConfigError(ValueError):
    """Raised when the waypoint configuration is missing or unsafe."""


@dataclass(frozen=True)
class NavigateToPoseSpec:
    """ROS-independent representation of a Nav2 pose goal."""

    waypoint: str
    frame_id: str
    x: float
    y: float
    yaw: float
    orientation_z: float
    orientation_w: float


class WaypointStore:
    """Load named waypoints and convert them to map-frame pose specifications."""

    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = self._load(config_path)
        self.frame_id = self.config.get("frame_id")
        self.mock_only = self.config.get("mock_only", True)
        self.waypoints = self.config.get("waypoints")

        if self.frame_id != "map":
            raise WaypointConfigError("waypoints frame_id must be 'map'.")
        if not isinstance(self.mock_only, bool):
            raise WaypointConfigError("waypoints mock_only must be a boolean.")
        if not isinstance(self.waypoints, dict) or not self.waypoints:
            raise WaypointConfigError("waypoints must be a non-empty object.")

    @staticmethod
    def _load(path: Path) -> Dict[str, Any]:
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise WaypointConfigError(f"Cannot load waypoint config {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise WaypointConfigError("Waypoint config must be a JSON object.")
        return value

    def goal_spec(self, waypoint: str) -> NavigateToPoseSpec:
        try:
            pose = self.waypoints[waypoint]
        except (KeyError, TypeError) as exc:
            raise WaypointConfigError(f"Unknown waypoint: {waypoint}") from exc
        if not isinstance(pose, dict) or set(pose) != {"x", "y", "yaw"}:
            raise WaypointConfigError(
                f"Waypoint {waypoint} must contain exactly x, y, and yaw."
            )

        values = []
        for field in ("x", "y", "yaw"):
            value = pose[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WaypointConfigError(f"Waypoint {waypoint}.{field} must be numeric.")
            value = float(value)
            if not math.isfinite(value):
                raise WaypointConfigError(f"Waypoint {waypoint}.{field} must be finite.")
            values.append(value)

        x, y, yaw = values
        half_yaw = yaw / 2.0
        return NavigateToPoseSpec(
            waypoint=waypoint,
            frame_id=self.frame_id,
            x=x,
            y=y,
            yaw=yaw,
            orientation_z=math.sin(half_yaw),
            orientation_w=math.cos(half_yaw),
        )


class Nav2ActionClient:
    """Thin ROS 2 action client; construction requires a running rclpy node."""

    def __init__(self, node: Any, waypoint_store: WaypointStore) -> None:
        if waypoint_store.mock_only:
            raise WaypointConfigError(
                "Real navigation is disabled because waypoints.json has mock_only=true."
            )
        try:
            from nav2_msgs.action import NavigateToPose
            from rclpy.action import ActionClient
        except ImportError as exc:
            raise RuntimeError("ROS 2 Nav2 Python packages are not available.") from exc

        self._node = node
        self._store = waypoint_store
        self._action_type = NavigateToPose
        self._client = ActionClient(node, NavigateToPose, "/navigate_to_pose")

    def build_goal(self, waypoint: str) -> Any:
        spec = self._store.goal_spec(waypoint)
        goal = self._action_type.Goal()
        goal.pose.header.frame_id = spec.frame_id
        goal.pose.header.stamp = self._node.get_clock().now().to_msg()
        goal.pose.pose.position.x = spec.x
        goal.pose.pose.position.y = spec.y
        goal.pose.pose.orientation.z = spec.orientation_z
        goal.pose.pose.orientation.w = spec.orientation_w
        return goal

    def wait_for_server(self, timeout_sec: float = 5.0) -> bool:
        return self._client.wait_for_server(timeout_sec=timeout_sec)

    def send_waypoint_async(self, waypoint: str) -> Any:
        """Send one verified named waypoint; caller owns future/result handling."""
        return self._client.send_goal_async(self.build_goal(waypoint))
