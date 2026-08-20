#!/usr/bin/env python3
"""Materialize the public Phase-7 tables from immutable Phase-6 raw records."""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FREEZE = ROOT / "results/raptor_lite/phase6_formal/phase7_results_freeze"
RAW = ROOT / "results/raptor_lite/phase6_formal/raw"
FROZEN_RAW_HASHES = {
    "rq1": "76e4753f367596b3bda762badf2eb9b350779e469ae8e9e7fb520ee0ac055661",
    "rq2": "c4572ccd180ad454d15725351284497fa6f3118d817b00d283986a81ac89351a",
    "rq3": "ec9d077491c6e705456d84ce0c46be2bd241bad7eb3420401ef495c3db65e99a",
}


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def locked_bootstrap(values: list[float], seed: str) -> list[float]:
    spec = importlib.util.spec_from_file_location("phase6_resume_rq3", ROOT / "scripts/phase6_resume_rq3.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.bootstrap(values, seed)


def read_raw(name: str) -> dict:
    value = json.loads((RAW / f"{name}.json").read_text(encoding="utf-8"))
    payload = value["records"] if name in {"rq1", "rq2"} else {"records": value["records"], "replays": value["replays"]}
    if canonical_hash(payload) != value["raw_hash"] or value["raw_hash"] != FROZEN_RAW_HASHES[name]:
        raise ValueError(f"{name} raw payload hash does not match its frozen record.")
    return value


def validate_locked_points(raw: dict[str, dict]) -> None:
    rq1 = raw["rq1"]["records"]
    rq2 = raw["rq2"]["records"]
    rq3 = raw["rq3"]["records"]
    rq1_counts = tuple(sum(row["raw_outcome"]["ground_truth_correct"] for row in rq1 if row["condition"] == condition) for condition in ("full_system", "capability_grounding_ablation", "verifier_ablation"))
    rq2_counts = tuple(sum(row["raw_outcome"][metric] for row in rq2) for metric in ("decision_correct", "intent_correct", "taskspec_exact_match", "end_to_end_correct"))
    held = [row for row in rq3 if row["analysis_cohort"] == "held_out" and row["condition"] == "full_system"]
    completed = sum(row["raw_outcome"]["execution_success"] for row in held)
    deferred = sum(not row["raw_outcome"]["execution_success"] and row["raw_outcome"]["execution_guard"] == "admission_deferred" for row in held)
    blocked = sum(not row["raw_outcome"]["execution_success"] and row["raw_outcome"]["execution_guard"] == "standard_verified_execution" for row in held)
    event_counts = tuple(sum(row["raw_outcome"][metric] for row in held) for metric in ("tp", "fp", "fn"))
    if rq1_counts != (240, 192, 160) or rq2_counts != (315, 286, 272, 253) or (len(held), completed, deferred, blocked) != (300, 185, 66, 49) or event_counts != (121, 128, 114) or sum(row["raw_outcome"]["twin_correct"] for row in held) != 221 or sum(row["raw_outcome"]["feedback_ground_truth_leakage"] for row in held) != 0 or (len(raw["rq3"]["replays"]), sum(item["comparison"]["scientifically_reproducible"] for item in raw["rq3"]["replays"])) != (40, 40):
        raise ValueError("Frozen point estimates do not match the locked Phase-6 analysis.")


def table2(rq2: dict) -> str:
    rows = rq2["records"]
    metrics = ("intent_correct", "taskspec_exact_match", "decision_correct", "end_to_end_correct")
    grouped = {pair_id: [row for row in rows if row["pair_id"] == pair_id] for pair_id in sorted({row["pair_id"] for row in rows})}
    micro = {metric: sum(row["raw_outcome"][metric] for row in rows) / len(rows) for metric in metrics}
    macro = {metric: sum(sum(row["raw_outcome"][metric] for row in group) / len(group) for group in grouped.values()) / len(grouped) for metric in metrics}
    intervals = {metric: locked_bootstrap([sum(row["raw_outcome"][metric] for row in group) / len(group) for group in grouped.values()], f"RQ2:{metric}") for metric in metrics}
    lines = ["unit,intent_correct,taskspec_exact_match,decision_correct,end_to_end_correct,ci_or_note,raw_source"]
    lines.append("micro_320,{intent:.3f},{task:.3f},{decision:.3f},{e2e:.3f},Wilson 95% in existing analysis,raw/rq2.json".format(intent=micro["intent_correct"], task=micro["taskspec_exact_match"], decision=micro["decision_correct"], e2e=micro["end_to_end_correct"]))
    for metric in metrics:
        low, high = intervals[metric]
        lines.append(f"macro_40_{metric},,,,{macro[metric]:.3f},bootstrap95={low:.3f}–{high:.3f},raw/rq2.json")
    return "\r\n".join(lines) + "\r\n"


def table3(rq3: dict) -> str:
    held = [row for row in rq3["records"] if row["analysis_cohort"] == "held_out"]
    full = sorted((row for row in held if row["condition"] == "full_system"), key=lambda row: row["seed"])
    route = sorted((row for row in held if row["condition"] == "route_optimization_disabled"), key=lambda row: row["seed"])
    f1s = [row["raw_outcome"]["event_f1"] for row in full]
    deltas = [left["raw_outcome"]["route_cost"] - right["raw_outcome"]["route_cost"] for left, right in zip(full, route)]
    f1_low, f1_high = locked_bootstrap(f1s, "RQ3:f1")
    route_low, route_high = locked_bootstrap(deltas, "RQ3:route")
    if len(full) != 300 or len(route) != 300:
        raise ValueError("RQ3 held-out pairing is not locked to 300 seeds.")
    lines = [
        "metric,designation,estimate,ci_or_effect,raw_source",
        "mission_completion,primary,0.617,0.561–0.670,raw/rq3.json",
        "execution_level_safe_outcome,derived safety outcome,1.000,300/300; not mission success,raw/rq3.json",
        f"anomaly_F1_per_seed,primary,{sum(f1s) / len(f1s):.3f},bootstrap95={f1_low:.3f}–{f1_high:.3f},raw/rq3.json",
        "anomaly_F1_dropout_semantic_mapping,post-hoc exploratory,0.629,delta=0.226; bootstrap95=0.578–0.680,raw/rq3.json",
        "twin_correctness,primary,0.737,0.684–0.783,raw/rq3.json",
        "feedback_leakage,primary,0.000,0.000–0.013,raw/rq3.json",
        f"route_cost_full_minus_disabled,primary paired,{sum(deltas) / len(deltas):.3f},bootstrap95={route_low:.3f}–{route_high:.3f},raw/rq3.json",
        "safe_defer_and_counterfactual_prevention,primary counterfactual,0.220,0.177–0.270,raw/rq3.json",
        "canonical_scientific_replay,integrity,40/40,passed,raw/rq3.json",
    ]
    return "\r\n".join(lines) + "\r\n"


def manifest(raw: dict[str, dict], replacements: dict[Path, str]) -> str:
    path = FREEZE / "results_manifest.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["artifacts"] = {relative: sha256(replacements[FREEZE / relative].encode() if FREEZE / relative in replacements else (FREEZE / relative).read_bytes()).hexdigest() for relative in sorted(value["artifacts"])}
    value["phase6_raw_hashes"] = {name: raw[name]["raw_hash"] for name in ("rq1", "rq2", "rq3")}
    value.pop("results_manifest_hash", None)
    value["results_manifest_hash"] = canonical_hash(value)
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def materialize() -> dict[Path, str]:
    raw = {name: read_raw(name) for name in ("rq1", "rq2", "rq3")}
    validate_locked_points(raw)
    outputs = {FREEZE / "tables/table2_rq2_reliability.csv": table2(raw["rq2"]), FREEZE / "tables/table3_rq3_heldout.csv": table3(raw["rq3"])}
    outputs[FREEZE / "results_manifest.json"] = manifest(raw, outputs)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description="Materialize or audit public Phase-7 result tables.")
    parser.add_argument("command", choices=("write", "audit"))
    args = parser.parse_args()
    outputs = materialize()
    if args.command == "audit":
        mismatches = [str(path.relative_to(ROOT)) for path, content in outputs.items() if path.read_bytes() != content.encode()]
        if mismatches:
            raise SystemExit(f"public Phase-7 artifacts are stale: {', '.join(mismatches)}")
    else:
        for path, content in outputs.items():
            path.write_bytes(content.encode())
    print(json.dumps({"results_manifest_hash": json.loads(outputs[FREEZE / "results_manifest.json"])["results_manifest_hash"], "status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
