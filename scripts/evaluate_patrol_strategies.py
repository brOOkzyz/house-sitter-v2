#!/usr/bin/env python3
"""Run the local deterministic patrol-strategy experiment; no ROS stack is used."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.patrol_strategy_evaluation import (  # noqa: E402
    evaluate_patrol_strategies, render_patrol_strategy_artifacts, write_patrol_strategy_artifacts,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic simulation-only house patrol strategies.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    result = evaluate_patrol_strategies(ROOT, args.repeats)
    paths = write_patrol_strategy_artifacts(args.output_dir, render_patrol_strategy_artifacts(result))
    print(f"scenario_count: {result['summary']['scenario_count']}")
    print(f"total_runs: {result['summary']['total_runs']}")
    print(f"output_dir: {args.output_dir}")
    print(f"artifact_count: {len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
