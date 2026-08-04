#!/usr/bin/env python3
"""List declaration-driven, local simulation-only capabilities."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.skill_catalog import SkillCatalogError, catalog_document  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List simulation-only smart-home skills; no robot or ROS action is performed.")
    parser.add_argument("--category")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        document = catalog_document(args.category)
        if args.json:
            print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print("SIMULATION ONLY / REVIEW ONLY / NOT REAL ROBOT EXECUTION")
            print(f"capability_count: {document['capability_count']}")
            for item in document["capabilities"]:
                support = "supported" if item["supported"] else f"unsupported: {item['unsupported_reason']}"
                required = ",".join(item["required_parameters"]) or "-"
                optional = ",".join(item["optional_parameters"]) or "-"
                flags = f"simulation_only={item['simulation_only']},review_only={item['review_only']},executable={item['executable']}"
                policies = f"safety={item['safety_policy']};interrupt={item['interruption_policy']};recovery={item['recovery_policy']};queue={item['queue_priority_policy']}"
                print(f"{item['skill_name']}\tcategory={item['category']}\tclassification={item['classification']}\tuser_callable={item['user_callable']}\tbuilder={item['implementation_kind']}\t{support}\trequired={required}\toptional={optional}\tflags={flags}\tpolicies={policies}\t{item['description']}")
        return 0
    except SkillCatalogError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
