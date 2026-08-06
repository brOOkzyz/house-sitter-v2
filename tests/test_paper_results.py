"""Regression tests for the read-only paper-result materialization layer."""
from __future__ import annotations

import csv
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.paper_results import (
    FIGURES, ROBUSTNESS_ARTIFACTS, PaperResultsError, _regenerate_sources, build_paper_results,
    load_robustness_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


class PaperResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.frozen = [
            ROOT / "house_sitter_core" / "monitoring_robustness_evaluation.py",
            ROOT / "house_sitter_core" / "temporal_filter_comparison.py",
            ROOT / "house_sitter_core" / "patrol_strategy_evaluation.py",
            ROOT / "evaluation" / "monitoring_robustness_scenarios_v2.json",
            ROOT / "evaluation" / "patrol_strategy_scenarios_v1.json",
        ]
        cls.frozen_hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in cls.frozen]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.output = Path(cls.temporary.name) / "paper-results"
        cls.paths = build_paper_results(ROOT, cls.output, regenerate=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_required_outputs_and_figures_exist(self):
        expected = {
            "results_manifest.json", "results_summary.json", "results_chapter_draft.md", "limitations_and_threats.md",
            "tables/robustness_summary.csv", "tables/robustness_summary.md", "tables/robustness_summary.tex",
            "tables/temporal_filter_comparison.csv", "tables/temporal_filter_comparison.md", "tables/temporal_filter_comparison.tex",
            "tables/patrol_strategy_overall.csv", "tables/patrol_strategy_overall.md", "tables/patrol_strategy_overall.tex",
            "tables/patrol_strategy_by_battery.csv", "tables/patrol_strategy_by_battery.md", "tables/patrol_strategy_by_battery.tex",
            "tables/failure_case_summary.csv", "tables/failure_case_summary.md",
        }
        self.assertEqual(set(self.paths), expected)
        self.assertTrue(all((self.output / name).is_file() for name in expected))
        for name in FIGURES:
            self.assertTrue((self.output / "figures" / f"{name}.png").is_file())
            self.assertTrue((self.output / "figures" / f"{name}.pdf").is_file())

    def test_manifest_has_all_hashes_and_correct_run_counts(self):
        manifest = json.loads((self.output / "results_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["regeneration_enabled"])
        self.assertFalse(manifest["statistical_inference_performed"])
        self.assertTrue(manifest["simulation_only"])
        self.assertFalse(manifest["real_robot_supported"])
        self.assertEqual(manifest["experiments"]["robustness"], {"scenario_count": 20, "run_count": 100})
        self.assertEqual(manifest["experiments"]["temporal_filtering"]["run_count"], 200)
        self.assertEqual(manifest["experiments"]["patrol_strategy"], {"scenario_count": 18, "run_count": 270})
        self.assertEqual(set(manifest["input_artifact_sha256"]["robustness"]), set(ROBUSTNESS_ARTIFACTS))
        self.assertEqual(sum(len(group) for group in manifest["input_artifact_sha256"].values()), 17)

    def test_csv_markdown_latex_tables_share_artifact_derived_values(self):
        summary = json.loads((self.output / "results_summary.json").read_text(encoding="utf-8"))
        for table in ("robustness_summary", "temporal_filter_comparison", "patrol_strategy_overall", "patrol_strategy_by_battery"):
            rows = list(csv.DictReader((self.output / "tables" / f"{table}.csv").open(encoding="utf-8")))
            markdown = (self.output / "tables" / f"{table}.md").read_text(encoding="utf-8")
            latex = (self.output / "tables" / f"{table}.tex").read_text(encoding="utf-8")
            self.assertEqual(len(rows), len(summary["table_rows"][table]))
            for csv_row, source_row in zip(rows, summary["table_rows"][table]):
                self.assertIn(next(iter(csv_row.values())), markdown)
                for field, value in source_row.items():
                    if value is None:
                        self.assertEqual(csv_row[field], "")
                    elif isinstance(value, (int, float)):
                        self.assertAlmostEqual(float(csv_row[field]), value)
                    else:
                        self.assertEqual(csv_row[field], str(value))
            self.assertIn("\\begin{table}", latex)

    def test_failures_and_patrol_miss_attribution_are_retained(self):
        rows = list(csv.DictReader((self.output / "tables" / "failure_case_summary.csv").open(encoding="utf-8")))
        text = "\n".join(row["Failure or limitation"] + " " + row["Details"] for row in rows)
        self.assertIn("Transient layout_signature perturbation false positive", text)
        self.assertIn("Single-observation combined layout-change miss retained", text)
        self.assertIn("Recovery confirmation delay or incomplete recovery retained", text)
        self.assertIn("missed_due_to_patrol_policy=", text)
        self.assertIn("detector_false_negative=0", text)

    def test_results_draft_and_limitations_preserve_simulation_scope_without_inference(self):
        chapter = (self.output / "results_chapter_draft.md").read_text(encoding="utf-8")
        limitations = (self.output / "limitations_and_threats.md").read_text(encoding="utf-8")
        self.assertIn("in the deterministic simulation", chapter)
        self.assertIn("does not establish real-world performance", chapter)
        self.assertIn("No p-values, confidence intervals, error bars", chapter)
        for phrase in ("simulation-only", "deterministic simulation models", "not independent randomized experiments", "must not be interpreted as real-world robustness"):
            self.assertIn(phrase, limitations)
        self.assertNotIn("universally optimal", limitations)
        self.assertIn("No single patrol strategy dominated across all evaluated objectives.", limitations)
        self.assertIn("deterministic-simulation trade-off analysis", limitations)

    def test_missing_artifact_and_summary_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PaperResultsError):
                load_robustness_artifacts(Path(directory))
        with tempfile.TemporaryDirectory() as directory:
            sources = _regenerate_sources(ROOT, Path(directory))
            summary_path = sources["robustness"] / "robustness_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["event_precision"] = 0.0
            summary_path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaises(PaperResultsError):
                load_robustness_artifacts(sources["robustness"])
        with tempfile.TemporaryDirectory() as directory:
            sources = _regenerate_sources(ROOT, Path(directory))
            trials_path = sources["robustness"] / "robustness_trials.csv"
            rows = list(csv.DictReader(trials_path.open(encoding="utf-8")))
            rows[0]["true_positive"] = "999"
            with trials_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(PaperResultsError):
                load_robustness_artifacts(sources["robustness"])

    def test_existing_artifact_directory_mode_records_verifiable_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            sources = _regenerate_sources(ROOT, temporary / "sources")
            output = temporary / "output"
            build_paper_results(ROOT, output, robustness_dir=sources["robustness"], temporal_dir=sources["temporal"], patrol_dir=sources["patrol"])
            manifest = json.loads((output / "results_manifest.json").read_text(encoding="utf-8"))
            self.assertFalse(manifest["regeneration_enabled"])
            for group, artifact_hashes in manifest["input_artifact_sha256"].items():
                for name, digest in artifact_hashes.items():
                    self.assertEqual(digest, hashlib.sha256((sources[group] / name).read_bytes()).hexdigest())

    def test_new_layer_does_not_edit_or_depend_on_frozen_implementations(self):
        self.assertEqual(self.frozen_hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in self.frozen])
        source = (ROOT / "house_sitter_core" / "paper_results.py").read_text(encoding="utf-8").casefold()
        for prohibited in ("requests", "urllib", "socket", "llm_provider", "rclpy", "ros2cli"):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
