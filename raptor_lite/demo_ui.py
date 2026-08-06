"""Localhost-only, dependency-free visual replay for the verified House-Sitter flow."""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil, hypot
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .artifacts import write_planning_run
from .capability_registry import CapabilityRegistry
from .executor import BackendExecutor, MockExecutor
from .house2d import DOORS, HOUSE_LAYOUT, ROOMS, House2DBackend, layout_location, layout_route
from .models import PlanningResult, VerificationReport
from .planner import OfflineHouseSitterPlanner
from .robot_feedback import build_feedback, feedback_markdown
from .scenario import plan_scenario, verify_scenario
from .verifier import verify_task


DEFAULT_REQUEST = "Run a complete house-sitter patrol and report any environmental changes."
LEGACY_SCENARIO_TEXT = {"complete": "There is a box in the kitchen and the bathroom has high humidity.", "normal": "The home is normal.", "dropout": "The kitchen sensor observation is unavailable.", "blocked": "The kitchen doorway is blocked.", "low_battery": "The available battery is low."}


class DemoError(ValueError):
    pass


class DemoController:
    """One serialized UI session; all simulation values originate in House2D output."""

    def __init__(self, profile: Path, artifact_root: Path = Path("artifacts") / "raptor_lite"):
        self.registry = CapabilityRegistry.from_yaml(profile)
        self.planner = OfflineHouseSitterPlanner(self.registry)
        self.artifact_root = artifact_root
        self._lock = threading.RLock()
        self._busy = False
        self.reset()

    @staticmethod
    def _seed(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < 2**31:
            raise DemoError("Seed must be an integer between 0 and 2147483647.")
        return value

    @staticmethod
    def _text(value: Any) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 4096:
            raise DemoError("Request text must be between 1 and 4096 characters.")
        return value

    def _begin(self) -> None:
        if self._busy:
            raise DemoError("A simulation run is already in progress.")
        self._busy = True

    def _end(self) -> None:
        self._busy = False

    def reset(self) -> dict[str, Any]:
        with getattr(self, "_lock", threading.RLock()):
            if getattr(self, "_busy", False):
                raise DemoError("Cannot reset while a simulation run is in progress.")
            self.planning: PlanningResult | None = None
            self.scenario_planning: dict[str, Any] | None = None
            self.scenario_report: dict[str, Any] | None = None
            self.scenario_input: str | None = None
            self.report: VerificationReport | None = None
            self.result = None
            self.trace: list[Any] = []
            self.playback_trace: list[dict[str, Any]] = []
            self.bundle: dict[str, Any] = {}
            self.artifact_dir: Path | None = None
            self.index = 0
            self.paused = True
            self.started = False
            self.explicitly_paused = False
            self.speed = 1
            self.phase = "idle"
        return self.state()

    def plan(self, text: Any) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                self.planning = self.planner.plan(self._text(text))
                self.report = None; self.result = None; self.trace = []; self.playback_trace = []; self.bundle = {}; self.artifact_dir = None
                self.index = 0; self.paused = True; self.started = False; self.explicitly_paused = False; self.phase = "planned"
                return self.state()
            finally:
                self._end()

    def interpret_scenario(self, text: Any, seed: Any) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                scenario_text, parsed_seed = self._text(text), self._seed(seed)
                self.scenario_input = scenario_text
                self.scenario_planning = plan_scenario(scenario_text, parsed_seed)
                self.scenario_report = verify_scenario(self.scenario_planning)
                self.result = None; self.trace = []; self.playback_trace = []; self.bundle = {}; self.artifact_dir = None
                self.index = 0; self.paused = True; self.started = False; self.explicitly_paused = False
                self.phase = "scenario_planned" if self.scenario_report["approved"] else "scenario_rejected"
                return self.state()
            finally:
                self._end()

    def validate(self) -> dict[str, Any]:
        with self._lock:
            if self.planning is None:
                raise DemoError("Plan a natural-language request before validation.")
            if self.scenario_planning is None:
                self.scenario_input = LEGACY_SCENARIO_TEXT["normal"]
                self.scenario_planning = plan_scenario(self.scenario_input, 0); self.scenario_report = verify_scenario(self.scenario_planning)
            self.report = verify_task(self.planning.candidate_task, self.registry) if self.planning.status == "planned" and self.planning.candidate_task else VerificationReport(approved=False, safety_summary=["No planned candidate task is available for validation."])
            if not self.scenario_report or not self.scenario_report["approved"]: self.report = VerificationReport(approved=False, safety_summary=["Scenario verification must approve the structured scenario before execution."])
            self.phase = "approved" if self.report.approved else "rejected"
            return self.state()

    def _run(self, seed: int, scenario: str | None = None) -> dict[str, Any]:
        if self.planning is None or self.report is None or not self.report.approved or self.planning.candidate_task is None:
            raise DemoError("Execution is denied until a candidate task is verifier-approved.")
        if scenario is not None:
            if scenario not in LEGACY_SCENARIO_TEXT: raise DemoError("Scenario is not supported by this local demo.")
            self.scenario_input = LEGACY_SCENARIO_TEXT[scenario]; self.scenario_planning = plan_scenario(self.scenario_input, seed); self.scenario_report = verify_scenario(self.scenario_planning)
            if not self.scenario_report["approved"]: raise DemoError("Scenario verification did not approve this scenario.")
        elif self.scenario_input is not None and self.scenario_planning and self.scenario_planning.get("scenario_seed") != seed:
            self.scenario_planning = plan_scenario(self.scenario_input, seed); self.scenario_report = verify_scenario(self.scenario_planning)
        if not self.scenario_planning or not self.scenario_report or not self.scenario_report["approved"]: raise DemoError("Execution is denied until a scenario is verified.")
        backend = House2DBackend(seed=seed, scenario={**self.scenario_planning["candidate_scenario"], "validation_status": "approved"})
        self.result, self.trace = BackendExecutor(backend).run(self.planning.candidate_task, self.report, self.registry)
        self.bundle = backend.artifact_bundle()
        feedback = build_feedback(self.planning.extracted_rooms, self.trace, self.bundle, execution_success=self.result.success)
        self.bundle["robot_feedback"] = feedback
        self.playback_trace = self._expand_playback_trace()
        self.artifact_dir = write_planning_run(self.artifact_root, self.planning, self.registry.as_json(), self.report, self.result, self.trace, backend, scenario_input=self.scenario_input, scenario_plan=self.scenario_planning, scenario_report=self.scenario_report, robot_feedback=feedback, robot_feedback_markdown=feedback_markdown(feedback))
        self.index = 0; self.paused = True; self.started = False; self.explicitly_paused = False; self.phase = "playback" if self.result.success else "failed"
        return self.state()

    def run(self, seed: Any, scenario: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                return self._run(self._seed(seed), scenario)
            finally:
                self._end()

    def complete_demo(self, seed: Any = 12345) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                self.planning = self.planner.plan(DEFAULT_REQUEST)
                self.scenario_input = LEGACY_SCENARIO_TEXT["complete"]; self.scenario_planning = plan_scenario(self.scenario_input, self._seed(seed)); self.scenario_report = verify_scenario(self.scenario_planning)
                self.report = verify_task(self.planning.candidate_task, self.registry) if self.planning.candidate_task else VerificationReport(approved=False)
                self.phase = "approved" if self.report.approved else "rejected"
                return self._run(self._seed(seed))
            finally:
                self._end()

    def playback(self, action: str) -> dict[str, Any]:
        with self._lock:
            if not self.playback_trace:
                raise DemoError("Run an approved task before controlling playback.")
            if action == "pause": self.paused = True; self.explicitly_paused = True
            elif action == "resume": self.paused = False; self.started = True; self.explicitly_paused = False
            elif action == "step": self.paused = True; self.started = True; self.explicitly_paused = False; self.index = min(len(self.playback_trace), self.index + 1)
            elif action == "restart": self.paused = True; self.started = False; self.explicitly_paused = False; self.index = 0
            elif action == "faster": self.speed = 4 if self.speed == 1 else 1
            else: raise DemoError("Unknown playback action.")
            return self.state()

    def advance(self) -> dict[str, Any]:
        with self._lock:
            if self.playback_trace and not self.paused:
                self.started = True
                self.index = min(len(self.playback_trace), self.index + self.speed)
                if self.index == len(self.playback_trace): self.paused = True
            return self.state()

    @staticmethod
    def _interpolate(points: list[list[float]], maximum_step: float = 0.35) -> list[list[float]]:
        output: list[list[float]] = []
        for start, target in zip(points, points[1:]):
            count = max(1, ceil(hypot(target[0] - start[0], target[1] - start[1]) / maximum_step))
            output.extend([[round(start[0] + (target[0] - start[0]) * index / count, 4), round(start[1] + (target[1] - start[1]) * index / count, 4)] for index in range(1, count + 1)])
        return output

    def _phase_for(self, trace_count: int) -> str:
        injected = any(item.details.get("events_injected") for item in self.trace[:trace_count])
        return "Change-monitoring patrol" if injected else "Baseline patrol"

    def _expand_playback_trace(self) -> list[dict[str, Any]]:
        """Derive deterministic visual frames from, but never modify, execution trace evidence."""
        frames: list[dict[str, Any]] = []
        position = list(HOUSE_LAYOUT["checkpoints"]["charging_area"])
        for trace_index, item in enumerate(self.trace):
            detail, step = item.details, self._step(item.step_id)
            phase = self._phase_for(trace_index)
            if "route" in detail:
                route = layout_route(detail["route"])
                target = detail.get("entered_room") or detail.get("revisited_room") or detail["route"][-1]
                for point in self._interpolate(route):
                    location = layout_location(point)
                    message = f"The robot is moving through the {self._room(location)} toward the {self._room(target)}." if location == "hallway" else None
                    frames.append({"trace_count": trace_index, "position": point, "action": f"Moving to the {self._room(target)}", "goal": target, "waypoint": location, "phase": phase, "observation_status": "Travelling", "logs": [message] if message and not any(frame.get("waypoint") == "hallway" for frame in frames) else []})
                position = list(route[-1])
                frames.append({"trace_count": trace_index + 1, "position": position, "action": f"Arrived at the {self._room(target)} inspection point", "goal": target, "waypoint": target, "phase": self._phase_for(trace_index + 1), "observation_status": "Arrived", "logs": [f"The robot arrived at the {self._room(target)} inspection point."]})
            elif step and step.skill == "inspect_room":
                room = str(step.parameters["room"])
                frames.append({"trace_count": trace_index, "position": position, "action": f"Inspecting the {self._room(room)}", "goal": room, "waypoint": room, "phase": phase, "observation_status": "Inspecting", "logs": []})
                frames.append({"trace_count": trace_index + 1, "position": position, "action": f"Observation completed in the {self._room(room)}", "goal": room, "waypoint": room, "phase": self._phase_for(trace_index + 1), "observation_status": "Observation completed", "logs": [f"The {self._room(room)} observation was completed."] if detail.get("observation_valid") else [f"The simulated observation in the {self._room(room)} was unavailable."]})
            elif step and step.skill == "detect_environment_change":
                room = str(step.parameters["room"])
                frames.append({"trace_count": trace_index, "position": position, "action": f"Checking the {self._room(room)} for changes", "goal": room, "waypoint": room, "phase": phase, "observation_status": "Checking for changes", "logs": []})
                anomalies = detail.get("anomalies", [])
                labels = {"unexpected_obstacle": "An unexpected obstacle was detected", "high_humidity": "High humidity was detected", "high_temperature": "High temperature was detected", "blocked_transition": "A blocked transition was detected", "missing_observation": "A valid observation was unavailable"}
                messages = [f"{labels.get(anomaly.get('anomaly_type'), 'An anomaly was detected')} in the {self._room(anomaly.get('room'))}." for anomaly in anomalies] or [f"No environmental anomaly was detected in the {self._room(room)}."]
                frames.append({"trace_count": trace_index + 1, "position": position, "action": "Detected change" if anomalies else "No anomaly detected", "goal": room, "waypoint": room, "phase": self._phase_for(trace_index + 1), "observation_status": "Detection completed", "logs": messages})
            else:
                logs: list[str] = []
                if detail.get("updated"): logs.append(f"The {self._room(detail.get('room'))} Digital Twin was updated.")
                if detail.get("alert_id"): logs.append(f"An actionable {self._room(detail.get('room'))} alert was generated.")
                if detail.get("returned_to_start"): logs.append("The robot returned safely to the charging area.")
                if detail.get("stopped"): logs.append("The robot stopped safely.")
                if detail.get("markdown"): logs.append("The monitoring report was generated.")
                if item.event == "step_failed": logs.append(f"Task execution stopped: {self._failure_reason(str(detail.get('error', 'Unknown failure')))[0]}.")
                if item.event == "emergency_stop": logs.append("The robot stopped safely.")
                if detail.get("events_injected") is not None: logs.append("Environmental changes are now active for monitoring.")
                frames.append({"trace_count": trace_index + 1, "position": position, "action": self._action(item), "goal": step.parameters.get("room") if step else None, "waypoint": layout_location(position), "phase": self._phase_for(trace_index + 1), "observation_status": "Preparing next move", "logs": logs})
        return frames

    def _frame(self) -> dict[str, Any]:
        initial = self.bundle.get("initial_world_state", {"room": "charging_area", "pose": [1.0, 1.0], "battery": None, "time": 0.0, "visit_history": ["charging_area"]})
        visual = self.playback_trace[self.index - 1] if self.index else {"trace_count": 0, "position": list(HOUSE_LAYOUT["checkpoints"]["charging_area"]), "action": "Ready to start", "goal": None, "waypoint": "charging_area", "phase": "Baseline patrol", "observation_status": "Waiting", "logs": []}
        trace_count = visual["trace_count"]
        frame = {"current_room": layout_location(visual["position"]), "pose": visual["position"], "battery": initial.get("battery"), "simulation_time": initial.get("time", 0.0), "visited_rooms": list(initial.get("visit_history", [])), "routes": [], "events": [], "visual_events": self.bundle.get("visual_event_manifest", {}).get("events", []), "observations": [], "anomalies": [], "twin_updates": [], "alerts": [], "report": None, "stopped": False, "task_phase": "ready", "trace_count": trace_count, "playback_action": visual["action"], "goal": visual["goal"], "waypoint": visual["waypoint"], "patrol_phase": visual["phase"], "observation_status": visual["observation_status"], "travelled_path": [entry["position"] for entry in self.playback_trace[:self.index]], "planned_path": []}
        event_records = self.bundle.get("scenario_ground_truth", {}).get("events", [])
        if self.index < len(self.playback_trace):
            frame["planned_path"] = [entry["position"] for entry in self.playback_trace[self.index:] if entry.get("goal") == visual.get("goal")][:32]
        for item in self.trace[:trace_count]:
            detail = item.details
            frame["task_phase"] = item.step_id or item.event
            room = detail.get("entered_room") or detail.get("revisited_room")
            if room:
                frame["visited_rooms"].append(room)
            if "route" in detail: frame["routes"].append(detail["route"])
            if "battery" in detail: frame["battery"] = detail["battery"]
            if "simulation_time" in detail: frame["simulation_time"] = detail["simulation_time"]
            if item.step_id and "inject-household-events" in item.step_id or detail.get("events_injected"):
                frame["events"] = [{key: event[key] for key in ("event_id", "type", "room")} for event in event_records]
            if "observation_id" in detail: frame["observations"].append(detail)
            if "anomalies" in detail: frame["anomalies"].extend(detail["anomalies"])
            if "changed_fields" in detail or "updated" in detail: frame["twin_updates"].append(detail)
            if "alert_id" in detail: frame["alerts"].append(detail)
            if "markdown" in detail: frame["report"] = detail["markdown"]
            if detail.get("stopped"): frame["stopped"] = True
            if item.event == "step_failed": frame["failure"] = detail.get("error")
        return frame

    def _artifact_files(self) -> list[str]:
        return sorted(item.name for item in self.artifact_dir.iterdir() if item.is_file()) if self.artifact_dir else []

    def _visible_twin(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        before = self.bundle.get("digital_twin_before")
        if before is None:
            return None
        current = deepcopy(before)
        for update in frame["twin_updates"]:
            if update.get("updated") and update.get("room"):
                current["rooms"][update["room"]] = deepcopy(update["after"])
        return current

    @staticmethod
    def _room(room: Any) -> str:
        return str(room or "charging_area").replace("_", " ")

    def _step(self, step_id: str | None) -> Any:
        if self.planning and self.planning.candidate_task:
            return next((step for step in self.planning.candidate_task.steps if step.step_id == step_id), None)
        return None

    def _action(self, item: Any) -> str:
        if item.event == "emergency_stop": return "Task execution stopped"
        if item.event == "step_failed": return "Task execution stopped"
        step = self._step(item.step_id)
        if step is None: return "Task execution stopped"
        room = self._room(step.parameters.get("room"))
        return {
            "move_to_room": f"Moving to the {room}", "revisit_active_event_rooms": f"Returning to the {room}",
            "inspect_room": f"Inspecting the {room}", "establish_household_baseline": f"Recording the {room} baseline",
            "record_baseline": f"Recording the {room} baseline", "inject_household_events": "Starting the monitoring phase",
            "detect_environment_change": f"Checking the {room} for changes", "update_digital_twin": f"Updating the {room} Digital Twin",
            "generate_alert": f"Preparing the {room} alert", "return_to_start": "Returning to the charging area",
            "stop": "Stopping safely", "generate_monitoring_report": "Generating the monitoring report",
        }.get(step.skill, "Executing the verified task")

    def _purpose(self, item: Any) -> str:
        step = self._step(item.step_id)
        if step is None: return "Keep the robot in a safe state."
        room = self._room(step.parameters.get("room"))
        return {
            "move_to_room": f"Reach the {room} for the verified patrol.", "revisit_active_event_rooms": f"Recheck the {room} after monitoring begins.",
            "inspect_room": f"Collect a simulated observation in the {room}.", "establish_household_baseline": "Create a comparison point for later observations.",
            "record_baseline": "Create a comparison point for later observations.", "inject_household_events": "Begin the planned monitoring phase.",
            "detect_environment_change": "Compare the latest observation with the baseline.", "update_digital_twin": "Record only observation-supported changes.",
            "generate_alert": "Create an alert only when a detected anomaly supports it.", "return_to_start": "Return safely to the charging area.",
            "stop": "End the verified task safely.", "generate_monitoring_report": "Create the monitoring evidence report.",
        }.get(step.skill, "Complete the verifier-approved task.")

    @staticmethod
    def _failure_reason(message: str) -> tuple[str, str]:
        lowered = message.casefold()
        if "battery" in lowered and "insufficient" in lowered:
            return "Insufficient battery to complete the verified route", "Revise the task or increase the initial battery level."
        if "blocked" in lowered or "transition" in lowered or "no legal route" in lowered:
            return "A required transition is blocked", "Clear the route and run the verified task again."
        return message, "Review the verified task and resolve the reported condition."

    def _activity_log(self) -> list[dict[str, str]]:
        log: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for index, entry in enumerate(self.playback_trace[:self.index]):
            trace_count = entry["trace_count"]
            item = self.trace[trace_count - 1] if trace_count else None
            for message in entry.get("logs", []):
                key = (item.timestamp if item else f"playback:{index:03d}", item.step_id if item else "playback", message)
                if key in seen: continue
                seen.add(key); log.append({"time": item.timestamp if item else f"playback:{index:03d}", "step": item.step_id if item else "playback", "message": message})
        return log

    def _summary(self, frame: dict[str, Any]) -> dict[str, Any]:
        base = {"current_room": "Waiting for a task", "current_location": "Waiting for a task", "current_action": "Not started", "current_goal": "Waiting for verification", "route": "Not started", "patrol_phase": "Waiting", "observation_status": "Not started", "newly_detected_change": "No anomaly detected yet", "robot_status": "Waiting for verification", "safety_status": "Waiting for verification", "progress": {"completed": 0, "total": 0, "percent": 0}, "detected_anomalies": [], "digital_twin_status": "No Digital Twin update yet", "next_action": "Waiting for verification", "purpose": "Create and verify a task before the simulation can run.", "activity_log": []}
        if self.planning is None: return base
        if self.report is None:
            base.update({"current_action": "Task planned — not executing", "robot_status": "Awaiting verification", "next_action": "Validate the candidate task", "purpose": "Verification must approve the task before execution."}); return base
        if not self.report.approved:
            base.update({"current_action": "Task rejected before execution", "robot_status": "Warning", "next_action": "Revise the task", "purpose": (self.report.safety_summary or ["The verifier did not approve this task."])[0]}); return base
        total, completed = len(self.playback_trace), self.index
        if not total:
            base.update({"current_room": "Waiting to run", "current_action": "Task approved — not executing", "robot_status": "Ready to run", "next_action": "Run the verified task", "purpose": "The verifier approved this task for the local simulation."}); return base
        new_change = frame["anomalies"][-1] if frame["anomalies"] else None
        base.update({"current_room": self._room(frame.get("current_room")), "current_location": self._room(frame.get("current_room")), "current_action": frame["playback_action"], "current_goal": self._room(frame.get("goal")) if frame.get("goal") else "Safe completion", "route": f"via {self._room(frame.get('waypoint'))}", "patrol_phase": frame["patrol_phase"], "observation_status": frame["observation_status"], "newly_detected_change": f"{new_change['anomaly_type'].replace('_', ' ')} in {self._room(new_change['room'])}" if new_change else "No anomaly detected yet", "safety_status": "Safe to continue", "progress": {"completed": completed, "total": total, "percent": round(100 * completed / total) if total else 0}, "detected_anomalies": frame["anomalies"], "digital_twin_status": "Digital Twin updated" if any(update.get("updated") for update in frame["twin_updates"]) else "No Digital Twin update yet", "activity_log": self._activity_log()})
        failure = frame.get("failure")
        if failure:
            reason, next_action = self._failure_reason(str(failure)); base.update({"current_action": "Task execution stopped", "robot_status": "Safely stopped" if frame.get("stopped") else "Failed", "safety_status": "Safely stopped" if frame.get("stopped") else "Failure requires attention", "next_action": next_action, "purpose": reason}); return base
        if completed == total:
            if self.result and self.result.success:
                base.update({"current_action": "Task completed", "robot_status": "Completed", "safety_status": "Safely stopped", "next_action": "Task complete", "purpose": "The robot returned safely and the monitoring report was generated."})
            else:
                reason, next_action = self._failure_reason(str(self.result.first_failure if self.result else "Task failed")); base.update({"current_action": "Task execution stopped", "robot_status": "Safely stopped", "safety_status": "Safely stopped", "next_action": next_action, "purpose": reason})
            return base
        next_frame = self.playback_trace[completed]
        base.update({"next_action": next_frame["action"], "purpose": f"Continue the verified route toward the {self._room(next_frame.get('goal'))}." if next_frame.get("goal") else "Continue the verifier-approved task."})
        base["robot_status"] = "Paused" if self.explicitly_paused else "Running" if self.started and not self.paused else "Ready to run"
        base["safety_status"] = "Paused safely" if self.explicitly_paused else "Safe to continue"
        return base

    def state(self) -> dict[str, Any]:
        with self._lock:
            frame = self._frame()
            world = {**self.bundle.get("simulator_config", {"rooms": ROOMS, "doors": [list(item) for item in DOORS]}), "layout": HOUSE_LAYOUT}
            feedback = build_feedback(self.planning.extracted_rooms if self.planning else [], self.trace, self.bundle, trace_count=frame["trace_count"], execution_success=None) if self.bundle else None
            if self.index == len(self.playback_trace) and self.bundle: feedback = self.bundle.get("robot_feedback")
            return {"phase": self.phase, "busy": self._busy, "planning": self.planning.model_dump(mode="json") if self.planning else None, "scenario_planning": self.scenario_planning, "scenario_verification": self.scenario_report, "verification": self.report.model_dump(mode="json") if self.report else None, "execution": self.result.model_dump(mode="json") if self.result else None, "robot_feedback": feedback, "playback": {"index": self.index, "total": len(self.playback_trace), "paused": self.paused, "speed": self.speed, "frame": frame}, "summary": self._summary(frame), "world": world, "digital_twin_before": self.bundle.get("digital_twin_before"), "digital_twin_current": self._visible_twin(frame), "artifact_directory": str(self.artifact_dir) if self.artifact_dir else None, "artifact_files": self._artifact_files(), "simulation_only": True, "physical_robot_validated": False}

    def artifact(self, name: str) -> dict[str, Any]:
        with self._lock:
            if self.artifact_dir is None or Path(name).name != name or name not in self._artifact_files():
                raise DemoError("Artifact file is not available for this demo run.")
            return {"name": name, "content": (self.artifact_dir / name).read_text(encoding="utf-8")}


def _legacy_page_with_inline_handlers() -> str:
    return """<!doctype html><html><head><meta charset=\"utf-8\"><title>RaPToR-Lite House-Sitter Demo</title><style>
body{font:14px system-ui,sans-serif;margin:0;background:#f4f6f8;color:#17212b}header{padding:14px 22px;background:#17324d;color:#fff}main{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;padding:12px}section{background:#fff;border:1px solid #ccd5df;border-radius:8px;padding:12px;min-width:0}textarea,select,input,button{font:inherit;margin:4px 0;padding:7px}textarea{width:96%;height:95px}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}pre{white-space:pre-wrap;overflow:auto;max-height:220px;background:#f6f8fa;padding:8px}.boundary{color:#f7d794;font-weight:700}.ok{color:#16733b}.bad{color:#b32424}.room{fill:#eaf1f7;stroke:#47657c;stroke-width:2}.event{fill:#ff5b5b}.robot{fill:#1b79d1;stroke:#0c395f;stroke-width:2}.route{fill:none;stroke:#37a867;stroke-width:5;stroke-linecap:round}.door{stroke:#7b5725;stroke-width:5}small{color:#536273}.row{display:flex;gap:6px;flex-wrap:wrap}@media(max-width:1000px){main{grid-template-columns:1fr}}</style></head><body><header><b>RaPToR-Lite House-Sitter Demo</b><span class=\"boundary\"> simulation-only — physical robot validation not performed</span></header><main>
<section><h2>Task Creation</h2><textarea id=\"text\">Run a complete house-sitter patrol and report any environmental changes.</textarea><label>Seed <input id=\"seed\" type=number value=12345 min=0 max=2147483647></label><label>Scenario <select id=\"scenario\"><option value=\"complete\">Kitchen obstacle + bathroom humidity</option><option value=\"normal\">Normal</option><option value=\"dropout\">Observation dropout</option><option value=\"blocked\">Blocked transition</option><option value=\"low_battery\">Low battery</option></select></label><div class=\"row\"><button onclick=\"plan()\">Plan</button><button onclick=\"validate()\">Validate</button><button id=\"run\" onclick=\"run()\">Run</button><button onclick=\"demo()\">Run Complete House-Sitter Demo</button><button onclick=\"reset()\">Reset</button></div><h3>Planning Result</h3><pre id=\"planning\"></pre><h3>Verification</h3><pre id=\"verify\"></pre></section>
<section><h2>Household Simulation</h2><svg id=\"map\" viewBox=\"0 0 620 480\" width=\"100%\" aria-label=\"House2D replay\"></svg><div class=\"row\"><button onclick=\"play('pause')\">Pause</button><button onclick=\"play('resume')\">Resume</button><button onclick=\"play('step')\">Step</button><button onclick=\"play('restart')\">Restart</button><button onclick=\"play('faster')\">Run faster</button></div><pre id=\"status\"></pre><h3>Sensor Observations</h3><pre id=\"observations\"></pre></section>
<section><h2>System Evidence</h2><h3>Candidate Task</h3><pre id=\"task\"></pre><h3>Capability Match</h3><pre id=\"caps\"></pre><h3>Detected Anomalies / Alerts</h3><pre id=\"alerts\"></pre><h3>Digital Twin Diff</h3><pre id=\"twin\"></pre><h3>Monitoring Report</h3><pre id=\"report\"></pre><h3>Artifacts</h3><div id=\"files\"></div><pre id=\"file\"></pre></section></main><script>
let state={};const $=id=>document.getElementById(id);const show=(id,v)=>$(id).textContent=JSON.stringify(v,null,2);async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const x=await r.json();if(!r.ok)throw Error(x.error);return x}function text(){return $('text').value}function seed(){return Number($('seed').value)}function scenario(){return $('scenario').value}async function plan(){try{render(await api('/api/plan',{text:text()}))}catch(e){alert(e.message)}}async function validate(){try{render(await api('/api/validate',{}))}catch(e){alert(e.message)}}async function run(){try{render(await api('/api/run',{seed:seed(),scenario:scenario()}))}catch(e){alert(e.message)}}async function demo(){try{render(await api('/api/demo',{seed:seed()}))}catch(e){alert(e.message)}}async function reset(){render(await api('/api/reset',{}))}async function play(action){try{render(await api('/api/playback',{action}))}catch(e){alert(e.message)}}async function tick(){if(state.playback&&!state.playback.paused)render(await api('/api/advance',{}))}setInterval(tick,350);
function node(tag,attrs={},value){const n=document.createElementNS('http://www.w3.org/2000/svg',tag);Object.entries(attrs).forEach(([k,v])=>n.setAttribute(k,v));if(value)n.textContent=value;return n}function draw(s){const svg=$('map');svg.replaceChildren();const source=s.world?.rooms||{};const rooms={};Object.entries(source).forEach(([name,r])=>{const b=r.bounds;rooms[name]=[20+b[0]*57,20+(10-b[3])*40,(b[2]-b[0])*57,(b[3]-b[1])*40]});Object.entries(rooms).forEach(([name,a])=>{svg.append(node('rect',{x:a[0],y:a[1],width:a[2],height:a[3],class:'room'}));svg.append(node('text',{x:a[0]+10,y:a[1]+23},name.replace('_',' '))});const center=n=>{const a=rooms[n]||rooms.charging_area;return[a[0]+a[2]/2,a[1]+a[3]/2]};(s.world?.doors||[]).forEach(d=>{const a=center(d[0]),b=center(d[1]);svg.append(node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],class:'door'}))});const f=s.playback?.frame||{};(f.routes||[]).forEach(route=>{const p=route.map(r=>center(r).join(',')).join(' ');svg.append(node('polyline',{points:p,class:'route'}))});(f.events||[]).forEach(e=>{const c=center(e.room);svg.append(node('rect',{x:c[0]-12,y:c[1]-12,width:24,height:24,class:'event'}));svg.append(node('text',{x:c[0]-30,y:c[1]-20},e.type))});const c=center(f.current_room);svg.append(node('circle',{cx:c[0],cy:c[1],r:12,class:'robot'}));}
function render(s){state=s;const p=s.planning||{};show('planning',{status:p.status,intent:p.detected_intent,rooms:p.extracted_rooms,checks:p.extracted_checks,automatic_safety:p.automatic_addition_reasons,warnings:p.warnings,clarification:p.clarification_questions,unsupported:p.unsupported_elements});show('verify',s.verification||{message:'Plan before validation'});show('task',p.candidate_task||{});show('caps',{resolved_capabilities:(s.verification||{}).resolved_capabilities,safety_summary:(s.verification||{}).safety_summary});const f=s.playback?.frame||{},done=s.playback&&s.playback.index===s.playback.total;show('status',{phase:s.phase,run:s.artifact_directory,playback:s.playback,room:f.current_room,battery:f.battery,time:f.simulation_time,stopped:f.stopped,execution_success:done?s.execution?.success:'replay_pending',first_failure:f.failure||(done?s.execution?.first_failure:null)});show('observations',f.observations||[]);show('alerts',{anomalies:f.anomalies||[],alerts:f.alerts||[]});show('twin',{baseline:s.digital_twin_before,current:s.digital_twin_current,updates:f.twin_updates||[]});$('report').textContent=f.report||'';$('run').disabled=!(s.verification&&s.verification.approved);const files=$('files');files.replaceChildren();(s.artifact_files||[]).forEach(name=>{const b=document.createElement('button');b.textContent=name;b.onclick=async()=>{$('file').textContent=(await api('/api/artifact?name='+encodeURIComponent(name))).content};files.append(b)});draw(s)}api('/api/state').then(render);</script></body></html>"""


def _page() -> str:
    """Return static markup; all application code is served as /app.js."""
    return """<!doctype html>
<html><head><meta charset="utf-8"><title>RaPToR-Lite House-Sitter Demo</title><style>
body{font:14px system-ui,sans-serif;margin:0;background:#f4f6f8;color:#17212b}header{padding:14px 22px;background:#17324d;color:#fff}#app-status{margin:8px 12px;padding:8px;border-radius:5px;background:#e7f1fb;color:#17324d}#app-status[data-state=error]{background:#fde8e8;color:#8b1f1f}main{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;padding:12px}section{background:#fff;border:1px solid #ccd5df;border-radius:8px;padding:12px;min-width:0}textarea,select,input,button{font:inherit;margin:4px 0;padding:7px}textarea{width:96%;height:95px}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}pre{white-space:pre-wrap;overflow:auto;max-height:220px;background:#f6f8fa;padding:8px}.boundary{color:#f7d794;font-weight:700}.room{fill:#eaf1f7;stroke:#354d61;stroke-width:4}.hallway{fill:#f5ead7;stroke:#354d61;stroke-width:4}.furniture{fill:#bfd1df;stroke:#60798c;stroke-width:1}.event{fill:#ff5b5b}.robot{fill:#1b79d1;stroke:#0c395f;stroke-width:2}.route{fill:none;stroke:#37a867;stroke-width:5;stroke-linecap:round}.planned{fill:none;stroke:#7c8da0;stroke-width:3;stroke-dasharray:7 5}.door{stroke:#fff;stroke-width:8}.checkpoint{fill:#fff;stroke:#60798c;stroke-width:2}.anomaly-label{fill:#8b1f1f;font-weight:700}.legend{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0;color:#536273}.legend span::before{content:'•';font-size:20px;padding-right:3px}.legend .planned-key::before{color:#7c8da0}.legend .travelled-key::before{color:#37a867}.legend .event-key::before{color:#ff5b5b}.row{display:flex;gap:6px;flex-wrap:wrap}.summary{background:#f6f8fa;padding:8px;border-radius:5px}.summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px}.summary-grid dt{font-weight:700}.summary-grid dd{margin:1px 0 0}.activity-log{max-height:180px;overflow:auto;margin:8px 0 0;padding-left:20px}.activity-log li{margin:4px 0}@media(max-width:1000px){main{grid-template-columns:1fr}}@media(max-width:500px){.summary-grid{grid-template-columns:1fr}}</style></head>
<body><header><b>RaPToR-Lite House-Sitter Demo</b><span class="boundary"> simulation-only — physical robot validation not performed</span></header>
<div id="app-status" data-state="loading" role="status">Loading interface…</div>
<main>
<section><h2>Task Creation</h2><label>Robot Task</label><textarea id="task-text">Patrol the bedroom and tell me whether anything is wrong.</textarea><label>Household Scenario</label><textarea id="scenario-text">The bedroom is normal.</textarea><label>Seed <input id="seed" type="number" value="12345" min="0" max="2147483647"></label><div class="row"><button id="interpret-button">Interpret Scenario</button><button id="plan-button">Plan Task</button><button id="validate-button">Validate</button><button id="run-button" disabled>Run</button><button id="reset-button">Reset</button></div><small>Example: There is a box in the bedroom and the bathroom has high humidity.</small><details><summary>Technical planning evidence</summary><h3>Task Planning Result</h3><pre id="planning"></pre><h3>Scenario Planning Result</h3><pre id="scenario-planning"></pre><h3>Task Verification</h3><pre id="verify"></pre><h3>Scenario Verification</h3><pre id="scenario-verify"></pre></details></section>
<section><h2>Household Simulation</h2><svg id="map" viewBox="0 0 720 520" width="100%" aria-label="House2D replay"></svg><div class="legend"><span class="planned-key">planned route</span><span class="travelled-key">travelled route</span><span class="event-key">physical event / confirmed change</span></div><div class="row"><button id="pause-button">Pause</button><button id="resume-button">Resume</button><button id="step-button">Step</button><button id="restart-button">Restart</button><button id="faster-button">Run faster</button></div><pre id="status"></pre><h3>Sensor Observations</h3><pre id="observations"></pre></section>
<section><h2>Robot Feedback</h2><pre id="feedback"></pre><h3>Detected Anomalies / Alerts</h3><pre id="alerts"></pre><h3>Digital Twin Diff</h3><pre id="twin"></pre><h3>Monitoring Report</h3><pre id="report"></pre><article class="summary" aria-labelledby="summary-heading"><h3 id="summary-heading">Live Demo Summary</h3><dl id="summary-fields" class="summary-grid"></dl><h4>Activity Log</h4><ol id="activity-log" class="activity-log" aria-live="polite"></ol></article><details><summary>Technical evidence</summary><h3>Candidate Task</h3><pre id="task"></pre><h3>Capability Match</h3><pre id="caps"></pre><h3>Artifacts</h3><div id="files"></div><pre id="file"></pre></details></section>
</main>
<script>window.raptorDemo={initialized:false,lastError:null};window.addEventListener("error",function(event){var status=document.getElementById("app-status");window.raptorDemo.lastError=event.message;status.textContent="JavaScript error: "+event.message;status.dataset.state="error";});</script>
<script src="/app.js"></script></body></html>"""


def _script() -> str:
    """Return the static, parseable application script without user interpolation."""
    return r"""(() => {
  "use strict";
  let state = {};
  const app = window.raptorDemo;
  const byId = (id) => document.getElementById(id);
  const show = (id, value) => { byId(id).textContent = JSON.stringify(value, null, 2); };
  const setStatus = (message, level = "ready") => {
    const status = byId("app-status");
    status.textContent = message;
    status.dataset.state = level;
  };
  async function request(path, body) {
    const response = await fetch(path, {
      method: body ? "POST" : "GET",
      headers: {"Content-Type": "application/json"},
      body: body ? JSON.stringify(body) : undefined,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The demo request failed.");
    return payload;
  }
  const taskText = () => byId("task-text").value;
  const scenarioText = () => byId("scenario-text").value;
  const seed = () => Number(byId("seed").value);
  async function perform(message, path, body, completed) {
    setStatus(message);
    try {
      render(await request(path, body));
      setStatus(completed);
    } catch (error) {
      app.lastError = error.message;
      setStatus("Request failed: " + error.message, "error");
    }
  }
  const interpret = () => perform("Interpreting scenario…", "/api/interpret-scenario", {text: scenarioText(), seed: seed()}, "Scenario interpretation is ready.");
  const plan = () => perform("Planning task…", "/api/plan", {text: taskText()}, "Task plan is ready.");
  const validate = () => perform("Validating candidate task…", "/api/validate", {}, "Verification is complete.");
  const run = () => perform("Executing approved task…", "/api/run", {seed: seed()}, "Execution trace is ready.");
  // Preparing demonstration… remains available through the localhost API for legacy automation.
  const reset = () => perform("Resetting demonstration…", "/api/reset", {}, "Demonstration has been reset.");
  const playback = (action) => perform("Updating replay…", "/api/playback", {action}, "Replay updated.");
  async function tick() {
    if (!state.playback || state.playback.paused) return;
    try { render(await request("/api/advance", {})); }
    catch (error) { app.lastError = error.message; setStatus("Replay failed: " + error.message, "error"); }
  }
  function node(tag, attrs = {}, value = "") {
    const item = document.createElementNS("http://www.w3.org/2000/svg", tag);
    Object.entries(attrs).forEach(([key, value]) => item.setAttribute(key, value));
    if (value) item.textContent = value;
    return item;
  }
  function draw(snapshot) {
    const svg = byId("map");
    svg.replaceChildren();
    const frame = snapshot.playback?.frame || {};
    const layout = snapshot.world?.layout || {rooms: {}, checkpoints: {}, doors: [], furniture: []};
    const point = ([x, y]) => [40 + x * 53, 480 - y * 43];
    Object.entries(layout.rooms).forEach(([name, room]) => {
      const [left, bottom, right, top] = room.bounds, origin = point([left, top]);
      svg.append(node("rect", {x: origin[0], y: origin[1], width: (right - left) * 53, height: (top - bottom) * 43, class: name === "hallway" ? "hallway" : "room"}));
      svg.append(node("text", {x: origin[0] + 10, y: origin[1] + 22}, room.label));
    });
    layout.furniture.forEach(([, , bounds]) => { const [left, bottom, right, top] = bounds, origin = point([left, top]); svg.append(node("rect", {x: origin[0], y: origin[1], width: (right - left) * 53, height: (top - bottom) * 43, class: "furniture"})); });
    Object.values(layout.checkpoints).forEach((position) => { const [x, y] = point(position); svg.append(node("circle", {cx: x, cy: y, r: 4, class: "checkpoint"})); });
    layout.doors.forEach(([x, y, direction]) => { const [sx, sy] = point([x, y]); svg.append(node("line", direction === "vertical" ? {x1: sx, y1: sy - 10, x2: sx, y2: sy + 10, class: "door"} : {x1: sx - 10, y1: sy, x2: sx + 10, y2: sy, class: "door"})); });
    const polyline = (positions, className) => { if (positions?.length > 1) svg.append(node("polyline", {points: positions.map((position) => point(position).join(",")).join(" "), class: className})); };
    polyline(frame.planned_path, "planned");
    polyline(frame.travelled_path, "route");
    (frame.visual_events || []).forEach((event) => { const room = layout.rooms[event.room]; if (!room) return; const [left,bottom,right,top] = room.bounds; const relative = event.parameters?.position || [0.82,0.78]; const [x,y] = point([left + (right-left)*relative[0], bottom + (top-bottom)*relative[1]]); const icon = event.visual_representation?.icon; const confirmed = (frame.anomalies || []).some((item) => item.room === event.room && item.anomaly_type === event.event_type); if (icon === "box") svg.append(node("rect", {x:x-9,y:y-9,width:18,height:18,class:"event"})); else svg.append(node("text", {x:x-9,y:y+7,class:"anomaly-label"}, icon === "droplet" ? "💧" : icon === "thermometer" ? "🌡" : icon === "barrier" ? "⛔" : icon === "sensor_unknown" ? "?" : "▱")); if (confirmed) svg.append(node("text", {x:x-18,y:y-13,class:"anomaly-label"}, "detected")); });
    (frame.anomalies || []).forEach((anomaly, index) => { const checkpoint = layout.checkpoints[anomaly.room]; if (!checkpoint) return; const [x, y] = point(checkpoint); svg.append(node("text", {x: x + 12, y: y - 16 - index * 14, class: "anomaly-label"}, anomaly.anomaly_type.replaceAll("_", " "))); });
    const robot = point(frame.pose || layout.checkpoints.charging_area);
    svg.append(node("circle", {cx: robot[0], cy: robot[1], r: 12, class: "robot"}));
  }
  function renderSummary(summary) {
    const fields = byId("summary-fields");
    fields.replaceChildren();
    const values = [
      ["Current Location", summary.current_location], ["Current Action", summary.current_action], ["Current Goal", summary.current_goal], ["Route", summary.route],
      ["Patrol Phase", summary.patrol_phase], ["Observation Status", summary.observation_status], ["Newly Detected Change", summary.newly_detected_change],
      ["Robot Status", summary.robot_status], ["Progress", `${summary.progress.completed}/${summary.progress.total} (${summary.progress.percent}%)`],
      ["Safety Status", summary.safety_status], ["Detected Anomalies", summary.detected_anomalies.length ? summary.detected_anomalies.map((item) => `${item.anomaly_type.replaceAll("_", " ")} in ${item.room.replaceAll("_", " ")}`).join("; ") : "No anomaly detected yet"],
      ["Digital Twin Status", summary.digital_twin_status], ["Next Action", summary.next_action], ["Purpose", summary.purpose],
    ];
    values.forEach(([label, value]) => { const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; fields.append(term, detail); });
    const log = byId("activity-log");
    const followLatest = log.scrollTop + log.clientHeight >= log.scrollHeight - 8;
    log.replaceChildren();
    summary.activity_log.forEach((entry) => { const item = document.createElement("li"); item.textContent = `[${entry.time}] ${entry.message}`; log.append(item); });
    if (followLatest) log.scrollTop = log.scrollHeight;
  }
  function render(snapshot) {
    state = snapshot;
    const planning = snapshot.planning || {};
    show("planning", {status: planning.status, intent: planning.detected_intent, rooms: planning.extracted_rooms, checks: planning.extracted_checks, automatic_safety: planning.automatic_addition_reasons, warnings: planning.warnings, clarification: planning.clarification_questions, unsupported: planning.unsupported_elements});
    show("scenario-planning", snapshot.scenario_planning || {message: "Interpret a household scenario first."});
    show("scenario-verify", snapshot.scenario_verification || {message: "No scenario verification yet."});
    show("verify", snapshot.verification || {message: "Plan before validation"});
    show("task", planning.candidate_task || {});
    show("caps", {resolved_capabilities: snapshot.verification?.resolved_capabilities, safety_summary: snapshot.verification?.safety_summary});
    renderSummary(snapshot.summary);
    const frame = snapshot.playback?.frame || {};
    const completeReplay = snapshot.playback && snapshot.playback.index === snapshot.playback.total;
    show("status", {phase: snapshot.phase, run: snapshot.artifact_directory, playback: snapshot.playback, room: frame.current_room, battery: frame.battery, time: frame.simulation_time, stopped: frame.stopped, execution_success: completeReplay ? snapshot.execution?.success : "replay_pending", first_failure: frame.failure || (completeReplay ? snapshot.execution?.first_failure : null)});
    show("observations", frame.observations || []);
    show("alerts", {anomalies: frame.anomalies || [], alerts: frame.alerts || []});
    show("twin", {baseline: snapshot.digital_twin_before, current: snapshot.digital_twin_current, updates: frame.twin_updates || []});
    byId("feedback").textContent = snapshot.robot_feedback?.final_message || "Robot feedback will be based on completed observations.";
    byId("report").textContent = frame.report || "";
    byId("run-button").disabled = !(snapshot.verification && snapshot.verification.approved);
    const files = byId("files");
    files.replaceChildren();
    (snapshot.artifact_files || []).forEach((name) => {
      const button = document.createElement("button");
      button.textContent = name;
      button.addEventListener("click", async () => {
        try { byId("file").textContent = (await request("/api/artifact?name=" + encodeURIComponent(name))).content; }
        catch (error) { app.lastError = error.message; setStatus("Artifact loading failed: " + error.message, "error"); }
      });
      files.append(button);
    });
    draw(snapshot);
  }
  byId("interpret-button").addEventListener("click", interpret);
  byId("plan-button").addEventListener("click", plan);
  byId("validate-button").addEventListener("click", validate);
  byId("run-button").addEventListener("click", run);
  byId("reset-button").addEventListener("click", reset);
  byId("pause-button").addEventListener("click", () => playback("pause"));
  byId("resume-button").addEventListener("click", () => playback("resume"));
  byId("step-button").addEventListener("click", () => playback("step"));
  byId("restart-button").addEventListener("click", () => playback("restart"));
  byId("faster-button").addEventListener("click", () => playback("faster"));
  app.initialized = true;
  setStatus("Ready. Create or select a task.");
  request("/api/state").then(render).catch((error) => { app.lastError = error.message; setStatus("Initialisation failed: " + error.message, "error"); });
  window.setInterval(tick, 350);
})();"""


def make_server(controller: DemoController, host: str = "127.0.0.1", port: int = 8765) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost"}:
        raise DemoError("The demo server may listen only on localhost.")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return None

        def _send(self, status: int, body: Any, content_type: str = "application/json; charset=utf-8") -> None:
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 65536: raise DemoError("Request body is too large.")
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict): raise DemoError("Request body must be a JSON object.")
            return data

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            try:
                if parsed.path == "/": self._send(HTTPStatus.OK, _page(), "text/html; charset=utf-8")
                elif parsed.path == "/app.js": self._send(HTTPStatus.OK, _script(), "application/javascript; charset=utf-8")
                elif parsed.path == "/favicon.ico": self._send(HTTPStatus.NO_CONTENT, "", "image/x-icon")
                elif parsed.path == "/api/health": self._send(HTTPStatus.OK, {"ok": True, "localhost_only": True, "simulation_only": True})
                elif parsed.path == "/api/state": self._send(HTTPStatus.OK, controller.state())
                elif parsed.path == "/api/artifact": self._send(HTTPStatus.OK, controller.artifact(parse_qs(parsed.query).get("name", [""])[0]))
                else: self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            except DemoError as exc: self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

        def do_POST(self) -> None:
            try:
                body = self._body()
                action = urlparse(self.path).path
                if action == "/api/plan": output = controller.plan(body.get("text"))
                elif action == "/api/interpret-scenario": output = controller.interpret_scenario(body.get("text"), body.get("seed"))
                elif action == "/api/validate": output = controller.validate()
                elif action == "/api/run": output = controller.run(body.get("seed"), body.get("scenario"))
                elif action == "/api/demo": output = controller.complete_demo(body.get("seed", 12345))
                elif action == "/api/reset": output = controller.reset()
                elif action == "/api/playback": output = controller.playback(str(body.get("action", "")))
                elif action == "/api/advance": output = controller.advance()
                else: self._send(HTTPStatus.NOT_FOUND, {"error": "Not found."}); return
                self._send(HTTPStatus.OK, output)
            except DemoError as exc: self._send(HTTPStatus.CONFLICT if "already" in str(exc) or "while" in str(exc) else HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            except (json.JSONDecodeError, UnicodeDecodeError): self._send(HTTPStatus.BAD_REQUEST, {"error": "Malformed JSON request."})

    return ThreadingHTTPServer((host, port), Handler)
