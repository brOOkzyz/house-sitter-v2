# 导师会议监督演示

会议时只需运行：

```bash
python3 scripts/run_supervisor_demo.py
```

这是稳定的离线主演示。它不启动、不停止、也不配置 Gazebo、TurtleBot4、localization、AMCL、Nav2 或任何外部 ROS 进程；不发布 `/cmd_vel`，不连接真实机器人或硬件。

## 默认步骤

1. 仓库与正式 semantic-regions / safe-goals artifact 预检。
2. 解析“检查厨房”。
3. 完整 dry-run：现有 planner 验证 accepted safe-goal，`action_goals_sent=0`。
4. 展示模糊请求与真实机器人请求的拒绝边界。
5. 展示本次结构化 pipeline artifact。
6. 只读展示已有 `patrol_home` 成果与离线评估入口。
7. 可选 attach-only 实时仿真与总结。

前六项不依赖 ROS，因而在没有 ROS/Gazebo/Nav2 的机器上也可完整演示。每步都有中文操作提示和简短英文讲解。

## Artifact 与历史证据

程序优先读取 `local_annotations/gui_demo_semantic_001` 的正式 artifact，再按文件名搜索。目录、`tests/fixtures`、`build`、`install`、`log(s)`、虚拟环境和损坏 JSON 都会跳过；每一对候选都必须由现有 loader 和 planner 完成严格本地验证。每次运行使用新的临时目录，绝不覆盖旧输出。

步骤 5 只读展示 `docs/final_demo_evidence.md` 中的 **Previously validated Gazebo/Nav2 run**：`living_room → kitchen → bedroom → charging_area`、4 个顺序 goal、最终 `succeeded`，以及 30–300 秒自适应 timeout 策略。它不伪造新的 execution artifact。已有完整 execution artifact 可由 `scripts/evaluate_skill_execution.py` 离线转换为 CSV、JSON 和 Markdown。

## 可选 attach-only 实时仿真

默认最后询问“是否检查已经启动的实时仿真环境？[y/N]”。选择 `y` 后，程序最多用 10 秒执行只读检查；每项 probe 最多 3 秒：`/navigate_to_pose`、三个 Nav2 lifecycle 节点、`map→odom` 和 `use_sim_time=true`。

实时仿真需要提前启动 Gazebo、localization 和 Nav2。本程序只连接已就绪环境，不负责启动或清理它们。环境未就绪时立即回退到离线展示，不重试、不显示 traceback。环境就绪时，用户可选择从自然语言 pipeline 执行“检查厨房”；目标仍只能来自 planner-approved safe-goal。收到首条 feedback 后可选择取消、继续等待，或在发送 goal 前返回离线主演示。

```bash
python3 scripts/run_supervisor_demo.py --offline-only
python3 scripts/run_supervisor_demo.py --preflight-only
python3 scripts/run_supervisor_demo.py --dry-run-only
python3 scripts/run_supervisor_demo.py --offline-only --non-interactive
```

`--offline-only` 完全不执行任何 ROS 命令，适合稳定演示与自动测试。
