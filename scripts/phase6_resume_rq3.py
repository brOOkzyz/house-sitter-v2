#!/usr/bin/env python3
"""Resume only the missing RQ3 portion of an interrupted Phase 6 formal run."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import random
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results/raptor_lite/phase6_formal"
BASE_HEAD = "c097a1babdd887297afed233ec7057aa2220f271"
PH = "25a16e15fc07c2c9d3c76e52de067ca47f09950ac532bbbf1f14e611753c2847"
AH = "fc1e1e1e20817435a80c0886715dcb25ce4ee3844e0ecf64f15c7189f34f9594"
sys.path.insert(0, str(ROOT))

from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.executor import BackendExecutor
from raptor_lite.house2d import House2DBackend
from raptor_lite.phase57 import resource_decision
from raptor_lite.planner import OfflineHouseSitterPlanner
from raptor_lite.verifier import verify_task


def module(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


CF, REPLAY, STAT = module("phase6_counterfactuals"), module("phase6_replay"), module("phase6_statistics")


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def file_hash(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path: Path, value: object) -> None:
    if path.exists(): raise RuntimeError(f"Refusing to overwrite immutable formal artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
def tagged(**value: object) -> dict: return {"phase6_formal": True, "protocol_hash": PH, "analysis_plan_hash": AH, **value}
def wilson(k: int, n: int) -> list[float | None]:
    if not n: return [None, None]
    z, p = 1.95996398454, k / n; d = 1 + z * z / n; center = (p + z * z / (2 * n)) / d; half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [center - half, center + half]
def binary(values: list[bool]) -> dict: return {"n": len(values), "count": sum(values), "rate": sum(values) / len(values), "wilson95": wilson(sum(values), len(values))}
def f1(tp: int, fp: int, fn: int) -> float:
    p, r = (tp / (tp + fp) if tp + fp else 0), (tp / (tp + fn) if tp + fn else 0)
    return 2 * p * r / (p + r) if p + r else 0
def bootstrap(values: list[float], seed: str) -> list[float]:
    rng = random.Random(seed); n = len(values); samples = sorted(sum(rng.choice(values) for _ in values) / n for _ in range(10000)); return [samples[249], samples[9749]]
def bh(named: list[tuple[str, float]]) -> list[dict]:
    ordered = sorted(named, key=lambda item: item[1]); floor, answer = 1.0, [None] * len(ordered)
    for index in range(len(ordered) - 1, -1, -1): floor = min(floor, ordered[index][1] * len(ordered) / (index + 1)); answer[index] = {"comparison": ordered[index][0], "pvalue": ordered[index][1], "bh_fdr_qvalue": min(1.0, floor)}
    return answer


def audit_existing() -> tuple[dict, dict]:
    expected = {OUT / "provenance.json", OUT / "raw/rq1.json", OUT / "raw/rq2.json", OUT / "integrity/rq1.json", OUT / "integrity/rq2.json", OUT / "manifests/rq1.json", OUT / "manifests/rq2.json"}
    if not OUT.is_dir() or any(not path.is_file() for path in expected): raise RuntimeError("Interrupted formal directory lacks complete RQ1/RQ2 artifacts.")
    if any(path.exists() for path in (OUT / "raw/rq3.json", OUT / "integrity/rq3.json", OUT / "analysis/summary.json")): raise RuntimeError("RQ3 or analysis artifact already exists; resume is not safe.")
    values = []
    for rq, count in (("rq1", 720), ("rq2", 320)):
        raw = json.loads((OUT / f"raw/{rq}.json").read_text()); integrity = json.loads((OUT / f"integrity/{rq}.json").read_text())
        if raw["phase6_formal"] is not True or raw["protocol_hash"] != PH or raw["analysis_plan_hash"] != AH or raw["git_head"] != BASE_HEAD or len(raw["records"]) != count or digest(raw["records"]) != raw["raw_hash"] != "": raise RuntimeError(f"{rq} raw artifact is invalid.")
        if integrity["raw_hash"] != raw["raw_hash"] or integrity["actual"] != count or integrity["duplicates"] != 0 or integrity["missing"] != 0 or not integrity["oracle_independence"]: raise RuntimeError(f"{rq} integrity artifact is invalid.")
        values.append(raw)
    if "pilot" in json.dumps(values).casefold(): raise RuntimeError("Pilot data contamination detected.")
    return values[0], values[1]


def preflight() -> tuple[dict, dict, dict]:
    rq1, rq2 = audit_existing()
    for command in (("scripts/phase6_formal.py", "preflight"), ("scripts/phase6_validity_audit.py", "audit"), ("scripts/phase6_replay_addendum.py", "preflight"), ("scripts/phase6_rq3_correction.py", "preflight"), ("scripts/phase6_rq3_validity_audit.py", "audit")):
        environment = {**__import__("os").environ, "PYTHONPATH": str(ROOT)}
        result = subprocess.run([sys.executable, str(ROOT / command[0]), command[1]], cwd=ROOT, text=True, capture_output=True, env=environment)
        if result.returncode: raise RuntimeError(result.stderr or result.stdout)
    correction = yaml.safe_load((ROOT / "configs/raptor_lite/phase6_rq3_implementation_correction.yaml").read_text())
    data = json.loads((ROOT / "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json").read_text())
    rows = [*data["development"], *data["held_out"]]
    if len(rows) != 400 or len({row["seed"] for row in rows}) != 400 or len(data["replay_seeds"]) != 40: raise RuntimeError("Corrected RQ3 seed material is invalid.")
    return rq1, rq2, {"correction": correction, "data": data}


def mission(row: dict, route_disabled: bool, registry: CapabilityRegistry, planner: OfflineHouseSitterPlanner) -> tuple[dict, dict, dict]:
    plan = planner.plan("Patrol all rooms and report anything unusual.", optimize_route=not route_disabled); task = plan.candidate_task; scenario = row["scenario"]
    backend = House2DBackend(seed=row["seed"], initial_battery=scenario["initial_battery"], sensor_noise_bound=scenario["sensor_noise_bound"], scenario={"seed": row["seed"], "events": scenario["scenario_events"], "validation_status": "approved"})
    backend.initialize(task); verifier = verify_task(task, registry); policy = resource_decision(task, {**backend.current_robot_state(), "activity": "idle"})
    result, trace = (None, []) if policy["decision"] != "APPROVE" else BackendExecutor(backend).run(task, verifier, registry)
    bundle = backend.artifact_bundle(); truth = {(event["room"], event["type"]) for event in bundle["scenario_ground_truth"]["events"] if event["type"] != "observation_dropout"}; detected = {(item["room"], item["anomaly_type"]) for item in bundle["detected_anomalies"]}; tp, fp, fn = len(truth & detected), len(detected - truth), len(truth - detected)
    execution = result.model_dump(mode="json") if result else {"executed": False, "reason": "resource_policy_defer"}
    science = {"seed": row["seed"], "condition": "route_optimization_disabled" if route_disabled else "full_system", "scenario_input": scenario, "task_spec": task.model_dump(mode="json"), "verifier_decision": verifier.model_dump(mode="json"), "resource_policy_decision": policy, "execution_guard": "standard_verified_execution" if result else "admission_deferred", "execution_result": execution, "trace": [item.model_dump(mode="json") for item in trace], "ground_truth": bundle["scenario_ground_truth"], "observations": bundle["sensor_observations"], "route": bundle["route_trace"], "detections": bundle["detected_anomalies"], "twin": {"before": bundle["digital_twin_before"], "after": bundle["digital_twin_after"], "diff": bundle["digital_twin_updates"]}}
    outcome = {"execution_success": bool(result and result.success), "execution_guard": science["execution_guard"], "resource_policy": policy, "tp": tp, "fp": fp, "fn": fn, "event_f1": f1(tp, fp, fn), "feedback_ground_truth_leakage": any(set(item) - {"observation_id", "room", "timestamp", "robot_state", "visit_index", "visible_object_identifiers", "obstacle_present", "temperature_c", "humidity_percent", "transition_accessibility", "battery", "observation_valid"} for item in bundle["sensor_observations"]), "route_cost": sum(float(item["path_length"]) for item in bundle["route_trace"]), "planned_route_cost": task.metadata["planned_route_cost"], "twin_correct": all(item["room"] in {update.get("room") for update in bundle["digital_twin_updates"]} for item in bundle["detected_anomalies"]), "scientific_replay_hash": REPLAY.scientific_hash(science)}
    provenance = {"execution_wall_clock": {key: execution.get(key) for key in ("start_timestamp", "end_timestamp")}, "backend": {"identity": House2DBackend.name, "version": House2DBackend.version, "role": "experimental"}}
    return outcome, science, provenance


def run_rq3(extra: dict) -> dict:
    data, correction = extra["data"], extra["correction"]; registry = CapabilityRegistry.from_yaml(ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"); planner = OfflineHouseSitterPlanner(registry); head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.strip(); rows = []
    for row in [*data["development"], *data["held_out"]]:
        cohort = row["analysis_cohort"]
        for condition, disabled in (("full_system", False), ("route_optimization_disabled", True)):
            outcome, science, provenance = mission(row, disabled, registry, planner); rows.append(tagged(rq_id="RQ3", record_id=f"{row['seed']}:{condition}", pair_id=str(row["seed"]), condition=condition, seed=row["seed"], analysis_cohort=cohort, confirmatory=cohort == "held_out", ground_truth=row["scenario"], raw_outcome=outcome, scientific_payload=science, provenance=provenance, git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"]))
        task = planner.plan("Patrol all rooms and report anything unusual.").candidate_task; scenario = row["scenario"]; backend = House2DBackend(seed=row["seed"], initial_battery=scenario["initial_battery"], sensor_noise_bound=scenario["sensor_noise_bound"], scenario={"seed": row["seed"], "events": scenario["scenario_events"], "validation_status": "approved"}); backend.initialize(task); cf = CF.resource_policy_counterfactual(task.model_dump(mode="json"), {**backend.current_robot_state(), "activity": "idle"})
        rows.append(tagged(rq_id="RQ3", record_id=f"{row['seed']}:resource_policy_counterfactual", pair_id=str(row["seed"]), condition="resource_policy_counterfactual", seed=row["seed"], analysis_cohort=cohort, confirmatory=cohort == "held_out", ground_truth=row["scenario"], raw_outcome=cf, git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"]))
    ids = [row["record_id"] for row in rows]; pairs = {str(row["seed"]) for row in [*data["development"], *data["held_out"]]}
    if len(rows) != 1200 or len(ids) != len(set(ids)) or any({row["pair_id"] for row in rows if row["condition"] == condition} != pairs for condition in ("full_system", "route_optimization_disabled", "resource_policy_counterfactual")): raise RuntimeError("RQ3 logical-record integrity failure.")
    source = {row["seed"]: row for row in data["held_out"]}; replays = []
    for seed in data["replay_seeds"]:
        _, first, first_provenance = mission(source[seed], False, registry, planner); _, second, second_provenance = mission(source[seed], False, registry, planner); comparison = REPLAY.compare_replays(first, second); replays.append({"seed": seed, "comparison": comparison, "first_provenance": first_provenance, "second_provenance": second_provenance})
    if len(replays) != 40 or not all(item["comparison"]["scientifically_reproducible"] for item in replays): raise RuntimeError("RQ3 canonical scientific replay failure.")
    raw = {"records": rows, "replays": replays}; raw_hash = digest(raw)
    write(OUT / "provenance/rq3_implementation_correction.json", tagged(git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"], corrected_seed_manifest_sha256=file_hash(ROOT / "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json"), replay_addendum_hash=correction["sources"]["replay_addendum_hash"], correction_precedes_rq3_formal_admission=True, pilot_inputs_included=False))
    write(OUT / "raw/rq3.json", tagged(rq_id="RQ3", **raw, raw_hash=raw_hash, git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"]))
    write(OUT / "integrity/rq3.json", tagged(rq_id="RQ3", expected=1200, actual=1200, unique=1200, missing=0, duplicates=0, paired_alignment=True, replays=40, replay_matches=40, oracle_independence=True, confirmatory_held_out_only=True, raw_hash=raw_hash, git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"]))
    write(OUT / "manifests/rq3.json", tagged(rq_id="RQ3", input_path="configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json", input_sha256=file_hash(ROOT / "configs/raptor_lite/phase6_rq3_corrected_seed_manifest.json"), raw_sha256=raw_hash, expected_records=1200, actual_records=1200, development_seeds=100, held_out_seeds=300, replay_seeds=40, paired=True, oracle_independence=True, git_head=head, rq3_implementation_correction_hash=correction["locks"]["correction_hash"]))
    return raw


def analyze(rq1: dict, rq2: dict, rq3: dict) -> dict:
    r1, r2, r3 = rq1["records"], rq2["records"], rq3["records"]; by = {condition: [row for row in r1 if row["condition"] == condition] for condition in ("full_system", "capability_grounding_ablation", "verifier_ablation")}; r1_metrics = {condition: binary([row["raw_outcome"]["ground_truth_correct"] for row in values]) for condition, values in by.items()}; r1_comp = {f"full_vs_{condition}": STAT.paired_binary_effect([row["raw_outcome"]["ground_truth_correct"] for row in by["full_system"]], [row["raw_outcome"]["ground_truth_correct"] for row in by[condition]]) for condition in ("capability_grounding_ablation", "verifier_ablation")}
    micro = {key: binary([row["raw_outcome"][key] for row in r2]) for key in ("intent_correct", "taskspec_exact_match", "decision_correct", "end_to_end_correct")}; grouped = {target: [row for row in r2 if row["pair_id"] == target] for target in sorted({row["pair_id"] for row in r2})}; macro = {key: {"mean": sum(sum(row["raw_outcome"][key] for row in rows) / len(rows) for rows in grouped.values()) / 40, "bootstrap95": bootstrap([sum(row["raw_outcome"][key] for row in rows) / len(rows) for rows in grouped.values()], f"RQ2:{key}")} for key in micro}
    held = [row for row in r3 if row["analysis_cohort"] == "held_out"]; full = sorted([row for row in held if row["condition"] == "full_system"], key=lambda row: row["seed"]); route = sorted([row for row in held if row["condition"] == "route_optimization_disabled"], key=lambda row: row["seed"]); resource = sorted([row for row in held if row["condition"] == "resource_policy_counterfactual"], key=lambda row: row["seed"])
    deltas = [a["raw_outcome"]["route_cost"] - b["raw_outcome"]["route_cost"] for a, b in zip(full, route)]; f1s = [row["raw_outcome"]["event_f1"] for row in full]; tp, fp, fn = (sum(row["raw_outcome"][key] for row in full) for key in ("tp", "fp", "fn")); route_test = STAT.paired_binary_effect([row["raw_outcome"]["execution_success"] for row in full], [row["raw_outcome"]["execution_success"] for row in route])
    r3_metrics = {"confirmatory_n_per_condition": 300, "mission_success": binary([row["raw_outcome"]["execution_success"] for row in full]), "event_f1": {"mean": sum(f1s) / 300, "bootstrap95": bootstrap(f1s, "RQ3:f1")}, "event_counts": {"tp": tp, "fp": fp, "fn": fn}, "event_precision": tp / (tp + fp) if tp + fp else 0, "event_recall": tp / (tp + fn) if tp + fn else 0, "twin_correctness": binary([row["raw_outcome"]["twin_correct"] for row in full]), "feedback_ground_truth_leakage": binary([row["raw_outcome"]["feedback_ground_truth_leakage"] for row in full]), "route_full_minus_disabled": {"mean": sum(deltas) / 300, "bootstrap95": bootstrap(deltas, "RQ3:route")}, "route_mission_mcnemar": route_test, "resource_counterfactual": {"safe_defer": binary([row["raw_outcome"]["safe_defer"] for row in resource]), "unsafe_attempt": binary([row["raw_outcome"]["counterfactual_unsafe_attempt"] for row in resource]), "actual_unsafe_execution": "N/A (counterfactual is non-executing)"}, "replay_matches": 40}
    pvalues = [(name, result["exact_mcnemar_pvalue"]) for name, result in {**r1_comp, "rq3_route_mission": route_test}.items()]
    return {"rq1": {"decision_correctness": r1_metrics, "paired_comparisons": r1_comp}, "rq2": {"micro_320": micro, "macro_40_targets": macro}, "rq3": r3_metrics, "fdr": bh(pvalues), "failure_taxonomy": {"rq1_false_accept": sum(row["raw_outcome"]["would_accept"] and row["ground_truth"]["decision"] != "accept" for row in by["full_system"]), "rq2_taskspec_mismatch": sum(not row["raw_outcome"]["taskspec_exact_match"] for row in r2), "rq3_detector_tp_fp_fn": {"tp": tp, "fp": fp, "fn": fn}, "rq3_feedback_ground_truth_leakage": sum(row["raw_outcome"]["feedback_ground_truth_leakage"] for row in full), "rq3_reproducibility_mismatch": 0}}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "resume-rq3": raise SystemExit("usage: phase6_resume_rq3.py resume-rq3")
    rq1, rq2, extra = preflight(); rq3 = run_rq3(extra); summary = analyze(rq1, rq2, rq3); write(OUT / "analysis/summary.json", tagged(**summary)); write(OUT / "tables/metrics.json", tagged(**summary)); write(OUT / "plots/plot_data.json", tagged(**summary)); print(json.dumps({"status": "completed", "rq1": 720, "rq2": 320, "rq3": 1240}, sort_keys=True))


if __name__ == "__main__": main()
