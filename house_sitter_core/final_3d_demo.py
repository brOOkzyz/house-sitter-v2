"""Phase-1 orchestration for the simulation-only 3D house-sitter demo.

The module keeps ROS and Gazebo imports out of module scope so dry-run and unit
tests remain local.  Live execution uses the installed TurtleBot4 Gazebo and
Nav2 interfaces only after explicit readiness checks have passed.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .digital_twin import create_house_v1_baseline, room_index, update_room_from_observation
from .environment_monitoring import actionable_alerts, detect_anomalies
from .house_sitter_patrol import load_house_v1_monitoring_inputs
from .nav2_sim_bridge import NavigationExecutor, NavigationGoal, NavigationOutcome, Nav2SimulationExecutor
from .simulated_onboard_sensors import observe_room
from .simulation_boundary import synthetic_onboard_boundary


ARTIFACT_NAMES = (
    "demo_manifest.json", "preflight_check.json", "navigation_to_kitchen.json", "injected_entities.json",
    "simulated_observation.json", "detected_anomalies.json", "digital_twin_before.json", "digital_twin_after.json",
    "actionable_alerts.json", "return_to_charge.json", "demo_summary.json", "demo_report.md",
)
SCENARIO_ID = "kitchen_unexpected_obstacle"
OBSTACLE_ENTITY_PREFIX = "house_sitter_final_demo_obstacle"


class FinalDemoError(RuntimeError):
    """A concise, fail-closed error for the final 3D demonstration."""


@dataclass(frozen=True)
class SafeGoal:
    label: str
    x: float
    y: float
    yaw: float
    reference: dict[str, Any]


@dataclass(frozen=True)
class ObstacleSpec:
    entity_name: str
    x: float
    y: float
    z: float = 0.25
    size: float = 0.45


@dataclass
class StartedProcess:
    process: subprocess.Popen[Any]
    name: str


@dataclass(frozen=True)
class CommandResult:
    """Bounded result for a diagnostic command that must never stop a preview."""

    command: list[str]
    started: bool
    completed: bool
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    duration_seconds: float
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "started": self.started,
            "completed": self.completed,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
        }


class LiveRuntime(Protocol):
    def start(self, *, headless: bool, charging_goal: SafeGoal, output_dir: Path) -> None: ...
    def ready(self, timeout_seconds: float) -> dict[str, bool]: ...
    def navigate(self, goal: SafeGoal, timeout_seconds: float) -> dict[str, Any]: ...
    def spawn_obstacle(self, obstacle: ObstacleSpec) -> dict[str, Any]: ...
    def entity_exists(self, entity_name: str) -> bool: ...
    def remove_entity(self, entity_name: str) -> bool: ...
    def close(self) -> None: ...


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalDemoError(f"Could not read required local input: {path}") from exc
    if not isinstance(value, dict):
        raise FinalDemoError(f"Required local input is not a JSON object: {path}")
    return value


def safe_goals(root: Path) -> tuple[dict[str, Any], dict[str, SafeGoal]]:
    regions, goals_document = load_house_v1_monitoring_inputs(root)
    selected: dict[str, SafeGoal] = {}
    for item in goals_document.get("goals", []):
        if not isinstance(item, dict) or item.get("status") != "accepted":
            continue
        label, goal = item.get("canonical_label"), item.get("goal")
        if not isinstance(label, str) or not isinstance(goal, dict):
            continue
        try:
            selected[label] = SafeGoal(label, float(goal["map_x"]), float(goal["map_y"]), float(goal.get("yaw", 0.0)), deepcopy(item))
        except (KeyError, TypeError, ValueError) as exc:
            raise FinalDemoError(f"Accepted safe goal for {label!r} is invalid.") from exc
    for label in ("kitchen", "charging_area"):
        if label not in selected:
            raise FinalDemoError(f"The required accepted safe goal is missing: {label}.")
    return regions, selected


def _point_in_polygon(x: float, y: float, vertices: list[list[float]]) -> bool:
    inside = False
    for index, current in enumerate(vertices):
        previous = vertices[index - 1]
        x1, y1, x2, y2 = float(current[0]), float(current[1]), float(previous[0]), float(previous[1])
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def kitchen_obstacle_spec(regions: dict[str, Any], kitchen_goal: SafeGoal, run_id: str) -> ObstacleSpec:
    """Derive a visible obstacle pose from the accepted kitchen goal, not a new goal."""
    entity_name = f"{OBSTACLE_ENTITY_PREFIX}_{run_id}"
    # The reviewed offset remains inside the current kitchen polygon and clear of
    # the kitchen goal; containment is verified below against the committed region.
    obstacle = ObstacleSpec(entity_name, kitchen_goal.x + 1.05, kitchen_goal.y - 0.55)
    room = next((item for item in regions.get("regions", []) if isinstance(item, dict) and item.get("canonical_label") == "kitchen"), None)
    vertices = room.get("polygon", {}).get("vertices") if isinstance(room, dict) else None
    if not isinstance(vertices, list) or not _point_in_polygon(obstacle.x, obstacle.y, vertices):
        raise FinalDemoError("The configured obstacle pose is outside the kitchen semantic region.")
    return obstacle


def preflight(root: Path, *, require_live_tools: bool = False) -> dict[str, Any]:
    regions, goals = safe_goals(root)
    required = {
        "world": root / "worlds" / "house_v1.sdf", "map": root / "maps" / "house_v1.yaml",
        "semantic_regions": root / "local_annotations" / "house_v1" / "semantic_regions.json",
        "safe_goals": root / "local_annotations" / "house_v1" / "safe_goals.json",
        "headless_bringup": root / "scripts" / "bringup_house_v1_headless.sh",
        "gui_bringup": root / "scripts" / "bringup_house_v1_gui.sh",
        "nav2_readiness": root / "scripts" / "check_nav2_ready.sh",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    tools = {name: bool(__import__("shutil").which(name)) for name in ("ros2", "gz")}
    if missing or (require_live_tools and not all(tools.values())):
        details = ", ".join(missing or [name for name, available in tools.items() if not available])
        raise FinalDemoError(f"Live demonstration preflight failed: {details}.")
    return {
        "house_world": str(required["world"]), "map": str(required["map"]), "required_files": {name: str(path) for name, path in required.items()},
        "tools": tools, "kitchen_goal": {"x": goals["kitchen"].x, "y": goals["kitchen"].y, "source": goals["kitchen"].reference["proposal_id"]},
        "charging_goal": {"x": goals["charging_area"].x, "y": goals["charging_area"].y, "source": goals["charging_area"].reference["proposal_id"]},
        "house_v1_dynamic_navigation_verified": False, "simulation_only": True, "real_robot_supported": False,
    }


def make_observation(before: dict[str, Any], obstacle: ObstacleSpec, robot_pose: dict[str, Any] | None = None) -> dict[str, Any]:
    baseline = room_index(before)["kitchen"]
    observation = observe_room("kitchen", 1, baseline, unexpected_obstacle=True)
    observation.update({
        "observation_id": f"gazebo:house_v1:{obstacle.entity_name}:kitchen:step:1",
        "source": "gazebo_entity_state_to_simulated_onboard_observation",
        "observation_trace": {"entity_name": obstacle.entity_name, "gazebo_pose": {"x": obstacle.x, "y": obstacle.y, "z": obstacle.z}, "robot_pose": robot_pose or {}, "timestamp_monotonic": time.monotonic()},
        **synthetic_onboard_boundary(),
    })
    return observation


def _compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_artifacts(output_dir: Path, payload: dict[str, Any]) -> dict[str, Path]:
    output = Path(output_dir)
    if output.exists():
        # Live startup creates only logs/ before artifact publication.  Any
        # other pre-existing content is an unsafe overwrite request.
        if {path.name for path in output.iterdir()} != {"logs"}:
            raise FinalDemoError(f"The output directory already exists: {output}")
    else:
        output.mkdir(parents=True)
        (output / "logs").mkdir()
    for name in ARTIFACT_NAMES:
        if name not in payload:
            raise FinalDemoError(f"Artifact payload is incomplete: {name}")
        (output / name).write_text(payload[name] if isinstance(payload[name], str) else _compact(payload[name]), encoding="utf-8", newline="")
    return {name: output / name for name in ARTIFACT_NAMES}


@dataclass
class RosGazeboRuntime:
    root: Path
    processes: list[StartedProcess] = field(default_factory=list)
    node: Any | None = None
    navigator: NavigationExecutor | None = None
    log_dir: Path | None = None

    def _start(self, name: str, command: list[str]) -> None:
        assert self.log_dir is not None
        handle = (self.log_dir / f"{name}.log").open("w", encoding="utf-8")
        process = subprocess.Popen(command, cwd=str(self.root), env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append(StartedProcess(process, name))
        time.sleep(1.0)
        if process.poll() is not None:
            raise FinalDemoError(f"{name} exited during startup; see {self.log_dir / (name + '.log')}.")

    def start(self, *, headless: bool, charging_goal: SafeGoal, output_dir: Path) -> None:
        self.log_dir = output_dir / "logs"
        mode = "-r -s" if headless else "-r"
        self._start("gazebo", ["ros2", "launch", "ros_gz_sim", "gz_sim.launch.py", f"gz_args:={mode} {self.root / 'worlds' / 'house_v1.sdf'}"])
        self._start("clock_bridge", ["ros2", "run", "ros_gz_bridge", "parameter_bridge", "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"])
        self._start("turtlebot4", ["ros2", "launch", "turtlebot4_gz_bringup", "turtlebot4_spawn.launch.py", "world:=house_v1", "rviz:=false", "localization:=false", "slam:=false", "nav2:=false", f"x:={charging_goal.x}", f"y:={charging_goal.y}", "z:=0.05", f"yaw:={charging_goal.yaw}"])
        self._start("localization", ["ros2", "launch", "turtlebot4_navigation", "localization.launch.py", "use_sim_time:=true", f"map:={self.root / 'maps' / 'house_v1.yaml'}"])
        self._start("nav2", ["ros2", "launch", "turtlebot4_navigation", "nav2.launch.py", "use_sim_time:=true"])

    def _query(self, command: list[str], timeout: float = 5.0) -> str:
        completed = subprocess.run(command, cwd=str(self.root), env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
        return completed.stdout if completed.returncode == 0 else ""

    def ready(self, timeout_seconds: float) -> dict[str, bool]:
        deadline = time.monotonic() + timeout_seconds
        expected = {"/clock", "/scan", "/odom", "/tf", "/tf_static", "/map", "/amcl_pose"}
        while time.monotonic() < deadline:
            topics = set(self._query(["ros2", "topic", "list"]).splitlines())
            actions = set(self._query(["ros2", "action", "list"]).splitlines())
            services = set(self._query(["ros2", "service", "list"]).splitlines())
            stop_motor_ready = any(service.rsplit("/", 1)[-1] == "stop_motor" for service in services)
            if expected.issubset(topics) and "/navigate_to_pose" in actions and stop_motor_ready:
                return {name: True for name in sorted(expected | {"/navigate_to_pose", "/stop_motor"})}
            time.sleep(1.0)
        return {name: False for name in sorted(expected | {"/navigate_to_pose", "/stop_motor"})}

    def _navigator(self) -> NavigationExecutor:
        if self.navigator is None:
            import rclpy
            from rclpy.parameter import Parameter
            rclpy.init()
            self.node = rclpy.create_node("house_sitter_final_3d_demo", parameter_overrides=[Parameter("use_sim_time", value=True)])
            self.navigator = Nav2SimulationExecutor(self.node)
        return self.navigator

    def navigate(self, goal: SafeGoal, timeout_seconds: float) -> dict[str, Any]:
        started = time.monotonic(); executor = self._navigator()
        nav_goal = NavigationGoal(goal.label, goal.x, goal.y, goal.reference)
        outcome = executor.wait_for_result(executor.send_goal(nav_goal), timeout_seconds)
        raw_pose = self._query(["ros2", "topic", "echo", "/amcl_pose", "--once"], 5.0)
        match = re.search(r"position:\s*\n\s*x:\s*([-+0-9.eE]+)\s*\n\s*y:\s*([-+0-9.eE]+)", raw_pose)
        final_pose = {"x": float(match.group(1)), "y": float(match.group(2))} if match else None
        distance_to_goal = None if final_pose is None else math.hypot(final_pose["x"] - goal.x, final_pose["y"] - goal.y)
        within_tolerance = distance_to_goal is not None and distance_to_goal <= 0.75
        status = outcome.status if outcome.status != "succeeded" or within_tolerance else "failed"
        reason = outcome.reason if status == outcome.status else "final_pose_outside_0.75m_goal_tolerance"
        return {"goal": {"label": goal.label, "x": goal.x, "y": goal.y, "frame_id": "map"}, "goal_sent_monotonic": started, "result": status, "travel_duration_seconds": time.monotonic() - started, "feedback": list(outcome.feedback), "distance_travelled_m": outcome.feedback[0].get("distance_remaining") if outcome.feedback else None, "final_pose": final_pose, "final_pose_distance_to_goal_m": distance_to_goal, "final_pose_within_tolerance": within_tolerance, "reason": reason}

    def spawn_obstacle(self, obstacle: ObstacleSpec) -> dict[str, Any]:
        sdf = f'<sdf version="1.7"><model name="{obstacle.entity_name}"><static>true</static><pose>{obstacle.x} {obstacle.y} {obstacle.z} 0 0 0</pose><link name="link"><collision name="collision"><geometry><box><size>{obstacle.size} {obstacle.size} {obstacle.size}</size></box></geometry></collision><visual name="visual"><geometry><box><size>{obstacle.size} {obstacle.size} {obstacle.size}</size></box></geometry><material><diffuse>0.9 0.05 0.05 1</diffuse></material></visual></link></model></sdf>'
        result = self._query(["gz", "service", "-s", "/world/house_v1/create", "--reqtype", "gz.msgs.EntityFactory", "--reptype", "gz.msgs.Boolean", "--timeout", "5000", "--req", f'sdf: "{sdf.replace(chr(34), chr(92) + chr(34))}"'], 8.0)
        return {"entity_name": obstacle.entity_name, "pose": {"x": obstacle.x, "y": obstacle.y, "z": obstacle.z}, "spawn_response": result, "success": "data: true" in result.casefold()}

    def entity_exists(self, entity_name: str) -> bool:
        result = self._query(["gz", "service", "-s", "/world/house_v1/pose/info", "--reqtype", "gz.msgs.Empty", "--reptype", "gz.msgs.Pose_V", "--timeout", "3000", "--req", ""], 5.0)
        return entity_name in result

    def remove_entity(self, entity_name: str) -> bool:
        result = self._query(["gz", "service", "-s", "/world/house_v1/remove", "--reqtype", "gz.msgs.Entity", "--reptype", "gz.msgs.Boolean", "--timeout", "5000", "--req", f'name: "{entity_name}" type: MODEL'], 8.0)
        return "data: true" in result.casefold()

    def close(self) -> None:
        if self.node is not None:
            self.node.destroy_node()
            import rclpy
            rclpy.shutdown()
        for item in reversed(self.processes):
            if item.process.poll() is None:
                try: os.killpg(item.process.pid, signal.SIGTERM); item.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try: os.killpg(item.process.pid, signal.SIGKILL)
                    except OSError: pass


@dataclass
class Local3DRuntime:
    """Minimal local Gazebo + TurtleBot4 runtime for the menu's preview option.

    This deliberately starts neither localisation nor Nav2.  The commands are
    the same ROS/Gazebo and TurtleBot4 spawn interfaces used by the existing
    house_v1 bringup scripts, with the spawn pose read from the accepted goal.
    """

    root: Path
    output_dir: Path
    processes: list[StartedProcess] = field(default_factory=list)
    cleanup_events: list[dict[str, Any]] = field(default_factory=list)
    requested_entity_name: str = "turtlebot4"
    detected_entity_name: str | None = None
    entity_creation_reported_success: bool = False
    entity_verification_attempted: bool = False
    entity_verification_method: str = "not_attempted"
    entity_query_confirmed: bool = False
    entity_query_timed_out: bool = False
    entity_query_result: CommandResult | None = None
    robot_simulation_interfaces_ready: bool = False
    stop_motor_service_ready: bool = False
    robot_control_stack_ready: bool = False
    control_readiness_warnings: list[str] = field(default_factory=list)

    @property
    def log_dir(self) -> Path:
        return self.output_dir / "logs"

    def preflight(self) -> dict[str, Any]:
        if shutil.which("ros2") is None:
            raise FinalDemoError("ROS 2 was not found in the current environment.")
        if shutil.which("gz") is None:
            raise FinalDemoError("Gazebo was not found in the current environment.")
        result = preflight(self.root, require_live_tools=True)
        ros_setup = Path("/opt/ros/jazzy/setup.bash")
        workspace_setup = self.root.parent / "install" / "setup.bash"
        if not ros_setup.is_file():
            raise FinalDemoError(f"ROS 2 setup file could not be found at: {ros_setup}")
        if not workspace_setup.is_file():
            raise FinalDemoError(f"The workspace setup file could not be found at: {workspace_setup}")
        packages: dict[str, str] = {}
        for package in ("ros_gz_sim", "ros_gz_bridge", "turtlebot4_gz_bringup"):
            completed = subprocess.run(["ros2", "pkg", "prefix", package], cwd=str(self.root), env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=8)
            if completed.returncode != 0:
                raise FinalDemoError(f"The required ROS package is not available: {package}")
            packages[package] = completed.stdout.strip()
        result.update({"ros_setup": str(ros_setup), "workspace_setup": str(workspace_setup), "packages": packages})
        return result

    def commands(self, charging_goal: SafeGoal, *, headless: bool = False) -> dict[str, list[str]]:
        mode = "-r -s" if headless else "-r"
        return {
            "gazebo": ["ros2", "launch", "ros_gz_sim", "gz_sim.launch.py", f"gz_args:={mode} {self.root / 'worlds' / 'house_v1.sdf'}"],
            "clock_bridge": ["ros2", "run", "ros_gz_bridge", "parameter_bridge", "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock"],
            "turtlebot4_spawn": ["ros2", "launch", "turtlebot4_gz_bringup", "turtlebot4_spawn.launch.py", "world:=house_v1", "use_sim_time:=true", "rviz:=false", "localization:=false", "slam:=false", "nav2:=false", f"x:={charging_goal.x}", f"y:={charging_goal.y}", "z:=0.05", f"yaw:={charging_goal.yaw}"],
        }

    def _start(self, name: str, command: list[str]) -> None:
        handle = (self.log_dir / f"{name}.log").open("w", encoding="utf-8")
        process = subprocess.Popen(command, cwd=str(self.root), env=os.environ.copy(), stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes.append(StartedProcess(process, name))
        time.sleep(1.0)
        if process.poll() is not None:
            tail = (self.log_dir / f"{name}.log").read_text(encoding="utf-8", errors="replace").splitlines()[-8:]
            suffix = " | ".join(tail) if tail else "no log output"
            raise FinalDemoError(f"{name} exited during startup. Log: {self.log_dir / f'{name}.log'}. Detail: {suffix}")

    def _record_query(self, result: CommandResult) -> None:
        """Keep diagnostic output out of the presenter terminal and in this run's logs."""
        if not self.log_dir.exists():
            return
        with (self.log_dir / "auxiliary_queries.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(result.as_dict(), sort_keys=True) + "\n")

    def _run(self, command: list[str], timeout_seconds: float) -> CommandResult:
        """Run a bounded query and always return a record instead of raising."""
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.root),
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
        except (FileNotFoundError, OSError) as exc:
            result = CommandResult(command, False, False, None, False, "", "", time.monotonic() - started, str(exc))
            self._record_query(result)
            return result
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            result = CommandResult(command, True, True, process.returncode, False, stdout, stderr, time.monotonic() - started)
        except subprocess.TimeoutExpired as exc:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                stdout, stderr = process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                stdout, stderr = process.communicate()
            def _text(value: str | bytes | None) -> str:
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else (value or "")
            stdout = _text(exc.stdout) + _text(stdout)
            stderr = _text(exc.stderr) + _text(stderr)
            result = CommandResult(command, True, True, process.returncode, True, stdout, stderr, time.monotonic() - started, f"timed out after {timeout_seconds:.1f} seconds")
        except OSError as exc:
            result = CommandResult(command, True, False, process.poll(), False, "", "", time.monotonic() - started, str(exc))
        self._record_query(result)
        return result

    def wait_until_house_ready(self, timeout_seconds: float = 45.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            if not self.processes or self.processes[0].process.poll() is not None:
                return False
            services = self._run(["gz", "service", "-l"], 5.0).stdout.splitlines()
            if "/world/house_v1/control" in services and "/world/house_v1/create" in services:
                return True
            time.sleep(0.5)
        return False

    def _verify_entity_with_scene_service(self) -> None:
        """Try the Gazebo Sim 8 world-scoped scene service exactly once.

        This is deliberately auxiliary: the spawn acknowledgement is the
        preview success criterion, while this query can improve diagnostics.
        """
        self.entity_verification_attempted = True
        command = [
            "gz", "service", "-s", "/world/house_v1/scene/info",
            "--reqtype", "gz.msgs.Empty", "--reptype", "gz.msgs.Scene",
            "--timeout", "2000", "--req", "",
        ]
        result = self._run(command, 3.0)
        self.entity_query_result = result
        self.entity_query_timed_out = result.timed_out
        if result.exit_code == 0 and not result.timed_out and re.search(rf"(?<![A-Za-z0-9_-]){re.escape(self.requested_entity_name)}(?![A-Za-z0-9_-])", result.stdout):
            self.detected_entity_name = self.requested_entity_name
            self.entity_query_confirmed = True
            self.entity_verification_method = "world_scene_info_service"
        else:
            self.entity_verification_method = "creation_acknowledgement"

    def wait_until_robot_spawned(self, timeout_seconds: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            gazebo = next((item for item in self.processes if item.name == "gazebo"), None)
            if gazebo is None or gazebo.process.poll() is not None:
                return False
            lines = self._spawn_log_lines()
            self.entity_creation_reported_success = any("entity creation successful" in line.casefold() for line in lines)
            if self.entity_creation_reported_success:
                self._verify_entity_with_scene_service()
                return True
            if self._fatal_spawn_error(lines):
                return False
            time.sleep(0.5)
        return False

    def _spawn_log_lines(self) -> list[str]:
        log_path = self.log_dir / "turtlebot4_spawn.log"
        if not log_path.is_file():
            return []
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    def _fatal_spawn_error(self, lines: list[str]) -> str | None:
        """Return only errors that invalidate creation, never control warnings."""
        fatal_markers = (
            "entity creation failed",
            "failed to create entity",
            "failed to load robot description",
            "xacro command failed",
            "failed to process xacro",
        )
        for line in lines:
            lowered = line.casefold()
            if any(marker in lowered for marker in fatal_markers):
                return line.strip()
        return None

    def _control_readiness(self) -> dict[str, Any]:
        """Inspect control interfaces without redefining entity creation success."""
        services = {line.strip() for line in self._run(["ros2", "service", "list"], 5.0).stdout.splitlines()}
        ros_topics = {line.strip() for line in self._run(["ros2", "topic", "list"], 5.0).stdout.splitlines()}
        gz_topics = {line.strip() for line in self._run(["gz", "topic", "-l"], 5.0).stdout.splitlines()}
        self.stop_motor_service_ready = any(service.rsplit("/", 1)[-1] == "stop_motor" for service in services)
        cmd_vel_ready = any("cmd_vel" in topic for topic in ros_topics | gz_topics)
        scan_ready = any("scan" in topic for topic in ros_topics | gz_topics)
        self.robot_simulation_interfaces_ready = "/clock" in ros_topics and cmd_vel_ready and scan_ready
        self.robot_control_stack_ready = self.stop_motor_service_ready and self.robot_simulation_interfaces_ready
        warnings: list[str] = []
        if not self.stop_motor_service_ready:
            warnings.append("Service stop_motor unavailable.")
        if not self.robot_simulation_interfaces_ready:
            warnings.append("Required robot simulation interfaces are not ready.")
        self.control_readiness_warnings = warnings
        return {
            "robot_simulation_interfaces_ready": self.robot_simulation_interfaces_ready,
            "stop_motor_service_ready": self.stop_motor_service_ready,
            "robot_control_stack_ready": self.robot_control_stack_ready,
            "control_readiness_warnings": list(warnings),
        }

    def spawn_diagnostics(self, charging_goal: SafeGoal, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
        item = next((entry for entry in self.processes if entry.name == "turtlebot4_spawn"), None)
        log_path = self.log_dir / "turtlebot4_spawn.log"
        lines = self._spawn_log_lines()
        self.entity_creation_reported_success = any("entity creation successful" in line.casefold() for line in lines)
        control = self._control_readiness() if self.entity_creation_reported_success else {
            "robot_simulation_interfaces_ready": False,
            "stop_motor_service_ready": False,
            "robot_control_stack_ready": False,
            "control_readiness_warnings": [],
        }
        return {
            "command": self.commands(charging_goal)["turtlebot4_spawn"], "started": item is not None,
            "exit_code": None if item is None else item.process.poll(), "first_error": self._fatal_spawn_error(lines),
            "requested_entity_name": self.requested_entity_name, "detected_entity_name": self.detected_entity_name,
            "requested_pose": {"x": charging_goal.x, "y": charging_goal.y, "z": 0.05, "yaw": charging_goal.yaw},
            "create_service": "/world/house_v1/create (via ros_gz_sim create)", "service_ready": self.wait_until_house_ready(0.01),
            "entity_creation_reported_success": self.entity_creation_reported_success,
            "entity_detected": self.detected_entity_name is not None,
            "robot_entity_spawned": self.entity_creation_reported_success,
            "entity_verification_attempted": self.entity_verification_attempted,
            "entity_verification_method": self.entity_verification_method,
            "entity_query_confirmed": self.entity_query_confirmed,
            "entity_query_timed_out": self.entity_query_timed_out,
            "entity_query_command": None if self.entity_query_result is None else self.entity_query_result.command,
            "entity_query_log": str(self.log_dir / "auxiliary_queries.jsonl"),
            "timeout_seconds": timeout_seconds, "log_path": str(log_path), **control,
        }

    def launch_house_and_robot(self, charging_goal: SafeGoal, *, headless: bool = False) -> dict[str, Any]:
        self.output_dir.mkdir(parents=True, exist_ok=False); self.log_dir.mkdir()
        commands = self.commands(charging_goal, headless=headless)
        self._start("gazebo", commands["gazebo"])
        if not self.wait_until_house_ready():
            raise FinalDemoError(f"The house_v1 world did not become ready. Log: {self.log_dir / 'gazebo.log'}")
        self._start("clock_bridge", commands["clock_bridge"])
        self._start("turtlebot4_spawn", commands["turtlebot4_spawn"])
        if not self.wait_until_robot_spawned():
            diagnostic = self.spawn_diagnostics(charging_goal)
            error = diagnostic["first_error"] or "The create process remained active but the requested entity was not listed."
            raise FinalDemoError(f"The robot could not be spawned. The first spawn error was: {error}. Full log: {diagnostic['log_path']}")
        diagnostic = self.spawn_diagnostics(charging_goal)
        if not diagnostic["robot_entity_spawned"]:
            raise FinalDemoError(f"The TurtleBot4 spawn launch did not acknowledge entity creation. Full log: {diagnostic['log_path']}")
        return {
            "commands": commands,
            "house_world_started": True,
            "house_world_ready": True,
            "robot_spawn_requested": True,
            "robot_spawned": True,
            "robot_entity_spawned": diagnostic["robot_entity_spawned"],
            "entity_creation_reported_success": diagnostic["entity_creation_reported_success"],
            "entity_verification_attempted": diagnostic["entity_verification_attempted"],
            "entity_verification_method": diagnostic["entity_verification_method"],
            "entity_query_confirmed": diagnostic["entity_query_confirmed"],
            "entity_query_timed_out": diagnostic["entity_query_timed_out"],
            "entity_query_command": diagnostic["entity_query_command"],
            "entity_query_log": diagnostic["entity_query_log"],
            "robot_simulation_interfaces_ready": diagnostic["robot_simulation_interfaces_ready"],
            "robot_control_stack_ready": diagnostic["robot_control_stack_ready"],
            "stop_motor_service_ready": diagnostic["stop_motor_service_ready"],
            "control_readiness_warnings": diagnostic["control_readiness_warnings"],
            "preview_available": True,
            "navigation_available": diagnostic["robot_control_stack_ready"],
            "robot_spawn": diagnostic,
            "charging_area_pose": {"x": charging_goal.x, "y": charging_goal.y, "yaw": charging_goal.yaw, "z": 0.05, "source": charging_goal.reference["proposal_id"]},
        }

    def wait_for_house_close(self) -> None:
        if self.processes:
            self.processes[0].process.wait()

    def shutdown(self) -> list[dict[str, Any]]:
        for item in reversed(self.processes):
            event = {"name": item.name, "pid": item.process.pid, "initial_status": item.process.poll(), "signal": None, "final_status": None}
            if item.process.poll() is None:
                try:
                    os.killpg(item.process.pid, signal.SIGINT); event["signal"] = "SIGINT"; item.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    try: os.killpg(item.process.pid, signal.SIGTERM); event["signal"] = "SIGTERM"; item.process.wait(timeout=5)
                    except (OSError, subprocess.TimeoutExpired):
                        try: os.killpg(item.process.pid, signal.SIGKILL); event["signal"] = "SIGKILL"
                        except OSError: pass
            event["final_status"] = item.process.poll(); self.cleanup_events.append(event)
        return list(self.cleanup_events)


def launch_house_preview(root: Path, output_dir: Path, *, headless: bool = False, runtime: Local3DRuntime | None = None, dry_run: bool = False) -> dict[str, Any]:
    """Launch only the house and robot, with truthful artifacts on every path."""
    regions, goals = safe_goals(root); del regions
    runtime = runtime or Local3DRuntime(root, output_dir)
    summary = {"live_runtime_selected": not dry_run, "house_world_started": False, "house_world_ready": False, "robot_spawn_requested": False, "robot_spawned": False, "robot_entity_spawned": False, "entity_creation_reported_success": False, "entity_verification_attempted": False, "entity_verification_method": "not_attempted", "entity_query_confirmed": False, "entity_query_timed_out": False, "entity_query_command": None, "entity_query_log": str(output_dir / "logs" / "auxiliary_queries.jsonl"), "robot_simulation_interfaces_ready": False, "robot_control_stack_ready": False, "stop_motor_service_ready": False, "control_readiness_warnings": [], "preview_available": False, "navigation_available": False, "cleanup_reason": "not_started", "charging_area_pose": {"x": goals["charging_area"].x, "y": goals["charging_area"].y, "yaw": goals["charging_area"].yaw, "z": 0.05, "source": goals["charging_area"].reference["proposal_id"]}, **synthetic_onboard_boundary()}
    commands = runtime.commands(goals["charging_area"], headless=headless); check: dict[str, Any] = {}; startup: dict[str, Any] = {}; cleanup: list[dict[str, Any]] = []; robot_spawn: dict[str, Any] = {"command": commands["turtlebot4_spawn"], "started": False, "exit_code": None, "first_error": None, "requested_entity_name": "turtlebot4", "detected_entity_name": None, "requested_pose": summary["charging_area_pose"], "create_service": "/world/house_v1/create (via ros_gz_sim create)", "service_ready": False, "entity_creation_reported_success": False, "entity_detected": False, "robot_entity_spawned": False, "entity_verification_attempted": False, "entity_verification_method": "not_attempted", "entity_query_confirmed": False, "entity_query_timed_out": False, "entity_query_command": None, "entity_query_log": summary["entity_query_log"], "robot_simulation_interfaces_ready": False, "stop_motor_service_ready": False, "robot_control_stack_ready": False, "control_readiness_warnings": [], "timeout_seconds": 30.0, "log_path": str(output_dir / "logs" / "turtlebot4_spawn.log")}
    try:
        check = runtime.preflight()
        if dry_run:
            startup = {"result": "dry_run", "house_world_started": False, "robot_spawned": False}
        else:
            startup = runtime.launch_house_and_robot(goals["charging_area"], headless=headless)
            summary.update({key: startup.get(key, summary[key]) for key in ("house_world_started", "house_world_ready", "robot_spawn_requested", "robot_spawned", "robot_entity_spawned", "entity_creation_reported_success", "entity_verification_attempted", "entity_verification_method", "entity_query_confirmed", "entity_query_timed_out", "entity_query_command", "entity_query_log", "robot_simulation_interfaces_ready", "robot_control_stack_ready", "stop_motor_service_ready", "control_readiness_warnings", "preview_available", "navigation_available", "charging_area_pose")})
            robot_spawn = startup.get("robot_spawn", robot_spawn)
    except FinalDemoError as exc:
        summary["failure_reason"] = str(exc)
        if isinstance(runtime, Local3DRuntime) and runtime.processes:
            summary["house_world_started"] = any(item.name == "gazebo" and item.process.poll() is None for item in runtime.processes)
            summary["house_world_ready"] = runtime.wait_until_house_ready(0.01)
            summary["robot_spawn_requested"] = any(item.name == "turtlebot4_spawn" for item in runtime.processes)
            robot_spawn = runtime.spawn_diagnostics(goals["charging_area"])
            summary.update({key: robot_spawn[key] for key in ("robot_entity_spawned", "entity_creation_reported_success", "entity_verification_attempted", "entity_verification_method", "entity_query_confirmed", "entity_query_timed_out", "entity_query_command", "entity_query_log", "robot_simulation_interfaces_ready", "robot_control_stack_ready", "stop_motor_service_ready", "control_readiness_warnings")})
            summary["robot_spawned"] = summary["robot_entity_spawned"]
            summary["preview_available"] = summary["house_world_ready"] and summary["robot_entity_spawned"]
            summary["navigation_available"] = summary["robot_control_stack_ready"]
    payload = {"preflight_check.json": check, "runtime_commands.json": commands, "house_startup.json": startup, "robot_spawn.json": robot_spawn, "cleanup.json": cleanup, "demo_summary.json": summary}
    if not output_dir.exists():
        output_dir.mkdir(parents=True)
    (output_dir / "logs").mkdir(exist_ok=True)
    for name, value in payload.items(): (output_dir / name).write_text(_compact(value), encoding="utf-8")
    if "failure_reason" in summary:
        blocking = "# house_v1 3D Preview Blocking Report\n\n" + "\n".join((
            f"- First blocking condition: {summary['failure_reason']}",
            f"- World process status: {summary['house_world_started']}",
            f"- Robot spawn process status: {summary['robot_spawned']}",
            f"- Runtime commands: {output_dir / 'runtime_commands.json'}",
            f"- Logs: {output_dir / 'logs'}",
            "- Manual reproduction: python3 scripts/run_final_3d_house_sitter_demo.py, then choose option 2.",
        )) + "\n"
        (output_dir / "blocking_report.md").write_text(blocking, encoding="utf-8")
    elif not summary["robot_control_stack_ready"] and summary["robot_entity_spawned"]:
        control_report = "# Robot Control Readiness Blocking Report\n\n" + "\n".join((
            "- Entity status: TurtleBot4 was created and detected in house_v1.",
            "- Warning requester: turtlebot4_node reported that stop_motor was unavailable.",
            "- Expected control stack: irobot_create_common_bringup starts the motion_control component; turtlebot4_gz_bringup also includes irobot_create_gz_bringup for Gazebo-facing simulation interfaces.",
            "- Warehouse reference: the reviewed TurtleBot4 warehouse launch uses the same turtlebot4_spawn stack and its Create 3 common/Gazebo components.",
            "- Minimal next step: verify the house_v1 world exposes the Create 3 stop_motor service through the same control/bridge configuration before enabling navigation.",
            "- This preview remains valid, but navigation is blocked until robot_control_stack_ready is true.",
        )) + "\n"
        (output_dir / "control_readiness_blocking_report.md").write_text(control_report, encoding="utf-8")
    return {"runtime": runtime, "summary": summary, "output_dir": output_dir}


def run_demo(root: Path, output_dir: Path, *, runtime: LiveRuntime | None, dry_run: bool, headless: bool, timeout_seconds: float) -> dict[str, Any]:
    regions, goals = safe_goals(root); before = create_house_v1_baseline(regions)
    run_id = f"{int(time.time() * 1000)}_{os.getpid()}"; obstacle = kitchen_obstacle_spec(regions, goals["kitchen"], run_id)
    check = preflight(root, require_live_tools=not dry_run)
    summary = {key: False for key in ("house_world_started", "robot_spawned", "nav2_ready", "kitchen_navigation_success", "obstacle_spawn_success", "anomaly_detection_success", "digital_twin_update_success", "alert_generation_success", "return_to_charge_success")}
    navigation = {"result": "not_executed"}; injected = {"entities": []}; observation: dict[str, Any] | None = None; anomalies: list[dict[str, Any]] = []; alerts: list[dict[str, Any]] = []; after = deepcopy(before); returned = {"result": "not_executed"}
    try:
        if dry_run:
            navigation = {"result": "dry_run", "goal": {"label": "kitchen", "x": goals["kitchen"].x, "y": goals["kitchen"].y}}
            injected = {"entities": [{"entity_name": obstacle.entity_name, "pose": {"x": obstacle.x, "y": obstacle.y, "z": obstacle.z}, "result": "dry_run"}]}
            returned = {"result": "dry_run", "goal": {"label": "charging_area", "x": goals["charging_area"].x, "y": goals["charging_area"].y}}
        else:
            if runtime is None: runtime = RosGazeboRuntime(root)
            if output_dir.exists():
                raise FinalDemoError(f"The output directory already exists: {output_dir}")
            (output_dir / "logs").mkdir(parents=True)
            runtime.start(headless=headless, charging_goal=goals["charging_area"], output_dir=output_dir)
            summary["house_world_started"] = summary["robot_spawned"] = True
            readiness = runtime.ready(timeout_seconds); check["readiness"] = readiness
            summary["nav2_ready"] = all(readiness.values())
            if not readiness.get("/stop_motor", False):
                raise FinalDemoError("The robot model is present, but its control services are not ready. Navigation cannot start until the simulation control interface is available.")
            if not summary["nav2_ready"]: raise FinalDemoError("Nav2 readiness checks did not pass; no navigation goal was sent.")
            navigation = runtime.navigate(goals["kitchen"], timeout_seconds); summary["kitchen_navigation_success"] = navigation.get("result") == "succeeded"
            if not summary["kitchen_navigation_success"]: raise FinalDemoError("Navigation to the kitchen failed; obstacle injection was not attempted.")
            spawned = runtime.spawn_obstacle(obstacle); injected = {"entities": [spawned]}; summary["obstacle_spawn_success"] = bool(spawned.get("success")) and runtime.entity_exists(obstacle.entity_name)
            if not summary["obstacle_spawn_success"]: raise FinalDemoError("The kitchen obstacle was not confirmed in Gazebo.")
            observation = make_observation(before, obstacle, navigation.get("final_pose")); anomalies = detect_anomalies(observation, room_index(before)["kitchen"]); after, update = update_room_from_observation(after, observation, anomalies)
            summary["anomaly_detection_success"] = bool(anomalies); summary["digital_twin_update_success"] = bool(update["changed_fields"]); alerts = actionable_alerts(anomalies); summary["alert_generation_success"] = bool(alerts)
            returned = runtime.navigate(goals["charging_area"], timeout_seconds); summary["return_to_charge_success"] = returned.get("result") == "succeeded"
            if not summary["return_to_charge_success"]: raise FinalDemoError("Return navigation to the charging area failed.")
    except FinalDemoError as exc:
        summary["failure_reason"] = str(exc)
    finally:
        if runtime is not None and not dry_run: runtime.close()
    summary.update({"scenario_id": SCENARIO_ID, "dry_run": dry_run, "simulation_only": True, "real_robot_supported": False, "synthetic": True, "simulated_onboard_sensor": True})
    report = "# House-Sitter Final 3D Demo\n\n" + "\n".join(f"- {key}: {value}" for key, value in summary.items()) + "\n\nThis is a simulation-only Gazebo orchestration. The anomaly observation is synthetic and is not physical sensor validation.\n"
    payload = {"demo_manifest.json": {"scenario_id": SCENARIO_ID, "artifact_names": list(ARTIFACT_NAMES), "obstacle_entity_name": obstacle.entity_name, **synthetic_onboard_boundary()}, "preflight_check.json": check, "navigation_to_kitchen.json": navigation, "injected_entities.json": injected, "simulated_observation.json": observation or {"result": "not_generated", **synthetic_onboard_boundary()}, "detected_anomalies.json": {"anomalies": anomalies, **synthetic_onboard_boundary()}, "digital_twin_before.json": before, "digital_twin_after.json": after, "actionable_alerts.json": {"alerts": alerts, **synthetic_onboard_boundary()}, "return_to_charge.json": returned, "demo_summary.json": summary, "demo_report.md": report}
    write_artifacts(output_dir, payload)
    return {"summary": summary, "output_dir": output_dir, "obstacle": obstacle, "navigation": navigation, "return": returned}
