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
    "Pre-flight Check", "Project Objective and System Pipeline", "Static 3D Residential Preview", "2D Patrol Demonstration", "Live Monitoring Scenario",
    "Detected Anomaly and Alert", "Digital Twin Update", "Monitoring Report and Safe Return", "Monitoring Robustness", "Temporal Filtering Trade-off",
    "Patrol Strategy Trade-off", "Research Contributions and Limitations", "Final Summary",
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
        raise DemoError(f"Could not read {path.name}.") from exc
    if not isinstance(data, dict):
        raise DemoError(f"{path.name} is not a JSON object.")
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
            answer = self.input("[Enter] Continue\n[s] Skip\n[r] Retry\n[q] Quit" + ("  " + "  ".join(f"[{key}] {key.upper()}" for key in extra) if extra else "") + "\n: ").strip().casefold()
            if answer in extra:
                extra[answer](); continue
            if answer in {"", "r", "q"} or (optional and answer == "s"):
                return {"": "c", "r": "r", "q": "q", "s": "s"}[answer]
            self.say("Please use Enter, s, r, or q.")

    def launch(self, command: list[str], label: str, timeout: float = 10.0) -> bool:
        try:
            process = subprocess.Popen(command, cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True, env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        except OSError as exc:
            raise DemoError(f"Could not start {label}.") from exc
        self.children.append(Child(process, label))
        time.sleep(min(0.25, timeout))
        if process.poll() not in {None, 0}:
            stdout, stderr = process.communicate(timeout=1)
            (self.logs / f"gui_{len(self.children):02d}.log").write_text(f"COMMAND: {' '.join(command)}\n\nSTDOUT\n{stdout}\n\nSTDERR\n{stderr}", encoding="utf-8")
            raise DemoError(f"{label} could not be opened. Full details were written to the log.")
        return True

    def run_command(self, command: list[str], step: int, timeout: float) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(command, cwd=self.root, text=True, capture_output=True, timeout=timeout, check=False,
                                    env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DemoError(f"The local command did not finish within {timeout:g} seconds.") from exc
        log_path = self.logs / f"command_step_{step:02d}.log"
        log_path.write_text(f"COMMAND: {' '.join(command)}\n\nSTDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}", encoding="utf-8")
        if result.returncode:
            raise DemoError(f"The command failed. Full details were written to: {log_path}")
        return result

    def open_path(self, path: Path, step: int) -> bool:
        if self.args.non_interactive or not path.is_file():
            return False
        for command in (["xdg-open", str(path)], ["gio", "open", str(path)]):
            if shutil.which(command[0]):
                try:
                    return self.launch(command, f"image {path.name}")
                except DemoError:
                    continue
        self.say(f"Please open this file manually: {path.resolve()}")
        return False

    def show_step(self, number: int, english: str, action: Callable[[], bool], *, optional: bool = False,
                  extra: dict[str, Callable[[], None]] | None = None) -> bool:
        if number < self.args.start_at:
            return True
        while True:
            self.say("\n" + "=" * 72)
            self.say(f"Step {number}: {STEPS[number]}{' (optional)' if optional else ''}")
            self.say(english)
            try:
                ok = action()
            except DemoQuit:
                raise
            except Exception as exc:
                path = self.log_failure(number, exc)
                self.say(f"This step encountered a problem. Full details were written to: {path}")
                ok = False
            if not ok and optional:
                self.say("The optional visualisation could not be opened. The demonstration will continue using the text summary.")
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
        self.say(f"Repository root: {self.root}\nGit commit: {git_head}\nWorking tree: {'modified' if dirty else 'clean'}\nPython: {sys.version.split()[0]}")
        self.say("Required resources: " + ("available" if all((self.root / item).is_file() for item in resources) else "missing"))
        self.say(f"Graphical display: {'available' if os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY') else 'not detected'}; Static 3D entry point: {'available' if shutil.which('gz') and (self.root/'scripts/preview_house_v1_3d.sh').is_file() else 'unavailable'}")
        try:
            import matplotlib  # noqa: F401
            matplotlib_ready = "available"
        except ImportError:
            matplotlib_ready = "unavailable"
        self.say(f"matplotlib: {matplotlib_ready}; Cached paper results: {self.paper_dir or 'not found'}")
        self.say("This demo uses the current simulation framework and previously generated experiment results.")
        if self.paper_dir is None:
            self.say("No cached paper results were found. The live demo will continue without regenerating the full experiments. Use --prepare-paper-results before the meeting.")
        return True

    def step1(self) -> bool:
        self.say("Idle domestic robot\n→ autonomous home patrol\n→ simulated onboard sensing\n→ anomaly detection\n→ Digital Twin update\n→ explainable alert\n→ evaluation and safe return")
        self.say("The project focuses on idle-time home patrol, monitoring and Digital Twin updates. The natural-language and skill layers provide task entry and safety validation. The current project is simulation-only.")
        return True

    def step2(self) -> bool:
        if self.args.skip_3d or self.args.non_interactive or not shutil.which("gz"):
            self.say("The static 3D preview was skipped or is unavailable.")
            return True
        return self.launch(["bash", str(self.root / "scripts/preview_house_v1_3d.sh")], "static 3D preview", 15)

    def step3(self) -> bool:
        if self.args.skip_2d:
            self.say("The 2D patrol demonstration was skipped."); return True
        command = [sys.executable, str(self.root / "scripts/run_house_v1_visual_demo.py"), "--text", "Patrol the whole house"]
        if self.args.non_interactive:
            command.extend(["--non-interactive", "--export-gif"])
            result = self.run_command(command, 3, 20)
            self.say("The deterministic 2D patrol was generated with semantic regions, accepted safe goals and A* paths.")
            return bool(result.stdout is not None)
        try:
            return self.launch(command, "2D patrol demonstration", 20)
        except DemoError:
            self.say("The 2D animation is unavailable. Patrol order: living_room → kitchen → bedroom → bathroom → charging_area.")
            return True

    def step4(self) -> bool:
        if self.monitoring_dir.exists():
            raise DemoError("This monitoring directory already exists; it will not be overwritten.")
        result = self.run_command([sys.executable, str(self.root / "scripts/run_house_sitter_monitoring.py"), "--scenario", "kitchen_unexpected_obstacle",
                                   "--output-dir", str(self.monitoring_dir)], 4, 60)
        missing = [name for name in REQUIRED_MONITORING if not (self.monitoring_dir / name).is_file()]
        if missing:
            raise DemoError("The monitoring artefact set is incomplete.")
        self.monitoring = {name: (json_file(self.monitoring_dir / name) if name.endswith(".json") else None) for name in REQUIRED_MONITORING if name.endswith(".json")}
        plan, summary, anomalies = self.monitoring["patrol_plan.json"], self.monitoring["monitoring_summary.json"], self.monitoring["detected_anomalies.json"]
        anomaly = anomalies.get("anomalies", [{}])[0]
        yes_no = lambda value: "Yes" if value else "No"
        self.say(f"Patrol order: {' → '.join(plan.get('patrol_order', []))}\nInspected rooms: {summary.get('covered_rooms')}; Anomaly room: {anomaly.get('room_id')}; Anomaly type: {anomaly.get('anomaly_type')}")
        self.say(f"Anomaly count: {summary.get('detected_anomaly_count')}; False positives: {summary.get('false_positive_count')}; Detection latency: {summary.get('detection_latency_steps')}; Returned to charging area: {yes_no(summary.get('returned_to_charging_area'))}")
        self.say(f"Simulation only: {yes_no(summary.get('simulation_only'))}; Real robot supported: {yes_no(summary.get('real_robot_supported'))}")
        return True

    def _require_monitoring(self) -> None:
        if not self.monitoring:
            raise DemoError("Please complete Step 4 first.")

    def step5(self) -> bool:
        self._require_monitoring()
        anomaly = self.monitoring["detected_anomalies.json"].get("anomalies", [{}])[0]
        alert = self.monitoring["actionable_alerts.json"].get("alerts", [{}])[0]
        labels = {"room_id": "Anomaly room", "anomaly_type": "Anomaly type", "severity": "Severity",
                  "expected_value": "Expected value", "observed_value": "Observed value",
                  "explanation": "Explanation", "recommended_action": "Recommended action"}
        for key, label in labels.items():
            self.say(f"{label}: {anomaly.get(key)}")
        self.say(f"Alert: {alert.get('message')}")
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
            self.say(f"Changed room: {room}")
            for field, (before, after) in fields.items():
                self.say(f"  {field}: {before} → {after}")
        normal_rooms = sorted(room for room, fields in changes.items() if fields.get("anomaly_status", (None, None))[1] != "anomaly")
        self.say(f"Changed rooms: {', '.join(changes) or 'None'}; Rooms with normal observation metadata only: {', '.join(normal_rooms) or 'None'}.")
        return bool(changes)

    def show_figures(self, names: tuple[str, ...], step: int) -> None:
        if not self.paper_dir:
            return
        for name in names:
            shown = self.open_path(self.paper_dir / "figures" / name, step)
            if shown and not self.args.non_interactive:
                self.input(f"Close {name}, then press Enter to continue.")

    def step7(self) -> bool:
        self._require_monitoring()
        summary = self.monitoring["monitoring_summary.json"]
        alerts = self.monitoring["actionable_alerts.json"].get("alerts", [])
        self.say(f"Coverage: {summary.get('coverage_rate')}; Anomaly count: {summary.get('detected_anomaly_count')}; False positives: {summary.get('false_positive_count')}")
        self.say(f"Detection latency: {summary.get('detection_latency_steps')}; Returned to charging area: {'Yes' if summary.get('returned_to_charging_area') else 'No'}")
        self.say(f"Recommended action: {alerts[0].get('recommended_action') if alerts else 'None'}\nGenerated artefacts are available at: {self.monitoring_dir}")
        return True

    def _paper_summary(self) -> dict[str, Any] | None:
        return json_file(self.paper_dir / "results_summary.json") if self.paper_dir else None

    def step8(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("No cached paper results were found. The robustness step will use the text summary."); return True
        data = summary["robustness"]
        self.show_figures(("robustness_detection_metrics.png",), 8)
        self.say(f"20 scenarios; 100 deterministic runs; Precision={data.get('event_precision')}; Recall={data.get('event_recall')}; F1={data.get('event_f1')}")
        failures = json_file(self.paper_dir / "results_summary.json").get("table_rows", {}).get("failure_case_summary", [])
        robustness_failures = sum(item.get("Experiment") == "robustness" for item in failures if isinstance(item, dict))
        self.say(f"Noise false positive rate={data.get('noise_false_positive_rate')}; Digital Twin field Precision={data.get('field_update_precision')}; Digital Twin field Recall={data.get('field_update_recall')}; Failed scenarios={robustness_failures}")
        self.say("The retained failure is a transient layout_signature disturbance, a false layout_change alert and an unnecessary Digital Twin update.")
        return True

    def step9(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("No cached paper results were found. The temporal-filtering step will use the text summary."); return True
        self.show_figures(("temporal_filter_detection_tradeoff.png", "temporal_filter_twin_tradeoff.png"), 9)
        data = summary["temporal_filtering"]
        for policy in ("pre_filtering", "two_observation_confirmation"):
            row = data[policy]
            self.say(f"{policy}: Precision={row.get('event_precision')} Recall={row.get('event_recall')} F1={row.get('event_f1')} noise FPR={row.get('noise_false_positive_rate')} layout Recall={row.get('layout_change_recall')} Twin P/R={row.get('field_update_precision')}/{row.get('field_update_recall')} unintended={row.get('unintended_field_update_count')} combined={row.get('combined_anomaly_exact_set_accuracy')} recovery={row.get('anomaly_resolution_accuracy')} latency={row.get('mean_layout_detection_latency')}")
        self.say("Temporal confirmation removes temporary noise false positives and unnecessary updates, but reduces recall for some short-lived changes and increases latency. The filtered policy is not a default replacement.")
        return True

    def step10(self) -> bool:
        summary = self._paper_summary()
        if not summary:
            self.say("No cached paper results were found. The patrol-strategy step will use the text summary."); return True
        self.show_figures(("patrol_strategy_coverage_discovery.png", "patrol_strategy_distance_energy.png", "patrol_coverage_energy_pareto.png"), 10)
        data = summary["patrol_strategy"]
        for row in data["overall_by_strategy"]:
            self.say(f"{row['strategy']}: coverage={row['mean_coverage_rate']} discovery={row['anomaly_discovery_rate']} latency={row['mean_detection_latency']} distance={row['mean_travel_distance_m']} energy={row['mean_simulated_energy_consumption']} return={row['return_to_charging_success_rate']} skipped={row['rooms_skipped']}")
        self.say("battery_aware has higher coverage and anomaly discovery with higher energy use; risk_priority uses less distance and energy with lower coverage; fixed_order is a stable intermediate baseline. All policies return safely, unvisited rooms cause missed anomalies, and Detector false negatives are 0.")
        return True

    def step11(self) -> bool:
        self.say("Contributions: end-to-end house-sitter monitoring pipeline; simulated onboard sensing; anomaly detection; traceable Digital Twin updates; explainable alerts; robustness evaluation; temporal-filter trade-off; patrol-strategy evaluation; reproducible paper-results pipeline.")
        self.say("Limitations: simulation-only; synthetic sensor data; deterministic energy model; single house_v1 layout; fixed accepted safe goals; no real localisation drift, wheel slip, dynamic humans or physical battery validation; house_v1 has not completed dynamic Gazebo/Nav2 validation, and warehouse evidence is separate. This does not represent real-robot performance.")
        return True

    def step12(self) -> bool:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=self.root, text=True, capture_output=True, check=False).stdout.strip())
        self.say(f"Git commit: {head}; Working tree: {'modified' if dirty else 'clean'}; Monitoring artefacts: {self.monitoring_dir}")
        self.say("The monitoring pipeline, robustness experiment, temporal-filtering experiment, patrol-strategy experiment, and paper-results pipeline are complete.")
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
        self.say(f"Generated artefacts are available at: {self.monitoring_dir}\nLogs are available at: {self.logs}")

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
                self.input("Press Enter to exit safely.")
            return 0
        except (DemoQuit, KeyboardInterrupt):
            self.say("Exiting safely.")
            return 0
        finally:
            self.cleanup()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive supervisor research demonstration (simulation-only).")
    parser.add_argument("--skip-3d", action="store_true"); parser.add_argument("--skip-2d", action="store_true")
    parser.add_argument("--paper-results-dir", type=Path); parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start-at", type=int, default=0); parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--list-steps", action="store_true"); parser.add_argument("--prepare-paper-results", action="store_true")
    args = parser.parse_args(argv)
    if not 0 <= args.start_at < len(STEPS):
        parser.error("--start-at must be between 0 and 12.")
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
            result = subprocess.run(command, cwd=ROOT, timeout=300, text=True, capture_output=True, check=False,
                                    env={**os.environ, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"})
            log_dir = Path(tempfile.mkdtemp(prefix="house_sitter_supervisor_prepare_logs_"))
            log_path = log_dir / "prepare_paper_results.log"
            log_path.write_text(f"COMMAND: {' '.join(command)}\n\nSTDOUT\n{result.stdout}\n\nSTDERR\n{result.stderr}", encoding="utf-8")
            if result.returncode:
                print(f"Paper-results preparation failed. Full details were written to: {log_path}", file=sys.stderr)
            else:
                print(f"Paper-results preparation completed. Full details were written to: {log_path}")
            return result.returncode
        except (OSError, subprocess.TimeoutExpired):
            print("Paper-results preparation failed. Check the local command log.", file=sys.stderr); return 2
    return ResearchDemo(args).run()


if __name__ == "__main__":
    raise SystemExit(main())
