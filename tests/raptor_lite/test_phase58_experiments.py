from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from raptor_lite.experiment import ExperimentConfig, ExperimentRunner, load_experiment_config


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
DEFAULT = ROOT / "configs/raptor_lite/default_experiment.yaml"


def config(output: Path, **changes: object) -> ExperimentConfig:
    document = {"seed": 41, "task_text": "Patrol the whole house and report anything unusual.", "scenario_distribution": [{"name": "events", "text": "There is a box in the bedroom and the bathroom has high humidity.", "weight": 1.0}], "output_path": str(output)}
    document.update(changes)
    return ExperimentConfig.model_validate(document)


def test_versioned_experiment_config_loads_hashes_and_rejects_ordinary_ablations(tmp_path):
    loaded = load_experiment_config(DEFAULT)
    assert loaded.config_hash() == load_experiment_config(DEFAULT).config_hash() and not loaded.ablations.requested()
    with pytest.raises(ValidationError, match="experiment_mode"):
        config(tmp_path, ablations={"disable_verifier": True})


def test_manifest_is_complete_and_reproducible_for_identical_config_and_seed(tmp_path):
    reproducible_config = config(tmp_path / "runs")
    first = ExperimentRunner(PROFILE).run(reproducible_config)
    second = ExperimentRunner(PROFILE).run(reproducible_config)
    manifest = first["manifest"]
    for key in ("run_id", "timestamp", "git_commit", "config_hash", "seed", "inputs", "task_spec", "capability_profile", "verifier_decision", "resource_policy_decision", "ground_truth", "observations", "route", "detections", "twin", "final_outcome", "reproducibility_hash"):
        assert key in manifest
    assert manifest["simulation_only"] and not manifest["physical_robot_validated"]
    assert manifest["final_outcome"]["success"] and manifest["ground_truth"]["events"]
    assert manifest["reproducibility_hash"] == second["manifest"]["reproducibility_hash"]
    assert manifest["observations"] == second["manifest"]["observations"]
    assert manifest["detections"] == second["manifest"]["detections"]
    artifact = Path(first["artifact_directory"])
    assert json.loads((artifact / "experiment_manifest.json").read_text()) == manifest


def test_ablation_hooks_are_explicit_and_keep_safety_gates_non_executing(tmp_path):
    route = config(tmp_path / "route", experiment_mode=True, ablations={"disable_route_optimization": True})
    routed = ExperimentRunner(PROFILE).run(route)["manifest"]
    assert routed["planning"]["candidate_task"]["metadata"]["visit_order_source"] == "input_order_ablation"
    assert routed["ablations"]["execution_guard"] == "standard_verified_execution" and routed["final_outcome"]["success"]
    unsafe = config(tmp_path / "unsafe", experiment_mode=True, ablations={"disable_capability_grounding": True, "disable_verifier": True, "disable_resource_policy": True})
    baseline = ExperimentRunner(PROFILE).run(unsafe)["manifest"]
    assert baseline["ablations"]["execution_guard"] == "ablation_non_executing_baseline"
    assert baseline["final_outcome"] == {"executed": False, "reason": "ablation_non_executing_baseline"}
    assert not baseline["observations"] and not baseline["detections"]


def test_configured_sensor_dropout_and_battery_policy_are_recorded_without_detector_leakage(tmp_path):
    dropout = config(tmp_path / "dropout", sensor={"noise_bound": 0.1, "dropout_probability": 1.0})
    manifest = ExperimentRunner(PROFILE).run(dropout)["manifest"]
    assert manifest["observations"] and all(not item["observation_valid"] for item in manifest["observations"])
    assert all("events" not in item for item in manifest["observations"])
    low = config(tmp_path / "low", resource={"initial_battery": 1.0, "battery_per_door": 4.0, "inspection_battery_cost": 0.2, "safe_return_margin": 2.0})
    rejected = ExperimentRunner(PROFILE).run(low)["manifest"]
    assert rejected["resource_policy_decision"]["decision"] == "DEFER"
    assert rejected["ablations"]["execution_guard"] == "admission_denied" and not rejected["observations"]
    thresholds = config(tmp_path / "thresholds", task_text="Inspect the kitchen.", scenario_distribution=[{"name": "hot", "text": "The kitchen is hot.", "weight": 1.0}], thresholds={"temperature_max": 100.0, "humidity_max": 100.0})
    thresholded = ExperimentRunner(PROFILE).run(thresholds)["manifest"]
    assert not thresholded["detections"] and thresholded["ground_truth"]["events"]
