# house_v1：本地住宅仿真

`house_v1` 是为最终展示新增的单层住宅，不复用或重命名 warehouse 的区域。世界文件 [house_v1.sdf](../worlds/house_v1.sdf) 仅由 SDF primitive 组成：地板、墙体、门洞、沙发、餐桌、厨房台面、床、卫生间设施和入口柜；不包含 Gazebo Fuel URL 或运行时下载模型。

## 布局与坐标

住宅外框为 `12 m × 10 m`，map frame 的 origin 是 `[0.0, 0.0, 0.0]`。入口在东北侧；中央走廊横向连接客厅、厨房、卧室和卫生间。客厅位于西南，厨房位于东南，卧室位于西北，卫生间位于中北，充电点位于客厅东北侧靠墙的清晰停靠区。所有室内门洞宽 `1.3–1.5 m`，适合 TurtleBot4 的导航净空。

`maps/house_v1.yaml` 与 `maps/house_v1.pgm` 为确定性静态 occupancy map，尺寸 `240 × 200`、分辨率 `0.05 m/pixel`，对应同一 `12 m × 10 m` 平面。`scripts/generate_house_v1_assets.py` 是生成地图和标注 JSON 的本地确定性来源；提交后的产物无需 SLAM 或网络。

## 语义与 safe goals

`local_annotations/house_v1/semantic_regions.json` 包含真实几何区域：`entrance` 通过走廊连通，导航语义区域为 `living_room`、`kitchen`、`bedroom`、`bathroom`、`hallway` 和 `charging_area`。每个区域均有唯一、非空 polygon。

`safe_goals.json` 使用 `house_v1` map identity，包含六个已接受的 goal：`living_room_safe_goal`、`kitchen_safe_goal`、`bedroom_safe_goal`、`bathroom_safe_goal`、`hallway_safe_goal` 和 `charging_area_safe_goal`。它们均位于对应 polygon 和占据地图自由空间，保留 review-only、simulation-only 和 `real_robot_supported: false` 边界；不会修改自然语言适配器、planner 或 Nav2 bridge。

## 最终展示架构

house_v1 的正式展示采用“住宅二维导航演示 + 住宅三维静态预览”。二维部分使用已提交的 occupancy map、语义区域和 accepted safe goals，通过现有确定性 review sequencer 展示 `living_room → kitchen → bedroom → charging_area` 的 planner-approved 路径与评估输入：

```bash
python3 scripts/run_simulation_sequence.py \
  --semantic-regions local_annotations/house_v1/semantic_regions.json \
  --safe-goals local_annotations/house_v1/safe_goals.json \
  --output-dir local_annotations/house_v1_runs/sequence_001
```

该输出是明确标记为 simulation-only、review-only、non-executable 的二维规划/逻辑评估，不是 Gazebo execution artifact，也不发送 ROS 或 Nav2 goal。三维 `house_v1.sdf` 保留为本地住宅布局的静态预览；它不构成机器人运行成功的证据。warehouse 继续保留为已有真实 Gazebo/Nav2 导航回归环境。

本环境在本机唯一的 60 秒 headless smoke 中，Gazebo 在 TurtleBot4 控制器激活后以 exit code 139 退出，并留下 core dump；因此最终展示不依赖 house_v1 内的 Gazebo TurtleBot4 运行，也不伪造真实 Gazebo 执行结果。

## 可选静态预览

```bash
scripts/bringup_house_v1_headless.sh
scripts/bringup_house_v1_gui.sh
```

headless 与 GUI 脚本保留为仅限诊断/静态场景预览的本地工具；它们都不启动 localization、SLAM、Nav2 或 RViz，也不发布 `/cmd_vel` 或发送 navigation goal。由于上述已记录的 exit code 139，正式展示不以其运行结果作为执行声明。
