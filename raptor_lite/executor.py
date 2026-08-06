"""Deterministic simulation-only executor; it never imports ROS."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .backends import BackendError, RobotBackend
from .capability_registry import CapabilityRegistry
from .models import ExecutionResult, ExecutionStepResult, ExecutionTrace, TaskSpec, VerificationReport
from .verifier import verify_task


def _now() -> str:
    return datetime.now(UTC).isoformat()


class MockExecutor:
    mode = "mock"

    def run(self, task: TaskSpec, report: VerificationReport, registry: CapabilityRegistry) -> tuple[ExecutionResult, list[ExecutionTrace]]:
        if not report.approved or not verify_task(task, registry).approved:
            raise ValueError("Execution refused: verification was not approved.")
        started = _now()
        state: dict[str, Any] = {"room": "start", "baseline": {}, "observations": {}, "twin": {}, "alerts": [], "stopped": False}
        results: list[ExecutionStepResult] = []
        trace: list[ExecutionTrace] = []
        for step in task.steps:
            outcome = self._execute(step.skill, step.parameters, state)
            results.append(ExecutionStepResult(step_id=step.step_id, skill=step.skill, success=True, result=outcome))
            trace.append(ExecutionTrace(timestamp=_now(), event="step_completed", step_id=step.step_id, details=outcome))
        return ExecutionResult(success=True, step_results=results, start_timestamp=started, end_timestamp=_now()), trace

    def _execute(self, skill: str, params: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        room = params.get("room", state["room"])
        if skill == "move_to_room": state["room"] = room; return {"entered_room": room, "simulation_only": True}
        if skill == "inspect_room":
            observation = {"room": room, "obstacle_count": 0, "simulation_only": True, "simulated_onboard_sensor": True}
            state["observations"][room] = observation; return observation
        if skill == "record_baseline": state["baseline"][room] = state["observations"].get(room, {"room": room, "obstacle_count": 0}); return {"baseline_recorded": room}
        if skill == "detect_environment_change": return {"room": room, "changes": [], "simulation_only": True}
        if skill == "update_digital_twin": state["twin"][room] = "updated"; return {"room": room, "digital_twin_updated": True}
        if skill == "generate_alert":
            alert = {"room": room, "severity": params.get("severity", "warning"), "simulation_only": True}; state["alerts"].append(alert); return alert
        if skill == "generate_monitoring_report": return {"markdown": f"# Monitoring report\n\nRoom: {state['room']}\n", "simulation_only": True}
        if skill == "return_to_start": state["room"] = "start"; return {"returned_to_start": True}
        if skill == "stop": state["stopped"] = True; return {"stopped": True}
        raise ValueError(f"Mock adapter has no implementation for '{skill}'.")


class BackendExecutor:
    """Execute only an approved task through a bounded backend."""

    def __init__(self, backend: RobotBackend):
        self.backend = backend

    def run(self, task: TaskSpec, report: VerificationReport, registry: CapabilityRegistry) -> tuple[ExecutionResult, list[ExecutionTrace]]:
        if not report.approved or not verify_task(task, registry).approved:
            raise ValueError("Execution refused: verification was not approved.")
        started = _now()
        self.backend.initialize(task)
        results: list[ExecutionStepResult] = []
        trace: list[ExecutionTrace] = []
        first_failure: str | None = None
        try:
            for step in task.steps:
                try:
                    outcome = self.backend.execute(step.skill, step.parameters, float(step.timeout_seconds or 0))
                    result = ExecutionStepResult(step_id=step.step_id, skill=step.skill, success=True, result=outcome)
                    trace.append(ExecutionTrace(timestamp=f"sim:{self.backend.simulation_time():.3f}", event="step_completed", step_id=step.step_id, details=outcome))
                except (BackendError, ValueError, KeyError) as exc:
                    message = str(exc); result = ExecutionStepResult(step_id=step.step_id, skill=step.skill, success=False, message=message)
                    trace.append(ExecutionTrace(timestamp=f"sim:{self.backend.simulation_time():.3f}", event="step_failed", step_id=step.step_id, details={"error": message}))
                    first_failure = first_failure or message
                results.append(result)
                if not result.success:
                    if step.on_failure == "continue":
                        continue
                    if step.on_failure == "stop":
                        stop = self.backend.emergency_stop()
                        trace.append(ExecutionTrace(timestamp=f"sim:{self.backend.simulation_time():.3f}", event="emergency_stop", step_id=step.step_id, details=stop))
                    elif step.on_failure == "abort":
                        stop = self.backend.emergency_stop()
                        trace.append(ExecutionTrace(timestamp=f"sim:{self.backend.simulation_time():.3f}", event="emergency_stop", step_id=step.step_id, details=stop))
                    break
            return ExecutionResult(success=first_failure is None, step_results=results, first_failure=first_failure, start_timestamp=started, end_timestamp=_now()), trace
        finally:
            self.backend.cleanup()
