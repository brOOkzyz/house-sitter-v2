"""Read frozen experiment artifacts and publish reproducible paper-result material."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROBUSTNESS_ARTIFACTS = (
    "robustness_trials.csv", "robustness_scenario_results.json", "robustness_summary.json", "robustness_failures.json",
    "robustness_twin_field_results.csv", "robustness_temporal_results.csv",
)
TEMPORAL_ARTIFACTS = (
    "temporal_filter_trials.csv", "temporal_filter_paired_results.json", "temporal_filter_summary.json",
    "temporal_filter_failures.json", "temporal_filter_metric_comparison.csv",
)
PATROL_ARTIFACTS = (
    "patrol_strategy_trials.csv", "patrol_strategy_scenario_results.json", "patrol_strategy_summary.json",
    "patrol_strategy_paired_comparison.csv", "patrol_strategy_pareto.csv", "patrol_strategy_failures.json",
)
TABLE_NAMES = (
    "robustness_summary.csv", "robustness_summary.md", "robustness_summary.tex",
    "temporal_filter_comparison.csv", "temporal_filter_comparison.md", "temporal_filter_comparison.tex",
    "patrol_strategy_overall.csv", "patrol_strategy_overall.md", "patrol_strategy_overall.tex",
    "patrol_strategy_by_battery.csv", "patrol_strategy_by_battery.md", "patrol_strategy_by_battery.tex",
    "failure_case_summary.csv", "failure_case_summary.md",
)
FIGURES = (
    "robustness_detection_metrics", "temporal_filter_detection_tradeoff", "temporal_filter_twin_tradeoff",
    "patrol_strategy_coverage_discovery", "patrol_strategy_distance_energy", "patrol_strategy_by_battery",
    "patrol_coverage_energy_pareto",
)
BOUNDARY_FIELDS = ("synthetic", "simulated_onboard_sensor", "simulation_only", "real_robot_supported")


class PaperResultsError(ValueError):
    """Raised when frozen artifacts are missing or internally inconsistent."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PaperResultsError(f"无法读取 JSON artifact {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise PaperResultsError(f"JSON artifact {path.name} 必须是对象。")
    return value


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))
    except OSError as exc:
        raise PaperResultsError(f"无法读取 CSV artifact {path.name}: {exc}") from exc


def _as_float(value: Any) -> float | None:
    if value in {None, "", "None", "null", "N/A"}:
        return None
    return float(value)


def _same(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return abs(float(left) - float(right)) < 1e-6


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _require_artifacts(directory: Path, names: Iterable[str]) -> dict[str, Path]:
    directory = Path(directory)
    missing = [name for name in names if not (directory / name).is_file()]
    if missing:
        raise PaperResultsError(f"artifact 目录 {directory} 缺少必需文件：{', '.join(missing)}")
    return {name: directory / name for name in names}


def _validate_boundary(records: Iterable[dict[str, Any]], source: str) -> None:
    for record in records:
        required_true = all(record.get(field) is True or record.get(field) == "True" for field in BOUNDARY_FIELDS[:3])
        real_robot_false = record.get("real_robot_supported") is False or record.get("real_robot_supported") == "False"
        if not required_true or not real_robot_false:
            raise PaperResultsError(f"{source} 含无效 simulation boundary 字段。")


def load_robustness_artifacts(directory: Path) -> dict[str, Any]:
    paths = _require_artifacts(directory, ROBUSTNESS_ARTIFACTS)
    trials = _read_csv(paths["robustness_trials.csv"])
    results = _read_json(paths["robustness_scenario_results.json"]).get("scenario_results")
    summary = _read_json(paths["robustness_summary.json"])
    failures = _read_json(paths["robustness_failures.json"]).get("failures")
    if not isinstance(results, list) or not isinstance(failures, list) or len(results) != 20 or len(trials) != 100:
        raise PaperResultsError("robustness artifact 的场景或运行数量不符合 20 / 100。")
    _validate_boundary(trials, "robustness trials")
    _validate_boundary(results, "robustness scenario results")
    if summary.get("scenario_count") != 20 or summary.get("total_runs") != 100 or summary.get("repeat_count") != 5:
        raise PaperResultsError("robustness summary 的运行计数不一致。")
    tp, fp, fn = (sum(int(item[key]) for item in results) for key in ("true_positive", "false_positive", "false_negative"))
    precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 6) if precision is not None and recall is not None and precision + recall else None
    for field, expected in (("event_precision", precision), ("event_recall", recall), ("event_f1", f1)):
        if not _same(summary.get(field), expected):
            raise PaperResultsError(f"robustness CSV/JSON 汇总不一致：{field}。")
    by_scenario = Counter(item["scenario_id"] for item in trials)
    if any(count != 5 for count in by_scenario.values()) or set(by_scenario) != {item["scenario_id"] for item in results}:
        raise PaperResultsError("robustness trials 的场景重复数不一致。")
    trial_representatives = {item["scenario_id"]: item for item in trials if item["trial_index"] == "1"}
    for result in results:
        trial = trial_representatives.get(result["scenario_id"])
        if trial is None or any(int(trial[field]) != result[field] for field in ("true_positive", "false_positive", "false_negative")):
            raise PaperResultsError("robustness CSV 与 scenario results 不一致。")
    if not all(item.get("scenario_id") for item in failures):
        raise PaperResultsError("robustness failures 不能删除场景标识。")
    return {"paths": paths, "trials": trials, "results": results, "summary": summary, "failures": failures}


