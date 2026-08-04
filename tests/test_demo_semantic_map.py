"""Tests for the local synthetic demo-label to safe-goal bridge."""

import importlib.util
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
from house_sitter_core.offline_safe_goal_selection import OfflineSafeGoalSelectionError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "create_demo_semantic_map.py"
LOCAL_ROOT = PROJECT_ROOT / "local_annotations"
SPEC = importlib.util.spec_from_file_location("demo_semantic_map", SCRIPT_PATH)
demo = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(demo)


class DemoSemanticMapTests(unittest.TestCase):
    def metadata(self):
        pixels = np.zeros((60, 60), dtype=np.uint8)
        for row, column in ((5, 5), (5, 32), (32, 5), (32, 32)):
            pixels[row : row + 20, column : column + 20] = 254
        return RosMapMetadata(Path("synthetic.yaml"), Path("synthetic.pgm"), PgmImage(60, 60, pixels.tobytes(), "P2", 255), .1, (0., 0., 0.), 0, .65, .196)

    def record(self, metadata, number, row, column):
        points = ((column, row), (column + 19, row), (column + 19, row + 19), (column, row + 19))
        return {"proposal_id": f"candidate_{number}", "partition_id": f"partition_{number}", "centroid_pixel": [column + 9.5, row + 9.5], "map_area_m2": 4.0, "geometry": {"type": "polygon", "vertices": [list(pixel_to_map(metadata, x, y)) for x, y in points]}, "polygon_validation": False, "raster_safety_passed": False}

    def document(self, metadata, records=None):
        records = records or [self.record(metadata, n, row, col) for n, (row, col) in enumerate(((5, 5), (5, 32), (32, 5), (32, 32)), 1)]
        return {"map_id": "synthetic_demo_map", "map_identity": map_identity(metadata).as_dict(), "selection_strategy": "spatial-balanced", "safe_candidates": records}

    def test_four_fixed_labels_and_safe_goals_are_generated(self):
        metadata = self.metadata()
        regions, goals, rejected = demo.create_demo(metadata, self.document(metadata))
        self.assertEqual([item["canonical_label"] for item in regions["regions"]], list(demo.LABELS))
        self.assertEqual([item["canonical_label"] for item in goals["goals"]], list(demo.LABELS))
        self.assertEqual([item["goal_order"] for item in goals["goals"]], [1, 2, 3, 4])
        self.assertEqual(rejected["rejected_safe_goals"], [])
        for goal in goals["goals"]:
            self.assertTrue(goal["faster_safety_passed"])
            self.assertEqual(goal["raster_safety_evidence"]["occupied_count"], 0)
            self.assertEqual(goal["raster_safety_evidence"]["unknown_count"], 0)

    def test_demo_fields_and_provenance_are_explicit(self):
        metadata = self.metadata()
        regions, goals, _ = demo.create_demo(metadata, self.document(metadata))
        for record in [*regions["regions"], *goals["goals"]]:
            self.assertTrue(record["demo_only"])
            self.assertTrue(record["synthetic_semantics"])
            self.assertFalse(record["ground_truth"])
            self.assertTrue(record["review_only"])
            self.assertFalse(record["executable"])
        self.assertIn("automatic_synthetic_demo_assignment", regions["provenance"])

    def test_fewer_than_four_safe_regions_fails_closed(self):
        metadata = self.metadata()
        with self.assertRaisesRegex(demo.DemoSemanticMapError, "Fewer than four"):
            demo.create_demo(metadata, self.document(metadata, self.document(metadata)["safe_candidates"][:3]))

    def test_duplicate_regions_do_not_create_duplicate_labels(self):
        metadata = self.metadata(); records = self.document(metadata)["safe_candidates"]
        duplicate = dict(records[0]); duplicate.update({"proposal_id": "candidate_copy", "partition_id": "partition_copy"})
        with self.assertRaisesRegex(demo.DemoSemanticMapError, "Fewer than four"):
            demo.create_demo(metadata, self.document(metadata, [*records[:3], duplicate]))

    def test_map_identity_mismatch_fails_closed(self):
        metadata = self.metadata(); document = self.document(metadata)
        document["map_identity"]["width"] += 1
        with self.assertRaisesRegex(OfflineSafeGoalSelectionError, "map_identity mismatch"):
            demo.create_demo(metadata, document)

    def test_provenance_preserves_original_position_rank_and_demo_order(self):
        metadata = self.metadata()
        records = self.document(metadata)["safe_candidates"]
        records = [
            {**records[0], "selection_rank": None},
            {**records[1], "geometry": {"type": "polygon", "vertices": [[99, 99], [100, 99], [100, 100]]}, "selection_rank": 99},
            {**records[2], "selection_rank": 23},
            {**records[3], "geometry": {"type": "polygon", "vertices": [[99, 99], [100, 99], [100, 100]]}, "selection_rank": 88},
            {**records[1], "proposal_id": "candidate_5", "partition_id": "partition_5", "selection_rank": 17},
            {**records[2], "proposal_id": "candidate_6", "partition_id": "partition_6", "selection_rank": None},
            {**records[3], "proposal_id": "candidate_7", "partition_id": "partition_7", "selection_rank": 31},
        ]
        regions, goals, _ = demo.create_demo(metadata, self.document(metadata, records))
        goal_by_key = {(goal["proposal_id"], goal["candidate_partition_id"]): goal for goal in goals["goals"]}
        for region in regions["regions"]:
            goal = goal_by_key[(region["proposal_id"], region["partition_id"])]
            self.assertEqual(region["source_candidate_order"], goal["source_candidate_order"])
            self.assertEqual(region["source_selection_rank"], goal["source_selection_rank"])
            self.assertEqual(region["demo_assignment_order"], goal["demo_assignment_order"])
        self.assertEqual(sorted(item["demo_assignment_order"] for item in regions["regions"]), [1, 2, 3, 4])
        self.assertEqual(sorted(goal["goal_order"] for goal in goals["goals"]), [1, 2, 3, 4])

    def test_preview_renderer_adds_warnings_and_all_labels(self):
        metadata = self.metadata()
        regions, goals, _ = demo.create_demo(metadata, self.document(metadata))
        captured = []
        from PIL import ImageDraw
        real_draw = ImageDraw.Draw

        class CapturingDraw:
            def __init__(self, drawing):
                self.drawing = drawing

            def text(self, xy, value, *args, **kwargs):
                captured.append(str(value))
                return self.drawing.text(xy, value, *args, **kwargs)

            def __getattr__(self, name):
                return getattr(self.drawing, name)

        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            ImageDraw, "Draw", side_effect=lambda image: CapturingDraw(real_draw(image))
        ):
            demo._draw_preview(Path(directory) / "preview.png", metadata, regions["regions"], goals["goals"])
            self.assertTrue(Path(directory, "preview.png").exists())
        for warning in ("SYNTHETIC DEMO LABELS", "NOT GROUND TRUTH", "SIMULATION / REVIEW ONLY"):
            self.assertIn(warning, captured)
        for label in demo.LABELS:
            self.assertIn(label, captured)

    def _cli_inputs(self, directory):
        pgm = directory / "map.pgm"; yaml = directory / "map.yaml"; candidates = directory / "candidates.json"
        pixels = np.frombuffer(self.metadata().image.pixels, dtype=np.uint8).reshape(60, 60)
        pgm.write_text("P2\n60 60\n255\n" + " ".join(str(int(value)) for value in pixels.flat), encoding="utf-8")
        yaml.write_text("image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
        candidates.write_text(json.dumps(self.document(load_ros_map(yaml))), encoding="utf-8")
        return yaml, candidates

    def test_intermediate_json_and_png_failures_leave_no_published_or_temp_output(self):
        original_write = Path.write_text
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory(dir=LOCAL_ROOT) as local:
            yaml, candidates = self._cli_inputs(Path(directory))
            for failure in ("first_json", "second_json", "preview"):
                output = Path(local) / failure
                if failure == "preview":
                    patcher = mock.patch.object(demo, "_draw_preview", side_effect=OSError("png failure"))
                else:
                    calls = []
                    def write_then_fail(path, data, *args, **kwargs):
                        calls.append(len(calls) + 1)
                        if (failure == "first_json" and len(calls) == 1) or (failure == "second_json" and len(calls) == 2):
                            raise OSError("json failure")
                        return original_write(path, data, *args, **kwargs)
                    patcher = mock.patch.object(Path, "write_text", autospec=True, side_effect=write_then_fail)
                with patcher:
                    self.assertEqual(demo.main(["--map", str(yaml), "--candidates", str(candidates), "--output-dir", str(output)]), 2)
                self.assertFalse(output.exists())
                self.assertEqual(list(Path(local).glob(f".{failure}.tmp-*")), [])

    def test_cross_process_json_is_byte_identical(self):
        metadata = self.metadata()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pgm = root / "map.pgm"; yaml = root / "map.yaml"; candidates = root / "candidates.json"
            pixels = np.frombuffer(metadata.image.pixels, dtype=np.uint8).reshape(60, 60)
            pgm.write_text("P2\n60 60\n255\n" + " ".join(str(int(value)) for value in pixels.flat), encoding="utf-8")
            yaml.write_text("image: map.pgm\nresolution: 0.1\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
            candidates.write_text(json.dumps(self.document(load_ros_map(yaml))), encoding="utf-8")
            with tempfile.TemporaryDirectory(dir=LOCAL_ROOT) as local:
                first, second = Path(local) / "one", Path(local) / "two"
                for seed, target in (("1", first), ("777", second)):
                    environment = {**os.environ, "PYTHONHASHSEED": seed}
                    run = subprocess.run([sys.executable, str(SCRIPT_PATH), "--map", str(yaml), "--candidates", str(candidates), "--output-dir", str(target)], cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, check=False)
                    self.assertEqual(run.returncode, 0, run.stderr)
                for name in ("demo_semantic_regions.json", "safe_goal_candidates.json", "rejected_safe_goals.json"):
                    self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())

    def test_existing_output_is_refused_and_no_partial_directory_is_published(self):
        with tempfile.TemporaryDirectory(dir=LOCAL_ROOT) as directory:
            output = Path(directory) / "existing"; output.mkdir()
            run = subprocess.run([sys.executable, str(SCRIPT_PATH), "--map", "maps/minimal_slam_map.yaml", "--candidates", "tests/fixtures/automatic_area_proposal_test.json", "--output-dir", str(output)], cwd=PROJECT_ROOT, capture_output=True, text=True, check=False)
            self.assertNotEqual(run.returncode, 0)
            self.assertIn("already exists", run.stderr)
            self.assertEqual(list(Path(directory).glob(".existing.tmp-*")), [])
