# house_v1 空闲期巡逻策略实验

本实验是 **Untuned deterministic patrol-strategy baseline**：本地、确定性、simulation-only 的资源分配评估。三种策略没有根据本轮实验结果进行调参；不启动 ROS、Gazebo、Nav2 或 RViz，也不代表真实 TurtleBot4 续航测试。

三种策略固定为：`fixed_order` 按 living_room → kitchen → bedroom → bathroom → charging_area；`risk_priority` 仅按预定义房间风险、A* 距离和稳定 room_id 排序；`battery_aware` 只访问在“去程 + 观测 + A* 保证返航 + 安全储备”内可负担的房间。

场景文件包含六种人工定义的监测情况和 high / medium / constrained 三种统一初始电量，合计 18 个场景。每场景每策略运行五次，形成 270 次配对运行。风险画像 `house_idle_risk_profile_v1` 在异常注入之前定义，不包含实际异常位置；三种策略的选择接口不接收 `injected_events` 或 `expected_ground_truth_events`，因此不存在 ground-truth 泄漏。

所有路径距离均由 `house_v1` occupancy map、accepted safe goals 和现有二维视觉演示的保守栅格 A* 得出；不使用直线穿墙距离，也没有第二套路径规划器。`battery_aware` 使用场景文件集中定义的确定性模拟能耗模型：距离行驶项、每房间观测项和固定启动开销；模拟能耗不是真实 TurtleBot4 或商业扫地机器人的电池测量。

每次运行把未访问房间的 ground-truth 异常记录为 `missed_due_to_patrol_policy=true`，与已访问房间的检测器漏报 `detector_false_negative_count` 分开。监测调用仍统一使用 `layout_filter_policy="none"`、既有 `observe_room` 和 `detect_anomalies`，本轮不调整检测器或时间过滤。

结果解释保持为策略 trade-off，而非优化结论：`battery_aware` 以较高能耗换取更高覆盖率和异常发现率；`risk_priority` 距离和能耗较低，但覆盖率较低；`fixed_order` 是简单、稳定、可预测的中间基线。三种策略均实现 100% 安全返航；漏掉的异常来自未访问房间，而不是检测器 false negative。

输出包含逐次 trial、场景代表结果、按策略/电量分层汇总、相对 `fixed_order` 的同场景同 repeat 配对差值、三类 CSV Pareto 标记、失败记录与 JSONL 路线追踪。Pareto 标记只呈现 coverage/energy、latency/energy、discovery/distance 的非支配点，不声明任一策略全面最优。

本实验未包含真实住宅中的动态人员、传感器噪声、打滑、定位漂移或真实电池衰减。当前结果只用于分析仿真中的策略 trade-off，不代表真实部署性能。

运行：

```bash
python3 scripts/evaluate_patrol_strategies.py --output-dir /tmp/house_sitter_patrol_strategy_experiment --repeats 5
```
