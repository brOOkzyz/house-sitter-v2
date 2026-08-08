"""Plain-language output derived from execution evidence, never scenario prose."""
from __future__ import annotations

from typing import Any


def _name(room: str) -> str:
    return room.replace("_", " ")


def build_feedback(task_scope: list[str], trace: list[Any], bundle: dict[str, Any], *, trace_count: int | None = None, execution_success: bool | None = None) -> dict[str, Any]:
    selected = trace if trace_count is None else trace[:trace_count]
    observations = [item.details for item in selected if "observation_id" in item.details]
    anomalies = [entry for item in selected for entry in item.details.get("anomalies", [])]
    failures = [str(item.details.get("error")) for item in selected if item.event == "step_failed"]
    visited = [str(item.details.get("entered_room") or item.details.get("revisited_room")) for item in selected if item.details.get("entered_room") or item.details.get("revisited_room")]
    by_room = {room: [entry for entry in anomalies if entry["room"] == room] for room in task_scope}
    messages: list[str] = []
    requested = [room for room in task_scope if room != "charging_area"]
    for room in requested:
        observation = next((item for item in reversed(observations) if item["room"] == room), None)
        if observation is None:
            continue
        room_name = _name(room).capitalize()
        if not observation.get("observation_valid"):
            messages.append(f"I reached the {_name(room)}, but the sensor observation could not be completed. I cannot confirm whether the room is normal.")
        elif not by_room[room]:
            messages.append(f"The {room_name} inspection is complete. Everything appears normal.")
        else:
            labels = {"unexpected_obstacle": f"An unexpected obstacle was detected in the {_name(room)}. Please inspect and remove it if it affects normal movement.", "high_humidity": "High humidity was detected. Please check for moisture or a possible water leak.", "high_temperature": "High temperature was detected. Please inspect possible heat sources or ventilation issues.", "blocked_transition": "A blocked transition was detected. Please clear the route before another inspection."}
            messages.extend(f"The {room_name} inspection is complete. {labels.get(item['anomaly_type'], 'An anomaly was detected.')}" for item in by_room[room])
    if failures:
        failure = failures[0].casefold()
        target = next((room for room in requested if room not in visited), requested[-1] if requested else "room")
        if "battery" in failure: messages = ["I could not begin the patrol because the available battery was insufficient to complete the task and return safely."]
        elif "route" in failure or "blocked" in failure: messages = [f"I could not reach the {_name(target)} because the route was blocked. The {_name(target)} was not inspected."]
    final_state = bundle.get("final_world_state", {})
    done = execution_success is not None and trace_count is None
    final = "\n".join(messages) if messages else ("The patrol is in progress." if not done else "The patrol is complete.")
    if done and execution_success and len(requested) > 1:
        final = "The patrol is complete.\n\n- " + "\n- ".join(messages) + ("\n\nI returned safely to the charging area." if final_state.get("room") == "charging_area" and final_state.get("stopped") else "")
    return {"task_scope": requested, "rooms_requested": requested, "rooms_visited": [room for room in requested if room in visited], "rooms_not_reached": [room for room in requested if room not in visited], "valid_observations": [item["room"] for item in observations if item.get("observation_valid")], "failed_observations": [item["room"] for item in observations if not item.get("observation_valid")], "detected_anomalies": anomalies, "unconfirmed_ground_truth_events": {"redacted": True}, "final_message": final, "safely_stopped": bool(final_state.get("stopped")) if done else False, "returned_to_start": final_state.get("room") == "charging_area" if done else False, "simulation_only": True}


def feedback_markdown(feedback: dict[str, Any]) -> str:
    return "# Robot Feedback\n\n" + feedback["final_message"] + "\n\nSimulation-only; based on verified task scope and completed onboard observations.\n"
