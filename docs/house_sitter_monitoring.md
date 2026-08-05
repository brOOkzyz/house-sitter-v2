# house_v1 环境监测垂直切片

这一切片实现 Project 25 的第一条完整研究链路：空闲期住宅巡逻、模拟机载观测、环境异常检测、
Digital Twin 更新、可解释警报与结构化评估。它是确定性的本地仿真，不启动 ROS、Gazebo、Nav2 或 RViz，
也不访问网络、LLM、硬件或真实传感器。

```bash
python3 scripts/run_house_sitter_monitoring.py \
  --scenario kitchen_unexpected_obstacle \
  --output-dir /tmp/house_sitter_monitoring_demo
```

巡逻顺序为 `living_room → kitchen → bedroom → bathroom → charging_area`，之后逻辑上返回
`charging_area`。每个房间绑定 house_v1 的现有正式语义区域和 accepted safe goal；程序不生成坐标、
不发送动作目标，也不执行机器人移动。

模拟观测包括房间位置、距离/障碍物计数、简化温度、简化湿度和布局签名。每条观测均标记
`synthetic: true`、`simulated_onboard_sensor: true`、`simulation_only: true`。默认场景只在厨房注入
一个新的障碍物，其他房间保持基线，预期零误报。

Digital Twin 为每间房间记录最后观测步骤、温湿度、障碍物数量、布局签名、异常状态/类型、置信度与
可追溯观测来源。检测器提供 `unexpected_obstacle`、`temperature_out_of_range`、
`humidity_out_of_range` 和 `layout_change` 的最小可测试接口。厨房警报为：

> An unexpected obstacle was detected in the kitchen. Inspect the area before the next cleaning cycle.

输出目录必须是新的，程序通过临时同级目录和一次 rename 原子发布：`patrol_plan.json`、
`sensor_observations.jsonl`、Digital Twin 前后快照、异常/警报、摘要与 Markdown 报告。摘要包括覆盖率、
巡逻步骤、异常数和房间、以步骤计算的检测延迟、误报数、Twin 更新字段及返回充电区状态。
