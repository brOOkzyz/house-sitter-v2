#!/usr/bin/env python3
"""Pure-data projection from a reported TaskSpec dictionary to the independent RQ2 oracle schema."""
from __future__ import annotations

import argparse
import json
from typing import Any


def project_task_spec(task: dict[str, Any] | None, decision: str) -> dict[str, Any]:
    if task is None:
        return {"decision": decision}
    metadata = task.get("metadata", {})
    return {"robot_profile": task.get("robot_profile"), "metadata": {"baseline_mode": metadata.get("baseline_mode"), "checks": list(metadata.get("checks", [])), "optimized_visit_order": list(metadata.get("optimized_visit_order", []))}, "steps": [{"skill": step.get("skill"), "parameters": dict(step.get("parameters", {})), "timeout_seconds": step.get("timeout_seconds"), "on_failure": step.get("on_failure")} for step in task.get("steps", [])]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Self-check independent Phase 6 TaskSpec projection.")
    parser.add_argument("command", choices=("self-test",)); parser.parse_args()
    sample = {"robot_profile": "create3_sim", "metadata": {"baseline_mode": "normal_reference", "checks": ["humidity"], "optimized_visit_order": ["bathroom"], "ignored": True}, "steps": [{"skill": "inspect_room", "parameters": {"room": "bathroom"}, "timeout_seconds": 20, "on_failure": "abort", "step_id": "ignored"}]}
    assert project_task_spec(sample, "accept") == {"robot_profile": "create3_sim", "metadata": {"baseline_mode": "normal_reference", "checks": ["humidity"], "optimized_visit_order": ["bathroom"]}, "steps": [{"skill": "inspect_room", "parameters": {"room": "bathroom"}, "timeout_seconds": 20, "on_failure": "abort"}]}
    print(json.dumps({"status": "passed"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
