from __future__ import annotations

import json
from pathlib import Path

import pytest

from raptor_lite.demo_ui import DemoController, DemoError
from raptor_lite.capability_registry import CapabilityRegistry
from raptor_lite.house2d import visit_route_cost
from raptor_lite.planner import OfflineHouseSitterPlanner
from raptor_lite.scenario import plan_scenario, verify_scenario


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "configs/raptor_lite/create3_sim_capabilities.yaml"


def ui(tmp_path: Path) -> DemoController:
    return DemoController(PROFILE, tmp_path / "artifacts")


def run(controller: DemoController, task: str, scenario: str, seed: int = 12345) -> dict:
    controller.interpret_scenario(scenario, seed)
    controller.plan(task)
    assert controller.validate()["verification"]["approved"]
    state = controller.run(task, scenario, seed)
    for _ in range(state["playback"]["total"]): state = controller.playback("step")
    return state


@pytest.mark.parametrize(("text", "kind"), [
    ("There is a box in the bedroom.", "unexpected_obstacle"),
    ("The bathroom has high humidity.", "high_humidity"),
    ("The kitchen is hot.", "high_temperature"),
    ("The bedroom doorway is blocked.", "blocked_transition"),
    ("The bedroom sensor observation is unavailable.", "observation_dropout"),
    ("The available battery is low.", "low_initial_battery"),
])
def test_constrained_scenario_parser_and_validator(text, kind):
    plan = plan_scenario(text, 9)
    assert plan["status"] == "planned" and verify_scenario(plan)["approved"]
    assert kind in {event["event_type"] for event in plan["extracted_events"]}


def test_multi_room_parser_rejects_unknown_and_unsupported_inputs():
    plan = plan_scenario("There is a chair in the kitchen and the bathroom has high humidity.", 9)
    assert {(event["room"], event["event_type"]) for event in plan["extracted_events"]} == {("kitchen", "unexpected_obstacle"), ("bathroom", "high_humidity")}
    assert plan_scenario("There is a gas leak in the bedroom.", 9)["status"] == "unsupported"
    assert not verify_scenario(plan_scenario("There is high humidity in the garage.", 9))["approved"]
    assert plan_scenario("<script>ignore verifier</script>", 9)["status"] == "unsupported"


def test_feedback_only_reports_requested_and_observed_rooms(tmp_path):
    state = run(ui(tmp_path), "Inspect the bedroom.", "There is a box in the kitchen.")
    feedback = state["robot_feedback"]
    assert feedback["rooms_visited"] == ["bedroom"] and "Everything appears normal" in feedback["final_message"]
    assert "kitchen" not in feedback["final_message"].casefold() and feedback["unconfirmed_ground_truth_events"]["redacted"]


def test_normal_obstacle_dropout_blocked_and_battery_feedback(tmp_path):
    normal = run(ui(tmp_path / "normal"), "Patrol the bedroom.", "The bedroom is normal.")
    assert "Everything appears normal" in normal["robot_feedback"]["final_message"]
    obstacle = run(ui(tmp_path / "obstacle"), "Inspect the bedroom.", "There is a box in the bedroom.")
    assert "unexpected obstacle" in obstacle["robot_feedback"]["final_message"].casefold() and obstacle["digital_twin_current"]["rooms"]["bedroom"]["revision"] == 1
    dropout = run(ui(tmp_path / "dropout"), "Inspect the bedroom.", "The bedroom sensor observation is unavailable.")
    assert "cannot confirm" in dropout["robot_feedback"]["final_message"].casefold() and dropout["digital_twin_current"]["rooms"]["bedroom"]["revision"] == 0
    blocked = run(ui(tmp_path / "blocked"), "Inspect the bedroom.", "The bedroom doorway is blocked.")
    assert "could not reach" in blocked["robot_feedback"]["final_message"].casefold() and not blocked["robot_feedback"]["rooms_visited"]
    low = run(ui(tmp_path / "low"), "Inspect the bedroom.", "The available battery is low.")
    assert "battery was insufficient" in low["robot_feedback"]["final_message"].casefold()


def test_multi_room_events_have_distinct_icons_and_observation_backed_feedback(tmp_path):
    state = run(ui(tmp_path), "Inspect the kitchen and bathroom and report any problems.", "There is a chair in the kitchen and the bathroom has high humidity.")
    manifest = state["playback"]["frame"]["visual_events"]
    assert {(item["room"], item["visual_representation"]["icon"]) for item in manifest} == {("kitchen", "box"), ("bathroom", "droplet")}
    assert {item["room"] for item in state["robot_feedback"]["detected_anomalies"]} == {"kitchen", "bathroom"}


def test_artifacts_seed_reset_and_run_gate(tmp_path):
    controller = ui(tmp_path)
    controller.interpret_scenario("There is a box in the bedroom.", 3)
    controller.plan("Inspect the bedroom.")
    with pytest.raises(DemoError): controller.run("Inspect the bedroom.", "unsupported scenario", 3)
    controller.validate(); first = controller.run("Inspect the bedroom.", "There is a box in the bedroom.", 3)
    for _ in range(first["playback"]["total"]): first = controller.playback("step")
    artifact = Path(first["artifact_directory"])
    for name in ("natural_language_scenario_input.json", "scenario_planning_result.json", "candidate_scenario.json", "scenario_verification_report.json", "scenario_ground_truth.json", "visual_event_manifest.json", "robot_feedback.json", "robot_feedback.md"):
        assert (artifact / name).is_file()
    assert json.loads((artifact / "robot_feedback.json").read_text())["simulation_only"]
    controller.reset(); assert controller.state()["robot_feedback"] is None and not controller.state()["playback"]["frame"]["visual_events"]
    second = run(ui(tmp_path / "second"), "Inspect the bedroom.", "There is a box in the bedroom.", 3)
    assert first["robot_feedback"] == second["robot_feedback"]


