"""Deterministic, capability-grounded text planning for House-Sitter tasks."""
from __future__ import annotations

import re
from typing import Iterable

from .capability_registry import CapabilityRegistry
from .house2d import optimized_visit_order, visit_route_cost
from .models import PlanningResult, TaskSpec, TaskStep


ROOMS = ("living_room", "kitchen", "bedroom", "bathroom")
ROOM_ALIASES = {
    "living_room": ("living room", "lounge"),
    "kitchen": ("kitchen",),
    "bedroom": ("bedroom",),
    "bathroom": ("bathroom", "washroom"),
    "charging_area": ("charging area", "charger", "dock"),
}
UNSUPPORTED_ROOMS = ("garage", "office", "garden", "hallway", "hall", "study")
UNSUPPORTED_CAPABILITIES = ("camera", "arm", "open the door", "open door", "physical robot", "real robot")
SAFETY_BYPASSES = ("ignore the verifier", "bypass verifier", "ignore all previous instructions", "ignore previous instructions", "system prompt", "prompt injection", "disable safety", "turn off safety", "remove safety", "without stopping", "do not stop", "dont stop", "don t stop", "skip stop", "without return", "do not return", "dont return", "don t return", "dont come back", "don t come back", "do not come back", "never return", "skip return", "no timeout", "no time limit", "without a time limit", "infinite", "forever", "unlimited")
CODE_REQUESTS = ("python", "shell", "bash", "execute code", "run code", "arbitrary code")


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.casefold()))


def normalized_task(task: TaskSpec) -> dict[str, object]:
    """Stable semantic representation; display identifiers are intentionally excluded."""
    return {
        "robot_profile": task.robot_profile,
        "metadata": {key: value for key, value in sorted(task.metadata.items()) if key not in {"task_id", "timestamp", "display_name"}},
        "steps": [
            {"skill": step.skill, "parameters": dict(sorted(step.parameters.items())), "timeout_seconds": step.timeout_seconds, "on_failure": step.on_failure}
            for step in task.steps
        ],
    }


def semantically_equivalent(first: TaskSpec, second: TaskSpec) -> bool:
    return normalized_task(first) == normalized_task(second)


