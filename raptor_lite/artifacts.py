"""Persistent evidence for every verification and mock execution."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ExecutionResult, ExecutionTrace, TaskSpec, VerificationReport


def write_run(root: Path, task: TaskSpec, capabilities: dict[str, Any], report: VerificationReport, result: ExecutionResult | None = None, trace: list[ExecutionTrace] | None = None) -> Path:
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
    timestamp = datetime.now(UTC).isoformat()
    dump(output / "demo_summary.json", {"schema_version": "1.0", "run_id": run_id, "start_timestamp": timestamp, "end_timestamp": timestamp, "simulation_only": True, "verifier_version": "1.0", "executor_mode": "mock", "success": execution.success, "first_failure": execution.first_failure})
    return output
