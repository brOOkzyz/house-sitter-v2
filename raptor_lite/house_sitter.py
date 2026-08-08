"""Observation-only House-Sitter application logic for the House2D backend."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


TEMPERATURE_MAX = 28.0
HUMIDITY_MAX = 70.0


def _anomaly(room: str, kind: str, severity: str, evidence: str, observed: Any, baseline: Any, threshold: Any, timestamp: float, observation_id: str) -> dict[str, Any]:
    actions = {
        "unexpected_obstacle": "inspect the unexpected obstacle before the next cleaning cycle",
        "high_temperature": "inspect the room climate condition",
        "high_humidity": "inspect the room humidity condition",
        "blocked_transition": "inspect the blocked doorway before the next cleaning cycle",
        "missing_observation": "revisit the room when a valid observation is available",
    }
    return {"anomaly_id": f"anomaly:{room}:{kind}:{timestamp:.3f}", "room": room, "anomaly_type": kind, "severity": severity,
            "evidence": evidence, "observed_value": observed, "baseline_value": baseline, "threshold": threshold,
            "decision_basis": "deterministic observation-to-baseline comparison", "detected_at": timestamp,
            "recommended_action": actions[kind], "observation_id": observation_id, "simulation_only": True, "physical_robot_validated": False}


class HouseSitterApplication:
    """Small room-level baseline, detection, Twin, alert, and report state."""

    def __init__(self, task_name: str, seed: int):
        self.task_name, self.seed = task_name, seed
        self.baselines: dict[str, dict[str, Any]] = {}
        self.latest: dict[str, dict[str, Any]] = {}
        self.observations: list[dict[str, Any]] = []
        self.anomalies: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.twin = {"schema_version": "1.0", "simulation_only": True, "physical_robot_validated": False, "rooms": {}}
        self.before: dict[str, Any] | None = None
        self.report: str | None = None

    def observe(self, observation: dict[str, Any], *, baseline: bool = False) -> None:
        room = observation["room"]
        if not any(item["observation_id"] == observation["observation_id"] for item in self.observations):
            self.observations.append(deepcopy(observation))
        self.latest[room] = deepcopy(observation)
        if baseline:
            if not observation["observation_valid"]:
                raise ValueError(f"Cannot establish a baseline from an invalid '{room}' observation.")
            self.baselines[room] = deepcopy(observation)
            self.twin["rooms"][room] = {"room_identity": room, "layout_object_state": list(observation["visible_object_identifiers"]),
                "temperature_state": observation["temperature_c"], "humidity_state": observation["humidity_percent"],
                "accessibility_state": deepcopy(observation["transition_accessibility"]), "last_valid_observation": observation["observation_id"],
                "last_visit": observation["timestamp"], "active_anomalies": [], "anomaly_history": [], "revision": 0,
                "provenance": {"source": "baseline_observation", "observation_id": observation["observation_id"]}}

    def capture_before(self) -> None:
        self.before = deepcopy(self.twin)

    def detect(self, room: str) -> list[dict[str, Any]]:
        baseline, observation = self.baselines.get(room), self.latest.get(room)
        if baseline is None:
            raise ValueError(f"No baseline exists for '{room}'.")
        if observation is None:
            raise ValueError(f"No observation exists for '{room}'.")
        timestamp = float(observation["timestamp"])
        if not observation["observation_valid"]:
            found = [_anomaly(room, "missing_observation", "warning", "The simulated onboard observation was unavailable.", "invalid", "valid observation", "observation_valid=true", timestamp, observation["observation_id"])]
        else:
            found = []
            if observation["obstacle_present"] != baseline["obstacle_present"] or observation["visible_object_identifiers"] != baseline["visible_object_identifiers"]:
                found.append(_anomaly(room, "unexpected_obstacle", "warning", "Visible objects or obstacle presence differ from the baseline.", observation["visible_object_identifiers"], baseline["visible_object_identifiers"], "baseline layout", timestamp, observation["observation_id"]))
            if float(observation["temperature_c"]) > TEMPERATURE_MAX:
                found.append(_anomaly(room, "high_temperature", "warning", "Observed temperature exceeds the safe monitoring threshold.", observation["temperature_c"], baseline["temperature_c"], TEMPERATURE_MAX, timestamp, observation["observation_id"]))
            if float(observation["humidity_percent"]) > HUMIDITY_MAX:
                found.append(_anomaly(room, "high_humidity", "warning", "Observed humidity exceeds the safe monitoring threshold.", observation["humidity_percent"], baseline["humidity_percent"], HUMIDITY_MAX, timestamp, observation["observation_id"]))
            changed = [door for door, accessible in observation["transition_accessibility"].items() if accessible != baseline["transition_accessibility"].get(door)]
            if changed:
                found.append(_anomaly(room, "blocked_transition", "warning", "Observed transition accessibility differs from the baseline.", changed, baseline["transition_accessibility"], "baseline accessibility", timestamp, observation["observation_id"]))
        self.anomalies.extend(found)
        return deepcopy(found)

    def update_twin(self, room: str) -> dict[str, Any]:
        observation = self.latest.get(room)
        if observation is None:
            raise ValueError(f"No observation exists for '{room}'.")
        if not observation["observation_valid"]:
            return {"room": room, "updated": False, "reason": "invalid observation does not update the Digital Twin"}
        relevant = [item for item in self.anomalies if item["room"] == room and item["observation_id"] == observation["observation_id"]]
        if not relevant:
            return {"room": room, "updated": False, "reason": "no detected anomaly supports a Digital Twin update"}
        current = self.twin["rooms"].get(room)
        if current is None:
            raise ValueError(f"No Digital Twin baseline exists for '{room}'.")
        before = deepcopy(current)
        values = {"layout_object_state": list(observation["visible_object_identifiers"]), "temperature_state": observation["temperature_c"],
                  "humidity_state": observation["humidity_percent"], "accessibility_state": deepcopy(observation["transition_accessibility"]),
                  "last_valid_observation": observation["observation_id"], "last_visit": observation["timestamp"],
                  "active_anomalies": [item["anomaly_type"] for item in relevant]}
        changed = {key: {"before": current.get(key), "after": value} for key, value in values.items() if current.get(key) != value}
        if changed:
            current.update(values); current["revision"] += 1; current["anomaly_history"].extend(item["anomaly_id"] for item in relevant)
            current["provenance"] = {"source": "observation", "observation_id": observation["observation_id"], "anomaly_ids": [item["anomaly_id"] for item in relevant]}
        update = {"room": room, "updated": bool(changed), "before": before, "after": deepcopy(current), "changed_fields": changed,
                  "reason": "observation-supported anomaly update" if changed else "observation already represented"}
        self.updates.append(update)
        return deepcopy(update)

    def generate_alert(self, room: str, anomaly_type: str) -> dict[str, Any]:
        anomaly = next((item for item in reversed(self.anomalies) if item["room"] == room and (anomaly_type == "detected_anomaly" or item["anomaly_type"] == anomaly_type)), None)
        if anomaly is None:
            if any(item["room"] == room for item in self.anomalies):
                raise ValueError(f"Alert for '{room}' must reference a detected anomaly type.")
            return {"room": room, "problem": anomaly_type, "generated": False, "reason": "no matching detected anomaly", "simulation_only": True, "physical_robot_validated": False}
        revision = self.twin["rooms"].get(room, {}).get("revision")
        alert = {"alert_id": f"alert:{anomaly['anomaly_id']}", "room": room, "problem": anomaly["anomaly_type"], "severity": anomaly["severity"],
                 "evidence_summary": anomaly["evidence"], "recommended_action": anomaly["recommended_action"], "timestamp": anomaly["detected_at"],
                 "related_digital_twin_revision": revision, "simulation_only": True, "physical_robot_validated": False}
        self.alerts.append(alert)
        return deepcopy(alert)

    def render_report(self, final_state: dict[str, Any], execution_success: bool | None = None) -> str:
        onboard = [item for item in self.observations if item["observation_id"].startswith("observation:")]
        valid = sum(1 for item in onboard if item["observation_valid"])
        success = bool(execution_success) and final_state["room"] == "charging_area" and final_state["stopped"]
        lines = ["# RaPToR-Lite House-Sitter Monitoring Report", "", f"- Task: {self.task_name}", f"- Scenario seed: {self.seed}",
                 f"- Rooms planned: living_room, kitchen, bedroom, bathroom", f"- Rooms visited: {', '.join(final_state['visit_history'])}",
                 f"- Successful observations: {valid}", f"- Failed observations: {len(onboard) - valid}",
                 f"- Detected anomalies: {len(self.anomalies)}", f"- Digital Twin updates: {sum(1 for item in self.updates if item['updated'])}",
                 f"- Generated alerts: {len(self.alerts)}", f"- Return to start: {final_state['room'] == 'charging_area'}", f"- Battery usage: {final_state['battery']:.1f}% remaining", f"- Execution duration: {final_state['time']:.1f}s", f"- Overall success: {success}", "",
                 "## Detected anomalies"]
        lines.extend([f"- {item['room']}: {item['anomaly_type']} ({item['severity']})" for item in self.anomalies] or ["- None"])
        lines.extend(["", "## Limitations", "This is a deterministic, simulation-only observation model, not physical-robot validation."])
        self.report = "\n".join(lines) + "\n"
        return self.report

    def artifacts(self) -> dict[str, Any]:
        return {"baseline_observations": [self.baselines[key] for key in sorted(self.baselines)], "detected_anomalies": deepcopy(self.anomalies),
                "digital_twin_before": deepcopy(self.before or self.twin), "digital_twin_after": deepcopy(self.twin), "digital_twin_updates": deepcopy(self.updates),
                "actionable_alerts": deepcopy(self.alerts), "monitoring_report": self.report}
