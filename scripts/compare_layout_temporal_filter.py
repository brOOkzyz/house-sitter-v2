#!/usr/bin/env python3
from __future__ import annotations
import argparse,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from house_sitter_core.temporal_filter_comparison import TemporalFilterError,compare,render,write
def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--output-dir',required=True,type=Path);p.add_argument('--repeats',type=int,default=5);a=p.parse_args(argv)
 try:c=compare(ROOT,a.repeats);paths=write(a.output_dir,render(c))
 except (TemporalFilterError,OSError,ValueError) as e:print(f'时间过滤对照无法完成：{e}',file=sys.stderr);return 2
 print(f"配对实验完成：每种策略 {len(c['trials'])//2} 次，总计 {len(c['trials'])} 次。输出："+'、'.join(x.name for x in paths.values()));return 0
if __name__=='__main__':raise SystemExit(main())
