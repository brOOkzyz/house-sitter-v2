#!/usr/bin/env python3
"""Single-command Phase-1 house-sitter 3D demonstration."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.final_3d_demo import FinalDemoError, SCENARIO_ID, launch_house_preview, run_demo  # noqa: E402


MENU = """House-Sitter 3D Demo

1. Run the kitchen obstacle demonstration
2. Launch the 3D house only
3. Reset the demonstration
4. Show the latest alert
5. Show the latest Digital Twin update
6. Return the robot to the charging area
7. Run a short motion test
q. Quit"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simulation-only Phase-1 3D house-sitter demonstration.")
    parser.add_argument("--scenario", choices=[SCENARIO_ID]); parser.add_argument("--headless", action="store_true")
    parser.add_argument("--output-dir", type=Path); parser.add_argument("--reset", action="store_true")
    parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--motion-test", action="store_true", help="launch the preview and run one bounded low-speed motion test")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--keep-world-on-failure", action="store_true", help="keep only this command's house preview open after a spawn failure")
    return parser.parse_args(argv)


def _output(path: Path | None) -> Path:
    return path if path is not None else Path(tempfile.mkdtemp(prefix="house_sitter_final_3d_demo_")) / "artifacts"


def _empty_motion_record(reason: str) -> dict[str, object]:
    return {
        "attempted": False, "requested_topic": "/cmd_vel", "resolved_command_topic": None,
        "command_message_type": None, "speed": 0.05, "duration": 1.0,
        "command_publish_count": 0, "zero_velocity_publish_count": 0,
        "control_command_published": False, "zero_velocity_sent": False,
        "initial_odom": None, "final_odom": None,
        "initial_gazebo_pose": None, "final_gazebo_pose": None,
        "odometry_received": False, "gazebo_pose_received": False,
        "displacement": None, "verification_source": "none", "movement_threshold": 0.01,
        "displacement_verified": False, "stop_verified": False,
        "robot_moved": False, "robot_stopped": False, "dds_warnings": [],
        "success": False, "failure_reason": reason,
    }


def _hold_motion_preview() -> None:
    print("The motion test has finished.")
    print("The Gazebo preview will remain open.")
    print("Press Enter to stop the preview and return to the menu.")
    print("Press Ctrl+C to exit safely.")
    try:
        input()
    except (EOFError, OSError):
        print("Input is unavailable; stopping the preview safely.")


def _run(args: argparse.Namespace) -> int:
    if args.reset:
        print("Reset is available only for a live process started by this command.")
        return 0
    if args.timeout <= 0:
        raise FinalDemoError("--timeout must be positive.")
    result = run_demo(ROOT, _output(args.output_dir), runtime=None, dry_run=args.dry_run, headless=args.headless, timeout_seconds=args.timeout)
    summary = result["summary"]
    if args.dry_run:
        print("Dry-run completed. No Gazebo, ROS, or GUI process was started.")
    else:
        print("Starting from the charging area.")
        print("Navigating to the kitchen observation point.")
        if summary["obstacle_spawn_success"]: print("An unexpected obstacle has been added to the kitchen.")
        if summary["anomaly_detection_success"]: print("The simulated observation reports a new obstacle in the kitchen.")
        print("Detected anomaly: unexpected_obstacle")
        print(f"Digital Twin updated: {'Yes' if summary['digital_twin_update_success'] else 'No'}")
        print(f"Alert generated: {'Yes' if summary['alert_generation_success'] else 'No'}")
        if summary["return_to_charge_success"]: print("The robot has returned to the charging area.")
    print(f"Artifacts are available at: {result['output_dir']}")
    if summary.get("failure_reason"):
        print(f"The live demonstration stopped safely: {summary['failure_reason']}", file=sys.stderr)
        return 2
    return 0


