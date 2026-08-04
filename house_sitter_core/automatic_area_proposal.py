"""Offline free-space region proposals for human review; never a navigation interface."""

from __future__ import annotations

import copy
import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from scipy import ndimage

from .map_coordinates import pixel_to_map
from .map_metadata import MapMetadataError, RosMapMetadata, map_identity
from .semantic_waypoints import SemanticWaypointError, SemanticWaypointRegistry


class AutomaticAreaProposalError(ValueError):
    """Raised for invalid offline proposal parameters or outputs."""


@dataclass(frozen=True)
class OccupancyClassification:
    """Disjoint map masks derived from ROS YAML thresholds, not fixed grayscale values."""

    occupied: np.ndarray
    free: np.ndarray
    unknown: np.ndarray


@dataclass(frozen=True)
class AutoAreaProposal:
    """A geometrically validated candidate requiring human semantic confirmation."""

    proposal_id: str
    pixel_polygon: tuple[tuple[float, float], ...]
    map_polygon: tuple[tuple[float, float], ...]
    pixel_area: int
    map_area_m2: float
    centroid_pixel: tuple[float, float]
    centroid_map: tuple[float, float]
    bounding_box: tuple[int, int, int, int]
    confidence: float
    proposed_label: str | None
    label_confidence: float
    label_status: str
    label_evidence: tuple[str, ...]
    requires_confirmation: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)
    proposal_mode: str = "legacy"
    partition_id: str | None = None
    raster_safety: dict[str, Any] = field(default_factory=dict)
    unknown_boundary_ratio: float | None = None
    occupied_wall_support_ratio: float | None = None
    # This is evidence emitted by the internal validator call below.  It is
    # not accepted from CLI input and is not a generic trust/bypass marker.
    polygon_validation_passed: bool = False
    candidate_id: str | None = None
    selected_for_review: bool = False
    selection_rank: int | None = None


@dataclass(frozen=True)
class HoleAwareReviewBatch:
    """All safe cells plus one deterministic, bounded human-review batch."""

    safe_candidates: tuple[AutoAreaProposal, ...]
    selected_proposals: tuple[AutoAreaProposal, ...]
    selection_strategy: str
    maximum_proposal_count: int
    partition_info: dict[str, Any]