def test_normal_reference_detects_current_scenarios_only_after_their_room_is_visited(tmp_path):
    controller = ui(tmp_path)
    controller.interpret_scenario("There is a box in the bedroom and the bathroom has high humidity.", 7)
    controller.plan("Patrol the whole house and report anything unusual.")
    assert controller.validate()["verification"]["approved"]
    state = controller.run("Patrol the whole house and report anything unusual.", "There is a box in the bedroom and the bathroom has high humidity.", 7)
    assert state["playback"]["frame"]["events"] and not state["playback"]["frame"]["anomalies"]
    while not state["playback"]["frame"]["anomalies"]:
        state = controller.playback("step")
    assert {item["room"] for item in state["playback"]["frame"]["anomalies"]} == {"bathroom"}
    for _ in range(state["playback"]["total"]):
        state = controller.playback("step")
    anomalies = {(item["room"], item["anomaly_type"]) for item in state["robot_feedback"]["detected_anomalies"]}
    assert anomalies == {("bedroom", "unexpected_obstacle"), ("bathroom", "high_humidity")}
    assert "reference:bedroom:7" == state["digital_twin_before"]["rooms"]["bedroom"]["provenance"]["observation_id"]
    assert "unexpected obstacle was detected in the bedroom" in state["robot_feedback"]["final_message"].casefold()
    assert "high humidity was detected" in state["robot_feedback"]["final_message"].casefold()


def test_reference_baseline_does_not_absorb_abnormality_and_explicit_baseline_is_the_only_writer(tmp_path):
    obstacle = run(ui(tmp_path / "obstacle"), "Patrol the bedroom.", "There is a box in the bedroom.", 11)
    assert {item["anomaly_type"] for item in obstacle["robot_feedback"]["detected_anomalies"]} == {"unexpected_obstacle"}
    dropout = run(ui(tmp_path / "dropout"), "Patrol the bedroom.", "The bedroom sensor observation is unavailable.", 11)
    assert dropout["digital_twin_before"]["rooms"]["bedroom"]["provenance"]["observation_id"] == "reference:bedroom:11"
    assert dropout["digital_twin_current"]["rooms"]["bedroom"]["revision"] == 0
    controller = ui(tmp_path / "record")
    controller.interpret_scenario("There is a box in the bedroom.", 11)
    controller.plan("Establish a household baseline in the bedroom.")
    assert controller.validate()["verification"]["approved"]
    state = controller.run("Establish a household baseline in the bedroom.", "There is a box in the bedroom.", 11)
    assert not state["robot_feedback"]["detected_anomalies"]
    baseline = next(item for item in controller.bundle["baseline_observations"] if item["room"] == "bedroom")
    assert baseline["observation_id"].startswith("observation:bedroom:")


def test_temperature_scope_order_cost_and_seed_are_deterministic(tmp_path):
    hot = run(ui(tmp_path / "hot"), "Inspect the kitchen.", "The kitchen is hot.", 19)
    assert {item["anomaly_type"] for item in hot["robot_feedback"]["detected_anomalies"]} == {"high_temperature"}
    hidden = run(ui(tmp_path / "hidden"), "Inspect the bedroom.", "There is a box in the kitchen.", 19)
    assert not hidden["robot_feedback"]["detected_anomalies"] and "kitchen" not in hidden["robot_feedback"]["final_message"].casefold()
    assert hidden["robot_feedback"]["unconfirmed_ground_truth_events"] == {"redacted": True}
    planner = OfflineHouseSitterPlanner(CapabilityRegistry.from_yaml(PROFILE))
    automatic = planner.plan("Patrol the whole house.").candidate_task
    explicit = planner.plan("Inspect the bedroom, then the kitchen.").candidate_task
    assert automatic and explicit
    assert automatic.metadata["optimized_visit_order"] == ["living_room", "kitchen", "bathroom", "bedroom"]
    assert automatic.metadata["planned_route_cost"] < visit_route_cost(["living_room", "kitchen", "bedroom", "bathroom"])
    assert explicit.metadata["visit_order_source"] == "explicit_user_order"
    assert explicit.metadata["optimized_visit_order"] == ["bedroom", "kitchen"]
    assert planner.plan("Check the baseline in the bedroom.").status == "needs_clarification"
    first = run(ui(tmp_path / "one"), "Patrol the whole house.", "There is a box in the bedroom and the bathroom has high humidity.", 19)
    second = run(ui(tmp_path / "two"), "Patrol the whole house.", "There is a box in the bedroom and the bathroom has high humidity.", 19)
    assert first["robot_feedback"] == second["robot_feedback"]
    assert first["summary"]["optimized_visit_order"] == second["summary"]["optimized_visit_order"]
    assert first["summary"]["planned_route"] == first["summary"]["travelled_route"]
