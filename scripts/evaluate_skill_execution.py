#!/usr/bin/env python3
"""Summarize completed simulation-only Gazebo/Nav2 skill execution artifacts."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.execution_evaluation import (  # noqa: E402
    ExecutionEvaluationError, evaluate_execution_artifacts, write_execution_evaluation,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline evaluator for completed simulation-only Gazebo/Nav2 execution artifacts.")
    parser.add_argument("artifact_dirs", nargs="+", type=Path, help="completed execution artifact directories")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        contents = evaluate_execution_artifacts(args.artifact_dirs)
        paths = write_execution_evaluation(args.output_dir, contents)
    except (ExecutionEvaluationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    print("SIMULATION ONLY")
    print(f"summary: {paths['execution_summary.json']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
