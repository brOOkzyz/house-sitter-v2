"""Deterministic synthetic observations from a plausible onboard sensor bundle."""
from __future__ import annotations

from typing import Any

from .simulation_boundary import synthetic_onboard_boundary


class SimulatedSensorError(ValueError):
    """Raised when a deterministic scenario requests an unknown room."""


def observe_room(room_id: str, step: int, baseline_room: dict[str, Any], *, unexpected_obstacle: bool = False) -> dict[str, Any]:
    """Return a local, repeatable distance/location/temperature/humidity observation."""
    if baseline_room.get("room_id") != room_id or not isinstance(step, int) or step < 1:
        raise SimulatedSensorError("模拟传感器需要有效房间、基线和正整数步骤。")
    obstacle_count = int(baseline_room["obstacle_count"]) + (1 if unexpected_obstacle else 0)
    return {
        "observation_id": f"house_v1:{room_id}:step:{step}", "room_id": room_id, "step": step,
        "position_observation": {"room_id": room_id, "source": "house_v1_semantic_region"},
        "distance_observation": {"nearest_obstacle_m": 0.28 if unexpected_obstacle else 0.80, "obstacle_count": obstacle_count},
        "temperature_c": baseline_room["temperature_c"], "humidity_percent": baseline_room["humidity_percent"],
        "obstacle_count": obstacle_count, "unexpected_obstacle": unexpected_obstacle,
        "layout_signature": baseline_room["layout_signature"], "confidence": 0.96,
        **synthetic_onboard_boundary(),
        "source": "deterministic_house_v1_onboard_sensor_model",
    }
