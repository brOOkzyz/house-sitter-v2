"""Synthetic, ROS-free tests for automatic semantic-area proposals."""

import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from house_sitter_core.automatic_area_proposal import (
    AutomaticAreaProposalError,
    _proposal_record,
    _raster_safety_check,
    build_hole_aware_review_batch,
    build_confirmed_registry_draft,
    classify_occupancy,
    proposal_report,
    propose_semantic_areas,
    safe_candidates_report,
    write_all_safe_candidates_preview,
    write_preview,
)
from house_sitter_core.map_coordinates import map_to_pixel
from house_sitter_core.map_metadata import PgmImage, RosMapMetadata
from house_sitter_core.semantic_waypoints import SemanticWaypointError, SemanticWaypointRegistry


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_REGISTRY = PROJECT_ROOT / "config" / "semantic_waypoints.json"
FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "automatic_area_proposal_test.json"


class AutomaticAreaProposalTests(unittest.TestCase):
    def metadata(self, pixels: np.ndarray, *, negate: int = 0) -> RosMapMetadata:
        height, width = pixels.shape
        return RosMapMetadata(
            yaml_path=Path("synthetic.yaml"),
            image_path=Path("synthetic.pgm"),
            image=PgmImage(width, height, pixels.astype(np.uint8).tobytes(), "P2", 255),
            resolution=0.1,
            origin=(0.0, 0.0, 0.0),
            negate=negate,
            occupied_thresh=0.65,
            free_thresh=0.196,
        )

    def free_map(self, height: int = 24, width: int = 36) -> np.ndarray:
        return np.zeros((height, width), dtype=np.uint8)

    def propose(self, pixels: np.ndarray, **kwargs):
        parameters = {
            "minimum_area_m2": 0.1,
            "doorway_width_m": 0.4,
            "simplify_tolerance_m": 0.05,
        }
        parameters.update(kwargs)
        return propose_semantic_areas(self.metadata(pixels), **parameters)

    def hole_aware(self, pixels: np.ndarray, **kwargs):
        parameters = {
            "minimum_area_m2": 0.1,
            "doorway_width_m": 0.4,
            "simplify_tolerance_m": 0.05,
            "proposal_mode": "hole-aware-cells",
            "maximum_proposal_count": 12,
        }
        parameters.update(kwargs)
        return propose_semantic_areas(self.metadata(pixels), **parameters)

    def hole_aware_batch(self, pixels: np.ndarray, **kwargs):
        parameters = {
            "minimum_area_m2": 0.1,
            "doorway_width_m": 0.4,
            "simplify_tolerance_m": 0.05,
            "maximum_proposal_count": 2,
        }
        parameters.update(kwargs)
        return build_hole_aware_review_batch(self.metadata(pixels), **parameters)

    def test_synthetic_fixture_is_explicitly_test_only(self):
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertTrue(fixture["test_fixture"])
        self.assertIn("fictional", fixture["description"].lower())

    def test_occupancy_classification_thresholds_unknown_and_negate(self):
        metadata = self.metadata(np.array([[0, 254, 205]], dtype=np.uint8))
        masks = classify_occupancy(metadata)
        self.assertEqual(masks.occupied.tolist(), [[True, False, False]])
        self.assertEqual(masks.free.tolist(), [[False, True, False]])
        self.assertEqual(masks.unknown.tolist(), [[False, False, True]])
        negated = classify_occupancy(self.metadata(np.array([[0, 254]], dtype=np.uint8), negate=1))
        self.assertEqual(negated.free.tolist(), [[True, False]])
        boundary = self.metadata(np.array([[128]], dtype=np.uint8))
        boundary = RosMapMetadata(**{**boundary.__dict__, "free_thresh": 127 / 255, "occupied_thresh": 1.0})
        self.assertTrue(classify_occupancy(boundary).unknown[0, 0])

    def test_single_enclosed_room_has_valid_proposal_and_area(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        proposals = self.propose(pixels)
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertEqual(proposal.proposal_id, "proposal_1")
        self.assertEqual(proposal.pixel_area, 16 * 16)
        self.assertAlmostEqual(proposal.map_area_m2, 2.56)
        self.assertEqual(proposal.proposed_label, None)
        self.assertEqual(proposal.label_status, "unassigned")
        self.assertTrue(proposal.requires_confirmation)
        for vertex in proposal.map_polygon:
            self.assertEqual(map_to_pixel(self.metadata(pixels), *vertex), proposal.pixel_polygon[proposal.map_polygon.index(vertex)])

    def test_narrow_doorway_splits_and_wide_connection_remains_joined(self):
        narrow = self.free_map()
        narrow[3:20, 3:14] = 254
        narrow[3:20, 20:31] = 254
        narrow[10:12, 14:20] = 254
        self.assertEqual(len(self.propose(narrow)), 2)
        wide = self.free_map()
        wide[3:20, 3:14] = 254
        wide[3:20, 20:31] = 254
        wide[7:16, 14:20] = 254
        self.assertEqual(len(self.propose(wide)), 1)

    def test_small_noise_unknown_and_border_outside_do_not_create_proposals(self):
        pixels = self.free_map()
        pixels[4:18, 4:18] = 254
        pixels[1:3, 25:27] = 254
        pixels[8:12, 8:12] = 205
        proposals = self.propose(pixels)
        self.assertTrue(all(proposal.pixel_area >= 10 for proposal in proposals))
        self.assertLess(sum(proposal.pixel_area for proposal in proposals), 14 * 14)

    def test_concave_region_and_stable_sorting(self):
        pixels = self.free_map()
        pixels[3:16, 3:9] = 254
        pixels[10:16, 9:16] = 254
        pixels[3:10, 22:32] = 254
        proposals = self.propose(pixels)
        self.assertEqual([proposal.proposal_id for proposal in proposals], ["proposal_1", "proposal_2"])
        self.assertLess(proposals[0].centroid_pixel[1], proposals[1].centroid_pixel[1])

    def test_elongated_geometry_is_only_low_confidence_candidate(self):
        pixels = self.free_map()
        pixels[8:14, 3:31] = 254
        proposal = self.propose(pixels)[0]
        self.assertEqual(proposal.proposed_label, "hallway")
        self.assertEqual(proposal.label_status, "low_confidence_candidate")
        self.assertLess(proposal.label_confidence, 0.5)
        self.assertTrue(proposal.requires_confirmation)

    def test_report_preview_and_no_confirmed_draft(self):
        pixels = np.zeros((100, 240), dtype=np.uint8)
        pixels[55:75, 60:90] = 254
        metadata = self.metadata(pixels)
        proposals = self.propose(pixels)
        report = proposal_report(metadata, proposals, map_id="synthetic_map", algorithm_parameters={"doorway_width_m": 0.4})
        self.assertTrue(report["requires_human_review"])
        self.assertEqual(report["generated_by"], "automatic_area_proposal")
        with tempfile.TemporaryDirectory() as tmpdir:
            preview = write_preview(Path(tmpdir) / "preview.png", metadata, proposals)
            image = Image.open(preview)
            self.assertEqual(image.getpixel((0, 0)), (35, 35, 35))
            self.assertEqual(image.getpixel((70, 65)), (245, 245, 245))
        with self.assertRaisesRegex(AutomaticAreaProposalError, "No confirmed"):
            build_confirmed_registry_draft(SemanticWaypointRegistry(PRODUCTION_REGISTRY), proposals, map_id="synthetic_map")

    def test_confirmed_draft_requires_existing_label_and_concrete_evidence(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        proposal = self.propose(pixels)[0]
        registry = SemanticWaypointRegistry(PRODUCTION_REGISTRY)
        missing_evidence = replace(
            proposal, proposed_label="hallway", label_status="confirmed_from_existing_evidence", label_evidence=()
        )
        with self.assertRaisesRegex(AutomaticAreaProposalError, "concrete"):
            build_confirmed_registry_draft(registry, [missing_evidence], map_id="synthetic_map")
        unknown_label = replace(
            proposal, proposed_label="balcony", label_status="confirmed_from_existing_evidence", label_evidence=("test",)
        )
        with self.assertRaisesRegex(AutomaticAreaProposalError, "existing canonical"):
            build_confirmed_registry_draft(registry, [unknown_label], map_id="synthetic_map")

    def test_dry_run_writes_no_output_and_invalid_parameter_is_concise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            (directory / "map.pgm").write_bytes(b"P2\n4 4\n255\n0 0 0 0 0 254 254 0 0 254 254 0 0 0 0 0\n")
            (directory / "map.yaml").write_text("image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "auto_propose_semantic_areas.py"), "--map", str(directory / "map.yaml"), "--map-id", "synthetic", "--dry-run"],
                cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("candidate_count", result.stdout)
            self.assertFalse((directory / "auto_area_proposals.json").exists())
            invalid = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "auto_propose_semantic_areas.py"), "--map", str(directory / "map.yaml"), "--map-id", "synthetic", "--minimum-area-m2", "0", "--dry-run"],
                cwd=PROJECT_ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("minimum_area_m2", invalid.stderr)
            self.assertNotIn("Traceback", invalid.stderr)

    def test_production_registry_and_local_output_policy_remain_safe(self):
        before = PRODUCTION_REGISTRY.read_text(encoding="utf-8")
        self.assertEqual(PRODUCTION_REGISTRY.read_text(encoding="utf-8"), before)
        self.assertIn("local_annotations/", (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8"))

    def test_hole_aware_rectangle_is_one_safe_unassigned_zone(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        proposals = self.hole_aware(pixels)
        self.assertEqual(len(proposals), 1)
        proposal = proposals[0]
        self.assertIsNone(proposal.proposed_label)
        self.assertEqual(proposal.label_status, "proposed")
        self.assertLessEqual(proposal.confidence, 0.25)
        self.assertIn("automatic_observed_free_space_partition", proposal.label_evidence)
        self.assertTrue(proposal.raster_safety["raster_safety_passed"])
        self.assertEqual(proposal.raster_safety["interior_occupied_count"], 0)
        self.assertEqual(proposal.raster_safety["interior_unknown_count"], 0)

    def test_hole_aware_obstacle_and_unknown_islands_are_not_enclosed(self):
        pixels = self.free_map(40, 40)
        pixels[3:37, 3:37] = 254
        pixels[15:23, 15:23] = 0
        pixels[5:10, 27:33] = 205
        proposals = self.hole_aware(pixels)
        self.assertGreaterEqual(len(proposals), 1)
        for proposal in proposals:
            self.assertEqual(proposal.raster_safety["interior_occupied_count"], 0)
            self.assertEqual(proposal.raster_safety["interior_unknown_count"], 0)
            self.assertEqual(proposal.raster_safety["interior_non_partition_count"], 0)

    def test_hole_aware_ring_never_returns_a_polygon_with_a_hole(self):
        pixels = self.free_map(36, 36)
        pixels[3:33, 3:33] = 254
        pixels[12:24, 12:24] = 0
        proposals = self.hole_aware(pixels)
        self.assertGreaterEqual(len(proposals), 1)
        for proposal in proposals:
            self.assertTrue(proposal.raster_safety["raster_safety_passed"])
            self.assertEqual(proposal.raster_safety["interior_occupied_count"], 0)

    def test_hole_aware_narrow_doorway_and_open_space_fragment_limit(self):
        narrow = self.free_map(40, 48)
        narrow[4:30, 4:19] = 254
        narrow[4:30, 28:43] = 254
        narrow[15:18, 19:28] = 254
        self.assertGreaterEqual(len(self.hole_aware(narrow)), 2)
        open_space = self.free_map(70, 70)
        open_space[4:66, 4:66] = 254
        proposals = self.hole_aware(open_space, maximum_proposal_count=4)
        self.assertLessEqual(len(proposals), 4)

    def test_raster_safety_rejects_polygon_crossing_an_occupied_wall(self):
        pixels = self.free_map(20, 20)
        pixels[2:18, 2:18] = 254
        pixels[8:12, 2:18] = 0
        metadata = self.metadata(pixels)
        classification = classify_occupancy(metadata)
        region = classification.free
        result = _raster_safety_check(
            classification, region, ((2.0, 2.0), (17.0, 2.0), (17.0, 17.0), (2.0, 17.0))
        )
        self.assertFalse(result["raster_safety_passed"])
        self.assertGreater(result["interior_occupied_count"], 0)

    def test_hole_aware_mode_is_reported_and_never_eligible_for_confirmed_draft(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        metadata = self.metadata(pixels)
        proposals = self.hole_aware(pixels)
        report = proposal_report(
            metadata,
            proposals,
            map_id="synthetic_map",
            algorithm_parameters={"proposal_mode": "hole-aware-cells"},
        )
        self.assertEqual(report["proposal_mode"], "hole-aware-cells")
        self.assertIn("cannot enter the production registry", report["registry_compatibility"])
        with self.assertRaisesRegex(AutomaticAreaProposalError, "No confirmed"):
            build_confirmed_registry_draft(
                SemanticWaypointRegistry(PRODUCTION_REGISTRY), proposals, map_id="synthetic_map"
            )

    def test_hole_aware_safe_candidates_are_separate_from_selected_batch(self):
        pixels = self.free_map(80, 80)
        for y, x in ((5, 5), (5, 30), (5, 55), (35, 5), (35, 30), (35, 55)):
            pixels[y:y + 18, x:x + 18] = 254
        batch = self.hole_aware_batch(pixels, maximum_proposal_count=2)
        self.assertGreater(len(batch.safe_candidates), len(batch.selected_proposals))
        self.assertEqual(sum(item.selected_for_review for item in batch.safe_candidates), 2)
        self.assertEqual(
            {item.candidate_id for item in batch.selected_proposals},
            {item.candidate_id for item in batch.safe_candidates if item.selected_for_review},
        )
        report = safe_candidates_report(
            self.metadata(pixels), batch, map_id="synthetic_map", algorithm_parameters={}
        )
        self.assertEqual(report["safe_candidate_count"], len(batch.safe_candidates))
        self.assertEqual(report["unselected_safe_count"], len(batch.safe_candidates) - 2)
        self.assertTrue(all(item["proposal_id"] for item in report["safe_candidates"]))
        self.assertTrue(all(item["polygon_validation"] for item in report["safe_candidates"]))
        self.assertTrue(all(item["raster_safety_passed"] for item in report["safe_candidates"]))

    def test_largest_first_and_spatial_balanced_are_deterministic(self):
        pixels = self.free_map(120, 120)
        # Two largest cells deliberately occupy one spatial bucket.  The
        # balanced selector should show another part of the map in a two-cell batch.
        for y, x, size in ((4, 4, 24), (4, 35, 23), (4, 84, 18), (80, 4, 18), (80, 84, 18)):
            pixels[y:y + size, x:x + size] = 254
        largest_one = self.hole_aware_batch(pixels, maximum_proposal_count=2, selection_strategy="largest-first")
        largest_two = self.hole_aware_batch(pixels, maximum_proposal_count=2, selection_strategy="largest-first")
        balanced_one = self.hole_aware_batch(pixels, maximum_proposal_count=2, selection_strategy="spatial-balanced")
        balanced_two = self.hole_aware_batch(pixels, maximum_proposal_count=2, selection_strategy="spatial-balanced")
        self.assertEqual(largest_one.selected_proposals, largest_two.selected_proposals)
        self.assertEqual(balanced_one.selected_proposals, balanced_two.selected_proposals)
        self.assertEqual(
            {item.candidate_id for item in largest_one.safe_candidates},
            {item.candidate_id for item in balanced_one.safe_candidates},
        )
        self.assertNotEqual(
            {item.candidate_id for item in largest_one.selected_proposals},
            {item.candidate_id for item in balanced_one.selected_proposals},
        )

    def test_zero_maximum_selects_all_safe_and_negative_is_rejected(self):
        pixels = self.free_map(80, 80)
        for y, x in ((5, 5), (5, 35), (35, 5), (35, 35)):
            pixels[y:y + 18, x:x + 18] = 254
        batch = self.hole_aware_batch(
            pixels, maximum_proposal_count=0, selection_strategy="spatial-balanced"
        )
        self.assertEqual(len(batch.safe_candidates), len(batch.selected_proposals))
        self.assertTrue(all(item.selected_for_review for item in batch.safe_candidates))
        with self.assertRaisesRegex(AutomaticAreaProposalError, "non-negative"):
            self.hole_aware_batch(pixels, maximum_proposal_count=-1)

    def test_hole_aware_report_has_explicit_review_only_validation_fields(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        batch = self.hole_aware_batch(pixels, maximum_proposal_count=1)
        report = proposal_report(
            self.metadata(pixels),
            list(batch.selected_proposals),
            map_id="synthetic_map",
            algorithm_parameters={"proposal_mode": "hole-aware-cells"},
            review_batch=batch,
        )
        row = report["proposals"][0]
        self.assertIsNone(row["canonical_label"])
        self.assertEqual(row["suggested_label"], "unassigned")
        self.assertEqual(row["status"], "proposed")
        self.assertTrue(row["polygon_validation"])
        self.assertTrue(row["raster_safety_passed"])
        self.assertEqual(report["selection_strategy"], "largest-first")

    def test_polygon_validation_output_reads_saved_validator_evidence(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        batch = self.hole_aware_batch(pixels, maximum_proposal_count=1)
        proposal = batch.safe_candidates[0]
        self.assertTrue(proposal.polygon_validation_passed)
        self.assertTrue(_proposal_record(proposal)["polygon_validation"])
        # Serialization must not invent a passing validator result for an
        # object that does not carry this internal validation evidence.
        unvalidated = replace(proposal, polygon_validation_passed=False)
        self.assertFalse(_proposal_record(unvalidated)["polygon_validation"])
        self.assertTrue(_proposal_record(unvalidated)["raster_safety_passed"])

    def test_validator_rejection_cannot_enter_safe_candidates(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        with mock.patch(
            "house_sitter_core.automatic_area_proposal.SemanticWaypointRegistry.validate_polygon_geometry",
            side_effect=SemanticWaypointError("synthetic validator rejection"),
        ):
            batch = self.hole_aware_batch(pixels, maximum_proposal_count=1)
        self.assertEqual(batch.safe_candidates, ())
        self.assertEqual(batch.selected_proposals, ())

    def test_hole_aware_preview_contains_review_only_text(self):
        pixels = self.free_map()
        pixels[3:19, 4:20] = 254
        batch = self.hole_aware_batch(pixels, maximum_proposal_count=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_preview(
                Path(tmpdir) / "selected.png", self.metadata(pixels), list(batch.selected_proposals),
                proposal_mode="hole-aware-cells", safe_candidate_count=len(batch.safe_candidates),
                all_safe_candidates=list(batch.safe_candidates),
            )
            all_path = write_all_safe_candidates_preview(
                Path(tmpdir) / "all.png", self.metadata(pixels), list(batch.safe_candidates),
                selection_strategy="largest-first",
            )
            self.assertTrue(path.exists())
            self.assertTrue(all_path.exists())
            notice = Image.open(path).info["review_notice"].lower()
            self.assertIn("observed free-space zones", notice)
            self.assertIn("review only", notice)
            self.assertIn("not rooms", notice)


if __name__ == "__main__":
    unittest.main()
