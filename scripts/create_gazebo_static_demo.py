#!/usr/bin/env python3
"""Create a static, simulation-only Gazebo Sim world from local demo artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.gazebo_static_demo import (  # noqa: E402
    GazeboStaticDemoError,
    generate_world,
    write_demo,
)


LOCAL_ROOT = (PROJECT_ROOT / "local_annotations").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a static synthetic-label Gazebo Sim world; no ROS or navigation is started.")
    parser.add_argument("--semantic-regions", required=True, type=Path)
    parser.add_argument("--safe-goals", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        output = args.output_dir.resolve()
        output.relative_to(LOCAL_ROOT)
        world, manifest = generate_world(args.semantic_regions, args.safe_goals)
        write_demo(output, world, manifest)
        print("SYNTHETIC DEMO LABELS")
        print("NOT GROUND TRUTH")
        print("SIMULATION / REVIEW ONLY")
        print("ROBOT MOTION DISABLED")
        print(f"world: {output / 'synthetic_demo.sdf'}")
        print(f"manifest: {output / 'gazebo_demo_manifest.json'}")
        return 0
    except (GazeboStaticDemoError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