class OfflineHouseSitterPlanner:
    """Small grammar: it produces a task or a safe, explicit non-plan result."""

    name = "offline_house_sitter"
    version = "1.0"

    def __init__(self, registry: CapabilityRegistry):
        self.registry = registry

    def _step(self, step_id: str, skill: str, room: str | None = None) -> TaskStep:
        capability = self.registry.get(skill)
        assert capability is not None
        return TaskStep(step_id=step_id, skill=skill, parameters={"room": room} if room is not None else {}, timeout_seconds=capability.timeout_seconds, on_failure="stop" if skill == "stop" else "abort")

    @staticmethod
    def _has(text: str, phrases: Iterable[str]) -> bool:
        return any(phrase in text for phrase in phrases)

    def _rooms(self, text: str) -> tuple[list[str], bool, bool]:
        found: list[tuple[int, str]] = []
        for room, aliases in ROOM_ALIASES.items():
            positions = [text.find(alias) for alias in aliases if text.find(alias) >= 0]
            if positions:
                found.append((min(positions), room))
        all_rooms = self._has(text, ("all rooms", "every room", "each room", "whole house", "entire house", "the house"))
        selected = [room for _, room in sorted(found)]
        explicit_order = len(selected) > 1 and bool(re.search(r"\bthen\b", text))
        return (list(ROOMS) if all_rooms else selected), all_rooms, explicit_order

    @staticmethod
    def _checks(text: str) -> list[str]:
        checks: list[str] = []
        for label, phrases in (("environmental_changes", ("environmental change", "changes", "anomaly")), ("temperature", ("temperature", "heat")), ("humidity", ("humidity", "moisture")), ("obstacles", ("obstacle", "object"))):
            if any(phrase in text for phrase in phrases): checks.append(label)
        return checks

    def _tail(self, text: str, steps: list[TaskStep]) -> tuple[list[str], dict[str, str]]:
        additions: list[str] = []
        reasons: dict[str, str] = {}
        for skill, phrases in (("return_to_start", ("return", "charging", "dock")), ("stop", ("stop", "safely")), ("generate_monitoring_report", ("report",))):
            if not self._has(text, phrases):
                additions.append(skill)
                reasons[skill] = {"return_to_start": "required safe return for a patrol task", "stop": "required explicit bounded safe stop", "generate_monitoring_report": "required reproducible monitoring evidence"}[skill]
        steps.extend((self._step("return-to-charging-area", "return_to_start"), self._step("stop-safely", "stop"), self._step("generate-monitoring-report", "generate_monitoring_report")))
        return additions, reasons

    def _task(self, intent: str, rooms: list[str], text: str, checks: list[str], *, explicit_order: bool, route_cost: float) -> tuple[TaskSpec, list[str], dict[str, str]]:
        steps: list[TaskStep] = []
        index = 0

        def add(skill: str, room: str | None = None) -> None:
            nonlocal index
            index += 1; steps.append(self._step(f"{index:02d}-{skill.replace('_', '-')}{'-' + room if room else ''}", skill, room))

        for room in rooms:
            add("move_to_room", room); add("inspect_room", room)
            if intent == "establish_baseline":
                add("establish_household_baseline", room)
            elif room != "charging_area":
                # House2D supplies a fixed simulation reference; detection still
                # consumes only the inspection above, never scenario text.
                add("detect_environment_change", room); add("update_digital_twin", room)
                alert = self._step(f"{index + 1:02d}-generate-alert-{room}", "generate_alert", room)
                alert.parameters["anomaly_type"] = "detected_anomaly"; index += 1; steps.append(alert)
        additions, reasons = self._tail(text, steps)
        return TaskSpec(task_id="nl-house-sitter-v1", name=f"Constrained House-Sitter {intent.replace('_', ' ')}", description="Deterministic simulation-only task created from constrained natural language.", robot_profile="create3_sim", steps=steps, metadata={"simulation_only": True, "physical_robot_supported": False, "planner_name": self.name, "planner_version": self.version, "checks": checks, "baseline_mode": "record_current" if intent == "establish_baseline" else "normal_reference", "visit_order_source": "explicit_user_order" if explicit_order else "legal_path_optimization", "optimized_visit_order": rooms, "planned_route_cost": route_cost}), additions, reasons

    def plan(self, original_text: str) -> PlanningResult:
        text = normalize_text(original_text)
        if not text:
            return PlanningResult(original_text=original_text, normalized_text=text, status="invalid", warnings=["The request is empty."], clarification_questions=["Describe a supported House-Sitter task."])
        blocked = [phrase for phrase in (*SAFETY_BYPASSES, *CODE_REQUESTS) if phrase in text]
        if blocked:
            return PlanningResult(original_text=original_text, normalized_text=text, status="unsupported", unsupported_elements=blocked, warnings=["Safety boundaries and code execution cannot be changed by user text."], match_basis=["safety policy block"])
        unsupported = [item for item in (*UNSUPPORTED_ROOMS, *UNSUPPORTED_CAPABILITIES) if item in text]
        if unsupported:
            return PlanningResult(original_text=original_text, normalized_text=text, status="unsupported", unsupported_elements=unsupported, warnings=["The request needs a room or capability not declared by this simulation profile."], match_basis=["unsupported capability or room"])
        rooms, all_rooms, explicit_order = self._rooms(text)
        if "only" in text and all_rooms:
            return PlanningResult(original_text=original_text, normalized_text=text, status="needs_clarification", clarification_questions=["Should the patrol visit every room or only the named rooms?"], warnings=["The room scope is conflicting."], match_basis=["conflicting room scope"])
        checks = self._checks(text)
        full_words = ("complete", "full", "run house sitter", "house sitter" )
        if self._has(text, full_words) and self._has(text, ("patrol", "monitor", "environmental", "report")):
            intent = "complete_house_sitter"; rooms = rooms or list(ROOMS)
            if "environmental_changes" not in checks: checks.append("environmental_changes")
        elif self._has(text, ("detect", "environmental change", "anomaly")):
            if not rooms:
                return PlanningResult(original_text=original_text, normalized_text=text, status="needs_clarification", detected_intent="detect_environment_changes", extracted_checks=checks, clarification_questions=["Which room should be revisited for change detection?"], match_basis=["detection intent without room"])
            intent = "detect_environment_changes"
        elif self._has(text, ("baseline",)):
            if not self._has(text, ("establish", "record")):
                return PlanningResult(original_text=original_text, normalized_text=text, status="needs_clarification", clarification_questions=["Say 'establish' or 'record' to create a household baseline."], warnings=["Ordinary patrols use the fixed normal reference baseline."], match_basis=["baseline request is not explicit"])
            intent = "establish_baseline"; rooms = rooms or list(ROOMS)
        elif self._has(text, ("patrol", "monitor")):
            intent = "patrol"; rooms = rooms or list(ROOMS)
        elif self._has(text, ("inspect", "check", "look")):
            if not rooms:
                return PlanningResult(original_text=original_text, normalized_text=text, status="needs_clarification", detected_intent="inspect", extracted_checks=checks, clarification_questions=["Which supported room should be inspected?"], match_basis=["inspection intent without room"])
            intent = "inspect"
        elif self._has(text, ("return", "charging", "dock")):
            intent = "return_to_charging_area"; rooms = []
        else:
            return PlanningResult(original_text=original_text, normalized_text=text, status="unsupported", unsupported_elements=["unmapped request"], warnings=["The request does not map to a declared House-Sitter capability."], match_basis=["no supported intent"])
        if intent in {"complete_house_sitter", "detect_environment_changes"} and "charging_area" in rooms:
            return PlanningResult(original_text=original_text, normalized_text=text, status="needs_clarification", detected_intent=intent, extracted_rooms=rooms, extracted_checks=checks, clarification_questions=["The charging area is a safe return location, not an active-event monitoring room; which household room should be revisited?"], match_basis=["unsupported event-revisit room"])
        requested_rooms = list(rooms)
        route_cost = 0.0
        if "charging_area" not in rooms:
            if explicit_order:
                route_cost = round(visit_route_cost(rooms), 4)
            else:
                rooms, route_cost = optimized_visit_order(rooms)
        task, additions, reasons = self._task(intent, rooms, text, checks, explicit_order=explicit_order, route_cost=route_cost)
        return PlanningResult(original_text=original_text, normalized_text=text, detected_intent=intent, extracted_rooms=requested_rooms, extracted_checks=checks, candidate_task=task, confidence=1.0, match_basis=["deterministic keyword and room grammar"], automatically_added_steps=additions, automatic_addition_reasons=reasons, status="planned")
