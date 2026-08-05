# house_v1 二维住宅可视化演示

运行以下命令即可开始会议演示：

```bash
python3 scripts/run_house_v1_visual_demo.py
```

这是 **2D deterministic residential simulation visualization**。它完全在本地读取
`maps/house_v1.*`、`local_annotations/house_v1/semantic_regions.json` 和
`local_annotations/house_v1/safe_goals.json`，不会启动或连接 ROS、Gazebo、Nav2 或 RViz。

## 演示流程

程序复用现有 `NaturalLanguageAdapter`、`SkillRequest`、planner 与 accepted safe-goal
验证链路，依次展示住宅地图、请求、解析与 planner 验证、目标房间、approved safe goal、
路径、连续动画，以及结构化结果。交互模式中每个阶段按 Enter 继续；空格暂停/继续动画，
`r` 从头演示，`q` 或 Esc 关闭窗口。

默认请求是“检查厨房”。还支持“检查卧室”、“去安全等待区”及 `Patrol the whole house`。
后者先经过既有 `patrol_home` planner 验证，再按正式 house_v1 展示配置依次可视化
`living_room → kitchen → bedroom → bathroom → charging_area`。

## 规划和安全边界

机器人从 accepted `charging_area_safe_goal` 开始。路径使用静态 occupancy map 上的确定性
八邻域 A*，并按 TurtleBot4 半径 0.22 m 加 0.10 m 安全余量保守膨胀障碍物；因此路径不会
穿过墙体或家具，且不会由语言层生成坐标。语言请求不明确或涉及真实机器人时，现有 adapter
会拒绝请求，程序不会启动动画。

每次运行都会新建临时输出目录，保留：

- `visual_demo_request.json`
- `visual_demo_plan.json`
- `visual_demo_result.json`
- `visual_demo_report.md`
- `final_frame.png`

会议备用 GIF 可用：

```bash
MPLBACKEND=Agg python3 scripts/run_house_v1_visual_demo.py --non-interactive --text "检查厨房" --export-gif
```

这会额外产生 `visual_demo.gif`。所有结果都明确标注为二维确定性可视化，
**不是 Gazebo/Nav2 execution artifact**；不会伪造 house_v1 的三维动态执行记录。
warehouse 仍然是项目此前已验证的真实 Gazebo/Nav2 回归环境。
