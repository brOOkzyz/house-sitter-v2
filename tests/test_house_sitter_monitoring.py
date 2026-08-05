"""Contract tests for the deterministic house_v1 monitoring vertical slice."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from house_sitter_core.digital_twin import create_house_v1_baseline, room_index, update_room_from_observation
from house_sitter_core.environment_monitoring import detect_anomalies
from house_sitter_core.house_sitter_patrol import (
    ARTIFACT_NAMES, PATROL_ORDER, load_house_v1_monitoring_inputs, render_monitoring_artifacts,
    run_house_sitter_patrol, write_monitoring_artifacts,
)
from house_sitter_core.simulated_onboard_sensors import observe_room


ROOT = Path(__file__).resolve().parents[1]
BOUNDARY = {
    "synthetic": True,
    "simulated_onboard_sensor": True,
    "simulation_only": True,
    "real_robot_supported": False,
}


def assert_strict_boundary(record: dict) -> None:
    for field, expected in BOUNDARY.items():
        assert field in record
        assert type(record[field]) is bool
        assert record[field] is expected


def test_house_v1_rooms_and_baseline_twin_schema_are_complete():
    regions, _ = load_house_v1_monitoring_inputs(ROOT)
    twin = create_house_v1_baseline(regions)
    rooms = room_index(twin)
    assert set(rooms) == {"living_room", "kitchen", "bedroom", "bathroom", "hallway", "charging_area"}
    assert all(set(("room_id", "last_observed_step", "temperature_c", "humidity_percent", "obstacle_count", "layout_signature", "anomaly_status", "anomaly_types", "observation_confidence", "simulation_only")).issubset(room) for room in rooms.values())
    assert_strict_boundary(twin)
    for room in rooms.values():
        assert_strict_boundary(room)


def test_sensor_output_is_deterministic_and_marks_itself_synthetic():
    regions, _ = load_house_v1_monitoring_inputs(ROOT)
    kitchen = room_index(create_house_v1_baseline(regions))["kitchen"]
    first = observe_room("kitchen", 2, kitchen, unexpected_obstacle=True)
    assert first == observe_room("kitchen", 2, kitchen, unexpected_obstacle=True)
    assert first["synthetic"] is True and first["simulated_onboard_sensor"] is True
    assert first["unexpected_obstacle"] is True and first["distance_observation"]["obstacle_count"] == 1


def test_normal_rooms_do_not_create_false_positive_anomalies():
    result = run_house_sitter_patrol(ROOT)
    assert result["summary"]["false_positive_count"] == 0
    assert {item["room_id"] for item in result["anomalies"]} == {"kitchen"}


def test_kitchen_new_obstacle_is_detected_with_actionable_alert():
    result = run_house_sitter_patrol(ROOT)
    anomaly = result["anomalies"][0]
    alert = result["alerts"][0]
    assert anomaly["room_id"] == "kitchen" and anomaly["anomaly_type"] == "unexpected_obstacle"
    assert "unexpected obstacle" in anomaly["explanation"] and anomaly["recommended_action"]
    assert alert["room_id"] == "kitchen" and alert["message"] == anomaly["explanation"]


def test_other_anomaly_interfaces_detect_temperature_humidity_and_layout_changes():
    regions, _ = load_house_v1_monitoring_inputs(ROOT)
    room = room_index(create_house_v1_baseline(regions))["bedroom"]
    observation = observe_room("bedroom", 3, room)
    observation.update({"temperature_c": 30.0, "humidity_percent": 75.0, "layout_signature": "changed"})
    assert {item["anomaly_type"] for item in detect_anomalies(observation, room)} == {
        "temperature_out_of_range", "humidity_out_of_range", "layout_change",
    }


def test_digital_twin_updates_only_the_observed_room_for_one_observation():
    regions, _ = load_house_v1_monitoring_inputs(ROOT)
    before = create_house_v1_baseline(regions)
    kitchen = room_index(before)["kitchen"]
    observation = observe_room("kitchen", 2, kitchen, unexpected_obstacle=True)
    detected = detect_anomalies(observation, kitchen)
    after, difference = update_room_from_observation(before, observation, detected)
    assert room_index(before)["living_room"] == room_index(after)["living_room"]
    assert room_index(after)["kitchen"]["obstacle_count"] == 1
    assert difference["room_id"] == "kitchen" and "obstacle_count" in difference["changed_fields"]


def test_patrol_covers_the_formal_order_and_returns_to_charging_area():
    result = run_house_sitter_patrol(ROOT)
    assert tuple(item["room_id"] for item in result["plan"]["steps"]) == PATROL_ORDER
    assert result["summary"]["covered_rooms"] == len(PATROL_ORDER)
    assert result["summary"]["returned_to_charging_area"] is True
    assert result["plan"]["return_step"]["room_id"] == "charging_area"


def test_repeated_runs_and_artifacts_are_complete_and_consistent():
    first = run_house_sitter_patrol(ROOT)
    second = run_house_sitter_patrol(ROOT)
    assert first == second
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "monitoring"
        paths = write_monitoring_artifacts(output, render_monitoring_artifacts(first))
        assert set(paths) == set(ARTIFACT_NAMES)
        assert all(path.is_file() for path in paths.values())
        observations = [json.loads(line) for line in paths["sensor_observations.jsonl"].read_text(encoding="utf-8").splitlines()]
        summary = json.loads(paths["monitoring_summary.json"].read_text(encoding="utf-8"))
        anomalies = json.loads(paths["detected_anomalies.json"].read_text(encoding="utf-8"))["anomalies"]
        alerts = json.loads(paths["actionable_alerts.json"].read_text(encoding="utf-8"))["alerts"]
        before_rooms = json.loads(paths["digital_twin_before.json"].read_text(encoding="utf-8"))["rooms"]
        after_rooms = json.loads(paths["digital_twin_after.json"].read_text(encoding="utf-8"))["rooms"]
        assert len(observations) == summary["covered_rooms"]
        assert len(anomalies) == summary["detected_anomaly_count"]
        assert "simulation_only: true" in paths["monitoring_report.md"].read_text(encoding="utf-8")
        for record in observations + before_rooms + after_rooms + anomalies + alerts + [summary]:
            assert_strict_boundary(record)


def test_monitoring_slice_contains_no_ros_gazebo_nav2_network_or_llm_calls():
    sources = "\n".join((ROOT / path).read_text(encoding="utf-8") for path in (
        "house_sitter_core/digital_twin.py", "house_sitter_core/simulated_onboard_sensors.py",
        "house_sitter_core/environment_monitoring.py", "house_sitter_core/house_sitter_patrol.py",
        "scripts/run_house_sitter_monitoring.py",
    )).casefold()
    for forbidden in ("import subprocess", "ros2", "gazebo", "nav2", "import requests", "import urllib", "openai", "import llm"):
        assert forbidden not in sources
