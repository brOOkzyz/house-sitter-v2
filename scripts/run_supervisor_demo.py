#!/usr/bin/env python3
"""Stable offline supervisor demonstration with optional attach-only Nav2."""
from __future__ import annotations

import argparse
import json
import os
import shlex
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
    NaturalLanguagePipelineError, render_pipeline_artifacts, run_natural_language_pipeline,
    run_natural_language_pipeline_detailed, write_pipeline_artifacts,
)
from house_sitter_core.skill_artifacts import SkillArtifactError, load_skill_inputs  # noqa: E402
from house_sitter_core.skill_execution_bridge import render_execution_artifacts, write_execution_artifacts  # noqa: E402
from house_sitter_core.skill_planner import SkillPlanningError, create_skill_request  # noqa: E402

PREFERRED_ARTIFACT_PAIRS = (("local_annotations/gui_demo_semantic_001/demo_semantic_regions.json", "local_annotations/gui_demo_semantic_001/safe_goal_candidates.json"),)
IGNORED_PATH_PARTS = frozenset({"build", "install", "log", "logs", "tests", "fixtures", ".venv", "__pycache__"})
PIPELINE_FILES = ("natural_language_request.json", "natural_language_parse.json", "skill_plan.json", "pipeline_result.json", "pipeline_report.md")
ATTACH_TIMEOUT_SECONDS = 10.0
PROBE_TIMEOUT_SECONDS = 3.0


class DemoError(RuntimeError):
    """Expected, user-facing error without a traceback."""


class DemoExit(RuntimeError):
    """A user-selected safe exit."""


@dataclass(frozen=True)
class ArtifactPair:
    semantic_regions: Path
    safe_goals: Path


