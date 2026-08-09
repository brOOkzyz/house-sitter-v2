from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/phase6_formal.py"


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


def test_phase6a_run_command_refuses_to_create_formal_data():
    result = subprocess.run([sys.executable, str(SCRIPT), "run"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode != 0
    assert "disabled in Phase 6A" in result.stderr
