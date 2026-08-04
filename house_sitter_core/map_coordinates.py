"""Conversions between top-left image pixels and ROS map-frame coordinates."""

from __future__ import annotations

import math
from typing import Any

from .map_metadata import MapMetadataError, RosMapMetadata


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapMetadataError(f"{name} must be a finite number.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise MapMetadataError(f"{name} must be a finite number.")
    return numeric


def pixel_to_map(metadata: RosMapMetadata, pixel_x: Any, pixel_y: Any) -> tuple[float, float]:
    """Convert an image pixel centre (top-left origin) into a ROS map-frame point."""
    x = _finite(pixel_x, "pixel_x")
    y = _finite(pixel_y, "pixel_y")
    if not 0 <= x <= metadata.image.width - 1 or not 0 <= y <= metadata.image.height - 1:
        raise MapMetadataError("Pixel coordinate is outside the map image.")
    origin_x, origin_y, origin_yaw = metadata.origin
    local_x = (x + 0.5) * metadata.resolution
    local_y = (metadata.image.height - 1 - y + 0.5) * metadata.resolution
    cosine, sine = math.cos(origin_yaw), math.sin(origin_yaw)
    return (
        origin_x + cosine * local_x - sine * local_y,
        origin_y + sine * local_x + cosine * local_y,
    )


def map_to_pixel(metadata: RosMapMetadata, map_x: Any, map_y: Any) -> tuple[float, float]:
    """Convert a map-frame point into an image pixel-centre coordinate."""
    x = _finite(map_x, "map_x")
    y = _finite(map_y, "map_y")
    origin_x, origin_y, origin_yaw = metadata.origin
    delta_x, delta_y = x - origin_x, y - origin_y
    cosine, sine = math.cos(origin_yaw), math.sin(origin_yaw)
    local_x = cosine * delta_x + sine * delta_y
    local_y = -sine * delta_x + cosine * delta_y
    pixel_x = local_x / metadata.resolution - 0.5
    pixel_y = metadata.image.height - 0.5 - local_y / metadata.resolution
    tolerance = 1e-9
    if not -tolerance <= pixel_x <= metadata.image.width - 1 + tolerance or not -tolerance <= pixel_y <= metadata.image.height - 1 + tolerance:
        raise MapMetadataError("Map coordinate is outside the map image.")
    return pixel_x, pixel_y
