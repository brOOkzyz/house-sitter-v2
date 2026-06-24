#!/usr/bin/env python3
"""Read-only TurtleBot 4 ROS 2 graph and topic diagnostics.

This node creates subscriptions only. It never publishes velocity commands,
sends action goals, changes launch parameters, or manages Gazebo processes.
"""

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import rclpy
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile, ReliabilityPolicy
from rosidl_runtime_py.utilities import get_message


COMMAND_TOPICS = (
    "/cmd_vel",
    "/cmd_vel_unstamped",
    "/diffdrive_controller/cmd_vel",
)

DATA_TOPICS = (
    "/clock",
    "/scan",
    "/odom",
    "/tf",
    "/dock_status",
    "/wheel_status",
)

REQUIRED_ACTIONS = ("/dock", "/undock")


@dataclass(frozen=True)
class TopicSnapshot:
    types: Tuple[str, ...]
    publisher_count: int
    subscriber_count: int


class TurtleBot4Diagnostic(Node):
    """A ROS 2 node containing no publishers and no action clients."""

    def __init__(self) -> None:
        super().__init__("house_sitter_v2_turtlebot4_diagnostic")
        self.received: Dict[str, object] = {}
        self.subscriptions_by_topic: Dict[str, object] = {}

        # A best-effort/volatile reader is compatible with normal sensor-data
        # publishers and with reliable/transient-local publishers.
        self.read_qos = QoSProfile(
            depth=10,
            reliability=ReliabilityPolicy.BEST_EFFORT,
            durability=DurabilityPolicy.VOLATILE,
        )

    def snapshot_topic(self, topic: str, topic_types: Dict[str, List[str]]) -> TopicSnapshot:
        return TopicSnapshot(
            types=tuple(topic_types.get(topic, [])),
            publisher_count=len(self.get_publishers_info_by_topic(topic)),
            subscriber_count=len(self.get_subscriptions_info_by_topic(topic)),
        )

    def subscribe_for_one_sample(self, topic: str, type_name: str) -> Optional[str]:
        """Create one read-only subscription; return an error string on failure."""
        try:
            message_class = get_message(type_name)
            subscription = self.create_subscription(
                message_class,
                topic,
                lambda message, name=topic: self.received.setdefault(name, message),
                self.read_qos,
            )
            self.subscriptions_by_topic[topic] = subscription
            return None
        except Exception as exc:  # Continue diagnosing all remaining topics.
            return str(exc)


def discover_graph(node: Node, duration_seconds: float) -> None:
    deadline = time.monotonic() + duration_seconds
    while time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=0.1)


def action_status(node: Node, action_name: str) -> str:
    """Detect an action server through its standard read-only graph endpoints."""
    services = dict(node.get_service_names_and_types())
    required_services = (
        f"{action_name}/_action/send_goal",
        f"{action_name}/_action/get_result",
        f"{action_name}/_action/cancel_goal",
    )
    present = [name for name in required_services if name in services]

    if len(present) == len(required_services):
        send_goal_types = services[required_services[0]]
        action_types = [
            type_name.removesuffix("_SendGoal") for type_name in send_goal_types
        ]
        type_text = ", ".join(action_types) if action_types else "type unknown"
        return f"PRESENT ({type_text})"
    if present:
        return f"INCOMPLETE ({len(present)}/{len(required_services)} service endpoints)"
    return "NOT FOUND"


def format_status_sample(message: object, max_lines: int = 12) -> str:
    lines = str(message).rstrip().splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["... (truncated)"]
    return "\n".join(f"    {line}" for line in lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only TurtleBot 4 ROS 2 diagnostic"
    )
    parser.add_argument(
        "--discovery-seconds",
        type=float,
        default=5.0,
        help="wall-clock seconds to wait for DDS discovery (default: 5.0)",
    )
    parser.add_argument(
        "--data-seconds",
        type=float,
        default=5.0,
        help="wall-clock seconds to wait for topic data (default: 5.0)",
    )
    args = parser.parse_args()

    if args.discovery_seconds <= 0.0 or args.data_seconds <= 0.0:
        parser.error("Timeout values must be greater than zero.")

    rclpy.init()
    node = TurtleBot4Diagnostic()

    try:
        print(
            f"Waiting {args.discovery_seconds:.1f}s for ROS 2 graph discovery..."
        )
        discover_graph(node, args.discovery_seconds)

        topic_types = dict(node.get_topic_names_and_types())
        all_topics = COMMAND_TOPICS + DATA_TOPICS
        snapshots = {
            topic: node.snapshot_topic(topic, topic_types) for topic in all_topics
        }

        print("\n=== Command topics ===")
        for topic in COMMAND_TOPICS:
            snapshot = snapshots[topic]
            type_text = ", ".join(snapshot.types) if snapshot.types else "NOT FOUND"
            print(f"{topic}")
            print(f"  type(s): {type_text}")
            print(f"  publishers: {snapshot.publisher_count}")
            print(f"  subscribers: {snapshot.subscriber_count}")

        print("\n=== Data topic graph ===")
        subscription_errors: Dict[str, str] = {}
        for topic in DATA_TOPICS:
            snapshot = snapshots[topic]
            type_text = ", ".join(snapshot.types) if snapshot.types else "NOT FOUND"
            print(
                f"{topic}: type(s)={type_text}, "
                f"publishers={snapshot.publisher_count}, "
                f"subscribers={snapshot.subscriber_count}"
            )
            if snapshot.types and snapshot.publisher_count > 0:
                error = node.subscribe_for_one_sample(topic, snapshot.types[0])
                if error is not None:
                    subscription_errors[topic] = error

        print("\n=== Actions ===")
        for action_name in REQUIRED_ACTIONS:
            print(f"{action_name}: {action_status(node, action_name)}")

        expected_samples = set(node.subscriptions_by_topic)
        deadline = time.monotonic() + args.data_seconds
        while time.monotonic() < deadline:
            if expected_samples.issubset(node.received):
                break
            rclpy.spin_once(node, timeout_sec=0.1)

        print("\n=== Data samples ===")
        for topic in DATA_TOPICS:
            snapshot = snapshots[topic]
            if topic in node.received:
                print(f"{topic}: DATA RECEIVED")
                if topic in ("/dock_status", "/wheel_status"):
                    print(format_status_sample(node.received[topic]))
            elif topic in subscription_errors:
                print(f"{topic}: SUBSCRIPTION ERROR: {subscription_errors[topic]}")
            elif not snapshot.types:
                print(f"{topic}: NOT FOUND")
            elif snapshot.publisher_count == 0:
                print(f"{topic}: NO PUBLISHER")
            else:
                print(f"{topic}: NO DATA")

        print("\nRead-only diagnostic complete. No ROS 2 commands were sent.")
        return 0
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    sys.exit(main())
