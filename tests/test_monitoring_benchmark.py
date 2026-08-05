"""Tests for the deterministic multi-anomaly house_v1 monitoring benchmark."""
from __future__ import annotations

import csv
import json
import tempfile
from pathlib import Path

from house_sitter_core.house_sitter_patrol import run_house_sitter_patrol
from house_sitter_core.monitoring_benchmark import (
    BENCHMARK_ARTIFACTS, evaluate_benchmark, load_benchmark_scenarios, render_benchmark_artifacts,
    score_monitoring_run, write_benchmark_artifacts,
)

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = {"synthetic": True, "simulated_onboard_sensor": True, "simulation_only": True, "real_robot_supported": False}


def assert_boundary(record: dict) -> None:
    for field, expected in BOUNDARY.items():
        assert type(record[field]) is bool and record[field] is expected


def test_fifteen_unique_manual_ground_truth_scenarios_have_five_equal_categories():
    scenarios = load_benchmark_scenarios(ROOT)
    assert len(scenarios) == 15
    assert len({item["scenario_id"] for item in scenarios}) == 15
    assert {item["category"] for item in scenarios} == {
        "normal_control", "unexpected_obstacle", "layout_change", "temperature_out_of_range", "humidity_out_of_range",
    }
    assert all(not item["expected_anomalies"] for item in scenarios if item["category"] == "normal_control")
    assert all(item["simulation_only"] is True and item["real_robot_supported"] is False for item in scenarios)


def test_locked_kitchen_baseline_business_result_is_unchanged():
    result = run_house_sitter_patrol(ROOT, "kitchen_unexpected_obstacle")
    assert [(item["room_id"], item["anomaly_type"]) for item in result["anomalies"]] == [("kitchen", "unexpected_obstacle")]
    assert result["summary"]["false_positive_count"] == 0
    assert result["summary"]["returned_to_charging_area"] is True


def test_tp_fp_fn_and_not_applicable_normal_control_scoring_are_correct():
    scenarios = {item["scenario_id"]: item for item in load_benchmark_scenarios(ROOT)}
    positive = score_monitoring_run(scenarios["kitchen_unexpected_obstacle"], run_house_sitter_patrol(ROOT, "kitchen_unexpected_obstacle"))
    normal = score_monitoring_run(scenarios["normal_control_baseline"], run_house_sitter_patrol(ROOT, "normal_control_baseline"))
    assert (positive["true_positive"], positive["false_positive"], positive["false_negative"]) == (1, 0, 0)
    assert normal["ground_truth_anomaly_count"] == 0 and normal["anomaly_type_correct"] is None
    assert normal["room_localisation_correct"] is None and normal["detection_latency_steps"] is None


def test_benchmark_scores_twin_updates_and_three_repeats_deterministically():
    benchmark = evaluate_benchmark(ROOT, repeats=3)
    summary = benchmark["summary"]
    assert summary["scenario_count"] == 15 and summary["total_runs"] == 45
    assert summary["anomaly_detection_precision"] == 1.0
    assert summary["anomaly_detection_recall"] == 1.0
    assert summary["digital_twin_room_update_accuracy"] == 1.0
    assert summary["digital_twin_field_update_accuracy"] == 1.0
    assert summary["deterministic_repeat_rate"] == 1.0
    assert summary["categories"]["normal_control"]["recall"] is None
    assert benchmark["failures"] == []


def test_benchmark_artifacts_are_atomic_complete_and_internally_consistent():
    benchmark = evaluate_benchmark(ROOT, repeats=3)
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "benchmark"
        paths = write_benchmark_artifacts(output, render_benchmark_artifacts(benchmark))
        assert set(paths) == set(BENCHMARK_ARTIFACTS)
        trials = list(csv.DictReader(paths["monitoring_trials.csv"].open(encoding="utf-8", newline="")))
        scenario_document = json.loads(paths["monitoring_scenario_results.json"].read_text(encoding="utf-8"))
        scenarios = scenario_document["scenario_results"]
        summary = json.loads(paths["monitoring_benchmark_summary.json"].read_text(encoding="utf-8"))
        failures = json.loads(paths["monitoring_failures.json"].read_text(encoding="utf-8"))
        assert len(trials) == summary["total_runs"] == 45
        assert len(scenarios) == summary["scenario_count"] == 15
        assert failures["failures"] == []
        for record in scenarios + [scenario_document, summary, failures]:
            assert_boundary(record)
        assert all(row["synthetic"] == "True" and row["real_robot_supported"] == "False" for row in trials)


def test_benchmark_modules_do_not_call_ros_gazebo_network_or_llm():
    sources = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in (
        "house_sitter_core/monitoring_benchmark.py", "scripts/evaluate_monitoring_scenarios.py",
    )).casefold()
    for forbidden in ("import subprocess", "ros2", "gazebo", "nav2", "import requests", "import urllib", "openai", "import llm"):
        assert forbidden not in sources
