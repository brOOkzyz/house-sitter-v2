"""Command-line entry point for the Phase-1 pure Python task core."""
from __future__ import annotations

import argparse
from pathlib import Path

from .artifacts import write_run
from .capability_registry import CapabilityRegistry
from .executor import MockExecutor
from .task_schema import load_task
from .verifier import verify_task


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RaPToR-Lite simulation-first task verification.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("capabilities", "validate"):
        command = sub.add_parser(name); command.add_argument("task", nargs="?" if name == "capabilities" else None, type=Path); command.add_argument("--profile", required=True, type=Path)
    run = sub.add_parser("run"); run.add_argument("task", type=Path); run.add_argument("--profile", required=True, type=Path); run.add_argument("--executor", choices=["mock"], required=True)
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
    if report.approved:
        result, trace = MockExecutor().run(task, report)
    output = write_run(Path("artifacts") / "raptor_lite", task, registry.as_json(), report, result, trace)
    print(f"Task: {task.name}")
    print(f"Verification result: {'approved' if report.approved else 'rejected'}")
    print(f"Execution result: {'success' if result and result.success else 'not executed'}")
    print(f"Artifact directory: {output}")
    return 0 if result and result.success else 2


if __name__ == "__main__":
    raise SystemExit(main())
