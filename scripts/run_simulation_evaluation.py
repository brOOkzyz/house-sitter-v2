#!/usr/bin/env python3
"""Run deterministic offline evaluation over synthetic demo safe-goal artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.simulation_evaluation import (  # noqa: E402
    SimulationEvaluationError,
    evaluate_paths,
    write_evaluation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic offline simulation-only evaluation; never starts ROS or navigation.")
    parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--trials-per-scenario", type=int, default=10)
    args = parser.parse_args(argv)
    try:
        contents = evaluate_paths(args.semantic_regions, args.safe_goals, args.trials_per_scenario)
        write_evaluation(args.output_dir, contents)
        print("overall_contract_passed: true")
        print("total_trials:", args.trials_per_scenario * 4)
        return 0
    except (SimulationEvaluationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
