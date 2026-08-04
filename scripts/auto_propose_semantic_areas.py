#!/usr/bin/env python3
"""Create offline semantic-area candidates for review; no ROS, network, or navigation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.automatic_area_proposal import (  # noqa: E402
    AutomaticAreaProposalError,
    build_hole_aware_review_batch,
    build_confirmed_registry_draft,
    proposal_report,
    propose_semantic_areas,
    safe_candidates_report,
    write_all_safe_candidates_preview,
    write_hole_aware_diagnostics,
    write_preview,
    write_proposal_report,
)
from house_sitter_core.map_metadata import MapMetadataError, load_ros_map  # noqa: E402
from house_sitter_core.semantic_waypoints import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    SemanticWaypointError,
    SemanticWaypointRegistry,
)


LOCAL_ROOT = (PROJECT_ROOT / "local_annotations").resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline free-space proposals only; results require human review and never execute navigation."
    )
    parser.add_argument("--map", required=True, type=Path, help="ROS map YAML")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--map-id", required=True, help="Manual map identifier; generated reports also carry strict map identity")
    parser.add_argument("--auto-propose", action="store_true", help="Compatibility flag; proposal mode is the default")
    parser.add_argument(
        "--proposal-mode",
        choices=("legacy", "hole-aware-cells"),
        default="legacy",
        help="legacy keeps the original component contour path; hole-aware-cells is review-only.",
    )
    parser.add_argument("--doorway-width-m", type=float, default=0.8)
    parser.add_argument("--minimum-area-m2", type=float, default=1.0)
    parser.add_argument("--simplify-tolerance-m", type=float, default=0.1)
    parser.add_argument("--minimum-seed-separation-m", type=float, default=None)
    parser.add_argument("--maximum-proposal-count", type=int, default=24)
    parser.add_argument(
        "--selection-strategy",
        choices=("largest-first", "spatial-balanced"),
        default="largest-first",
        help="Review-batch selection only; largest-first is the compatible default.",
    )
    parser.add_argument("--maximum-unknown-boundary-ratio", type=float, default=0.20)
    parser.add_argument("--minimum-wall-support-ratio", type=float, default=0.0)
    parser.add_argument("--preview-output", type=Path, default=LOCAL_ROOT / "auto_area_proposals.png")
    parser.add_argument("--proposal-output", type=Path, default=LOCAL_ROOT / "auto_area_proposals.json")
    parser.add_argument(
        "--safe-candidates-output",
        type=Path,
        default=None,
        help="Hole-aware only: complete validator + raster-safety passed candidate set.",
    )
    parser.add_argument(
        "--all-safe-preview-output",
        type=Path,
        default=None,
        help="Hole-aware only: all safe review-cell outlines, separate from the selected batch preview.",
    )
    parser.add_argument("--auto-draft-output", type=Path, default=None)
    parser.add_argument(
        "--diagnostics-output-dir",
        type=Path,
        default=None,
        help="Optional local directory for hole-aware review diagnostics.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print proposals without writing any file")
    return parser.parse_args(argv)


def _local_output(path: Path) -> Path:
    output = Path(path).resolve()
    try:
        output.relative_to(LOCAL_ROOT)
    except ValueError as exc:
        raise AutomaticAreaProposalError(
            f"Automatic proposal output must be inside {LOCAL_ROOT}."
        ) from exc
    return output


def _validate_map_id(map_id: str) -> str:
    if not isinstance(map_id, str) or not map_id.strip() or map_id != map_id.strip():
        raise AutomaticAreaProposalError(
            "map_id must be a non-empty string without leading or trailing whitespace."
        )
    return map_id


def _print_proposals(proposals) -> None:
    print(f"candidate_count: {len(proposals)}")
    for proposal in proposals:
        print(
            f"{proposal.proposal_id}: area_m2={proposal.map_area_m2:.3f}, "
            f"centroid_map={proposal.centroid_map}, "
            f"label={proposal.proposed_label or 'unassigned'}, "
            f"label_confidence={proposal.label_confidence:.2f}, "
            f"warnings={list(proposal.warnings)}"
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        map_id = _validate_map_id(args.map_id)
        metadata = load_ros_map(args.map)
        registry = SemanticWaypointRegistry(args.registry)
        parameters = {
            "proposal_mode": args.proposal_mode,
            "doorway_width_m": args.doorway_width_m,
            "minimum_area_m2": args.minimum_area_m2,
            "simplify_tolerance_m": args.simplify_tolerance_m,
            "minimum_seed_separation_m": args.minimum_seed_separation_m,
            "maximum_proposal_count": args.maximum_proposal_count,
            "selection_strategy": args.selection_strategy,
            "maximum_unknown_boundary_ratio": args.maximum_unknown_boundary_ratio,
            "minimum_wall_support_ratio": args.minimum_wall_support_ratio,
        }
        review_batch = None
        if args.proposal_mode == "hole-aware-cells":
            hole_aware_parameters = {
                key: value for key, value in parameters.items() if key != "proposal_mode"
            }
            review_batch = build_hole_aware_review_batch(metadata, **hole_aware_parameters)
            proposals = list(review_batch.selected_proposals)
        else:
            proposals = propose_semantic_areas(metadata, **parameters)
        if args.proposal_mode == "hole-aware-cells" and args.auto_draft_output is not None:
            raise AutomaticAreaProposalError(
                "hole-aware-cells proposals are review-only and cannot create a registry draft."
            )
        _print_proposals(proposals)
        if review_batch is not None:
            print(
                f"safe_candidate_count: {len(review_batch.safe_candidates)}; "
                f"selected_count: {len(review_batch.selected_proposals)}; "
                f"unselected_safe_count: {len(review_batch.safe_candidates) - len(review_batch.selected_proposals)}"
            )
        if args.dry_run:
            return 0

        preview_path = _local_output(args.preview_output)
        proposal_path = _local_output(args.proposal_output)
        report = proposal_report(
            metadata, proposals, map_id=map_id, algorithm_parameters=parameters, review_batch=review_batch
        )
        write_preview(
            preview_path,
            metadata,
            proposals,
            proposal_mode=args.proposal_mode,
            selection_strategy=args.selection_strategy,
            safe_candidate_count=len(review_batch.safe_candidates) if review_batch else None,
            all_safe_candidates=list(review_batch.safe_candidates) if review_batch else None,
        )
        write_proposal_report(proposal_path, report)
        print(f"preview_output: {preview_path}")
        print(f"proposal_output: {proposal_path}")

        if review_batch is not None:
            safe_path = _local_output(
                args.safe_candidates_output or proposal_path.parent / "safe_candidates.json"
            )
            all_safe_preview_path = _local_output(
                args.all_safe_preview_output or preview_path.parent / "all_safe_candidates.png"
            )
            write_proposal_report(
                safe_path,
                safe_candidates_report(
                    metadata, review_batch, map_id=map_id, algorithm_parameters=parameters
                ),
            )
            write_all_safe_candidates_preview(
                all_safe_preview_path,
                metadata,
                list(review_batch.safe_candidates),
                selection_strategy=args.selection_strategy,
            )
            print(f"safe_candidates_output: {safe_path}")
            print(f"all_safe_preview_output: {all_safe_preview_path}")

        if args.diagnostics_output_dir is not None:
            diagnostic_root = _local_output(args.diagnostics_output_dir)
            if args.proposal_mode != "hole-aware-cells":
                raise AutomaticAreaProposalError(
                    "--diagnostics-output-dir is available only with --proposal-mode hole-aware-cells."
                )
            diagnostics = write_hole_aware_diagnostics(
                diagnostic_root,
                metadata,
                minimum_area_m2=args.minimum_area_m2,
                doorway_width_m=args.doorway_width_m,
                simplify_tolerance_m=args.simplify_tolerance_m,
                minimum_seed_separation_m=args.minimum_seed_separation_m,
                maximum_proposal_count=args.maximum_proposal_count,
                maximum_unknown_boundary_ratio=args.maximum_unknown_boundary_ratio,
                minimum_wall_support_ratio=args.minimum_wall_support_ratio,
                safe_candidates=list(review_batch.safe_candidates),
                selected_proposals=proposals,
                selection_strategy=args.selection_strategy,
            )
            for name, path in diagnostics.items():
                print(f"{name}: {path}")

        if args.auto_draft_output is not None:
            draft_path = _local_output(args.auto_draft_output)
            draft = build_confirmed_registry_draft(registry, proposals, map_id=map_id)
            draft_path.parent.mkdir(parents=True, exist_ok=True)
            draft_path.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
            print(f"auto_draft_output: {draft_path}")
        return 0
    except (MapMetadataError, SemanticWaypointError, AutomaticAreaProposalError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
