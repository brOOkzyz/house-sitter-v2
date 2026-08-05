"""Explainable, deterministic anomaly checks for simulation-only observations."""
from __future__ import annotations

from typing import Any

from .simulation_boundary import synthetic_onboard_boundary


def _anomaly(room_id: str, kind: str, severity: str, expected: Any, observed: Any, step: int, explanation: str, action: str) -> dict[str, Any]:
    return {
        "anomaly_id": f"house_v1:{room_id}:{kind}:step:{step}", "room_id": room_id, "anomaly_type": kind,
        "severity": severity, "expected_value": expected, "observed_value": observed, "detection_step": step,
        "explanation": explanation, "recommended_action": action, **synthetic_onboard_boundary(),
    }


def detect_anomalies(observation: dict[str, Any], baseline_room: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare one observation with its baseline without claiming real perception."""
    room_id, step = observation["room_id"], observation["step"]
    results: list[dict[str, Any]] = []
    if observation.get("unexpected_obstacle") or observation["obstacle_count"] > baseline_room["obstacle_count"]:
        results.append(_anomaly(room_id, "unexpected_obstacle", "high", baseline_room["obstacle_count"], observation["obstacle_count"], step,
            f"An unexpected obstacle was detected in the {room_id}. Inspect the area before the next cleaning cycle.",
            "Inspect the area before the next cleaning cycle."))
    if not 18.0 <= float(observation["temperature_c"]) <= 28.0:
        results.append(_anomaly(room_id, "temperature_out_of_range", "medium", "18.0-28.0 C", observation["temperature_c"], step,
            f"Temperature in {room_id} is outside the simulation monitoring range.", "Inspect the room climate condition."))
    if not 30.0 <= float(observation["humidity_percent"]) <= 70.0:
        results.append(_anomaly(room_id, "humidity_out_of_range", "medium", "30.0-70.0 %", observation["humidity_percent"], step,
            f"Humidity in {room_id} is outside the simulation monitoring range.", "Inspect the room humidity condition."))
    if observation["layout_signature"] != baseline_room["layout_signature"]:
        results.append(_anomaly(room_id, "layout_change", "medium", baseline_room["layout_signature"], observation["layout_signature"], step,
            f"The observed layout signature for {room_id} differs from its baseline.", "Review the room layout before the next cleaning cycle."))
    return results


def actionable_alerts(anomalies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{
        "alert_id": f"alert:{item['anomaly_id']}", "room_id": item["room_id"], "severity": item["severity"],
        "anomaly_type": item["anomaly_type"], "message": item["explanation"],
        "recommended_action": item["recommended_action"], **synthetic_onboard_boundary(),
    } for item in anomalies]
