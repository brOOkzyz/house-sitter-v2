from __future__ import annotations

from copy import deepcopy
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replay_module():
    spec = importlib.util.spec_from_file_location("phase6_replay", ROOT / "scripts/phase6_replay.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def sample() -> dict:
    return {"seed": 7, "condition": "full_system", "scenario_input": {"events": [{"room": "kitchen", "event_type": "high_temperature"}]}, "task_spec": {"task_id": "t"}, "verifier_decision": {"approved": True}, "resource_policy_decision": {"decision": "APPROVE"}, "execution_guard": "standard_verified_execution", "execution_result": {"success": True, "step_results": [{"step_id": "move", "success": True}], "first_failure": None, "start_timestamp": "2026-01-01T00:00:00Z", "end_timestamp": "2026-01-01T00:00:01Z"}, "trace": [{"timestamp": "sim:5.000", "event": "step_completed"}], "ground_truth": {"seed": 7, "events": []}, "observations": [{"room": "kitchen", "timestamp": 5.0}], "route": [{"rooms": ["charging_area", "kitchen"], "path_length": 7.0}], "detections": [{"room": "kitchen", "anomaly_type": "high_temperature"}], "twin": {"diff": [{"room": "kitchen", "updated": True}]}}


def test_replay_ignores_only_wall_clock_execution_timestamps():
    module = replay_module(); first, second = sample(), sample()
    second["execution_result"]["start_timestamp"] = "2026-01-02T00:00:00Z"
    second["execution_result"]["end_timestamp"] = "2026-01-02T00:00:01Z"
    result = module.compare_replays(first, second)
    assert result["scientifically_reproducible"]
    assert result["allowed_volatile_difference_paths"] == ["$.execution_result.end_timestamp", "$.execution_result.start_timestamp"]
    assert result["preserved_volatile_provenance"]["first"]["start_timestamp"] != result["preserved_volatile_provenance"]["second"]["start_timestamp"]


def test_replay_rejects_each_material_scientific_change():
    module = replay_module()
    for path, value in (("route", [{"rooms": ["charging_area", "bedroom"], "path_length": 9.0}]), ("observations", [{"room": "kitchen", "timestamp": 6.0}]), ("detections", []), ("execution_result", {"success": False, "step_results": [], "first_failure": "failure", "start_timestamp": "a", "end_timestamp": "b"})):
        first, second = sample(), deepcopy(sample())
        second[path] = value
        result = module.compare_replays(first, second)
        assert not result["scientifically_reproducible"], path
        assert result["scientific_difference_paths"], path


def test_replay_refuses_to_hide_unclassified_volatile_metadata():
    module = replay_module(); run = sample()
    run["execution_result"]["artifact_path"] = "/tmp/not-science"
    try:
        module.scientific_payload(run)
    except ValueError as error:
        assert "Unclassified volatile" in str(error)
    else:
        raise AssertionError("unclassified volatile metadata must fail closed")
