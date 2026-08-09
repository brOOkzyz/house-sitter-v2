#!/usr/bin/env python3
"""Independent corrected RQ3 materializer: fixed seeds, no fake spatial variable."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import random


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json"
ROOMS = ("living_room", "kitchen", "bedroom", "bathroom")
DOORS = (("charging_area", "living_room"), ("living_room", "kitchen"), ("living_room", "bedroom"), ("bedroom", "bathroom"), ("kitchen", "bathroom"))


def scenario(seed: int, cohort: str) -> dict:
    rng = random.Random(f"phase6-rq3-generator-v1:{seed}")
    room, kind = rng.choice(ROOMS), rng.choice(["unexpected_obstacle", "high_humidity", "high_temperature", "blocked_transition", "observation_dropout"])
    battery = rng.randint(2, 25) if rng.random() < .20 else rng.randint(40, 100)
    noise, dropout = rng.choice([0.0, .02, .05, .10]), rng.random() < .20
    # House2D is room-graph, not spatial. Keep the schema-required placement
    # constant and explicitly exclude it from the randomized dimensions.
    parameters = {"unexpected_obstacle": {"object_kind": "box", "position": [0.5, 0.5]}, "high_humidity": {"humidity_percent": round(rng.uniform(72, 88), 1)}, "high_temperature": {"temperature_c": round(rng.uniform(29, 42), 1)}, "blocked_transition": {"doors": [list(door) for door in DOORS if room in door and "charging_area" not in door]}, "observation_dropout": {"checkpoint": room}}[kind]
    events = [{"event_id": "oracle-event-001", "room": room, "event_type": kind, "parameters": parameters, "start_stage": "scenario_start", "persistence": "until_reset", "visual_representation": {"icon": "oracle_event", "label": kind}, "simulation_only": True}]
    if dropout and kind != "observation_dropout":
        events.append({"event_id": "oracle-dropout-001", "room": room, "event_type": "observation_dropout", "parameters": {"checkpoint": room}, "start_stage": "scenario_start", "persistence": "until_reset", "visual_representation": {"icon": "sensor_unknown", "label": "observation dropout"}, "simulation_only": True})
    text = {"unexpected_obstacle": f"There is a box in the {room.replace('_', ' ')}.", "high_humidity": f"The {room.replace('_', ' ')} has high humidity.", "high_temperature": f"The {room.replace('_', ' ')} is hot.", "blocked_transition": f"The {room.replace('_', ' ')} doorway is blocked.", "observation_dropout": f"The {room.replace('_', ' ')} sensor is unavailable."}[kind]
    return {"seed": seed, "analysis_cohort": cohort, "ground_truth_provenance": "independent_seeded_generator", "scenario": {"room": room, "event_type": kind, "scenario_events": events, "sensor_noise_bound": noise, "observation_dropout": dropout, "initial_battery": battery, "scenario_text": text, "randomized_dimensions": ["room", "event_type", "event_parameters", "sensor_noise_bound", "observation_dropout", "initial_battery"], "non_random_schema_parameters": {"unexpected_obstacle.position": "[0.5, 0.5] fixed; House2D has no spatial obstacle model"}, "constraints": ["all ground-truth events are direct House2D scenario events", "temporal event scheduling and obstacle location are excluded because frozen House2D has no such input"]}}


def materialize() -> dict:
    development = [scenario(seed, "development") for seed in range(61000, 61100)]
    held_out = [scenario(seed, "held_out") for seed in range(62000, 62300)]
    replay = sorted(held_out, key=lambda row: sha256(f"phase6-rq3-replay-v1:{row['seed']}".encode()).hexdigest())[:40]
    return {"phase6_formal": True, "oracle_version": "phase6-independent-oracle-v1-rq3-correction", "ground_truth_provenance": "independent_seeded_generator", "generator": {"id": "phase6-rq3-generator-v1-corrected", "seed_mapping": "random.Random('phase6-rq3-generator-v1:{seed}')", "development_seed_range": [61000, 61099], "held_out_seed_range": [62000, 62299], "replay_selection": "lowest 40 SHA-256 digests of phase6-rq3-replay-v1:{held_out_seed}", "removed_random_dimension": "unexpected_obstacle.position"}, "development": development, "held_out": held_out, "replay_seeds": [row["seed"] for row in replay]}


def audit(value: dict) -> dict:
    rows = [*value["development"], *value["held_out"]]
    assert len(value["development"]) == 100 and len(value["held_out"]) == 300 and len({row["seed"] for row in rows}) == 400
    assert all(scenario(row["seed"], row["analysis_cohort"]) == row for row in rows)
    assert all(row["scenario"]["randomized_dimensions"] == ["room", "event_type", "event_parameters", "sensor_noise_bound", "observation_dropout", "initial_battery"] for row in rows)
    assert all(event["parameters"].get("position") == [0.5, 0.5] for row in rows for event in row["scenario"]["scenario_events"] if event["event_type"] == "unexpected_obstacle")
    return {"development": 100, "held_out": 300, "replays": 40, "randomized_dimensions": 6, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("write", "audit")); args = parser.parse_args()
    value = materialize()
    if args.command == "write": OUT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif not OUT.exists(): raise FileNotFoundError(OUT)
    else: value = json.loads(OUT.read_text(encoding="utf-8"))
    print(json.dumps(audit(value), sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
