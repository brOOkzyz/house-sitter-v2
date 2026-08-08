from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.demo_ui import DemoController, DemoError
from raptor_lite.phase56 import confirmation_preview, explain_verification, safe_repair
from raptor_lite.planner import OfflineHouseSitterPlanner
from raptor_lite.task_schema import load_task
from raptor_lite.verifier import verify_task


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"
VALID = ROOT / "examples/raptor_lite/valid_house_sitter_task.json"
UNSUPPORTED = ROOT / "examples/raptor_lite/invalid_unsupported_skill.json"


def registry() -> CapabilityRegistry:
    return CapabilityRegistry.from_yaml(PROFILE)


def test_capability_explorer_is_registry_derived_and_states_simulation_limit():
    catalog = registry().explore("inspect")
    assert catalog["capabilities"] and {item["name"] for item in catalog["capabilities"]} == {"inspect_room"}
    assert catalog["capabilities"][0]["parameters"][0]["allowed_values"] == ["living_room", "kitchen", "bedroom", "bathroom", "charging_area"]
    assert "Cannot control a physical robot: this profile is simulation-only." in registry().explore()["natural_language"]


def test_explainable_verification_and_safe_repair_reverify_new_tasks_only():
    data = json.loads(VALID.read_text())
    data["steps"][0]["timeout_seconds"] = 0
    data["steps"][0]["parameters"]["not_declared"] = True
    data["steps"][1]["step_id"] = data["steps"][0]["step_id"]
    data["steps"] = [step for step in data["steps"] if step["skill"] != "return_to_start"]
    task = load_task(VALID).model_validate(data)
    report = verify_task(task, registry())
    explanation = explain_verification(task, registry(), report)
    assert not report.approved and any(item["repairable"] for item in explanation["issues"])
    repaired = safe_repair(task, registry())
    assert repaired["original_task"] != repaired["repaired_task"] and repaired["approved"]
    assert repaired["verification"]["approved"]
    unsupported = safe_repair(load_task(UNSUPPORTED), registry())
    assert not unsupported["approved"] and "UNKNOWN_SKILL" in unsupported["unsupported_capabilities"]
    assert unsupported["repaired_task"]["steps"][0]["skill"] == "fly_to_room"


def test_confirmation_snapshot_binds_inputs_route_and_verified_execution(tmp_path):
    tool = OfflineHouseSitterPlanner(registry())
    task = "Patrol the whole house and report anything unusual."
    scenario = "There is a box in the bedroom and the bathroom has high humidity."
    preview = confirmation_preview(task, scenario, 55, tool, registry())
    assert preview["approved"] and preview["route"]["visit_order"] == ["living_room", "kitchen", "bathroom", "bedroom"]
    assert "simulation_only" in preview["safety_constraints"] and preview["verification_explanation"]["approved"]
    changed = confirmation_preview(task, "There is a box in the kitchen.", 55, tool, registry())
    assert preview["snapshot"]["scenario_text_hash"] != changed["snapshot"]["scenario_text_hash"]
    ui = DemoController(PROFILE, tmp_path / "artifacts")
    with pytest.raises(DemoError):
        ui.run(task, scenario, 55)
    confirmed = ui.confirm(task, scenario, 55)
    assert confirmed["confirmation"]["approved"]
    with pytest.raises(DemoError):
        ui.run(task, "There is a box in the kitchen.", 55)
    state = ui.run(task, scenario, 55)
    assert state["execution"]["success"] and state["run_request"]["task_spec_hash"] == state["confirmation"]["snapshot"]["task_spec_hash"]
    assert (Path(state["artifact_directory"]) / "confirmation_preview.json").is_file()
