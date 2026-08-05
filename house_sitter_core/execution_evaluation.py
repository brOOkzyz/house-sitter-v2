"""Deterministic, offline evaluation of simulation-only execution artifacts."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .skill_execution_bridge import NAVIGATION_ACTIONS


REQUIRED_EXECUTION_ARTIFACTS = (
    "execution_request.json",
    "execution_plan.json",
    "execution_events.jsonl",
    "execution_result.json",
)
OUTPUT_ARTIFACTS = ("execution_trials.csv", "execution_summary.json", "execution_summary.md")
STATUSES = frozenset({"succeeded", "failed", "timed_out", "cancelled"})
EVENT_STATUSES = frozenset({"pending", "running", "feedback", "succeeded", "failed", "timed_out", "cancelled"})
TRIAL_FIELDS = (
    "trial_index", "request_id", "skill_name", "overall_status", "goal_count",
    "succeeded_steps", "timed_out_steps", "failed_steps", "cancelled_steps",
    "total_duration_seconds", "feedback_count", "recovery_count", "timeout_policy",
    "effective_timeout_seconds", "timeout_basis", "failure_reason", "step_durations_seconds",
)


class ExecutionEvaluationError(ValueError):
    """Raised when execution evidence is missing, malformed, or outside scope."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionEvaluationError(f"invalid JSON artifact: {path}") from exc
    if not isinstance(value, dict):
        raise ExecutionEvaluationError(f"JSON artifact must be an object: {path}")
    return value


