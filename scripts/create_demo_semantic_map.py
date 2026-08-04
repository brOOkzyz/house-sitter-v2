#!/usr/bin/env python3
"""Create four deterministic synthetic demo labels and their offline safe goals.

This is deliberately a local presentation tool.  It neither identifies rooms
nor updates the production semantic registry or a map.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.automatic_area_proposal import _spatial_balanced_order  # noqa: E402
from house_sitter_core.map_coordinates import map_to_pixel  # noqa: E402
from house_sitter_core.map_metadata import load_ros_map, map_identity  # noqa: E402
from house_sitter_core.offline_safe_goal_selection import (  # noqa: E402
    OfflineSafeGoalSelectionError,
    candidates_from_report,
    safe_goal_report,
    select_offline_safe_goals,
    validate_report_map_identity,
)


LABELS = ("living_room", "kitchen", "bedroom", "charging_area")
LOCAL_ROOT = (PROJECT_ROOT / "local_annotations").resolve()


class DemoSemanticMapError(ValueError):
    """Raised when a complete safe synthetic demonstration cannot be built."""


def _candidate_collection(document: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    if isinstance(document.get("safe_candidates"), list):
        return "all-safe", candidates_from_report(document, "all-safe")
    return "selected", candidates_from_report(document, "selected")


def _centroid_pixel(candidate: dict[str, Any], metadata: Any) -> tuple[float, float]:
    centroid = candidate.get("centroid_pixel")
    if isinstance(centroid, (list, tuple)) and len(centroid) == 2:
        return float(centroid[0]), float(centroid[1])
    vertices = candidate.get("geometry", {}).get("vertices", [])
    pixels = [map_to_pixel(metadata, float(point[0]), float(point[1])) for point in vertices]
    if not pixels:
        raise DemoSemanticMapError("safe candidate is missing a polygon centroid.")
    return (sum(point[0] for point in pixels) / len(pixels), sum(point[1] for point in pixels) / len(pixels))


def _area(candidate: dict[str, Any]) -> float:
    value = candidate.get("map_area_m2", 0.0)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def _select_four(records: list[dict[str, Any]], document: dict[str, Any], metadata: Any) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records:
        proposal_id, partition_id = record.get("proposal_id"), record.get("partition_id")
        if not isinstance(proposal_id, str) or not isinstance(partition_id, str):
            continue
        unique.setdefault((proposal_id, partition_id), record)
    candidates = list(unique.values())
    if len(candidates) < 4:
        raise DemoSemanticMapError("Fewer than four unique current-map safe candidates are available.")
    strategy = document.get("selection_strategy")
    if strategy == "spatial-balanced":
        proxies = [
            SimpleNamespace(
                candidate_id=record["proposal_id"], partition_id=record["partition_id"],
                centroid_pixel=_centroid_pixel(record, metadata), map_area_m2=_area(record),
            )
            for record in candidates
        ]
        by_id = {record["proposal_id"]: record for record in candidates}
        ordered = [by_id[item.candidate_id] for item in _spatial_balanced_order(proxies, count=4, metadata=metadata)]
    else:
        ordered = sorted(candidates, key=lambda item: (-_area(item), _centroid_pixel(item, metadata)[1], _centroid_pixel(item, metadata)[0], item["partition_id"], item["proposal_id"]))
    selected = ordered[:4]
    return sorted(selected, key=lambda item: (_centroid_pixel(item, metadata)[1], _centroid_pixel(item, metadata)[0], item["partition_id"], item["proposal_id"]))


def _demo_record(candidate: dict[str, Any], label: str, assignment_order: int) -> dict[str, Any]:
    record = dict(candidate)
    record.update({
        "canonical_label": label, "suggested_label": label,
        "demo_only": True, "synthetic_semantics": True, "ground_truth": False,
        "review_only": True, "executable": False,
        "demo_assignment_order": assignment_order,
        "provenance": ["automatic_synthetic_demo_assignment"],
    })
    return record


def _capture_source_provenance(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Capture original report positions before any filtering or sorting."""
    captured = []
    for order, candidate in enumerate(records, start=1):
        record = dict(candidate)
        record["_source_candidate_order"] = order
        record["_source_selection_rank"] = candidate.get("selection_rank")
        captured.append(record)
    return captured


def _draw_preview(path: Path, metadata: Any, regions: list[dict[str, Any]], goals: list[dict[str, Any]] | None) -> None:
    from PIL import Image, ImageDraw
    from house_sitter_core.automatic_area_proposal import classify_occupancy

    masks = classify_occupancy(metadata)
    background = __import__("numpy").zeros((metadata.image.height, metadata.image.width, 3), dtype="uint8")
    background[masks.occupied] = (35, 35, 35); background[masks.free] = (245, 245, 245); background[masks.unknown] = (145, 145, 145)
    header = 70; width = max(760, metadata.image.width); offset = (width - metadata.image.width) // 2
    image = Image.new("RGB", (width, metadata.image.height + header), "white"); image.paste(Image.fromarray(background), (offset, header))
    drawing = ImageDraw.Draw(image); colors = ((220, 50, 47), (38, 139, 210), (133, 153, 0), (108, 113, 196))
    goal_by_label = {goal["canonical_label"]: goal for goal in goals or []}
    for index, region in enumerate(regions):
        vertices = [map_to_pixel(metadata, *point) for point in region["polygon"]["vertices"]]
        points = [(x + offset, y + header) for x, y in vertices]; color = colors[index]
        drawing.line([*points, points[0]], fill=color, width=2)
        x, y = points[0]; drawing.text((x + 4, y + 4), region["canonical_label"], fill=color)
        goal = goal_by_label.get(region["canonical_label"])
        if goal:
            x, y = goal["goal"]["pixel_column"] + offset, goal["goal"]["pixel_row"] + header
            drawing.ellipse((x - 4, y - 4, x + 4, y + 4), fill=color)
            drawing.text((x + 5, y + 5), f"{region['canonical_label']} #{goal['goal_order']}", fill=(0, 0, 0))
    drawing.text((8, 6), "SYNTHETIC DEMO LABELS", fill=(180, 0, 0)); drawing.text((8, 25), "NOT GROUND TRUTH", fill=(180, 0, 0)); drawing.text((8, 44), "SIMULATION / REVIEW ONLY", fill=(180, 0, 0))
    image.save(path, format="PNG")


