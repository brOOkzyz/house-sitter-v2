#!/usr/bin/env python3
"""Independence audit for active Phase 6 oracle inputs; never imports evaluated code."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/raptor_lite"
BANNED = {"OfflineHouseSitterPlanner", "verify_task", "safe_repair", "Detector", "detect_environment_change"}
BANNED_MODULE_PREFIXES = ("raptor_lite",)


def assert_independent_source(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(not name.name.startswith(BANNED_MODULE_PREFIXES) for name in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith(BANNED_MODULE_PREFIXES)
        elif isinstance(node, ast.Name):
            assert node.id not in BANNED


def audit() -> dict[str, object]:
    protocol = yaml.safe_load((CONFIG / "phase6_formal_protocol.yaml").read_text(encoding="utf-8"))
    source = ROOT / protocol["materialized_inputs"]["generator_source"]
    projection = ROOT / protocol["materialized_inputs"]["oracle_projection_source"]
    assert_independent_source(source); assert_independent_source(projection)
    rq1 = json.loads((CONFIG / "phase6_rq1_cases.json").read_text(encoding="utf-8"))
    rq2 = yaml.safe_load((CONFIG / "phase6_rq2_corpus.yaml").read_text(encoding="utf-8"))
    rq3 = json.loads((CONFIG / "phase6_rq3_seed_manifest.json").read_text(encoding="utf-8"))
    assert rq1["ground_truth_provenance"] == rq2["ground_truth_provenance"] == "independent_explicit_rules"
    assert rq3["ground_truth_provenance"] == "independent_seeded_generator"
    assert len(rq1["cases"]) == 240 and Counter(row["stratum"] for row in rq1["cases"]) == Counter(rq1["strata"])
    assert len(rq2["utterances"]) == 320 and len(rq2["semantic_targets"]) == 40
    assert all(row["label_provenance"] == "independent_explicit_rule_v1" for row in [*rq1["cases"], *rq2["utterances"]])
    seeds = [row["seed"] for row in [*rq3["development"], *rq3["held_out"]]]
    assert len(set(seeds)) == 400 and set(rq3["replay_seeds"]).issubset(set(seeds[100:]))
    assert all(row["scenario"]["scenario_events"] and "event_time_stage" not in row["scenario"] and "event_duration_stages" not in row["scenario"] for row in [*rq3["development"], *rq3["held_out"]])
    return {"active_oracle": str(source.relative_to(ROOT)), "banned_component_imports": 0, "rq1_independent": True, "rq2_independent": True, "rq3_independent": True, "status": "passed"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 6 ground-truth independence.")
    parser.add_argument("command", choices=("audit",)); parser.parse_args()
    print(json.dumps(audit(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
