from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

import pytest

from raptor_lite.house_sitter import HouseSitterApplication


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_ENV = {**os.environ, "PYTHONPATH": str(ROOT)}
PHASE7_MATERIALIZER = ROOT / "scripts/phase7_results_materializer.py"


def test_detector_boundary_rejects_ground_truth_and_provenance_fields():
    application = HouseSitterApplication("test", 1)
    with pytest.raises(ValueError, match="forbidden ground-truth/provenance"):
        application.observe({"observation_id": "o", "room": "kitchen", "timestamp": 0.0, "observation_valid": True, "visible_object_identifiers": [], "obstacle_present": False, "temperature_c": 20.0, "humidity_percent": 40.0, "transition_accessibility": {}, "scenario_seed": 1}, baseline=True)


def test_corrected_rq3_randomization_is_consumed_and_observations_are_isolated():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/phase6_rq3_validity_audit.py"), "audit"], cwd=ROOT, text=True, capture_output=True, env=SCRIPT_ENV)
    assert result.returncode == 0, result.stderr
    assert '"status": "passed"' in result.stdout


def test_public_phase7_tables_match_frozen_raw_records_and_locked_bootstrap_method():
    result = subprocess.run([sys.executable, str(PHASE7_MATERIALIZER), "audit"], cwd=ROOT, text=True, capture_output=True, env=SCRIPT_ENV)
    assert result.returncode == 0, result.stderr
    assert '"status": "passed"' in result.stdout