def create_demo(metadata: Any, document: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    validate_report_map_identity(document, metadata)
    source, raw_records = _candidate_collection(document)
    records = _capture_source_provenance(raw_records)
    # Re-run the existing safety selector before choosing; source booleans are never trusted.
    preflight = select_offline_safe_goals(metadata, records, minimum_clearance_m=0.30, candidate_source=source)
    accepted_ids = {(goal["proposal_id"], goal["candidate_partition_id"]) for goal in preflight.goals}
    selected = _select_four([record for record in records if (record.get("proposal_id"), record.get("partition_id")) in accepted_ids], document, metadata)
    if len(selected) != 4:
        raise DemoSemanticMapError("Could not select four distinct current-map safe regions.")
    labelled = [_demo_record(record, LABELS[index], index + 1) for index, record in enumerate(selected)]
    result = select_offline_safe_goals(metadata, labelled, minimum_clearance_m=0.30, candidate_source=source)
    if len(result.goals) != 4 or result.rejected_safe_goals:
        raise DemoSemanticMapError("Four distinct safe goals could not be generated; no demo artifacts were published.")
    by_label = {goal["canonical_label"]: goal for goal in result.goals}
    goals = []
    for order, label in enumerate(LABELS, 1):
        goal = dict(by_label.get(label, {}))
        if not goal:
            raise DemoSemanticMapError("A labelled region did not retain its safe goal.")
        goal.update({"goal_order": order, "demo_only": True, "synthetic_semantics": True, "ground_truth": False, "review_only": True, "executable": False})
        goal["evidence"] = [*goal["evidence"], "automatic_synthetic_demo_assignment"]
        goals.append(goal)
    identity = map_identity(metadata).as_dict(); map_id = document.get("map_id")
    if not isinstance(map_id, str) or not map_id.strip(): raise DemoSemanticMapError("Candidate report has no valid map_id.")
    regions = {"schema_version": "1.0", "demo_only": True, "synthetic_semantics": True, "ground_truth": False, "review_only": True, "executable": False, "map_id": map_id, "map_identity": identity, "provenance": ["automatic_synthetic_demo_assignment"], "regions": [{"canonical_label": item["canonical_label"], "polygon": item["geometry"], "proposal_id": item["proposal_id"], "partition_id": item["partition_id"], "map_id": map_id, "map_identity": identity, "source_candidate_order": item["_source_candidate_order"], "source_selection_rank": item["_source_selection_rank"], "demo_assignment_order": item["demo_assignment_order"], "demo_only": True, "synthetic_semantics": True, "ground_truth": False, "review_only": True, "executable": False, "provenance": ["automatic_synthetic_demo_assignment"]} for item in labelled]}
    report = safe_goal_report(metadata, type("Result", (), {"goals": tuple(goals), "rejected_safe_goals": (), "minimum_clearance_m": 0.30, "candidate_source": source})(), map_id=map_id, input_candidate_count=4)
    report.update({"demo_only": True, "synthetic_semantics": True, "ground_truth": False, "review_only": True, "executable": False, "provenance": ["automatic_synthetic_demo_assignment"]})
    return regions, report, {"rejected_safe_goals": []}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create local synthetic demo labels and verified review-only safe goals.")
    parser.add_argument("--map", required=True, type=Path); parser.add_argument("--candidates", required=True, type=Path); parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir.resolve(); temporary = None; published = False
    try:
        try:
            output.relative_to(LOCAL_ROOT)
        except ValueError as exc:
            raise DemoSemanticMapError(f"Demo output must be inside Git-ignored {LOCAL_ROOT}.") from exc
        if output.exists(): raise DemoSemanticMapError(f"Demo output directory already exists: {output}")
        metadata = load_ros_map(args.map); document = json.loads(args.candidates.read_text(encoding="utf-8"))
        if not isinstance(document, dict): raise DemoSemanticMapError("Candidate document must be an object.")
        regions, goals, rejected = create_demo(metadata, document)
        output.parent.mkdir(parents=True, exist_ok=True); temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=output.parent))
        (temporary / "demo_semantic_regions.json").write_text(json.dumps(regions, indent=2) + "\n", encoding="utf-8")
        (temporary / "safe_goal_candidates.json").write_text(json.dumps(goals, indent=2) + "\n", encoding="utf-8")
        (temporary / "rejected_safe_goals.json").write_text(json.dumps(rejected, indent=2) + "\n", encoding="utf-8")
        _draw_preview(temporary / "demo_semantic_preview.png", metadata, regions["regions"], None)
        _draw_preview(temporary / "safe_goal_preview.png", metadata, regions["regions"], goals["goals"])
        os.replace(temporary, output); published = True
        print(f"demo_output: {output}"); print("accepted_goal_count: 4"); return 0
    except (OSError, ValueError, OfflineSafeGoalSelectionError) as exc:
        print(f"Error: {exc}", file=sys.stderr); return 2
    finally:
        if not published and temporary is not None and temporary.exists(): shutil.rmtree(temporary)


if __name__ == "__main__": raise SystemExit(main())
