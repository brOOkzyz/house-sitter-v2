"""Controlled engineering-corpus metrics for the deterministic planner."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .capability_registry import CapabilityRegistry
from .planner import OfflineHouseSitterPlanner, semantically_equivalent
from .verifier import verify_task


def evaluate_corpus(path: Path, registry: CapabilityRegistry) -> dict[str, Any]:
    corpus = json.loads(path.read_text(encoding="utf-8"))["examples"]
    planner = OfflineHouseSitterPlanner(registry)
    rows: list[dict[str, Any]] = []
    groups: dict[str, list[Any]] = defaultdict(list)
    for item in corpus:
        result = planner.plan(item["text"])
        verified = bool(result.candidate_task and verify_task(result.candidate_task, registry).approved)
        required = {step.skill for step in result.candidate_task.steps} if result.candidate_task else set()
        status_ok = result.status == item["expected_status"]
        intent_ok = result.detected_intent == item["expected_intent"]
        rooms_ok = result.extracted_rooms == item["expected_rooms"]
        skills_ok = set(item["expected_required_skills"]).issubset(required)
        rows.append({"example_id": item["example_id"], "status": result.status, "status_ok": status_ok, "intent_ok": intent_ok, "rooms_ok": rooms_ok, "skills_ok": skills_ok, "verified": verified})
        if item.get("paraphrase_group") and result.candidate_task is not None: groups[item["paraphrase_group"]].append(result.candidate_task)
    total = len(rows)
    planned = [row for row in rows if row["status"] == "planned"]
    unsafe = [(item, row) for item, row in zip(corpus, rows) if item.get("unsafe")]
    consistent = [all(semantically_equivalent(tasks[0], task) for task in tasks[1:]) for tasks in groups.values() if tasks]
    ratio = lambda numerator, denominator: numerator / denominator if denominator else 0.0
    return {"evaluation_label": "preliminary controlled corpus evaluation", "total_examples": total, "planned": len(planned), "needs_clarification": sum(row["status"] == "needs_clarification" for row in rows), "unsupported": sum(row["status"] == "unsupported" for row in rows), "invalid": sum(row["status"] == "invalid" for row in rows), "expected_status_accuracy": ratio(sum(row["status_ok"] for row in rows), total), "intent_accuracy": ratio(sum(row["intent_ok"] for row in rows), total), "room_extraction_accuracy": ratio(sum(row["rooms_ok"] for row in rows), total), "valid_plan_rate": ratio(sum(row["status"] == "planned" and row["verified"] for row in rows), total), "verifier_approval_rate": ratio(sum(row["verified"] for row in planned), len(planned)), "unsafe_request_blocking_rate": ratio(sum(row["status"] == "unsupported" for _, row in unsafe), len(unsafe)), "paraphrase_semantic_consistency": ratio(sum(consistent), len(consistent)), "rows": rows}


def evaluation_markdown(metrics: dict[str, Any]) -> str:
    labels = ("total_examples", "planned", "needs_clarification", "unsupported", "invalid", "expected_status_accuracy", "intent_accuracy", "room_extraction_accuracy", "valid_plan_rate", "verifier_approval_rate", "unsafe_request_blocking_rate", "paraphrase_semantic_consistency")
    return "# Phase 4 Preliminary Controlled Corpus Evaluation\n\nThis is an engineering corpus, not a user study or a final research result.\n\n" + "\n".join(f"- {label}: {metrics[label]}" for label in labels) + "\n"
