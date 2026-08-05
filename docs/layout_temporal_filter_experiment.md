# layout temporal filter experiment

本实验严格配对比较 `pre_filtering` 与 `two_observation_confirmation`：相同 20 个 v2 场景、相同固定 profile、每种策略每场景 5 次，共 200 次。无过滤策略直接复用冻结 v2 基线；过滤策略仅要求同一房间连续两次出现同一非基线 signature 才正式确认 layout_change。

首次变化只产生 `confirmed=false` 审计候选，不产生正式 anomaly、alert 或 Twin 布局更新。候选恢复基线时清除；不同 signature 或不同房间绝不合并。温度、湿度、障碍物、缺失与恢复逻辑仍复用原模块。

## Frozen paired comparison results

两种策略分别运行 100 次，总计 200 次；同一 scenario/repeat 使用完全相同的观测序列。`pre-filtering` 是冻结基线；`two_observation_confirmation` 是可配置候选策略，不替代 baseline，也不作为默认策略。结果体现 **Precision–Recall–Latency trade-off**，而不是 filtered 全面优于 baseline。

| Metric | pre-filtering | two_observation_confirmation |
|---|---:|---:|
| Runs | 100 | 100 |
| Precision | 0.928571 | 1.0 |
| Recall | 1.0 | 0.923077 |
| F1 | 0.962963 | 0.96 |
| Noise false positive rate | 0.25 | 0.0 |
| Layout-change Precision | 1.0 | 1.0 |
| Layout-change Recall | 1.0 | 0.666667 |
| Digital Twin field update Precision | 0.925 | 1.0 |
| Digital Twin field update Recall | 1.0 | 0.972973 |
| Unintended field updates | 3 | 0 |
| Combined anomaly exact-set accuracy | 1.0 | 0.75 |
| Recovery accuracy | 1.0 | 0.75 |
| Stale anomaly rate | 0.0 | 0.0 |
| Deterministic repeat rate | 1.0 | 1.0 |

mean layout detection latency 增加 0.5 个有效观测周期；noise false positive rate 从 0.25 降至 0；unintended Twin updates 从 3 降至 0。Precision 和 Twin update Precision 提高，但 Recall、layout-change Recall、combined exact-set accuracy 和 recovery accuracy 下降；总体 F1 从 0.962963 轻微下降至 0.96。

两个失败场景均完整保留，且不是测试基础设施错误：

1. 单次组合异常中的 layout change 因未获得第二次一致观测而漏检。
2. layout recovery 的确认存在额外时间序列延迟，导致 recovery accuracy 降低。

The two-observation confirmation policy removes transient layout-noise false positives and unintended Digital Twin updates, but reduces sensitivity to short-lived genuine layout events and introduces additional confirmation latency.

高敏感度场景可使用 `pre-filtering`；优先避免误报和误更新时可使用 `two_observation_confirmation`。实际部署应根据漏报代价和误报代价选择策略。当前实验为确定性仿真结果，不代表真实机器人性能。
