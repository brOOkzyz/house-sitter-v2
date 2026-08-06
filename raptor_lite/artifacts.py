"""Persistent evidence for every verification and mock execution."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ExecutionResult, ExecutionTrace, TaskSpec, VerificationReport


def write_run(root: Path, task: TaskSpec, capabilities: dict[str, Any], report: VerificationReport, result: ExecutionResult | None = None, trace: list[ExecutionTrace] | None = None, backend: Any | None = None) -> Path:
    run_id = f"{time.time_ns()}_{task.task_id}"
    output = Path(root) / run_id
    output.mkdir(parents=True, exist_ok=False)
    dump = lambda path, value: path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    dump(output / "input_task.json", task.model_dump(mode="json")); dump(output / "robot_capabilities.json", capabilities)
    dump(output / "verification_report.json", report.model_dump(mode="json")); dump(output / "resolved_task.json", task.model_dump(mode="json"))
    with (output / "execution_trace.jsonl").open("w", encoding="utf-8") as handle:
        for item in trace or []: handle.write(item.model_dump_json() + "\n")
    execution = result or ExecutionResult(success=False, first_failure="Not executed.")
    dump(output / "execution_result.json", execution.model_dump(mode="json"))
    bundle = backend.artifact_bundle() if backend is not None else {}
    filenames = {"simulator_config": "simulator_config.json", "scenario_seed": "scenario_seed.json", "scenario_ground_truth": "scenario_ground_truth.json", "initial_world_state": "initial_world_state.json", "final_world_state": "final_world_state.json"}
    for key, filename in filenames.items():
        if key in bundle: dump(output / filename, bundle[key])
    for key, filename in (("sensor_observations", "sensor_observations.jsonl"), ("route_trace", "route_trace.jsonl")):
        if key in bundle:
            with (output / filename).open("w", encoding="utf-8") as handle:
                for item in bundle[key]: handle.write(json.dumps(item, sort_keys=True) + "\n")
    timestamp = datetime.now(UTC).isoformat()
    final = bundle.get("final_world_state", {})
    returned = any(item.skill == "return_to_start" and item.success and item.result.get("returned_to_start") for item in execution.step_results)
    dump(output / "demo_summary.json", {"schema_version": "1.0", "run_id": run_id, "start_timestamp": execution.start_timestamp or timestamp, "end_timestamp": execution.end_timestamp or timestamp, "simulation_only": True, "physical_robot_validated": False, "verifier_version": "1.0", "executor_mode": getattr(backend, "name", "mock"), "backend_name": getattr(backend, "name", "mock"), "backend_version": getattr(backend, "version", "1.0"), "seed": bundle.get("scenario_seed", {}).get("seed"), "start_simulation_time": bundle.get("initial_world_state", {}).get("time"), "end_simulation_time": final.get("time"), "initial_battery": bundle.get("initial_world_state", {}).get("battery"), "final_battery": final.get("battery"), "rooms_visited": final.get("visit_history", []), "returned_to_start": returned, "success": execution.success, "first_failure": execution.first_failure})
    return output
