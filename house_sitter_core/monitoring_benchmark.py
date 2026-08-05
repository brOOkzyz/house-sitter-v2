"""Deterministic benchmark runner for the shared house-sitter monitoring chain."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .house_sitter_patrol import PATROL_ORDER, run_house_sitter_patrol
from .simulation_boundary import synthetic_onboard_boundary


BENCHMARK_ARTIFACTS = (
    "monitoring_trials.csv", "monitoring_scenario_results.json", "monitoring_benchmark_summary.json",
    "monitoring_benchmark_summary.md", "monitoring_failures.json", "monitoring_confusion_matrix.csv",
)
REQUIRED_SCENARIO_FIELDS = (
    "scenario_id", "category", "description", "patrol_order", "injected_room", "injected_anomaly_type",
    "injected_values", "expected_anomalies", "expected_alert_count", "expected_changed_twin_fields",
    "expected_false_positive_count", "simulation_only", "real_robot_supported",
)
EXPECTED_CATEGORY_COUNTS = {
    "normal_control": 3, "unexpected_obstacle": 3, "layout_change": 3,
    "temperature_out_of_range": 3, "humidity_out_of_range": 3,
}


class MonitoringBenchmarkError(ValueError):
    """Raised when manual ground truth or benchmark artifact safety is invalid."""


def load_benchmark_scenarios(root: Path) -> list[dict[str, Any]]:
    """Load manually authored ground truth without consulting detector output."""
    path = root / "evaluation" / "monitoring_scenarios_v1.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MonitoringBenchmarkError(f"无法读取监测场景集：{exc}") from exc
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list) or len(scenarios) != 15:
        raise MonitoringBenchmarkError("监测基准必须包含恰好 15 个场景。")
    identifiers = [item.get("scenario_id") for item in scenarios if isinstance(item, dict)]
    if len(identifiers) != 15 or len(set(identifiers)) != 15:
        raise MonitoringBenchmarkError("监测场景标识必须唯一。")
    categories = Counter(item.get("category") for item in scenarios)
    if categories != EXPECTED_CATEGORY_COUNTS:
        raise MonitoringBenchmarkError("五类监测场景必须各有 3 个。")
    for scenario in scenarios:
        if not isinstance(scenario, dict) or any(field not in scenario for field in REQUIRED_SCENARIO_FIELDS):
            raise MonitoringBenchmarkError("监测场景 ground truth 字段不完整。")
        if scenario["patrol_order"] != list(PATROL_ORDER):
            raise MonitoringBenchmarkError("基准场景不得改变锁定的住宅巡逻顺序。")
        if scenario["simulation_only"] is not True or scenario["real_robot_supported"] is not False:
            raise MonitoringBenchmarkError("基准场景缺少 simulation-only 边界。")
        if scenario["category"] == "normal_control" and scenario["expected_anomalies"]:
            raise MonitoringBenchmarkError("normal_control 的 expected_anomalies 必须为空。")
    return scenarios


def _pairs(items: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {(item["room_id"], item["anomaly_type"]) for item in items}


def _expected_fields(scenario: dict[str, Any]) -> set[str]:
    value = scenario["expected_changed_twin_fields"]
    return set(value) if isinstance(value, list) else set()


def score_monitoring_run(scenario: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """Score one detector run against independently authored scenario truth."""
    expected = _pairs(scenario["expected_anomalies"])
    detected = _pairs(result["anomalies"])
    true_positive_pairs = expected & detected
    false_positive_pairs = detected - expected
    false_negative_pairs = expected - detected
    injected_room = scenario["injected_room"]
    updates = {item["room_id"]: set(item["changed_fields"]) for item in result["summary"]["digital_twin_updates"]}
    if expected:
        expected_fields = _expected_fields(scenario)
        actual_fields = updates.get(injected_room, set())
        twin_room_correct = injected_room in updates
        twin_fields_correct = actual_fields == expected_fields
        anomaly_type_correct: bool | None = {pair[1] for pair in true_positive_pairs} == {pair[1] for pair in expected}
        room_localisation_correct: bool | None = {pair[0] for pair in true_positive_pairs} == {pair[0] for pair in expected}
        latency: int | None = min((item["detection_step"] for item in result["anomalies"] if (item["room_id"], item["anomaly_type"]) in true_positive_pairs), default=None)
    else:
        twin_room_correct = None
        twin_fields_correct = None
        anomaly_type_correct = None
        room_localisation_correct = None
        latency = None
    alert_correct = len(result["alerts"]) == scenario["expected_alert_count"]
    return {
        "scenario_id": scenario["scenario_id"], "category": scenario["category"],
        "ground_truth_anomaly_count": len(expected), "detected_anomaly_count": len(detected),
        "true_positive": len(true_positive_pairs), "false_positive": len(false_positive_pairs), "false_negative": len(false_negative_pairs),
        "anomaly_type_correct": anomaly_type_correct, "room_localisation_correct": room_localisation_correct,
        "detection_latency_steps": latency, "digital_twin_room_correct": twin_room_correct,
        "digital_twin_fields_correct": twin_fields_correct, "alert_correct": alert_correct,
        "returned_to_charging_area": result["summary"]["returned_to_charging_area"],
        "expected_false_positive_count": scenario["expected_false_positive_count"],
        **synthetic_onboard_boundary(),
    }


def _normalized(result: dict[str, Any]) -> dict[str, Any]:
    return {key: result[key] for key in ("plan", "observations", "anomalies", "alerts", "after", "summary")}


def evaluate_benchmark(root: Path, repeats: int = 3) -> dict[str, Any]:
    """Run every scenario repeatedly through the one existing monitoring pipeline."""
    if not isinstance(repeats, int) or repeats < 3:
        raise MonitoringBenchmarkError("每个场景至少需要 3 次确定性运行。")
    scenarios = load_benchmark_scenarios(root)
    trials: list[dict[str, Any]] = []
    scenario_results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for scenario in scenarios:
        normalized: list[dict[str, Any]] = []
        scores: list[dict[str, Any]] = []
        for trial_index in range(1, repeats + 1):
            run = run_house_sitter_patrol(root, scenario["scenario_id"])
            normalized.append(_normalized(run))
            score = score_monitoring_run(scenario, run)
            score["trial_index"] = trial_index
            trials.append(score); scores.append(score)
        deterministic = all(item == normalized[0] for item in normalized[1:])
        for score in scores:
            score["deterministic_result"] = deterministic
        representative = dict(scores[0])
        representative.pop("trial_index")
        representative["deterministic_result"] = deterministic
        scenario_results.append(representative)
        failed_fields = [field for field in ("false_positive", "false_negative") if representative[field] != 0]
        for field in ("anomaly_type_correct", "room_localisation_correct", "digital_twin_room_correct", "digital_twin_fields_correct"):
            if representative[field] is False:
                failed_fields.append(field)
        if not representative["alert_correct"]: failed_fields.append("alert_correct")
        if not representative["returned_to_charging_area"]: failed_fields.append("returned_to_charging_area")
        if not deterministic: failed_fields.append("deterministic_result")
        if failed_fields:
            failures.append({"scenario_id": scenario["scenario_id"], "category": scenario["category"], "failed_checks": failed_fields, **synthetic_onboard_boundary()})
    summary = summarize_benchmark(scenario_results, trials, repeats)
    return {"trials": trials, "scenario_results": scenario_results, "summary": summary, "failures": failures}


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def summarize_benchmark(results: list[dict[str, Any]], trials: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    tp, fp, fn = (sum(item[key] for item in results) for key in ("true_positive", "false_positive", "false_negative"))
    precision = _ratio(tp, tp + fp); recall = _ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 6) if precision is not None and recall is not None and precision + recall else None
    positives = [item for item in results if item["ground_truth_anomaly_count"]]
    normal = [item for item in results if not item["ground_truth_anomaly_count"]]
    categories: dict[str, dict[str, Any]] = {}
    for category in EXPECTED_CATEGORY_COUNTS:
        items = [item for item in results if item["category"] == category]
        category_tp, category_fp, category_fn = (sum(item[key] for item in items) for key in ("true_positive", "false_positive", "false_negative"))
        category_precision = _ratio(category_tp, category_tp + category_fp)
        category_recall = _ratio(category_tp, category_tp + category_fn)
        categories[category] = {
            "scenario_count": len(items), "true_positive": category_tp, "false_positive": category_fp, "false_negative": category_fn,
            "precision": category_precision, "recall": category_recall,
            "f1": round(2 * category_precision * category_recall / (category_precision + category_recall), 6) if category_precision is not None and category_recall is not None and category_precision + category_recall else None,
            "recall_handling": "not_applicable" if category == "normal_control" else "applicable",
        }
        if category == "normal_control":
            categories[category]["recall"] = None; categories[category]["f1"] = None
    bool_accuracy = lambda field: _ratio(sum(item[field] is True for item in positives), len(positives))
    latency_values = [item["detection_latency_steps"] for item in positives if item["detection_latency_steps"] is not None]
    return {
        "benchmark_name": "house_v1_monitoring_benchmark_v1", "scenario_count": len(results), "repeat_count": repeats,
        "total_runs": len(trials), "true_positive": tp, "false_positive": fp, "false_negative": fn,
        "anomaly_detection_precision": precision, "anomaly_detection_recall": recall, "anomaly_detection_f1": f1,
        "false_positive_rate": _ratio(fp, len(normal)), "anomaly_type_accuracy": bool_accuracy("anomaly_type_correct"),
        "room_localisation_accuracy": bool_accuracy("room_localisation_correct"),
        "digital_twin_room_update_accuracy": bool_accuracy("digital_twin_room_correct"),
        "digital_twin_field_update_accuracy": bool_accuracy("digital_twin_fields_correct"),
        "mean_detection_latency_steps": round(sum(latency_values) / len(latency_values), 6) if latency_values else None,
        "return_to_charging_success_rate": _ratio(sum(item["returned_to_charging_area"] is True for item in results), len(results)),
        "deterministic_repeat_rate": _ratio(sum(item["deterministic_result"] is True for item in results), len(results)),
        "categories": categories, **synthetic_onboard_boundary(),
    }


def _csv_text(rows: list[dict[str, Any]], fields: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader(); writer.writerows(rows)
    return output.getvalue()


def render_benchmark_artifacts(benchmark: dict[str, Any]) -> dict[str, str]:
    """Render the six complete records before atomic publication."""
    trial_fields = ["scenario_id", "category", "trial_index", "ground_truth_anomaly_count", "detected_anomaly_count", "true_positive", "false_positive", "false_negative", "anomaly_type_correct", "room_localisation_correct", "detection_latency_steps", "digital_twin_room_correct", "digital_twin_fields_correct", "alert_correct", "returned_to_charging_area", "deterministic_result", "synthetic", "simulated_onboard_sensor", "simulation_only", "real_robot_supported"]
    confusion = []
    for item in benchmark["scenario_results"]:
        confusion.append({"scenario_id": item["scenario_id"], "category": item["category"], "TP": item["true_positive"], "FP": item["false_positive"], "FN": item["false_negative"], **synthetic_onboard_boundary()})
    summary = benchmark["summary"]
    lines = ["# house_v1 monitoring benchmark v1", "", "Synthetic deterministic onboard-monitoring benchmark.", ""]
    for category, values in summary["categories"].items():
        lines.extend((f"## {category}", f"- Scenarios: {values['scenario_count']}", f"- TP / FP / FN: {values['true_positive']} / {values['false_positive']} / {values['false_negative']}", f"- Precision / Recall / F1: {values['precision']} / {values['recall']} / {values['f1']}", f"- Recall handling: {values['recall_handling']}", ""))
    lines.extend((f"- Digital Twin room/field accuracy: {summary['digital_twin_room_update_accuracy']} / {summary['digital_twin_field_update_accuracy']}", f"- Mean detection latency: {summary['mean_detection_latency_steps']}", f"- Failures: {len(benchmark['failures'])}", "- synthetic: true", "- simulated_onboard_sensor: true", "- simulation_only: true", "- real_robot_supported: false", ""))
    compact = lambda value: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return {
        "monitoring_trials.csv": _csv_text(benchmark["trials"], trial_fields),
        "monitoring_scenario_results.json": compact({"scenario_results": benchmark["scenario_results"], **synthetic_onboard_boundary()}),
        "monitoring_benchmark_summary.json": compact(summary),
        "monitoring_benchmark_summary.md": "\n".join(lines),
        "monitoring_failures.json": compact({"failures": benchmark["failures"], **synthetic_onboard_boundary()}),
        "monitoring_confusion_matrix.csv": _csv_text(confusion, ["scenario_id", "category", "TP", "FP", "FN", "synthetic", "simulated_onboard_sensor", "simulation_only", "real_robot_supported"]),
    }


def write_benchmark_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    if set(contents) != set(BENCHMARK_ARTIFACTS):
        raise MonitoringBenchmarkError("基准 artifact 集不完整。")
    output = Path(output_dir)
    if output.exists():
        raise MonitoringBenchmarkError(f"输出目录已存在，拒绝覆盖：{output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        for name in BENCHMARK_ARTIFACTS:
            (Path(temporary.name) / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary.name, output)
    finally:
        if temporary is not None: temporary.cleanup()
    return {name: output / name for name in BENCHMARK_ARTIFACTS}
