#!/usr/bin/env python3
"""Interactive, simulation-only supervisor demonstration for a mentor meeting.

The default path is deliberately ROS-free: it parses, plans, validates, and
records a dry-run before offering the separately optional Gazebo/Nav2 stages.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from house_sitter_core.natural_language_adapter import NaturalLanguageAdapterError, parse_skill_request  # noqa: E402
from house_sitter_core.natural_language_pipeline import (  # noqa: E402
    NaturalLanguagePipelineError,
    render_pipeline_artifacts,
    run_natural_language_pipeline,
    run_natural_language_pipeline_detailed,
    write_pipeline_artifacts,
)
from house_sitter_core.skill_artifacts import SkillArtifactError, load_skill_inputs  # noqa: E402
from house_sitter_core.skill_execution_bridge import (  # noqa: E402
    render_execution_artifacts,
    write_execution_artifacts,
)
from house_sitter_core.skill_planner import SkillPlanningError, create_skill_request  # noqa: E402


PREFERRED_ARTIFACT_PAIRS = (
    ("local_annotations/gui_demo_semantic_001/demo_semantic_regions.json",
     "local_annotations/gui_demo_semantic_001/safe_goal_candidates.json"),
)
IGNORED_PATH_PARTS = frozenset({"build", "install", "log", "logs", "tests", "fixtures", ".venv", "__pycache__"})
PIPELINE_FILES = ("natural_language_request.json", "natural_language_parse.json", "skill_plan.json", "pipeline_result.json", "pipeline_report.md")


class DemoError(RuntimeError):
    """Expected supervisor failure that is safe to show without a traceback."""


class DemoExit(RuntimeError):
    """A user-selected safe exit."""


@dataclass(frozen=True)
class ArtifactPair:
    semantic_regions: Path
    safe_goals: Path


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen[str]
    log_path: Path


def _tail(path: Path, lines: int = 24) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "（日志暂不可读取）"


def _stop_process_group(process: subprocess.Popen[str], *, grace_seconds: float = 4.0) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGINT)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + grace_seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        pass


def run_capture(command: Sequence[str], *, cwd: Path, timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    """Run a short local check in its own process group, with Chinese errors upstream."""
    try:
        process = subprocess.Popen(
            list(command), cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise DemoError(f"无法启动本地检查命令：{exc}") from exc
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _stop_process_group(process)
        raise DemoError(f"本地检查超时（{timeout:g} 秒）：{' '.join(command)}") from exc
    return subprocess.CompletedProcess(list(command), process.returncode, stdout, stderr)


def _artifact_file_candidates(repo_root: Path, kind: str) -> list[Path]:
    candidates: list[Path] = []
    for root, directories, files in os.walk(repo_root):
        directories[:] = sorted(name for name in directories if name not in IGNORED_PATH_PARTS)
        base = Path(root)
        if any(part in IGNORED_PATH_PARTS for part in base.relative_to(repo_root).parts):
            continue
        for name in sorted(files):
            path = base / name
            lower = name.casefold()
            if path.suffix != ".json":
                continue
            if kind == "regions" and not ("region" in lower or "semantic" in lower):
                continue
            if kind == "goals" and not ("goal" in lower or "safe" in lower):
                continue
            if path.is_file():
                candidates.append(path.resolve())
    return candidates


def _validate_pair(regions_path: Path, goals_path: Path) -> bool:
    """Use the existing strict loader/planner rather than a parallel schema."""
    if not regions_path.is_file() or not goals_path.is_file():
        return False
    try:
        regions, goals = load_skill_inputs(regions_path, goals_path)
        # A normal accepted request exercises map identity, provenance, flags,
        # accepted-goal evidence, and planner binding without ROS imports.
        _, parsed, plan, result = run_natural_language_pipeline("检查厨房", regions, goals)
        return (parsed.get("status") == "accepted" and plan.get("planning_status") == "ready"
                and result.get("action_goals_sent") == 0)
    except (NaturalLanguagePipelineError, SkillArtifactError, SkillPlanningError, ValueError, OSError):
        return False


def discover_artifact_pairs(repo_root: Path) -> list[ArtifactPair]:
    """Discover only real, readable, planner-valid artifact pairs."""
    ordered_regions: list[Path] = []
    ordered_goals: list[Path] = []
    for relative_regions, relative_goals in PREFERRED_ARTIFACT_PAIRS:
        regions, goals = repo_root / relative_regions, repo_root / relative_goals
        if regions.is_file() and goals.is_file():
            ordered_regions.append(regions.resolve()); ordered_goals.append(goals.resolve())
    for path in _artifact_file_candidates(repo_root, "regions"):
        if path not in ordered_regions:
            ordered_regions.append(path)
    for path in _artifact_file_candidates(repo_root, "goals"):
        if path not in ordered_goals:
            ordered_goals.append(path)
    pairs: list[ArtifactPair] = []
    for regions in ordered_regions:
        for goals in ordered_goals:
            if _validate_pair(regions, goals):
                pair = ArtifactPair(regions, goals)
                if pair not in pairs:
                    pairs.append(pair)
    return pairs


class SupervisorDemo:
    def __init__(
        self,
        repo_root: Path = PROJECT_ROOT,
        *,
        interactive: bool = True,
        keep_processes: bool = False,
        text: str | None = None,
        input_func: Callable[[str], str] = input,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        setup_candidates = (self.repo_root / "install/setup.bash", self.repo_root.parent / "install/setup.bash")
        self.workspace_setup_path = next((path for path in setup_candidates if path.is_file()), setup_candidates[0])
        self.interactive = interactive
        self.keep_processes = keep_processes
        self.text_override = text
        self.input = input_func
        self.output_root = Path(tempfile.mkdtemp(prefix="house-sitter-supervisor-"))
        self.pair: ArtifactPair | None = None
        self.regions: dict[str, Any] | None = None
        self.goals: dict[str, Any] | None = None
        self.request_text = text or "检查厨房"
        self.parsed: dict[str, Any] | None = None
        self.pipeline_dir: Path | None = None
        self.execution_dir: Path | None = None
        self.evaluation_dir: Path | None = None
        self.preflight: dict[str, Any] = {}
        self.processes: list[ManagedProcess] = []
        self._cleaned = False

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def choose_pair(self, pairs: list[ArtifactPair]) -> ArtifactPair:
        if len(pairs) == 1 or not self.interactive:
            return pairs[0]
        self.say("发现多个通过现有 schema 与 planner 验证的 artifact 组合：")
        for index, pair in enumerate(pairs, 1):
            self.say(f"  {index}. semantic-regions: {pair.semantic_regions.relative_to(self.repo_root)}")
            self.say(f"     safe-goals:       {pair.safe_goals.relative_to(self.repo_root)}")
        while True:
            answer = self.input("请选择编号（q 安全退出）：").strip().casefold()
            if answer == "q":
                raise DemoExit()
            if answer.isdigit() and 1 <= int(answer) <= len(pairs):
                return pairs[int(answer) - 1]
            self.say("请输入有效编号，或输入 q。")

    def transition(self, optional: bool) -> str:
        if not self.interactive:
            return "s" if optional else "c"
        while True:
            answer = self.input("[Enter] 继续下一步  [s] 跳过当前可选步骤  [r] 重试当前步骤  [q] 安全退出并清理进程：").strip().casefold()
            if answer in {"", "s", "r", "q"}:
                if answer == "s" and not optional:
                    self.say("当前步骤不是可选步骤；请继续、重试或安全退出。")
                    continue
                return {"": "c", "s": "s", "r": "r", "q": "q"}[answer]
            self.say("请输入 Enter、s、r 或 q。")

    def run_step(self, number: int, title: str, english: str, action: Callable[[], bool], *, optional: bool = False) -> bool:
        while True:
            self.say("\n" + "=" * 68)
            self.say(f"步骤 {number}：{title}{'（可选）' if optional else ''}")
            self.say(f"English: {english}")
            completed = action()
            if not completed and optional:
                return False
            if not completed:
                if not self.interactive:
                    return False
                self.say("当前步骤未完成；请重试，或安全退出。")
                while True:
                    choice = self.transition(False)
                    if choice == "r":
                        break
                    if choice == "q":
                        raise DemoExit()
                    self.say("此步骤必须成功后才能继续；请输入 r 或 q。")
                continue
            choice = self.transition(optional)
            if choice == "r":
                continue
            if choice == "q":
                raise DemoExit()
            return choice != "s"

    def _check_command(self, command: Sequence[str], timeout: float = 8.0) -> bool:
        try:
            return run_capture(command, cwd=self.repo_root, timeout=timeout).returncode == 0
        except DemoError:
            return False

    def step_preflight(self) -> bool:
        self.say(f"仓库根目录：{self.repo_root}")
        self.say(f"本次演示 artifact 总目录：{self.output_root}")
        git_head = run_capture(["git", "rev-parse", "HEAD"], cwd=self.repo_root).stdout.strip()
        git_status = run_capture(["git", "status", "--porcelain"], cwd=self.repo_root).stdout.strip()
        self.say(f"当前 git HEAD：{git_head or '不可用'}")
        self.say(f"工作区状态：{'干净' if not git_status else '存在未提交修改'}")
        self.say(f"Python 版本：{sys.version.split()[0]}")
        pairs = discover_artifact_pairs(self.repo_root)
        if not pairs:
            self.say("未找到同时通过现有 schema 和 planner 验证的 semantic-regions / safe-goals JSON。")
            self.say("已跳过目录、tests/fixtures、build/install/log 和损坏 JSON；请补齐正式 artifact 后重试。")
            return False
        self.pair = self.choose_pair(pairs)
        try:
            self.regions, self.goals = load_skill_inputs(self.pair.semantic_regions, self.pair.safe_goals)
        except SkillArtifactError as exc:
            self.say(f"读取 artifact 失败：{exc}")
            return False
        self.say(f"semantic-regions：{self.pair.semantic_regions}（验证通过）")
        self.say(f"safe-goals：{self.pair.safe_goals}（验证通过）")
        required = {
            "自然语言脚本": self.repo_root / "scripts/run_natural_language_skill.py",
            "pipeline 模块": self.repo_root / "house_sitter_core/natural_language_pipeline.py",
            "headless 启动脚本": self.repo_root / "scripts/bringup_headless_turtlebot4.sh",
        }
        for name, path in required.items():
            self.say(f"{name}：{'存在' if path.is_file() else '缺失'}（{path}）")
        ros_setup = Path("/opt/ros/jazzy/setup.bash").is_file()
        workspace_setup = self.workspace_setup_path.is_file()
        shell_check = f"source /opt/ros/jazzy/setup.bash >/dev/null 2>&1 && source {shlex.quote(str(self.workspace_setup_path))} && command -v ros2 >/dev/null && command -v gz >/dev/null && ros2 pkg prefix turtlebot4_navigation >/dev/null 2>&1 && ros2 pkg prefix nav2_bringup >/dev/null 2>&1"
        ros_stack = ros_setup and self._check_command(["bash", "-lc", shell_check])
        self.preflight = {"ros_setup": ros_setup, "workspace_setup": workspace_setup, "ros_stack": ros_stack}
        self.say(f"ROS2：{'可用' if ros_setup else '不可用'}；Gazebo/Nav2：{'可用' if ros_stack else '不可用'}")
        self.say(f"workspace install/setup.bash：{'存在' if workspace_setup else '缺失'}（{self.workspace_setup_path}）")
        if not ros_stack or not workspace_setup:
            self.say("提示：ROS 或完整 workspace 环境缺失不会影响步骤 1–3；实际仿真步骤将被禁用。")
        return True

    def _select_text(self) -> str:
        if self.text_override is not None or not self.interactive:
            return self.request_text
        choices = ("检查厨房", "Patrol the whole house", "去安全等待区", None, "检查房间", "让真实机器人返回充电区")
        self.say("1. 检查厨房\n2. Patrol the whole house\n3. 去安全等待区\n4. 用户自行输入\n5. 演示一个模糊请求\n6. 演示一个真实机器人请求被拒绝")
        while True:
            answer = self.input("请选择请求：").strip()
            if answer.isdigit() and 1 <= int(answer) <= 6:
                selected = choices[int(answer) - 1]
                if selected is None:
                    custom = self.input("请输入自然语言请求：").strip()
                    if custom:
                        return custom
                    self.say("输入不能为空。")
                    continue
                return selected
            self.say("请输入 1–6。")

    def step_language(self) -> bool:
        self.request_text = self._select_text()
        try:
            self.parsed = parse_skill_request(self.request_text)
        except NaturalLanguageAdapterError as exc:
            self.say(f"自然语言解析失败：{exc}")
            return False
        parsed = self.parsed
        for key in ("original_text", "status", "selected_capability", "parameters", "simulation_only", "real_robot_supported"):
            label = "parse_status" if key == "status" else key
            self.say(f"{label}: {parsed.get(key)}")
        if self.interactive and self.input("输入 j 查看完整 JSON，或直接 Enter：").strip().casefold() == "j":
            self.say(json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True))
        return parsed.get("status") == "accepted"

    def _ensure_inputs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.regions is None or self.goals is None:
            raise DemoError("artifact 尚未通过预检，不能进行规划。")
        return self.regions, self.goals

    def _show_goal(self, plan: dict[str, Any]) -> None:
        steps = plan.get("steps", [])
        if not isinstance(steps, list) or not steps:
            return
        reference = steps[0].get("goal_reference", {}) if isinstance(steps[0], dict) else {}
        if isinstance(reference, dict):
            fields = {name: reference.get(name) for name in ("canonical_label", "proposal_id", "partition_id", "goal_order")}
            self.say(f"accepted safe-goal：{fields}")

    def _write_pipeline(self, request: dict[str, Any], parsed: dict[str, Any], plan: dict[str, Any], result: dict[str, Any], name: str) -> Path:
        destination = self.output_root / name
        write_pipeline_artifacts(destination, render_pipeline_artifacts(request, parsed, plan, result))
        return destination

    def step_dry_run(self) -> bool:
        if self.parsed is None or self.parsed.get("status") != "accepted":
            self.say("当前请求未被接受；请在步骤 1 选择一个明确、仅仿真的支持请求后重试。")
            return False
        try:
            request, parsed, plan, result = run_natural_language_pipeline(self.request_text, *self._ensure_inputs())
            if not (parsed.get("status") == "accepted" and plan.get("planning_status") == "ready"
                    and result.get("action_goals_sent") == 0 and result.get("simulation_only") is True
                    and result.get("real_robot_supported") is False):
                raise DemoError("dry-run 安全约束验证失败。")
            self.pipeline_dir = self._write_pipeline(request, parsed, plan, result, "pipeline_dry_run")
        except (NaturalLanguagePipelineError, SkillPlanningError, SkillArtifactError, DemoError, OSError, ValueError) as exc:
            self.say(f"dry-run 失败：{exc}")
            return False
        self.say(f"capability: {result['selected_capability']}；semantic area: {parsed.get('parameters', {}).get('area', '由技能确定')}")
        self.say(f"计划步骤：{len(plan.get('steps', []))}；planner: {result['planner_status']}")
        self._show_goal(plan)
        self.say(f"execution_mode: {result['execution_mode']}；action_goals_sent: {result['action_goals_sent']}；final_status: {result['final_status']}")
        self.say(f"输出 artifact 目录：{self.pipeline_dir}")
        if self.interactive and self.input("输入 v 查看 pipeline 文件名，或直接 Enter：").strip().casefold() == "v":
            self.say("\n".join(str(self.pipeline_dir / name) for name in PIPELINE_FILES))
        return True

    def step_boundaries(self) -> bool:
        regions, goals = self._ensure_inputs()
        cases = (("A", "检查房间", "needs_clarification"), ("B", "让真实机器人返回充电区", "unsupported_intent"))
        for label, text, expected in cases:
            request, parsed, plan, result = run_natural_language_pipeline(text, regions, goals)
            passed = (parsed.get("status") == expected and plan.get("planning_status") == "not_started"
                      and result.get("action_goals_sent") == 0 and result.get("execution_mode") == "not_started")
            self.say(f"{label}. {text!r} → {parsed.get('status')}；未生成执行计划={plan.get('planning_status') == 'not_started'}；action_goals_sent=0；未连接 ROS/Nav2。")
            if not passed:
                self.say("安全边界案例结果不符合预期。")
                return False
            self._write_pipeline(request, parsed, plan, result, f"boundary_{label.lower()}")
        return True

    def _sourced_command(self, command: str) -> list[str]:
        return ["bash", "-lc", f"source /opt/ros/jazzy/setup.bash && source {shlex.quote(str(self.workspace_setup_path))} && {command}"]

    def start_managed(self, name: str, command: Sequence[str]) -> ManagedProcess:
        log_path = self.output_root / "logs" / f"{name}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            stream = log_path.open("w", encoding="utf-8")
            process = subprocess.Popen(list(command), cwd=self.repo_root, text=True, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            stream.close()
        except OSError as exc:
            raise DemoError(f"无法启动 {name}：{exc}") from exc
        managed = ManagedProcess(name, process, log_path)
        self.processes.append(managed)
        self.say(f"已启动 {name}（PID/PGID {process.pid}），日志：{log_path}")
        return managed

    def _wait_ready(self, description: str, check: str, timeout: float, managed: ManagedProcess) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if managed.process.poll() is not None:
                raise DemoError(f"{description} 启动进程提前退出。最后日志：\n{_tail(managed.log_path)}")
            if self._check_command(["bash", "-lc", f"source /opt/ros/jazzy/setup.bash && source {shlex.quote(str(self.workspace_setup_path))} && {check}"], timeout=8.0):
                return
            time.sleep(1.0)
        raise DemoError(f"等待 {description} 超时（{timeout:g} 秒）。最后日志：\n{_tail(managed.log_path)}")

    def step_bringup(self) -> bool:
        if not self.preflight.get("ros_stack") or not self.preflight.get("workspace_setup"):
            self.say("当前环境未通过 ROS/Gazebo/Nav2 预检，已禁用实际仿真步骤。")
            return False
        try:
            gazebo = self.start_managed("gazebo_headless", self._sourced_command("scripts/bringup_headless_turtlebot4.sh"))
            self._wait_ready("Gazebo /clock", "timeout 3s ros2 topic echo /clock rosgraph_msgs/msg/Clock --once >/dev/null 2>&1", 45.0, gazebo)
            localization = self.start_managed("localization", self._sourced_command("ros2 launch turtlebot4_navigation localization.launch.py use_sim_time:=true map:=/opt/ros/jazzy/share/turtlebot4_navigation/maps/warehouse.yaml"))
            self._wait_ready("localization/AMCL", "ros2 node list 2>/dev/null | grep -qx /amcl && ros2 param get /amcl use_sim_time 2>/dev/null | grep -qi true", 60.0, localization)
            nav2 = self.start_managed("nav2", self._sourced_command("ros2 launch turtlebot4_navigation nav2.launch.py use_sim_time:=true"))
            nav2_check = "ros2 action list 2>/dev/null | grep -qx /navigate_to_pose && ros2 lifecycle get /planner_server 2>/dev/null | grep -qi active && ros2 lifecycle get /controller_server 2>/dev/null | grep -qi active && ros2 lifecycle get /bt_navigator 2>/dev/null | grep -qi active && ros2 param get /planner_server use_sim_time 2>/dev/null | grep -qi true"
            self._wait_ready("Nav2 action、lifecycle 与 use_sim_time", nav2_check, 90.0, nav2)
        except DemoError as exc:
            self.say(f"仿真启动失败：{exc}")
            return False
        self.say("仿真验证通过：/navigate_to_pose、planner_server、controller_server、bt_navigator、AMCL 与 use_sim_time=true。")
        return True

    def _request_from_document(self, request: dict[str, Any]):
        return create_skill_request(
            request["skill_name"], request["parameters"], request_id=request["request_id"],
            requested_by=request["requested_by"], priority=request["priority"],
            battery_percent=request["simulated_battery_percent"], current_region=request["current_region"],
            injected_events=request["injected_events"], policy_overrides=request["policy_overrides"],
        )

    def step_execute(self) -> bool:
        if not self.processes:
            self.say("尚未启动并验证仿真环境，不能发送仿真 NavigateToPose goal。")
            return False
        if self.parsed is None or self.parsed.get("status") != "accepted":
            self.say("当前请求未被接受，不能执行。")
            return False
        if self.parsed.get("selected_capability") not in {"inspect_area", "go_to_safe_waiting_area", "return_to_charger"}:
            if not self.interactive:
                self.say("自动模式只执行单目标技能；当前请求不是单目标技能。")
                return False
            options = (("检查厨房", "inspect_area: kitchen"), ("去安全等待区", "go_to_safe_waiting_area"), ("返回充电区", "return_to_charger"))
            self.say("为避免现场执行耗时很长的多目标巡逻，请选择单目标演示请求：")
            for index, (text, label) in enumerate(options, 1):
                self.say(f"  {index}. {text} ({label})")
            answer = self.input("请选择 1–3：").strip()
            if not answer.isdigit() or not 1 <= int(answer) <= len(options):
                self.say("未选择有效的单目标请求。")
                return False
            self.request_text = options[int(answer) - 1][0]
            self.parsed = parse_skill_request(self.request_text)
        self.say(f"自然语言请求：{self.request_text}")
        last_feedback = 0.0
        feedback_count = 0

        def feedback_observer(feedback: dict[str, Any]) -> None:
            nonlocal last_feedback, feedback_count
            feedback_count += 1
            now = time.monotonic()
            if now - last_feedback >= 2.0:
                self.say(f"Nav2 feedback：distance_remaining={feedback.get('distance_remaining', '未知')}，recoveries={feedback.get('number_of_recoveries', 0)}")
                last_feedback = now

        try:
            import rclpy
            from rclpy.parameter import Parameter
            from house_sitter_core.nav2_sim_bridge import Nav2SimulationExecutor
            rclpy.init()
            node = rclpy.create_node("supervisor_demo_simulation", parameter_overrides=[Parameter("use_sim_time", value=True)])
            try:
                request, parsed, plan, result, execution, events = run_natural_language_pipeline_detailed(
                    self.request_text, *self._ensure_inputs(), executor=Nav2SimulationExecutor(node, feedback_observer=feedback_observer), execute_simulation=True,
                )
            finally:
                node.destroy_node(); rclpy.shutdown()
            if execution is None or result.get("action_goals_sent", 0) < 1:
                raise DemoError("仿真执行没有产生经过 planner 批准的 NavigateToPose goal。")
            self.pipeline_dir = self._write_pipeline(request, parsed, plan, result, "pipeline_execution")
            self.execution_dir = self.output_root / "execution"
            write_execution_artifacts(self.execution_dir, render_execution_artifacts(self._request_from_document(request), plan, execution, events))
        except (ImportError, NaturalLanguagePipelineError, SkillPlanningError, DemoError, OSError, ValueError) as exc:
            self.say(f"仿真执行失败：{exc}")
            return False
        recoveries = max((event.get("feedback", {}).get("number_of_recoveries", 0) for event in events if event.get("status") == "feedback"), default=0)
        self.say(f"selected capability: {result['selected_capability']}；planner 状态：{result['planner_status']}")
        self._show_goal(plan)
        self.say(f"action goal 已发送={result['action_goals_sent']}；Nav2 已接受；feedback={feedback_count}；recoveries={recoveries}；最终状态={result['final_status']}")
        return True

    def step_results(self) -> bool:
        if self.pipeline_dir is None:
            self.say("尚未生成 pipeline artifact。")
            return False
        result = json.loads((self.pipeline_dir / "pipeline_result.json").read_text(encoding="utf-8"))
        summary = {key: result.get(key) for key in ("original_text", "selected_capability", "parse_status", "planner_status", "execution_mode", "action_goals_sent", "final_status")}
        if isinstance(result.get("execution_summary"), dict):
            summary.update({key: result["execution_summary"].get(key) for key in ("timeout_policy", "effective_timeout_seconds")})
        summary.update({"feedback_count": 0, "recovery_count": 0, "simulation_only": result.get("simulation_only"), "real_robot_supported": result.get("real_robot_supported")})
        if self.execution_dir is not None:
            events = (self.execution_dir / "execution_events.jsonl").read_text(encoding="utf-8").splitlines()
            records = [json.loads(item) for item in events]
            summary["feedback_count"] = sum(item.get("status") == "feedback" for item in records)
            summary["recovery_count"] = max((item.get("feedback", {}).get("number_of_recoveries", 0) for item in records if item.get("status") == "feedback"), default=0)
        for key, value in summary.items():
            self.say(f"{key}: {value}")
        self.say("生成的 pipeline artifacts：")
        for name in PIPELINE_FILES:
            self.say(f"  {self.pipeline_dir / name}")
        if self.execution_dir is not None:
            self.say(f"execution artifacts：{self.execution_dir}")
        return True

    def step_evaluate(self) -> bool:
        if self.execution_dir is None:
            self.say("本次没有实际 Gazebo/Nav2 execution artifact，离线评估已跳过。")
            return False
        self.evaluation_dir = self.output_root / "evaluation"
        completed = run_capture([sys.executable, str(self.repo_root / "scripts/evaluate_skill_execution.py"), str(self.execution_dir), "--output-dir", str(self.evaluation_dir)], cwd=self.repo_root, timeout=30.0)
        if completed.returncode != 0:
            self.say(f"离线评估失败：{completed.stderr.strip() or completed.stdout.strip()}")
            return False
        summary = json.loads((self.evaluation_dir / "execution_summary.json").read_text(encoding="utf-8"))
        self.say(f"trial 数量：{summary['trial_count']}；状态：{summary['status_counts']}；goal 数量：{summary['total_goals']}；feedback：{summary['total_feedback']}；recovery：{summary['total_recoveries']}")
        self.say(f"评估输出：{self.evaluation_dir}")
        return True

    def cleanup(self) -> None:
        if self._cleaned or self.keep_processes:
            return
        self._cleaned = True
        for managed in reversed(self.processes):
            _stop_process_group(managed.process)
        self.say(f"已安全清理本程序启动的 {len(self.processes)} 个进程组；artifact 保留在：{self.output_root}")

    def step_cleanup(self) -> bool:
        if self.keep_processes:
            self.say("已显式指定 --keep-processes；本程序不停止已启动的仿真进程。")
            return True
        self.cleanup()
        remaining = [managed.name for managed in self.processes if managed.process.poll() is None]
        self.say(f"本程序子进程残留：{'无' if not remaining else ', '.join(remaining)}")
        self.say("所有本次 artifact 已保留；不会删除或覆盖仓库中的旧版 demo。")
        return not remaining

    def finish(self) -> None:
        status = run_capture(["git", "status", "--porcelain"], cwd=self.repo_root).stdout.strip()
        self.say(f"仓库工作区：{'干净' if not status else '存在原有或本次未提交修改（未作覆盖）'}")
        self.say(f"artifact 总目录：{self.output_root}")
        self.say("English: This demonstration showed the complete simulation-only pipeline from natural-language input to safe planning, Gazebo/Nav2 execution, structured artifacts, and quantitative evaluation.")

    def run(self, *, preflight_only: bool = False, dry_run_only: bool = False) -> int:
        try:
            if not self.run_step(0, "环境预检", "This preflight verifies the repository, trusted semantic artifacts, and the available simulation environment before any task is planned or executed.", self.step_preflight):
                return 2
            if preflight_only:
                return 0
            self.run_step(1, "自然语言解析", "The language adapter converts the user’s request into a constrained capability and parameters. It cannot generate navigation coordinates or directly control the robot.", self.step_language)
            self.run_step(2, "完整 dry-run 安全预演", "In dry-run mode, the request passes through the planner and safe-goal validation, but no ROS action goal is sent. This allows the complete plan to be reviewed before execution.", self.step_dry_run)
            self.run_step(3, "安全边界演示", "Ambiguous requests require clarification, while real-robot or hardware requests are rejected because this project is simulation-only.", self.step_boundaries)
            if dry_run_only:
                return 0
            brought_up = self.run_step(4, "启动仿真环境", "The simulation stack runs TurtleBot4, localization, and Nav2 with simulation time. No physical robot or hardware interface is involved.", self.step_bringup, optional=True)
            if brought_up:
                self.run_step(5, "自然语言到 Gazebo/Nav2 的完整执行", "The same natural-language request is now executed in Gazebo. The navigation goal comes from the planner-approved safe-goal artifact, and the execution bridge sends a standard NavigateToPose action to Nav2.", self.step_execute, optional=True)
            self.run_step(6, "结果与 artifact 展示", "The system records the complete decision and execution trace as structured artifacts, which supports reproducibility, auditing, and quantitative evaluation.", self.step_results)
            self.run_step(7, "离线评估", "The offline evaluator converts execution artifacts into quantitative metrics for the project report and repeated simulation experiments.", self.step_evaluate, optional=True)
            self.run_step(8, "结束与清理", "This demonstration showed the complete simulation-only pipeline from natural-language input to safe planning, Gazebo/Nav2 execution, structured artifacts, and quantitative evaluation.", self.step_cleanup)
            return 0
        except DemoExit:
            self.say("用户请求安全退出。")
            return 0
        except KeyboardInterrupt:
            self.say("收到 Ctrl+C，正在安全清理本程序启动的进程。")
            return 130
        except (DemoError, OSError, ValueError, KeyError) as exc:
            self.say(f"演示已安全停止：{exc}")
            return 2
        except Exception as exc:  # Never expose a traceback during a mentor demonstration.
            self.say(f"演示出现未预期的本地错误，已安全停止：{type(exc).__name__}: {exc}")
            return 2
        finally:
            self.cleanup()
            self.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="交互式、仅仿真的导师会议演示程序。")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dry-run-only", action="store_true")
    parser.add_argument("--text")
    parser.add_argument("--keep-processes", action="store_true", help="保留本程序启动的仿真进程；默认安全关闭。")
    parser.add_argument("--non-interactive", action="store_true", help="仅供自动测试；跳过可选仿真步骤。")
    args = parser.parse_args(argv)
    if args.preflight_only and args.dry_run_only:
        parser.error("--preflight-only 与 --dry-run-only 不能同时使用。")
    demo = SupervisorDemo(interactive=not args.non_interactive, keep_processes=args.keep_processes, text=args.text)
    previous = signal.getsignal(signal.SIGTERM)

    def on_signal(signum: int, _frame: Any) -> None:
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    atexit.register(demo.cleanup)
    try:
        return demo.run(preflight_only=args.preflight_only, dry_run_only=args.dry_run_only)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
