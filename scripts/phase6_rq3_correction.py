#!/usr/bin/env python3
"""Validate the pre-RQ3 implementation-correction record."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "configs/raptor_lite/phase6_rq3_implementation_correction.yaml"


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def load() -> dict:
    value = yaml.safe_load(PATH.read_text(encoding="utf-8")); locked = deepcopy(value); locked["locks"].pop("correction_hash", None)
    assert value["scope"]["correction_precedes_rq3_formal_admission"] is True
    assert value["randomization"]["consumed_dimensions"] == ["room", "event_type", "event_parameters", "sensor_noise_bound", "observation_dropout", "initial_battery"]
    for key, relative in {"house2d_sha256": "raptor_lite/house2d.py", "house_sitter_sha256": "raptor_lite/house_sitter.py", "corrected_materializer_sha256": "scripts/phase6_rq3_corrected_materializer.py", "corrected_seed_manifest_sha256": "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json", "consumption_audit_sha256": "scripts/phase6_rq3_validity_audit.py"}.items():
        assert value["sources"][key] == sha256((ROOT / relative).read_bytes()).hexdigest()
    assert value["locks"]["correction_hash"] == digest(locked)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("command", choices=("preflight", "lock-hash")); args = parser.parse_args()
    raw = yaml.safe_load(PATH.read_text(encoding="utf-8")); locked = deepcopy(raw); locked["locks"].pop("correction_hash", None)
    if args.command == "lock-hash": print(json.dumps({"correction_hash": digest(locked)}, sort_keys=True))
    else: print(json.dumps({"correction_hash": load()["locks"]["correction_hash"], "status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__": raise SystemExit(main())
