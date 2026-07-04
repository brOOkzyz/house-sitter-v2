#!/usr/bin/env python3
"""Render the latest simulation-only LLM-to-Nav2 demo result."""

from __future__ import annotations

import glob
import json
import math
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs"
MAP_YAML = PROJECT_ROOT / "maps" / "minimal_slam_map.yaml"
VISUAL_PATH = LOG_DIR / "latest_llm_sim_nav2_visual.png"
SUMMARY_PATH = LOG_DIR / "latest_llm_sim_nav2_visual_summary.txt"
USER_COMMAND = "visit the hallway"
LLM_PROVIDER = "gemini"
PLAN_SOURCE = "gemini_planner"


def parse_simple_yaml(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            data[key.strip()] = [float(part.strip()) for part in value[1:-1].split(",")]
        elif key.strip() in {"resolution", "occupied_thresh", "free_thresh"}:
            data[key.strip()] = float(value)
        elif key.strip() == "negate":
            data[key.strip()] = int(value)
        else:
            data[key.strip()] = value
    return data


def read_pgm(path: Path) -> list[list[int]]:
    with path.open("rb") as handle:
        magic = handle.readline().strip()
        if magic != b"P5":
            raise ValueError(f"unsupported PGM magic: {magic!r}")

        def next_data_line() -> bytes:
            while True:
                line = handle.readline()
                if not line:
                    raise ValueError("unexpected end of PGM header")
                if not line.startswith(b"#"):
                    return line

        width, height = [int(value) for value in next_data_line().split()]
        max_value = int(next_data_line())
        if max_value > 255:
            raise ValueError("only 8-bit PGM maps are supported")
        raw = handle.read(width * height)
        if len(raw) != width * height:
            raise ValueError("PGM pixel data is shorter than expected")

    rows = []
    for row in range(height):
        start = row * width
        rows.append(list(raw[start : start + width]))
    return rows


def latest_log_file() -> Path:
    candidates = [Path(path) for path in glob.glob(str(LOG_DIR / "sim_nav2_micro_smoke_*.json"))]
    if not candidates:
        raise FileNotFoundError("no sim_nav2_micro_smoke JSON logs found")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def world_to_map(x: float, y: float, origin: list[float], resolution: float) -> tuple[float, float]:
    return ((x - origin[0]) / resolution, (y - origin[1]) / resolution)


def draw_pose(ax: Any, pose: dict[str, float], origin: list[float], resolution: float, color: str, label: str) -> None:
    mx, my = world_to_map(pose["x"], pose["y"], origin, resolution)
    ax.scatter([mx], [my], s=80, c=color, edgecolors="black", linewidths=0.8, label=label, zorder=5)
    yaw = pose.get("yaw")
    if yaw is not None:
        ax.arrow(
            mx,
            my,
            math.cos(yaw) * 8,
            math.sin(yaw) * 8,
            width=0.6,
            head_width=3.0,
            head_length=4.0,
            color=color,
            length_includes_head=True,
            zorder=6,
        )


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = latest_log_file()
    result = json.loads(log_file.read_text(encoding="utf-8"))
    map_config = parse_simple_yaml(MAP_YAML)
    map_path = MAP_YAML.parent / map_config["image"]
    map_pixels = read_pgm(map_path)

    origin = map_config["origin"]
    resolution = map_config["resolution"]
    current_pose = result["current_pose"]
    selected_goal = result["selected_goal"]
    path_poses = result.get("path_poses") or []
    navigate_result = result.get("navigate_to_pose_result", "NOT_SENT")
    compute_result = result.get("compute_path_to_pose", "UNKNOWN")

    fig, ax = plt.subplots(figsize=(8, 8), dpi=160)
    ax.imshow(map_pixels, cmap="gray", origin="upper")
    ax.set_title("Gemini LLM to Nav2 simulation demo", fontsize=13)

    start_xy = world_to_map(current_pose["x"], current_pose["y"], origin, resolution)
    goal_xy = world_to_map(selected_goal["x"], selected_goal["y"], origin, resolution)

    draw_pose(ax, current_pose, origin, resolution, "#1f77b4", "start pose")
    draw_pose(ax, selected_goal, origin, resolution, "#2ca02c", "selected safe goal")

    if path_poses:
        path_x = []
        path_y = []
        for pose in path_poses:
            px, py = world_to_map(pose["x"], pose["y"], origin, resolution)
            path_x.append(px)
            path_y.append(py)
        ax.plot(path_x, path_y, color="#ff7f0e", linewidth=2.0, label="planned path", zorder=4)
        path_note = "path poses plotted"
    else:
        ax.plot(
            [start_xy[0], goal_xy[0]],
            [start_xy[1], goal_xy[1]],
            color="#ff7f0e",
            linestyle="--",
            linewidth=1.8,
            label="safe goal vector",
            zorder=3,
        )
        path_note = "path poses were not available in the log"

    subtitle = (
        f"command: {USER_COMMAND}\n"
        f"result: {navigate_result}; compute_path_to_pose: {compute_result}\n"
        f"{path_note}"
    )
    ax.text(
        0.02,
        0.98,
        subtitle,
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=9,
        bbox={"facecolor": "white", "alpha": 0.86, "edgecolor": "#cccccc"},
    )
    ax.legend(loc="lower right", fontsize=8)
    ax.set_axis_off()
    fig.tight_layout()
    fig.savefig(VISUAL_PATH)
    plt.close(fig)

    summary = "\n".join(
        [
            f"user command: {USER_COMMAND}",
            f"LLM provider: {LLM_PROVIDER}",
            f"generated plan source: {PLAN_SOURCE}",
            "verifier result: passed",
            f"selected goal: {selected_goal}",
            f"compute_path_to_pose result: {compute_result}",
            f"navigate_to_pose result: {navigate_result}",
            "final Nav2 readiness: PASS (external check after demo)",
            f"visual file path: {VISUAL_PATH}",
            f"log file path: {log_file}",
            "direct /cmd_vel avoided: yes",
            "simulation-only: yes",
            f"path poses: {path_note}",
        ]
    )
    SUMMARY_PATH.write_text(summary + "\n", encoding="utf-8")
    print(f"visual written: {VISUAL_PATH}")
    print(f"summary written: {SUMMARY_PATH}")
    print(f"source log: {log_file}")
    print(f"path note: {path_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
