#!/usr/bin/env python3
"""Executable consumption and leakage audit for the corrected RQ3 chain."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.executor import BackendExecutor
from raptor_lite.house2d import ROBOT_OBSERVATION_FIELDS, House2DBackend
from raptor_lite.phase57 import resource_decision
from raptor_lite.planner import OfflineHouseSitterPlanner
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
MANIFEST = ROOT / "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json"


def event(room: str, event_type: str, parameters: dict) -> dict:
    return {"event_id": "audit-event-001", "room": room, "event_type": event_type, "parameters": parameters, "start_stage": "scenario_start", "persistence": "until_reset", "visual_representation": {"icon": "audit", "label": event_type}, "simulation_only": True}


def run(events: list[dict], *, battery: float = 90.0, noise: float = 0.0):
    registry = CapabilityRegistry.from_yaml(PROFILE); task = OfflineHouseSitterPlanner(registry).plan("Patrol all rooms and report anything unusual.").candidate_task
    backend = House2DBackend(seed=97531, initial_battery=battery, sensor_noise_bound=noise, scenario={"seed": 97531, "events": events, "validation_status": "approved"})
    backend.initialize(task); policy = resource_decision(task, {**backend.current_robot_state(), "activity": "idle"})
    result, _ = BackendExecutor(backend).run(task, verify_task(task, registry), registry)
    return task, backend.artifact_bundle(), result, policy


def observation(bundle: dict, room: str) -> dict:
    return next(item for item in bundle["sensor_observations"] if item["room"] == room)


def audit() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")); rows = [*manifest["development"], *manifest["held_out"]]
    assert len(rows) == 400 and len({row["seed"] for row in rows}) == 400
    assert all(row["scenario"]["randomized_dimensions"] == ["room", "event_type", "event_parameters", "sensor_noise_bound", "observation_dropout", "initial_battery"] for row in rows)
    assert all(event_row["parameters"].get("position") == [0.5, 0.5] for row in rows for event_row in row["scenario"]["scenario_events"] if event_row["event_type"] == "unexpected_obstacle")
    hot_a = run([event("kitchen", "high_temperature", {"temperature_c": 29.0})])[1]; hot_b = run([event("kitchen", "high_temperature", {"temperature_c": 42.0})])[1]
    humid = run([event("bathroom", "high_humidity", {"humidity_percent": 72.0})])[1]
    obstacle = run([event("bedroom", "unexpected_obstacle", {"object_kind": "box", "position": [0.5, 0.5]})])[1]
    blocked_result = run([event("living_room", "blocked_transition", {"doors": [["living_room", "kitchen"], ["living_room", "bedroom"]]})])[2]
    dropout = run([event("kitchen", "observation_dropout", {"checkpoint": "kitchen"})])[1]
    quiet = run([event("bathroom", "high_humidity", {"humidity_percent": 72.0})], noise=0.0)[1]; noisy = run([event("bathroom", "high_humidity", {"humidity_percent": 72.0})], noise=.10)[1]
    low_policy = run([event("bathroom", "high_humidity", {"humidity_percent": 72.0})], battery=2.0)[3]; high_policy = run([event("bathroom", "high_humidity", {"humidity_percent": 72.0})], battery=90.0)[3]
    bundles = [hot_a, hot_b, humid, obstacle, dropout, quiet, noisy]
    assert all(set(item) == ROBOT_OBSERVATION_FIELDS for bundle in bundles for item in bundle["sensor_observations"])
    assert observation(hot_a, "kitchen")["temperature_c"] != observation(hot_b, "kitchen")["temperature_c"]
    assert observation(humid, "bathroom")["humidity_percent"] == 72.0
    assert observation(obstacle, "bedroom")["obstacle_present"] is True
    assert not blocked_result.success
    assert observation(dropout, "kitchen")["observation_valid"] is False
    assert observation(quiet, "living_room")["temperature_c"] != observation(noisy, "living_room")["temperature_c"]
    assert low_policy["decision"] != high_policy["decision"]
    return {"ground_truth_or_provenance_fields_in_detector_observations": 0, "randomized_dimensions": {"room": "consumed_by_event_room", "event_type": "consumed_by_event_dispatch", "event_parameters": "consumed_by_temperature_humidity_doors", "sensor_noise_bound": "consumed_by_observation_noise", "observation_dropout": "consumed_by_observation_validity", "initial_battery": "consumed_by_resource_policy"}, "excluded_random_dimension": "unexpected_obstacle.position", "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("audit",)); parser.parse_args()
    print(json.dumps(audit(), sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
