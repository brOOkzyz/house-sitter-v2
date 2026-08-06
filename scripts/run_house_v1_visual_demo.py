#!/usr/bin/env python3
"""Offline, deterministic 2D visual demonstration for the house_v1 assets.

This program intentionally has no ROS, Gazebo, Nav2, or RViz dependency.  It
reuses the repository's natural-language pipeline for parsing and planning,
then visualizes its planner-approved safe goals on the local occupancy map.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np

# Some minimal CI images do not install a CJK font.  The source text remains
# UTF-8; suppress only the harmless raster-font warning for headless exports.
warnings.filterwarnings("ignore", message=r"Glyph .* missing from current font", category=UserWarning)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.map_metadata import RosMapMetadata, load_ros_map  # noqa: E402
from house_sitter_core.natural_language_pipeline import run_natural_language_pipeline  # noqa: E402


TITLE = "2D deterministic residential simulation visualization"
STATIC_3D_TITLE = "Static 3D residential preview — no robot execution"
PATROL_ORDER = ("living_room", "kitchen", "bedroom", "bathroom", "charging_area")
ROBOT_RADIUS_METERS = 0.22
SAFETY_MARGIN_METERS = 0.10
STATIC_PREVIEW_SCRIPT = ROOT / "scripts" / "preview_house_v1_3d.sh"


class VisualDemoError(ValueError):
    """A concise, user-facing failure in the offline visualization."""


def static_preview_ready(root: Path = ROOT, *, find_command: Any = shutil.which) -> tuple[bool, str]:
    """Check only the local GUI prerequisite; it never probes or starts ROS."""
    if find_command("gz") is None:
        return False, "未找到 gz，跳过三维静态住宅预览。"
    if not (root / "worlds" / "house_v1.sdf").is_file():
        return False, "未找到 worlds/house_v1.sdf，跳过三维静态住宅预览。"
    if not (root / "scripts" / "preview_house_v1_3d.sh").is_file():
        return False, "未找到三维静态预览脚本，跳过预览。"
    return True, ""


def _stop_static_preview(process: subprocess.Popen[Any]) -> None:
    """Stop only the process group created by this demonstration."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass


def launch_static_preview(
    *,
    root: Path = ROOT,
    popen: Any = subprocess.Popen,
    sleep: Any = time.sleep,
) -> bool:
    """Launch one independent static GUI and wait for the user to close it.

    Its own process group is used solely so Ctrl+C can clean up this child; no
    external Gazebo or ROS process is inspected, stopped, or otherwise touched.
    """
    ready, message = static_preview_ready(root)
    if not ready:
        print(message)
        return False
    log_file = tempfile.NamedTemporaryFile(prefix="house-v1-static-preview-", suffix=".log", delete=False)
    log_path = Path(log_file.name)
    try:
        process = popen(
            ["bash", str(root / "scripts" / "preview_house_v1_3d.sh")], cwd=root,
            stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, start_new_session=True,
        )
    except OSError as exc:
        log_file.close()
        print(f"三维静态住宅预览无法启动：{exc}；继续二维动态演示。")
        return False
    finally:
        if not log_file.closed:
            log_file.close()
    print(STATIC_3D_TITLE)
    print("已打开独立窗口；关闭该窗口后将继续二维动态演示。")
    try:
        while process.poll() is None:
            sleep(0.2)
    except KeyboardInterrupt:
        _stop_static_preview(process)
        raise
    exit_code = process.returncode
    if exit_code not in {0, None}:
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-6:]
        except OSError:
            tail = []
        suffix = f" 日志摘要：{' | '.join(tail)}" if tail else ""
        print(f"三维静态住宅预览退出（code {exit_code}），继续二维动态演示。{suffix}")
        return False
    return True


def optional_static_preview(*, non_interactive: bool, input_fn: Any = input) -> bool:
    """Offer the optional first presentation step without affecting GIF exports."""
    if non_interactive:
        return True
    print("步骤 0：三维住宅静态预览（可选）")
    print("该窗口仅展示 house_v1 的三维住宅布局，不运行机器人、控制器或 Nav2。")
    try:
        choice = input_fn("[Enter] 打开三维静态住宅  [s] 跳过，直接进入二维动态演示  [q] 退出：").strip().casefold()
    except EOFError:
        choice = "s"
    if choice == "q":
        return False
    if choice == "s":
        return True
    launch_static_preview()
    return True


