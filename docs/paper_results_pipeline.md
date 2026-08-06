# 论文结果生成流程

该流程是只读的结果整理层：它读取冻结的 robustness、temporal filtering 和 patrol strategy artifact，或在隔离的临时目录中调用既有冻结流程重新生成 artifact。它不修改冻结场景、算法、ground truth、参数或指标。

研究定位保持为：

- robustness：Deterministic robustness and temporal Digital Twin evaluation
- temporal filtering：Precision–Recall–Latency trade-off
- patrol strategy：Untuned deterministic patrol-strategy baseline

全部结果均为 simulation-only。传感器、能耗与电池均为确定性模拟；固定重复用于验证确定性，而非独立随机样本。因此流程不计算 p-value、置信区间或统计显著性，也不将确定性重复描述为真实世界统计泛化能力。

使用已有 artifact：

```bash
python3 scripts/build_paper_results.py --robustness-dir <robustness_dir> --temporal-dir <temporal_dir> --patrol-dir <patrol_dir> --output-dir <output_dir>
```

重新生成冻结结果并整理：

```bash
python3 scripts/build_paper_results.py --regenerate --output-dir /tmp/house_sitter_paper_results
```

输出包含 manifest、统一 JSON、论文 Results 草稿、限制说明、CSV/Markdown/LaTex 表格及在可用时由 matplotlib 生成的 PNG/PDF 图表。若缺少 matplotlib，不安装依赖且不伪造图像，输出会明确记录依赖不可用。
