#!/usr/bin/env python3
"""Interactive, simulation-only supervisor demonstration for the house_sitter project."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MONITORING = (
    "patrol_plan.json", "sensor_observations.jsonl", "digital_twin_before.json", "digital_twin_after.json",
    "detected_anomalies.json", "actionable_alerts.json", "monitoring_summary.json", "monitoring_report.md",
)
STEPS = (
    "环境预检", "项目目标和系统流程", "三维住宅静态预览", "二维动态住宅巡逻", "运行完整监测场景",
    "异常和警报", "Digital Twin前后变化", "监测报告与返航", "鲁棒性实验", "时间过滤对照",
    "巡逻策略对照", "研究贡献和限制", "结束总结",
)


class DemoError(RuntimeError):
    pass


class DemoQuit(RuntimeError):
    pass


@dataclass
class Child:
    process: subprocess.Popen[str]
    label: str


def locate_repo(script_path: Path | None = None) -> Path:
    return Path(script_path or __file__).resolve().parents[1]


def valid_paper_results(path: Path) -> bool:
    return (path / "results_summary.json").is_file() and (path / "results_chapter_draft.md").is_file() and (path / "limitations_and_threats.md").is_file() and (path / "figures").is_dir()


def find_paper_results(explicit: Path | None = None) -> Path | None:
    candidates = [explicit, Path("/tmp/house_sitter_paper_results_final"), Path("/tmp/house_sitter_paper_results")]
    for candidate in candidates:
        if candidate is not None and valid_paper_results(candidate):
            return candidate.resolve()
    matches = sorted((path for path in Path("/tmp").glob("house_sitter_paper_results*") if valid_paper_results(path)),
                     key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0].resolve() if matches else None


def json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"无法读取 {path.name}。") from exc
    if not isinstance(data, dict):
        raise DemoError(f"{path.name} 不是 JSON 对象。")
    return data


def twin_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, tuple[Any, Any]]]:
    index = lambda item: {room["room_id"]: room for room in item.get("rooms", []) if isinstance(room, dict) and isinstance(room.get("room_id"), str)}
    left, right = index(before), index(after)
    result: dict[str, dict[str, tuple[Any, Any]]] = {}
    for room_id in sorted(set(left) & set(right)):
        changed = {field: (left[room_id].get(field), right[room_id].get(field))
                   for field in sorted(set(left[room_id]) | set(right[room_id])) if left[room_id].get(field) != right[room_id].get(field)}
        if changed:
            result[room_id] = changed
    return result


class ResearchDemo:
    def __init__(self, args: argparse.Namespace, *, input_func: Callable[[str], str] = input) -> None:
        self.args, self.root, self.input = args, locate_repo(), input_func
        base = Path(args.output_dir) if args.output_dir else Path(tempfile.gettempdir()) / f"house_sitter_supervisor_demo_{uuid.uuid4().hex[:10]}"
        self.output = base.resolve()
        self.logs = self.output / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.monitoring_dir = self.output / "monitoring_artifacts"
        self.paper_dir = find_paper_results(args.paper_results_dir)
        self.children: list[Child] = []
        self.monitoring: dict[str, Any] = {}

    def say(self, text: str = "") -> None:
        print(text, flush=True)

    def log_failure(self, step: int, exc: BaseException, stdout: str = "", stderr: str = "") -> Path:
        path = self.logs / f"step_{step:02d}.log"
        path.write_text(f"{type(exc).__name__}: {exc}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}\n\n{traceback.format_exc()}", encoding="utf-8")
        return path

    def menu(self, *, optional: bool = False, extra: dict[str, Callable[[], None]] | None = None) -> str:
        if self.args.non_interactive:
            return "s" if optional else "c"
        extra = extra or {}
        while True:
            answer = self.input("[Enter] Continue  [s] Skip  [r] Retry  [q] Quit" + ("  " + "  ".join(f"[{key}] {key.upper()}" for key in extra) if extra else "") + ": ").strip().casefold()
            if answer in extra:
                extra[answer](); continue
            if answer in {"", "r", "q"} or (optional and answer == "s"):
                return {"": "c", "r": "r", "q": "q", "s": "s"}[answer]
            self.say("请输入 Enter、s、r 或 q。")

    def launch(self, command: list[str], label: str, timeout: float = 10.0) -> bool:
        try:
            process = subprocess.Popen(command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)
        except OSError as exc:
            raise DemoError(f"无法启动{label}。") from exc
        self.children.append(Child(process, label))
        time.sleep(min(0.25, timeout))
        if process.poll() not in {None, 0}:
            stdout, stderr = process.communicate(timeout=1)
            raise DemoError(f"{label}启动失败：{stderr.strip() or stdout.strip()}")
        return True

    def run_command(self, command: list[str], step: int, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DemoError(f"本地命令无法在 {timeout:g} 秒内完成。") from exc
        if result.returncode:
            raise DemoError(f"命令失败（详情见日志）。")
        return result

    def open_path(self, path: Path, step: int) -> bool:
        if self.args.non_interactive or not path.is_file():
            return False
        for command in (["xdg-open", str(path)], ["gio", "open", str(path)]):
            if shutil.which(command[0]):
                try:
                    return self.launch(command, f"图像 {path.name}")
                except DemoError:
                    continue
        self.say(f"请手动打开：{path.resolve()}")
        return False

    def show_step(self, number: int, english: str, action: Callable[[], bool], *, optional: bool = False,
                  extra: dict[str, Callable[[], None]] | None = None) -> bool:
        if number < self.args.start_at:
            return True
        while True:
            self.say("\n" + "=" * 72)
            self.say(f"步骤 {number}：{STEPS[number]}{'（可选）' if optional else ''}")
            self.say(english)
            try:
                ok = action()
            except DemoQuit:
                raise
            except Exception as exc:
                path = self.log_failure(number, exc)
                self.say(f"该步骤出现问题，详情已写入：{path}")
                ok = False
            if not ok and optional:
                self.say("可选步骤不可用，继续主演示。")
                return True
            choice = self.menu(optional=optional, extra=extra)
            if choice == "q":
                raise DemoQuit()
            if choice == "r":
                continue
            return choice != "s"

    def step0(self) -> bool:
        git_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip())
        resources = ("scripts/run_house_v1_visual_demo.py", "scripts/run_house_sitter_monitoring.py", "maps/house_v1.pgm",
                     "local_annotations/house_v1/semantic_regions.json", "local_annotations/house_v1/safe_goals.json")
        self.say(f"仓库根目录：{self.root}\nGit commit：{git_head}\n工作区：{'存在未提交修改' if dirty else '干净'}\nPython：{sys.version.split()[0]}")
        self.say("资源：" + ("完整" if all((self.root / item).is_file() for item in resources) else "存在缺失"))
        self.say(f"图形界面：{'可用' if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY') else '未检测到'}；静态3D入口：{'可用' if shutil.which('gz') and (self.root/'scripts/preview_house_v1_3d.sh').is_file() else '不可用'}")
        try:
            import matplotlib  # noqa: F401
            matplotlib_ready = "可用"
        except ImportError:
            matplotlib_ready = "不可用"
        self.say(f"matplotlib：{matplotlib_ready}；论文结果缓存：{self.paper_dir or '未找到'}")
        self.say("This demo uses the current simulation framework and previously generated experiment results.")
        if self.paper_dir is None:
            self.say("不会现场重跑完整实验；后续将使用文字摘要。会前可使用 --prepare-paper-results。")
        return True

    def step1(self) -> bool:
        self.say("Idle domestic robot\n→ autonomous home patrol\n→ simulated onboard sensing\n→ anomaly detection\n→ Digital Twin update\n→ explainable alert\n→ evaluation and safe return")
        self.say("项目聚焦空闲期家庭巡逻、监测和 Digital Twin 更新；自然语言与技能层负责任务入口和安全验证。当前项目完全是 simulation-only。")
        return True

    def step2(self) -> bool:
        if self.args.skip_3d or self.args.non_interactive or not shutil.which("gz"):
            self.say("三维静态预览已跳过或不可用。")
            return True
        return self.launch(["bash", str(self.root / "scripts/preview_house_v1_3d.sh")], "三维静态预览", 15)

    def step3(self) -> bool:
        if self.args.skip_2d:
            self.say("二维动态巡逻已跳过。"); return True
        command = [sys.executable, str(self.root / "scripts/run_house_v1_visual_demo.py"), "--text", "Patrol the whole house"]
        if self.args.non_interactive:
            command.extend(["--non-interactive", "--export-gif"])
            result = self.run_command(command, 3, 20)
            self.say("二维确定性巡逻已生成；包含语义区域、accepted safe goals 与 A* 路径。")
            return bool(result.stdout is not None)
        try:
            return self.launch(command, "二维动态巡逻", 20)
        except DemoError:
            self.say("二维动画不可用；巡逻顺序：living_room → kitchen → bedroom → bathroom → charging_area。")
            return True

    def step4(self) -> bool:
        if self.monitoring_dir.exists():
            raise DemoError("本次监测目录已存在，拒绝覆盖。")
        result = self.run_command([sys.executable, str(self.root / "scripts/run_house_sitter_monitoring.py"), "--scenario", "kitchen_unexpected_obstacle",
                                   "--output-dir", str(self.monitoring_dir)], 4, 60)
        missing = [name for name in REQUIRED_MONITORING if not (self.monitoring_dir / name).is_file()]
        if missing:
            raise DemoError("监测 artifact 不完整。")
        self.monitoring = {name: (json_file(self.monitoring_dir / name) if name.endswith(".json") else None) for name in REQUIRED_MONITORING if name.endswith(".json")}
        plan, summary, anomalies = self.monitoring["patrol_plan.json"], self.monitoring["monitoring_summary.json"], self.monitoring["detected_anomalies.json"]
        anomaly = anomalies.get("anomalies", [{}])[0]
        self.say(f"巡逻顺序：{' → '.join(plan.get('patrol_order', []))}\n已检查房间：{summary.get('covered_rooms')}；异常房间：{anomaly.get('room_id')}；类型：{anomaly.get('anomaly_type')}")
        self.say(f"异常数：{summary.get('detected_anomaly_count')}；误报：{summary.get('false_positive_count')}；延迟：{summary.get('detection_latency_steps')}；返航：{summary.get('returned_to_charging_area')}")
        self.say(f"simulation_only：{summary.get('simulation_only')}；real_robot_supported：{summary.get('real_robot_supported')}")
        return True

    def _require_monitoring(self) -> None:
        if not self.monitoring:
            raise DemoError("请先完成步骤4。")

    def step5(self) -> bool:
        self._require_monitoring()
        anomaly = self.monitoring["detected_anomalies.json"].get("anomalies", [{}])[0]
        alert = self.monitoring["actionable_alerts.json"].get("alerts", [{}])[0]
        for key in ("room_id", "anomaly_type", "severity", "expected_value", "observed_value", "explanation", "recommended_action"):
            self.say(f"{key}: {anomaly.get(key)}")
        self.say(f"alert: {alert.get('message')}")
        return True

    def view_anomalies(self) -> None:
        self._require_monitoring()
        self.say(json.dumps(self.monitoring["detected_anomalies.json"], ensure_ascii=False, indent=2))

    def view_alerts(self) -> None:
        self._require_monitoring()
        self.say(json.dumps(self.monitoring["actionable_alerts.json"], ensure_ascii=False, indent=2))

    def view_twins(self) -> None:
        self._require_monitoring()
        self.say(json.dumps(self.monitoring["digital_twin_before.json"], ensure_ascii=False, indent=2))
        self.say(json.dumps(self.monitoring["digital_twin_after.json"], ensure_ascii=False, indent=2))

    def view_report(self) -> None:
        self._require_monitoring()
        self.say((self.monitoring_dir / "monitoring_report.md").read_text(encoding="utf-8"))

    def step6(self) -> bool:
        self._require_monitoring()
        changes = twin_changes(self.monitoring["digital_twin_before.json"], self.monitoring["digital_twin_after.json"])
        for room, fields in changes.items():
            self.say(f"Room: {room}")
            for field, (before, after) in fields.items():
                self.say(f"  {field}: {before} → {after}")
        normal_rooms = sorted(room for room, fields in changes.items() if fields.get("anomaly_status", (None, None))[1] != "anomaly")
        self.say(f"更新房间：{', '.join(changes) or '无'}；非异常房间仅有正常观测元数据更新：{', '.join(normal_rooms) or '无'}。")
        return bool(changes)

    def show_figures(self, names: tuple[str, ...], step: int) -> None:
        if not self.paper_dir:
            return
        for name in names:
            shown = self.open_path(self.paper_dir / "figures" / name, step)
            if shown and not self.args.non_interactive:
                self.input(f"关闭 {name} 后按 Enter 继续。")

    def step7(self) -> bool:
        self._require_monitoring()
        summary = self.monitoring["monitoring_summary.json"]
        alerts = self.monitoring["actionable_alerts.json"].get("alerts", [])
        self.say(f"room coverage：{summary.get('coverage_rate')}；anomaly count：{summary.get('detected_anomaly_count')}；false positives：{summary.get('false_positive_count')}")
        self.say(f"detection latency：{summary.get('detection_latency_steps')}；returned to charging area：{summary.get('returned_to_charging_area')}")
        self.say(f"建议：{alerts[0].get('recommended_action') if alerts else '无'}\nartifact目录：{self.monitoring_dir}")
        return True

    def _paper_summary(self) -> dict[str, Any] | None:
        return json_file(self.paper_dir / "results_summary.json") if self.paper_dir else None

    def step8(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("未找到论文结果缓存：鲁棒性结果仅显示会前摘要。"); return True
        data = summary["robustness"]
        self.show_figures(("robustness_detection_metrics.png",), 8)
        self.say(f"20 scenarios；100 deterministic runs；Precision={data.get('event_precision')}；Recall={data.get('event_recall')}；F1={data.get('event_f1')}")
        failures = json_file(self.paper_dir / "results_summary.json").get("table_rows", {}).get("failure_case_summary", [])
        robustness_failures = sum(item.get("Experiment") == "robustness" for item in failures if isinstance(item, dict))
        self.say(f"noise FPR={data.get('noise_false_positive_rate')}；Twin field Precision={data.get('field_update_precision')}；Twin field Recall={data.get('field_update_recall')}；failed scenarios={robustness_failures}")
        self.say("唯一保留失败：transient layout_signature disturbance、false layout_change alert、unnecessary Digital Twin update。")
        return True

    def step9(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("未找到论文结果缓存：时间过滤对照仅显示文字摘要。"); return True
        self.show_figures(("temporal_filter_detection_tradeoff.png", "temporal_filter_twin_tradeoff.png"), 9)
        data = summary["temporal_filtering"]
        for policy in ("pre_filtering", "two_observation_confirmation"):
            row = data[policy]
            self.say(f"{policy}: Precision={row.get('event_precision')} Recall={row.get('event_recall')} F1={row.get('event_f1')} noise FPR={row.get('noise_false_positive_rate')} layout Recall={row.get('layout_change_recall')} Twin P/R={row.get('field_update_precision')}/{row.get('field_update_recall')} unintended={row.get('unintended_field_update_count')} combined={row.get('combined_anomaly_exact_set_accuracy')} recovery={row.get('anomaly_resolution_accuracy')} latency={row.get('mean_layout_detection_latency')}")
        self.say("时间确认消除短暂噪声误报和不必要更新，但会降低部分短暂变化 Recall 并增加延迟；filtered 不是默认替代方案。")
        return True

    def step10(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("未找到论文结果缓存：巡逻策略仅显示文字摘要。"); return True
        self.show_figures(("patrol_strategy_coverage_discovery.png", "patrol_strategy_distance_energy.png", "patrol_coverage_energy_pareto.png"), 10)
        data = summary["patrol_strategy"]
        for row in data["overall_by_strategy"]:
            self.say(f"{row['strategy']}: coverage={row['mean_coverage_rate']} discovery={row['anomaly_discovery_rate']} latency={row['mean_detection_latency']} distance={row['mean_travel_distance_m']} energy={row['mean_simulated_energy_consumption']} return={row['return_to_charging_success_rate']} skipped={row['rooms_skipped']}")
        self.say("battery_aware 覆盖与发现较高但能耗较高；risk_priority 距离与能耗较低但覆盖较低；fixed_order 是稳定中间基线。所有策略安全返航，未访问房间造成遗漏，detector false negative 为 0。")
        return True

    def step11(self) -> bool:
        self.say("贡献：end-to-end house-sitter monitoring pipeline；simulated onboard sensing；anomaly detection；traceable Digital Twin updates；explainable alerts；robustness evaluation；temporal-filter trade-off；patrol-strategy evaluation；reproducible paper-results pipeline。")
        self.say("限制：simulation-only；synthetic sensor data；deterministic energy model；single house_v1 layout；fixed accepted safe goals；无真实定位漂移、打滑、动态人员或物理电池验证；house_v1 未完成动态 Gazebo/Nav2 验证，warehouse 仅为已有执行证据；不代表真实机器人性能。")
        return True

    def step12(self) -> bool:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip())
        self.say(f"Git commit：{head}；工作区：{'存在未提交修改' if dirty else '干净'}；监测artifact：{self.monitoring_dir}")
        self.say("监测链路、鲁棒性实验、时间过滤实验、巡逻策略实验及论文表格图表流程均已完成。")
        self.say("The monitoring pipeline and the three main experiments are complete. The next stage is dissertation writing and final integration.")
        self.say("Demonstration completed.")
        return True

    def cleanup(self) -> None:
        for child in self.children:
            if child.process.poll() is None:
                try:
                    os.killpg(os.getpgid(child.process.pid), signal.SIGTERM)
                    child.process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    try:
                        os.killpg(os.getpgid(child.process.pid), signal.SIGKILL)
                    except OSError:
                        pass
        self.say(f"artifact目录：{self.monitoring_dir}\nlogs目录：{self.logs}")

    def run(self) -> int:
        english = (
            "This demo uses the current simulation framework and previously generated experiment results.",
            "The robot patrols the home during idle time, checks each room and updates the Digital Twin when it detects a change.",
            "This is the residential environment used in the project. The 3D view is only a static preview.",
            "The robot follows safe paths between labelled rooms and avoids walls and furniture.",
            "In this scenario, the robot patrols the home and detects a new obstacle in the kitchen.",
            "The system explains what changed, where it happened and what action the user should take.",
            "Only the affected room receives anomaly updates in the Digital Twin. The other rooms retain normal status.",
            "The report summarises the patrol, the detected anomaly and the robot’s return to the charging area.",
            "The robustness test detected all injected anomalies, but one temporary layout disturbance caused a false alarm.",
            "Using two observations removes the temporary false alarm, but it also increases delay and misses some short-lived changes.",
            "Battery-aware patrol covers more rooms, while risk-priority patrol uses less distance and energy.",
            "The project provides a reproducible simulation and evaluation framework, but it has not yet been validated on a physical robot.",
            "The monitoring pipeline and the three main experiments are complete. The next stage is dissertation writing and final integration.",
        )
        actions = (self.step0, self.step1, self.step2, self.step3, self.step4, self.step5, self.step6, self.step7, self.step8, self.step9, self.step10, self.step11, self.step12)
        try:
            extras = {5: {"v": self.view_anomalies, "a": self.view_alerts}, 6: {"v": self.view_twins}, 7: {"v": self.view_report}}
            for number, (sentence, action) in enumerate(zip(english, actions)):
                self.show_step(number, sentence, action, optional=number in {2, 3}, extra=extras.get(number))
            if not self.args.non_interactive and self.args.start_at <= 12:
                self.input("按 Enter 安全退出。")
            return 0
        except (DemoQuit, KeyboardInterrupt):
            self.say("演示已安全退出。")
            return 0
        finally:
            self.cleanup()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="交互式导师研究演示（simulation-only）。")
    parser.add_argument("--skip-3d", action="store_true"); parser.add_argument("--skip-2d", action="store_true")
    parser.add_argument("--paper-results-dir", type=Path); parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start-at", type=int, default=0); parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--list-steps", action="store_true"); parser.add_argument("--prepare-paper-results", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.start_at < len(STEPS):
        parser.error("--start-at 必须在 0 到 12 之间。")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.list_steps:
        for index, title in enumerate(STEPS):
            print(f"{index}: {title}")
        return 0
    if args.prepare_paper_results:
        command = [sys.executable, str(ROOT / "scripts/build_paper_results.py"), "--regenerate",
                   "--output-dir", str(args.paper_results_dir or Path("/tmp/house_sitter_paper_results"))]
        try:
            return subprocess.run(command, cwd=ROOT, timeout=300, check=False).returncode
        except (OSError, subprocess.TimeoutExpired):
            print("论文结果准备失败；请查看本地命令输出。", file=sys.stderr); return 2
    return ResearchDemo(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
