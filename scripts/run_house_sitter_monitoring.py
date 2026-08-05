#!/usr/bin/env python3
"""Run the first complete, deterministic house-sitter monitoring scenario."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from house_sitter_core.house_sitter_patrol import (  # noqa: E402
    HouseSitterMonitoringError, render_monitoring_artifacts, run_house_sitter_patrol, write_monitoring_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="离线、确定性的 house_v1 环境监测演示。")
    parser.add_argument("--scenario", default="kitchen_unexpected_obstacle", help="正式监测场景标识")
    parser.add_argument("--output-dir", required=True, type=Path, help="新的 artifact 输出目录")
    args = parser.parse_args(argv)
    try:
        result = run_house_sitter_patrol(ROOT, args.scenario)
        paths = write_monitoring_artifacts(args.output_dir, render_monitoring_artifacts(result))
    except (HouseSitterMonitoringError, OSError, ValueError) as exc:
        print(f"环境监测演示无法完成：{exc}", file=sys.stderr)
        return 2
    summary = result["summary"]
    print("house_v1 环境监测完成（完全离线、simulation-only）。")
    print(f"覆盖房间：{summary['covered_rooms']}；异常：{summary['detected_anomaly_count']}；误报：{summary['false_positive_count']}。")
    print(f"已返回 charging_area：{summary['returned_to_charging_area']}；输出目录：{args.output_dir}")
    print("已生成：" + "、".join(path.name for path in paths.values()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
