# house_v1 多异常监测基准 v1

该基准是 **Controlled deterministic functional validation**：它复用现有 house-sitter 巡逻、模拟机载传感器、
异常检测、Digital Twin 和统一 simulation boundary，用于验证受控、确定性、无噪声条件下的端到端功能正确性，
不复制任何检测逻辑。它包含 15 个人工定义 ground truth 的独立场景：normal_control、
unexpected_obstacle、layout_change、temperature_out_of_range、humidity_out_of_range 各 3 个。

当前 1.0 指标不代表真实住宅、真实机器人或噪声传感器性能，也不表示真实世界准确率达到 100%。后续鲁棒性实验将覆盖噪声、
临界阈值、数据缺失、组合异常和异常恢复。

```bash
python3 scripts/evaluate_monitoring_scenarios.py \
  --output-dir /tmp/house_sitter_monitoring_benchmark_v1 \
  --repeats 3
```

每个场景至少运行三次，并严格比较 patrol plan、传感器观测、异常、警报、Digital Twin after 和摘要；
若结果不一致，`deterministic_result` 为 false。normal_control 的 ground truth 不含正样本，因此类别
recall 与 F1 明确写为 `null` / `not_applicable`，不会伪造召回率。

输出通过同级临时目录和一次 rename 原子发布：试验 CSV、场景结果、汇总 JSON/Markdown、失败记录和
混淆矩阵 CSV。每个输出均标记 `synthetic: true`、`simulated_onboard_sensor: true`、
`simulation_only: true`、`real_robot_supported: false`。失败不会通过修改 ground truth 或阈值隐藏，
而保留在 `monitoring_failures.json` 供下一研究阶段处理。
