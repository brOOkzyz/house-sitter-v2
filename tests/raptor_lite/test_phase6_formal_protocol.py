from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ENV = {**os.environ, "PYTHONPATH": str(ROOT)}
SCRIPT = ROOT / "scripts/phase6_formal.py"
MATERIALIZER = ROOT / "scripts/phase6_independent_oracle.py"
COUNTERFACTUALS = ROOT / "scripts/phase6_counterfactuals.py"
STATISTICS = ROOT / "scripts/phase6_statistics.py"
VALIDITY_AUDIT = ROOT / "scripts/phase6_validity_audit.py"
ORACLE_PROJECTION = ROOT / "scripts/phase6_oracle_projection.py"


def test_phase6a_preflight_is_pinned_and_non_executing():
    result = subprocess.run([sys.executable, str(SCRIPT), "preflight"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["execution_allowed"] is False
    assert payload["frozen_head"] == "3540c8922a840f43df5292e3df81ce662d66be20"
    assert payload["logical_runs"] == 2280
    assert len(payload["protocol_hash"]) == len(payload["analysis_plan_hash"]) == 64


def test_phase6a_analysis_plan_locks_grouped_rq2_and_held_out_rq3_rules():
    result = subprocess.run([sys.executable, str(SCRIPT), "analysis-plan"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["analysis_plan"]["analysis_entrypoint"] == "python scripts/phase6_formal.py analysis-plan"
    assert payload["analysis_plan"]["inference"]["multiple_comparisons"]["primary_family"] == "benjamini_hochberg_fdr_q_0_05_per_rq"
    assert "taskspec_exact_match" in payload["rq_metrics"]["rq2"]["primary"]


def test_phase6_revision3_independent_materialized_inputs_pass_the_integrity_audit():
    result = subprocess.run([sys.executable, str(MATERIALIZER), "audit"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"rq1_cases": 240, "rq1_repairable": 80, "rq2_targets": 40, "rq2_utterances": 320, "rq3_development": 100, "rq3_held_out": 300, "rq3_replays": 40, "status": "passed"}


def test_phase6_revision3_validity_audit_rejects_evaluated_component_dependencies():
    result = subprocess.run([sys.executable, str(VALIDITY_AUDIT), "audit"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"active_oracle": "scripts/phase6_independent_oracle.py", "banned_component_imports": 0, "rq1_independent": True, "rq2_independent": True, "rq3_independent": True, "status": "passed"}


def test_phase6_revision3_oracle_projection_is_a_pure_data_normalizer():
    result = subprocess.run([sys.executable, str(ORACLE_PROJECTION), "self-test"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"status": "passed"}


def test_phase6_revision2_counterfactual_ablations_are_non_executing_and_distinct():
    result = subprocess.run([sys.executable, str(COUNTERFACTUALS), "self-test"], cwd=ROOT, text=True, capture_output=True, env=SCRIPT_ENV)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"paired_seed_alignment": 400, "rq1_ablation_decisions_differ": True, "resource_counterfactual_non_executing": True, "status": "passed", "unsafe_executor_invocations": 0}


def test_phase6_statistics_self_test_locks_paired_mcnemar_and_effect_size_math():
    result = subprocess.run([sys.executable, str(STATISTICS), "self-test"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"absolute_difference_pp": 50.0, "counterfactual_only": 2, "exact_mcnemar_pvalue": 0.5, "full_only": 0, "n_pairs": 4, "status": "passed"}


def test_phase6a_run_command_refuses_to_create_formal_data():
    result = subprocess.run([sys.executable, str(SCRIPT), "run"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode != 0
    assert "disabled in Phase 6A" in result.stderr