def run_capture(command: Sequence[str], *, cwd: Path, timeout: float = 12.0) -> subprocess.CompletedProcess[str]:
    """Run a bounded local command. It never starts a ROS stack."""
    try:
        return subprocess.run(list(command), cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise DemoError(f"本地检查超时（{timeout:g} 秒）：{' '.join(command)}") from exc
    except OSError as exc:
        raise DemoError(f"无法启动本地检查命令：{exc}") from exc


def _artifact_file_candidates(repo_root: Path, kind: str) -> list[Path]:
    candidates: list[Path] = []
    for root, directories, files in os.walk(repo_root):
        directories[:] = sorted(name for name in directories if name not in IGNORED_PATH_PARTS)
        base = Path(root)
        if any(part in IGNORED_PATH_PARTS for part in base.relative_to(repo_root).parts):
            continue
        for name in sorted(files):
            path, lower = base / name, name.casefold()
            if not path.is_file() or path.suffix != ".json":
                continue
            if kind == "regions" and ("region" in lower or "semantic" in lower):
                candidates.append(path.resolve())
            if kind == "goals" and ("goal" in lower or "safe" in lower):
                candidates.append(path.resolve())
    return candidates


def _validate_pair(regions_path: Path, goals_path: Path) -> bool:
    if not regions_path.is_file() or not goals_path.is_file():
        return False
    try:
        regions, goals = load_skill_inputs(regions_path, goals_path)
        _, parsed, plan, result = run_natural_language_pipeline("检查厨房", regions, goals)
        return parsed.get("status") == "accepted" and plan.get("planning_status") == "ready" and result.get("action_goals_sent") == 0
    except (NaturalLanguagePipelineError, SkillArtifactError, SkillPlanningError, ValueError, OSError):
        return False


def discover_artifact_pairs(repo_root: Path) -> list[ArtifactPair]:
    regions: list[Path] = []
    goals: list[Path] = []
    for region_relative, goal_relative in PREFERRED_ARTIFACT_PAIRS:
        region_path, goal_path = repo_root / region_relative, repo_root / goal_relative
        if region_path.is_file() and goal_path.is_file():
            regions.append(region_path.resolve()); goals.append(goal_path.resolve())
    for path in _artifact_file_candidates(repo_root, "regions"):
        if path not in regions:
            regions.append(path)
    for path in _artifact_file_candidates(repo_root, "goals"):
        if path not in goals:
            goals.append(path)
    return [pair for region in regions for goal in goals if _validate_pair(region, goal) for pair in (ArtifactPair(region, goal),)]


class SupervisorDemo:
    def __init__(self, repo_root: Path = PROJECT_ROOT, *, interactive: bool = True, text: str | None = None, offline_only: bool = False, input_func: Callable[[str], str] = input) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.workspace_setup_path = next((candidate for candidate in (self.repo_root / "install/setup.bash", self.repo_root.parent / "install/setup.bash") if candidate.is_file()), self.repo_root / "install/setup.bash")
        self.interactive, self.text_override, self.offline_only, self.input = interactive, text, offline_only, input_func
        self.output_root = Path(tempfile.mkdtemp(prefix="house-sitter-supervisor-"))
        self.pair: ArtifactPair | None = None
        self.regions: dict[str, Any] | None = None
        self.goals: dict[str, Any] | None = None
        self.request_text = text or "检查厨房"
        self.parsed: dict[str, Any] | None = None
        self.pipeline_dir: Path | None = None
        self.execution_dir: Path | None = None

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def transition(self, optional: bool = False) -> str:
        if not self.interactive:
            return "s" if optional else "c"
        while True:
            answer = self.input("[Enter] 继续下一步  [s] 跳过当前可选步骤  [r] 重试当前步骤  [q] 安全退出：").strip().casefold()
            if answer in {"", "s", "r", "q"} and (optional or answer != "s"):
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
                choice = self.transition(False)
                if choice == "r":
                    continue
                if choice == "q":
                    raise DemoExit()
                continue
            choice = self.transition(optional)
            if choice == "r":
                continue
            if choice == "q":
                raise DemoExit()
            return choice != "s"

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

    def step_preflight(self) -> bool:
        self.say(f"仓库根目录：{self.repo_root}")
        self.say(f"本次演示 artifact 总目录：{self.output_root}")
        try:
            head = run_capture(["git", "rev-parse", "HEAD"], cwd=self.repo_root).stdout.strip()
            status = run_capture(["git", "status", "--porcelain"], cwd=self.repo_root).stdout.strip()
        except DemoError as exc:
            self.say(f"git 预检不可用：{exc}"); head, status = "不可用", ""
        self.say(f"当前 git HEAD：{head}")
        self.say(f"工作区状态：{'干净' if not status else '存在未提交修改'}；Python：{sys.version.split()[0]}")
        pairs = discover_artifact_pairs(self.repo_root)
        if not pairs:
            self.say("未找到同时通过现有 schema 和 planner 验证的正式 semantic-regions / safe-goals JSON。已跳过目录、tests/fixtures、build/install/log 和损坏 JSON。")
            return False
        self.pair = self.choose_pair(pairs)
        try:
            self.regions, self.goals = load_skill_inputs(self.pair.semantic_regions, self.pair.safe_goals)
        except SkillArtifactError as exc:
            self.say(f"读取 artifact 失败：{exc}"); return False
        self.say(f"semantic-regions：{self.pair.semantic_regions}（验证通过）")
        self.say(f"safe-goals：{self.pair.safe_goals}（验证通过）")
        self.say(f"自然语言脚本：{'存在' if (self.repo_root / 'scripts/run_natural_language_skill.py').is_file() else '缺失'}；pipeline：{'存在' if (self.repo_root / 'house_sitter_core/natural_language_pipeline.py').is_file() else '缺失'}")
        self.say("主演示为离线模式：不会检查、启动、停止或修改任何 ROS/Gazebo/Nav2 环境。")
        return True

    def _ensure_inputs(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.regions is None or self.goals is None:
            raise DemoError("artifact 尚未通过预检。")
        return self.regions, self.goals

    def _select_text(self) -> str:
        if self.text_override is not None or not self.interactive:
            return self.request_text
        choices = ("检查厨房", "Patrol the whole house", "去安全等待区", None, "检查房间", "让真实机器人返回充电区")
        self.say("1. 检查厨房\n2. Patrol the whole house\n3. 去安全等待区\n4. 用户自行输入\n5. 演示一个模糊请求\n6. 演示一个真实机器人请求被拒绝")
        while True:
            answer = self.input("请选择请求：").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(choices):
                selected = choices[int(answer) - 1]
                if selected is not None:
                    return selected
                custom = self.input("请输入自然语言请求：").strip()
                if custom:
                    return custom
            self.say("请输入 1–6，且自定义请求不能为空。")

    def step_language(self) -> bool:
        self.request_text = self._select_text()
        try:
            self.parsed = parse_skill_request(self.request_text)
        except NaturalLanguageAdapterError as exc:
            self.say(f"自然语言解析失败：{exc}"); return False
        for key in ("original_text", "status", "selected_capability", "parameters", "simulation_only", "real_robot_supported"):
            self.say(f"{'parse_status' if key == 'status' else key}: {self.parsed.get(key)}")
        return self.parsed.get("status") == "accepted"

    def _write_pipeline(self, request: dict[str, Any], parsed: dict[str, Any], plan: dict[str, Any], result: dict[str, Any], name: str) -> Path:
        destination = self.output_root / name
        write_pipeline_artifacts(destination, render_pipeline_artifacts(request, parsed, plan, result))
        return destination

    def _show_goal(self, plan: dict[str, Any]) -> None:
        steps = plan.get("steps", [])
        if steps and isinstance(steps[0], dict):
            reference = steps[0].get("goal_reference", {})
            if isinstance(reference, dict):
                self.say(f"accepted safe-goal：{ {key: reference.get(key) for key in ('canonical_label', 'proposal_id', 'partition_id', 'goal_order')} }")

    def step_dry_run(self) -> bool:
        if self.parsed is None or self.parsed.get("status") != "accepted":
            self.say("当前请求未被接受；请选择明确的仅仿真请求。")
            return False
        try:
            request, parsed, plan, result = run_natural_language_pipeline(self.request_text, *self._ensure_inputs())
            if not (parsed.get("status") == "accepted" and plan.get("planning_status") == "ready" and result.get("action_goals_sent") == 0 and result.get("simulation_only") is True and result.get("real_robot_supported") is False):
                raise DemoError("dry-run 安全约束验证失败。")
            self.pipeline_dir = self._write_pipeline(request, parsed, plan, result, "pipeline_dry_run")
        except (NaturalLanguagePipelineError, SkillPlanningError, SkillArtifactError, DemoError, OSError, ValueError) as exc:
            self.say(f"dry-run 失败：{exc}"); return False
        self.say(f"capability: {result['selected_capability']}；planner: {result['planner_status']}；计划步骤：{len(plan.get('steps', []))}")
        self._show_goal(plan)
        self.say(f"execution_mode: {result['execution_mode']}；action_goals_sent: 0；final_status: {result['final_status']}")
        self.say(f"输出 artifact 目录：{self.pipeline_dir}")
        return True

    def step_boundaries(self) -> bool:
        try:
            for label, text, expected in (("A", "检查房间", "needs_clarification"), ("B", "让真实机器人返回充电区", "unsupported_intent")):
                request, parsed, plan, result = run_natural_language_pipeline(text, *self._ensure_inputs())
                passed = parsed.get("status") == expected and plan.get("planning_status") == "not_started" and result.get("action_goals_sent") == 0
                self.say(f"{label}. {text!r} → {parsed.get('status')}；未生成执行计划={plan.get('planning_status') == 'not_started'}；action_goals_sent=0；未连接 ROS/Nav2。")
                if not passed:
                    return False
                self._write_pipeline(request, parsed, plan, result, f"boundary_{label.lower()}")
        except (NaturalLanguagePipelineError, SkillPlanningError, SkillArtifactError, OSError, ValueError) as exc:
            self.say(f"安全边界检查失败：{exc}"); return False
        return True

    def step_artifacts(self) -> bool:
        if self.pipeline_dir is None:
            self.say("尚未生成 pipeline artifact。")
            return False
        result = json.loads((self.pipeline_dir / "pipeline_result.json").read_text(encoding="utf-8"))
        for key in ("original_text", "selected_capability", "parse_status", "planner_status", "execution_mode", "action_goals_sent", "final_status", "simulation_only", "real_robot_supported"):
            self.say(f"{key}: {result.get(key)}")
        self.say("生成的 pipeline artifacts：")
        for name in PIPELINE_FILES:
            self.say(f"  {self.pipeline_dir / name}")
        return True

    def step_previous_results(self) -> bool:
        evidence = self.repo_root / "docs/final_demo_evidence.md"
        self.say("Previously validated Gazebo/Nav2 run：")
        self.say("patrol_home：living_room → kitchen → bedroom → charging_area；4 个顺序 Nav2 goals；无跳步、重复或并发；最终状态 succeeded；自适应 timeout 30–300 秒。")
        if evidence.is_file():
            self.say(f"已验证的历史全栈证据：{evidence}（仅只读展示；不伪造新的 execution artifact）。")
        else:
            self.say("未找到完整历史 execution artifact；上述内容仅作为 Previously validated Gazebo/Nav2 run 展示。")
        self.say("离线评估器：scripts/evaluate_skill_execution.py 可对已有完整 execution artifact 生成 CSV/JSON/Markdown；本次离线主演示不创建 execution artifact。")
        return True

    def _ros_probe(self, command: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[bool, str]:
        """Read-only attach probe, bounded independently to at most three seconds."""
        bounded = max(0.1, min(PROBE_TIMEOUT_SECONDS, timeout))
        shell = f"source /opt/ros/jazzy/setup.bash >/dev/null 2>&1 && source {shlex.quote(str(self.workspace_setup_path))} >/dev/null 2>&1 && timeout {bounded:.1f}s bash -lc {shlex.quote(command)}"
        try:
            result = run_capture(["bash", "-lc", shell], cwd=self.repo_root, timeout=bounded + 0.5)
        except DemoError as exc:
            return False, str(exc)
        return result.returncode == 0, (result.stdout + result.stderr).strip()

    def attach_readiness(self) -> tuple[bool, list[str]]:
        started = time.monotonic()
        checks = (
            ("/navigate_to_pose", "ros2 action list 2>/dev/null | grep -qx /navigate_to_pose"),
            ("planner_server active", "ros2 lifecycle get /planner_server 2>/dev/null | grep -qi active"),
            ("controller_server active", "ros2 lifecycle get /controller_server 2>/dev/null | grep -qi active"),
            ("bt_navigator active", "ros2 lifecycle get /bt_navigator 2>/dev/null | grep -qi active"),
            ("map→odom", "ros2 run tf2_ros tf2_echo map odom 2>/dev/null | grep -q 'Translation:'"),
            ("use_sim_time=true", "ros2 param get /planner_server use_sim_time 2>/dev/null | grep -qi true"),
        )
        failed: list[str] = []
        for name, command in checks:
            remaining = ATTACH_TIMEOUT_SECONDS - (time.monotonic() - started)
            if remaining <= 0:
                failed.append("attach-only 总时限")
                break
            if not self._ros_probe(command, timeout=min(PROBE_TIMEOUT_SECONDS, remaining))[0]:
                failed.append(name)
        return not failed, failed

    def _request_from_document(self, request: dict[str, Any]):
        return create_skill_request(request["skill_name"], request["parameters"], request_id=request["request_id"], requested_by=request["requested_by"], priority=request["priority"], battery_percent=request["simulated_battery_percent"], current_region=request["current_region"], injected_events=request["injected_events"], policy_overrides=request["policy_overrides"])

    def step_attach(self) -> bool:
        if self.offline_only:
            self.say("--offline-only：未执行任何 ROS 命令，已跳过 attach-only 实时仿真。")
            return False
        if not self.interactive or self.input("是否检查已经启动的实时仿真环境？[y/N] ").strip().casefold() != "y":
            self.say("未请求实时仿真检查；离线主演示和已有真实仿真结果不受影响。")
            return False
        self.say("实时仿真需要提前启动 Gazebo、localization 和 Nav2。本程序只连接已就绪环境，不负责启动或清理它们。")
        ready, failed = self.attach_readiness()
        if not ready:
            self.say("外部仿真环境未就绪，本次跳过实时执行。离线主流程和已有真实仿真结果不受影响。")
            self.say(f"未通过的只读检查：{', '.join(failed)}")
            return False
        self.say("外部环境已就绪。是否用自然语言“检查厨房”执行一条 planner-approved safe-goal？[y/N]")
        if self.input("请选择：").strip().casefold() != "y":
            return True
        strategy = self.input("收到首条 feedback 后：[c] 取消  [w] 继续等待  [b] 返回主演示：").strip().casefold() or "c"
        if strategy == "b":
            self.say("已返回离线主演示；未发送 action goal。")
            return True
        if strategy not in {"c", "w"}:
            self.say("未选择有效策略；已返回离线主演示，未发送 action goal。")
            return True
        try:
            import rclpy
            from rclpy.parameter import Parameter
            from house_sitter_core.nav2_sim_bridge import Nav2SimulationExecutor
            feedback_count = 0
            def observer(_feedback: dict[str, Any]) -> None:
                nonlocal feedback_count
                feedback_count += 1
            rclpy.init()
            node = rclpy.create_node("supervisor_demo_attach_only", parameter_overrides=[Parameter("use_sim_time", value=True)])
            try:
                request, parsed, plan, result, execution, events = run_natural_language_pipeline_detailed("检查厨房", *self._ensure_inputs(), executor=Nav2SimulationExecutor(node, feedback_observer=observer, cancel_after_feedback=(strategy == "c")), execute_simulation=True)
            finally:
                node.destroy_node(); rclpy.shutdown()
            if execution is None or result.get("action_goals_sent", 0) < 1 or feedback_count < 1:
                raise DemoError("外部 Nav2 未确认 accepted goal 与至少一条 feedback。")
            self.execution_dir = self.output_root / "attach_execution"
            write_execution_artifacts(self.execution_dir, render_execution_artifacts(self._request_from_document(request), plan, execution, events))
            ending = "已在首条 feedback 后安全取消" if strategy == "c" else "已按用户选择继续等待至 Nav2 返回"
            self.say(f"链路成功：Nav2 已接受 planner-approved safe-goal，收到 {feedback_count} 条 feedback；{ending}。")
            return True
        except (ImportError, NaturalLanguagePipelineError, SkillPlanningError, DemoError, OSError, ValueError) as exc:
            self.say(f"attach-only 执行失败：{exc}")
            return False

    def finish(self) -> None:
        self.say(f"artifact 总目录：{self.output_root}")
        self.say("English: This demonstration showed the offline, simulation-only pipeline and an optional attach-only Nav2 check without starting or stopping any external process.")

    def run(self, *, preflight_only: bool = False, dry_run_only: bool = False) -> int:
        try:
            if not self.run_step(0, "仓库和正式 artifact 预检", "This preflight verifies the repository and trusted semantic artifacts without requiring ROS.", self.step_preflight): return 2
            if preflight_only: return 0
            self.run_step(1, "自然语言解析", "The language adapter converts the request into a constrained capability and parameters.", self.step_language)
            self.run_step(2, "完整 dry-run 安全预演", "The planner validates an accepted safe-goal while dry-run sends zero ROS action goals.", self.step_dry_run)
            self.run_step(3, "安全边界演示", "Ambiguous and real-robot requests are rejected by the simulation-only boundary.", self.step_boundaries)
            if dry_run_only: return 0
            self.run_step(4, "结构化 pipeline artifact", "Structured artifacts make the offline decision trace reproducible and auditable.", self.step_artifacts)
            self.run_step(5, "已有 patrol_home 结果与离线评估", "Previously validated simulation evidence is shown read-only; no new execution artifact is fabricated.", self.step_previous_results)
            self.run_step(6, "attach-only 实时仿真", "The program can only attach to a ready external simulation; it never starts or stops Gazebo, localization, or Nav2.", self.step_attach, optional=True)
            self.run_step(7, "总结", "The stable offline demo remains available even when an external simulation is unavailable.", lambda: True)
            return 0
        except DemoExit:
            self.say("用户请求安全退出。"); return 0
        except (DemoError, OSError, ValueError, KeyError) as exc:
            self.say(f"演示已安全停止：{exc}"); return 2
        except Exception as exc:
            self.say(f"演示出现未预期的本地错误，已安全停止：{type(exc).__name__}: {exc}"); return 2
        finally:
            self.finish()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线主演示与可选 attach-only 仿真的导师会议程序。")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dry-run-only", action="store_true")
    parser.add_argument("--offline-only", action="store_true", help="完全不执行任何 ROS 命令。")
    parser.add_argument("--text")
    parser.add_argument("--non-interactive", action="store_true", help="仅供自动测试；不执行 attach-only 检查。")
    args = parser.parse_args(argv)
    if args.preflight_only and args.dry_run_only:
        parser.error("--preflight-only 与 --dry-run-only 不能同时使用。")
    return SupervisorDemo(interactive=not args.non_interactive, text=args.text, offline_only=args.offline_only).run(preflight_only=args.preflight_only, dry_run_only=args.dry_run_only)


if __name__ == "__main__":
    raise SystemExit(main())
