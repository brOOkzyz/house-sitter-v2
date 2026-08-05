"""Offline deterministic adapter from short natural-language requests to skills."""

from __future__ import annotations

import re
from typing import Any

from .skill_planner import SkillPlanningError, compile_skill_plan, create_skill_request


MAX_TEXT_LENGTH = 500
REGION_ALIASES = {
    "living_room": ("客厅", "living room", "living_room"),
    "kitchen": ("厨房", "kitchen"),
    "bedroom": ("卧室", "bedroom"),
    "charging_area": ("充电区", "charging area", "charging_area"),
}
INTENT_PATTERNS = {
    "patrol_home": ("巡逻整个房子", "巡逻全屋", "patrol home", "patrol the whole house"),
    "check_all_rooms": ("检查所有房间", "检查全部房间", "check all rooms"),
    "go_to_safe_waiting_area": ("去安全等待区", "go to safe waiting area", "go to the safe waiting area"),
    "return_to_charger": ("返回充电区", "return to charging area", "go back to charging area"),
    "pause_current_task": ("暂停当前任务", "pause current task"),
    "resume_current_task": ("继续任务", "继续当前任务", "resume task", "resume current task"),
    "cancel_current_task": ("取消当前任务", "cancel current task"),
}
PHYSICAL_DEVICE_WORDS = (
    "打开", "关闭", "灯", "设备", "真实机器人", "实体机器人", "真机", "硬件", "真实设备", "实体设备",
    "物理机器人", "现实机器人", "dock", "undock", "cmd_vel", "real robot", "physical robot", "hardware", "physical device",
)


class NaturalLanguageAdapterError(ValueError):
    """Raised for invalid adapter input or an unavailable planner validation."""


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _result(
    original_text: str,
    normalized_text: str,
    capability: str | None,
    parameters: dict[str, Any],
    confidence: float,
    status: str,
    explanation: str,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "original_text": original_text,
        "normalized_text": normalized_text,
        "selected_capability": capability,
        "parameters": dict(sorted(parameters.items())),
        "confidence": confidence,
        "status": status,
        "explanation": explanation,
        "simulation_only": True,
        "review_only": True,
        "real_robot_supported": False,
        "executable": False,
    }
    if status == "accepted" and capability is not None:
        result["skill_request"] = create_skill_request(capability, parameters).as_dict()
    return result


def _matched_regions(normalized_text: str) -> tuple[str, ...]:
    matches = [
        label for label, aliases in REGION_ALIASES.items()
        if any(alias in normalized_text for alias in aliases)
    ]
    return tuple(matches)


def _matched_intents(normalized_text: str) -> tuple[str, ...]:
    matches = [
        capability for capability, phrases in INTENT_PATTERNS.items()
        if any(phrase in normalized_text for phrase in phrases)
    ]
    has_inspect = "检查" in normalized_text or re.search(r"\b(?:inspect|check)\b", normalized_text) is not None
    if has_inspect and "check_all_rooms" not in matches:
        matches.append("inspect_area")
    return tuple(matches)


def parse_skill_request(text: str) -> dict[str, Any]:
    """Parse a small, explicit bilingual vocabulary without a model or network."""
    if not isinstance(text, str):
        raise NaturalLanguageAdapterError("text must be a string.")
    if not text.strip():
        raise NaturalLanguageAdapterError("text must be non-empty.")
    if len(text) > MAX_TEXT_LENGTH:
        raise NaturalLanguageAdapterError(f"text must be at most {MAX_TEXT_LENGTH} characters.")
    normalized = _normalize(text)
    if any(word in normalized for word in PHYSICAL_DEVICE_WORDS):
        return _result(text, normalized, None, {}, 0.0, "unsupported_intent", "This project is simulation-only; physical devices and real-robot control are unsupported.")
    intents = _matched_intents(normalized)
    regions = _matched_regions(normalized)
    if len(intents) != 1:
        status = "needs_clarification" if intents else "unsupported_intent"
        explanation = "The request contains conflicting supported intents." if intents else "The request is outside the supported offline simulation vocabulary."
        return _result(text, normalized, None, {}, 0.0, status, explanation)
    capability = intents[0]
    if capability == "inspect_area":
        if len(regions) != 1:
            return _result(text, normalized, capability, {}, 0.0, "needs_clarification", "Specify exactly one supported region to inspect.")
        return _result(text, normalized, capability, {"area": regions[0]}, 1.0, "accepted", "Mapped the requested inspection to one canonical region.")
    if capability == "resume_current_task":
        checkpoint = re.search(r"(?:继续(?:当前)?任务|resume(?: current)? task)\s+([a-z0-9][a-z0-9_-]*)$", normalized)
        if checkpoint is None:
            return _result(text, normalized, capability, {}, 0.0, "needs_clarification", "resume_current_task requires a checkpoint_id.")
        return _result(text, normalized, capability, {"checkpoint_id": checkpoint.group(1)}, 1.0, "accepted", "Mapped the requested resume to its explicit checkpoint_id.")
    if regions and capability not in {"return_to_charger", "go_to_safe_waiting_area"}:
        return _result(text, normalized, capability, {}, 0.0, "needs_clarification", "The requested capability does not accept a region parameter.")
    return _result(text, normalized, capability, {}, 1.0, "accepted", "Mapped an explicit offline simulation request to an existing capability.")


def validate_with_planner(parsed: dict[str, Any], regions_document: dict[str, Any], goals_document: dict[str, Any]) -> dict[str, Any]:
    """Run existing planner validation only; never execute or expose a plan's goals."""
    if not isinstance(parsed, dict) or parsed.get("status") != "accepted":
        raise NaturalLanguageAdapterError("only an accepted adapter result can be planner-validated.")
    capability, parameters = parsed.get("selected_capability"), parsed.get("parameters")
    if not isinstance(capability, str) or not isinstance(parameters, dict):
        raise NaturalLanguageAdapterError("accepted adapter result is malformed.")
    try:
        request = create_skill_request(capability, parameters)
        plan = compile_skill_plan(request, regions_document, goals_document)
    except SkillPlanningError as exc:
        raise NaturalLanguageAdapterError(f"planner validation failed: {exc}") from exc
    return {
        "status": "accepted",
        "planning_status": plan["planning_status"],
        "step_count": len(plan["steps"]),
        "simulation_only": True,
        "real_robot_supported": False,
    }
