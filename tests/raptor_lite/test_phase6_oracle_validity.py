from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest

from raptor_lite.scenario import verify_scenario


ROOT = Path(__file__).resolve().parents[2]


def test_every_independent_rq3_truth_is_a_consumable_direct_house2d_scenario():
    manifest = json.loads((ROOT / "configs/raptor_lite/phase6_rq3_seed_manifest.json").read_text())
    rows = [*manifest["development"], *manifest["held_out"]]
    assert len(rows) == 400
    for row in rows:
        scenario = {"seed": row["seed"], "events": row["scenario"]["scenario_events"]}
        assert verify_scenario({"status": "planned", "candidate_scenario": scenario})["approved"]


def test_independence_guard_fails_when_an_oracle_imports_an_evaluated_component(tmp_path):
    spec = importlib.util.spec_from_file_location("phase6_validity_audit", ROOT / "scripts/phase6_validity_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    bad_oracle = tmp_path / "bad_oracle.py"
    bad_oracle.write_text("from raptor_lite.planner import OfflineHouseSitterPlanner\n")
    with pytest.raises(AssertionError):
        module.assert_independent_source(bad_oracle)
