#!/usr/bin/env python3
"""Capture a single RGB camera frame from the current simulation."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Tuple

import numpy as np
from PIL import Image
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image as RosImage


TOPIC_NAME = "/oakd/rgb/preview/image_raw"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = PROJECT_ROOT / "logs" / "latest_rgb_camera_snapshot.png"
SUMMARY_PATH = PROJECT_ROOT / "logs" / "latest_rgb_camera_snapshot_summary.txt"
SUPPORTED_ENCODINGS = {"rgb8", "bgr8", "rgba8", "bgra8", "mono8"}


def image_from_message(msg: RosImage) -> Image.Image:
    encoding = msg.encoding.lower()
    if encoding not in SUPPORTED_ENCODINGS:
        raise ValueError(f"unsupported encoding: {msg.encoding}")

    channels = {
        "mono8": 1,
        "rgb8": 3,
        "bgr8": 3,
        "rgba8": 4,
        "bgra8": 4,
    }[encoding]

    row_size = msg.width * channels
    if msg.step < row_size:
        raise ValueError(
            f"invalid step {msg.step} for width {msg.width} and encoding {msg.encoding}"
        )

    array = np.frombuffer(msg.data, dtype=np.uint8)
    expected = msg.step * msg.height
    if array.size < expected:
        raise ValueError(
            f"image data too short: got {array.size} bytes, expected at least {expected}"
        )

    array = array[:expected].reshape((msg.height, msg.step))
    array = array[:, :row_size]
    if channels == 1:
        pixels = array.reshape((msg.height, msg.width))
        return Image.fromarray(pixels, mode="L")

    pixels = array.reshape((msg.height, msg.width, channels))
    if encoding == "bgr8":
        pixels = pixels[:, :, ::-1]
        return Image.fromarray(pixels, mode="RGB")
    if encoding == "bgra8":
        pixels = pixels[:, :, [2, 1, 0, 3]]
        return Image.fromarray(pixels, mode="RGBA")
    if encoding == "rgb8":
        return Image.fromarray(pixels, mode="RGB")
    return Image.fromarray(pixels, mode="RGBA")


def build_summary(width: int, height: int, encoding: str, result: str) -> str:
    return "\n".join(
        [
            f"topic name: {TOPIC_NAME}",
            f"image width: {width}",
            f"image height: {height}",
            f"encoding: {encoding}",
            f"saved file path: {OUTPUT_PATH}",
            f"capture result: {result}",
            "simulation-only: yes",
            "Gazebo GUI used: no",
        ]
    )


class SnapshotNode(Node):
    def __init__(self) -> None:
        super().__init__("capture_rgb_snapshot")
        self.subscription = self.create_subscription(
            RosImage,
            TOPIC_NAME,
            self._on_image,
            10,
        )
        self.result: Tuple[Image.Image, RosImage] | None = None

    def _on_image(self, msg: RosImage) -> None:
        if self.result is not None:
            return
        image = image_from_message(msg)
        self.result = (image, msg)


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    rclpy.init(args=None)
    node = SnapshotNode()
    try:
        deadline = node.get_clock().now().nanoseconds + 10_000_000_000
        while rclpy.ok() and node.result is None:
            rclpy.spin_once(node, timeout_sec=0.5)
            if node.get_clock().now().nanoseconds >= deadline:
                raise TimeoutError(f"timed out waiting for image on {TOPIC_NAME}")

        assert node.result is not None
        image, msg = node.result
        image.save(OUTPUT_PATH)
        SUMMARY_PATH.write_text(
            build_summary(msg.width, msg.height, msg.encoding, "PASS") + "\n",
            encoding="utf-8",
        )
        print(f"capture result: PASS")
        print(f"saved file path: {OUTPUT_PATH}")
        print(f"image size: {msg.width}x{msg.height}")
        print(f"encoding: {msg.encoding}")
        return 0
    except Exception as exc:
        SUMMARY_PATH.write_text(
            build_summary(0, 0, "unknown", "FAIL") + f"\nerror: {exc}\n",
            encoding="utf-8",
        )
        print(f"capture result: FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
