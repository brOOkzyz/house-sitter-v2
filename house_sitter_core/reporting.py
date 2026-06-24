"""Small reporting helpers for dry-run and future execution records."""

from typing import Any, Dict, List


def build_task_report(task_name: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "task_name": task_name,
        "mode": "dry_run",
        "step_count": len(records),
        "completed": all(record["status"] == "dry_run_only" for record in records),
    }

