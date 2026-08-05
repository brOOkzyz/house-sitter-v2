# 导师会议监督演示

从项目内或任意工作目录运行：

```bash
python3 /home/brookz/create3_ws/house_sitter_v2/scripts/run_supervisor_demo.py
```

程序按步骤暂停，并在每一步显示中文操作提示与简短英文讲解。输入 Enter 继续；`s` 跳过当前可选步骤；`r` 重试；`q` 安全退出并清理本程序启动的进程。

## 演示内容

1. 预检仓库、git 状态、Python、正式 semantic-regions/safe-goals artifact、自然语言 pipeline，以及可选 ROS2/Gazebo/Nav2 环境。
2. 使用既有 `NaturalLanguageAdapter` 解析自然语言请求，不生成坐标、不直接控制机器人。
3. 使用既有 natural-language pipeline 完成 dry-run：planner 和 accepted safe-goal 均会验证，`action_goals_sent` 必为 `0`。
4. 展示模糊请求和真实机器人请求的拒绝边界。
5. 仅在预检通过且用户继续时，使用现有 `bringup_headless_turtlebot4.sh`，再启动仓库已有文档/脚本中使用的 TurtleBot4 localization 与 Nav2 命令；全程 `use_sim_time=true`，无 GUI。
6. 可选地通过同一个自然语言 pipeline 发送 Gazebo 内的 `NavigateToPose`；目标只能来自 planner 绑定的 accepted safe-goal。
7. 展示结构化 artifact，并在存在 execution artifact 时调用既有 `evaluate_skill_execution.py`。

## Artifact 发现与安全边界

程序先检查正式的 `local_annotations/gui_demo_semantic_001` artifact 对，再按文件名搜索。它跳过目录、`tests/fixtures`、`build`、`install`、`log(s)`、虚拟环境和损坏 JSON；候选必须由现有 loader 和 planner 对“检查厨房”完成严格本地验证。多个有效组合会编号供选择。找不到时只显示中文诊断，不显示 traceback。

每次运行会创建新的系统临时目录，绝不覆盖旧输出；路径会打印在终端。默认模式仅 dry-run，且不导入 ROS/Nav2。可选执行也始终是 `simulation_only=true`、`real_robot_supported=false`：不发布 `/cmd_vel`，不发送 `/dock` 或 `/undock`，不连接真实机器人或硬件。

本程序用独立 process group 启动自身管理的子进程，并通过 `try/finally`、`atexit`、`SIGINT`/`SIGTERM` 清理；不会使用全局 `pkill`。`--keep-processes` 是唯一保留已启动仿真进程的显式选项。

## 自动化选项

```bash
python3 scripts/run_supervisor_demo.py --preflight-only
python3 scripts/run_supervisor_demo.py --dry-run-only
python3 scripts/run_supervisor_demo.py --text "检查厨房"
python3 scripts/run_supervisor_demo.py --keep-processes
python3 scripts/run_supervisor_demo.py --dry-run-only --non-interactive
```

`--non-interactive` 只供测试：它自动跳过可选仿真步骤，因此不会启动 ROS、Gazebo、Nav2 或 RViz。
