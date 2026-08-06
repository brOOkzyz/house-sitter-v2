"""Fair, local and deterministic house_v1 patrol strategy evaluation."""
from __future__ import annotations

import csv
import io
import json
import os
import statistics
import tempfile
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any

from .digital_twin import create_house_v1_baseline, room_index, update_room_from_observation
from .environment_monitoring import detect_anomalies
from .house_sitter_patrol import load_house_v1_monitoring_inputs
from .patrol_strategies import (
    CHARGING_ROOM, FIXED_ORDER, PATROL_ROOMS, STRATEGIES, EnergyModel, PatrolMap,
    choose_battery_aware_room, energy_for_distance, fixed_order_rooms, load_patrol_map,
    required_visit_energy, risk_priority_rooms,
)
from .simulated_onboard_sensors import observe_room
from .simulation_boundary import synthetic_onboard_boundary


ARTIFACTS = (
    "patrol_strategy_trials.csv", "patrol_strategy_scenario_results.json", "patrol_strategy_summary.json",
    "patrol_strategy_summary.md", "patrol_strategy_paired_comparison.csv", "patrol_strategy_pareto.csv",
    "patrol_strategy_failures.json", "patrol_route_traces.jsonl",
)
MONITORING_CASES = {
    "no_anomaly", "kitchen_unexpected_obstacle", "bedroom_temperature_anomaly",
    "bathroom_humidity_anomaly", "living_room_layout_change", "multi_room_combined_anomalies",
}
BATTERY_LEVELS = {"high_battery", "medium_battery", "constrained_battery"}


class PatrolStrategyEvaluationError(ValueError):
    """Raised when a local patrol experiment is malformed or unsafe to publish."""


