from __future__ import annotations

from pathlib import Path

import pytest

from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.demo_ui import DemoController, DemoError
from raptor_lite.phase56 import confirmation_preview
from raptor_lite.phase57 import TwinHistory, resource_decision
from raptor_lite.planner import OfflineHouseSitterPlanner


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"


def _observation(room: str, *, valid: bool = True, objects: list[str] | None = None) -> dict:
    return {"observation_id": f"observation:{room}:1", "room": room, "robot_state": {"room": room}, "observation_valid": valid,
            "visible_object_identifiers": objects or [f"{room}_furniture"], "obstacle_present": bool(objects), "temperature_c": 21.0,
            "humidity_percent": 44.0, "transition_accessibility": {"hall": True}}


def test_twin_history_tracks_only_valid_visited_observations_and_can_be_explicitly_reset():
    history = TwinHistory()
    first = history.record("history-0001", [_observation("bedroom"), _observation("kitchen")])
    assert not first["confirmed_changes"] and first["initialized_rooms"] == ["bedroom", "kitchen"]
    changed = history.record("history-0002", [_observation("bedroom", objects=["bedroom_furniture", "bedroom_unexpected_obstacle"])])
    assert changed["trusted_rooms"] == ["bedroom"] and {item["room"] for item in changed["confirmed_changes"]} == {"bedroom"}
    kitchen_before = history.snapshot()["rooms"]["kitchen"]
    dropout = history.record("history-0003", [_observation("bedroom", valid=False)])
    assert not dropout["confirmed_changes"] and dropout["ignored_observations"] == [{"room": "bedroom", "reason": "invalid observation is not trusted"}]
    assert history.snapshot()["rooms"]["kitchen"] == kitchen_before
    history.reset()
    assert history.snapshot()["rooms"] == {} and history.snapshot()["runs"] == []


def test_resource_policy_is_idle_only_and_reserves_real_return_cost():
    registry = CapabilityRegistry.from_yaml(PROFILE)
    task = confirmation_preview("Inspect the bedroom.", "The bedroom is normal.", 17, OfflineHouseSitterPlanner(registry), registry)["planning"].candidate_task
    assert task is not None
    approved = resource_decision(task, {"activity": "idle", "room": "charging_area", "battery": 100.0})
    assert approved["decision"] == "APPROVE" and approved["safe_return_reserve"] > 0 and approved["required_battery"] > approved["estimated_task_cost"]
    assert resource_decision(task, {"activity": "busy", "room": "charging_area", "battery": 100.0})["decision"] == "DEFER"
    assert resource_decision(task, {"activity": "idle", "room": "charging_area", "battery": 1.0})["decision"] == "DEFER"
    assert resource_decision(task, {"activity": "idle"})["decision"] == "REJECT"


def test_controller_keeps_history_across_reset_without_leaking_unvisited_or_dropout(tmp_path):
    ui = DemoController(PROFILE, tmp_path / "artifacts")
    whole, normal = "Patrol the whole house and report anything unusual.", "The home is normal."
    ui.confirm(whole, normal, 91)
    first = ui.run(whole, normal, 91)
    assert set(first["twin_history"]["rooms"]) == {"living_room", "kitchen", "bedroom", "bathroom"}
    kitchen_before = first["twin_history"]["rooms"]["kitchen"]
    assert ui.reset()["twin_history"]["rooms"]["kitchen"] == kitchen_before
    ui.confirm("Inspect the bedroom.", "There is a box in the bedroom.", 91)
    second = ui.run("Inspect the bedroom.", "There is a box in the bedroom.", 91)
    assert {item["room"] for item in second["temporal_change"]["confirmed_changes"]} == {"bedroom"}
    assert second["twin_history"]["rooms"]["kitchen"] == kitchen_before
    assert {"resource_policy.json", "twin_history.json", "twin_history_diff.json"} <= set(second["artifact_files"])
    ui.reset()
    ui.confirm("Inspect the bedroom.", "The bedroom sensor observation is unavailable.", 91)
    dropout = ui.run("Inspect the bedroom.", "The bedroom sensor observation is unavailable.", 91)
    assert not dropout["temporal_change"]["confirmed_changes"] and dropout["temporal_change"]["ignored_observations"]
    assert dropout["twin_history"]["rooms"]["bedroom"] == second["twin_history"]["rooms"]["bedroom"]
    assert ui.reset_twin_history()["twin_history"]["rooms"] == {}


def test_low_battery_is_deferred_before_execution_and_cannot_update_history(tmp_path):
    ui = DemoController(PROFILE, tmp_path / "artifacts")
    task, scenario = "Inspect the bedroom.", "The available battery is low."
    confirmation = ui.confirm(task, scenario, 23)
    assert confirmation["resource_policy"]["decision"] == "DEFER" and not confirmation["confirmation"]["approved"]
    with pytest.raises(DemoError, match="Execution DEFER"):
        ui.run(task, scenario, 23)
    assert not ui.twin_history.snapshot()["runs"] and not ui.bundle
