"""Minimal JSON-compatible task-plan schema used by the v2 prototype."""

from typing import Any, Dict, List, TypedDict


SCHEMA_VERSION = "1.0"


class ActionStep(TypedDict):
    action: str
    parameters: Dict[str, Any]


class TaskPlan(TypedDict):
    schema_version: str
    task_name: str
    source: str
    steps: List[ActionStep]


def make_plan(task_name: str, source: str, steps: List[ActionStep]) -> TaskPlan:
    return {
        "schema_version": SCHEMA_VERSION,
        "task_name": task_name,
        "source": source,
        "steps": steps,
    }

