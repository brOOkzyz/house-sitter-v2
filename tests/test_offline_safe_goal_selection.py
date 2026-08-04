"""ROS-free unit tests for review-only offline safe-goal selection."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

from house_sitter_core.map_coordinates import pixel_to_map
from house_sitter_core.map_metadata import PgmImage, RosMapMetadata, load_ros_map, map_identity
from house_sitter_core.offline_safe_goal_selection import (
    OfflineSafeGoalSelectionError,
    candidates_from_report,
    load_candidate_report,
    safe_goal_report,
    select_offline_safe_goals,
    validate_report_map_identity,
    write_safe_goal_artifacts,
    write_safe_goal_preview,
)
import house_sitter_core.offline_safe_goal_selection as safe_goal_module
from house_sitter_core.semantic_waypoints import SemanticWaypointError


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class OfflineSafeGoalSelectionTests(unittest.TestCase):
    def metadata(self, pixels: np.ndarray) -> RosMapMetadata:
        height, width = pixels.shape
        return RosMapMetadata(
            yaml_path=Path("synthetic.yaml"),
            image_path=Path("synthetic.pgm"),
            image=PgmImage(width, height, pixels.astype(np.uint8).tobytes(), "P2", 255),
            resolution=0.1,
            origin=(1.0, -2.0, 0.0),
            negate=0,
            occupied_thresh=0.65,
            free_thresh=0.196,
        )

    def map_pixels(self, *, height: int = 20, width: int = 20) -> np.ndarray:
        pixels = np.zeros((height, width), dtype=np.uint8)
        pixels[2:-2, 2:-2] = 254
        return pixels

    def candidate(self, metadata: RosMapMetadata, *, proposal_id: str = "proposal_1", partition_id: str = "partition_1", validation: bool = True, raster: bool = True, pixels=((3, 3), (16, 3), (16, 16), (3, 16))) -> dict:
        vertices = [list(pixel_to_map(metadata, column, row)) for column, row in pixels]
        return {
            "proposal_id": proposal_id,
            "partition_id": partition_id,
            "geometry": {"type": "polygon", "vertices": vertices},
            "polygon_validation": validation,
            "raster_safety_passed": raster,
            "canonical_label": None,
            "suggested_label": "unassigned",
            "status": "proposed",
        }

    def tree_snapshot(self, directory: Path) -> dict[str, bytes]:
        return {
            str(path.relative_to(directory)): path.read_bytes()
            for path in sorted(directory.rglob("*"))
            if path.is_file()
        }

    def assert_no_transaction_residue(self, parent: Path, output: Path) -> None:
        self.assertEqual(list(parent.glob(f".{output.name}.staging-*")), [])
        self.assertEqual(list(parent.glob(f".{output.name}.backup-*")), [])
        self.assertEqual(list(parent.glob(f".{output.name}.rollback-*")), [])

    def test_rectangle_selects_deterministic_maximum_clearance_free_point(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0.3)
        self.assertEqual(len(result.goals), 1)
        self.assertEqual(len(result.rejected_safe_goals), 0)
        goal = result.goals[0]["goal"]
        self.assertEqual((goal["pixel_row"], goal["pixel_column"]), (9, 9))
        self.assertGreaterEqual(goal["clearance_m"], 0.3)
        self.assertTrue(result.goals[0]["polygon_validation_passed"])
        self.assertTrue(result.goals[0]["faster_safety_passed"])

    def test_goal_is_inside_bounds_free_and_coordinate_round_trips(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        goal = result.goals[0]["goal"]
        self.assertGreaterEqual(goal["pixel_row"], 0)
        self.assertLess(goal["pixel_row"], metadata.image.height)
        self.assertGreaterEqual(goal["pixel_column"], 0)
        self.assertLess(goal["pixel_column"], metadata.image.width)
        self.assertEqual(self.map_pixels()[goal["pixel_row"], goal["pixel_column"]], 254)
        self.assertEqual(
            pixel_to_map(metadata, goal["pixel_column"], goal["pixel_row"]),
            (goal["map_x"], goal["map_y"]),
        )

    def test_forged_raster_safety_cannot_accept_occupied_or_unknown_polygon(self):
        pixels = self.map_pixels()
        pixels[9, 9] = 0
        pixels[9, 10] = 205
        metadata = self.metadata(pixels)
        candidate = self.candidate(metadata)
        candidate["faster_safety_passed"] = True
        result = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
        self.assertEqual(result.goals, ())
        rejection = result.rejected_safe_goals[0]
        self.assertEqual(rejection["rejection_stage"], "raster_safety")
        self.assertIn("occupied", rejection["rejection_reason"])
        self.assertIn("unknown", rejection["rejection_reason"])
        self.assertFalse(rejection["relevant_safety_statistics"]["passed"])
        self.assertFalse(rejection["faster_safety_passed"])

    def test_insufficient_clearance_and_empty_interior_reject_without_partial_goal(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=5.0)
        self.assertEqual(result.goals, ())
        self.assertEqual(len(result.rejected_safe_goals), 1)
        self.assertEqual(result.rejected_safe_goals[0]["rejection_stage"], "clearance_selection")

    def test_forged_or_wrong_typed_source_safety_fields_never_authorize_selection(self):
        metadata = self.metadata(self.map_pixels())
        for value in ("true", 1, None, {}, []):
            candidate = self.candidate(metadata, validation=value, raster=value)
            candidate["faster_safety_passed"] = value
            candidate["geometry"] = {"type": "polygon", "vertices": [[-1, -1], [0.5, -1], [0.5, 0.5]]}
            result = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
            self.assertEqual(result.goals, ())
            self.assertEqual(result.rejected_safe_goals[0]["rejection_stage"], "map_bounds")

    def test_validator_failure_cannot_enter_safe_goals(self):
        metadata = self.metadata(self.map_pixels())
        with mock.patch(
            "house_sitter_core.offline_safe_goal_selection.SemanticWaypointRegistry.validate_polygon_geometry",
            side_effect=SemanticWaypointError("validator rejected test polygon"),
        ):
            result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        self.assertEqual(result.goals, ())
        evidence = result.rejected_safe_goals[0]["relevant_safety_statistics"]
        self.assertFalse(evidence["polygon_validation_passed"])
        self.assertIn("validator rejected", result.rejected_safe_goals[0]["rejection_reason"])

    def test_repeated_runs_and_tie_break_are_deterministic(self):
        metadata = self.metadata(self.map_pixels())
        candidate = self.candidate(metadata)
        first = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
        second = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
        self.assertEqual(first, second)
        self.assertEqual((first.goals[0]["goal"]["pixel_row"], first.goals[0]["goal"]["pixel_column"]), (9, 9))

    def test_selected_and_all_safe_sources_are_explicit_and_safe_unselected_is_not_rejected(self):
        metadata = self.metadata(self.map_pixels())
        selected = self.candidate(metadata, proposal_id="proposal_1", pixels=((3, 3), (9, 3), (9, 16), (3, 16)))
        unselected = self.candidate(
            metadata, proposal_id="candidate_2", partition_id="partition_2", pixels=((11, 3), (16, 3), (16, 16), (11, 16))
        )
        document = {"proposals": [selected], "safe_candidates": [selected, unselected]}
        selected_records = candidates_from_report(document, "selected")
        all_records = candidates_from_report(document, "all-safe")
        self.assertEqual(len(selected_records), 1)
        self.assertEqual(len(all_records), 2)
        all_result = select_offline_safe_goals(metadata, all_records, minimum_clearance_m=0, candidate_source="all-safe")
        self.assertEqual(len(all_result.goals), 2)
        self.assertEqual(len(all_result.rejected_safe_goals), 0)
        self.assertEqual([goal["goal_order"] for goal in all_result.goals], [1, 2])
        self.assertEqual([goal["source_candidate_order"] for goal in all_result.goals], [2, 1])
        self.assertEqual([goal["source_selection_rank"] for goal in all_result.goals], [None, None])

    def test_empty_input_and_invalid_clearance_are_handled(self):
        metadata = self.metadata(self.map_pixels())
        empty = select_offline_safe_goals(metadata, [], minimum_clearance_m=0)
        self.assertEqual(empty.goals, ())
        self.assertEqual(empty.rejected_safe_goals, ())
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "minimum_clearance_m"):
            select_offline_safe_goals(metadata, [], minimum_clearance_m=-0.1)
        report = safe_goal_report(metadata, empty, map_id="test_map", input_candidate_count=0)
        self.assertTrue(report["review_only"])
        self.assertFalse(report["executable"])

    def test_invalid_polygon_cannot_enter_safe_goals_even_when_source_claims_validation(self):
        metadata = self.metadata(self.map_pixels())
        candidate = self.candidate(metadata)
        candidate["geometry"] = {"type": "polygon", "vertices": [[1.0, -1.0], [1.2, -1.0]]}
        result = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
        self.assertEqual(result.goals, ())
        self.assertEqual(result.rejected_safe_goals[0]["rejection_stage"], "polygon_validation")

    def test_out_of_bounds_polygon_is_rejected_with_current_map_evidence(self):
        metadata = self.metadata(self.map_pixels())
        candidate = self.candidate(metadata)
        candidate["geometry"] = {"type": "polygon", "vertices": [[-1.0, -1.0], [0.5, -1.0], [0.5, 0.5]]}
        result = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
        self.assertEqual(result.goals, ())
        evidence = result.rejected_safe_goals[0]["relevant_safety_statistics"]
        self.assertTrue(evidence["polygon_validation_passed"])
        self.assertFalse(evidence["bounds_validation_passed"])
        self.assertFalse(evidence["raster_evaluation_completed"])
        self.assertIsNone(evidence["rasterized_pixel_count"])
        self.assertIsNone(evidence["out_of_bounds_count"])

    def test_duplicate_ids_fail_closed_and_duplicate_polygon_is_rejected(self):
        metadata = self.metadata(self.map_pixels())
        first = self.candidate(metadata)
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "duplicate proposal_id"):
            select_offline_safe_goals(metadata, [first, dict(first)], minimum_clearance_m=0)
        duplicate_partition = self.candidate(metadata, proposal_id="proposal_partition_copy")
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "duplicate partition_id"):
            select_offline_safe_goals(metadata, [first, duplicate_partition], minimum_clearance_m=0)
        duplicate_polygon = self.candidate(
            metadata,
            proposal_id="proposal_2",
            partition_id="partition_2",
            pixels=((16, 16), (16, 3), (3, 3), (3, 16)),
        )
        result = select_offline_safe_goals(metadata, [first, duplicate_polygon], minimum_clearance_m=0)
        self.assertEqual(len(result.goals), 1)
        self.assertEqual(result.rejected_safe_goals[0]["rejection_reason"], "duplicate_polygon")

    def test_duplicate_goal_pixel_keeps_first_normalized_candidate(self):
        metadata = self.metadata(self.map_pixels())
        first = self.candidate(metadata, proposal_id="proposal_a", partition_id="partition_a")
        second = self.candidate(
            metadata,
            proposal_id="proposal_b",
            partition_id="partition_b",
            pixels=((3, 4), (16, 4), (16, 15), (3, 15)),
        )
        result = select_offline_safe_goals(metadata, [second, first], minimum_clearance_m=0)
        self.assertEqual(len(result.goals), 1)
        self.assertEqual(result.goals[0]["proposal_id"], "proposal_a")
        self.assertEqual(result.rejected_safe_goals[0]["rejection_reason"], "duplicate_goal_pixel")

    def test_maximum_clearance_precedes_centroid(self):
        pixels = self.map_pixels()
        pixels[2:18, 2] = 0
        metadata = self.metadata(pixels)
        result = select_offline_safe_goals(
            metadata,
            [self.candidate(metadata, pixels=((3, 3), (12, 3), (12, 16), (3, 16)))],
            minimum_clearance_m=0,
        )
        goal = result.goals[0]["goal"]
        self.assertGreater(goal["pixel_column"], 8)

    def test_candidate_source_requires_matching_report_collection(self):
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "safe_candidates"):
            candidates_from_report({"proposals": []}, "all-safe")
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "proposals"):
            candidates_from_report({"safe_candidates": []}, "selected")

    def test_map_identity_matches_and_all_mismatches_fail_closed(self):
        metadata = self.metadata(self.map_pixels())
        identity = map_identity(metadata).as_dict()
        self.assertEqual(validate_report_map_identity({"map_identity": identity}, metadata), identity)
        for field, value in {
            "width": identity["width"] + 1,
            "height": identity["height"] + 1,
            "resolution": 0.2,
            "origin": [2.0, -2.0, 0.0],
            "negate": 1,
            "occupied_thresh": 0.7,
            "free_thresh": 0.1,
            "image_sha256": "0" * 64,
            "fingerprint": "1" * 64,
        }.items():
            changed = dict(identity)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(OfflineSafeGoalSelectionError, "map_identity mismatch"):
                validate_report_map_identity({"map_identity": changed}, metadata)
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "missing"):
            validate_report_map_identity({}, metadata)
        bad_type = dict(identity)
        bad_type["width"] = "20"
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "invalid type"):
            validate_report_map_identity({"map_identity": bad_type}, metadata)

    def test_every_map_identity_field_rejects_missing_wrong_type_and_mismatch(self):
        metadata = self.metadata(self.map_pixels())
        identity = map_identity(metadata).as_dict()
        for field, value in identity.items():
            missing = dict(identity)
            del missing[field]
            with self.subTest(field=field, kind="missing"), self.assertRaisesRegex(OfflineSafeGoalSelectionError, "map_identity"):
                validate_report_map_identity({"map_identity": missing}, metadata)
            wrong_type = dict(identity)
            wrong_type[field] = {} if field != "origin" else "not-a-list"
            with self.subTest(field=field, kind="type"), self.assertRaisesRegex(OfflineSafeGoalSelectionError, "invalid"):
                validate_report_map_identity({"map_identity": wrong_type}, metadata)
            mismatch = dict(identity)
            mismatch[field] = "old" if isinstance(value, str) else ([9.0, 8.0, 7.0] if field == "origin" else value + 1)
            with self.subTest(field=field, kind="mismatch"), self.assertRaisesRegex(OfflineSafeGoalSelectionError, "mismatch"):
                validate_report_map_identity({"map_identity": mismatch}, metadata)

    def test_partial_and_complete_bounds_fail_after_validator_without_raster_counts(self):
        metadata = self.metadata(self.map_pixels())
        partial = self.candidate(metadata)
        partial["geometry"]["vertices"][0] = [-1.0, -1.0]
        complete = self.candidate(metadata, proposal_id="complete", partition_id="complete")
        complete["geometry"] = {"type": "polygon", "vertices": [[-4, -4], [-3, -4], [-3, -3]]}
        for candidate in (partial, complete):
            result = select_offline_safe_goals(metadata, [candidate], minimum_clearance_m=0)
            evidence = result.rejected_safe_goals[0]["relevant_safety_statistics"]
            self.assertEqual(result.rejected_safe_goals[0]["rejection_stage"], "map_bounds")
            self.assertTrue(evidence["polygon_validation_passed"])
            self.assertFalse(evidence["bounds_validation_passed"])
            self.assertFalse(evidence["raster_evaluation_completed"])
            self.assertTrue(all(evidence[field] is None for field in ("rasterized_pixel_count", "free_count", "occupied_count", "unknown_count", "out_of_bounds_count", "safe_free_ratio")))

    def test_invalid_json_report_is_rejected_concisely(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            invalid = Path(tmpdir) / "invalid.json"
            invalid.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "Cannot load candidate report"):
                load_candidate_report(invalid, "selected")

    def test_artifacts_preserve_review_only_semantics(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = write_safe_goal_artifacts(Path(tmpdir) / "output", metadata, result, map_id="test_map", input_candidate_count=1)
            document = json.loads(paths["safe_goal_candidates"].read_text(encoding="utf-8"))
            self.assertTrue(document["review_only"])
            self.assertFalse(document["executable"])
            self.assertIsNone(document["goals"][0]["canonical_label"])
            self.assertEqual(document["goals"][0]["suggested_label"], "unassigned")
            self.assertEqual(document["goals"][0]["goal_order"], 1)
            self.assertTrue(document["goals"][0]["raster_safety_evidence"]["passed"])
            self.assertTrue(paths["safe_goal_preview"].is_file())

    def test_raster_counts_are_real_for_occupied_and_unknown_attacks(self):
        for pixel, field in (((9, 9), "occupied_count"), ((9, 9), "unknown_count")):
            pixels = self.map_pixels()
            pixels[pixel] = 0 if field == "occupied_count" else 205
            result = select_offline_safe_goals(self.metadata(pixels), [self.candidate(self.metadata(pixels))], minimum_clearance_m=0)
            evidence = result.rejected_safe_goals[0]["relevant_safety_statistics"]
            self.assertGreater(evidence[field], 0)
            self.assertEqual(evidence["unknown_count" if field == "occupied_count" else "occupied_count"], 0)
            self.assertTrue(evidence["raster_evaluation_completed"])
            self.assertFalse(result.rejected_safe_goals[0]["faster_safety_passed"])

    def test_source_order_rank_and_signed_zero_polygon_duplicate_are_preserved(self):
        metadata = self.metadata(self.map_pixels())
        later = self.candidate(metadata, proposal_id="z", partition_id="z")
        later["selection_rank"] = 71
        first = self.candidate(metadata, proposal_id="a", partition_id="a")
        first["selection_rank"] = 9
        result = select_offline_safe_goals(metadata, [later, first], minimum_clearance_m=0)
        self.assertEqual([goal["goal_order"] for goal in result.goals], [1])
        self.assertEqual(result.goals[0]["source_candidate_order"], 2)
        self.assertEqual(result.goals[0]["source_selection_rank"], 9)
        duplicate = self.candidate(metadata, proposal_id="b", partition_id="b")
        duplicate["geometry"]["vertices"] = [[-0.0 if value == 0.0 else value for value in point] for point in first["geometry"]["vertices"]]
        result = select_offline_safe_goals(metadata, [first, duplicate], minimum_clearance_m=0)
        self.assertEqual(result.rejected_safe_goals[0]["rejection_stage"], "duplicate_polygon")

    def test_output_directory_must_be_new_and_old_bytes_are_unchanged(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            output = parent / "output"
            output.mkdir()
            (output / "old.bin").write_bytes(b"unchanged")
            before = self.tree_snapshot(output)
            with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "already exists"):
                write_safe_goal_artifacts(output, metadata, result, map_id="test", input_candidate_count=1)
            self.assertEqual(self.tree_snapshot(output), before)
            self.assert_no_transaction_residue(parent, output)

    def test_json_and_preview_failures_leave_no_output_or_tmp(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        for target in ("safe_goal_candidates.json", "rejected_safe_goals.json"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmpdir:
                parent = Path(tmpdir)
                output = parent / "output"
                original_write_text = Path.write_text
                def fail_json(path, *args, **kwargs):
                    if path.name == target:
                        raise OSError("json write failed")
                    return original_write_text(path, *args, **kwargs)
                with mock.patch.object(Path, "write_text", new=fail_json), self.assertRaisesRegex(OSError, "json write failed"):
                    write_safe_goal_artifacts(output, metadata, result, map_id="test", input_candidate_count=1)
                self.assertFalse(output.exists())
                self.assert_no_transaction_residue(parent, output)
                self.assertEqual(list(parent.glob(f".{output.name}.tmp-*")), [])
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            output = parent / "output"
            with mock.patch.object(safe_goal_module, "write_safe_goal_preview", side_effect=RuntimeError("preview failed")), self.assertRaisesRegex(RuntimeError, "preview failed"):
                write_safe_goal_artifacts(output, metadata, result, map_id="test", input_candidate_count=1)
            self.assertFalse(output.exists())
            self.assert_no_transaction_residue(parent, output)
            self.assertEqual(list(parent.glob(f".{output.name}.tmp-*")), [])

    def test_rename_failure_leaves_no_output_or_tmp(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            output = parent / "output"
            with mock.patch.object(safe_goal_module.os, "replace", side_effect=OSError("rename failed")), self.assertRaisesRegex(OSError, "rename failed"):
                write_safe_goal_artifacts(output, metadata, result, map_id="test", input_candidate_count=1)
            self.assertFalse(output.exists())
            self.assertEqual(list(parent.glob(f".{output.name}.tmp-*")), [])
            self.assert_no_transaction_residue(parent, output)

    def test_success_writes_exactly_three_artifacts(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            parent = Path(tmpdir)
            output = parent / "output"
            paths = write_safe_goal_artifacts(output, metadata, result, map_id="test", input_candidate_count=1)
            self.assertEqual(set(path.name for path in paths.values()), {"safe_goal_candidates.json", "rejected_safe_goals.json", "safe_goal_preview.png"})
            self.assertEqual({path.name for path in output.iterdir()}, {"safe_goal_candidates.json", "rejected_safe_goals.json", "safe_goal_preview.png"})
            self.assert_no_transaction_residue(parent, output)
            self.assertEqual(list(parent.glob(f".{output.name}.tmp-*")), [])

    def test_final_safety_assertion_fault_rejects_with_explicit_false_safety(self):
        metadata = self.metadata(self.map_pixels())
        with mock.patch.object(safe_goal_module, "_select_pixel", return_value=(0, 0, 0.0)):
            result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        self.assertEqual(result.goals, ())
        rejection = result.rejected_safe_goals[0]
        self.assertEqual(rejection["rejection_stage"], "final_safety_assertion")
        self.assertFalse(rejection["faster_safety_passed"])

    def test_geometry_boundary_cases_keep_goals_strictly_inside_free_pixels(self):
        metadata = self.metadata(self.map_pixels())
        tiny = select_offline_safe_goals(metadata, [self.candidate(metadata, pixels=((9, 9), (9.2, 9), (9.2, 9.2), (9, 9.2)))], minimum_clearance_m=0)
        thin = select_offline_safe_goals(metadata, [self.candidate(metadata, pixels=((3, 8), (16, 8), (16, 9), (3, 9)))], minimum_clearance_m=0)
        self.assertEqual(tiny.rejected_safe_goals[0]["rejection_stage"], "raster_safety")
        self.assertEqual(thin.rejected_safe_goals[0]["rejection_stage"], "raster_safety")
        all_free_pixels = np.full((20, 20), 254, dtype=np.uint8)
        all_free_metadata = self.metadata(all_free_pixels)
        for proposal_id, pixels in (
            ("boundary", ((0, 0), (10, 0), (10, 10), (0, 10))),
            ("concave", ((3, 3), (16, 3), (16, 16), (10, 10), (3, 16))),
        ):
            result = select_offline_safe_goals(all_free_metadata, [self.candidate(all_free_metadata, proposal_id=proposal_id, partition_id=proposal_id, pixels=pixels)], minimum_clearance_m=0)
            self.assertEqual(len(result.goals), 1)
            goal = result.goals[0]["goal"]
            self.assertTrue(0 <= goal["pixel_row"] < all_free_metadata.image.height)
            self.assertTrue(0 <= goal["pixel_column"] < all_free_metadata.image.width)
            self.assertEqual(all_free_pixels[goal["pixel_row"], goal["pixel_column"]], 254)

    def test_small_preview_has_reviewable_minimum_width(self):
        metadata = self.metadata(self.map_pixels())
        result = select_offline_safe_goals(metadata, [self.candidate(metadata)], minimum_clearance_m=0)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_safe_goal_preview(Path(tmpdir) / "preview.png", metadata, result)
            from PIL import Image
            with Image.open(path) as image:
                self.assertGreaterEqual(image.width, 640)
                self.assertGreaterEqual(image.height, metadata.image.height + 88)

    def test_independent_processes_produce_identical_accepted_and_rejected_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "map.pgm").write_bytes(b"P2\n20 20\n255\n" + b" ".join(b"254" if 2 <= row < 18 and 2 <= column < 18 else b"0" for row in range(20) for column in range(20)) + b"\n")
            (root / "map.yaml").write_text("image: map.pgm\nresolution: 0.1\norigin: [1, -2, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
            metadata = load_ros_map(root / "map.yaml")
            candidate = self.candidate(metadata)
            report = {"map_id": "test", "map_identity": map_identity(metadata).as_dict(), "proposals": [candidate]}
            (root / "proposals.json").write_text(json.dumps(report), encoding="utf-8")
            child = (
                "from pathlib import Path; import sys; "
                "from house_sitter_core.map_metadata import load_ros_map; "
                "from house_sitter_core.offline_safe_goal_selection import load_candidate_report, validate_report_map_identity, select_offline_safe_goals, write_safe_goal_artifacts; "
                "m=load_ros_map(Path(sys.argv[1])); d,c=load_candidate_report(Path(sys.argv[2]), 'selected'); "
                "validate_report_map_identity(d,m); r=select_offline_safe_goals(m,c,minimum_clearance_m=0); "
                "write_safe_goal_artifacts(Path(sys.argv[3]),m,r,map_id=d['map_id'],input_candidate_count=len(c))"
            )
            outputs = []
            for seed in ("11", "777"):
                output = root / f"output_{seed}"
                environment = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"}
                subprocess.run([sys.executable, "-c", child, str(root / "map.yaml"), str(root / "proposals.json"), str(output)], cwd=PROJECT_ROOT, env=environment, check=True)
                outputs.append(output)
            self.assertEqual((outputs[0] / "safe_goal_candidates.json").read_bytes(), (outputs[1] / "safe_goal_candidates.json").read_bytes())
            self.assertEqual((outputs[0] / "rejected_safe_goals.json").read_bytes(), (outputs[1] / "rejected_safe_goals.json").read_bytes())

    def test_cli_invalid_parameter_is_concise_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "map.pgm").write_bytes(b"P2\n4 4\n255\n0 0 0 0 0 254 254 0 0 254 254 0 0 0 0 0\n")
            (root / "map.yaml").write_text(
                "image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n",
                encoding="utf-8",
            )
            identity = map_identity(load_ros_map(root / "map.yaml")).as_dict()
            (root / "proposals.json").write_text(json.dumps({"map_id": "test", "map_identity": identity, "proposals": []}), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "select_safe_goals.py"),
                    "--map", str(root / "map.yaml"),
                    "--candidates", str(root / "proposals.json"), "--output-dir", str(PROJECT_ROOT / "local_annotations" / "pytest-invalid-output"),
                    "--minimum-clearance-m", "-1",
                ],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("minimum_clearance_m", result.stderr)
            self.assertNotIn("Traceback", result.stderr)

    def test_cli_help_explains_local_review_output_contract(self):
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "select_safe_goals.py"), "--help"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("writes local artifacts", result.stdout)
        self.assertIn("not already exist", result.stdout)
        self.assertIn("never modifies maps", result.stdout)


if __name__ == "__main__":
    unittest.main()
