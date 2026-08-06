"""Constrained, offline household-scenario planning and validation."""
from __future__ import annotations

import random
import re
from typing import Any


ROOMS = ("living_room", "kitchen", "bedroom", "bathroom", "charging_area")
ROOM_WORDS = {
    "living_room": ("living room", "lounge"), "kitchen": ("kitchen",), "bedroom": ("bedroom",),
    "bathroom": ("bathroom", "washroom"), "charging_area": ("charging area", "charger", "dock"),
}
DOORS = (("charging_area", "living_room"), ("living_room", "kitchen"), ("living_room", "bedroom"), ("bedroom", "bathroom"), ("kitchen", "bathroom"))
EVENTS = {"unexpected_obstacle", "high_humidity", "high_temperature", "blocked_transition", "observation_dropout", "low_initial_battery"}
UNSAFE = ("<script", "javascript:", "shell", "bash", "python", "file://", "../", "ignore verifier", "bypass verifier", "execute code", "run code")


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def _rooms(text: str) -> list[str]:
    return [room for room, words in ROOM_WORDS.items() if any(word in text for word in words)]


def _event(event_id: str, room: str, event_type: str, parameters: dict[str, Any], visual: dict[str, Any]) -> dict[str, Any]:
    return {"event_id": event_id, "room": room, "event_type": event_type, "parameters": parameters, "start_stage": "scenario_start", "persistence": "until_reset", "visual_representation": visual, "simulation_only": True}


def plan_scenario(original_text: str, seed: int) -> dict[str, Any]:
    """Map a deliberately small grammar to data, never simulator calls."""
    text = normalize(original_text)
    result: dict[str, Any] = {"original_text": original_text, "normalized_text": text, "status": "planned", "extracted_events": [], "referenced_rooms": _rooms(text), "unsupported_elements": [], "clarification_questions": [], "warnings": [], "deterministic_match_basis": ["offline keyword grammar"], "scenario_seed": seed, "candidate_scenario": {"seed": seed, "events": [], "simulation_only": True}}
    if not text:
        result.update(status="invalid", clarification_questions=["Describe a supported household condition."])
        return result
    blocked = [word for word in UNSAFE if word in original_text.casefold()]
    if blocked:
        result.update(status="unsupported", unsupported_elements=blocked, warnings=["Scenario text cannot execute code, files, HTML, or change verification."])
        return result
    if "gas leak" in text or "gas" in text:
        result.update(status="unsupported", unsupported_elements=["gas leak"], warnings=["This simulator has no gas-sensing capability."])
        return result
    known_rooms = result["referenced_rooms"]
    clauses = [normalize(part) for part in re.split(r"[.;]|\band\s+(?:the\s+)?", original_text.casefold())]
    if any(word in text for word in ("garage", "office", "garden", "attic")):
        result.update(status="invalid", unsupported_elements=["unknown room"], warnings=["The scenario references a room outside the House2D map."])
        return result
    rng = random.Random(f"{seed}:{text}")
    events: list[dict[str, Any]] = []
    numbered = 0
    def add(room: str, kind: str, params: dict[str, Any], visual: dict[str, Any]) -> None:
        nonlocal numbered
        numbered += 1; events.append(_event(f"scenario-{numbered:03d}", room, kind, params, visual))
    object_words = ("box", "chair", "object", "obstacle")
    humid_words = ("humid", "humidity", "moisture")
    hot_words = ("hot", "high temperature", "temperature")
    blocked_words = ("doorway blocked", "doorway is blocked", "passage blocked", "passage is blocked", "blocked doorway", "blocked transition")
    dropout_words = ("sensor unavailable", "no observation", "observation dropout", "observation is unavailable")
    for room in known_rooms:
        room_text = " ".join(part for part in clauses if any(word in part for word in ROOM_WORDS[room])) or text
        if any(word in room_text for word in object_words):
            kind = "chair" if "chair" in room_text else "box" if "box" in room_text else "object"
            # Keep the icon away from room labels, checkpoints, doors, and fixed furniture.
            add(room, "unexpected_obstacle", {"object_kind": kind, "position": [round(rng.uniform(0.20, 0.30), 3), round(rng.uniform(0.72, 0.82), 3)]}, {"icon": "box", "label": kind})
        if any(word in room_text for word in humid_words): add(room, "high_humidity", {"humidity_percent": round(rng.uniform(72, 88), 1)}, {"icon": "droplet", "label": "high humidity"})
        if any(word in room_text for word in hot_words): add(room, "high_temperature", {"temperature_c": round(rng.uniform(29, 42), 1)}, {"icon": "thermometer", "label": "high temperature"})
        if any(word in room_text for word in blocked_words):
            doors = [list(door) for door in DOORS if room in door and "charging_area" not in door]
            add(room, "blocked_transition", {"doors": doors}, {"icon": "barrier", "label": "blocked doorway"})
        if any(word in room_text for word in dropout_words): add(room, "observation_dropout", {"checkpoint": room}, {"icon": "sensor_unknown", "label": "observation unavailable"})
    if any(word in text for word in ("low battery", "battery low", "battery is low", "insufficient battery")):
        add("charging_area", "low_initial_battery", {"battery_percent": round(rng.uniform(2, 5), 1)}, {"icon": "battery_low", "label": "low battery"})
    if ("normal" in text or "no anomaly" in text) and not events:
        result["deterministic_match_basis"].append("explicit normal-room declaration")
    elif ("normal" in text or "no anomaly" in text) and events:
        result["warnings"].append("An explicit anomaly takes precedence over a conflicting normal-room declaration.")
    if not events and not ("normal" in text or "no anomaly" in text):
        result.update(status="needs_clarification", clarification_questions=["Name a supported room and condition, such as high humidity or an unexpected box."], warnings=["No supported household condition was found."])
        return result
    result["extracted_events"] = events
    result["candidate_scenario"]["events"] = events
    return result


