from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from raptor_lite import issue_codes as codes
from raptor_lite.artifacts import write_run
from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.executor import BackendExecutor, MockExecutor
from raptor_lite.house2d import House2DBackend
from raptor_lite.house_sitter import HouseSitterApplication
from raptor_lite.models import TaskSpec
from raptor_lite.task_schema import load_task
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
TASK_PATH = ROOT / "examples/raptor_lite/complete_house_sitter_demo.json"


def task_data() -> dict:
    return json.loads(TASK_PATH.read_text())


def run(events: list[str], seed: int = 12345, alert_types: dict[str, str] | None = None):
    data = task_data()
    for step in data["steps"]:
        if step["skill"] == "generate_alert" and alert_types and step["parameters"]["room"] in alert_types:
            step["parameters"]["anomaly_type"] = alert_types[step["parameters"]["room"]]
    task = TaskSpec.model_validate(data); registry = CapabilityRegistry.from_yaml(PROFILE); report = verify_task(task, registry); assert report.approved
    backend = House2DBackend(seed=seed, events=events); result, trace = BackendExecutor(backend).run(task, report, registry)
    return task, registry, report, backend, result, trace


def types(backend: House2DBackend) -> set[str]:
    return {item["anomaly_type"] for item in backend.artifact_bundle()["detected_anomalies"]}


def test_normal_patrol_has_no_false_positive_and_returns_with_report():
    _, _, _, backend, result, _ = run([])
    bundle = backend.artifact_bundle(); final = bundle["final_world_state"]
    assert result.success and not bundle["detected_anomalies"] and not bundle["actionable_alerts"]
    assert final["room"] == "charging_area" and final["stopped"] and bundle["monitoring_report"]


def test_single_and_dual_events_are_detected_from_observations():
    assert types(run(["unexpected_obstacle"])[3]) == {"unexpected_obstacle"}
    assert types(run(["high_temperature"], alert_types={"kitchen": "high_temperature"})[3]) == {"high_temperature"}
    assert types(run(["high_humidity"])[3]) == {"high_humidity"}
    task, registry, report, backend, result, trace = run(["unexpected_obstacle", "high_humidity"])
    assert result.success and types(backend) == {"unexpected_obstacle", "high_humidity"}
    bundle = backend.artifact_bundle(); updates = {item["room"]: item for item in bundle["digital_twin_updates"]}
    assert updates["kitchen"]["updated"] and updates["bathroom"]["updated"]
    assert "layout_object_state" in updates["kitchen"]["changed_fields"] and "humidity_state" in updates["bathroom"]["changed_fields"]
    for alert in bundle["actionable_alerts"]:
        assert alert["related_digital_twin_revision"] == bundle["digital_twin_after"]["rooms"][alert["room"]]["revision"]
    assert "Overall success: True" in bundle["monitoring_report"]
    assert "Successful observations: 6" in bundle["monitoring_report"]
    assert "Failed observations: 0" in bundle["monitoring_report"]
    assert "kitchen: unexpected_obstacle" in bundle["monitoring_report"]
    assert "bathroom: high_humidity" in bundle["monitoring_report"]
    with tempfile.TemporaryDirectory() as directory:
        output = write_run(Path(directory), task, registry.as_json(), report, result, trace, backend)
        for name in ("baseline_observations.jsonl", "detected_anomalies.json", "digital_twin_before.json", "digital_twin_after.json", "digital_twin_updates.jsonl", "actionable_alerts.json", "monitoring_summary.json", "monitoring_report.md"):
            assert (output / name).is_file()
        summary = json.loads((output / "monitoring_summary.json").read_text())
        assert summary["anomalies_detected"] == 2 and "unexpected_obstacle" not in (output / "demo_summary.json").read_text()


def test_blocked_transition_is_observed_without_reading_event_type():
    task = load_task(TASK_PATH); backend = House2DBackend(seed=12, events=["blocked_transition"]); backend.initialize(task)
    backend.execute("move_to_room", {"room": "living_room"}, 30); backend.execute("inspect_room", {"room": "living_room"}, 20)
    backend.execute("establish_household_baseline", {"room": "living_room"}, 10); backend.execute("inject_household_events", {}, 5)
    backend.execute("inspect_room", {"room": "living_room"}, 20)
    result = backend.execute("detect_environment_change", {"room": "living_room"}, 15)
    assert {item["anomaly_type"] for item in result["anomalies"]} == {"blocked_transition"}


def test_noise_is_bounded_and_transient_false_reading_is_one_observation_only():
    task = load_task(TASK_PATH); backend = House2DBackend(seed=9, events=["transient_false_reading"], sensor_noise_bound=0.2); backend.initialize(task)
    backend.execute("move_to_room", {"room": "kitchen"}, 30); baseline = backend.execute("inspect_room", {"room": "kitchen"}, 20)
    backend.execute("inject_household_events", {}, 5); first = backend.execute("inspect_room", {"room": "kitchen"}, 20); second = backend.execute("inspect_room", {"room": "kitchen"}, 20)
    assert first["temperature_c"] > baseline["temperature_c"] + 10.0 and abs(second["temperature_c"] - baseline["temperature_c"]) <= 0.4


