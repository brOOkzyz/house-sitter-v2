"""Persistent evidence for every verification and mock execution."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ExecutionResult, ExecutionTrace, PlanningResult, TaskSpec, VerificationReport


def write_run(root: Path, task: TaskSpec, capabilities: dict[str, Any], report: VerificationReport, result: ExecutionResult | None = None, trace: list[ExecutionTrace] | None = None, backend: Any | None = None, *, write_unexecuted_result: bool = True) -> Path:
    run_id = f"{time.time_ns()}_{task.task_id}"
    output = Path(root) / run_id
    output.mkdir(parents=True, exist_ok=False)
    dump = lambda path, value: path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dump(output / "input_task.json", task.model_dump(mode="json")); dump(output / "robot_capabilities.json", capabilities)
    dump(output / "verification_report.json", report.model_dump(mode="json")); dump(output / "resolved_task.json", task.model_dump(mode="json"))
    with (output / "execution_trace.jsonl").open("w", encoding="utf-8") as handle:
        for item in trace or []: handle.write(item.model_dump_json() + "\n")
    execution = result or ExecutionResult(success=False, first_failure="Not executed.")
    if result is not None or write_unexecuted_result:
        dump(output / "execution_result.json", execution.model_dump(mode="json"))
    bundle = backend.artifact_bundle() if backend is not None else {}
    filenames = {"simulator_config": "simulator_config.json", "scenario_seed": "scenario_seed.json", "scenario_ground_truth": "scenario_ground_truth.json", "initial_world_state": "initial_world_state.json", "final_world_state": "final_world_state.json"}
    for key, filename in filenames.items():
        if key in bundle: dump(output / filename, bundle[key])
    if "visual_event_manifest" in bundle: dump(output / "visual_event_manifest.json", bundle["visual_event_manifest"])
    for key, filename in (("sensor_observations", "sensor_observations.jsonl"), ("route_trace", "route_trace.jsonl")):
        if key in bundle:
            with (output / filename).open("w", encoding="utf-8") as handle:
                for item in bundle[key]: handle.write(json.dumps(item, sort_keys=True) + "\n")
    for key, filename in (("baseline_observations", "baseline_observations.jsonl"), ("digital_twin_updates", "digital_twin_updates.jsonl")):
        if key in bundle:
            with (output / filename).open("w", encoding="utf-8") as handle:
                for item in bundle[key]: handle.write(json.dumps(item, sort_keys=True) + "\n")
    for key, filename, wrapper in (("detected_anomalies", "detected_anomalies.json", "anomalies"), ("digital_twin_before", "digital_twin_before.json", None), ("digital_twin_after", "digital_twin_after.json", None), ("actionable_alerts", "actionable_alerts.json", "alerts")):
        if key in bundle: dump(output / filename, {wrapper: bundle[key], "simulation_only": True} if wrapper else bundle[key])
    if bundle.get("monitoring_report") is not None:
        (output / "monitoring_report.md").write_text(bundle["monitoring_report"], encoding="utf-8")
    timestamp = datetime.now(UTC).isoformat()
    final = bundle.get("final_world_state", {})
    returned = any(item.skill == "return_to_start" and item.success and item.result.get("returned_to_start") for item in execution.step_results)
    summary = {"schema_version": "1.0", "run_id": run_id, "start_timestamp": execution.start_timestamp or timestamp, "end_timestamp": execution.end_timestamp or timestamp, "simulation_only": True, "physical_robot_validated": False, "verifier_version": "1.0", "executor_mode": getattr(backend, "name", "mock"), "backend_name": getattr(backend, "name", "mock"), "backend_version": getattr(backend, "version", "1.0"), "seed": bundle.get("scenario_seed", {}).get("seed"), "start_simulation_time": bundle.get("initial_world_state", {}).get("time"), "end_simulation_time": final.get("time"), "initial_battery": bundle.get("initial_world_state", {}).get("battery"), "final_battery": final.get("battery"), "rooms_visited": final.get("visit_history", []), "returned_to_start": returned, "verification_approved": report.approved, "baseline_completed": bool(bundle.get("baseline_observations")), "events_injected": [item["event_id"] for item in bundle.get("scenario_ground_truth", {}).get("events", []) if item.get("timestamp") is not None], "anomalies_detected": len(bundle.get("detected_anomalies", [])), "digital_twin_updated": any(item.get("updated") for item in bundle.get("digital_twin_updates", [])), "alerts_generated": len(bundle.get("actionable_alerts", [])), "stopped_safely": bool(final.get("stopped")), "report_generated": bundle.get("monitoring_report") is not None, "success": execution.success, "first_failure": execution.first_failure}
    dump(output / "demo_summary.json", summary)
    if bundle:
        dump(output / "monitoring_summary.json", {key: summary[key] for key in ("verification_approved", "baseline_completed", "rooms_visited", "events_injected", "anomalies_detected", "digital_twin_updated", "alerts_generated", "returned_to_start", "stopped_safely", "report_generated", "success", "first_failure", "simulation_only", "physical_robot_validated")})
    return output


def write_planning_run(root: Path, planning: PlanningResult, capabilities: dict[str, Any], report: VerificationReport | None = None, result: ExecutionResult | None = None, trace: list[ExecutionTrace] | None = None, backend: Any | None = None, *, scenario_input: str | None = None, scenario_plan: dict[str, Any] | None = None, scenario_report: dict[str, Any] | None = None, robot_feedback: dict[str, Any] | None = None, robot_feedback_markdown: str | None = None, run_request: dict[str, Any] | None = None, confirmation_preview: dict[str, Any] | None = None, resource_policy: dict[str, Any] | None = None, twin_history_diff: dict[str, Any] | None = None, twin_history: dict[str, Any] | None = None) -> Path:
    """Persist a planning decision; execution evidence is added only after approval."""
    candidate = planning.candidate_task
    report = report or VerificationReport(approved=False, safety_summary=["No candidate task was submitted to the verifier."])
    if candidate is not None:
        output = write_run(root, candidate, capabilities, report, result, trace, backend, write_unexecuted_result=False)
    else:
        output = Path(root) / f"{time.time_ns()}_nl-planning"
        output.mkdir(parents=True, exist_ok=False)
        dump = lambda path, value: path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        dump(output / "robot_capabilities.json", capabilities)
        dump(output / "verification_report.json", report.model_dump(mode="json"))
        dump(output / "demo_summary.json", {"schema_version": "1.0", "run_id": output.name, "simulation_only": True, "physical_robot_validated": False})
    dump = lambda path, value: path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dump(output / "natural_language_input.json", {"original_text": planning.original_text, "normalized_text": planning.normalized_text})
    dump(output / "planning_result.json", planning.model_dump(mode="json"))
    if scenario_input is not None: dump(output / "natural_language_scenario_input.json", {"original_text": scenario_input, "normalized_text": (scenario_plan or {}).get("normalized_text", "")})
    if scenario_plan is not None:
        dump(output / "scenario_planning_result.json", scenario_plan)
        dump(output / "candidate_scenario.json", scenario_plan.get("candidate_scenario"))
    if scenario_report is not None: dump(output / "scenario_verification_report.json", scenario_report)
    if robot_feedback is not None: dump(output / "robot_feedback.json", robot_feedback)
    if robot_feedback_markdown is not None: (output / "robot_feedback.md").write_text(robot_feedback_markdown, encoding="utf-8")
    if run_request is not None:
        snapshot = {**run_request, "run_id": output.name}
        dump(output / "run_request.json", snapshot)
    if confirmation_preview is not None: dump(output / "confirmation_preview.json", confirmation_preview)
    if resource_policy is not None: dump(output / "resource_policy.json", resource_policy)
    if twin_history_diff is not None: dump(output / "twin_history_diff.json", twin_history_diff)
    if twin_history is not None: dump(output / "twin_history.json", twin_history)
    if candidate is not None:
        dump(output / "candidate_task.json", candidate.model_dump(mode="json"))
        from .planner import normalized_task
        dump(output / "normalized_task.json", normalized_task(candidate))
    dump(output / "planner_trace.json", {"planner_name": "offline_house_sitter", "planner_version": "1.0", "deterministic": True, "status": planning.status, "match_basis": planning.match_basis, "automatic_addition_reasons": planning.automatic_addition_reasons})
    summary_path = output / "demo_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.update({"planner_name": "offline_house_sitter", "planner_version": "1.0", "deterministic": True, "original_text": planning.original_text, "planning_status": planning.status, "verification_approved": report.approved, "execution_attempted": result is not None, "execution_success": bool(result and result.success), "simulation_only": True, "physical_robot_validated": False})
    if run_request is not None: summary.update({key: value for key, value in snapshot.items() if key != "scenario_applied"})
    dump(summary_path, summary)
    return output