def verify_scenario(plan: dict[str, Any]) -> dict[str, Any]:
    """Fail closed before scenario data can reach House2D."""
    issues: list[dict[str, str]] = []
    scenario = plan.get("candidate_scenario") or {}
    seen: set[str] = set()
    for event in scenario.get("events", []):
        event_id, room, kind, params = event.get("event_id"), event.get("room"), event.get("event_type"), event.get("parameters", {})
        if not isinstance(event_id, str) or event_id in seen: issues.append({"field": "event_id", "message": "Event ids must be unique."})
        seen.add(str(event_id))
        if room not in ROOMS: issues.append({"field": "room", "message": f"Unknown room: {room}."})
        if kind not in EVENTS: issues.append({"field": "event_type", "message": f"Unsupported event type: {kind}."}); continue
        if event.get("simulation_only") is not True: issues.append({"field": "simulation_only", "message": "Every scenario event must remain simulation-only."})
        if room == "charging_area" and kind != "low_initial_battery": issues.append({"field": "room", "message": "The charging area only supports initial-battery scenarios."})
        if kind == "unexpected_obstacle":
            position = params.get("position")
            if not isinstance(position, list) or len(position) != 2 or not all(isinstance(value, (int, float)) and .15 <= value <= .85 for value in position): issues.append({"field": "parameters.position", "message": "Obstacle position must be inside its room."})
        elif kind == "blocked_transition":
            doors = params.get("doors")
            if not isinstance(doors, list) or not doors or any(not isinstance(door, list) or len(door) != 2 or (tuple(door) not in DOORS and tuple(reversed(door)) not in DOORS) for door in doors): issues.append({"field": "parameters.doors", "message": "Blocked transitions must be real doors."})
        elif kind == "high_humidity" and not isinstance(params.get("humidity_percent"), (int, float)) or kind == "high_humidity" and not 70 <= params["humidity_percent"] <= 100: issues.append({"field": "parameters.humidity_percent", "message": "Humidity must be within 70–100%."})
        elif kind == "high_temperature" and not isinstance(params.get("temperature_c"), (int, float)) or kind == "high_temperature" and not 28 <= params["temperature_c"] <= 50: issues.append({"field": "parameters.temperature_c", "message": "Temperature must be within 28–50°C."})
        elif kind == "observation_dropout" and params.get("checkpoint") != room: issues.append({"field": "parameters.checkpoint", "message": "Observation dropout needs a valid room checkpoint."})
        elif kind == "low_initial_battery" and (room != "charging_area" or not isinstance(params.get("battery_percent"), (int, float)) or not 1 <= params["battery_percent"] <= 25): issues.append({"field": "parameters.battery_percent", "message": "Initial battery must be 1–25% at the charging area."})
    return {"approved": not issues and plan.get("status") == "planned", "issues": issues, "safety_summary": ["simulation_only=true", "scenario data is validated before world-state injection"], "scenario_status": plan.get("status")}
