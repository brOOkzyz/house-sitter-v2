#!/usr/bin/env python3
"""Build paper-facing result tables and figures from frozen local artifacts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.paper_results import PaperResultsError, build_paper_results  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build reproducible paper-result materials from local frozen artifacts.")
    parser.add_argument("--robustness-dir", type=Path)
    parser.add_argument("--temporal-dir", type=Path)
    parser.add_argument("--patrol-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--regenerate", action="store_true")
    args = parser.parse_args()
    try:
        paths = build_paper_results(ROOT, args.output_dir, robustness_dir=args.robustness_dir, temporal_dir=args.temporal_dir,
                                    patrol_dir=args.patrol_dir, regenerate=args.regenerate)
    except (PaperResultsError, OSError, ValueError) as exc:
        print(f"论文结果生成失败：{exc}", file=sys.stderr)
        return 2
    print(f"output_dir: {args.output_dir}")
    print(f"table_and_document_count: {len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
