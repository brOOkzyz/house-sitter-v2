from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from raptor_lite.artifacts import write_run
from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.executor import MockExecutor
from raptor_lite import issue_codes as codes
from raptor_lite.models import TaskSpec, VerificationReport
from raptor_lite.task_schema import load_task
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
EXAMPLES = ROOT / "examples/raptor_lite"


def valid_data() -> dict:
    return json.loads((EXAMPLES / "valid_house_sitter_task.json").read_text())


def test_registry_and_json_round_trip_are_strict():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    assert len(registry.capabilities) == 12
    task = load_task(EXAMPLES / "valid_house_sitter_task.json")
    assert TaskSpec.model_validate_json(task.model_dump_json()) == task


def test_valid_house_sitter_task_is_approved_and_executes_in_order():
    task = TaskSpec.model_validate(valid_data()); report = verify_task(task, CapabilityRegistry.from_yaml(PROFILE))
    assert report.approved and not report.issues
    result, trace = MockExecutor().run(task, report, CapabilityRegistry.from_yaml(PROFILE))
    assert result.success
    assert [item.skill for item in result.step_results] == [item.skill for item in task.steps]
    assert len(trace) == len(task.steps)


def test_invalid_examples_have_stable_issue_codes():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    expected = {
        "invalid_unsupported_skill.json": codes.UNKNOWN_SKILL,
        "invalid_parameter_type.json": codes.INVALID_PARAMETER_TYPE,
        "invalid_unsafe_parameter.json": codes.PARAMETER_OUT_OF_RANGE,
        "invalid_missing_return.json": codes.MISSING_SAFE_RETURN,
    }
    for name, code in expected.items():
        report = verify_task(load_task(EXAMPLES / name), registry)
        assert not report.approved
        assert code in [item.issue_code for item in report.issues]


def test_missing_parameter_timeout_and_physical_boundary_are_rejected():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    missing = valid_data(); missing["steps"][0]["parameters"] = {}
    report = verify_task(missing, registry)
    assert codes.MISSING_PARAMETER in [item.issue_code for item in report.issues]
    timeout = valid_data(); timeout["steps"][0].pop("timeout_seconds")
    report = verify_task(timeout, registry)
    assert codes.MISSING_TIMEOUT in [item.issue_code for item in report.issues]
    physical = valid_data(); physical["metadata"]["physical_robot_supported"] = True
    report = verify_task(physical, registry)
    assert codes.UNSUPPORTED_EXECUTION_MODE in [item.issue_code for item in report.issues]


def test_executor_refuses_unverified_task_and_artifacts_are_complete():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    task = load_task(EXAMPLES / "invalid_unsupported_skill.json"); report = verify_task(task, registry)
    try:
        MockExecutor().run(task, report, registry)
    except ValueError as exc:
        assert "refused" in str(exc)
    else: raise AssertionError("executor accepted a rejected task")
    try:
        MockExecutor().run(task, VerificationReport(approved=True), registry)
    except ValueError as exc:
        assert "refused" in str(exc)
    else: raise AssertionError("executor trusted a forged approval report")
    valid = load_task(EXAMPLES / "valid_house_sitter_task.json"); approved = verify_task(valid, registry)
    result, trace = MockExecutor().run(valid, approved, registry)
    with tempfile.TemporaryDirectory() as directory:
        output = write_run(Path(directory), valid, registry.as_json(), approved, result, trace)
        for name in ("input_task.json", "robot_capabilities.json", "verification_report.json", "resolved_task.json", "execution_trace.jsonl", "execution_result.json", "demo_summary.json"):
            assert (output / name).is_file()


def test_cli_validate_and_run_exit_codes():
    command = [sys.executable, "-m", "raptor_lite.cli"]
    valid = subprocess.run(command + ["validate", str(EXAMPLES / "valid_house_sitter_task.json"), "--profile", str(PROFILE)], cwd=ROOT, text=True, capture_output=True)
    invalid = subprocess.run(command + ["validate", str(EXAMPLES / "invalid_missing_return.json"), "--profile", str(PROFILE)], cwd=ROOT, text=True, capture_output=True)
    run = subprocess.run(command + ["run", str(EXAMPLES / "valid_house_sitter_task.json"), "--profile", str(PROFILE), "--executor", "mock"], cwd=ROOT, text=True, capture_output=True)
    assert valid.returncode == 0 and "approved" in valid.stdout
    assert invalid.returncode != 0 and codes.MISSING_SAFE_RETURN in invalid.stdout
    assert run.returncode == 0 and "Artifact directory:" in run.stdout
