#!/usr/bin/env python3
"""Evaluate the deterministic house_v1 monitoring scenario benchmark offline."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.monitoring_benchmark import (  # noqa: E402
    MonitoringBenchmarkError, evaluate_benchmark, render_benchmark_artifacts, write_benchmark_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线确定性 house_v1 监测基准评估。")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        benchmark = evaluate_benchmark(ROOT, args.repeats)
        paths = write_benchmark_artifacts(args.output_dir, render_benchmark_artifacts(benchmark))
    except (MonitoringBenchmarkError, OSError, ValueError) as exc:
        print(f"监测基准评估无法完成：{exc}", file=sys.stderr)
        return 2
    summary = benchmark["summary"]
    print(f"监测基准完成：{summary['scenario_count']} 个场景、{summary['total_runs']} 次运行。")
    print(f"Precision={summary['anomaly_detection_precision']} Recall={summary['anomaly_detection_recall']} F1={summary['anomaly_detection_f1']}。")
    print(f"输出目录：{args.output_dir}；已生成：" + "、".join(path.name for path in paths.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