def load_temporal_artifacts(directory: Path) -> dict[str, Any]:
    paths = _require_artifacts(directory, TEMPORAL_ARTIFACTS)
    trials = _read_csv(paths["temporal_filter_trials.csv"])
    paired = _read_json(paths["temporal_filter_paired_results.json"]).get("paired_results")
    summary = _read_json(paths["temporal_filter_summary.json"])
    failures = _read_json(paths["temporal_filter_failures.json"]).get("failures")
    comparison = _read_csv(paths["temporal_filter_metric_comparison.csv"])
    if not isinstance(paired, list) or not isinstance(failures, list) or len(paired) != 20 or len(trials) != 200:
        raise PaperResultsError("temporal artifact 的场景或运行数量不符合 20 / 200。")
    if Counter(item.get("policy") for item in trials) != Counter({"none": 100, "two_observation_confirmation": 100}):
        raise PaperResultsError("temporal artifact 的两种策略运行数不一致。")
    _validate_boundary(trials, "temporal trials")
    _validate_boundary(paired, "temporal paired results")
    for policy in ("pre_filtering", "two_observation_confirmation"):
        if not isinstance(summary.get(policy), dict) or summary[policy].get("scenario_count") != 20 or summary[policy].get("total_runs") != 100:
            raise PaperResultsError("temporal summary 的策略运行计数不一致。")
    rows = {item.get("policy"): item for item in comparison}
    for policy, row_name in (("pre_filtering", "pre_filtering"), ("two_observation_confirmation", "two_observation_confirmation")):
        if row_name not in rows:
            raise PaperResultsError("temporal metric comparison 缺少策略行。")
        for field in ("event_precision", "event_recall", "event_f1", "noise_false_positive_rate", "layout_change_precision", "layout_change_recall", "field_update_precision", "field_update_recall", "unintended_field_update_count", "mean_layout_detection_latency"):
            if not _same(_as_float(rows[row_name].get(field)), summary[policy].get(field)):
                raise PaperResultsError(f"temporal CSV/JSON 汇总不一致：{policy}/{field}。")
    for policy, trial_policy in (("pre_filtering", "none"), ("two_observation_confirmation", "two_observation_confirmation")):
        policy_trials = [item for item in trials if item["policy"] == trial_policy]
        tp, fp, fn = (sum(int(item[field]) for item in policy_trials) for field in ("true_positive", "false_positive", "false_negative"))
        precision, recall = _ratio(tp, tp + fp), _ratio(tp, tp + fn)
        f1 = round(2 * precision * recall / (precision + recall), 6) if precision is not None and recall is not None and precision + recall else None
        for field, value in (("event_precision", precision), ("event_recall", recall), ("event_f1", f1)):
            if not _same(summary[policy].get(field), value):
                raise PaperResultsError(f"temporal trials/summary 不一致：{policy}/{field}。")
    return {"paths": paths, "trials": trials, "paired": paired, "summary": summary, "failures": failures}