def _launch_house_only(args: argparse.Namespace) -> int:
    output = _output(args.output_dir)
    print("Starting the house_v1 3D environment...")
    print("Spawning the robot at the charging area...")
    result = launch_house_preview(ROOT, output, headless=args.headless, dry_run=args.dry_run)
    summary, runtime, output = result["summary"], result["runtime"], result["output_dir"]
    try:
        if summary.get("failure_reason"):
            spawn = json.loads((output / "robot_spawn.json").read_text(encoding="utf-8"))
            print("The house environment started successfully." if summary.get("house_world_ready") else "The house environment did not become ready.")
            print("The robot could not be spawned.")
            print(f"The first spawn error was: {spawn.get('first_error') or summary['failure_reason']}")
            print(f"Full log: {spawn.get('log_path')}")
            if args.keep_world_on_failure and summary.get("house_world_ready"):
                input("Press Enter to stop the preview.")
            return 2
        if args.dry_run:
            print("Dry-run completed. No Gazebo, ROS, or GUI process was started.")
            return 0
        print("The 3D house is ready.")
        if summary.get("entity_query_timed_out"):
            print("The robot spawn request completed successfully.")
            print("The additional entity verification timed out.")
            print("The preview will remain open, but navigation readiness has not been confirmed.")
        else:
            print("The robot has been spawned at the charging area.")
        if summary.get("robot_control_stack_ready"):
            print("Robot control services are ready.")
            if not summary.get("stop_motor_service_ready"):
                print("The optional TurtleBot4 HMI power service is unavailable.")
        elif not summary.get("entity_query_timed_out"):
            print("Robot control services are not ready yet.")
            print("This preview will remain open, but navigation is not available.")
        print("Close the Gazebo window or press Ctrl+C to stop the preview.")
        runtime.wait_for_house_close()
        summary["cleanup_reason"] = "preview_process_finished"
        return 0
    except KeyboardInterrupt:
        summary["cleanup_reason"] = "keyboard_interrupt"
        print("Exiting safely.")
        return 0
    finally:
        cleanup = runtime.shutdown()
        (output / "cleanup.json").write_text(json.dumps(cleanup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        (output / "demo_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Artifacts are available at: {output}")


def _run_short_motion_test(args: argparse.Namespace) -> int:
    output = _output(args.output_dir)
    print("Starting the house_v1 3D environment...")
    print("Spawning the robot at the charging area...")
    result = launch_house_preview(ROOT, output, headless=args.headless, dry_run=args.dry_run)
    summary, runtime, output = result["summary"], result["runtime"], result["output_dir"]
    motion = _empty_motion_record("The motion test was not started.")
    exit_code = 0
    try:
        if summary.get("failure_reason"):
            print("The house preview did not become available.")
            return 2
        if args.dry_run:
            motion = _empty_motion_record("Dry-run does not publish velocity commands.")
            print("Dry-run completed. No Gazebo, ROS, or GUI process was started.")
            return 0
        if not summary.get("robot_control_stack_ready"):
            motion = _empty_motion_record("The robot control stack is not ready.")
            print("The robot control stack is not ready. The motion test was not started.")
            return 2
        print("Robot control services are ready.")
        print("Running a short motion test...")
        motion = runtime.run_motion_smoke_test()
        if motion.get("success"):
            print("The robot moved successfully.")
            if motion.get("verification_source") == "ros_odometry":
                print("The displacement was verified using odometry.")
            else:
                print("No odometry message was available, so the displacement was verified using the Gazebo model pose.")
            print("The robot has stopped.")
        else:
            if motion.get("control_command_published") and motion.get("zero_velocity_sent"):
                print("The motion command was sent and the stop command was confirmed.")
                print("The displacement could not be verified from odometry or the Gazebo pose.")
                print("The preview will remain open for inspection.")
            else:
                print(f"The motion test did not complete: {motion.get('failure_reason')}")
            exit_code = 2
        if motion.get("dds_warnings"):
            print("A DDS shared-memory transport warning was recorded.")
            print("The motion verification will continue using the available transport.")
        _hold_motion_preview()
        summary["cleanup_reason"] = "motion_preview_stopped_by_user"
        return exit_code
    except KeyboardInterrupt:
        summary["cleanup_reason"] = "keyboard_interrupt"
        if hasattr(runtime, "send_zero_velocity"):
            extra_zeros = runtime.send_zero_velocity()
            motion["zero_velocity_publish_count"] = int(motion.get("zero_velocity_publish_count", 0)) + extra_zeros
            motion["zero_velocity_sent"] = bool(motion.get("zero_velocity_sent")) or extra_zeros > 0
        print("Exiting safely.")
        return 0
    finally:
        (output / "motion_test.json").write_text(json.dumps(motion, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary["motion_test_success"] = bool(motion.get("success"))
        (output / "demo_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        interfaces = getattr(runtime, "control_interfaces", None)
        if isinstance(interfaces, dict) and interfaces:
            (output / "control_interfaces.json").write_text(json.dumps(interfaces, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        cleanup = runtime.shutdown()
        (output / "cleanup.json").write_text(json.dumps(cleanup, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Artifacts are available at: {output}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.motion_test:
            return _run_short_motion_test(args)
        if args.dry_run:
            return _launch_house_only(args)
        if args.scenario:
            return _run(args)
        print(MENU)
        choice = input("Select an option: ").strip().casefold()
        if choice == "q": print("Exiting safely."); return 0
        if choice == "1": args.scenario = SCENARIO_ID; return _run(args)
        if choice == "2": return _launch_house_only(args)
        if choice == "7": return _run_short_motion_test(args)
        print("This Phase-1 option is not implemented yet.")
        return 0
    except (FinalDemoError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Full details are available in the run logs.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Exiting safely.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
