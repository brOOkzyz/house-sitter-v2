#!/usr/bin/env python3
"""Run a deterministic, local simulation-only sequence over demo safe goals."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.simulation_sequence import (  # noqa: E402
    DEFAULT_SEQUENCE,
    SimulationSequenceError,
    build_simulation_sequence,
    load_sequence_inputs,
    write_simulation_sequence_artifacts,
)


LOCAL_ROOT = (PROJECT_ROOT / "local_annotations").resolve()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create deterministic simulation-only sequence artifacts; never starts ROS or navigation.")
    parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path)
    parser.add_argument("--sequence", default=",".join(DEFAULT_SEQUENCE))
    parser.add_argument("--output-dir", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        output = args.output_dir.resolve()
        try:
            output.relative_to(LOCAL_ROOT)
        except ValueError as exc:
            raise SimulationSequenceError(f"Sequence output must be inside Git-ignored {LOCAL_ROOT}.") from exc
        sequence = tuple(args.sequence.split(","))
        regions, goals = load_sequence_inputs(args.semantic_regions, args.safe_goals)
        plan, result = build_simulation_sequence(regions, goals, sequence)
        paths = write_simulation_sequence_artifacts(output, plan, result)
        print(f"plan: {paths['plan']}")
        print(f"result: {paths['result']}")
        print(f"succeeded_steps: {result['succeeded_steps']}")
        return 0
    except SimulationSequenceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
