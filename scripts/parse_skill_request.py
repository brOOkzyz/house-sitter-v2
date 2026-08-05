#!/usr/bin/env python3
"""Parse a small offline bilingual vocabulary into a simulation skill request."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.natural_language_adapter import (  # noqa: E402
    NaturalLanguageAdapterError, parse_skill_request, validate_with_planner,
)
from house_sitter_core.skill_artifacts import SkillArtifactError, load_skill_inputs  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline deterministic simulation-only natural-language skill adapter.")
    parser.add_argument("--text", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--validate-plan", action="store_true")
    parser.add_argument("--semantic-regions", type=Path)
    parser.add_argument("--safe-goals", type=Path)
    args = parser.parse_args(argv)
    try:
        parsed = parse_skill_request(args.text)
        if args.validate_plan:
            if args.semantic_regions is None or args.safe_goals is None:
                raise NaturalLanguageAdapterError("--validate-plan requires --semantic-regions and --safe-goals.")
            regions, goals = load_skill_inputs(args.semantic_regions, args.safe_goals)
            parsed["planner_validation"] = validate_with_planner(parsed, regions, goals)
        rendered = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if args.output is None:
            print(rendered, end="")
        else:
            args.output.write_text(rendered, encoding="utf-8", newline="")
        return 0
    except (NaturalLanguageAdapterError, SkillArtifactError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
