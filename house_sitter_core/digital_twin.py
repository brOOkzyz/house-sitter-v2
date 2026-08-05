"""Deterministic, simulation-only residential Digital Twin records."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .simulation_boundary import synthetic_onboard_boundary


class DigitalTwinError(ValueError):
    """Raised when a local Digital Twin record would lose traceability."""


BASELINE_ENVIRONMENT = {
    "living_room": (21.0, 45.0), "kitchen": (21.5, 46.0), "bedroom": (20.5, 44.0),
    "bathroom": (22.0, 55.0), "hallway": (20.0, 42.0), "charging_area": (20.0, 40.0),
}


def create_house_v1_baseline(regions_document: dict[str, Any]) -> dict[str, Any]:
    """Create one explicit, locally traceable baseline for every annotated room."""
    if regions_document.get("map_id") != "house_v1":
        raise DigitalTwinError("Digital Twin 仅接受 house_v1 正式房间标注。")
    labels = [item.get("canonical_label") for item in regions_document.get("regions", []) if isinstance(item, dict)]
    if set(labels) != set(BASELINE_ENVIRONMENT) or len(labels) != len(set(labels)):
        raise DigitalTwinError("house_v1 房间集合不完整或重复。")
    rooms = []
    for room_id in labels:
        temperature_c, humidity_percent = BASELINE_ENVIRONMENT[room_id]
        rooms.append({
            "room_id": room_id, "last_observed_step": None, "temperature_c": temperature_c,
            "humidity_percent": humidity_percent, "obstacle_count": 0,
            "layout_signature": f"house_v1:{room_id}:baseline", "anomaly_status": "normal",
            "anomaly_types": [], "observation_confidence": 1.0, "observation_source": "house_v1_baseline",
            **synthetic_onboard_boundary(),
        })
    return {
        "schema_version": "1.0", "twin_id": "house_v1_baseline", "map_id": "house_v1",
        "baseline_source": "house_v1 deterministic residential baseline", **synthetic_onboard_boundary(), "rooms": rooms,
    }


def room_index(twin: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rooms = twin.get("rooms")
    if not isinstance(rooms, list):
        raise DigitalTwinError("Digital Twin 缺少 rooms 列表。")
    indexed = {room.get("room_id"): room for room in rooms if isinstance(room, dict) and isinstance(room.get("room_id"), str)}
    if len(indexed) != len(rooms):
        raise DigitalTwinError("Digital Twin 房间标识无效或重复。")
    return indexed


def update_room_from_observation(
    twin: dict[str, Any], observation: dict[str, Any], anomalies: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Update exactly one room and return a field-level, auditable difference."""
    room_id = observation.get("room_id")
    if not isinstance(room_id, str):
        raise DigitalTwinError("观测缺少 room_id。")
    updated = deepcopy(twin)
    room = room_index(updated).get(room_id)
    if room is None:
        raise DigitalTwinError(f"观测引用未知房间：{room_id}。")
    required = ("step", "temperature_c", "humidity_percent", "obstacle_count", "layout_signature", "confidence", "observation_id")
    if any(field not in observation for field in required):
        raise DigitalTwinError("观测字段不完整，拒绝更新 Digital Twin。")
    changed: dict[str, dict[str, Any]] = {}
    values = {
        "last_observed_step": observation["step"], "temperature_c": observation["temperature_c"],
        "humidity_percent": observation["humidity_percent"], "obstacle_count": observation["obstacle_count"],
        "layout_signature": observation["layout_signature"], "observation_confidence": observation["confidence"],
        "observation_source": observation["observation_id"],
        "anomaly_status": "anomaly" if anomalies else "normal",
        "anomaly_types": [item["anomaly_type"] for item in anomalies],
    }
    for field, value in values.items():
        if room.get(field) != value:
            changed[field] = {"before": room.get(field), "after": value}
            room[field] = value
    return updated, {"room_id": room_id, "observation_id": observation["observation_id"], "changed_fields": changed}
