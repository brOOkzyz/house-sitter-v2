"""Deterministic patrol-order policies over the committed house_v1 A* map."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Reuse the exact conservative occupancy and A* implementation that powers the
# existing two-dimensional visual demonstration.  This module deliberately does
# not introduce a second route planner.
from scripts.run_house_v1_visual_demo import _goal_cell, astar_path, load_house_v1_inputs


PATROL_ROOMS = ("living_room", "kitchen", "bedroom", "bathroom")
CHARGING_ROOM = "charging_area"
FIXED_ORDER = PATROL_ROOMS + (CHARGING_ROOM,)
STRATEGIES = ("fixed_order", "risk_priority", "battery_aware")


class PatrolStrategyError(ValueError):
    """Raised when a patrol policy cannot be evaluated safely."""


@dataclass(frozen=True)
class EnergyModel:
    energy_model_id: str
    travel_energy_per_meter: float
    sensing_energy_per_room: float
    fixed_task_overhead: float


@dataclass(frozen=True)
class PatrolMap:
    """Distances and cell traces derived only from accepted safe goals."""

    distances_m: dict[tuple[str, str], float]
    route_cells: dict[tuple[str, str], tuple[tuple[int, int], ...]]

    def distance_m(self, start_room: str, goal_room: str) -> float:
        return self.distances_m[(start_room, goal_room)]

    def cells(self, start_room: str, goal_room: str) -> tuple[tuple[int, int], ...]:
        return self.route_cells[(start_room, goal_room)]


def load_patrol_map(root: Path) -> PatrolMap:
    """Measure every accepted-safe-goal pair with the existing A* routine."""
    inputs = load_house_v1_inputs(Path(root))
    labels = PATROL_ROOMS + (CHARGING_ROOM,)
    distances: dict[tuple[str, str], float] = {}
    cells: dict[tuple[str, str], tuple[tuple[int, int], ...]] = {}
    for start in labels:
        for goal in labels:
            path = astar_path(inputs.inflated_free_cells, _goal_cell(inputs, start), _goal_cell(inputs, goal))
            distance = sum(math.hypot(next_row - row, next_column - column)
                           for (row, column), (next_row, next_column) in zip(path, path[1:])) * inputs.metadata.resolution
            distances[(start, goal)] = round(distance, 6)
            cells[(start, goal)] = path
    return PatrolMap(distances, cells)


def energy_for_distance(distance_m: float, model: EnergyModel) -> float:
    return distance_m * model.travel_energy_per_meter


def required_visit_energy(
    current_room: str,
    candidate_room: str,
    patrol_map: PatrolMap,
    model: EnergyModel,
    safety_reserve: float,
) -> float:
    """Energy needed to observe a candidate and still guarantee a return."""
    return (energy_for_distance(patrol_map.distance_m(current_room, candidate_room), model)
            + model.sensing_energy_per_room
            + energy_for_distance(patrol_map.distance_m(candidate_room, CHARGING_ROOM), model)
            + safety_reserve)


def fixed_order_rooms() -> tuple[str, ...]:
    """The formal baseline order, excluding only the final charging return."""
    return PATROL_ROOMS


def risk_priority_rooms(
    current_room: str,
    room_risk_scores: dict[str, float],
    patrol_map: PatrolMap,
) -> tuple[str, ...]:
    """Rank predeclared risk only; no scenario ground truth is accepted here."""
    _validate_risk_scores(room_risk_scores)
    return tuple(sorted(
        PATROL_ROOMS,
        key=lambda room: (-room_risk_scores[room], patrol_map.distance_m(current_room, room), room),
    ))


def choose_battery_aware_room(
    current_room: str,
    remaining_battery: float,
    unvisited_rooms: tuple[str, ...],
    room_risk_scores: dict[str, float],
    patrol_map: PatrolMap,
    model: EnergyModel,
    safety_reserve: float,
) -> str | None:
    """Choose the highest-risk currently feasible room, otherwise return now."""
    _validate_risk_scores(room_risk_scores)
    feasible = [
        room for room in unvisited_rooms
        if remaining_battery + 1e-9 >= required_visit_energy(current_room, room, patrol_map, model, safety_reserve)
    ]
    if not feasible:
        return None
    return min(feasible, key=lambda room: (-room_risk_scores[room], patrol_map.distance_m(current_room, room), room))


def _validate_risk_scores(scores: dict[str, float]) -> None:
    if set(scores) != set(PATROL_ROOMS):
        raise PatrolStrategyError("risk profile 必须为四个可巡逻房间分别给出分数。")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in scores.values()):
        raise PatrolStrategyError("risk profile 分数必须是数值。")
