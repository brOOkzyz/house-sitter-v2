#!/usr/bin/env python3
"""Run monitoring robustness benchmark v2 without ROS or external services."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from house_sitter_core.monitoring_robustness_evaluation import RobustnessError, evaluate_robustness, render_robustness_artifacts, write_robustness_artifacts  # noqa: E402
def main(argv: list[str] | None=None) -> int:
    parser=argparse.ArgumentParser(description="离线 v2 监测鲁棒性评估。");parser.add_argument("--output-dir",required=True,type=Path);parser.add_argument("--repeats",type=int,default=5);args=parser.parse_args(argv)
    try: result=evaluate_robustness(ROOT,args.repeats); paths=write_robustness_artifacts(args.output_dir,render_robustness_artifacts(result))
    except (RobustnessError,OSError,ValueError) as exc: print(f"鲁棒性评估无法完成：{exc}",file=sys.stderr);return 2
    s=result["summary"];print(f"v2 鲁棒性基准完成：{s['scenario_count']} 个场景、{s['total_runs']} 次运行。");print(f"event Precision={s['event_precision']} Recall={s['event_recall']} F1={s['event_f1']}；失败场景={len(result['failures'])}。");print("已生成："+"、".join(x.name for x in paths.values()));return 0
if __name__=="__main__": raise SystemExit(main())