def load_patrol_artifacts(directory: Path) -> dict[str, Any]:
    paths = _require_artifacts(directory, PATROL_ARTIFACTS)
    trials = _read_csv(paths["patrol_strategy_trials.csv"])
    scenario_results = _read_json(paths["patrol_strategy_scenario_results.json"]).get("scenario_results")
    summary = _read_json(paths["patrol_strategy_summary.json"])
    failures = _read_json(paths["patrol_strategy_failures.json"]).get("failures")
    paired = _read_csv(paths["patrol_strategy_paired_comparison.csv"])
    pareto = _read_csv(paths["patrol_strategy_pareto.csv"])
    if not isinstance(scenario_results, list) or not isinstance(failures, list) or len(trials) != 270 or len(scenario_results) != 54:
        raise PaperResultsError("patrol artifact 的场景或运行数量不符合 18×3×5=270。")
    if Counter(item.get("strategy") for item in trials) != Counter({"fixed_order": 90, "risk_priority": 90, "battery_aware": 90}):
        raise PaperResultsError("patrol artifact 的策略运行数不一致。")
    if len(paired) != 180 or len(pareto) != 27:
        raise PaperResultsError("patrol paired 或 Pareto artifact 行数不一致。")
    _validate_boundary(trials, "patrol trials")
    _validate_boundary(scenario_results, "patrol scenario results")
    if summary.get("scenario_count") != 18 or summary.get("total_runs") != 270 or summary.get("repeat_count") != 5:
        raise PaperResultsError("patrol summary 的运行计数不一致。")
    overall = {item["strategy"]: item for item in summary.get("overall_by_strategy", [])}
    for strategy in ("fixed_order", "risk_priority", "battery_aware"):
        rows = [item for item in trials if item["strategy"] == strategy]
        coverage = round(sum(float(item["coverage_rate"]) for item in rows) / len(rows), 6)
        if strategy not in overall or not _same(overall[strategy].get("mean_coverage_rate"), coverage):
            raise PaperResultsError(f"patrol CSV/JSON 汇总不一致：{strategy}/coverage。")
    return {"paths": paths, "trials": trials, "scenario_results": scenario_results, "summary": summary,
            "failures": failures, "paired": paired, "pareto": pareto}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _format(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return str(value)


def _table_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _table_markdown(title: str, rows: list[dict[str, Any]], columns: list[str]) -> str:
    lines = [f"# {title}", "", "| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    lines.extend("| " + " | ".join(_format(row.get(column)) for column in columns) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def _latex_escape(value: Any) -> str:
    return _format(value).replace("_", "\\_").replace("%", "\\%")


def _table_tex(caption: str, rows: list[dict[str, Any]], columns: list[str]) -> str:
    align = "l" + "r" * (len(columns) - 1)
    lines = ["\\begin{table}[t]", "\\centering", f"\\caption{{{caption}}}", f"\\begin{{tabular}}{{{align}}}", "\\hline",
             " & ".join(_latex_escape(column) for column in columns) + " \\\\", "\\hline"]
    lines.extend(" & ".join(_latex_escape(row.get(column)) for column in columns) + " \\\\" for row in rows)
    lines.extend(("\\hline", "\\end{tabular}", "\\end{table}", ""))
    return "\n".join(lines)


def _robustness_table(summary: dict[str, Any], failure_count: int) -> tuple[list[dict[str, Any]], list[str]]:
    metrics = (
        ("Precision", "event_precision"), ("Recall", "event_recall"), ("F1", "event_f1"),
        ("Noise false positive rate", "noise_false_positive_rate"), ("Threshold consistency", "threshold_boundary_consistency_rate"),
        ("Missing-data safe handling rate", "missing_data_safe_handling_rate"), ("Combined anomaly exact-set accuracy", "combined_anomaly_exact_set_accuracy"),
        ("Digital Twin field Precision", "field_update_precision"), ("Digital Twin field Recall", "field_update_recall"),
        ("Anomaly resolution accuracy", "anomaly_resolution_accuracy"), ("Stale anomaly rate", "stale_anomaly_rate"),
        ("Deterministic repeat rate", "deterministic_repeat_rate"),
    )
    rows = [{"Metric": label, "Value": summary.get(field)} for label, field in metrics]
    rows.append({"Metric": "Failed scenarios", "Value": failure_count})
    return rows, ["Metric", "Value"]


def _temporal_table(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    fields = (
        ("Precision", "event_precision"), ("Recall", "event_recall"), ("F1", "event_f1"),
        ("Noise false positive rate", "noise_false_positive_rate"), ("Layout-change Precision", "layout_change_precision"),
        ("Layout-change Recall", "layout_change_recall"), ("Twin field Precision", "field_update_precision"),
        ("Twin field Recall", "field_update_recall"), ("Unintended updates", "unintended_field_update_count"),
        ("Combined exact-set accuracy", "combined_anomaly_exact_set_accuracy"), ("Recovery accuracy", "anomaly_resolution_accuracy"),
        ("Stale anomaly rate", "stale_anomaly_rate"), ("Layout detection latency", "mean_layout_detection_latency"),
    )
    before, after = summary["pre_filtering"], summary["two_observation_confirmation"]
    rows = [{"Metric": label, "pre-filtering": before.get(field), "two_observation_confirmation": after.get(field),
             "delta": None if before.get(field) is None or after.get(field) is None else round(after[field] - before[field], 6)}
            for label, field in fields]
    return rows, ["Metric", "pre-filtering", "two_observation_confirmation", "delta"]


def _patrol_overall_table(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows = [{"Strategy": item["strategy"], "Mean coverage": item["mean_coverage_rate"], "Anomaly discovery rate": item["anomaly_discovery_rate"],
             "Mean detection latency": item["mean_detection_latency"], "Mean travel distance": item["mean_travel_distance_m"],
             "Mean simulated energy": item["mean_simulated_energy_consumption"], "Return-to-charging success": item["return_to_charging_success_rate"],
             "Deterministic repeat rate": item["deterministic_repeat_rate"]} for item in summary["overall_by_strategy"]]
    return rows, list(rows[0])


def _patrol_battery_table(summary: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows = [{"Battery level": item["battery_level"], "Strategy": item["strategy"], "Coverage": item["mean_coverage_rate"],
             "Anomaly discovery": item["anomaly_discovery_rate"], "Latency": item["mean_detection_latency"],
             "Distance": item["mean_travel_distance_m"], "Energy": item["mean_simulated_energy_consumption"],
             "Mean skipped rooms": round(item["rooms_skipped"] / item["run_count"], 6), "Return success": item["return_to_charging_success_rate"]}
            for item in summary["by_strategy_and_battery"]]
    return rows, list(rows[0])


def _failure_rows(robustness: dict[str, Any], temporal: dict[str, Any], patrol: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows = []
    for item in robustness["failures"]:
        interpretation = "Transient layout_signature perturbation false positive" if item.get("category") == "sensor_noise" else "Robustness benchmark failure retained"
        rows.append({"Experiment": "robustness", "Case": item["scenario_id"], "Failure or limitation": interpretation,
                     "Details": ", ".join(item.get("failed_checks", []))})
    paired_categories = {item["scenario_id"]: item.get("category", "unknown") for item in temporal["paired"]}
    for item in temporal["failures"]:
        category = paired_categories.get(item["scenario_id"], "unknown")
        if category == "combined_anomalies":
            interpretation = "Single-observation combined layout-change miss retained"
        elif category == "anomaly_recovery":
            interpretation = "Recovery confirmation delay or incomplete recovery retained"
        else:
            interpretation = "Temporal filtering failure retained"
        rows.append({"Experiment": "temporal_filtering", "Case": item["scenario_id"], "Failure or limitation": interpretation,
                     "Details": ", ".join(item.get("failed_checks", []))})
    policy_misses = sum(int(item["missed_anomaly_count"]) for item in patrol["trials"])
    detector_fn = sum(int(item["detector_false_negative_count"]) for item in patrol["trials"])
    rows.append({"Experiment": "patrol_strategy", "Case": "all trials", "Failure or limitation": "Missed anomalies in unvisited rooms (policy miss)",
                 "Details": f"missed_due_to_patrol_policy={policy_misses}"})
    rows.append({"Experiment": "patrol_strategy", "Case": "all trials", "Failure or limitation": "Detector false negatives after room visit",
                 "Details": f"detector_false_negative={detector_fn}"})
    return rows, ["Experiment", "Case", "Failure or limitation", "Details"]


def _save_figure(figure: Any, directory: Path, name: str) -> None:
    figure.tight_layout()
    figure.savefig(directory / f"{name}.png", dpi=300)
    figure.savefig(directory / f"{name}.pdf")
    figure.clf()


def _generate_figures(figures_dir: Path, robustness: dict[str, Any], temporal: dict[str, Any], patrol: dict[str, Any]) -> tuple[str, list[str]]:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:
        return f"unavailable: {exc}", []
    generated: list[str] = []
    def finish(figure: Any, name: str) -> None:
        _save_figure(figure, figures_dir, name)
        generated.extend((f"{name}.png", f"{name}.pdf"))

    robust = robustness["summary"]
    labels = ["Precision", "Recall", "F1", "Twin field Precision", "Twin field Recall"]
    values = [robust["event_precision"], robust["event_recall"], robust["event_f1"], robust["field_update_precision"], robust["field_update_recall"]]
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar(labels, values, color="#3b6ea5")
    axis.set_title("Robustness Detection Metrics"); axis.set_ylabel("Rate"); axis.set_ylim(0, 1.05)
    axis.tick_params(axis="x", rotation=20)
    finish(figure, "robustness_detection_metrics")

    before, after = temporal["summary"]["pre_filtering"], temporal["summary"]["two_observation_confirmation"]
    labels = ["Precision", "Recall", "F1", "Layout-change Recall"]
    keys = ["event_precision", "event_recall", "event_f1", "layout_change_recall"]
    positions = list(range(len(labels))); width = 0.36
    figure, axis = plt.subplots(figsize=(8, 4.5))
    axis.bar([x - width / 2 for x in positions], [before[key] for key in keys], width, label="pre-filtering", color="#4c78a8")
    axis.bar([x + width / 2 for x in positions], [after[key] for key in keys], width, label="two_observation_confirmation", color="#f58518")
    axis.set_title("Temporal Filtering Detection Trade-off"); axis.set_ylabel("Rate"); axis.set_ylim(0, 1.05)
    axis.set_xticks(positions, labels); axis.legend()
    finish(figure, "temporal_filter_detection_tradeoff")

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    rate_labels, rate_keys = ["Twin Precision", "Twin Recall"], ["field_update_precision", "field_update_recall"]
    axes[0].bar([x - width / 2 for x in range(2)], [before[key] for key in rate_keys], width, label="pre-filtering", color="#4c78a8")
    axes[0].bar([x + width / 2 for x in range(2)], [after[key] for key in rate_keys], width, label="two_observation_confirmation", color="#f58518")
    axes[0].set_title("Twin Field Rates"); axes[0].set_ylabel("Rate"); axes[0].set_ylim(0, 1.05); axes[0].set_xticks(range(2), rate_labels); axes[0].legend()
    count_labels, count_keys = ["Unintended updates", "Confirmation latency"], ["unintended_field_update_count", "mean_layout_detection_latency"]
    axes[1].bar([x - width / 2 for x in range(2)], [before[key] for key in count_keys], width, label="pre-filtering", color="#4c78a8")
    axes[1].bar([x + width / 2 for x in range(2)], [after[key] for key in count_keys], width, label="two_observation_confirmation", color="#f58518")
    axes[1].set_title("Twin Updates and Latency"); axes[1].set_ylabel("Count or steps"); axes[1].set_xticks(range(2), count_labels); axes[1].legend()
    figure.suptitle("Temporal Filtering Twin Trade-off")
    finish(figure, "temporal_filter_twin_tradeoff")

    overall = patrol["summary"]["overall_by_strategy"]
    strategies = [item["strategy"] for item in overall]
    figure, axis = plt.subplots(figsize=(7.5, 4.5))
    axis.bar([x - width / 2 for x in range(3)], [item["mean_coverage_rate"] for item in overall], width, label="Coverage", color="#54a24b")
    axis.bar([x + width / 2 for x in range(3)], [item["anomaly_discovery_rate"] for item in overall], width, label="Anomaly discovery", color="#e45756")
    axis.set_title("Patrol Coverage and Discovery"); axis.set_ylabel("Rate"); axis.set_ylim(0, 1.05); axis.set_xticks(range(3), strategies); axis.legend()
    finish(figure, "patrol_strategy_coverage_discovery")

    figure, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    axes[0].bar(strategies, [item["mean_travel_distance_m"] for item in overall], color="#4c78a8")
    axes[0].set_title("Patrol Travel Distance"); axes[0].set_ylabel("Distance (m)")
    axes[1].bar(strategies, [item["mean_simulated_energy_consumption"] for item in overall], color="#f58518")
    axes[1].set_title("Patrol Simulated Energy"); axes[1].set_ylabel("Energy units")
    figure.suptitle("Patrol Distance and Energy")
    finish(figure, "patrol_strategy_distance_energy")

    battery_rows = patrol["summary"]["by_strategy_and_battery"]
    levels = ["high_battery", "medium_battery", "constrained_battery"]
    figure, axis = plt.subplots(figsize=(8, 4.5))
    for index, strategy in enumerate(strategies):
        values = [next(item["mean_coverage_rate"] for item in battery_rows if item["battery_level"] == level and item["strategy"] == strategy) for level in levels]
        axis.bar([x + (index - 1) * 0.24 for x in range(3)], values, 0.24, label=strategy)
    axis.set_title("Patrol Coverage by Battery Level"); axis.set_ylabel("Coverage rate"); axis.set_ylim(0, 1.05); axis.set_xticks(range(3), levels); axis.legend()
    finish(figure, "patrol_strategy_by_battery")

    pareto_rows = [item for item in patrol["pareto"] if item["pareto_analysis"] == "coverage_vs_energy"]
    figure, axis = plt.subplots(figsize=(7, 4.5))
    colors = {"fixed_order": "#4c78a8", "risk_priority": "#f58518", "battery_aware": "#54a24b"}
    for strategy in strategies:
        points = [item for item in pareto_rows if item["strategy"] == strategy]
        axis.scatter([float(item["mean_simulated_energy_consumption"]) for item in points], [float(item["mean_coverage_rate"]) for item in points],
                     label=strategy, color=colors[strategy], marker="o")
        for point in points:
            if point["pareto_optimal"] == "True":
                axis.annotate(point["battery_level"].replace("_battery", ""), (float(point["mean_simulated_energy_consumption"]), float(point["mean_coverage_rate"])))
    axis.set_title("Patrol Coverage-Energy Pareto"); axis.set_xlabel("Simulated energy"); axis.set_ylabel("Coverage rate"); axis.legend()
    finish(figure, "patrol_coverage_energy_pareto")
    return f"matplotlib {matplotlib.__version__}", generated


def _chapter(robustness: dict[str, Any], temporal: dict[str, Any], patrol: dict[str, Any], figures_available: bool) -> str:
    robust, filter_summary, patrol_summary = robustness["summary"], temporal["summary"], patrol["summary"]
    overall = {item["strategy"]: item for item in patrol_summary["overall_by_strategy"]}
    figure_note = "Figures are generated from the corresponding artifact-derived tables." if figures_available else "Figure generation was skipped because matplotlib is unavailable; tables remain the authoritative output."
    return f"""# Experimental Results

## Controlled Functional Validation

The earlier benchmark v1 established controlled end-to-end functional behavior. This chapter reports the subsequent deterministic simulation evaluations without treating repeated deterministic executions as independent random samples.

## Monitoring Robustness

Across {robust['total_runs']} deterministic runs over {robust['scenario_count']} scenarios, Recall was {robust['event_recall']} and Precision was {robust['event_precision']} in the deterministic simulation. The retained transient layout-signature perturbation false positive accounts for the observed precision limitation and also affects Digital Twin field updates. Missing-data handling, combined-anomaly evaluation, and anomaly recovery are reported directly in Table 1. This deterministic evaluation does not establish real-world performance.

## Temporal Filtering Trade-off

Each temporal policy ran {filter_summary['pre_filtering']['total_runs']} times under the evaluated scenarios. Two-observation confirmation removes transient-noise false positives and associated unintended Twin updates, while the artifact-derived table records lower Recall, lower combined-event and recovery performance where present, and higher layout detection latency. The results suggest a Precision–Recall–Latency trade-off rather than a universally better filtered policy.

## Patrol Strategy Trade-off

The patrol study comprises {patrol_summary['scenario_count']} scenarios and {patrol_summary['total_runs']} deterministic runs. In the deterministic simulation, battery_aware reached coverage {overall['battery_aware']['mean_coverage_rate']} and anomaly discovery {overall['battery_aware']['anomaly_discovery_rate']} with mean simulated energy {overall['battery_aware']['mean_simulated_energy_consumption']}. risk_priority used lower distance and energy ({overall['risk_priority']['mean_travel_distance_m']} m; {overall['risk_priority']['mean_simulated_energy_consumption']} units) while reducing spatial coverage. fixed_order is the predictable intermediate baseline. All three policies returned safely to charging under the evaluated scenarios; missed anomalies primarily arise when rooms are not visited.

## Summary of Findings

In the deterministic simulation, the three studies provide controlled trade-off evidence only. {figure_note} No p-values, confidence intervals, error bars, or claims of statistical significance are reported because deterministic repeats validate reproducibility rather than real-world statistical generalization.

Figure captions: Figure 1 reports robustness detection and Twin-field rates; Figure 2 reports the temporal detection trade-off; Figure 3 separates Twin rates from update/latency quantities; Figures 4–7 report patrol coverage/discovery, distance/energy in separate panels, battery-level coverage, and the artifact-provided coverage–energy Pareto points.
"""


def _limitations() -> str:
    return """# Limitations and Threats to Validity

- All three evaluations are simulation-only and use synthetic sensor observations.
- Energy and battery values are deterministic simulation models, not measurements from TurtleBot4, commercial robot vacuums, or other physical robots.
- The study uses one house_v1 layout and fixed accepted safe goals.
- The simulation does not include real localization drift, wheel slip, dynamic people, sensor noise beyond the designed synthetic cases, or physical battery degradation.
- Repeated runs are deterministic reproducibility checks, not independent randomized experiments; a 100% deterministic repeat rate must not be interpreted as real-world robustness.
- Patrol risk scores are pre-defined priors and do not encode the injected anomaly location.
- The Digital Twin is a simplified state representation.
- Warehouse Gazebo/Nav2 execution evidence and the house_v1 two-dimensional monitoring experiments use different environments.
- Results support only deterministic-simulation trade-off analysis and do not establish real-world performance. No single patrol strategy dominated across all evaluated objectives.
"""


def _source_commit(root: Path) -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _regenerate_sources(root: Path, temporary_root: Path) -> dict[str, Path]:
    """Call the frozen evaluation entry points, publishing only into a temporary source area."""
    from .monitoring_robustness_evaluation import evaluate_robustness, render_robustness_artifacts, write_robustness_artifacts
    from .temporal_filter_comparison import compare, render as render_temporal, write as write_temporal
    from .patrol_strategy_evaluation import evaluate_patrol_strategies, render_patrol_strategy_artifacts, write_patrol_strategy_artifacts

    destinations = {"robustness": temporary_root / "robustness", "temporal": temporary_root / "temporal", "patrol": temporary_root / "patrol"}
    robustness = evaluate_robustness(root, repeats=5)
    write_robustness_artifacts(destinations["robustness"], render_robustness_artifacts(robustness))
    temporal = compare(root, repeats=5)
    write_temporal(destinations["temporal"], render_temporal(temporal))
    patrol = evaluate_patrol_strategies(root, repeats=5)
    write_patrol_strategy_artifacts(destinations["patrol"], render_patrol_strategy_artifacts(patrol))
    return destinations


def _table_contents(robustness: dict[str, Any], temporal: dict[str, Any], patrol: dict[str, Any]) -> tuple[dict[str, str], dict[str, list[dict[str, Any]]]]:
    robust_rows, robust_columns = _robustness_table(robustness["summary"], len(robustness["failures"]))
    temporal_rows, temporal_columns = _temporal_table(temporal["summary"])
    patrol_rows, patrol_columns = _patrol_overall_table(patrol["summary"])
    battery_rows, battery_columns = _patrol_battery_table(patrol["summary"])
    failure_rows, failure_columns = _failure_rows(robustness, temporal, patrol)
    definitions = {
        "robustness_summary": ("Robustness benchmark", robust_rows, robust_columns),
        "temporal_filter_comparison": ("Temporal filtering paired comparison", temporal_rows, temporal_columns),
        "patrol_strategy_overall": ("Patrol strategy overall results", patrol_rows, patrol_columns),
        "patrol_strategy_by_battery": ("Patrol strategy by battery level", battery_rows, battery_columns),
        "failure_case_summary": ("Failure cases and limitations", failure_rows, failure_columns),
    }
    contents: dict[str, str] = {}
    source_rows: dict[str, list[dict[str, Any]]] = {}
    for name, (title, rows, columns) in definitions.items():
        contents[f"tables/{name}.csv"] = _table_csv(rows, columns)
        contents[f"tables/{name}.md"] = _table_markdown(title, rows, columns)
        source_rows[name] = rows
        if name != "failure_case_summary":
            contents[f"tables/{name}.tex"] = _table_tex(title, rows, columns)
    return contents, source_rows


def _manifest(root: Path, sources: dict[str, Path], regeneration: bool, robustness: dict[str, Any], temporal: dict[str, Any], patrol: dict[str, Any], figure_generator: str) -> dict[str, Any]:
    source_artifacts = {}
    for group, loaded in (("robustness", robustness), ("temporal", temporal), ("patrol", patrol)):
        source_artifacts[group] = {name: _sha256(path) for name, path in loaded["paths"].items()}
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_commit": _source_commit(root),
        "source_experiment_directories": {name: str(path) for name, path in sources.items()},
        "input_artifact_sha256": source_artifacts,
        "experiments": {
            "robustness": {"scenario_count": robustness["summary"]["scenario_count"], "run_count": robustness["summary"]["total_runs"]},
            "temporal_filtering": {"scenario_count": 20, "run_count": len(temporal["trials"]), "runs_per_policy": 100},
            "patrol_strategy": {"scenario_count": patrol["summary"]["scenario_count"], "run_count": patrol["summary"]["total_runs"]},
        },
        "regeneration_enabled": regeneration,
        "figure_generator": figure_generator,
        "simulation_only": True,
        "real_robot_supported": False,
        "statistical_inference_performed": False,
    }


def _render_paper_contents(root: Path, sources: dict[str, Path], regeneration: bool) -> dict[str, str]:
    robustness = load_robustness_artifacts(sources["robustness"])
    temporal = load_temporal_artifacts(sources["temporal"])
    patrol = load_patrol_artifacts(sources["patrol"])
    tables, table_rows = _table_contents(robustness, temporal, patrol)
    contents: dict[str, str] = dict(tables)
    figures_available = _matplotlib_available()
    figure_note = "matplotlib available" if figures_available else "matplotlib unavailable; figures skipped"
    manifest = _manifest(root, sources, regeneration, robustness, temporal, patrol, figure_note)
    summary = {
        "research_positioning": {
            "robustness": "Deterministic robustness and temporal Digital Twin evaluation",
            "temporal_filtering": "Precision–Recall–Latency trade-off",
            "patrol_strategy": "Untuned deterministic patrol-strategy baseline",
        },
        "robustness": robustness["summary"], "temporal_filtering": temporal["summary"], "patrol_strategy": patrol["summary"],
        "table_rows": table_rows, "statistical_inference_performed": False,
        "simulation_only": True, "real_robot_supported": False,
    }
    contents["results_manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    contents["results_summary.json"] = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    contents["results_chapter_draft.md"] = _chapter(robustness, temporal, patrol, figures_available)
    contents["limitations_and_threats.md"] = _limitations()
    return contents


def _matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def write_paper_results(output_dir: Path, contents: dict[str, str], sources: dict[str, Path], regeneration: bool, root: Path) -> dict[str, Path]:
    output = Path(output_dir)
    if output.exists():
        raise PaperResultsError(f"输出目录已存在，拒绝覆盖：{output}")
    temporary = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        temporary_path = Path(temporary.name)
        for relative, text in contents.items():
            path = temporary_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="")
        figures_path = temporary_path / "figures"
        figures_path.mkdir(exist_ok=True)
        robustness = load_robustness_artifacts(sources["robustness"])
        temporal = load_temporal_artifacts(sources["temporal"])
        patrol = load_patrol_artifacts(sources["patrol"])
        generator, generated = _generate_figures(figures_path, robustness, temporal, patrol)
        if generated:
            manifest_path = temporary_path / "results_manifest.json"
            manifest = _read_json(manifest_path)
            manifest["figure_generator"] = generator
            manifest["generated_figures"] = generated
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        else:
            (temporary_path / "figures" / "DEPENDENCY_UNAVAILABLE.md").write_text(
                f"# Figure generation unavailable\n\n{generator}\nTables and Markdown were generated without synthetic images.\n", encoding="utf-8")
        os.replace(temporary.name, output)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return {relative: output / relative for relative in contents}


def build_paper_results(root: Path, output_dir: Path, *, robustness_dir: Path | None = None, temporal_dir: Path | None = None,
                        patrol_dir: Path | None = None, regenerate: bool = False) -> dict[str, Path]:
    root = Path(root).resolve()
    if regenerate:
        if any(path is not None for path in (robustness_dir, temporal_dir, patrol_dir)):
            raise PaperResultsError("--regenerate 不能与已有 artifact 目录混用。")
        with tempfile.TemporaryDirectory(prefix="house-sitter-paper-result-sources-") as temporary:
            sources = _regenerate_sources(root, Path(temporary))
            contents = _render_paper_contents(root, sources, True)
            return write_paper_results(output_dir, contents, sources, True, root)
    if any(path is None for path in (robustness_dir, temporal_dir, patrol_dir)):
        raise PaperResultsError("必须同时提供三组 artifact 目录，或使用 --regenerate。")
    sources = {"robustness": Path(robustness_dir), "temporal": Path(temporal_dir), "patrol": Path(patrol_dir)}
    contents = _render_paper_contents(root, sources, False)
    return write_paper_results(output_dir, contents, sources, False, root)
