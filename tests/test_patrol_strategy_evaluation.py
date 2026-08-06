"""Contract tests for the local, paired patrol-policy experiment."""
from __future__ import annotations

import hashlib
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.patrol_strategies import (
    CHARGING_ROOM, FIXED_ORDER, PATROL_ROOMS, PatrolMap, choose_battery_aware_room,
    fixed_order_rooms, load_patrol_map, risk_priority_rooms,
)
from house_sitter_core.patrol_strategy_evaluation import (
    ARTIFACTS, _dominates, _paired_comparisons, _pareto_rows, evaluate_patrol_strategies,
    load_patrol_strategy_scenarios, render_patrol_strategy_artifacts, write_patrol_strategy_artifacts,
)
from scripts.run_house_v1_visual_demo import load_house_v1_inputs


ROOT = Path(__file__).resolve().parents[1]


class PatrolStrategyEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.document, cls.scenarios = load_patrol_strategy_scenarios(ROOT)
        cls.patrol_map = load_patrol_map(ROOT)
        cls.result = evaluate_patrol_strategies(ROOT, repeats=2)

    def test_eighteen_unique_complete_scenarios(self):
        self.assertEqual(len(self.scenarios), 18)
        self.assertEqual(len({item["scenario_id"] for item in self.scenarios}), 18)
        self.assertEqual({item["monitoring_case"] for item in self.scenarios}, {
            "no_anomaly", "kitchen_unexpected_obstacle", "bedroom_temperature_anomaly",
            "bathroom_humidity_anomaly", "living_room_layout_change", "multi_room_combined_anomalies",
        })
        self.assertEqual({item["scenario_id"].rsplit("_", 2)[-2] for item in self.scenarios}, {"high", "medium", "constrained"})

    def test_risk_profile_is_predefined_and_strategy_cannot_receive_ground_truth(self):
        profiles = self.document["risk_profiles"]
        self.assertTrue(all(item["room_risk_scores"] == profiles[item["risk_profile_id"]] for item in self.scenarios))
        parameter_names = set(inspect.signature(risk_priority_rooms).parameters)
        self.assertFalse({"injected_room", "injected_events", "expected_ground_truth_events", "expected_anomalies"} & parameter_names)
        self.assertNotIn("injected_room", Path("house_sitter_core/patrol_strategies.py").read_text(encoding="utf-8"))

    def test_fixed_order_and_risk_tie_break_are_stable(self):
        self.assertEqual(FIXED_ORDER, ("living_room", "kitchen", "bedroom", "bathroom", "charging_area"))
        self.assertEqual(fixed_order_rooms(), FIXED_ORDER[:-1])
        labels = PATROL_ROOMS + (CHARGING_ROOM,)
        fake = PatrolMap({(left, right): 1.0 for left in labels for right in labels}, {})
        tied = {room: 1.0 for room in PATROL_ROOMS}
        self.assertEqual(risk_priority_rooms(CHARGING_ROOM, tied, fake), tuple(sorted(PATROL_ROOMS)))

    def test_battery_aware_keeps_return_reserve_and_constrained_skips(self):
        constrained = [item for item in self.result["trials"] if item["battery_level"] == "constrained_battery"]
        self.assertTrue(all(item["returned_to_charging_area"] and item["return_reserve_satisfied"] for item in constrained))
        self.assertTrue(any(item["strategy"] == "battery_aware" and item["skipped_rooms"] for item in constrained))
        scenario = next(item for item in self.scenarios if item["scenario_id"] == "no_anomaly_constrained_battery")
        model = self.document["energy_models"][scenario["energy_model_id"]]
        from house_sitter_core.patrol_strategies import EnergyModel
        energy = EnergyModel(scenario["energy_model_id"], model["travel_energy_per_meter"],
                             model["sensing_energy_per_room"], model["fixed_task_overhead"])
        self.assertIsNone(choose_battery_aware_room(CHARGING_ROOM, 0.0, PATROL_ROOMS, scenario["room_risk_scores"], self.patrol_map, energy, 2.0))

    def test_astar_routes_remain_in_conservative_free_cells(self):
        inputs = load_house_v1_inputs(ROOT)
        for cells in self.patrol_map.route_cells.values():
            self.assertTrue(all(inputs.inflated_free_cells[row, column] for row, column in cells))
        self.assertGreater(self.patrol_map.distance_m("charging_area", "kitchen"), 9.0)

    def test_policies_share_sensor_profile_and_missed_is_not_detector_false_negative(self):
        high = [item for item in self.result["trials"] if item["scenario_id"] == "bathroom_humidity_anomaly_high_battery" and item["repeat_index"] == 1]
        self.assertEqual({item["sensor_observation_profile_id"] for item in high}, {"house_v1_patrol_sensor_profile_v1"})
        skipped = next(item for item in self.result["trials"] if item["scenario_id"] == "bedroom_temperature_anomaly_constrained_battery" and item["strategy"] == "fixed_order")
        self.assertEqual(skipped["missed_anomaly_count"], 1)
        self.assertEqual(skipped["detector_false_negative_count"], 0)
        self.assertTrue(skipped["missed_events"][0]["missed_due_to_patrol_policy"])

    def test_metrics_pairing_pareto_and_repeats_are_deterministic(self):
        trial = next(item for item in self.result["trials"] if item["scenario_id"] == "no_anomaly_constrained_battery" and item["strategy"] == "fixed_order")
        self.assertEqual(trial["coverage_rate"], len(trial["visited_rooms"]) / 4)
        self.assertEqual(trial["total_energy_consumed"], round(trial["travel_energy"] + trial["sensing_energy"] + trial["fixed_task_overhead"], 6))
        paired = _paired_comparisons(self.result["trials"])
        sample = next(item for item in paired if item["scenario_id"] == "no_anomaly_constrained_battery" and item["comparison_strategy"] == "battery_aware")
        self.assertAlmostEqual(sample["coverage_delta"], 0.25)
        self.assertTrue(_dominates({"a": 1.0, "b": 1.0}, {"a": 0.5, "b": 2.0}, "a", True, "b", False))
        self.assertEqual(len(_pareto_rows(self.result["summary"]["by_strategy_and_battery"])), 27)
        self.assertTrue(all(item["deterministic_result"] for item in self.result["trials"]))

    def test_layered_markdown_report_uses_summary_values_and_scope_document_is_explicit(self):
        markdown = render_patrol_strategy_artifacts(self.result)["patrol_strategy_summary.md"]
        for battery_level in ("high_battery", "medium_battery", "constrained_battery"):
            self.assertIn(f"### {battery_level}", markdown)
        for label in ("Anomaly discovery rate", "Mean detection latency", "Mean travel distance (m)",
                      "Mean simulated energy consumption", "Return-to-charging success rate"):
            self.assertIn(label, markdown)
        for row in self.result["summary"]["by_strategy_and_battery"]:
            discovery = "N/A" if row["anomaly_discovery_rate"] is None else f"{row['anomaly_discovery_rate']:.3f}"
            latency = "N/A" if row["mean_detection_latency"] is None else f"{row['mean_detection_latency']:.3f}"
            expected = (f"| {row['strategy']} | {row['mean_coverage_rate']:.3f} | "
                        f"{discovery} | {latency} | "
                        f"{row['mean_travel_distance_m']:.3f} | {row['mean_simulated_energy_consumption']:.3f} | "
                        f"{row['return_to_charging_success_rate']:.3f} | {row['rooms_skipped'] / row['run_count']:.3f} |")
            self.assertIn(expected, markdown)
        documentation = (ROOT / "docs" / "patrol_strategy_experiment.md").read_text(encoding="utf-8")
        self.assertIn("Untuned deterministic patrol-strategy baseline", documentation)
        self.assertIn("不代表真实部署性能", documentation)

    def test_artifacts_are_atomic_and_legacy_experiments_remain_unchanged(self):
        legacy = [ROOT / "evaluation" / "monitoring_scenarios_v1.json", ROOT / "evaluation" / "monitoring_robustness_scenarios_v2.json", ROOT / "docs" / "layout_temporal_filter_experiment.md"]
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in legacy]
        contents = render_patrol_strategy_artifacts(self.result)
        self.assertEqual(set(contents), set(ARTIFACTS))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "experiment"
            paths = write_patrol_strategy_artifacts(output, contents)
            self.assertEqual(set(paths), set(ARTIFACTS))
            self.assertTrue(all(path.is_file() for path in paths.values()))
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in legacy])

    def test_experiment_has_no_ros_gazebo_nav2_network_or_llm_runtime_dependency(self):
        source = (Path("house_sitter_core/patrol_strategy_evaluation.py").read_text(encoding="utf-8") +
                  Path("scripts/evaluate_patrol_strategies.py").read_text(encoding="utf-8"))
        for prohibited in ("ros2 ", "gazebo", "nav2", "requests", "urllib", "socket", "llm_provider"):
            self.assertNotIn(prohibited, source.casefold())


if __name__ == "__main__":
    unittest.main()
