from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from raptor_lite.artifacts import write_run
from raptor_lite.backends import BackendError
from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.executor import BackendExecutor, MockExecutor
from raptor_lite.house2d import House2DBackend
from raptor_lite.task_schema import load_task
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
TASK = ROOT / "examples/raptor_lite/valid_house_sitter_task.json"


def approved_task():
    task = load_task(TASK); report = verify_task(task, CapabilityRegistry.from_yaml(PROFILE)); assert report.approved
    return task, report


def test_seeded_reset_and_world_variation_are_reproducible():
    task, _ = approved_task()
    first, second = House2DBackend(seed=12345), House2DBackend(seed=12345)
    first.initialize(task); second.initialize(task)
    assert first.artifact_bundle()["scenario_ground_truth"] == second.artifact_bundle()["scenario_ground_truth"]
    different = House2DBackend(seed=54321); different.initialize(task)
    assert first.artifact_bundle()["scenario_ground_truth"]["rooms"] != different.artifact_bundle()["scenario_ground_truth"]["rooms"]


def test_legal_routes_consume_time_battery_and_return_to_start():
    task, report = approved_task(); backend = House2DBackend(seed=12345)
    result, _ = BackendExecutor(backend).run(task, report, CapabilityRegistry.from_yaml(PROFILE))
    final = backend.artifact_bundle()["final_world_state"]
    assert result.success and final["room"] == "charging_area" and final["stopped"]
    assert final["battery"] < backend.artifact_bundle()["initial_world_state"]["battery"]
    assert [row["rooms"] for row in backend.artifact_bundle()["route_trace"]][:2] == [["charging_area", "living_room"], ["living_room", "kitchen"]]


def test_blocked_and_low_battery_fail_closed_and_stop_is_bounded():
    task, report = approved_task()
    registry = CapabilityRegistry.from_yaml(PROFILE)
    blocked = House2DBackend(seed=1, events=["blocked_transition"]); blocked.initialize(task); blocked.execute("inject_household_events", {}, 5)
    try: blocked.execute("move_to_room", {"room": "living_room"}, 30); blocked.execute("move_to_room", {"room": "kitchen"}, 30)
    except BackendError as exc: assert "No legal route" in str(exc)
    else: raise AssertionError("blocked transition allowed a route")
    result, _ = BackendExecutor(House2DBackend(seed=1, events=["low_initial_battery"])).run(task, report, registry)
    assert not result.success and "insufficient" in (result.first_failure or "")
    backend = House2DBackend(seed=1); backend.initialize(task); assert backend.emergency_stop()["stopped"]
    try: backend.execute("move_to_room", {"room": "living_room"}, 30)
    except BackendError as exc: assert "stopped" in str(exc)
    else: raise AssertionError("emergency stop allowed movement")


def test_timeout_and_observations_keep_ground_truth_separate():
    task, _ = approved_task(); backend = House2DBackend(seed=3, events=["unexpected_obstacle", "high_temperature", "high_humidity", "observation_dropout"]); backend.initialize(task)
    try: backend.execute("move_to_room", {"room": "kitchen"}, 5)
    except BackendError as exc: assert "exceeding" in str(exc)
    else: raise AssertionError("short movement timeout was accepted")
    backend.execute("inject_household_events", {}, 5); backend.execute("move_to_room", {"room": "kitchen"}, 30)
    observation = backend.execute("inspect_room", {"room": "kitchen"}, 20)
    assert observation["synthetic"] and observation["simulated_onboard_sensor"] and observation["simulation_only"]
    assert observation["physical_robot_validated"] is False and "events" not in observation and observation["observation_valid"] is False
    assert all("observation_dropout" not in event_id for event_id in observation["active_event_identifiers"])
    kitchen_truth = backend.artifact_bundle()["scenario_ground_truth"]["rooms"]["kitchen"]
    assert "static_objects" in kitchen_truth and "static_objects" not in observation
    backend = House2DBackend(seed=3, events=["unexpected_obstacle", "high_temperature", "high_humidity"]); backend.initialize(task); backend.execute("inject_household_events", {}, 5)
    backend.execute("move_to_room", {"room": "kitchen"}, 30); kitchen = backend.execute("inspect_room", {"room": "kitchen"}, 20)
    assert kitchen["observation_valid"] and kitchen["obstacle_present"] and kitchen["temperature_c"] == 75.0
    backend.execute("move_to_room", {"room": "bathroom"}, 30); bathroom = backend.execute("inspect_room", {"room": "bathroom"}, 20)
    assert bathroom["humidity_percent"] == 92.0


def test_artifacts_mock_compatibility_and_headless_cli():
    task, report = approved_task(); backend = House2DBackend(seed=12345, events=["unexpected_obstacle"])
    registry = CapabilityRegistry.from_yaml(PROFILE)
    result, trace = BackendExecutor(backend).run(task, report, registry)
    with tempfile.TemporaryDirectory() as directory:
        output = write_run(Path(directory), task, CapabilityRegistry.from_yaml(PROFILE).as_json(), report, result, trace, backend)
        for name in ("simulator_config.json", "scenario_seed.json", "scenario_ground_truth.json", "initial_world_state.json", "final_world_state.json", "sensor_observations.jsonl", "route_trace.jsonl", "execution_result.json", "demo_summary.json"):
            assert (output / name).is_file()
        assert json.loads((output / "demo_summary.json").read_text())["backend_name"] == "house2d"
    mock_result, _ = MockExecutor().run(task, report, registry); assert mock_result.success
    command = [sys.executable, "-m", "raptor_lite.cli", "run", str(TASK), "--profile", str(PROFILE), "--backend", "house2d", "--seed", "12345"]
    run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 0 and "Scenario seed: 12345" in run.stdout and "Artifact directory:" in run.stdout
