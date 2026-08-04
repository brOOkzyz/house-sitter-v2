"""Core tests for the offline map annotation prototype; no GUI or ROS runtime is used."""

import json
import tempfile
import unittest
from pathlib import Path

from house_sitter_core.map_coordinates import map_to_pixel, pixel_to_map
from house_sitter_core.map_metadata import MapMetadataError, PgmImage, RosMapMetadata, load_pgm, load_ros_map
from house_sitter_core.semantic_annotation import SemanticAnnotationError, SemanticAnnotationSession
from house_sitter_core.semantic_waypoints import SemanticWaypointRegistry
from house_sitter_core.schemas import make_plan
from house_sitter_core.sim_execution_request import build_sim_nav2_execution_requests
from house_sitter_core.verifier import PlanVerifier


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_REGISTRY = PROJECT_ROOT / "config" / "semantic_waypoints.json"


class MapAnnotationTests(unittest.TestCase):
    def make_map(self, directory: Path, *, pgm: bytes, yaml_text: str | None = None) -> Path:
        (directory / "fixture.pgm").write_bytes(pgm)
        yaml_path = directory / "fixture.yaml"
        yaml_path.write_text(
            yaml_text
            or "\n".join(
                [
                    "image: fixture.pgm",
                    "resolution: 0.5",
                    "origin: [10.0, 20.0, 0.0]",
                    "negate: 0",
                    "occupied_thresh: 0.65",
                    "free_thresh: 0.196",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return yaml_path

    def metadata(self) -> RosMapMetadata:
        return RosMapMetadata(
            yaml_path=Path("test.yaml"),
            image_path=Path("test.pgm"),
            image=PgmImage(4, 3, bytes([0] * 12), "P2", 255),
            resolution=0.5,
            origin=(10.0, 20.0, 0.0),
            negate=0,
            occupied_thresh=0.65,
            free_thresh=0.196,
        )

    def session(self) -> SemanticAnnotationSession:
        return SemanticAnnotationSession(self.metadata(), SemanticWaypointRegistry(PRODUCTION_REGISTRY))

    def add_triangle(self, session: SemanticAnnotationSession) -> None:
        session.select_label("hallway")
        session.set_map_id("fixture_map")
        session.add_pixel_vertex(0, 0)
        session.add_pixel_vertex(3, 0)
        session.add_pixel_vertex(1, 2)

    def test_yaml_relative_image_path_metadata_and_bounds(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yaml_path = self.make_map(Path(tmpdir), pgm=b"P2\n# test map\n2 2\n255\n0 255 205 0\n")
            metadata = load_ros_map(yaml_path)
        self.assertEqual(metadata.image_path.name, "fixture.pgm")
        self.assertEqual((metadata.image.width, metadata.image.height), (2, 2))
        self.assertEqual(metadata.resolution, 0.5)
        self.assertEqual(metadata.origin, (10.0, 20.0, 0.0))
        self.assertEqual(metadata.bounds, (10.0, 20.0, 11.0, 21.0))

    def test_p2_and_p5_pgm_with_comments_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            p2 = directory / "p2.pgm"
            p2.write_bytes(b"P2\n# comment\n2 2\n255\n0 # inner comment\n255 128 64\n")
            p5 = directory / "p5.pgm"
            p5.write_bytes(b"P5\n# comment\n2 2\n255\nABCD")
            self.assertEqual(load_pgm(p2).pixels, bytes([0, 255, 128, 64]))
            self.assertEqual(load_pgm(p5).pixels, b"ABCD")
            self.assertEqual(load_pgm(p5).format, "P5")

    def test_truncated_pgm_and_invalid_metadata_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            directory = Path(tmpdir)
            broken = directory / "broken.pgm"
            broken.write_bytes(b"P5\n2 2\n255\nAB")
            with self.assertRaisesRegex(MapMetadataError, "truncated"):
                load_pgm(broken)
            yaml_path = self.make_map(directory, pgm=b"P2\n1 1\n255\n0\n", yaml_text="image: fixture.pgm\nresolution: 0\norigin: [0, 0, 0]\nnegate: 0\noccupied_thresh: 0.5\nfree_thresh: 0.2\n")
            with self.assertRaisesRegex(MapMetadataError, "resolution"):
                load_ros_map(yaml_path)

    def test_pixel_map_conversion_flips_y_and_round_trips(self):
        metadata = self.metadata()
        self.assertEqual(pixel_to_map(metadata, 0, 0), (10.25, 21.25))
        self.assertEqual(pixel_to_map(metadata, 0, 2), (10.25, 20.25))
        self.assertEqual(map_to_pixel(metadata, 10.25, 21.25), (0.0, 0.0))
        for pixel in ((0, 0), (3, 0), (0, 2), (2, 1)):
            with self.subTest(pixel=pixel):
                self.assertEqual(map_to_pixel(metadata, *pixel_to_map(metadata, *pixel)), pixel)

    def test_coordinate_validation_rejects_nonfinite_and_out_of_bounds(self):
        metadata = self.metadata()
        for value in (float("nan"), float("inf"), -float("inf"), True):
            with self.subTest(value=value):
                with self.assertRaises(MapMetadataError):
                    pixel_to_map(metadata, value, 0)
        with self.assertRaisesRegex(MapMetadataError, "outside"):
            map_to_pixel(metadata, 1, 1)

    def test_annotation_requires_label_map_id_and_three_vertices(self):
        session = self.session()
        with self.assertRaisesRegex(SemanticAnnotationError, "canonical"):
            session.build_draft()
        with self.assertRaisesRegex(SemanticAnnotationError, "Unknown"):
            session.select_label("balcony")
        session.select_label("hallway")
        with self.assertRaisesRegex(SemanticAnnotationError, "three"):
            session.build_draft()
        for map_id in ("", " fixture", "fixture "):
            with self.subTest(map_id=map_id):
                with self.assertRaises(SemanticAnnotationError):
                    session.set_map_id(map_id)

    def test_default_frame_source_and_draft_reuse_registry_validator(self):
        session = self.session()
        self.add_triangle(session)
        draft = session.build_draft()
        target = draft["labels"]["hallway"]
        self.assertEqual(target["frame_id"], "map")
        self.assertEqual(target["source"], {"type": "user_annotation", "map_id": "fixture_map"})
        self.assertEqual(set(target["source"]), {"type", "map_id"})
        self.assertEqual(target["geometry"]["vertices"], [[10.25, 21.25], [11.75, 21.25], [10.75, 20.25]])
        self.assertIsNotNone(SemanticWaypointRegistry.from_config(draft).areas["hallway"].geometry)

    def test_annotation_polygon_validation_rejects_bow_tie_and_accepts_concave(self):
        session = self.session()
        session.select_label("hallway")
        session.set_map_id("fixture_map")
        for point in ((0, 0), (3, 2), (0, 2), (3, 0)):
            session.add_pixel_vertex(*point)
        with self.assertRaisesRegex(SemanticAnnotationError, "self-intersect"):
            session.build_draft()
        session.clear_vertices()
        for point in ((0, 0), (3, 0), (3, 2), (2, 1), (0, 2)):
            session.add_pixel_vertex(*point)
        self.assertIsNotNone(SemanticWaypointRegistry.from_config(session.build_draft()).areas["hallway"].geometry)

    def test_export_changes_only_target_and_never_production_config(self):
        before = PRODUCTION_REGISTRY.read_text(encoding="utf-8")
        session = self.session()
        self.add_triangle(session)
        untouched = json.loads(before)["labels"]["kitchen"]
        with tempfile.TemporaryDirectory() as tmpdir:
            output = session.export_draft(Path(tmpdir) / "draft.json")
            exported = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exported["labels"]["kitchen"], untouched)
        self.assertEqual(PRODUCTION_REGISTRY.read_text(encoding="utf-8"), before)
        with self.assertRaisesRegex(SemanticAnnotationError, "production"):
            session.export_draft(PRODUCTION_REGISTRY)

    def test_local_annotation_directory_is_ignored(self):
        gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("local_annotations/", gitignore)

    def test_request_builder_does_not_copy_annotation_geometry(self):
        session = self.session()
        self.add_triangle(session)
        with tempfile.TemporaryDirectory() as tmpdir:
            draft_path = Path(tmpdir) / "draft.json"
            session.export_draft(draft_path)
            verifier = PlanVerifier(
                PROJECT_ROOT / "config" / "allowed_actions.json",
                PROJECT_ROOT / "config" / "waypoints.json",
                draft_path,
            )
            result = build_sim_nav2_execution_requests(
                make_plan(
                    "hallway_only",
                    "test_provider",
                    [{"action": "navigate_to_waypoint", "parameters": {"waypoint": "hallway"}}],
                ),
                verifier=verifier,
            )
        request = result.execution_requests[0]
        self.assertEqual(request["parameters"], {"waypoint": "hallway"})
        self.assertNotIn("geometry", request)
        self.assertNotIn("vertices", request)


if __name__ == "__main__":
    unittest.main()
