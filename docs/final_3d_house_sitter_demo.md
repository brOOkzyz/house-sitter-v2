# Final 3D Demo Phase 1

## Phase 1A purpose and command

Phase 1A connects the interactive preview only. Run:

```bash
python3 scripts/run_final_3d_house_sitter_demo.py
```

Then choose `2. Launch the 3D house only`. The program starts the local `house_v1` Gazebo world and the installed TurtleBot4 spawn launch at the committed charging-area accepted safe goal. It does not start Nav2, send a navigation goal, inject an obstacle, run anomaly detection, update the Digital Twin, or generate an alert in this phase.

Use `--headless` to omit the Gazebo GUI, `--timeout SECONDS` to bound readiness and navigation, and `--output-dir PATH` for a new result directory. The safe preflight path is:

```bash
python3 scripts/run_final_3d_house_sitter_demo.py \
  --scenario kitchen_unexpected_obstacle --dry-run \
  --output-dir /tmp/house_sitter_final_3d_demo_dry_run
```

Dry-run validates the local launch resources and accepted charging-area goal, creates preview artifacts, and starts no ROS, Gazebo, or GUI process.

## Current architecture review

`worlds/house_v1.sdf` is a real local Gazebo world with the `UserCommands` system. `maps/house_v1.yaml`, `local_annotations/house_v1/semantic_regions.json`, and `local_annotations/house_v1/safe_goals.json` provide the aligned map, six semantic regions, and accepted goals. The kitchen goal is read from the committed artifact; the charging-area goal is likewise read and is never redefined in this demo.

The installed TurtleBot4 stack provides `turtlebot4_gz_bringup turtlebot4_spawn.launch.py` with `world`, `x`, `y`, `z`, and `yaw` arguments, plus its ROS--Gazebo bridges. The installed navigation stack provides `localization.launch.py` and `nav2.launch.py`, and the project already has the optional `Nav2SimulationExecutor` client. Warehouse is the previously validated Gazebo/Nav2 regression environment.

house_v1 does **not** yet have a previously validated dynamic navigation launch. Its existing GUI and headless scripts explicitly disable localization and Nav2, and the repository records a previous house_v1 headless Gazebo exit with code 139. The preview now records four independent states: `house_world_ready`, `robot_entity_spawned`, `robot_simulation_interfaces_ready`, and `robot_control_stack_ready`.

For option 2, the `ros_gz_sim` `Entity creation successful` acknowledgement is sufficient for a successful visual preview. The optional follow-up uses the Gazebo Sim 8 world-scoped `/world/house_v1/scene/info` service, whose documented response type is `gz.msgs.Scene`; it never uses `gz model --list`, which has no world argument in this local CLI. Every auxiliary query has a strict timeout and is recorded in `logs/auxiliary_queries.jsonl`. If the query times out, the preview remains open with `entity_verification_method=creation_acknowledgement` and navigation remains unavailable until the separate control checks pass.

If `stop_motor` or another control interface is unavailable, the preview remains open and reports that navigation is unavailable; it does not relabel the entity as unspawned. The corresponding `control_readiness_blocking_report.md` identifies `turtlebot4_node` as the observed warning requester and records the reviewed Create 3 common/Gazebo control-stack dependency. Option 1 remains fail-closed: it must observe the control interface and Nav2 readiness before sending any navigation goal.

## Runtime boundary

Gazebo world loading, TurtleBot4 spawning, and the minimal clock bridge are real local simulation interfaces when the preview reaches them. This phase does not make an anomaly observation or a physical-sensor claim. All preview records remain `synthetic=true`, `simulation_only=true`, and `real_robot_supported=false`.

The command owns and cleans up only the process groups it starts. It never uses `pkill`, `killall`, global entity deletion, or a real-robot command path. The red obstacle entity has a per-run name and can only be removed by that exact name.

## Artifacts

Each preview writes `preflight_check.json`, `runtime_commands.json`, `house_startup.json`, `robot_spawn.json`, `cleanup.json`, `demo_summary.json`, and `logs/` under the requested new output directory. Entity records distinguish the creation acknowledgement from the optional query through `entity_verification_method`, `entity_query_confirmed`, and `entity_query_timed_out`. Failed stages retain their real false or failure state. The report must not be interpreted as physical-robot, physical-sensor, or real-home validation.
