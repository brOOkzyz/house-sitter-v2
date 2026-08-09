"""Command-line entry point for the Phase-1 pure Python task core."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .artifacts import write_planning_run, write_run
from .capability_registry import CapabilityRegistry
from .create3_ros2 import Create3ROS2Backend
from .executor import BackendExecutor, MockExecutor
from .house2d import EVENTS, House2DBackend
from .models import ExecutionResult, VerificationReport
from .planner import OfflineHouseSitterPlanner
from .task_schema import load_task
from .verifier import verify_task


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RaPToR-Lite simulation-first task verification.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("capabilities", "validate"):
        command = sub.add_parser(name); command.add_argument("task", nargs="?" if name == "capabilities" else None, type=Path); command.add_argument("--profile", required=True, type=Path)
    run = sub.add_parser("run"); run.add_argument("task", type=Path); run.add_argument("--profile", required=True, type=Path)
    run.add_argument("--backend", choices=["mock", "house2d"], default="mock"); run.add_argument("--executor", choices=["mock"])
    run.add_argument("--seed", type=int); run.add_argument("--event", action="append", choices=sorted(EVENTS), default=[]); run.add_argument("--no-events", action="store_true"); run.add_argument("--initial-battery", type=float)
    for name in ("plan", "run-text"):
        command = sub.add_parser(name); command.add_argument("--text", required=True); command.add_argument("--profile", required=True, type=Path)
        if name == "run-text":
            command.add_argument("--backend", choices=["mock", "house2d"], default="mock"); command.add_argument("--seed", type=int); command.add_argument("--event", action="append", choices=sorted(EVENTS), default=[]); command.add_argument("--no-events", action="store_true"); command.add_argument("--initial-battery", type=float)
    readiness = sub.add_parser("deployment-readiness", help="Discover a ROS 2 graph without commanding a robot.")
    readiness.add_argument("--plan", type=Path, help="Optional read-only JSON plan to verify against discovered capabilities.")
    readiness.add_argument("--output", type=Path, help="Optional JSON report path.")
    return parser


def _execute(task, report, args):
    backend = None; trace = []
    if args.backend == "mock":
        result, trace = MockExecutor().run(task, report, args.registry)
    else:
        declared_events = task.metadata.get("scenario_events", [])
        events = [] if args.no_events else args.event or (declared_events if isinstance(declared_events, list) else [])
        backend = House2DBackend(seed=args.seed, events=events, initial_battery=args.initial_battery)
        result, trace = BackendExecutor(backend).run(task, report, args.registry)
    return result, trace, backend


def _print_planning(planning, report, result, output) -> None:
    print(f"Planning status: {planning.status}")
    print(f"Interpreted intent: {planning.detected_intent or 'not determined'}")
    print(f"Extracted rooms: {', '.join(planning.extracted_rooms) or 'none'}")
    print(f"Extracted checks: {', '.join(planning.extracted_checks) or 'none'}")
    additions = [f"{step} ({planning.automatic_addition_reasons[step]})" for step in planning.automatically_added_steps]
    print(f"Automatic safety additions: {', '.join(additions) or 'none'}")
    if planning.clarification_questions:
        print(f"Clarification: {' '.join(planning.clarification_questions)}")
    if planning.unsupported_elements:
        print(f"Unsupported elements: {', '.join(planning.unsupported_elements)}")
    print(f"Verification result: {'approved' if report.approved else 'not approved'}")
    print(f"Execution result: {'success' if result and result.success else 'failed' if result else 'not attempted'}")
    print(f"Artifact directory: {output}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "deployment-readiness":
        backend = Create3ROS2Backend()
        try:
            discovery = backend.discover()
            payload = discovery.profile()
            if args.plan:
                task = load_task(args.plan)
                report = verify_task(task, discovery.registry())
                payload["plan"] = {"path": str(args.plan), "approved": report.approved, "issues": [item.model_dump(mode="json") for item in report.issues]}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0 if not args.plan or payload["plan"]["approved"] else 2
        except Exception as exc:
            print(json.dumps({"backend": "create3_ros2", "physical_robot_validated": False, "error": str(exc)}, indent=2, sort_keys=True))
            return 3
        finally:
            backend.cleanup()
    registry = CapabilityRegistry.from_yaml(args.profile)
    args.registry = registry
    if args.command == "capabilities":
        print(f"Capability profile: {args.profile}")
        print(f"Declared capabilities: {len(registry.capabilities)}")
        for name in registry.capabilities: print(f"- {name}")
        return 0
    if args.command in {"plan", "run-text"}:
        planning = OfflineHouseSitterPlanner(registry).plan(args.text)
        report = VerificationReport(approved=False, safety_summary=["Planning did not produce a candidate task."])
        result = None; trace = []; backend = None
        if planning.status == "planned" and planning.candidate_task is not None:
            report = verify_task(planning.candidate_task, registry)
            if args.command == "run-text" and report.approved:
                result, trace, backend = _execute(planning.candidate_task, report, args)
        output = write_planning_run(Path("artifacts") / "raptor_lite", planning, registry.as_json(), report, result, trace, backend)
        _print_planning(planning, report, result, output)
        if planning.status == "needs_clarification": return 3
        if planning.status == "unsupported": return 5
        if planning.status == "invalid": return 6
        if not report.approved: return 4
        return 0 if args.command == "plan" or result and result.success else 2
    task = load_task(args.task)
    report = verify_task(task, registry)
    if args.command == "validate":
        output = write_run(Path("artifacts") / "raptor_lite", task, registry.as_json(), report)
        print(f"Task: {task.name}")
        print(f"Verification result: {'approved' if report.approved else 'rejected'}")
        if report.issues:
            for issue in report.issues: print(f"- {issue.issue_code}: {issue.message}")
        else: print("Issue summary: no issues.")
        print(f"Artifact directory: {output}")
        return 0 if report.approved else 2
    result = None; trace = []
    backend = None
    if report.approved:
        if args.executor and args.backend != "mock":
            raise ValueError("--executor mock cannot be combined with a non-mock backend.")
        result, trace, backend = _execute(task, report, args)
    output = write_run(Path("artifacts") / "raptor_lite", task, registry.as_json(), report, result, trace, backend)
    print(f"Task: {task.name}")
    print(f"Verification result: {'approved' if report.approved else 'rejected'}")
    if args.backend == "house2d" and report.approved: print("Starting the baseline patrol.")
    status = "success" if result and result.success else "failed" if result else "not executed"
    print(f"Execution result: {status}")
    if backend is not None:
        bundle = backend.artifact_bundle()
        completed = {item.skill for item in (result.step_results if result else []) if item.success}
        if "inject_household_events" in completed: print("Controlled event injection completed.")
        if "revisit_active_event_rooms" in completed: print("Room revisit completed.")
        print(f"Detected changes: {len(bundle.get('detected_anomalies', []))}")
        print(f"Digital Twin updates: {sum(1 for item in bundle.get('digital_twin_updates', []) if item.get('updated'))}")
        print(f"Alerts: {len(bundle.get('actionable_alerts', []))}")
        print(f"Returned to charging area: {bundle.get('final_world_state', {}).get('room') == 'charging_area'}")
        print(f"Monitoring report: {'generated' if bundle.get('monitoring_report') else 'not generated'}")
    if backend is not None: print(f"Scenario seed: {backend.seed}")
    print(f"Artifact directory: {output}")
    return 0 if result and result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
