#!/usr/bin/env python3
"""Validate the immutable Phase 6 replay-normalization addendum."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
ADDENDUM = ROOT / "configs/raptor_lite/phase6_replay_addendum.yaml"
PROTOCOL_HASH = "25a16e15fc07c2c9d3c76e52de067ca47f09950ac532bbbf1f14e611753c2847"
ANALYSIS_HASH = "fc1e1e1e20817435a80c0886715dcb25ce4ee3844e0ecf64f15c7189f34f9594"


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def load(path: Path = ADDENDUM) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    locked = deepcopy(value); locked["locks"].pop("addendum_hash", None)
    if value["phase6_formal"] is not True or value["base_protocol_hash"] != PROTOCOL_HASH or value["base_analysis_plan_hash"] != ANALYSIS_HASH:
        raise ValueError("Replay addendum must be pinned to the existing formal protocol and analysis plan.")
    if value["reason"] != "replay normalization excludes non-scientific volatile provenance metadata; no outcome-dependent change to hypotheses, samples, metrics, labels or system behaviour.":
        raise ValueError("Replay addendum reason is incomplete.")
    source = ROOT / value["replay_comparison_source"]
    if sha256(source.read_bytes()).hexdigest() != value["replay_comparison_sha256"]:
        raise ValueError("Replay comparison source hash is not locked.")
    if canonical_hash(locked) != value["locks"]["addendum_hash"]:
        raise ValueError("Replay addendum hash is not locked.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Phase 6 replay addendum.")
    parser.add_argument("command", choices=("preflight", "lock-hash"))
    args = parser.parse_args()
    raw = yaml.safe_load(ADDENDUM.read_text(encoding="utf-8")); locked = deepcopy(raw); locked["locks"].pop("addendum_hash", None)
    if args.command == "lock-hash":
        print(json.dumps({"replay_comparison_sha256": sha256((ROOT / raw["replay_comparison_source"]).read_bytes()).hexdigest(), "addendum_hash": canonical_hash(locked)}, sort_keys=True))
    else:
        value = load()
        print(json.dumps({"phase6_formal": True, "addendum_hash": value["locks"]["addendum_hash"], "scientific_payload_schema": value["scientific_payload_schema"], "base_protocol_hash": value["base_protocol_hash"], "base_analysis_plan_hash": value["base_analysis_plan_hash"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