def _load_events(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ExecutionEvaluationError(f"cannot read event artifact: {path}") from exc
    events: list[dict[str, Any]] = []
    for index, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ExecutionEvaluationError(f"invalid JSONL event at {path}:{index}") from exc
        if not isinstance(event, dict):
            raise ExecutionEvaluationError(f"event at {path}:{index} must be an object")
        events.append(event)
    return events


def _require_simulation_only(document: dict[str, Any], name: str) -> None:
    if (document.get("simulation_only") is not True or document.get("review_only") is not True
            or document.get("real_robot_supported") is not False):
        raise ExecutionEvaluationError(f"{name} is not a trusted simulation-only artifact.")


def _finite_number(value: Any, name: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ExecutionEvaluationError(f"{name} must be a finite number no smaller than {minimum}.")
    return float(value)


def _plan_steps(plan: dict[str, Any]) -> dict[int, dict[str, str | int]]:
    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ExecutionEvaluationError("execution plan steps are incomplete.")
    indexed: dict[int, dict[str, str | int]] = {}
    for step in steps:
        if not isinstance(step, dict):
            raise ExecutionEvaluationError("execution plan step must be an object.")
        order, label, action_type = step.get("step_order"), step.get("label"), step.get("action_type")
        if (isinstance(order, bool) or not isinstance(order, int) or order <= 0
                or not isinstance(label, str) or not label or not isinstance(action_type, str) or not action_type):
            raise ExecutionEvaluationError("execution plan step is malformed.")
        if order in indexed:
            raise ExecutionEvaluationError("execution plan has duplicate step_order values.")
        indexed[order] = {"step_order": order, "label": label, "action_type": action_type}
    if list(indexed) != sorted(indexed):
        raise ExecutionEvaluationError("execution plan steps must have deterministic order.")
    return indexed


def _validate_events(events: list[dict[str, Any]], plan_steps: dict[int, dict[str, str | int]]) -> tuple[int, int, dict[int, float]]:
    feedback_count = 0
    recovery_by_step: dict[int, int] = {}
    duration_by_step: dict[int, float] = {}
    for expected_order, event in enumerate(events, 1):
        _require_simulation_only(event, f"event {expected_order}")
        if event.get("logical_event_order") != expected_order:
            raise ExecutionEvaluationError("event logical_event_order must be contiguous and ordered.")
        step_order = event.get("step_order")
        if isinstance(step_order, bool) or not isinstance(step_order, int) or step_order <= 0:
            raise ExecutionEvaluationError("event step_order must be a positive integer.")
        expected = plan_steps.get(step_order)
        if expected is None or event.get("status") not in EVENT_STATUSES or event.get("action_type") != expected["action_type"]:
            raise ExecutionEvaluationError("event does not match a valid execution step.")
        # Existing bridge artifacts derive this identity from step_order and do
        # not serialise label in every event. If it is present, it is never
        # trusted over the canonical plan label.
        if "label" in event and event["label"] != expected["label"]:
            raise ExecutionEvaluationError("event label does not match its execution plan step.")
        if event.get("status") != "feedback":
            continue
        feedback = event.get("feedback")
        if not isinstance(feedback, dict):
            raise ExecutionEvaluationError("feedback event must contain a normalized object.")
        feedback_count += 1
        if "number_of_recoveries" in feedback:
            recoveries = _finite_number(feedback["number_of_recoveries"], "number_of_recoveries")
            if not recoveries.is_integer():
                raise ExecutionEvaluationError("number_of_recoveries must be an integer.")
            recovery_by_step[step_order] = max(recovery_by_step.get(step_order, 0), int(recoveries))
        if "navigation_time_seconds" in feedback:
            duration_by_step[step_order] = _finite_number(feedback["navigation_time_seconds"], "navigation_time_seconds")
    return feedback_count, sum(recovery_by_step.values()), duration_by_step


def _evaluate_directory(directory: Path) -> dict[str, Any]:
    source = Path(directory)
    if not source.is_dir():
        raise ExecutionEvaluationError(f"execution artifact directory is missing: {source}")
    missing = [name for name in REQUIRED_EXECUTION_ARTIFACTS if not (source / name).is_file()]
    if missing:
        raise ExecutionEvaluationError(f"execution artifact directory is incomplete: {source} ({', '.join(missing)})")
    request = _load_json(source / "execution_request.json")
    plan_document = _load_json(source / "execution_plan.json")
    result = _load_json(source / "execution_result.json")
    events = _load_events(source / "execution_events.jsonl")
    for name, document in (("execution request", request), ("execution plan", plan_document), ("execution result", result)):
        _require_simulation_only(document, name)
    plan = plan_document.get("skill_plan")
    if not isinstance(plan, dict):
        raise ExecutionEvaluationError("execution plan lacks skill_plan.")
    plan_steps = _plan_steps(plan)
    skill_name = request.get("skill_name")
    request_id = request.get("request_id")
    if (not isinstance(skill_name, str) or not skill_name or not isinstance(request_id, str) or not request_id
            or plan.get("skill_name") != skill_name or result.get("skill_name") != skill_name
            or plan.get("request_id") != request_id or result.get("request_id") != request_id):
        raise ExecutionEvaluationError("execution request, plan, and result identities must match.")
    if result.get("overall_status") not in STATUSES | {None}:
        raise ExecutionEvaluationError("execution result has an invalid overall_status.")
    if result.get("timeout_policy") not in {"explicit", "adaptive"}:
        raise ExecutionEvaluationError("execution result has an invalid timeout_policy.")
    timeout = _finite_number(result.get("effective_timeout_seconds"), "effective_timeout_seconds", minimum=0.0)
    if not isinstance(result.get("timeout_basis"), str) or not result["timeout_basis"]:
        raise ExecutionEvaluationError("execution result has an invalid timeout_basis.")
    steps = result.get("steps")
    if not isinstance(steps, list) or result.get("total_steps") != len(steps) or len(steps) != len(plan_steps):
        raise ExecutionEvaluationError("execution result steps are incomplete.")
    orders: list[int] = []
    counts = {status: 0 for status in STATUSES}
    for step in steps:
        if not isinstance(step, dict):
            raise ExecutionEvaluationError("execution result step must be an object.")
        order, status = step.get("step_order"), step.get("status")
        expected = plan_steps.get(order)
        if (isinstance(order, bool) or not isinstance(order, int) or order <= 0
                or expected is None or step.get("label") != expected["label"]
                or step.get("action_type") != expected["action_type"]):
            raise ExecutionEvaluationError("execution result step is malformed.")
        if status not in STATUSES:
            raise ExecutionEvaluationError("execution result step has an invalid status.")
        orders.append(order); counts[status] += 1
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        raise ExecutionEvaluationError("execution result steps must have unique deterministic order.")
    if any(result.get(f"{status}_steps") != counts[status] for status in STATUSES):
        raise ExecutionEvaluationError("execution result status counts are inconsistent.")
    if set(orders) != set(plan_steps):
        raise ExecutionEvaluationError("execution result steps do not match the execution plan.")
    feedback_count, recovery_count, duration_by_step = _validate_events(events, plan_steps)
    running_navigation_steps = {
        event["step_order"] for event in events
        if event.get("status") == "running" and event.get("action_type") in NAVIGATION_ACTIONS
    }
    step_durations = [
        {"step_order": step["step_order"], "label": step["label"], "duration_seconds": duration_by_step.get(step["step_order"])}
        for step in steps
    ]
    known_durations = [entry["duration_seconds"] for entry in step_durations if entry["duration_seconds"] is not None]
    failure_reason = result.get("terminal_reason") if result.get("overall_status") != "succeeded" else None
    if failure_reason is not None and not isinstance(failure_reason, str):
        raise ExecutionEvaluationError("execution result terminal_reason must be a string or null.")
    return {
        "request_id": request_id, "skill_name": skill_name, "overall_status": result["overall_status"],
        "goal_count": len(running_navigation_steps), "succeeded_steps": counts["succeeded"],
        "timed_out_steps": counts["timed_out"], "failed_steps": counts["failed"],
        "cancelled_steps": counts["cancelled"], "total_duration_seconds": sum(known_durations) if known_durations else None,
        "feedback_count": feedback_count, "recovery_count": recovery_count,
        "timeout_policy": result["timeout_policy"], "effective_timeout_seconds": timeout,
        "timeout_basis": result["timeout_basis"], "failure_reason": failure_reason,
        "step_durations": step_durations,
    }


def _csv(rows: list[dict[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=TRIAL_FIELDS, lineterminator="\n", extrasaction="raise")
    writer.writeheader()
    for row in rows:
        durations = row.pop("step_durations")
        writer.writerow({**row, "step_durations_seconds": json.dumps(durations, sort_keys=True, separators=(",", ":"))})
    return stream.getvalue()


def _report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Simulation-only Execution Evaluation", "", "This project is simulation-only and does not support real-robot deployment.",
        "The evaluator reads completed Gazebo/Nav2 execution artifacts offline; it does not start ROS, Gazebo, Nav2, or a command path.", "",
        "| trial | skill | status | goals | succeeded | timed out | failed | cancelled | feedback | recoveries | duration (s) |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        duration = "" if row["total_duration_seconds"] is None else str(row["total_duration_seconds"])
        lines.append(f"| {row['trial_index']} | {row['skill_name']} | {row['overall_status']} | {row['goal_count']} | {row['succeeded_steps']} | {row['timed_out_steps']} | {row['failed_steps']} | {row['cancelled_steps']} | {row['feedback_count']} | {row['recovery_count']} | {duration} |")
    return "\n".join(lines) + "\n"


def evaluate_execution_artifacts(artifact_directories: list[Path]) -> dict[str, str]:
    """Return deterministic, offline reports for one or more trusted execution directories."""
    if not artifact_directories:
        raise ExecutionEvaluationError("at least one execution artifact directory is required.")
    rows = [_evaluate_directory(path) for path in sorted((Path(path) for path in artifact_directories), key=lambda path: path.as_posix())]
    for index, row in enumerate(rows, 1):
        row["trial_index"] = index
    summary_rows = [{key: value for key, value in row.items() if key != "trial_index"} for row in rows]
    summary = {
        "schema_version": "1.0", "simulation_only": True, "review_only": True,
        "real_robot_supported": False, "executable": False, "trial_count": len(rows),
        "status_counts": {status: sum(row["overall_status"] == status for row in rows) for status in sorted(STATUSES)},
        "total_goals": sum(row["goal_count"] for row in rows),
        "total_feedback": sum(row["feedback_count"] for row in rows),
        "total_recoveries": sum(row["recovery_count"] for row in rows), "trials": summary_rows,
    }
    return {
        "execution_trials.csv": _csv([dict(row) for row in rows]),
        "execution_summary.json": json.dumps(summary, indent=2, sort_keys=True) + "\n",
        "execution_summary.md": _report(rows),
    }


def write_execution_evaluation(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    """Atomically publish a complete evaluation; existing outputs always fail closed."""
    if set(contents) != set(OUTPUT_ARTIFACTS):
        raise ExecutionEvaluationError("evaluation output set is incomplete or contains extra files.")
    output = Path(output_dir)
    if output.exists():
        raise ExecutionEvaluationError(f"evaluation output directory already exists: {output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    primary: BaseException | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        for name in OUTPUT_ARTIFACTS:
            (Path(temporary.name) / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary.name, output)
    except BaseException as exc:
        primary = exc
        raise
    finally:
        if temporary is not None:
            try:
                temporary.cleanup()
            except BaseException as cleanup:
                if primary is not None:
                    primary.add_note(f"temporary cleanup failed: {type(cleanup).__name__}: {cleanup}")
                else:
                    raise
    return {name: output / name for name in OUTPUT_ARTIFACTS}
