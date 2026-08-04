#!/usr/bin/env python3
"""Read and display local semantic-area metadata without any robot action."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.semantic_waypoints import (  # noqa: E402
    DEFAULT_REGISTRY_PATH,
    SemanticWaypointError,
    SemanticWaypointRegistry,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect local semantic-area registry metadata.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--show-geometry", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        registry = SemanticWaypointRegistry(args.registry)
    except SemanticWaypointError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    for label in sorted(registry.labels):
        entry = registry.labels[label]
        area = registry.areas[label]
        geometry = area.geometry
        print(f"label: {label}")
        print(f"aliases: {', '.join(entry['aliases']) or '(none)'}")
        print(f"grounding_mode: {area.grounding_mode}")
        print(f"mapping_status: {area.mapping_status}")
        print(f"frame_id: {geometry.frame_id if geometry else '(none)'}")
        print(f"map_id: {area.map_id or '(none)'}")
        print(f"has_geometry: {'yes' if geometry else 'no'}")
        print(f"polygon_vertex_count: {len(geometry.vertices) if geometry else 0}")
        if args.show_geometry and geometry:
            print(f"geometry: {json.dumps({'type': 'polygon', 'vertices': geometry.vertices})}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