def _positive_finite(value: float, name: str, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AutomaticAreaProposalError(f"{name} must be a finite number.")
    if value < 0 or (not allow_zero and value <= 0):
        comparison = "non-negative" if allow_zero else "greater than zero"
        raise AutomaticAreaProposalError(f"{name} must be {comparison}.")
    return float(value)


def classify_occupancy(metadata: RosMapMetadata) -> OccupancyClassification:
    """Classify pixels with ROS map negate/free/occupied threshold semantics."""
    grayscale = np.frombuffer(metadata.image.pixels, dtype=np.uint8).reshape(
        metadata.image.height, metadata.image.width
    )
    intensity = grayscale.astype(np.float64) / 255.0
    occupancy = intensity if metadata.negate else 1.0 - intensity
    free = occupancy < metadata.free_thresh
    occupied = occupancy > metadata.occupied_thresh
    unknown = ~(free | occupied)
    return OccupancyClassification(occupied=occupied, free=free, unknown=unknown)


def _disk(radius: int) -> np.ndarray:
    y_axis, x_axis = np.ogrid[-radius : radius + 1, -radius : radius + 1]
    return x_axis * x_axis + y_axis * y_axis <= radius * radius


def _split_free_space(
    free: np.ndarray, resolution: float, doorway_width_m: float
) -> tuple[np.ndarray, list[str]]:
    """Break connections narrower than the requested doorway width, then regrow labels."""
    radius = max(1, math.ceil(doorway_width_m / (2.0 * resolution)))
    structure = _disk(radius)
    eroded = ndimage.binary_erosion(free, structure=structure, border_value=0)
    seed_labels, seed_count = ndimage.label(eroded, structure=np.ones((3, 3), dtype=bool))
    free_labels, free_count = ndimage.label(free, structure=np.ones((3, 3), dtype=bool))
    region_labels = np.zeros(free.shape, dtype=np.int32)
    warnings: list[str] = []
    if seed_count == 0:
        return region_labels, ["No free-space seed remains after doorway-width erosion."]

    next_label = 1
    skipped_components = 0
    for free_component in range(1, free_count + 1):
        component = free_labels == free_component
        seed_ids = np.unique(seed_labels[component])
        seed_ids = seed_ids[seed_ids > 0]
        if not len(seed_ids):
            skipped_components += 1
            continue
        seeds = np.isin(seed_labels, seed_ids)
        _, indices = ndimage.distance_transform_edt(~seeds, return_indices=True)
        nearest_seed_ids = seed_labels[tuple(indices)]
        for seed_id in seed_ids:
            assigned = component & (nearest_seed_ids == seed_id)
            if np.any(assigned):
                region_labels[assigned] = next_label
                next_label += 1
    if skipped_components:
        warnings.append(
            f"{skipped_components} thin free-space components had no stable room seed and were skipped."
        )
    return region_labels, warnings


def _pixel_polygon_for_region(
    region: np.ndarray, simplify_tolerance_px: float
) -> tuple[tuple[float, float], ...] | None:
    image = region.astype(np.uint8) * 255
    contours, hierarchy = cv2.findContours(image, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if len(contours) != 1 or hierarchy is None or hierarchy[0][0][2] != -1:
        return None
    contour = contours[0]
    approximation = cv2.approxPolyDP(contour, simplify_tolerance_px, True)
    if len(approximation) < 3:
        return None
    return tuple((float(point[0][0]), float(point[0][1])) for point in approximation)


def _raster_safety_check(
    classification: OccupancyClassification,
    region: np.ndarray,
    pixel_polygon: tuple[tuple[float, float], ...],
) -> dict[str, Any]:
    """Re-rasterize a candidate and reject every unsafe interior pixel.

    Contour pixels sit on free/obstacle boundaries, so one pixel is treated as
    boundary tolerance.  The strictly eroded interior must be wholly contained
    in the specific partition and in observed free space.
    """
    polygon_mask = np.zeros(region.shape, dtype=np.uint8)
    points = np.rint(np.asarray(pixel_polygon, dtype=np.float64)).astype(np.int32)
    cv2.fillPoly(polygon_mask, [points], 1)
    interior = ndimage.binary_erosion(
        polygon_mask.astype(bool), structure=np.ones((3, 3), dtype=bool), border_value=0
    )
    interior_count = int(interior.sum())
    occupied_count = int((interior & classification.occupied).sum())
    unknown_count = int((interior & classification.unknown).sum())
    non_partition_count = int((interior & ~region).sum())
    free_count = int((interior & classification.free).sum())
    safe_ratio = free_count / interior_count if interior_count else 0.0
    reasons: list[str] = []
    if interior_count == 0:
        reasons.append("polygon has no interior pixels after one-pixel boundary tolerance")
    if occupied_count:
        reasons.append("polygon interior contains occupied pixels")
    if unknown_count:
        reasons.append("polygon interior contains unknown pixels")
    if non_partition_count:
        reasons.append("polygon interior escapes its observed-free-space partition")
    return {
        "boundary_tolerance_pixels": 1,
        "interior_pixel_count": interior_count,
        "interior_free_count": free_count,
        "interior_occupied_count": occupied_count,
        "interior_unknown_count": unknown_count,
        "interior_non_partition_count": non_partition_count,
        "interior_safe_free_ratio": safe_ratio,
        "raster_safety_passed": not reasons,
        "raster_safety_rejection_reason": "; ".join(reasons) if reasons else None,
    }


def _boundary_ratios(
    classification: OccupancyClassification, region: np.ndarray
) -> tuple[float, float]:
    boundary = region & ~ndimage.binary_erosion(
        region, structure=np.ones((3, 3), dtype=bool), border_value=0
    )
    boundary_count = max(1, int(boundary.sum()))
    unknown_boundary = boundary & ndimage.binary_dilation(
        classification.unknown, structure=np.ones((3, 3), dtype=bool)
    )
    occupied_boundary = boundary & ndimage.binary_dilation(
        classification.occupied, structure=np.ones((3, 3), dtype=bool)
    )
    return float(unknown_boundary.sum() / boundary_count), float(occupied_boundary.sum() / boundary_count)


def _map_polygon(metadata: RosMapMetadata, pixel_polygon: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    try:
        return tuple(pixel_to_map(metadata, x, y) for x, y in pixel_polygon)
    except MapMetadataError as exc:
        raise AutomaticAreaProposalError(str(exc)) from exc


def _weak_label_match(
    bounding_box: tuple[int, int, int, int]
) -> tuple[str | None, float, str, tuple[str, ...], bool]:
    """Geometry is only weak evidence: it never produces a confirmed semantic label."""
    min_x, min_y, max_x, max_y = bounding_box
    width, height = max_x - min_x + 1, max_y - min_y + 1
    aspect_ratio = max(width, height) / max(1, min(width, height))
    if aspect_ratio >= 3.0:
        return (
            "hallway",
            0.25,
            "low_confidence_candidate",
            ("geometry_only: elongated free-space region; no room-layout evidence exists in the repository.",),
            True,
        )
    return (None, 0.0, "unassigned", (), True)


def _select_hole_aware_seeds(
    safe_free: np.ndarray,
    *,
    minimum_separation_pixels: int,
    maximum_seed_count: int,
) -> np.ndarray:
    """Select deterministic clearance-maxima/grid seeds without crossing obstacles."""
    distance = ndimage.distance_transform_edt(safe_free)
    window = max(3, 2 * minimum_separation_pixels + 1)
    maxima = safe_free & (distance == ndimage.maximum_filter(distance, size=window))
    candidate_mask = maxima.copy()
    # Local maxima can be sparse on wide plateaus.  A coarse grid is only a
    # fallback partition aid; it does not turn unknown or occupied into free.
    candidate_mask[::minimum_separation_pixels, ::minimum_separation_pixels] |= safe_free[
        ::minimum_separation_pixels, ::minimum_separation_pixels
    ]
    ys, xs = np.nonzero(candidate_mask)
    order = sorted(range(len(xs)), key=lambda index: (-distance[ys[index], xs[index]], ys[index], xs[index]))
    selected: list[tuple[int, int]] = []
    minimum_squared = minimum_separation_pixels * minimum_separation_pixels
    for index in order:
        y, x = int(ys[index]), int(xs[index])
        if any((y - other_y) ** 2 + (x - other_x) ** 2 < minimum_squared for other_y, other_x in selected):
            continue
        selected.append((y, x))
        if len(selected) >= maximum_seed_count:
            break
    seeds = np.zeros(safe_free.shape, dtype=np.int32)
    for seed_id, (y, x) in enumerate(selected, start=1):
        seeds[y, x] = seed_id
    return seeds


def _hole_aware_regions(
    free: np.ndarray,
    *,
    resolution: float,
    doorway_width_m: float,
    minimum_seed_separation_m: float | None,
    maximum_seed_count: int = 144,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Partition eroded observed free space into bounded, hole-free candidates.

    This is a marker-based Euclidean cell decomposition.  Labels are assigned
    only inside an eroded observed-free mask; every label is then split into
    connected components before contour extraction.  Raster safety validation
    remains the final authority for acceptance.
    """
    # Match the legacy doorway erosion: cells are never seeded through a
    # connection narrower than the requested doorway width.
    clearance_pixels = max(1, math.ceil(doorway_width_m / (2.0 * resolution)))
    safe_free = ndimage.binary_erosion(
        free, structure=_disk(clearance_pixels), border_value=0
    )
    separation_m = minimum_seed_separation_m
    if separation_m is None:
        separation_m = max(1.0, doorway_width_m * 2.0)
    separation_pixels = max(1, math.ceil(separation_m / resolution))
    seeds = _select_hole_aware_seeds(
        safe_free,
        minimum_separation_pixels=separation_pixels,
        # Candidate generation is deliberately independent from the later
        # human-review batch size.  A smaller review batch must never hide or
        # change the underlying safe candidate set.
        maximum_seed_count=max(8, maximum_seed_count),
    )
    if not np.any(seeds):
        return np.zeros(free.shape, dtype=np.int32), {
            "safe_free_pixel_count": int(safe_free.sum()),
            "clearance_pixels": clearance_pixels,
            "seed_count": 0,
            "partition_count": 0,
            "warnings": ["No clearance-safe seed exists in observed free space."],
        }
    _, nearest = ndimage.distance_transform_edt(~(seeds > 0), return_indices=True)
    nearest_seed = seeds[tuple(nearest)]
    provisional = np.where(safe_free, nearest_seed, 0)
    regions = np.zeros(free.shape, dtype=np.int32)
    next_label = 1
    for seed_id in range(1, int(seeds.max()) + 1):
        components, count = ndimage.label(
            provisional == seed_id, structure=np.ones((3, 3), dtype=bool)
        )
        for component_id in range(1, count + 1):
            component = components == component_id
            if np.any(component):
                regions[component] = next_label
                next_label += 1
    return regions, {
        "safe_free_pixel_count": int(safe_free.sum()),
        "clearance_pixels": clearance_pixels,
        "minimum_seed_separation_m": separation_m,
        "seed_count": int(seeds.max()),
        "partition_count": int(regions.max()),
        "warnings": [],
    }


def _build_all_hole_aware_safe_candidates(
    map_metadata: RosMapMetadata,
    *,
    minimum_area_m2: float,
    doorway_width_m: float,
    simplify_tolerance_m: float,
    minimum_seed_separation_m: float | None,
    maximum_unknown_boundary_ratio: float,
    minimum_wall_support_ratio: float,
) -> tuple[list[AutoAreaProposal], dict[str, Any]]:
    classification = classify_occupancy(map_metadata)
    regions, diagnostics = _hole_aware_regions(
        classification.free,
        resolution=map_metadata.resolution,
        doorway_width_m=doorway_width_m,
        minimum_seed_separation_m=minimum_seed_separation_m,
        maximum_seed_count=144,
    )
    minimum_pixels = math.ceil(minimum_area_m2 / (map_metadata.resolution ** 2))
    tolerance_px = simplify_tolerance_m / map_metadata.resolution
    candidates: list[AutoAreaProposal] = []
    for partition_id in range(1, int(regions.max()) + 1):
        region = regions == partition_id
        pixel_area = int(region.sum())
        if pixel_area < minimum_pixels:
            continue
        pixel_polygon = _pixel_polygon_for_region(region, tolerance_px)
        if pixel_polygon is None:
            continue
        map_polygon = _map_polygon(map_metadata, pixel_polygon)
        try:
            SemanticWaypointRegistry.validate_polygon_geometry(
                "automatic_observed_free_space_partition", "map",
                {"type": "polygon", "vertices": [list(point) for point in map_polygon]},
            )
        except SemanticWaypointError:
            continue
        polygon_validation_passed = True
        if not polygon_validation_passed:  # defensive invariant for safe output
            continue
        raster_safety = _raster_safety_check(classification, region, pixel_polygon)
        if not raster_safety["raster_safety_passed"]:
            continue
        ys, xs = np.nonzero(region)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        unknown_boundary_ratio, wall_support_ratio = _boundary_ratios(classification, region)
        if unknown_boundary_ratio > maximum_unknown_boundary_ratio:
            continue
        if wall_support_ratio < minimum_wall_support_ratio:
            continue
        candidates.append(
            AutoAreaProposal(
                proposal_id=f"candidate_partition_{partition_id}",
                pixel_polygon=pixel_polygon,
                map_polygon=map_polygon,
                pixel_area=pixel_area,
                map_area_m2=pixel_area * map_metadata.resolution ** 2,
                centroid_pixel=(float(xs.mean()), float(ys.mean())),
                centroid_map=pixel_to_map(map_metadata, float(xs.mean()), float(ys.mean())),
                bounding_box=bbox,
                confidence=0.25,
                proposed_label=None,
                label_confidence=0.0,
                label_status="proposed",
                label_evidence=("automatic_observed_free_space_partition",),
                requires_confirmation=True,
                warnings=tuple(diagnostics["warnings"]),
                proposal_mode="hole-aware-cells",
                partition_id=f"partition_{partition_id}",
                raster_safety=raster_safety,
                unknown_boundary_ratio=unknown_boundary_ratio,
                occupied_wall_support_ratio=wall_support_ratio,
                polygon_validation_passed=polygon_validation_passed,
                candidate_id=f"candidate_partition_{partition_id}",
            )
        )
    candidates.sort(key=lambda item: (item.partition_id or "", item.centroid_pixel[1], item.centroid_pixel[0]))
    return candidates, diagnostics


def _spatial_balanced_order(
    candidates: list[AutoAreaProposal], *, count: int, metadata: RosMapMetadata
) -> list[AutoAreaProposal]:
    """Round-robin deterministic grid coverage for a human review batch."""
    if not candidates:
        return []
    grid_size = max(1, math.ceil(math.sqrt(min(count or len(candidates), len(candidates)))))
    buckets: dict[tuple[int, int], list[AutoAreaProposal]] = {}
    for candidate in candidates:
        x, y = candidate.centroid_pixel
        column = min(grid_size - 1, max(0, int(x * grid_size / metadata.image.width)))
        row = min(grid_size - 1, max(0, int(y * grid_size / metadata.image.height)))
        buckets.setdefault((row, column), []).append(candidate)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: (-item.map_area_m2, item.partition_id or "", item.candidate_id or ""))
    ordered: list[AutoAreaProposal] = []
    offset = 0
    while True:
        added = False
        for key in sorted(buckets):
            if offset < len(buckets[key]):
                ordered.append(buckets[key][offset])
                added = True
        if not added:
            return ordered
        offset += 1


def _select_review_batch(
    candidates: list[AutoAreaProposal],
    *,
    maximum_proposal_count: int,
    selection_strategy: str,
    metadata: RosMapMetadata,
) -> tuple[list[AutoAreaProposal], list[AutoAreaProposal]]:
    if selection_strategy not in {"largest-first", "spatial-balanced"}:
        raise AutomaticAreaProposalError(
            "selection_strategy must be largest-first or spatial-balanced."
        )
    if selection_strategy == "largest-first":
        ordered = sorted(
            candidates,
            key=lambda item: (-item.map_area_m2, item.centroid_pixel[1], item.centroid_pixel[0], item.partition_id or ""),
        )
    else:
        ordered = _spatial_balanced_order(candidates, count=maximum_proposal_count, metadata=metadata)
    chosen = ordered if maximum_proposal_count == 0 else ordered[:maximum_proposal_count]
    selected_ids = {candidate.candidate_id for candidate in chosen}
    ranks = {candidate.candidate_id: index for index, candidate in enumerate(chosen, start=1)}
    safe_candidates = [
        AutoAreaProposal(
            **{
                **asdict(candidate),
                "selected_for_review": candidate.candidate_id in selected_ids,
                "selection_rank": ranks.get(candidate.candidate_id),
            }
        )
        for candidate in candidates
    ]
    selected = [
        AutoAreaProposal(
            **{
                **asdict(candidate),
                "proposal_id": f"proposal_{index}",
                "selected_for_review": True,
                "selection_rank": ranks[candidate.candidate_id],
            }
        )
        for index, candidate in enumerate(
            sorted(chosen, key=lambda item: (item.centroid_pixel[1], item.centroid_pixel[0], item.partition_id or "")),
            start=1,
        )
    ]
    return safe_candidates, selected


def build_hole_aware_review_batch(
    map_metadata: RosMapMetadata,
    *,
    minimum_area_m2: float,
    doorway_width_m: float,
    simplify_tolerance_m: float,
    minimum_seed_separation_m: float | None = None,
    maximum_proposal_count: int = 24,
    maximum_unknown_boundary_ratio: float = 0.20,
    minimum_wall_support_ratio: float = 0.0,
    selection_strategy: str = "largest-first",
) -> HoleAwareReviewBatch:
    """Build all safe review cells, then select a deterministic display batch."""
    minimum_area_m2 = _positive_finite(minimum_area_m2, "minimum_area_m2")
    doorway_width_m = _positive_finite(doorway_width_m, "doorway_width_m")
    simplify_tolerance_m = _positive_finite(
        simplify_tolerance_m, "simplify_tolerance_m", allow_zero=True
    )
    if minimum_seed_separation_m is not None:
        minimum_seed_separation_m = _positive_finite(
            minimum_seed_separation_m, "minimum_seed_separation_m"
        )
    if (
        isinstance(maximum_proposal_count, bool)
        or not isinstance(maximum_proposal_count, int)
        or maximum_proposal_count < 0
    ):
        raise AutomaticAreaProposalError(
            "maximum_proposal_count must be a non-negative integer; zero selects all safe candidates."
        )
    maximum_unknown_boundary_ratio = _positive_finite(
        maximum_unknown_boundary_ratio, "maximum_unknown_boundary_ratio", allow_zero=True
    )
    minimum_wall_support_ratio = _positive_finite(
        minimum_wall_support_ratio, "minimum_wall_support_ratio", allow_zero=True
    )
    if maximum_unknown_boundary_ratio > 1 or minimum_wall_support_ratio > 1:
        raise AutomaticAreaProposalError("boundary support ratios must be between zero and one.")
    candidates, partition_info = _build_all_hole_aware_safe_candidates(
        map_metadata,
        minimum_area_m2=minimum_area_m2,
        doorway_width_m=doorway_width_m,
        simplify_tolerance_m=simplify_tolerance_m,
        minimum_seed_separation_m=minimum_seed_separation_m,
        maximum_unknown_boundary_ratio=maximum_unknown_boundary_ratio,
        minimum_wall_support_ratio=minimum_wall_support_ratio,
    )
    safe_candidates, selected = _select_review_batch(
        candidates,
        maximum_proposal_count=maximum_proposal_count,
        selection_strategy=selection_strategy,
        metadata=map_metadata,
    )
    return HoleAwareReviewBatch(
        safe_candidates=tuple(safe_candidates),
        selected_proposals=tuple(selected),
        selection_strategy=selection_strategy,
        maximum_proposal_count=maximum_proposal_count,
        partition_info=partition_info,
    )


def propose_semantic_areas(
    map_metadata: RosMapMetadata,
    *,
    minimum_area_m2: float,
    doorway_width_m: float,
    simplify_tolerance_m: float,
    proposal_mode: str = "legacy",
    minimum_seed_separation_m: float | None = None,
    maximum_proposal_count: int = 24,
    maximum_unknown_boundary_ratio: float = 0.20,
    minimum_wall_support_ratio: float = 0.0,
    selection_strategy: str = "largest-first",
) -> list[AutoAreaProposal]:
    """Return stable, validated free-space polygons without writing any configuration."""
    minimum_area_m2 = _positive_finite(minimum_area_m2, "minimum_area_m2")
    doorway_width_m = _positive_finite(doorway_width_m, "doorway_width_m")
    simplify_tolerance_m = _positive_finite(
        simplify_tolerance_m, "simplify_tolerance_m", allow_zero=True
    )
    if proposal_mode not in {"legacy", "hole-aware-cells"}:
        raise AutomaticAreaProposalError("proposal_mode must be legacy or hole-aware-cells.")
    if minimum_seed_separation_m is not None:
        minimum_seed_separation_m = _positive_finite(
            minimum_seed_separation_m, "minimum_seed_separation_m"
        )
    if isinstance(maximum_proposal_count, bool) or not isinstance(maximum_proposal_count, int) or maximum_proposal_count < 0:
        raise AutomaticAreaProposalError(
            "maximum_proposal_count must be a non-negative integer; zero selects all safe candidates."
        )
    maximum_unknown_boundary_ratio = _positive_finite(
        maximum_unknown_boundary_ratio, "maximum_unknown_boundary_ratio", allow_zero=True
    )
    minimum_wall_support_ratio = _positive_finite(
        minimum_wall_support_ratio, "minimum_wall_support_ratio", allow_zero=True
    )
    if maximum_unknown_boundary_ratio > 1 or minimum_wall_support_ratio > 1:
        raise AutomaticAreaProposalError("boundary support ratios must be between zero and one.")
    if proposal_mode == "hole-aware-cells":
        return list(build_hole_aware_review_batch(
            map_metadata,
            minimum_area_m2=minimum_area_m2,
            doorway_width_m=doorway_width_m,
            simplify_tolerance_m=simplify_tolerance_m,
            minimum_seed_separation_m=minimum_seed_separation_m,
            maximum_proposal_count=maximum_proposal_count,
            maximum_unknown_boundary_ratio=maximum_unknown_boundary_ratio,
            minimum_wall_support_ratio=minimum_wall_support_ratio,
            selection_strategy=selection_strategy,
        ).selected_proposals)
    classification = classify_occupancy(map_metadata)
    region_labels, global_warnings = _split_free_space(
        classification.free, map_metadata.resolution, doorway_width_m
    )
    minimum_pixels = math.ceil(minimum_area_m2 / (map_metadata.resolution ** 2))
    simplify_tolerance_px = simplify_tolerance_m / map_metadata.resolution
    candidates: list[dict[str, Any]] = []

    for label in range(1, int(region_labels.max()) + 1):
        region = region_labels == label
        pixel_area = int(region.sum())
        if pixel_area < minimum_pixels:
            continue
        ys, xs = np.nonzero(region)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
        pixel_polygon = _pixel_polygon_for_region(region, simplify_tolerance_px)
        if pixel_polygon is None:
            continue
        map_polygon = _map_polygon(map_metadata, pixel_polygon)
        try:
            SemanticWaypointRegistry.validate_polygon_geometry(
                "automatic_candidate",
                "map",
                {"type": "polygon", "vertices": [list(point) for point in map_polygon]},
            )
        except SemanticWaypointError:
            continue
        polygon_validation_passed = True
        centroid_pixel = (float(xs.mean()), float(ys.mean()))
        centroid_map = pixel_to_map(map_metadata, *centroid_pixel)
        proposed_label, label_confidence, label_status, evidence, confirmation = _weak_label_match(bbox)
        candidates.append(
            {
                "pixel_polygon": pixel_polygon,
                "map_polygon": map_polygon,
                "pixel_area": pixel_area,
                "map_area_m2": pixel_area * map_metadata.resolution ** 2,
                "centroid_pixel": centroid_pixel,
                "centroid_map": centroid_map,
                "bounding_box": bbox,
                "confidence": 0.8,
                "proposed_label": proposed_label,
                "label_confidence": label_confidence,
                "label_status": label_status,
                "label_evidence": evidence,
                "requires_confirmation": confirmation,
                "warnings": tuple(global_warnings),
                "polygon_validation_passed": polygon_validation_passed,
            }
        )

    candidates.sort(key=lambda candidate: (candidate["centroid_pixel"][1], candidate["centroid_pixel"][0]))
    return [
        AutoAreaProposal(proposal_id=f"proposal_{index}", **candidate)
        for index, candidate in enumerate(candidates, start=1)
    ]


def _proposal_record(proposal: AutoAreaProposal) -> dict[str, Any]:
    """Serialize review-only geometry with explicit safety results."""
    return {
        **asdict(proposal),
        "canonical_label": None,
        "suggested_label": "unassigned",
        "status": "proposed" if proposal.proposal_mode == "hole-aware-cells" else proposal.label_status,
        "geometry": {"type": "polygon", "vertices": [list(point) for point in proposal.map_polygon]},
        "pixel_geometry": {"type": "polygon", "vertices": [list(point) for point in proposal.pixel_polygon]},
        "map_frame": "map",
        "polygon_validation": proposal.polygon_validation_passed,
        "raster_safety_passed": bool(proposal.raster_safety.get("raster_safety_passed")),
    }


def proposal_report(
    metadata: RosMapMetadata,
    proposals: list[AutoAreaProposal],
    *,
    map_id: str,
    algorithm_parameters: dict[str, Any],
    review_batch: HoleAwareReviewBatch | None = None,
) -> dict[str, Any]:
    """Build a local review report; it deliberately is not a registry schema document."""
    return {
        "proposal_schema_version": "1.0",
        "proposal_mode": proposals[0].proposal_mode if proposals else algorithm_parameters.get("proposal_mode", "legacy"),
        "generated_by": "automatic_area_proposal",
        "requires_human_review": True,
        "registry_compatibility": "automatic proposal is not a user-confirmed annotation and cannot enter the production registry without review.",
        "map_id": map_id,
        "map_metadata": {
            "yaml_path": str(metadata.yaml_path),
            "image_path": str(metadata.image_path),
            "width": metadata.image.width,
            "height": metadata.image.height,
            "resolution": metadata.resolution,
            "origin": list(metadata.origin),
            "negate": metadata.negate,
            "occupied_thresh": metadata.occupied_thresh,
            "free_thresh": metadata.free_thresh,
        },
        "map_identity": map_identity(metadata).as_dict(),
        "algorithm_parameters": algorithm_parameters,
        "warnings": (
            ["No validated candidate polygons were found; occupancy-grid geometry alone was insufficient."]
            if not proposals
            else []
        ),
        "occupancy_counts": {
            "free": int(classify_occupancy(metadata).free.sum()),
            "occupied": int(classify_occupancy(metadata).occupied.sum()),
            "unknown": int(classify_occupancy(metadata).unknown.sum()),
        },
        "selection_strategy": review_batch.selection_strategy if review_batch else "largest-first",
        "safe_candidate_count": len(review_batch.safe_candidates) if review_batch else len(proposals),
        "selected_count": len(proposals),
        "unselected_safe_count": (
            len(review_batch.safe_candidates) - len(proposals) if review_batch else 0
        ),
        "maximum_proposal_count": review_batch.maximum_proposal_count if review_batch else None,
        "proposals": [_proposal_record(proposal) for proposal in proposals],
    }


def safe_candidates_report(
    metadata: RosMapMetadata,
    batch: HoleAwareReviewBatch,
    *,
    map_id: str,
    algorithm_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Serialize the complete safe candidate set separately from the review batch."""
    safe_area = sum(candidate.map_area_m2 for candidate in batch.safe_candidates)
    selected_area = sum(candidate.map_area_m2 for candidate in batch.selected_proposals)
    safe_free_area = batch.partition_info.get("safe_free_pixel_count", 0) * metadata.resolution ** 2
    return {
        "schema_version": "1.0",
        "proposal_mode": "hole-aware-cells",
        "map_id": map_id,
        "map_identity": map_identity(metadata).as_dict(),
        "total_partition_count": batch.partition_info.get("partition_count", 0),
        "safe_candidate_count": len(batch.safe_candidates),
        "selected_count": len(batch.selected_proposals),
        "unselected_safe_count": len(batch.safe_candidates) - len(batch.selected_proposals),
        "selection_strategy": batch.selection_strategy,
        "maximum_proposal_count": batch.maximum_proposal_count,
        "safe_candidate_area_m2": safe_area,
        "selected_safe_area_m2": selected_area,
        "unselected_safe_area_m2": safe_area - selected_area,
        "selected_safe_candidate_area_coverage": selected_area / safe_area if safe_area else 0.0,
        "selected_safe_free_map_coverage": selected_area / safe_free_area if safe_free_area else 0.0,
        "algorithm_parameters": algorithm_parameters,
        "safe_candidates": [_proposal_record(candidate) for candidate in batch.safe_candidates],
    }


def write_proposal_report(path: Path, report: dict[str, Any]) -> Path:
    output = Path(path).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise AutomaticAreaProposalError(f"Cannot write proposal report {output}: {exc}") from exc
    return output


def write_preview(
    path: Path,
    metadata: RosMapMetadata,
    proposals: list[AutoAreaProposal],
    *,
    proposal_mode: str = "legacy",
    selection_strategy: str = "largest-first",
    safe_candidate_count: int | None = None,
    all_safe_candidates: list[AutoAreaProposal] | None = None,
) -> Path:
    """Render a top-left-origin review preview; colors have no execution meaning."""
    try:
        from PIL import Image, ImageDraw, PngImagePlugin
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise AutomaticAreaProposalError("Pillow is required to render proposal previews.") from exc
    classification = classify_occupancy(metadata)
    background = np.zeros((metadata.image.height, metadata.image.width, 3), dtype=np.uint8)
    background[classification.occupied] = (35, 35, 35)
    background[classification.free] = (245, 245, 245)
    background[classification.unknown] = (145, 145, 145)
    image = Image.fromarray(background, mode="RGB")
    drawing = ImageDraw.Draw(image)
    colors = ((220, 50, 47), (38, 139, 210), (133, 153, 0), (181, 137, 0), (108, 113, 196))
    # In the review-only mode, faint outlines retain the context of safe but
    # unselected cells without making them look rejected or semantically named.
    if proposal_mode == "hole-aware-cells" and all_safe_candidates is not None:
        selected_ids = {proposal.candidate_id for proposal in proposals}
        for candidate in all_safe_candidates:
            if candidate.candidate_id not in selected_ids:
                drawing.line(
                    [*candidate.pixel_polygon, candidate.pixel_polygon[0]],
                    fill=(100, 100, 100), width=1,
                )
    for index, proposal in enumerate(proposals):
        color = colors[index % len(colors)]
        drawing.line([*proposal.pixel_polygon, proposal.pixel_polygon[0]], fill=color, width=2)
        label = proposal.proposal_id if proposal_mode == "hole-aware-cells" else (
            f"{proposal.proposal_id}: {proposal.proposed_label or 'unassigned'} ({proposal.label_confidence:.2f})"
        )
        drawing.text(proposal.centroid_pixel, label, fill=color)
    if proposal_mode == "hole-aware-cells":
        panel_width = min(image.width - 8, 510)
        panel_height = min(image.height - 8, 100)
        drawing.rectangle((4, 4, panel_width, panel_height), fill=(255, 255, 255), outline=(0, 0, 0))
        drawing.text((10, 10), "Observed free-space zones — review only, not rooms", fill=(0, 0, 0))
        drawing.text((10, 30), f"mode: {proposal_mode}; strategy: {selection_strategy}", fill=(0, 0, 0))
        drawing.text(
            (10, 48),
            f"selected: {len(proposals)} / safe candidates: {safe_candidate_count if safe_candidate_count is not None else len(proposals)}",
            fill=(0, 0, 0),
        )
        drawing.text((10, 66), "unassigned; not written to production registry", fill=(0, 0, 0))
        drawing.text((10, 84), "solid=selected review batch; gray=safe but unselected", fill=(0, 0, 0))
    else:
        drawing.rectangle((4, 4, 210, 50), fill=(255, 255, 255), outline=(0, 0, 0))
        drawing.text((8, 8), "occupied / free / unknown", fill=(0, 0, 0))
        drawing.rectangle((8, 28, 18, 38), fill=(35, 35, 35))
        drawing.rectangle((76, 28, 86, 38), fill=(245, 245, 245), outline=(0, 0, 0))
        drawing.rectangle((128, 28, 138, 38), fill=(145, 145, 145))
    output = Path(path).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        png_info = PngImagePlugin.PngInfo()
        if proposal_mode == "hole-aware-cells":
            png_info.add_text("review_notice", "Observed free-space zones — review only, not rooms")
        image.save(output, format="PNG", pnginfo=png_info)
    except OSError as exc:
        raise AutomaticAreaProposalError(f"Cannot write preview image {output}: {exc}") from exc
    return output


def write_all_safe_candidates_preview(
    path: Path,
    metadata: RosMapMetadata,
    safe_candidates: list[AutoAreaProposal],
    *,
    selection_strategy: str,
) -> Path:
    """Render all safe review-cell outlines without treating them as rooms."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise AutomaticAreaProposalError("Pillow is required to render proposal previews.") from exc
    classification = classify_occupancy(metadata)
    background = np.zeros((metadata.image.height, metadata.image.width, 3), dtype=np.uint8)
    background[classification.occupied] = (35, 35, 35)
    background[classification.free] = (245, 245, 245)
    background[classification.unknown] = (145, 145, 145)
    image = Image.fromarray(background, mode="RGB")
    drawing = ImageDraw.Draw(image)
    for candidate in safe_candidates:
        drawing.line([*candidate.pixel_polygon, candidate.pixel_polygon[0]], fill=(60, 110, 170), width=1)
    panel_width = min(image.width - 8, 510)
    panel_height = min(image.height - 8, 82)
    drawing.rectangle((4, 4, panel_width, panel_height), fill=(255, 255, 255), outline=(0, 0, 0))
    drawing.text((10, 10), "All observed free-space zones — review only, not rooms", fill=(0, 0, 0))
    drawing.text((10, 30), f"safe candidates: {len(safe_candidates)}; strategy preview: {selection_strategy}", fill=(0, 0, 0))
    drawing.text((10, 48), "unassigned; not written to production registry", fill=(0, 0, 0))
    drawing.text((10, 66), "thin blue outlines = all validator + raster-safety passed cells", fill=(0, 0, 0))
    output = Path(path).resolve()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        image.save(output, format="PNG")
    except OSError as exc:
        raise AutomaticAreaProposalError(f"Cannot write preview image {output}: {exc}") from exc
    return output


def write_hole_aware_diagnostics(
    output_directory: Path,
    metadata: RosMapMetadata,
    *,
    minimum_area_m2: float,
    doorway_width_m: float,
    simplify_tolerance_m: float,
    minimum_seed_separation_m: float | None,
    maximum_proposal_count: int,
    maximum_unknown_boundary_ratio: float,
    minimum_wall_support_ratio: float,
    safe_candidates: list[AutoAreaProposal],
    selected_proposals: list[AutoAreaProposal],
    selection_strategy: str,
) -> dict[str, Path]:
    """Write review-only hole-aware diagnostics under an explicitly local path."""
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise AutomaticAreaProposalError("Pillow is required to render proposal diagnostics.") from exc
    classification = classify_occupancy(metadata)
    regions, partition_info = _hole_aware_regions(
        classification.free,
        resolution=metadata.resolution,
        doorway_width_m=doorway_width_m,
        minimum_seed_separation_m=minimum_seed_separation_m,
        maximum_seed_count=144,
    )
    minimum_pixels = math.ceil(minimum_area_m2 / (metadata.resolution ** 2))
    tolerance_px = simplify_tolerance_m / metadata.resolution
    rejected: list[dict[str, Any]] = []
    raster_results: list[dict[str, Any]] = []
    for partition_id in range(1, int(regions.max()) + 1):
        region = regions == partition_id
        row: dict[str, Any] = {
            "partition_id": f"partition_{partition_id}",
            "pixel_area": int(region.sum()),
            "area_m2": float(region.sum() * metadata.resolution ** 2),
        }
        if row["pixel_area"] < minimum_pixels:
            row["rejection_reason"] = "below minimum area"
            rejected.append(row)
            continue
        pixel_polygon = _pixel_polygon_for_region(region, tolerance_px)
        if pixel_polygon is None:
            row["rejection_reason"] = "multiple contours, holes, or no usable external contour"
            rejected.append(row)
            continue
        try:
            map_polygon = _map_polygon(metadata, pixel_polygon)
            SemanticWaypointRegistry.validate_polygon_geometry(
                "automatic_observed_free_space_partition", "map",
                {"type": "polygon", "vertices": [list(point) for point in map_polygon]},
            )
        except SemanticWaypointError as exc:
            row["rejection_reason"] = str(exc)
            rejected.append(row)
            continue
        raster = _raster_safety_check(classification, region, pixel_polygon)
        row["raster_safety"] = raster
        raster_results.append(row)
        if not raster["raster_safety_passed"]:
            row["rejection_reason"] = raster["raster_safety_rejection_reason"]
            rejected.append(row)
            continue
        unknown_ratio, wall_ratio = _boundary_ratios(classification, region)
        row["unknown_boundary_ratio"] = unknown_ratio
        row["occupied_wall_support_ratio"] = wall_ratio
        if unknown_ratio > maximum_unknown_boundary_ratio:
            row["rejection_reason"] = "unknown boundary ratio exceeds configured maximum"
            rejected.append(row)
        elif wall_ratio < minimum_wall_support_ratio:
            row["rejection_reason"] = "wall support ratio is below configured minimum"
            rejected.append(row)

    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    rejected_path = output / "rejected_candidates.json"
    raster_path = output / "raster_safety_report.json"
    labels_path = output / "partition_labels.png"
    masks_path = output / "diagnostic_masks.png"
    markdown_path = output / "proposal_report.md"
    rejected_path.write_text(json.dumps(rejected, indent=2) + "\n", encoding="utf-8")
    raster_path.write_text(json.dumps(raster_results, indent=2) + "\n", encoding="utf-8")

    height, width = regions.shape
    label_image = np.full((height, width, 3), 145, dtype=np.uint8)
    label_image[classification.occupied] = (35, 35, 35)
    label_image[classification.free] = (245, 245, 245)
    colors = ((220, 50, 47), (38, 139, 210), (133, 153, 0), (181, 137, 0), (108, 113, 196))
    for label in range(1, int(regions.max()) + 1):
        label_image[regions == label] = colors[(label - 1) % len(colors)]
    labels_image = Image.fromarray(label_image, mode="RGB")
    labels_draw = ImageDraw.Draw(labels_image)
    labels_draw.rectangle((4, 4, 320, 38), fill="white", outline="black")
    labels_draw.text((8, 8), f"hole-aware partitions: {partition_info['partition_count']}", fill="black")
    labels_image.save(labels_path, format="PNG")

    occupancy_rgb = np.full((height, width, 3), 145, dtype=np.uint8)
    occupancy_rgb[classification.occupied] = (35, 35, 35)
    occupancy_rgb[classification.free] = (245, 245, 245)
    safe_free = regions > 0
    safe_rgb = occupancy_rgb.copy()
    safe_rgb[safe_free] = (85, 180, 110)
    rejected_mask = np.zeros((height, width), dtype=bool)
    for row in rejected:
        partition_text = row["partition_id"].removeprefix("partition_")
        rejected_mask[regions == int(partition_text)] = True
    rejected_rgb = occupancy_rgb.copy()
    rejected_contours, _ = cv2.findContours(
        rejected_mask.astype(np.uint8) * 255, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )
    for contour in rejected_contours:
        cv2.polylines(rejected_rgb, [contour], True, (220, 50, 47), 2)

    def _caption(image: Image.Image, text: str) -> Image.Image:
        drawing = ImageDraw.Draw(image)
        drawing.rectangle((4, 4, min(image.width - 4, 520), 28), fill="white", outline="black")
        drawing.text((8, 8), text, fill="black")
        return image

    occupancy_panel = _caption(Image.fromarray(occupancy_rgb, mode="RGB"), "occupancy: black=occupied, white=free, gray=unknown")
    safe_panel = _caption(Image.fromarray(safe_rgb, mode="RGB"), "green=clearance-safe observed free")
    rejected_panel = _caption(Image.fromarray(rejected_rgb, mode="RGB"), f"red=all rejected partitions ({len(rejected)})")
    canvas = Image.new("RGB", (width * 2, height * 2), "white")
    canvas.paste(occupancy_panel, (0, 0))
    canvas.paste(safe_panel, (width, 0))
    canvas.paste(labels_image, (0, height))
    canvas.paste(rejected_panel, (width, height))
    canvas.save(masks_path, format="PNG")

    accepted_areas = [proposal.map_area_m2 for proposal in safe_candidates]
    markdown_path.write_text(
        "# Hole-aware observed-free-space review\n\n"
        "automatic proposal is not a user-confirmed annotation and cannot enter the production registry without review.\n\n"
        f"- partitions: {partition_info['partition_count']}\n"
        f"- safe candidates: {len(safe_candidates)}\n"
        f"- selected review batch: {len(selected_proposals)}\n"
        f"- selection strategy: {selection_strategy}\n"
        f"- maximum proposal count: {maximum_proposal_count} (0 means all safe candidates)\n"
        f"- rejected partitions: {len(rejected)}\n"
        f"- clearance pixels: {partition_info['clearance_pixels']}\n"
        f"- area range m²: {min(accepted_areas) if accepted_areas else 0:.3f} to {max(accepted_areas) if accepted_areas else 0:.3f}\n",
        encoding="utf-8",
    )
    return {
        "rejected_candidates": rejected_path,
        "raster_safety_report": raster_path,
        "partition_labels": labels_path,
        "diagnostic_masks": masks_path,
        "proposal_report": markdown_path,
    }


def build_confirmed_registry_draft(
    registry: SemanticWaypointRegistry, proposals: list[AutoAreaProposal], *, map_id: str
) -> dict[str, Any]:
    """Build a strict registry draft only from explicitly confirmed evidence proposals."""
    if not isinstance(map_id, str) or not map_id.strip() or map_id != map_id.strip():
        raise AutomaticAreaProposalError("map_id must be a non-empty string without surrounding whitespace.")
    confirmed = [proposal for proposal in proposals if proposal.label_status == "confirmed_from_existing_evidence"]
    if not confirmed:
        raise AutomaticAreaProposalError("No confirmed proposals are available for a registry draft.")
    labels = [proposal.proposed_label for proposal in confirmed]
    if any(label not in registry.labels for label in labels) or len(set(labels)) != len(labels):
        raise AutomaticAreaProposalError("Confirmed proposals must use unique existing canonical labels.")
    if any(not proposal.label_evidence for proposal in confirmed):
        raise AutomaticAreaProposalError(
            "Confirmed proposals require concrete existing-evidence records."
        )
    draft = copy.deepcopy(registry.config)
    for proposal in confirmed:
        entry = draft["labels"][proposal.proposed_label]
        entry.update(
            {
                "grounding_mode": "user_labelled_map_area",
                "mapping_status": "mapped",
                "frame_id": "map",
                "geometry": {"type": "polygon", "vertices": [list(point) for point in proposal.map_polygon]},
                "source": {"type": "user_annotation", "map_id": map_id},
            }
        )
    try:
        SemanticWaypointRegistry.from_config(draft)
    except SemanticWaypointError as exc:
        raise AutomaticAreaProposalError(str(exc)) from exc
    return draft
