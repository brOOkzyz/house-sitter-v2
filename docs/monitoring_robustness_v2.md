# monitoring robustness benchmark v2

**Deterministic robustness and temporal Digital Twin evaluation** 用于测量当前系统在固定噪声、阈值边界、数据缺失、组合异常和异常恢复条件下的表现；它不是检测器优化，也不代表真实住宅、真实机器人或噪声传感器性能。

```bash
python3 scripts/evaluate_monitoring_robustness.py --output-dir /tmp/house_sitter_monitoring_robustness_v2 --repeats 5
```

20 个手工 ground truth 场景分为五类，每类四个；每个场景固定重复五次。评估器复用现有 simulated onboard sensors、environment monitoring、Digital Twin、actionable alerts 和 simulation boundary。缺失观测不会被当作正常值；发生缺失时记录 `insufficient_data`，不以无依据结果清除异常。恢复序列区分 active、resolved、stale 与 accepted baseline update。

输出八项原子 artifact，记录 event-level、字段级和时间序列指标。失败场景会保留在 `robustness_failures.json`，并给出一次性最小改进建议；不会修改 ground truth 或阈值来提高指标。

## Frozen pre-filtering robustness baseline

**Deterministic robustness and temporal Digital Twin evaluation**。

这是**未加入时间过滤的鲁棒性基线**：本轮结果是 temporal filtering 改进之前冻结的 baseline。当前检测器具有高 Recall，但会对短暂 layout signature 扰动过度敏感。后续时间过滤实验必须使用相同的 20 个场景和 100 次运行进行前后对照；本结果不得描述为真实机器人或真实住宅性能。

| 指标 | 冻结结果 |
| --- | ---: |
| scenarios | 20 |
| runs | 100 |
| Precision | 0.928571 |
| Recall | 1.0 |
| F1 | 0.962963 |
| noise false positive rate | 0.25 |
| threshold consistency | 1.0 |
| missing-data safe handling rate | 1.0 |
| combined anomaly exact-set accuracy | 1.0 |
| Digital Twin field update Precision | 0.925 |
| Digital Twin field update Recall | 1.0 |
| anomaly resolution accuracy | 1.0 |
| stale anomaly rate | 0.0 |
| deterministic repeat rate | 1.0 |
| failed scenarios | 1 |

唯一失败案例是短暂 layout_signature 扰动：错误结果为触发 layout_change 误报，连带产生不必要的 Digital Twin 字段更新。研究解释是检测器目前缺乏时间持续性确认。下一阶段候选改进是连续两次一致观测后才确认布局变化；本轮不得实现该改进。
