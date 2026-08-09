#!/usr/bin/env python3
"""Phase 6A preflight and analysis-plan entry; formal execution is disabled."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path

import yaml


REQUIRED_HEAD = "3540c8922a840f43df5292e3df81ce662d66be20"


def canonical_hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")).hexdigest()


def lock_hashes(value: dict, inputs: dict[str, object]) -> dict[str, str]:
    locked_protocol = deepcopy(value)
    locked_protocol["locks"].pop("protocol_hash", None)
    locked_protocol["locks"].pop("analysis_plan_hash", None)
    locked_protocol["materialized_inputs"] = inputs
    return {"protocol_hash": canonical_hash(locked_protocol), "analysis_plan_hash": canonical_hash({"frozen_head": value["frozen_head"], "analysis_plan": value["analysis_plan"]})}


def load(path: Path) -> tuple[dict, dict[str, object]]:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    inputs = value["materialized_inputs"]
    return value, {
        "rq1_cases": json.loads(Path(inputs["rq1_cases_source"]).read_text(encoding="utf-8")),
        "rq2_corpus": yaml.safe_load(Path(inputs["rq2_corpus_source"]).read_text(encoding="utf-8")),
        "rq3_seed_manifest": json.loads(Path(inputs["rq3_seed_manifest_source"]).read_text(encoding="utf-8")),
    }


def protocol(path: Path) -> dict:
    value, inputs = load(path)
    if not isinstance(value, dict) or value.get("phase6_formal") is not True:
        raise ValueError("Protocol must declare phase6_formal=true.")
    if value.get("frozen_head") != REQUIRED_HEAD:
        raise ValueError("Protocol frozen_head does not match the Phase 5.11 baseline.")
    if value.get("execution_allowed") is not False:
        raise ValueError("Phase 6A protocol must keep execution_allowed=false.")
    if sum(value["rq1"]["strata"].values()) != value["rq1"]["held_out_cases"]:
        raise ValueError("RQ1 strata do not sum to held_out_cases.")
    if sum(value["rq2"]["form_strata"].values()) != value["rq2"]["corpus_items"]:
        raise ValueError("RQ2 form strata do not sum to corpus_items.")
    rq1, corpus, rq3 = inputs["rq1_cases"], inputs["rq2_corpus"], inputs["rq3_seed_manifest"]
    if rq1.get("phase6_formal") is not True or len(rq1.get("cases", [])) != value["rq1"]["held_out_cases"] or rq1.get("strata") != value["rq1"]["strata"] or rq1.get("safe_repair_subset") != value["rq1"]["safe_repair_subset"]:
        raise ValueError("RQ1 materialized cases are inconsistent with the locked protocol.")
    if corpus.get("phase6_formal") is not True or corpus.get("item_count") != value["rq2"]["corpus_items"] or corpus.get("semantic_target_count") != value["rq2"]["semantic_targets"] or len(corpus.get("utterances", [])) != corpus["item_count"]:
        raise ValueError("RQ2 corpus source is not the declared locked 320-item matrix.")
    if value["rq3"]["seed_partitions"] != {"development": 100, "held_out": 300} or any(value["rq3"]["development_runs"][key] != 100 for key in value["rq3"]["development_runs"]) or any(value["rq3"]["confirmatory_runs"][key] != 300 for key in value["rq3"]["confirmatory_runs"]):
        raise ValueError("RQ3 development and confirmatory seed partitions are not locked.")
    expected = sum(value["rq3"]["development_runs"].values()) + sum(value["rq3"]["confirmatory_runs"].values()) + value["rq3"]["reproducibility_replays"]
    if expected != value["rq3"]["logical_runs"]:
        raise ValueError("RQ3 logical_runs is inconsistent with its conditions.")
    if len(rq3.get("development", [])) != 100 or len(rq3.get("held_out", [])) != 300 or len(rq3.get("replay_seeds", [])) != value["rq3"]["reproducibility_replays"]:
        raise ValueError("RQ3 materialized seed manifest is inconsistent with the locked protocol.")
    hashes = lock_hashes(value, inputs)
    if value.get("locks", {}).get("hash_algorithm") != "sha256-canonical-json-v1" or any(value["locks"].get(key) != digest for key, digest in hashes.items()):
        raise ValueError("Protocol or analysis-plan hash is missing or does not match the locked content.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 6A protocol entry; never runs formal data.")
    parser.add_argument("command", choices=("preflight", "analysis-plan", "lock-hashes", "run"))
    parser.add_argument("--config", type=Path, default=Path("configs/raptor_lite/phase6_formal_protocol.yaml"))
    args = parser.parse_args(argv)
    if args.command == "lock-hashes":
        value, inputs = load(args.config)
        print(json.dumps(lock_hashes(value, inputs), sort_keys=True))
        return 0
    value = protocol(args.config)
    if args.command == "run":
        parser.error("Formal execution is disabled in Phase 6A; obtain a separate Phase 6 execution instruction.")
    if args.command == "preflight":
        print(json.dumps({"phase6_formal": True, "execution_allowed": False, "frozen_head": value["frozen_head"], "protocol_hash": value["locks"]["protocol_hash"], "analysis_plan_hash": value["locks"]["analysis_plan_hash"], "output_root": value["output_root"], "logical_runs": sum(value[key]["logical_runs"] for key in ("rq1", "rq2", "rq3"))}, sort_keys=True))
    else:
        print(json.dumps({"analysis_plan_hash": value["locks"]["analysis_plan_hash"], "analysis_plan": value["analysis_plan"], "rq_metrics": {key: {"primary": value[key]["primary_metrics"], "secondary": value[key]["secondary_metrics"]} for key in ("rq1", "rq2", "rq3")}, "failure_analysis": "retain trace-linked failures; stop on Critical/High or reproducibility defect"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
