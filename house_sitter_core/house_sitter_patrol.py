"""One complete deterministic house-sitter monitoring patrol vertical slice."""
from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from .digital_twin import DigitalTwinError, create_house_v1_baseline, room_index, update_room_from_observation
from .environment_monitoring import actionable_alerts, detect_anomalies
from .simulated_onboard_sensors import observe_room
from .simulation_boundary import synthetic_onboard_boundary


PATROL_ORDER = ("living_room", "kitchen", "bedroom", "bathroom", "charging_area")
ARTIFACT_NAMES = (
    "patrol_plan.json", "sensor_observations.jsonl", "digital_twin_before.json", "digital_twin_after.json",
    "detected_anomalies.json", "actionable_alerts.json", "monitoring_summary.json", "monitoring_report.md",
)


class HouseSitterMonitoringError(ValueError):
    """Raised for invalid local inputs or an unsafe artifact publication request."""


def load_house_v1_monitoring_inputs(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read the committed house_v1 semantic regions and accepted safe goals only."""
    paths = (root / "local_annotations" / "house_v1" / "semantic_regions.json", root / "local_annotations" / "house_v1" / "safe_goals.json")
    try:
        regions, goals = (json.loads(path.read_text(encoding="utf-8")) for path in paths)
    except (OSError, json.JSONDecodeError) as exc:
        raise HouseSitterMonitoringError(f"无法读取 house_v1 正式输入：{exc}") from exc
    if not isinstance(regions, dict) or not isinstance(goals, dict) or regions.get("map_id") != "house_v1" or goals.get("map_id") != "house_v1":
        raise HouseSitterMonitoringError("house_v1 语义或 safe goal 文件无效。")
    labels = {item.get("canonical_label") for item in regions.get("regions", []) if isinstance(item, dict)}
    accepted = {item.get("canonical_label") for item in goals.get("goals", []) if isinstance(item, dict) and item.get("status") == "accepted"}
    if not set(PATROL_ORDER).issubset(labels) or not set(PATROL_ORDER).issubset(accepted):
        raise HouseSitterMonitoringError("巡逻所需的 house_v1 房间或 accepted safe goals 不完整。")
    return regions, goals


def load_monitoring_scenario(root: Path, scenario_id: str) -> dict[str, Any]:
    path = root / "evaluation" / "monitoring_scenarios_v1.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HouseSitterMonitoringError(f"无法读取监测场景：{exc}") from exc
    scenarios = {item.get("scenario_id"): item for item in document.get("scenarios", []) if isinstance(item, dict)}
    scenario = scenarios.get(scenario_id)
    if not isinstance(scenario, dict):
        raise HouseSitterMonitoringError(f"未找到监测场景：{scenario_id}。")
    if scenario.get("simulation_only") is not True or scenario.get("real_robot_supported") is not False:
        raise HouseSitterMonitoringError("监测场景缺少 simulation-only 安全边界。")
    return scenario


def run_house_sitter_patrol(root: Path, scenario_id: str = "kitchen_unexpected_obstacle") -> dict[str, Any]:
    """Run a no-motion logical patrol, then return an internally consistent result bundle."""
    regions, goals = load_house_v1_monitoring_inputs(root)
    accepted_goals = {
        item["canonical_label"]: item for item in goals["goals"]
        if isinstance(item, dict) and item.get("status") == "accepted" and isinstance(item.get("canonical_label"), str)
    }
    scenario = load_monitoring_scenario(root, scenario_id)
    before = create_house_v1_baseline(regions)
    baseline_rooms = room_index(before)
    after = deepcopy(before)
    observations: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    injection = scenario.get("injection", {})
    injected_room = scenario.get("injected_room", injection.get("room_id"))
    injected_values = scenario.get("injected_values", {})
    for step, room_id in enumerate(PATROL_ORDER, start=1):
        unexpected = injection.get("room_id") == room_id and injection.get("unexpected_obstacle") is True
        values = injected_values if injected_room == room_id and isinstance(injected_values, dict) else None
        observation = observe_room(room_id, step, baseline_rooms[room_id], unexpected_obstacle=unexpected, injected_values=values)
        detected = detect_anomalies(observation, baseline_rooms[room_id])
        after, update = update_room_from_observation(after, observation, detected)
        observations.append(observation); anomalies.extend(detected); updates.append(update)
    alerts = actionable_alerts(anomalies)
    false_positives = sum(1 for item in anomalies if item["room_id"] != injection.get("room_id"))
    plan = {
        "scenario_id": scenario_id, "map_id": "house_v1", "patrol_order": list(PATROL_ORDER),
        "steps": [
            {"step": index, "room_id": room_id, "accepted_safe_goal": {
                "proposal_id": accepted_goals[room_id]["proposal_id"],
                "partition_id": accepted_goals[room_id]["partition_id"],
                "canonical_label": accepted_goals[room_id]["canonical_label"],
            }} for index, room_id in enumerate(PATROL_ORDER, 1)
        ],
        "return_step": {"step": len(PATROL_ORDER) + 1, "room_id": "charging_area", "action": "return_to_charging_area"},
        "simulation_only": True, "real_robot_supported": False,
    }
    summary = {
        "scenario_id": scenario_id, "covered_rooms": len(PATROL_ORDER), "coverage_rate": 1.0,
        "patrol_steps": len(PATROL_ORDER) + 1, "detected_anomaly_count": len(anomalies),
        "anomaly_rooms": sorted({item["room_id"] for item in anomalies}),
        "detection_latency_steps": min((item["detection_step"] for item in anomalies), default=None),
        "false_positive_count": false_positives, "digital_twin_updates": updates,
        "returned_to_charging_area": True, **synthetic_onboard_boundary(),
        "action_goals_sent": 0,
    }
    return {"plan": plan, "observations": observations, "before": before, "after": after, "anomalies": anomalies, "alerts": alerts, "summary": summary}


def render_monitoring_artifacts(result: dict[str, Any]) -> dict[str, str]:
    """Render all required artifacts before one atomic directory publication."""
    summary = result["summary"]
    report = "\n".join((
        "# house_v1 house-sitter environment monitoring", "", "Simulation-only deterministic monitoring vertical slice.", "",
        f"- Covered rooms: {summary['covered_rooms']} (coverage: {summary['coverage_rate']:.0%})",
        f"- Patrol steps: {summary['patrol_steps']}", f"- Detected anomalies: {summary['detected_anomaly_count']}",
        f"- Anomaly rooms: {', '.join(summary['anomaly_rooms']) or 'none'}",
        f"- Detection latency (steps): {summary['detection_latency_steps']}",
        f"- False positives: {summary['false_positive_count']}",
        f"- Digital Twin updated fields: {sum(len(item['changed_fields']) for item in summary['digital_twin_updates'])}",
        f"- Returned to charging area: {summary['returned_to_charging_area']}", "- simulation_only: true", "- real_robot_supported: false", "",
    ))
    compact = lambda value: json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    return {
        "patrol_plan.json": compact(result["plan"]),
        "sensor_observations.jsonl": "".join(json.dumps(item, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n" for item in result["observations"]),
        "digital_twin_before.json": compact(result["before"]), "digital_twin_after.json": compact(result["after"]),
        "detected_anomalies.json": compact({"anomalies": result["anomalies"], "simulation_only": True}),
        "actionable_alerts.json": compact({"alerts": result["alerts"], "simulation_only": True}),
        "monitoring_summary.json": compact(summary), "monitoring_report.md": report,
    }


def write_monitoring_artifacts(output_dir: Path, contents: dict[str, str]) -> dict[str, Path]:
    """Publish a complete monitoring run atomically and never overwrite a directory."""
    if set(contents) != set(ARTIFACT_NAMES):
        raise HouseSitterMonitoringError("监测 artifact 集不完整。")
    output = Path(output_dir)
    if output.exists():
        raise HouseSitterMonitoringError(f"输出目录已存在，拒绝覆盖：{output}")
    temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix=f".{output.name}.tmp-", dir=output.parent)
        temporary_path = Path(temporary.name)
        for name in ARTIFACT_NAMES:
            (temporary_path / name).write_text(contents[name], encoding="utf-8", newline="")
        os.replace(temporary_path, output)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return {name: output / name for name in ARTIFACT_NAMES}
