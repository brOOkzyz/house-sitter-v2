# 导师研究演示

会议时唯一命令是：python3 scripts/run_supervisor_research_demo.py

程序通过 Path(__file__).resolve() 定位仓库。每步显示中文标题、简短英文说明、执行摘要和 [Enter] Continue [s] Skip [r] Retry [q] Quit；q 与 Ctrl+C 都会只清理本程序创建的 GUI 子进程，并保留本次 artifact 与 logs。

## 会前准备

可在会前运行：python3 scripts/build_paper_results.py --regenerate --output-dir /tmp/house_sitter_paper_results_final

默认演示不会重跑 570 次实验。若缓存不存在，实验步骤会退回文字摘要。可用 --paper-results-dir 指定缓存，--output-dir 指定本次演示目录，--start-at 8 从研究实验恢复，--skip-3d 与 --skip-2d 跳过可选预览，--list-steps 只列出步骤，--non-interactive 用于无 GUI smoke test，--prepare-paper-results 仅供会前准备。

## 十三个步骤

0. 环境预检 — “This demo uses the current simulation framework and previously generated experiment results.”
1. 项目目标和系统流程 — “The robot patrols the home during idle time, checks each room and updates the Digital Twin when it detects a change.”
2. 三维住宅静态预览（可选）— “This is the residential environment used in the project. The 3D view is only a static preview.”
3. 二维动态住宅巡逻（可选）— “The robot follows safe paths between labelled rooms and avoids walls and furniture.”
4. 完整监测场景 — “In this scenario, the robot patrols the home and detects a new obstacle in the kitchen.”
5. 异常和警报 — “The system explains what changed, where it happened and what action the user should take.”
6. Digital Twin 前后变化 — “Only the affected room receives anomaly updates in the Digital Twin. The other rooms retain normal status.”
7. 监测报告与返航 — “The report summarises the patrol, the detected anomaly and the robot’s return to the charging area.”
8. 鲁棒性实验 — “The robustness test detected all injected anomalies, but one temporary layout disturbance caused a false alarm.”
9. 时间过滤对照 — “Using two observations removes the temporary false alarm, but it also increases delay and misses some short-lived changes.”
10. 巡逻策略对照 — “Battery-aware patrol covers more rooms, while risk-priority patrol uses less distance and energy.”
11. 研究贡献和限制 — “The project provides a reproducible simulation and evaluation framework, but it has not yet been validated on a physical robot.”
12. 结束总结 — “The monitoring pipeline and the three main experiments are complete. The next stage is dissertation writing and final integration.”

三维步骤只调用 preview_house_v1_3d.sh 的静态住宅预览；二维步骤复用确定性 run_house_v1_visual_demo.py。两者失败时都会继续，不声称 house_v1 已完成动态 Gazebo/Nav2 导航，也不声称已经完成真实机器人或真实传感器验证。

步骤 4 的真实监测 artifact 位于本次目录的 monitoring_artifacts/，错误日志位于 logs/。图像打不开时程序给出绝对路径，随后继续演示。