def load_patrol_strategy_scenarios(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(root) / "evaluation" / "patrol_strategy_scenarios_v1.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PatrolStrategyEvaluationError(f"无法读取巡逻策略场景：{exc}") from exc
    scenarios = document.get("scenarios")
    if not isinstance(scenarios, list):
        raise PatrolStrategyEvaluationError("巡逻策略场景必须含 scenarios 列表。")
    _validate_scenarios(document, scenarios)
    return document, scenarios


def _validate_scenarios(document: dict[str, Any], scenarios: list[dict[str, Any]]) -> None:
    if document.get("layout_filter_policy") != "none":
        raise PatrolStrategyEvaluationError("本实验只允许 layout_filter_policy=none。")
    if len(scenarios) != 18:
        raise PatrolStrategyEvaluationError("巡逻策略实验必须恰有 18 个场景。")
    ids = [item.get("scenario_id") for item in scenarios if isinstance(item, dict)]
    if len(ids) != len(scenarios) or len(set(ids)) != 18:
        raise PatrolStrategyEvaluationError("scenario_id 必须唯一。")
    combinations = {(item.get("monitoring_case"), _battery_level(item)) for item in scenarios}
    if combinations != {(case, level) for case in MONITORING_CASES for level in BATTERY_LEVELS}:
        raise PatrolStrategyEvaluationError("六种监测情况与三种电量等级必须完整配对。")
    profiles = document.get("risk_profiles")
    models = document.get("energy_models")
    if not isinstance(profiles, dict) or not isinstance(models, dict):
        raise PatrolStrategyEvaluationError("场景文件缺少集中定义的 risk_profiles 或 energy_models。")
    for scenario in scenarios:
        required = {"scenario_id", "monitoring_case", "initial_battery_units", "safety_reserve_units", "risk_profile_id",
                    "room_risk_scores", "injected_events", "expected_ground_truth_events", "patrol_start_room",
                    "charging_room", "energy_model_id", "deterministic_profile_id", "simulation_only", "real_robot_supported"}
        if not isinstance(scenario, dict) or not required.issubset(scenario):
            raise PatrolStrategyEvaluationError("场景缺少必填字段。")
        if scenario.get("layout_filter_policy") != "none" or scenario["patrol_start_room"] != CHARGING_ROOM or scenario["charging_room"] != CHARGING_ROOM:
            raise PatrolStrategyEvaluationError("场景必须从 charging_area 出发并使用未过滤的监测逻辑。")
        if scenario.get("simulation_only") is not True or scenario.get("real_robot_supported") is not False:
            raise PatrolStrategyEvaluationError("场景必须标注 simulation_only=true、real_robot_supported=false。")
        profile_id = scenario["risk_profile_id"]
        if profile_id not in profiles or scenario["room_risk_scores"] != profiles[profile_id]:
            raise PatrolStrategyEvaluationError("场景风险分数必须引用预定义 risk profile。")
        if scenario["energy_model_id"] not in models:
            raise PatrolStrategyEvaluationError("场景引用了未知 energy model。")
        if not isinstance(scenario["injected_events"], list) or not isinstance(scenario["expected_ground_truth_events"], list):
            raise PatrolStrategyEvaluationError("异常注入与 ground truth 必须为人工定义的列表。")
        injected = {(event.get("event_id"), event.get("room_id"), event.get("anomaly_type")) for event in scenario["injected_events"]}
        expected = {(event.get("event_id"), event.get("room_id"), event.get("anomaly_type")) for event in scenario["expected_ground_truth_events"]}
        if injected != expected:
            raise PatrolStrategyEvaluationError("injected_events 与人工 expected_ground_truth_events 不一致。")
        if any(room not in PATROL_ROOMS for _, room, _ in expected):
            raise PatrolStrategyEvaluationError("ground truth 只能引用可巡逻房间。")
        if any(key in scenario for key in ("injected_room", "expected_anomalies")):
            raise PatrolStrategyEvaluationError("策略场景不得使用单房间 ground-truth 快捷字段。")


def _battery_level(scenario: dict[str, Any]) -> str:
    return str(scenario["scenario_id"]).rsplit("_", 2)[-2] + "_battery" if str(scenario["scenario_id"]).endswith("_battery") else next(
        level for level in BATTERY_LEVELS if level.replace("_battery", "") in str(scenario["scenario_id"])
    )


def _energy_model(document: dict[str, Any], scenario: dict[str, Any]) -> EnergyModel:
    record = document["energy_models"][scenario["energy_model_id"]]
    return EnergyModel(scenario["energy_model_id"], float(record["travel_energy_per_meter"]),
                       float(record["sensing_energy_per_room"]), float(record["fixed_task_overhead"]))


def _event_observation_inputs(scenario: dict[str, Any], room_id: str) -> tuple[bool, dict[str, Any] | None]:
    events = [event for event in scenario["injected_events"] if event["room_id"] == room_id]
    if not events:
        return False, None
    values: dict[str, Any] = {}
    unexpected = False
    for event in events:
        unexpected = unexpected or event.get("unexpected_obstacle") is True
        values.update(event.get("injected_values", {}))
    return unexpected, values


def _round(value: float) -> float:
    return round(value, 6)


def run_patrol_strategy_trial(
    root: Path,
    document: dict[str, Any],
    scenario: dict[str, Any],
    strategy: str,
    repeat_index: int,
    patrol_map: PatrolMap,
    regions_document: dict[str, Any],
) -> dict[str, Any]:
    """Run one policy.  Its selection functions receive no injected-event data."""
    if strategy not in STRATEGIES:
        raise PatrolStrategyEvaluationError("未知巡逻策略。")
    model = _energy_model(document, scenario)
    initial = float(scenario["initial_battery_units"])
    reserve = float(scenario["safety_reserve_units"])
    remaining = initial - model.fixed_task_overhead
    if remaining < reserve:
        raise PatrolStrategyEvaluationError("启动开销后无法保留安全返航储备。")
    risks = deepcopy(scenario["room_risk_scores"])
    before = create_house_v1_baseline(regions_document)
    baseline = room_index(before)
    after = before
    current = scenario["patrol_start_room"]
    unvisited = list(PATROL_ROOMS)
    visited: list[str] = []
    observations: list[dict[str, Any]] = []
    detections: list[dict[str, Any]] = []
    route_segments: list[dict[str, Any]] = []
    fixed_or_risk_order = fixed_order_rooms() if strategy == "fixed_order" else (
        risk_priority_rooms(current, risks, patrol_map) if strategy == "risk_priority" else ())
    position = 0
    while unvisited:
        if strategy == "battery_aware":
            candidate = choose_battery_aware_room(current, remaining, tuple(unvisited), risks, patrol_map, model, reserve)
        else:
            candidate = fixed_or_risk_order[position] if position < len(fixed_or_risk_order) else None
            position += 1
            if candidate is not None and remaining + 1e-9 < required_visit_energy(current, candidate, patrol_map, model, reserve):
                candidate = None
        if candidate is None:
            break
        distance = patrol_map.distance_m(current, candidate)
        travel = energy_for_distance(distance, model)
        remaining -= travel + model.sensing_energy_per_room
        route_segments.append({"from_room": current, "to_room": candidate, "distance_m": _round(distance),
                               "path_cell_count": len(patrol_map.cells(current, candidate)), "purpose": "observe"})
        current = candidate
        unvisited.remove(candidate)
        visited.append(candidate)
        step = len(visited)
        unexpected, injected_values = _event_observation_inputs(scenario, candidate)
        observation = observe_room(candidate, step, baseline[candidate], unexpected_obstacle=unexpected, injected_values=injected_values)
        found = detect_anomalies(observation, baseline[candidate])
        after, _ = update_room_from_observation(after, observation, found)
        observations.append(observation)
        detections.extend(found)
    return_distance = patrol_map.distance_m(current, CHARGING_ROOM)
    return_energy = energy_for_distance(return_distance, model)
    if remaining + 1e-9 < return_energy + reserve:
        raise PatrolStrategyEvaluationError("策略未能保留返航能量；拒绝发布不安全结果。")
    remaining -= return_energy
    route_segments.append({"from_room": current, "to_room": CHARGING_ROOM, "distance_m": _round(return_distance),
                           "path_cell_count": len(patrol_map.cells(current, CHARGING_ROOM)), "purpose": "return"})
    expected = scenario["expected_ground_truth_events"]
    detected_keys = {(item["room_id"], item["anomaly_type"]) for item in detections}
    detected_events = [event for event in expected if (event["room_id"], event["anomaly_type"]) in detected_keys]
    missed_events = []
    false_negative_events = []
    latencies: dict[str, int | None] = {}
    for event in expected:
        if event in detected_events:
            latencies[event["event_id"]] = visited.index(event["room_id"]) + 1
        elif event["room_id"] not in visited:
            missed_events.append({**event, "missed_due_to_patrol_policy": True})
            latencies[event["event_id"]] = None
        else:
            false_negative_events.append({**event, "missed_due_to_patrol_policy": False})
            latencies[event["event_id"]] = None
    total_distance = sum(segment["distance_m"] for segment in route_segments)
    travel_energy = total_distance * model.travel_energy_per_meter
    sensing_energy = len(visited) * model.sensing_energy_per_room
    total_energy = model.fixed_task_overhead + travel_energy + sensing_energy
    coverage = len(visited) / len(PATROL_ROOMS)
    risk_weighted = sum(risks[room] for room in visited) / sum(risks.values())
    detected_latencies = [value for value in latencies.values() if value is not None]
    battery_level = _battery_level(scenario)
    return {
        "scenario_id": scenario["scenario_id"], "monitoring_case": scenario["monitoring_case"], "battery_level": battery_level,
        "repeat_index": repeat_index, "strategy": strategy, "layout_filter_policy": "none",
        "initial_battery": initial, "final_battery": _round(remaining), "visited_rooms": visited,
        "skipped_rooms": unvisited, "visit_order": visited + [CHARGING_ROOM], "formal_fixed_order": list(FIXED_ORDER),
        "route_segments": route_segments, "total_distance_m": _round(total_distance), "travel_energy": _round(travel_energy),
        "sensing_energy": _round(sensing_energy), "fixed_task_overhead": model.fixed_task_overhead,
        "total_energy_consumed": _round(total_energy), "coverage_rate": _round(coverage),
        "risk_weighted_coverage": _round(risk_weighted), "ground_truth_anomaly_count": len(expected),
        "detected_anomaly_count": len(detected_events), "missed_anomaly_count": len(missed_events),
        "detector_false_negative_count": len(false_negative_events), "missed_events": missed_events,
        "detector_false_negative_events": false_negative_events, "detection_latency_by_anomaly": latencies,
        "mean_detection_latency": _round(sum(detected_latencies) / len(detected_latencies)) if detected_latencies else None,
        "returned_to_charging_area": True, "return_reserve_satisfied": remaining + 1e-9 >= reserve,
        "route_completed": len(visited) == len(PATROL_ROOMS), "deterministic_result": True,
        "energy_model_id": model.energy_model_id, "risk_profile_id": scenario["risk_profile_id"],
        "sensor_observation_profile_id": scenario["deterministic_profile_id"], "observations": observations,
        "detected_anomalies": detections, "digital_twin_after": after, **synthetic_onboard_boundary(),
    }


def evaluate_patrol_strategies(root: Path, repeats: int = 5) -> dict[str, Any]:
    if repeats < 1:
        raise PatrolStrategyEvaluationError("repeats 至少为 1。")
    document, scenarios = load_patrol_strategy_scenarios(root)
    regions_document, _ = load_house_v1_monitoring_inputs(root)
    patrol_map = load_patrol_map(root)
    trials: list[dict[str, Any]] = []
    for scenario in scenarios:
        for repeat in range(1, repeats + 1):
            for strategy in STRATEGIES:
                trials.append(run_patrol_strategy_trial(root, document, scenario, strategy, repeat, patrol_map, regions_document))
    _mark_deterministic(trials)
    scenario_results = _scenario_results(trials)
    summary = _summarize(trials, repeats)
    paired = _paired_comparisons(trials)
    pareto = _pareto_rows(summary["by_strategy_and_battery"])
    failures = _failures(trials)
    return {"trials": trials, "scenario_results": scenario_results, "summary": summary, "paired": paired,
            "pareto": pareto, "failures": failures, "repeats": repeats}


def _normalized_trial(trial: dict[str, Any]) -> dict[str, Any]:
    ignored = {"repeat_index", "deterministic_result"}
    return {key: value for key, value in trial.items() if key not in ignored}


def _mark_deterministic(trials: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for trial in trials:
        grouped[(trial["scenario_id"], trial["strategy"])].append(trial)
    for grouped_trials in grouped.values():
        deterministic = all(_normalized_trial(item) == _normalized_trial(grouped_trials[0]) for item in grouped_trials[1:])
        for item in grouped_trials:
            item["deterministic_result"] = deterministic


def _scenario_results(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{key: value for key, value in trial.items() if key not in {"observations", "detected_anomalies", "digital_twin_after"}}
            for trial in trials if trial["repeat_index"] == 1]


def _mean(values: list[float]) -> float | None:
    return _round(sum(values) / len(values)) if values else None


def _summary_row(items: list[dict[str, Any]], strategy: str, battery_level: str | None) -> dict[str, Any]:
    gt = sum(item["ground_truth_anomaly_count"] for item in items)
    detected = sum(item["detected_anomaly_count"] for item in items)
    latencies = [item["mean_detection_latency"] for item in items if item["mean_detection_latency"] is not None]
    covered_rooms = sum(len(item["visited_rooms"]) for item in items)
    fields = {
        "mean_coverage_rate": _mean([item["coverage_rate"] for item in items]),
        "full_coverage_success_rate": _mean([float(item["route_completed"]) for item in items]),
        "risk_weighted_coverage": _mean([item["risk_weighted_coverage"] for item in items]),
        "anomaly_discovery_rate": _round(detected / gt) if gt else None,
        "missed_anomaly_count": sum(item["missed_anomaly_count"] for item in items),
        "detector_false_negative_count": sum(item["detector_false_negative_count"] for item in items),
        "mean_detection_latency": _mean(latencies),
        "median_detection_latency": _round(float(statistics.median(latencies))) if latencies else None,
        "total_travel_distance_m": _round(sum(item["total_distance_m"] for item in items)),
        "mean_travel_distance_m": _mean([item["total_distance_m"] for item in items]),
        "total_simulated_energy_consumption": _round(sum(item["total_energy_consumed"] for item in items)),
        "mean_simulated_energy_consumption": _mean([item["total_energy_consumed"] for item in items]),
        "energy_per_covered_room": _round(sum(item["total_energy_consumed"] for item in items) / covered_rooms) if covered_rooms else None,
        "energy_per_detected_anomaly": _round(sum(item["total_energy_consumed"] for item in items) / detected) if detected else None,
        "return_to_charging_success_rate": _mean([float(item["returned_to_charging_area"]) for item in items]),
        "safety_reserve_violation_count": sum(not item["return_reserve_satisfied"] for item in items),
        "rooms_skipped": sum(len(item["skipped_rooms"]) for item in items),
        "deterministic_repeat_rate": _mean([float(item["deterministic_result"]) for item in items]),
    }
    return {"strategy": strategy, "battery_level": battery_level or "all_battery_levels", "run_count": len(items), **fields,
            **synthetic_onboard_boundary()}


def _summarize(trials: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    by_level = []
    overall = []
    for strategy in STRATEGIES:
        strategy_trials = [item for item in trials if item["strategy"] == strategy]
        overall.append(_summary_row(strategy_trials, strategy, None))
        for level in sorted(BATTERY_LEVELS):
            by_level.append(_summary_row([item for item in strategy_trials if item["battery_level"] == level], strategy, level))
    return {"experiment_name": "house_v1_patrol_strategy_experiment_v1", "scenario_count": 18, "repeat_count": repeats,
            "total_runs": len(trials), "layout_filter_policy": "none", "energy_disclaimer": "Deterministic simulated energy units, not TurtleBot4 battery measurements.",
            "overall_by_strategy": overall, "by_strategy_and_battery": by_level, **synthetic_onboard_boundary()}


def _paired_comparisons(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {(item["scenario_id"], item["repeat_index"], item["strategy"]): item for item in trials}
    rows = []
    for scenario_id, repeat, strategy in sorted(indexed):
        if strategy != "fixed_order":
            continue
        baseline = indexed[(scenario_id, repeat, "fixed_order")]
        for candidate in ("risk_priority", "battery_aware"):
            item = indexed[(scenario_id, repeat, candidate)]
            latency_delta = None if item["mean_detection_latency"] is None or baseline["mean_detection_latency"] is None else _round(item["mean_detection_latency"] - baseline["mean_detection_latency"])
            rows.append({"scenario_id": scenario_id, "repeat_index": repeat, "battery_level": baseline["battery_level"],
                         "baseline_strategy": "fixed_order", "comparison_strategy": candidate,
                         "coverage_delta": _round(item["coverage_rate"] - baseline["coverage_rate"]),
                         "detection_latency_delta": latency_delta, "distance_delta": _round(item["total_distance_m"] - baseline["total_distance_m"]),
                         "energy_delta": _round(item["total_energy_consumed"] - baseline["total_energy_consumed"]),
                         "missed_anomaly_delta": item["missed_anomaly_count"] - baseline["missed_anomaly_count"],
                         "final_battery_delta": _round(item["final_battery"] - baseline["final_battery"]), **synthetic_onboard_boundary()})
    return rows


def _pareto_rows(summary_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = {
        "coverage_vs_energy": ("mean_coverage_rate", True, "mean_simulated_energy_consumption", False),
        "detection_latency_vs_energy": ("mean_detection_latency", False, "mean_simulated_energy_consumption", False),
        "anomaly_discovery_vs_distance": ("anomaly_discovery_rate", True, "mean_travel_distance_m", False),
    }
    output = []
    for level in sorted(BATTERY_LEVELS):
        points = [item for item in summary_rows if item["battery_level"] == level]
        for name, (first, first_high, second, second_high) in definitions.items():
            eligible = [item for item in points if item[first] is not None and item[second] is not None]
            for point in points:
                pareto = point in eligible and not any(_dominates(other, point, first, first_high, second, second_high) for other in eligible if other is not point)
                output.append({"battery_level": level, "pareto_analysis": name, "strategy": point["strategy"],
                               first: point[first], second: point[second], "pareto_optimal": pareto, **synthetic_onboard_boundary()})
    return output


def _dominates(left: dict[str, Any], right: dict[str, Any], first: str, first_high: bool, second: str, second_high: bool) -> bool:
    better_first = left[first] >= right[first] if first_high else left[first] <= right[first]
    better_second = left[second] >= right[second] if second_high else left[second] <= right[second]
    strict_first = left[first] > right[first] if first_high else left[first] < right[first]
    strict_second = left[second] > right[second] if second_high else left[second] < right[second]
    return better_first and better_second and (strict_first or strict_second)


def _failures(trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures = []
    for item in trials:
        checks = []
        if not item["returned_to_charging_area"]: checks.append("returned_to_charging_area")
        if not item["return_reserve_satisfied"]: checks.append("return_reserve_satisfied")
        if not item["deterministic_result"]: checks.append("deterministic_result")
        if checks:
            failures.append({"scenario_id": item["scenario_id"], "repeat_index": item["repeat_index"],
                             "strategy": item["strategy"], "failed_checks": checks, **synthetic_onboard_boundary()})
    return failures


def _csv_text(rows: list[dict[str, Any]], fields: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        flattened = {key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else value for key, value in row.items()}
        writer.writerow(flattened)
    return output.getvalue()


def render_patrol_strategy_artifacts(result: dict[str, Any]) -> dict[str, str]:
    compact = lambda value: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    trial_fields = ["scenario_id", "monitoring_case", "battery_level", "repeat_index", "strategy", "initial_battery", "final_battery",
                    "visited_rooms", "skipped_rooms", "visit_order", "route_segments", "total_distance_m", "travel_energy", "sensing_energy",
                    "total_energy_consumed", "coverage_rate", "risk_weighted_coverage", "ground_truth_anomaly_count", "detected_anomaly_count",
                    "missed_anomaly_count", "detector_false_negative_count", "detection_latency_by_anomaly", "mean_detection_latency",
                    "returned_to_charging_area", "return_reserve_satisfied", "route_completed", "deterministic_result", "synthetic",
                    "simulated_onboard_sensor", "simulation_only", "real_robot_supported"]
    paired_fields = ["scenario_id", "repeat_index", "battery_level", "baseline_strategy", "comparison_strategy", "coverage_delta",
                     "detection_latency_delta", "distance_delta", "energy_delta", "missed_anomaly_delta", "final_battery_delta",
                     "synthetic", "simulated_onboard_sensor", "simulation_only", "real_robot_supported"]
    pareto_fields = ["battery_level", "pareto_analysis", "strategy", "mean_coverage_rate", "mean_simulated_energy_consumption",
                     "mean_detection_latency", "anomaly_discovery_rate", "mean_travel_distance_m", "pareto_optimal", "synthetic",
                     "simulated_onboard_sensor", "simulation_only", "real_robot_supported"]
    summary = result["summary"]
    lines = ["# house_v1 patrol strategy experiment v1", "", "All runs are deterministic, local and simulation-only. Energy is a fixed model, not a TurtleBot4 battery measurement.", "",
             "## Policies", "- fixed_order: living_room → kitchen → bedroom → bathroom → charging_area.", "- risk_priority: predeclared risk descending; A* distance then room_id break ties; no ground truth input.", "- battery_aware: visit only if travel + sensing + guaranteed A* return + reserve is affordable.", "", "## Overall results"]
    for row in summary["overall_by_strategy"]:
        lines.append(f"- {row['strategy']}: coverage {row['mean_coverage_rate']:.3f}; discovery {row['anomaly_discovery_rate']}; latency {row['mean_detection_latency']}; distance {row['mean_travel_distance_m']:.3f}; energy {row['mean_simulated_energy_consumption']:.3f}; return {row['return_to_charging_success_rate']:.3f}.")
    lines.extend(("", "## Results by battery level"))
    by_level = summary["by_strategy_and_battery"]
    for battery_level in ("high_battery", "medium_battery", "constrained_battery"):
        lines.extend(("", f"### {battery_level}",
                      "| Strategy | Mean coverage rate | Anomaly discovery rate | Mean detection latency | Mean travel distance (m) | Mean simulated energy consumption | Return-to-charging success rate | Mean skipped rooms |",
                      "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"))
        for row in (item for item in by_level if item["battery_level"] == battery_level):
            latency = "N/A" if row["mean_detection_latency"] is None else f"{row['mean_detection_latency']:.3f}"
            discovery = "N/A" if row["anomaly_discovery_rate"] is None else f"{row['anomaly_discovery_rate']:.3f}"
            mean_skipped = row["rooms_skipped"] / row["run_count"]
            lines.append(f"| {row['strategy']} | {row['mean_coverage_rate']:.3f} | {discovery} | {latency} | {row['mean_travel_distance_m']:.3f} | {row['mean_simulated_energy_consumption']:.3f} | {row['return_to_charging_success_rate']:.3f} | {mean_skipped:.3f} |")
    lines.extend(("", "## Paired comparisons", "fixed_order is paired with risk_priority and battery_aware for the same scenario and repeat; the CSV records coverage, detection-latency, distance, energy, missed-anomaly and final-battery deltas.",
                  "", "## Pareto analysis", "Pareto flags cover coverage vs energy, detection latency vs energy, and anomaly discovery rate vs distance. They are comparison aids only and do not claim any policy is globally optimal.",
                  "", "## Missed anomaly attribution", "Missed anomalies are classified separately: unvisited-room misses are patrol-policy misses; visited-room misses are detector false negatives. No policy reads injected events or expected ground truth."))
    traces = []
    for trial in result["trials"]:
        traces.append(json.dumps({"scenario_id": trial["scenario_id"], "repeat_index": trial["repeat_index"], "strategy": trial["strategy"],
                                  "visit_order": trial["visit_order"], "route_segments": trial["route_segments"],
                                  "final_battery": trial["final_battery"], **synthetic_onboard_boundary()}, ensure_ascii=False, sort_keys=True))
    return {
        "patrol_strategy_trials.csv": _csv_text(result["trials"], trial_fields),
        "patrol_strategy_scenario_results.json": compact({"scenario_results": result["scenario_results"], **synthetic_onboard_boundary()}),
        "patrol_strategy_summary.json": compact(summary),
        "patrol_strategy_summary.md": "\n".join(lines) + "\n",
        "patrol_strategy_paired_comparison.csv": _csv_text(result["paired"], paired_fields),
        "patrol_strategy_pareto.csv": _csv_text(result["pareto"], pareto_fields),
        "patrol_strategy_failures.json": compact({"failures": result["failures"], **synthetic_onboard_boundary()}),
        "patrol_route_traces.jsonl": "\n".join(traces) + "\n",
    }


def write_patrol_strategy_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    if set(contents) != set(ARTIFACTS):
        raise PatrolStrategyEvaluationError("巡逻策略 artifact 集不完整。")
    output = Path(output_dir)
    if output.exists():
        raise PatrolStrategyEvaluationError(f"输出目录已存在，拒绝覆盖：{output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        for name in ARTIFACTS:
            (Path(temporary.name) / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary.name, output)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return {name: output / name for name in ARTIFACTS}
