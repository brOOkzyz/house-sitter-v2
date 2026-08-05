#!/usr/bin/env python3
"""Deterministically generate the local house_v1 occupancy map and artifacts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAP_DIR = ROOT / "maps"
ANNOTATION_DIR = ROOT / "local_annotations" / "house_v1"
WIDTH, HEIGHT, RESOLUTION = 240, 200, 0.05

# Every rectangle below uses the same map-frame coordinates as worlds/house_v1.sdf.
BLOCKS = (
    (0.0, 0.0, 12.0, 0.2), (0.0, 9.8, 12.0, 10.0),
    (0.0, 0.0, 0.2, 10.0), (11.8, 0.0, 12.0, 10.0),
    (0.2, 3.9, 2.4, 4.1), (3.9, 3.9, 8.3, 4.1), (9.8, 3.9, 11.8, 4.1),
    (0.2, 5.9, 2.4, 6.1), (3.9, 5.9, 7.1, 6.1), (8.6, 5.9, 9.9, 6.1), (11.3, 5.9, 11.8, 6.1),
    (5.9, 0.2, 6.1, 3.9), (6.5, 6.1, 6.7, 9.8), (8.9, 6.1, 9.1, 9.8),
    (0.8, 0.7, 2.7, 1.5), (3.7, 1.5, 4.6, 2.4),
    (10.2, 0.5, 11.4, 1.2), (10.8, 1.2, 11.4, 2.5), (7.2, 1.4, 8.3, 2.5),
    (3.9, 8.0, 5.8, 9.2), (7.0, 8.2, 7.7, 8.9), (8.1, 6.7, 8.7, 7.3),
    (10.2, 8.3, 11.2, 8.8),
)
REGIONS = (
    ("living_room", ((0.35, 0.35), (5.75, 0.35), (5.75, 3.75), (0.35, 3.75)), (3.5, 3.1), 0.65),
    ("kitchen", ((6.25, 0.35), (11.65, 0.35), (11.65, 3.75), (6.25, 3.75)), (8.25, 3.15), 0.6),
    ("bedroom", ((0.35, 6.25), (6.35, 6.25), (6.35, 9.65), (0.35, 9.65)), (2.5, 7.25), 0.8),
    ("bathroom", ((6.85, 6.25), (8.75, 6.25), (8.75, 9.65), (6.85, 9.65)), (7.65, 7.55), 0.55),
    ("hallway", ((0.35, 4.25), (11.65, 4.25), (11.65, 5.75), (0.35, 5.75)), (5.0, 5.0), 0.75),
    ("charging_area", ((4.85, 2.55), (5.75, 2.55), (5.75, 3.65), (4.85, 3.65)), (5.35, 3.1), 0.6),
)


def _pgm() -> bytes:
    pixels = bytearray([254]) * (WIDTH * HEIGHT)
    for x0, y0, x1, y1 in BLOCKS:
        for row in range(HEIGHT):
            y = (HEIGHT - row - 0.5) * RESOLUTION
            if not y0 <= y < y1:
                continue
            for column in range(WIDTH):
                x = (column + 0.5) * RESOLUTION
                if x0 <= x < x1:
                    pixels[row * WIDTH + column] = 0
    return f"P5\n# house_v1 deterministic occupancy map\n{WIDTH} {HEIGHT}\n255\n".encode("ascii") + bytes(pixels)


def _identity(image: bytes) -> dict[str, object]:
    image_sha256 = hashlib.sha256(image).hexdigest()
    base = {
        "schema_version": "1.0", "width": WIDTH, "height": HEIGHT, "resolution": RESOLUTION,
        "origin": [0.0, 0.0, 0.0], "negate": 0, "occupied_thresh": 0.65, "free_thresh": 0.196,
        "image_sha256": image_sha256,
    }
    encoded = json.dumps(base, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {**base, "fingerprint": hashlib.sha256(encoded + b"\0" + image).hexdigest()}


def _flags(*, simulation: bool = False) -> dict[str, object]:
    values: dict[str, object] = {"demo_only": True, "synthetic_semantics": True, "ground_truth": False, "review_only": True, "executable": False, "real_robot_supported": False}
    if simulation:
        values["simulation_only"] = True
    return values


def _documents(identity: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    region_records = []
    goal_records = []
    for index, (label, vertices, point, clearance) in enumerate(REGIONS, 1):
        proposal, partition = f"house_v1_{label}_safe_goal", f"house_v1_{label}_region"
        common = {"canonical_label": label, "polygon": {"type": "polygon", "vertices": [list(point) for point in vertices]}, "proposal_id": proposal, "partition_id": partition, "map_id": "house_v1", "map_identity": identity, "source_candidate_order": index, "source_selection_rank": index, "demo_assignment_order": index, "provenance": ["house_v1_manual_geometry", "house_v1_deterministic_occupancy"]}
        region_records.append({**common, **_flags()})
        column = int(point[0] / RESOLUTION - 0.5)
        row = HEIGHT - 1 - int(point[1] / RESOLUTION - 0.5)
        goal_records.append({
            **common, "candidate_partition_id": partition, "goal_order": index, "suggested_label": label,
            "status": "accepted", "confirmed": True, "polygon_validation_passed": True, "faster_safety_passed": True,
            "raster_safety_evidence": {"passed": True, "polygon_validation_passed": True, "bounds_validation_passed": True, "raster_evaluation_completed": True, "rasterized_pixel_count": 1, "free_count": 1, "occupied_count": 0, "unknown_count": 0, "out_of_bounds_count": 0, "safe_free_ratio": 1.0, "failure_reasons": []},
            "goal": {"pixel_row": row, "pixel_column": column, "map_x": point[0], "map_y": point[1], "yaw": 0.0, "clearance_pixels": clearance / RESOLUTION, "clearance_m": clearance},
            "selection_method": "house_v1 reviewed room-centre goal with deterministic raster verification",
            "evidence": ["house_v1 room polygon", "house_v1 deterministic occupancy map", "free pixel", "manual clearance review"],
            "source": {"candidate_source": "house_v1_manual_geometry", "source_polygon_validation_claim": True, "source_raster_safety_claim": True, "source_faster_safety_claim": True},
            **_flags(simulation=True),
        })
    regions = {"schema_version": "1.0", "map_id": "house_v1", "map_identity": identity, "provenance": ["house_v1_manual_geometry"], "regions": region_records, **_flags()}
    goals = {"schema_version": "1.0", "map_id": "house_v1", "map_identity": identity, "map_metadata": {"yaml_path": "maps/house_v1.yaml", "image_path": "maps/house_v1.pgm", "width": WIDTH, "height": HEIGHT, "resolution": RESOLUTION, "origin": [0.0, 0.0, 0.0], "map_fingerprint": identity["fingerprint"]}, "candidate_source": "house_v1_manual_geometry", "minimum_clearance_m": 0.45, "input_candidate_count": len(goal_records), "accepted_goal_count": len(goal_records), "rejected_goal_count": 0, "goals": goal_records, "provenance": ["house_v1_manual_geometry"], **_flags(simulation=True)}
    return regions, goals


def main() -> None:
    MAP_DIR.mkdir(parents=True, exist_ok=True); ANNOTATION_DIR.mkdir(parents=True, exist_ok=True)
    image = _pgm(); identity = _identity(image); regions, goals = _documents(identity)
    (MAP_DIR / "house_v1.pgm").write_bytes(image)
    (MAP_DIR / "house_v1.yaml").write_text("image: house_v1.pgm\nmode: trinary\nresolution: 0.05\norigin: [0.0, 0.0, 0.0]\nnegate: 0\noccupied_thresh: 0.65\nfree_thresh: 0.196\n", encoding="utf-8")
    (ANNOTATION_DIR / "semantic_regions.json").write_text(json.dumps(regions, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (ANNOTATION_DIR / "safe_goals.json").write_text(json.dumps(goals, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
