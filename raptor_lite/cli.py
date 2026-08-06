"""Command-line entry point for the Phase-1 pure Python task core."""
from __future__ import annotations

import argparse
from pathlib import Path

from .artifacts import write_run
from .capability_registry import CapabilityRegistry
from .executor import BackendExecutor, MockExecutor
from .house2d import EVENTS, House2DBackend
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    registry = CapabilityRegistry.from_yaml(args.profile)
    if args.command == "capabilities":
        print(f"Capability profile: {args.profile}")
        print(f"Declared capabilities: {len(registry.capabilities)}")
        for name in registry.capabilities: print(f"- {name}")
        return 0
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
        if args.backend == "mock": result, trace = MockExecutor().run(task, report, registry)
        else:
            declared_events = task.metadata.get("scenario_events", [])
            events = [] if args.no_events else args.event or (declared_events if isinstance(declared_events, list) else [])
            backend = House2DBackend(seed=args.seed, events=events, initial_battery=args.initial_battery)
            result, trace = BackendExecutor(backend).run(task, report, registry)
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