@dataclass(frozen=True)
class HouseInputs:
    metadata: RosMapMetadata
    regions_document: dict[str, Any]
    goals_document: dict[str, Any]
    free_cells: np.ndarray
    inflated_free_cells: np.ndarray
    regions: dict[str, dict[str, Any]]
    goals: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class VisualDemo:
    text: str
    request: dict[str, Any]
    parsed: dict[str, Any]
    planner_plan: dict[str, Any]
    pipeline_result: dict[str, Any]
    target_labels: tuple[str, ...]
    route_cells: tuple[tuple[int, int], ...]
    route_points: tuple[tuple[float, float], ...]
    accepted: bool


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VisualDemoError(f"无法读取正式住宅标注 {path.name}：{exc}") from exc
    if not isinstance(value, dict):
        raise VisualDemoError(f"正式住宅标注 {path.name} 必须是 JSON 对象。")
    return value


def _inflate_obstacles(free_cells: np.ndarray, radius_cells: int) -> np.ndarray:
    """Conservatively inflate obstacles without scipy or any runtime service."""
    occupied = ~free_cells
    expanded = occupied.copy()
    height, width = occupied.shape
    for row_delta in range(-radius_cells, radius_cells + 1):
        for column_delta in range(-radius_cells, radius_cells + 1):
            if row_delta * row_delta + column_delta * column_delta > radius_cells * radius_cells:
                continue
            source_rows = slice(max(0, -row_delta), min(height, height - row_delta))
            source_columns = slice(max(0, -column_delta), min(width, width - column_delta))
            target_rows = slice(max(0, row_delta), min(height, height + row_delta))
            target_columns = slice(max(0, column_delta), min(width, width + column_delta))
            expanded[target_rows, target_columns] |= occupied[source_rows, source_columns]
    return ~expanded


def load_house_v1_inputs(root: Path = ROOT) -> HouseInputs:
    """Load only committed local house_v1 map and approved semantic artifacts."""
    metadata = load_ros_map(root / "maps" / "house_v1.yaml")
    regions_document = _read_json(root / "local_annotations" / "house_v1" / "semantic_regions.json")
    goals_document = _read_json(root / "local_annotations" / "house_v1" / "safe_goals.json")
    regions = {item["canonical_label"]: item for item in regions_document.get("regions", []) if isinstance(item, dict)}
    goals = {
        item["canonical_label"]: item for item in goals_document.get("goals", [])
        if isinstance(item, dict) and item.get("status") == "accepted"
    }
    if set(regions) != {"living_room", "kitchen", "bedroom", "bathroom", "hallway", "charging_area"}:
        raise VisualDemoError("house_v1 正式语义区域不完整。")
    if set(goals) != set(regions):
        raise VisualDemoError("house_v1 accepted safe goals 不完整。")
    raw = np.frombuffer(metadata.image.pixels, dtype=np.uint8).reshape(metadata.image.height, metadata.image.width)
    free_cells = raw >= 250
    inflation = math.ceil((ROBOT_RADIUS_METERS + SAFETY_MARGIN_METERS) / metadata.resolution)
    return HouseInputs(metadata, regions_document, goals_document, free_cells, _inflate_obstacles(free_cells, inflation), regions, goals)


def _goal_cell(inputs: HouseInputs, label: str) -> tuple[int, int]:
    goal = inputs.goals[label]["goal"]
    return int(goal["pixel_row"]), int(goal["pixel_column"])


def _cell_point(inputs: HouseInputs, cell: tuple[int, int]) -> tuple[float, float]:
    row, column = cell
    origin_x, origin_y, _ = inputs.metadata.origin
    return (origin_x + (column + 0.5) * inputs.metadata.resolution,
            origin_y + (inputs.metadata.image.height - row - 0.5) * inputs.metadata.resolution)


def _nearest_free(free: np.ndarray, cell: tuple[int, int]) -> tuple[int, int]:
    row, column = cell
    if 0 <= row < free.shape[0] and 0 <= column < free.shape[1] and free[row, column]:
        return cell
    for radius in range(1, 31):
        candidates = []
        for r in range(max(0, row - radius), min(free.shape[0], row + radius + 1)):
            for c in range(max(0, column - radius), min(free.shape[1], column + radius + 1)):
                if free[r, c]:
                    candidates.append((abs(r - row) + abs(c - column), r, c))
        if candidates:
            _, result_row, result_column = min(candidates)
            return result_row, result_column
    raise VisualDemoError("安全膨胀后没有可用的住宅起点或目标点。")


