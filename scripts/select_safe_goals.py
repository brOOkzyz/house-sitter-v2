#!/usr/bin/env python3
"""Create local, review-only deterministic safe-goal artifacts from proposal JSON."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.map_metadata import MapMetadataError, load_ros_map  # noqa: E402
from house_sitter_core.offline_safe_goal_selection import (  # noqa: E402
    OfflineSafeGoalSelectionError,
    load_candidate_report,
    select_offline_safe_goals,
    validate_report_map_identity,
    write_safe_goal_artifacts,
)


LOCAL_ROOT = (PROJECT_ROOT / "local_annotations").resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline review-only observed free-space goals; writes local artifacts, never rooms or navigation commands.", epilog="Output must not exist. The tool never modifies maps or the production registry, starts ROS/Nav2, or generates or executes navigation commands."
    )
    parser.add_argument("--map", required=True, type=Path, help="ROS occupancy-map YAML")
    parser.add_argument("--candidates", required=True, type=Path, help="proposal report JSON containing selected or all-safe candidates")
    parser.add_argument("--candidate-source", choices=("selected", "all-safe"), default="selected")
    parser.add_argument("--output-dir", required=True, type=Path, help="new local directory for three review artifacts; must not already exist")
    parser.add_argument("--minimum-clearance-m", type=float, default=0.30)
    return parser.parse_args(argv)


def _local_output(path: Path) -> Path:
    output = Path(path).resolve()
    try:
        output.relative_to(LOCAL_ROOT)
    except ValueError as exc:
        raise OfflineSafeGoalSelectionError(f"Safe-goal output must be inside {LOCAL_ROOT}.") from exc
    return output


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output_dir = _local_output(args.output_dir)
        metadata = load_ros_map(args.map)
        document, candidates = load_candidate_report(args.candidates, args.candidate_source)
        validate_report_map_identity(document, metadata)
        map_id = document.get("map_id")
        if not isinstance(map_id, str) or not map_id.strip() or map_id != map_id.strip():
            raise OfflineSafeGoalSelectionError("Candidate report map_id must be a non-empty trimmed string.")
        result = select_offline_safe_goals(
            metadata,
            candidates,
            minimum_clearance_m=args.minimum_clearance_m,
            candidate_source=args.candidate_source,
        )
        paths = write_safe_goal_artifacts(
            output_dir,
            metadata,
            result,
            map_id=map_id,
            input_candidate_count=len(candidates),
        )
        print(f"candidate_source: {args.candidate_source}")
        print(f"input_candidate_count: {len(candidates)}")
        print(f"accepted_goal_count: {len(result.goals)}")
        print(f"rejected_goal_count: {len(result.rejected_safe_goals)}")
        print(f"minimum_clearance_m: {result.minimum_clearance_m:.3f}")
        for name, path in paths.items():
            print(f"{name}: {path}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
