"""Shared deterministic fixtures for simulation skill behavior tests."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("skill_demo_support", ROOT / "tests" / "test_demo_semantic_map.py")
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)


def demo_artifacts() -> tuple[dict[str, Any], dict[str, Any]]:
    case = _MODULE.DemoSemanticMapTests()
    metadata = case.metadata()
    return _MODULE.demo.create_demo(metadata, case.document(metadata))[:2]


def write_artifacts(directory: Path) -> tuple[Path, Path]:
    regions, goals = demo_artifacts()
    regions_path = directory / "regions.json"
    goals_path = directory / "goals.json"
    regions_path.write_text(json.dumps(regions), encoding="utf-8")
    goals_path.write_text(json.dumps(goals), encoding="utf-8")
    return regions_path, goals_path


SAMPLE_PARAMETERS = {
    "area": "living_room",
    "destination": "bedroom",
    "source": "kitchen",
    "item": "medicine",
    "items": "cup,book",
    "checkpoint_id": "checkpoint-001",
    "goal": "not-an-accepted-goal",
    "target": "客厅",
    "reason_code": "DEMO_REJECTION",
    "task_id": "queued-task-001",
    "new_priority": 40,
    "queued_skill": "patrol_home",
    "target_skill": "patrol_home",
}