def astar_path(free: np.ndarray, start: tuple[int, int], goal: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    """Deterministic 8-connected A* that disallows diagonal corner cutting."""
    start, goal = _nearest_free(free, start), _nearest_free(free, goal)
    if start == goal:
        return (start,)
    neighbours = ((-1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0), (1, 0, 1.0),
                  (-1, -1, math.sqrt(2)), (-1, 1, math.sqrt(2)), (1, -1, math.sqrt(2)), (1, 1, math.sqrt(2)))
    def heuristic(cell: tuple[int, int]) -> float:
        dr, dc = abs(cell[0] - goal[0]), abs(cell[1] - goal[1])
        return max(dr, dc) + (math.sqrt(2) - 1.0) * min(dr, dc)
    queue: list[tuple[float, float, int, int]] = [(heuristic(start), 0.0, start[0], start[1])]
    cost = {start: 0.0}
    parent: dict[tuple[int, int], tuple[int, int]] = {}
    while queue:
        _, current_cost, row, column = heapq.heappop(queue)
        current = (row, column)
        if current_cost != cost.get(current):
            continue
        if current == goal:
            result = [current]
            while result[-1] != start:
                result.append(parent[result[-1]])
            return tuple(reversed(result))
        for dr, dc, step_cost in neighbours:
            nxt = (row + dr, column + dc)
            if not (0 <= nxt[0] < free.shape[0] and 0 <= nxt[1] < free.shape[1]) or not free[nxt]:
                continue
            if dr and dc and (not free[row + dr, column] or not free[row, column + dc]):
                continue
            candidate = current_cost + step_cost
            if candidate < cost.get(nxt, math.inf):
                cost[nxt], parent[nxt] = candidate, current
                heapq.heappush(queue, (candidate + heuristic(nxt), candidate, nxt[0], nxt[1]))
    raise VisualDemoError("保守膨胀后的自由空间无法连接所选 accepted safe goal。")


def _navigation_labels(plan: dict[str, Any]) -> tuple[str, ...]:
    labels = []
    for step in plan.get("steps", []):
        reference = step.get("goal_reference") if isinstance(step, dict) else None
        if isinstance(reference, dict) and isinstance(reference.get("canonical_label"), str):
            labels.append(reference["canonical_label"])
    return tuple(labels)


def build_visual_demo(text: str, inputs: HouseInputs | None = None) -> VisualDemo:
    """Run the existing offline parser/planner then create a local A* visual route."""
    inputs = inputs or load_house_v1_inputs()
    try:
        request, parsed, planner_plan, pipeline_result = run_natural_language_pipeline(text, inputs.regions_document, inputs.goals_document)
    except Exception as exc:  # pipeline exceptions are translated for the presentation, never printed as tracebacks
        raise VisualDemoError(f"自然语言规划无法完成：{exc}") from exc
    accepted = parsed.get("status") == "accepted" and planner_plan.get("planning_status") == "ready"
    if not accepted:
        return VisualDemo(text, request, parsed, planner_plan, pipeline_result, (), (), (), False)
    planner_labels = _navigation_labels(planner_plan)
    labels = PATROL_ORDER if parsed.get("selected_capability") == "patrol_home" else planner_labels
    if not labels or any(label not in inputs.goals for label in labels):
        raise VisualDemoError("planner 未提供可视化所需的 accepted safe goal。")
    current = _nearest_free(inputs.inflated_free_cells, _goal_cell(inputs, "charging_area"))
    all_cells: list[tuple[int, int]] = [current]
    for label in labels:
        target = _nearest_free(inputs.inflated_free_cells, _goal_cell(inputs, label))
        segment = astar_path(inputs.inflated_free_cells, current, target)
        all_cells.extend(segment[1:])
        current = target
    return VisualDemo(text, request, parsed, planner_plan, pipeline_result, labels, tuple(all_cells),
                      tuple(_cell_point(inputs, cell) for cell in all_cells), True)


def trajectory_points(demo: VisualDemo, spacing_m: float = 0.07) -> tuple[tuple[float, float], ...]:
    if not demo.route_points:
        return ()
    result = [demo.route_points[0]]
    carry = 0.0
    for first, second in zip(demo.route_points, demo.route_points[1:]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        while carry + length >= spacing_m and length > 0:
            distance = spacing_m - carry
            ratio = distance / length
            first = (first[0] + ratio * dx, first[1] + ratio * dy)
            result.append(first)
            dx, dy = second[0] - first[0], second[1] - first[1]
            length = math.hypot(dx, dy)
            carry = 0.0
        carry += length
    if result[-1] != demo.route_points[-1]:
        result.append(demo.route_points[-1])
    return tuple(result)


def _visual_plan_document(demo: VisualDemo, inputs: HouseInputs) -> dict[str, Any]:
    route_source = "planner_approved_safe_goals"
    if demo.parsed.get("selected_capability") == "patrol_home":
        route_source = "house_v1_formal_visualization_configuration"
    return {
        "visualization_type": TITLE, "planner_plan": demo.planner_plan,
        "target_areas": list(demo.target_labels), "route_source": route_source,
        "accepted_safe_goals": [{"canonical_label": label, "proposal_id": inputs.goals[label]["proposal_id"]} for label in demo.target_labels],
        "robot_radius_m": ROBOT_RADIUS_METERS, "safety_margin_m": SAFETY_MARGIN_METERS,
        "path_cell_count": len(demo.route_cells), "simulation_only": True, "real_robot_supported": False,
    }


def _result_document(demo: VisualDemo, frames: Iterable[tuple[float, float]]) -> dict[str, Any]:
    frame_list = list(frames)
    return {
        "original_text": demo.text, "selected_capability": demo.parsed.get("selected_capability"),
        "parse_status": demo.parsed.get("status"), "planner_status": demo.planner_plan.get("planning_status"),
        "target_areas": list(demo.target_labels), "execution_mode": "2d_deterministic_visualization",
        "action_goals_sent": 0, "trajectory_frame_count": len(frame_list),
        "final_status": "succeeded" if demo.accepted else demo.pipeline_result.get("final_status"),
        "simulation_only": True, "real_robot_supported": False,
        "gazebo_nav2_execution": False,
    }


def _report(demo: VisualDemo, result: dict[str, Any]) -> str:
    return "\n".join((
        "# house_v1 visual demonstration", "", f"**{TITLE}**", "",
        "This is an offline deterministic residential visualization, not a Gazebo/Nav2 execution artifact.",
        "The warehouse remains the project’s previously validated Gazebo/Nav2 execution environment.", "",
        f"- Request: `{demo.text}`", f"- Parse status: `{result['parse_status']}`",
        f"- Planner status: `{result['planner_status']}`", f"- Target areas: `{', '.join(demo.target_labels) or 'none'}`",
        f"- Final visualization status: `{result['final_status']}`", "- action_goals_sent: `0`",
        "- simulation_only: `true`", "- real_robot_supported: `false`", "",
    ))


class HouseRenderer:
    def __init__(self, inputs: HouseInputs, demo: VisualDemo):
        import matplotlib.pyplot as plt
        from matplotlib.patches import Circle, Polygon
        self.plt, self.inputs, self.demo = plt, inputs, demo
        self.plt.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "DejaVu Sans"]
        self.fig, self.ax = plt.subplots(figsize=(13, 8))
        self.fig.subplots_adjust(right=0.70)
        raw = np.frombuffer(inputs.metadata.image.pixels, dtype=np.uint8).reshape(inputs.metadata.image.height, inputs.metadata.image.width)
        bounds = inputs.metadata.bounds
        self.ax.imshow(np.flipud(raw), cmap="gray", origin="lower", extent=(bounds[0], bounds[2], bounds[1], bounds[3]), vmin=0, vmax=255)
        colors = {"living_room": "#5dade2", "kitchen": "#f5b041", "bedroom": "#af7ac5", "bathroom": "#48c9b0", "hallway": "#f4d03f", "charging_area": "#58d68d"}
        for label, region in inputs.regions.items():
            vertices = region["polygon"]["vertices"]
            alpha = .48 if label in demo.target_labels else .22
            self.ax.add_patch(Polygon(vertices, closed=True, facecolor=colors[label], edgecolor=colors[label], alpha=alpha, linewidth=2))
            x = sum(point[0] for point in vertices) / len(vertices)
            y = sum(point[1] for point in vertices) / len(vertices)
            self.ax.text(x, y, label, ha="center", va="center", fontsize=9, weight="bold")
        for label, goal in inputs.goals.items():
            point = goal["goal"]
            marker_color = "#e74c3c" if label in demo.target_labels else "#1f8b4c"
            self.ax.plot(point["map_x"], point["map_y"], marker="*", color=marker_color, markersize=10)
        self.path_line, = self.ax.plot([], [], color="#2166ac", linewidth=2.5, label="planned A* path")
        self.trail_line, = self.ax.plot([], [], color="#00bcd4", linewidth=2, label="travelled trajectory")
        self.robot = Circle((0, 0), ROBOT_RADIUS_METERS, color="#e74c3c", zorder=5)
        self.ax.add_patch(self.robot)
        self.status = self.fig.text(.72, .88, "", va="top", family="monospace", fontsize=9, wrap=True)
        self.ax.set(title=TITLE, xlabel="map x (m)", ylabel="map y (m)", xlim=inputs.metadata.bounds[::2], ylim=inputs.metadata.bounds[1::2], aspect="equal")
        self.ax.legend(loc="upper left", fontsize=8)

    def draw(self, phase: str, frame_index: int = 0, frames: tuple[tuple[float, float], ...] = ()) -> None:
        if self.demo.accepted and phase in {"path", "move", "result"}:
            xs, ys = zip(*self.demo.route_points)
            self.path_line.set_data(xs, ys)
        if frames and phase in {"move", "result"}:
            point = frames[min(frame_index, len(frames) - 1)]
            self.robot.center = point
            xs, ys = zip(*frames[:min(frame_index + 1, len(frames))])
            self.trail_line.set_data(xs, ys)
        elif self.demo.route_points:
            self.robot.center = self.demo.route_points[0]
        result = _result_document(self.demo, frames[:frame_index + 1])
        safe_goal = ", ".join(self.demo.target_labels) or "none"
        self.status.set_text("\n".join((
            "STATE PANEL", f"original_text: {self.demo.text}",
            f"selected_capability: {self.demo.parsed.get('selected_capability')}",
            f"target_area: {safe_goal}", f"planner_status: {result['planner_status']}",
            f"safe_goal: {safe_goal}", f"current_step: {phase}",
            f"distance_remaining: {self._remaining(frame_index, frames):.2f} m",
            f"final_status: {result['final_status'] if phase == 'result' else 'in_progress'}",
            "simulation_only=true", "real_robot_supported=false", "", TITLE,
            "No Gazebo/Nav2 execution artifact.")))
        self.fig.canvas.draw_idle()

    def _remaining(self, index: int, frames: tuple[tuple[float, float], ...]) -> float:
        if not frames:
            return 0.0
        return sum(math.dist(a, b) for a, b in zip(frames[index:], frames[index + 1:]))


def write_visual_artifacts(inputs: HouseInputs, demo: VisualDemo, output_dir: Path, *, export_gif: bool = False) -> dict[str, Path]:
    """Write new, self-contained display records; never overwrite an existing directory."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise VisualDemoError(f"输出目录已存在且非空，拒绝覆盖：{output_dir}")
    frames = trajectory_points(demo)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "visual_demo_request.json").write_text(json.dumps(demo.request, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "visual_demo_plan.json").write_text(json.dumps(_visual_plan_document(demo, inputs), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = _result_document(demo, frames)
    (output_dir / "visual_demo_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "visual_demo_report.md").write_text(_report(demo, result), encoding="utf-8")
    renderer = HouseRenderer(inputs, demo)
    renderer.draw("result" if demo.accepted else "rejected", max(0, len(frames) - 1), frames)
    final_frame = output_dir / "final_frame.png"
    renderer.fig.savefig(final_frame, dpi=130)
    paths = {name: output_dir / name for name in ("visual_demo_request.json", "visual_demo_plan.json", "visual_demo_result.json", "visual_demo_report.md", "final_frame.png")}
    if export_gif:
        from PIL import Image
        selected = list(range(0, len(frames), max(1, len(frames) // 55))) or [0]
        if selected[-1] != max(0, len(frames) - 1): selected.append(max(0, len(frames) - 1))
        images: list[Image.Image] = []
        for index in selected:
            renderer.draw("move", index, frames)
            renderer.fig.canvas.draw()
            rgba = np.asarray(renderer.fig.canvas.buffer_rgba())
            images.append(Image.fromarray(rgba).convert("P", palette=Image.ADAPTIVE))
        gif_path = output_dir / "visual_demo.gif"
        images[0].save(gif_path, save_all=True, append_images=images[1:], duration=90, loop=0)
        paths["visual_demo.gif"] = gif_path
    renderer.plt.close(renderer.fig)
    return paths


def _wait(message: str, non_interactive: bool) -> bool:
    if non_interactive:
        return True
    try:
        input(f"{message}（按 Enter 继续；q 退出）")
        return True
    except (EOFError, KeyboardInterrupt):
        return False


def run_interactive(
    inputs: HouseInputs,
    demo: VisualDemo,
    *,
    non_interactive: bool,
    auto_advance: bool = False,
) -> None:
    """Display the existing patrol animation, optionally without terminal pauses.

    ``auto_advance`` is reserved for the supervisor's pure-2D launch path.  It
    deliberately changes neither the map nor the animation; it only bypasses
    the presentation pauses that otherwise require terminal input.
    """
    renderer = HouseRenderer(inputs, demo)
    frames = trajectory_points(demo)
    if non_interactive:
        renderer.draw("result" if demo.accepted else "rejected", max(0, len(frames) - 1), frames)
        renderer.plt.close(renderer.fig)
        return
    state = {"paused": False, "quit": False, "restart": False}

    def wait_for_step(message: str) -> bool:
        return True if auto_advance else _wait(message, False)

    def on_key(event: Any) -> None:
        if event.key == " ": state["paused"] = not state["paused"]
        elif event.key == "r": state["restart"] = True
        elif event.key in {"q", "escape"}: state["quit"] = True; renderer.plt.close(renderer.fig)
    renderer.fig.canvas.mpl_connect("key_press_event", on_key)
    renderer.plt.show(block=False)
    phases = (("map", "步骤 1/8：住宅地图"), ("request", "步骤 2/8：自然语言请求"),
              ("parse", "步骤 3/8：解析与 planner 验证"), ("target", "步骤 4/8：目标房间高亮"),
              ("safe_goal", "步骤 5/8：accepted safe goal"), ("path", "步骤 6/8：确定性 A* 路径"))
    for phase, message in phases:
        renderer.draw(phase, 0, frames)
        renderer.plt.pause(.01)
        if not wait_for_step(message) or state["quit"]: return
    if not demo.accepted:
        renderer.draw("rejected", 0, frames); renderer.plt.pause(.01)
        wait_for_step("请求未被接受，不启动动画"); return
    if not wait_for_step("步骤 7/8：开始机器人沿规划路径移动") or state["quit"]:
        return
    index = 0
    while index < len(frames) and not state["quit"]:
        if state["restart"]: index, state["restart"] = 0, False
        if not state["paused"]:
            renderer.draw("move", index, frames); index += 1
        renderer.plt.pause(.035)
    if not state["quit"]:
        renderer.draw("result", len(frames) - 1, frames); renderer.plt.pause(.01)
        wait_for_step("步骤 8/8：最终结果与 artifact")
    renderer.plt.close(renderer.fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="house_v1 离线二维住宅可视化演示（不调用 ROS）。")
    parser.add_argument("--text", default="检查厨房", help="现有自然语言适配器支持的请求文本")
    parser.add_argument("--non-interactive", action="store_true", help="用于自动生成，无窗口交互")
    parser.add_argument("--export-gif", action="store_true", help="额外生成会议备用 GIF")
    parser.add_argument(
        "--2d-only",
        action="store_true",
        dest="two_d_only",
        help="run the existing 2D patrol animation without the static-preview menu or terminal pauses",
    )
    args = parser.parse_args(argv)
    try:
        if not args.two_d_only and not optional_static_preview(non_interactive=args.non_interactive):
            print("演示已安全退出。")
            return 0
        inputs = load_house_v1_inputs()
        demo = build_visual_demo(args.text, inputs)
        output_dir = Path(tempfile.mkdtemp(prefix="house-v1-visual-demo-"))
        paths = write_visual_artifacts(inputs, demo, output_dir, export_gif=args.export_gif)
        print(f"{TITLE}\n输出目录：{output_dir}")
        print(f"解析状态：{demo.parsed.get('status')}；规划状态：{demo.planner_plan.get('planning_status')}；action_goals_sent=0")
        print("住宅二维演示不调用 ROS、Gazebo、Nav2 或 RViz；warehouse 保留真实 Gazebo/Nav2 回归用途。")
        print("已生成：" + "、".join(path.name for path in paths.values()))
        run_interactive(inputs, demo, non_interactive=args.non_interactive, auto_advance=args.two_d_only)
        return 0
    except (VisualDemoError, OSError, ValueError) as exc:
        print(f"演示无法完成：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("演示已安全退出。")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
