# RaPToR-Lite Phase 5.10: Create 3 ROS 2 deployment backend

`Create3ROS2Backend` shares the existing `RobotBackend` contract with the
research-only `House2DBackend`. House2D remains the seeded experimental
backend; its synthetic ground truth is never supplied to the ROS adapter.

The backend dynamically inspects the ROS graph and records both advertised
types and unavailable requirements. It recognises the current Create 3 ROS 2
interfaces: `/battery_state` (`sensor_msgs/msg/BatteryState`), `/odom`
(`nav_msgs/msg/Odometry`), `/imu` (`sensor_msgs/msg/Imu`),
`/hazard_detection` (`irobot_create_msgs/msg/HazardDetectionVector`),
`/dock_status` (`irobot_create_msgs/msg/DockStatus`), `/cmd_vel`
(`geometry_msgs/msg/Twist`), `drive_distance`, `rotate_angle`, `drive_arc`,
`navigate_to_position`, `dock`, `undock`, and `/e_stop`. The discovery report
contains the actual name/type pairs; a similarly named wrong type is not
accepted. Direct velocity publication is deliberately not an executor skill.
Bounded Create 3 actions are the supported motion mapping.

Run a non-hardware readiness check (it creates a ROS node and discovers only):

```bash
source /opt/ros/jazzy/setup.bash
PYTHONPATH="$PWD:$PWD/.venv_raptor_lite/lib/python3.12/site-packages:$PYTHONPATH" \
python3 -m raptor_lite.cli deployment-readiness \
  --plan examples/raptor_lite/create3_ros2_dry_run_plan.json
```

The bundled plan contains telemetry/observation steps only. It is verified
against the discovered profile before a backend executor can receive it.
Missing topics, action servers, services, stale sensor samples, timeouts,
communication failures, low battery, hazards, rejected/cancelled actions, and
missing return/dock/navigation capability are explicit failures. The backend
defaults to `allow_motion=False`; no `/cmd_vel`, dock, or undock command is
sent by the readiness command.

## Navigation boundary

Create 3 native ROS does not define `kitchen` or any other semantic room.
`move_to_room` and `return_to_start` remain unavailable unless both a Nav2
`/navigate_to_pose` action and an explicit `NavigationProvider` with legal
waypoints are supplied. This optional layer does not couple Gazebo or archived
3D code back into RaPToR-Lite.

## Observation boundary and validation status

`Create3ObservationAdapter` converts actual battery, odometry, IMU, hazard and
dock messages to the shared observation shape. It declares unavailable fields
for room semantics, object detection, temperature, humidity and transition
accessibility instead of inventing them; therefore a detector receives only
observations and cannot read simulator ground truth.

Status is deliberately three-layered:

- Implemented ROS2/Create3 backend: yes.
- Interface/mock tested: yes.
- Physical robot validation performed: **no** (`physical_robot_validated=false`).
