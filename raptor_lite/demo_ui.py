"""Localhost-only, dependency-free visual replay for the verified House-Sitter flow."""
from __future__ import annotations

import json
import threading
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .artifacts import write_planning_run
from .capability_registry import CapabilityRegistry
from .executor import BackendExecutor, MockExecutor
from .house2d import DOORS, ROOMS, House2DBackend
from .models import PlanningResult, VerificationReport
from .planner import OfflineHouseSitterPlanner
from .verifier import verify_task


DEFAULT_REQUEST = "Run a complete house-sitter patrol and report any environmental changes."
SCENARIOS = {
    "complete": {"events": ["unexpected_obstacle", "high_humidity"], "label": "Kitchen obstacle and bathroom humidity"},
    "normal": {"events": [], "label": "Normal patrol"},
    "dropout": {"events": ["observation_dropout"], "label": "Kitchen observation dropout"},
    "blocked": {"events": ["blocked_transition"], "label": "Blocked transition"},
    "low_battery": {"events": ["low_initial_battery"], "label": "Low initial battery"},
}


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
            self.report: VerificationReport | None = None
            self.result = None
            self.trace: list[Any] = []
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
                self.report = None; self.result = None; self.trace = []; self.bundle = {}; self.artifact_dir = None
                self.index = 0; self.paused = True; self.started = False; self.explicitly_paused = False; self.phase = "planned"
                return self.state()
            finally:
                self._end()

    def validate(self) -> dict[str, Any]:
        with self._lock:
            if self.planning is None:
                raise DemoError("Plan a natural-language request before validation.")
            self.report = verify_task(self.planning.candidate_task, self.registry) if self.planning.status == "planned" and self.planning.candidate_task else VerificationReport(approved=False, safety_summary=["No planned candidate task is available for validation."])
            self.phase = "approved" if self.report.approved else "rejected"
            return self.state()

    def _run(self, seed: int, scenario: str) -> dict[str, Any]:
        if self.planning is None or self.report is None or not self.report.approved or self.planning.candidate_task is None:
            raise DemoError("Execution is denied until a candidate task is verifier-approved.")
        if scenario not in SCENARIOS:
            raise DemoError("Scenario is not supported by this local demo.")
        backend = House2DBackend(seed=seed, events=SCENARIOS[scenario]["events"])
        self.result, self.trace = BackendExecutor(backend).run(self.planning.candidate_task, self.report, self.registry)
        self.bundle = backend.artifact_bundle()
        self.artifact_dir = write_planning_run(self.artifact_root, self.planning, self.registry.as_json(), self.report, self.result, self.trace, backend)
        self.index = 0; self.paused = True; self.started = False; self.explicitly_paused = False; self.phase = "playback" if self.result.success else "failed"
        return self.state()

    def run(self, seed: Any, scenario: str) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                return self._run(self._seed(seed), str(scenario))
            finally:
                self._end()

    def complete_demo(self, seed: Any = 12345) -> dict[str, Any]:
        with self._lock:
            self._begin()
            try:
                self.planning = self.planner.plan(DEFAULT_REQUEST)
                self.report = verify_task(self.planning.candidate_task, self.registry) if self.planning.candidate_task else VerificationReport(approved=False)
                self.phase = "approved" if self.report.approved else "rejected"
                return self._run(self._seed(seed), "complete")
            finally:
                self._end()

    def playback(self, action: str) -> dict[str, Any]:
        with self._lock:
            if not self.trace:
                raise DemoError("Run an approved task before controlling playback.")
            if action == "pause": self.paused = True; self.explicitly_paused = True
            elif action == "resume": self.paused = False; self.started = True; self.explicitly_paused = False
            elif action == "step": self.paused = True; self.started = True; self.explicitly_paused = False; self.index = min(len(self.trace), self.index + 1)
            elif action == "restart": self.paused = True; self.started = False; self.explicitly_paused = False; self.index = 0
            elif action == "faster": self.speed = 4 if self.speed == 1 else 1
            else: raise DemoError("Unknown playback action.")
            return self.state()

    def advance(self) -> dict[str, Any]:
        with self._lock:
            if self.trace and not self.paused:
                self.started = True
                self.index = min(len(self.trace), self.index + self.speed)
                if self.index == len(self.trace): self.paused = True
            return self.state()

    def _frame(self) -> dict[str, Any]:
        initial = self.bundle.get("initial_world_state", {"room": "charging_area", "pose": [1.0, 1.0], "battery": None, "time": 0.0, "visit_history": ["charging_area"]})
        frame = {"current_room": initial.get("room"), "pose": initial.get("pose"), "battery": initial.get("battery"), "simulation_time": initial.get("time", 0.0), "visited_rooms": list(initial.get("visit_history", [])), "routes": [], "events": [], "observations": [], "anomalies": [], "twin_updates": [], "alerts": [], "report": None, "stopped": False, "task_phase": "ready"}
        event_records = self.bundle.get("scenario_ground_truth", {}).get("events", [])
        for item in self.trace[:self.index]:
            detail = item.details
            frame["task_phase"] = item.step_id or item.event
            room = detail.get("entered_room") or detail.get("revisited_room")
            if room:
                frame["current_room"] = room; frame["visited_rooms"].append(room)
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
        def add(item: Any, message: str) -> None:
            key = (item.timestamp, item.step_id or item.event, message)
            if key not in seen:
                seen.add(key); log.append({"time": item.timestamp, "step": item.step_id or item.event, "message": message})
        for item in self.trace[:self.index]:
            detail = item.details
            room = self._room(detail.get("entered_room") or detail.get("revisited_room"))
            if item.event == "step_failed": add(item, f"Task execution stopped: {self._failure_reason(str(detail.get('error', 'Unknown failure')))[0]}."); continue
            if item.event == "emergency_stop": add(item, "The robot stopped safely."); continue
            if detail.get("entered_room") or detail.get("revisited_room"): add(item, f"The robot entered the {room}.")
            if "observation_id" in detail:
                if detail.get("observation_valid"): add(item, f"The robot inspected the {self._room(detail.get('room'))}.")
                else: add(item, f"The simulated observation in the {self._room(detail.get('room'))} was unavailable.")
            if "anomalies" in detail:
                anomalies = detail["anomalies"]
                if not anomalies: add(item, f"No environmental anomaly was detected in the {self._room(detail.get('room'))}.")
                for anomaly in anomalies:
                    labels = {"unexpected_obstacle": "An unexpected obstacle was detected", "high_humidity": "High humidity was detected", "high_temperature": "High temperature was detected", "blocked_transition": "A blocked transition was detected", "missing_observation": "A valid observation was unavailable"}
                    add(item, f"{labels.get(anomaly.get('anomaly_type'), 'An anomaly was detected')} in the {self._room(anomaly.get('room'))}.")
            if detail.get("updated"): add(item, f"The {self._room(detail.get('room'))} Digital Twin was updated.")
            if detail.get("alert_id"): add(item, f"An actionable {self._room(detail.get('room'))} alert was generated.")
            if detail.get("returned_to_start"): add(item, "The robot returned safely to the charging area.")
            if detail.get("stopped") and item.event != "emergency_stop": add(item, "The robot stopped safely.")
            if detail.get("markdown"): add(item, "The monitoring report was generated.")
        return log

    def _summary(self, frame: dict[str, Any]) -> dict[str, Any]:
        base = {"current_room": "Waiting for a task", "current_action": "Not started", "robot_status": "Waiting for verification", "progress": {"completed": 0, "total": 0, "percent": 0}, "detected_anomalies": [], "digital_twin_status": "No Digital Twin update yet", "next_action": "Waiting for verification", "purpose": "Create and verify a task before the simulation can run.", "activity_log": []}
        if self.planning is None: return base
        if self.report is None:
            base.update({"current_action": "Task planned — not executing", "robot_status": "Awaiting verification", "next_action": "Validate the candidate task", "purpose": "Verification must approve the task before execution."}); return base
        if not self.report.approved:
            base.update({"current_action": "Task rejected before execution", "robot_status": "Warning", "next_action": "Revise the task", "purpose": (self.report.safety_summary or ["The verifier did not approve this task."])[0]}); return base
        total, completed = len(self.trace), self.index
        if not total:
            base.update({"current_room": "Waiting to run", "current_action": "Task approved — not executing", "robot_status": "Ready to run", "next_action": "Run the verified task", "purpose": "The verifier approved this task for the local simulation."}); return base
        base.update({"current_room": self._room(frame.get("current_room")), "progress": {"completed": completed, "total": total, "percent": round(100 * completed / total) if total else 0}, "detected_anomalies": frame["anomalies"], "digital_twin_status": "Digital Twin updated" if any(update.get("updated") for update in frame["twin_updates"]) else "No Digital Twin update yet", "activity_log": self._activity_log()})
        failure = frame.get("failure")
        if failure:
            reason, next_action = self._failure_reason(str(failure)); base.update({"current_action": "Task execution stopped", "robot_status": "Safely stopped" if frame.get("stopped") else "Failed", "next_action": next_action, "purpose": reason}); return base
        if completed == total:
            if self.result and self.result.success:
                base.update({"current_action": "Task completed", "robot_status": "Completed", "next_action": "Task complete", "purpose": "The robot returned safely and the monitoring report was generated."})
            else:
                reason, next_action = self._failure_reason(str(self.result.first_failure if self.result else "Task failed")); base.update({"current_action": "Task execution stopped", "robot_status": "Safely stopped", "next_action": next_action, "purpose": reason})
            return base
        next_item = self.trace[completed]
        base.update({"next_action": self._action(next_item), "purpose": self._purpose(next_item)})
        if completed:
            base["current_action"] = self._action(self.trace[completed - 1])
        else: base["current_action"] = "Ready to start"
        base["robot_status"] = "Paused" if self.explicitly_paused else "Running" if self.started and not self.paused else "Ready to run"
        return base

    def state(self) -> dict[str, Any]:
        with self._lock:
            frame = self._frame()
            return {"phase": self.phase, "busy": self._busy, "planning": self.planning.model_dump(mode="json") if self.planning else None, "verification": self.report.model_dump(mode="json") if self.report else None, "execution": self.result.model_dump(mode="json") if self.result else None, "playback": {"index": self.index, "total": len(self.trace), "paused": self.paused, "speed": self.speed, "frame": frame}, "summary": self._summary(frame), "world": self.bundle.get("simulator_config", {"rooms": ROOMS, "doors": [list(item) for item in DOORS]}), "digital_twin_before": self.bundle.get("digital_twin_before"), "digital_twin_current": self._visible_twin(frame), "artifact_directory": str(self.artifact_dir) if self.artifact_dir else None, "artifact_files": self._artifact_files(), "simulation_only": True, "physical_robot_validated": False}

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
body{font:14px system-ui,sans-serif;margin:0;background:#f4f6f8;color:#17212b}header{padding:14px 22px;background:#17324d;color:#fff}#app-status{margin:8px 12px;padding:8px;border-radius:5px;background:#e7f1fb;color:#17324d}#app-status[data-state=error]{background:#fde8e8;color:#8b1f1f}main{display:grid;grid-template-columns:1fr 1.1fr 1fr;gap:12px;padding:12px}section{background:#fff;border:1px solid #ccd5df;border-radius:8px;padding:12px;min-width:0}textarea,select,input,button{font:inherit;margin:4px 0;padding:7px}textarea{width:96%;height:95px}button{cursor:pointer}button:disabled{cursor:not-allowed;opacity:.45}pre{white-space:pre-wrap;overflow:auto;max-height:220px;background:#f6f8fa;padding:8px}.boundary{color:#f7d794;font-weight:700}.room{fill:#eaf1f7;stroke:#47657c;stroke-width:2}.event{fill:#ff5b5b}.robot{fill:#1b79d1;stroke:#0c395f;stroke-width:2}.route{fill:none;stroke:#37a867;stroke-width:5;stroke-linecap:round}.door{stroke:#7b5725;stroke-width:5}.row{display:flex;gap:6px;flex-wrap:wrap}.summary{background:#f6f8fa;padding:8px;border-radius:5px}.summary-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px 12px}.summary-grid dt{font-weight:700}.summary-grid dd{margin:1px 0 0}.activity-log{max-height:180px;overflow:auto;margin:8px 0 0;padding-left:20px}.activity-log li{margin:4px 0}@media(max-width:1000px){main{grid-template-columns:1fr}}@media(max-width:500px){.summary-grid{grid-template-columns:1fr}}</style></head>
<body><header><b>RaPToR-Lite House-Sitter Demo</b><span class="boundary"> simulation-only — physical robot validation not performed</span></header>
<div id="app-status" data-state="loading" role="status">Loading interface…</div>
<main>
<section><h2>Task Creation</h2><textarea id="text">Run a complete house-sitter patrol and report any environmental changes.</textarea><label>Seed <input id="seed" type="number" value="12345" min="0" max="2147483647"></label><label>Scenario <select id="scenario"><option value="complete">Kitchen obstacle + bathroom humidity</option><option value="normal">Normal</option><option value="dropout">Observation dropout</option><option value="blocked">Blocked transition</option><option value="low_battery">Low battery</option></select></label><div class="row"><button id="plan-button">Plan</button><button id="validate-button">Validate</button><button id="run-button" disabled>Run</button><button id="complete-button">Run Complete House-Sitter Demo</button><button id="reset-button">Reset</button></div><h3>Planning Result</h3><pre id="planning"></pre><h3>Verification</h3><pre id="verify"></pre></section>
<section><h2>Household Simulation</h2><svg id="map" viewBox="0 0 620 480" width="100%" aria-label="House2D replay"></svg><div class="row"><button id="pause-button">Pause</button><button id="resume-button">Resume</button><button id="step-button">Step</button><button id="restart-button">Restart</button><button id="faster-button">Run faster</button></div><pre id="status"></pre><h3>Sensor Observations</h3><pre id="observations"></pre></section>
<section><h2>System Evidence</h2><h3>Candidate Task</h3><pre id="task"></pre><h3>Capability Match</h3><pre id="caps"></pre><article class="summary" aria-labelledby="summary-heading"><h3 id="summary-heading">Live Demo Summary</h3><dl id="summary-fields" class="summary-grid"></dl><h4>Activity Log</h4><ol id="activity-log" class="activity-log" aria-live="polite"></ol></article><h3>Detected Anomalies / Alerts</h3><pre id="alerts"></pre><h3>Digital Twin Diff</h3><pre id="twin"></pre><h3>Monitoring Report</h3><pre id="report"></pre><h3>Artifacts</h3><div id="files"></div><pre id="file"></pre></section>
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
  const taskText = () => byId("text").value;
  const seed = () => Number(byId("seed").value);
  const scenario = () => byId("scenario").value;
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
  const plan = () => perform("Planning request…", "/api/plan", {text: taskText()}, "Plan is ready.");
  const validate = () => perform("Validating candidate task…", "/api/validate", {}, "Verification is complete.");
  const run = () => perform("Executing approved task…", "/api/run", {seed: seed(), scenario: scenario()}, "Execution trace is ready.");
  const complete = () => perform("Preparing demonstration…", "/api/demo", {seed: seed()}, "Demonstration trace is ready.");
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
    const rooms = {};
    Object.entries(snapshot.world?.rooms || {}).forEach(([name, room]) => {
      const bounds = room.bounds;
      rooms[name] = [20 + bounds[0] * 57, 20 + (10 - bounds[3]) * 40, (bounds[2] - bounds[0]) * 57, (bounds[3] - bounds[1]) * 40];
    });
    Object.entries(rooms).forEach(([name, area]) => {
      svg.append(node("rect", {x: area[0], y: area[1], width: area[2], height: area[3], class: "room"}));
      svg.append(node("text", {x: area[0] + 10, y: area[1] + 23}, name.replace("_", " ")));
    });
    const center = (name) => { const area = rooms[name] || rooms.charging_area; return [area[0] + area[2] / 2, area[1] + area[3] / 2]; };
    (snapshot.world?.doors || []).forEach((door) => { const from = center(door[0]); const to = center(door[1]); svg.append(node("line", {x1: from[0], y1: from[1], x2: to[0], y2: to[1], class: "door"})); });
    const frame = snapshot.playback?.frame || {};
    (frame.routes || []).forEach((route) => svg.append(node("polyline", {points: route.map((room) => center(room).join(",")).join(" "), class: "route"})));
    (frame.events || []).forEach((event) => { const point = center(event.room); svg.append(node("rect", {x: point[0] - 12, y: point[1] - 12, width: 24, height: 24, class: "event"})); svg.append(node("text", {x: point[0] - 30, y: point[1] - 20}, event.type)); });
    const robot = center(frame.current_room);
    svg.append(node("circle", {cx: robot[0], cy: robot[1], r: 12, class: "robot"}));
  }
  function renderSummary(summary) {
    const fields = byId("summary-fields");
    fields.replaceChildren();
    const values = [
      ["Current Room", summary.current_room], ["Current Action", summary.current_action],
      ["Robot Status", summary.robot_status], ["Progress", `${summary.progress.completed}/${summary.progress.total} (${summary.progress.percent}%)`],
      ["Detected Anomalies", summary.detected_anomalies.length ? summary.detected_anomalies.map((item) => `${item.anomaly_type.replaceAll("_", " ")} in ${item.room.replaceAll("_", " ")}`).join("; ") : "No anomaly detected yet"],
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
  byId("plan-button").addEventListener("click", plan);
  byId("validate-button").addEventListener("click", validate);
  byId("run-button").addEventListener("click", run);
  byId("complete-button").addEventListener("click", complete);
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
