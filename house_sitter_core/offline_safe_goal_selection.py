"""Deterministic, offline safe-goal selection for review-only free-space zones.

This module never constructs navigation requests.  It consumes already reviewed
proposal reports, verifies their geometry again locally, and emits only local
review artifacts.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import cv2
import numpy as np
from scipy import ndimage

from .automatic_area_proposal import classify_occupancy
from .map_coordinates import map_to_pixel, pixel_to_map
from .map_metadata import MapMetadataError, RosMapMetadata, map_identity
from .semantic_waypoints import PolygonGeometry, SemanticWaypointError, SemanticWaypointRegistry


class OfflineSafeGoalSelectionError(ValueError):
    """Raised when a local review proposal or safe-goal setting is invalid."""


@dataclass(frozen=True)
class OfflineSafeGoalSelectionResult:
    """Review-only selection result; accepted and rejected records stay separate."""

    goals: tuple[dict[str, Any], ...]
    rejected_safe_goals: tuple[dict[str, Any], ...]
    minimum_clearance_m: float
    candidate_source: str


@dataclass(frozen=True)
class RasterSafetyEvidence:
    """Current-map evidence, never a boolean assertion trusted from input JSON."""

    passed: bool
    polygon_validation_passed: bool
    bounds_validation_passed: bool
    raster_evaluation_completed: bool
    rasterized_pixel_count: int | None
    free_count: int | None
    occupied_count: int | None
    unknown_count: int | None
    out_of_bounds_count: int | None
    safe_free_ratio: float | None
    failure_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "polygon_validation_passed": self.polygon_validation_passed,
            "bounds_validation_passed": self.bounds_validation_passed,
            "raster_evaluation_completed": self.raster_evaluation_completed,
            "rasterized_pixel_count": self.rasterized_pixel_count,
            "free_count": self.free_count,
            "occupied_count": self.occupied_count,
            "unknown_count": self.unknown_count,
            "out_of_bounds_count": self.out_of_bounds_count,
            "safe_free_ratio": self.safe_free_ratio,
            "failure_reasons": list(self.failure_reasons),
        }


def _non_negative_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OfflineSafeGoalSelectionError(f"{name} must be a non-negative finite number.")
    numeric = float(value)
    if not math.isfinite(numeric) or numeric < 0:
        raise OfflineSafeGoalSelectionError(f"{name} must be a non-negative finite number.")
    return numeric


def _required_identifier(candidate: dict[str, Any], field: str) -> str:
    value = candidate.get(field)
    if not isinstance(value, str) or not value or value != value.strip():
        raise OfflineSafeGoalSelectionError(f"candidate {field} must be a non-empty trimmed string.")
    return value


def _geometry_vertices(candidate: dict[str, Any], proposal_id: str) -> list[list[float]]:
    geometry = candidate.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("type") != "polygon":
        raise OfflineSafeGoalSelectionError(f"{proposal_id}: geometry must be a polygon object.")
    vertices = geometry.get("vertices")
    if not isinstance(vertices, list):
        raise OfflineSafeGoalSelectionError(f"{proposal_id}: geometry vertices must be a list.")
    return vertices


def _validated_polygon(candidate: dict[str, Any], proposal_id: str) -> PolygonGeometry:
    """Stage 1: run the registry geometry validator, without map interpretation."""
    vertices = _geometry_vertices(candidate, proposal_id)
    try:
        return SemanticWaypointRegistry.validate_polygon_geometry(
            proposal_id, "map", {"type": "polygon", "vertices": vertices}
        )
    except (SemanticWaypointError, IndexError, TypeError) as exc:
        raise OfflineSafeGoalSelectionError(f"{proposal_id}: polygon geometry validation failed: {exc}") from exc


def _polygon_pixels_in_bounds(metadata: RosMapMetadata, geometry: PolygonGeometry) -> np.ndarray:
    """Stage 2: require all validated vertices to map into the current raster."""
    try:
        pixels = [map_to_pixel(metadata, vertex[0], vertex[1]) for vertex in geometry.vertices]
    except (MapMetadataError, IndexError, TypeError) as exc:
        raise OfflineSafeGoalSelectionError(f"polygon is outside current map bounds: {exc}") from exc
    return np.asarray(pixels, dtype=np.float64)


def _polygon_mask(shape: tuple[int, int], pixel_vertices: np.ndarray) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    points = np.rint(pixel_vertices).astype(np.int32)
    cv2.fillPoly(mask, [points], 1)
    # The contour itself can touch a free/unknown or free/occupied boundary.
    # Select only its strict interior, matching the existing raster-safety rule.
    return ndimage.binary_erosion(
        mask.astype(bool), structure=np.ones((3, 3), dtype=bool), border_value=0
    )


def _not_rasterized_evidence(*, polygon_validation_passed: bool, bounds_validation_passed: bool, reason: str) -> RasterSafetyEvidence:
    return RasterSafetyEvidence(False, polygon_validation_passed, bounds_validation_passed, False,
                                None, None, None, None, None, None, (reason,))


def _raster_safety_evidence(classification, interior: np.ndarray) -> RasterSafetyEvidence:
    """Recheck every strict polygon-interior pixel against the current map."""
    interior_count = int(interior.sum())
    free_count = int((interior & classification.free).sum())
    occupied_count = int((interior & classification.occupied).sum())
    unknown_count = int((interior & classification.unknown).sum())
    safe_ratio = free_count / interior_count if interior_count else 0.0
    reasons: list[str] = []
    if interior_count == 0:
        reasons.append("polygon has no strict rasterized interior")
    if occupied_count:
        reasons.append("polygon interior contains occupied pixels")
    if unknown_count:
        reasons.append("polygon interior contains unknown pixels")
    if interior_count and safe_ratio != 1.0:
        reasons.append("polygon interior is not entirely observed free")
    return RasterSafetyEvidence(
        passed=not reasons,
        polygon_validation_passed=True,
        bounds_validation_passed=True,
        raster_evaluation_completed=True,
        rasterized_pixel_count=interior_count,
        free_count=free_count,
        occupied_count=occupied_count,
        unknown_count=unknown_count,
        out_of_bounds_count=0,
        safe_free_ratio=safe_ratio,
        failure_reasons=tuple(reasons),
    )


def _clearance_pixels(free: np.ndarray) -> np.ndarray:
    """Treat map borders, occupied cells, and unknown cells as unsafe clearance sources."""
    safe = free.copy()
    if safe.size:
        safe[0, :] = False
        safe[-1, :] = False
        safe[:, 0] = False
        safe[:, -1] = False
    return ndimage.distance_transform_edt(safe)


def _mask_centroid(mask: np.ndarray) -> tuple[float, float]:
    rows, columns = np.nonzero(mask)
    if not len(rows):
        raise OfflineSafeGoalSelectionError("polygon has no strict interior pixels.")
    return float(rows.mean()), float(columns.mean())


def _select_pixel(
    eligible: np.ndarray, polygon_interior: np.ndarray, clearance: np.ndarray
) -> tuple[int, int, float]:
    centroid_row, centroid_column = _mask_centroid(polygon_interior)
    rows, columns = np.nonzero(eligible)
    # All tie-break values come from ordered scalar keys, never a set or RNG.
    row, column = min(
        ((int(row), int(column)) for row, column in zip(rows, columns)),
        key=lambda point: (
            -float(clearance[point[0], point[1]]),
            (point[0] - centroid_row) ** 2 + (point[1] - centroid_column) ** 2,
            point[0],
            point[1],
        ),
    )
    return row, column, float(clearance[row, column])


def _rejection(
    proposal_id: str,
    stage: str,
    reason: str,
    *,
    candidate: dict[str, Any],
    statistics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "proposal_id": proposal_id,
        "candidate_partition_id": candidate.get("partition_id"),
        "source_candidate_order": candidate.get("_source_candidate_order"),
        "source_selection_rank": candidate.get("_source_selection_rank"),
        # This is a local result, never a source JSON assertion. A rejected
        # candidate has not produced a safe goal in this run.
        "faster_safety_passed": False,
        "polygon": candidate.get("geometry"),
        "rejection_stage": stage,
        "rejection_reason": reason,
        "relevant_safety_statistics": statistics or {},
    }


def _polygon_signature(geometry: PolygonGeometry) -> tuple[tuple[str, str], ...]:
    """Canonical rotation/orientation-independent signature for exact duplicate detection."""
    def canonical_float(value: float) -> str:
        # Preserve exact float identity except that IEEE signed zero is one point.
        return (0.0 if value == 0.0 else value).hex()
    vertices = tuple((canonical_float(x), canonical_float(y)) for x, y in geometry.vertices)
    rotations = [vertices[index:] + vertices[:index] for index in range(len(vertices))]
    reversed_vertices = tuple(reversed(vertices))
    rotations.extend(
        reversed_vertices[index:] + reversed_vertices[:index]
        for index in range(len(reversed_vertices))
    )
    return min(rotations)


def _normalized_candidates(candidates: Iterable[dict[str, Any]]) -> list[tuple[dict[str, Any], str, str]]:
    records = list(candidates)
    normalized: list[tuple[dict[str, Any], str, str]] = []
    proposal_ids: set[str] = set()
    partition_ids: set[str] = set()
    for source_order, candidate in enumerate(records, start=1):
        if not isinstance(candidate, dict):
            raise OfflineSafeGoalSelectionError("candidate batch entries must be objects.")
        proposal_id = _required_identifier(candidate, "proposal_id")
        partition_id = _required_identifier(candidate, "partition_id")
        if proposal_id in proposal_ids:
            raise OfflineSafeGoalSelectionError(f"duplicate proposal_id: {proposal_id}")
        if partition_id in partition_ids:
            raise OfflineSafeGoalSelectionError(f"duplicate partition_id: {partition_id}")
        proposal_ids.add(proposal_id)
        partition_ids.add(partition_id)
        # Copy rather than mutate caller JSON, and retain provenance before sorting.
        preserved = dict(candidate)
        preserved["_source_candidate_order"] = source_order
        preserved["_source_selection_rank"] = candidate.get("selection_rank")
        normalized.append((preserved, proposal_id, partition_id))
    return sorted(normalized, key=lambda item: (item[1], item[2]))


def select_offline_safe_goals(
    metadata: RosMapMetadata,
    candidates: Iterable[dict[str, Any]],
    *,
    minimum_clearance_m: float,
    candidate_source: str = "selected",
) -> OfflineSafeGoalSelectionResult:
    """Select one deterministic maximum-clearance point for each safe review polygon."""
    minimum_clearance_m = _non_negative_finite(minimum_clearance_m, "minimum_clearance_m")
    if candidate_source not in {"selected", "all-safe"}:
        raise OfflineSafeGoalSelectionError("candidate_source must be selected or all-safe.")
    classification = classify_occupancy(metadata)
    clearance = _clearance_pixels(classification.free)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_polygons: set[tuple[tuple[str, str], ...]] = set()
    seen_goal_pixels: set[tuple[int, int]] = set()
    for candidate, proposal_id, partition_id in _normalized_candidates(candidates):
        # Source booleans are retained as audit provenance only.  They cannot
        # authorize (or suppress) the local three-stage safety evaluation.
        try:
            geometry = _validated_polygon(candidate, proposal_id)
        except OfflineSafeGoalSelectionError as exc:
            evidence = _not_rasterized_evidence(
                polygon_validation_passed=False, bounds_validation_passed=False, reason=str(exc)
            )
            rejected.append(
                _rejection(proposal_id, "polygon_validation", str(exc), candidate=candidate, statistics=evidence.as_dict())
            )
            continue
        signature = _polygon_signature(geometry)
        if signature in seen_polygons:
            rejected.append(
                _rejection(proposal_id, "duplicate_polygon", "duplicate_polygon", candidate=candidate)
            )
            continue
        seen_polygons.add(signature)
        try:
            pixel_vertices = _polygon_pixels_in_bounds(metadata, geometry)
        except OfflineSafeGoalSelectionError as exc:
            evidence = _not_rasterized_evidence(
                polygon_validation_passed=True, bounds_validation_passed=False, reason=str(exc)
            )
            rejected.append(
                _rejection(proposal_id, "map_bounds", str(exc), candidate=candidate, statistics=evidence.as_dict())
            )
            continue
        interior = _polygon_mask(classification.free.shape, pixel_vertices)
        raster_evidence = _raster_safety_evidence(classification, interior)
        if not raster_evidence.passed:
            rejected.append(
                _rejection(
                    proposal_id,
                    "raster_safety",
                    "; ".join(raster_evidence.failure_reasons),
                    candidate=candidate,
                    statistics=raster_evidence.as_dict(),
                )
            )
            continue
        eligible = interior & classification.free & (clearance * metadata.resolution >= minimum_clearance_m)
        if not np.any(eligible):
            rejected.append(
                _rejection(
                    proposal_id,
                    "clearance_selection",
                    "no observed-free polygon interior pixel meets minimum clearance",
                    candidate=candidate,
                    statistics={**raster_evidence.as_dict(), "minimum_clearance_m": minimum_clearance_m},
                )
            )
            continue
        row, column, clearance_px = _select_pixel(eligible, interior, clearance)
        map_x, map_y = pixel_to_map(metadata, column, row)
        final_reasons: list[str] = []
        if not interior[row, column]:
            final_reasons.append("selected pixel is outside the polygon interior")
        if not (0 <= row < metadata.image.height and 0 <= column < metadata.image.width):
            final_reasons.append("selected pixel is outside map bounds")
        if not classification.free[row, column]:
            final_reasons.append("selected pixel is not observed free")
        if classification.occupied[row, column]:
            final_reasons.append("selected pixel is occupied")
        if classification.unknown[row, column]:
            final_reasons.append("selected pixel is unknown")
        clearance_m = clearance_px * metadata.resolution
        if clearance_m < minimum_clearance_m:
            final_reasons.append("selected pixel does not meet minimum clearance")
        if final_reasons:
            rejected.append(
                _rejection(proposal_id, "final_safety_assertion", "; ".join(final_reasons), candidate=candidate, statistics=raster_evidence.as_dict())
            )
            continue
        if (row, column) in seen_goal_pixels:
            rejected.append(
                _rejection(
                    proposal_id,
                    "duplicate_goal_pixel",
                    "duplicate_goal_pixel",
                    candidate=candidate,
                    statistics={**raster_evidence.as_dict(), "duplicate_pixel": [row, column]},
                )
            )
            continue
        seen_goal_pixels.add((row, column))
        goal_order = len(accepted) + 1
        accepted.append(
            {
                "proposal_id": proposal_id,
                "candidate_partition_id": partition_id,
                "goal_order": goal_order,
                "source_candidate_order": candidate["_source_candidate_order"],
                "source_selection_rank": candidate["_source_selection_rank"],
                "canonical_label": None,
                "suggested_label": "unassigned",
                "status": "proposed",
                "confirmed": False,
                "review_only": True,
                "executable": False,
                "polygon_validation_passed": raster_evidence.polygon_validation_passed,
                "faster_safety_passed": raster_evidence.passed,
                "raster_safety_evidence": raster_evidence.as_dict(),
                "polygon": {"type": "polygon", "vertices": [list(point) for point in geometry.vertices]},
                "goal": {
                    "pixel_row": row,
                    "pixel_column": column,
                    "map_x": map_x,
                    "map_y": map_y,
                    "clearance_pixels": clearance_px,
                    "clearance_m": clearance_m,
                },
                "selection_method": "deterministic maximum-clearance, centroid-distance, row-column tie-break",
                "evidence": [
                    "verified polygon",
                    "current-map raster safety evidence",
                    "observed-free occupancy pixels",
                    "deterministic maximum-clearance selection",
                ],
                "source": {
                    "candidate_source": candidate_source,
                    "source_polygon_validation_claim": candidate.get("polygon_validation"),
                    "source_raster_safety_claim": candidate.get("raster_safety_passed"),
                    "source_faster_safety_claim": candidate.get("faster_safety_passed"),
                },
            }
        )
    return OfflineSafeGoalSelectionResult(
        goals=tuple(accepted),
        rejected_safe_goals=tuple(rejected),
        minimum_clearance_m=minimum_clearance_m,
        candidate_source=candidate_source,
    )


def candidates_from_report(document: dict[str, Any], candidate_source: str) -> list[dict[str, Any]]:
    """Read only selected or all-safe records; never reinterpret rejected records."""
    if candidate_source == "selected":
        records = document.get("proposals")
    elif candidate_source == "all-safe":
        records = document.get("safe_candidates")
    else:
        raise OfflineSafeGoalSelectionError("candidate_source must be selected or all-safe.")
    if not isinstance(records, list):
        expected = "proposals" if candidate_source == "selected" else "safe_candidates"
        raise OfflineSafeGoalSelectionError(
            f"Candidate document does not contain a {expected} list for --candidate-source {candidate_source}."
        )
    return records


def validate_report_map_identity(document: dict[str, Any], metadata: RosMapMetadata) -> dict[str, Any]:
    """Fail closed unless the proposal report describes exactly the loaded map."""
    claimed = document.get("map_identity")
    actual = map_identity(metadata).as_dict()
    if not isinstance(claimed, dict) or set(claimed) != set(actual):
        raise OfflineSafeGoalSelectionError("Candidate report map_identity is missing or has an invalid field set.")
    integer_fields = ("width", "height", "negate")
    float_fields = ("resolution", "occupied_thresh", "free_thresh")
    for field in integer_fields:
        if isinstance(claimed[field], bool) or not isinstance(claimed[field], int):
            raise OfflineSafeGoalSelectionError(f"Candidate report map_identity.{field} has an invalid type.")
    for field in float_fields:
        if isinstance(claimed[field], bool) or not isinstance(claimed[field], (int, float)) or not math.isfinite(float(claimed[field])):
            raise OfflineSafeGoalSelectionError(f"Candidate report map_identity.{field} has an invalid type.")
    origin = claimed["origin"]
    if not isinstance(origin, list) or len(origin) != 3 or any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
        for value in origin
    ):
        raise OfflineSafeGoalSelectionError("Candidate report map_identity.origin has an invalid type.")
    for field in ("schema_version", "image_sha256", "fingerprint"):
        if not isinstance(claimed[field], str) or not claimed[field]:
            raise OfflineSafeGoalSelectionError(f"Candidate report map_identity.{field} has an invalid type.")
    for field, expected in actual.items():
        if field == "origin":
            matches = tuple(float(value) for value in claimed[field]) == tuple(expected)
        elif field in float_fields:
            matches = float(claimed[field]) == expected
        else:
            matches = claimed[field] == expected
        if not matches:
            raise OfflineSafeGoalSelectionError(f"Candidate report map_identity mismatch: {field}.")
    return actual


def load_candidate_report(path: Path, candidate_source: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineSafeGoalSelectionError(f"Cannot load candidate report {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise OfflineSafeGoalSelectionError("Candidate report must contain an object.")
    return document, candidates_from_report(document, candidate_source)


def safe_goal_report(
    metadata: RosMapMetadata,
    result: OfflineSafeGoalSelectionResult,
    *,
    map_id: str,
    input_candidate_count: int,
) -> dict[str, Any]:
    current_map_identity = map_identity(metadata)
    return {
        "schema_version": "1.0",
        "review_only": True,
        "executable": False,
        "map_id": map_id,
        "map_identity": current_map_identity.as_dict(),
        "map_metadata": {
            "yaml_path": str(metadata.yaml_path),
            "image_path": str(metadata.image_path),
            "width": metadata.image.width,
            "height": metadata.image.height,
            "resolution": metadata.resolution,
            "origin": list(metadata.origin),
            "map_fingerprint": current_map_identity.fingerprint,
        },
        "candidate_source": result.candidate_source,
        "minimum_clearance_m": result.minimum_clearance_m,
        "input_candidate_count": input_candidate_count,
        "accepted_goal_count": len(result.goals),
        "rejected_goal_count": len(result.rejected_safe_goals),
        "goals": list(result.goals),
    }



def write_safe_goal_artifacts(
    output_dir: Path,
    metadata: RosMapMetadata,
    result: OfflineSafeGoalSelectionResult,
    *,
    map_id: str,
    input_candidate_count: int,
) -> dict[str, Path]:
    """Write a new review directory once; existing output is deliberately never replaced."""
    output = Path(output_dir)
    parent = output.parent
    temporary: Path | None = None
    published = False
    if output.exists():
        raise OfflineSafeGoalSelectionError(
            f"Safe-goal output directory already exists: {output}. Delete it or choose a new output path."
        )
    try:
        parent.mkdir(parents=True, exist_ok=True)
        report = safe_goal_report(metadata, result, map_id=map_id, input_candidate_count=input_candidate_count)
        temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=parent))
        candidates_path = temporary / "safe_goal_candidates.json"
        rejected_path = temporary / "rejected_safe_goals.json"
        candidates_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        rejected_path.write_text(
            json.dumps({"rejected_safe_goals": list(result.rejected_safe_goals)}, indent=2) + "\n",
            encoding="utf-8",
        )
        write_safe_goal_preview(temporary / "safe_goal_preview.png", metadata, result)
        os.replace(temporary, output)
        published = True
    finally:
        if not published and temporary is not None and temporary.exists():
            shutil.rmtree(temporary)
    return {
        "safe_goal_candidates": output / "safe_goal_candidates.json",
        "rejected_safe_goals": output / "rejected_safe_goals.json",
        "safe_goal_preview": output / "safe_goal_preview.png",
    }


def write_safe_goal_preview(
    path: Path, metadata: RosMapMetadata, result: OfflineSafeGoalSelectionResult
) -> Path:
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise OfflineSafeGoalSelectionError("Pillow is required to render safe-goal previews.") from exc
    classification = classify_occupancy(metadata)
    background = np.zeros((metadata.image.height, metadata.image.width, 3), dtype=np.uint8)
    background[classification.occupied] = (35, 35, 35)
    background[classification.free] = (245, 245, 245)
    background[classification.unknown] = (145, 145, 145)
    # A tiny occupancy map must not crop review text just because its raster is tiny.
    header_height = 88
    canvas_width = max(640, metadata.image.width)
    map_x_offset = (canvas_width - metadata.image.width) // 2
    image = Image.new("RGB", (canvas_width, metadata.image.height + header_height), "white")
    image.paste(Image.fromarray(background, mode="RGB"), (map_x_offset, header_height))
    drawing = ImageDraw.Draw(image)
    label_stride = max(1, math.ceil(len(result.goals) / 24))
    for goal in result.goals:
        vertices = [map_to_pixel(metadata, point[0], point[1]) for point in goal["polygon"]["vertices"]]
        shifted = [(column + map_x_offset, row + header_height) for column, row in vertices]
        drawing.line([*shifted, shifted[0]], fill=(38, 139, 210), width=2)
        column, row = goal["goal"]["pixel_column"] + map_x_offset, goal["goal"]["pixel_row"] + header_height
        drawing.ellipse((column - 3, row - 3, column + 3, row + 3), fill=(38, 139, 210))
        if len(result.goals) <= 24 or goal["goal_order"] == 1 or goal["goal_order"] == len(result.goals) or (goal["goal_order"] - 1) % label_stride == 0:
            drawing.text((column + 4, row + 4), str(goal["goal_order"]), fill=(0, 0, 0))
    for rejected in result.rejected_safe_goals:
        polygon = rejected.get("polygon")
        if not isinstance(polygon, dict) or not isinstance(polygon.get("vertices"), list):
            continue
        try:
            vertices = [map_to_pixel(metadata, point[0], point[1]) for point in polygon["vertices"]]
        except (MapMetadataError, IndexError, TypeError):
            continue
        if len(vertices) >= 2:
            shifted = [(column + map_x_offset, row + header_height) for column, row in vertices]
            drawing.line([*shifted, shifted[0]], fill=(181, 137, 0), width=1)
    drawing.rectangle((1, 1, max(1, image.width - 2), header_height - 2), fill=(255, 255, 255), outline=(0, 0, 0))
    drawing.text((6, 7), "offline proposed safe goals", fill=(0, 0, 0))
    drawing.text((6, 26), "offline review only; not executable navigation commands", fill=(0, 0, 0))
    drawing.text((6, 45), "observed free-space zones; not rooms; blue=accepted, gold=rejected", fill=(0, 0, 0))
    order_note = "all goal_order labels" if len(result.goals) <= 24 else f"sparse goal_order labels (every {label_stride})"
    drawing.text((6, 64), f"accepted={len(result.goals)} rejected={len(result.rejected_safe_goals)}; {order_note}", fill=(0, 0, 0))
    try:
        image.save(path, format="PNG")
    except OSError as exc:
        raise OfflineSafeGoalSelectionError(f"Cannot write safe-goal preview {path}: {exc}") from exc
    return path