def test_dropout_is_missing_observation_without_twin_update_and_runtime_detector_has_no_ground_truth_input():
    _, _, _, backend, result, _ = run(["observation_dropout"], alert_types={"kitchen": "missing_observation"})
    assert result.success and types(backend) == {"missing_observation"}
    updates = backend.artifact_bundle()["digital_twin_updates"]
    kitchen_step = next(item for item in result.step_results if item.step_id == "update-kitchen")
    assert not any(item["room"] == "kitchen" for item in updates) and kitchen_step.result["updated"] is False
    assert "Failed observations: 1" in backend.artifact_bundle()["monitoring_report"]
    task = load_task(TASK_PATH); sensor = House2DBackend(seed=21, events=["unexpected_obstacle"]); sensor.initialize(task)
    sensor.execute("move_to_room", {"room": "kitchen"}, 30); baseline = sensor.execute("inspect_room", {"room": "kitchen"}, 20)
    sensor.execute("inject_household_events", {}, 5); changed = sensor.execute("inspect_room", {"room": "kitchen"}, 20)
    application = HouseSitterApplication("isolated", 21); application.observe(baseline, baseline=True); application.observe(changed)
    assert {item["anomaly_type"] for item in application.detect("kitchen")} == {"unexpected_obstacle"}


def test_blocked_transition_and_low_battery_fail_safely():
    _, _, _, blocked, result, _ = run(["blocked_transition"])
    assert not result.success and "No legal route" in (result.first_failure or "") and blocked.artifact_bundle()["final_world_state"]["stopped"]
    _, _, _, low, result, _ = run([], seed=1)
    low_battery = House2DBackend(seed=1, events=["low_initial_battery"]); task = load_task(TASK_PATH); registry = CapabilityRegistry.from_yaml(PROFILE); report = verify_task(task, registry)
    result, _ = BackendExecutor(low_battery).run(task, report, registry)
    assert not result.success and "insufficient" in (result.first_failure or "") and low_battery.artifact_bundle()["final_world_state"]["stopped"]
    _, _, _, mismatch, result, _ = run(["high_temperature"], alert_types={"kitchen": "unexpected_obstacle"})
    assert not result.success and "must reference a detected anomaly type" in (result.first_failure or "") and mismatch.artifact_bundle()["final_world_state"]["stopped"]


def test_workflow_verifier_rejects_missing_baseline_and_wrong_detection_order():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    missing = task_data(); missing["steps"] = [step for step in missing["steps"] if step["step_id"] != "baseline-save-bathroom"]
    assert codes.BASELINE_REQUIRED in [item.issue_code for item in verify_task(missing, registry).issues]
    wrong = task_data(); detect = next(step for step in wrong["steps"] if step["step_id"] == "detect-kitchen"); wrong["steps"].remove(detect); wrong["steps"].insert(13, detect)
    assert codes.OBSERVATION_REQUIRED in [item.issue_code for item in verify_task(wrong, registry).issues]
    alert_first = task_data(); alert = next(step for step in alert_first["steps"] if step["step_id"] == "alert-kitchen"); update = next(step for step in alert_first["steps"] if step["step_id"] == "update-kitchen")
    alert_first["steps"].remove(alert); alert_first["steps"].insert(alert_first["steps"].index(update), alert)
    assert codes.TWIN_UPDATE_REQUIRED in [item.issue_code for item in verify_task(alert_first, registry).issues]


def test_report_cannot_mark_a_continued_failure_as_success():
    task = load_task(TASK_PATH); backend = House2DBackend(seed=4); backend.initialize(task); backend.record_failure("simulated continued move failure"); backend.emergency_stop()
    report = backend.execute("generate_monitoring_report", {}, 10)["markdown"]
    assert "Overall success: False" in report


def test_seed_reproducibility_json_boundaries_and_cli():
    first, second = run(["unexpected_obstacle", "high_humidity"]), run(["unexpected_obstacle", "high_humidity"])
    assert first[3].artifact_bundle()["scenario_ground_truth"] == second[3].artifact_bundle()["scenario_ground_truth"]
    assert first[3].artifact_bundle()["route_trace"] == second[3].artifact_bundle()["route_trace"]
    assert first[3].artifact_bundle()["detected_anomalies"] == second[3].artifact_bundle()["detected_anomalies"]
    assert first[3].artifact_bundle()["scenario_ground_truth"] != run(["unexpected_obstacle", "high_humidity"], seed=54321)[3].artifact_bundle()["scenario_ground_truth"]
    command = [sys.executable, "-m", "raptor_lite.cli", "run", str(TASK_PATH), "--profile", str(PROFILE), "--backend", "house2d", "--seed", "12345"]
    cli = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert cli.returncode == 0 and "Detected changes: 2" in cli.stdout and "Monitoring report: generated" in cli.stdout


def test_complete_house_sitter_task_remains_mock_compatible():
    task = load_task(TASK_PATH); registry = CapabilityRegistry.from_yaml(PROFILE); report = verify_task(task, registry)
    result, _ = MockExecutor().run(task, report, registry)
    assert result.success and result.step_results[-1].skill == "generate_monitoring_report"
