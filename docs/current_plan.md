  Current Development Plan

   Project Direction

This v2 project is a cleaner continuation of the previous Create3 natural-language control prototype.

The current direction is to build an LLM-assisted house-sitter robot prototype with two main tracks: structured JSON task planning and map creation/navigation. A high-level user prompt should be converted into a structured JSON task plan, the plan should be verified against safety and capability constraints, and the LLM should stay out of direct robot control while the robot stack uses TurtleBot 4, localization, Nav2, and SLAM/map creation.

The intended long-term pipeline is:

   text
User prompt
-> LLM / mock planner
-> structured JSON task plan
-> safety verifier
-> task executor
-> TurtleBot 4 simulation
-> existing-map localization / SLAM route
-> Nav2 navigation
-> task report
   

At this stage, the project is still a prototype. The focus is to build a safe and testable planning-and-execution structure before connecting real LLM calls or sending autonomous navigation goals.

   Completed Milestones

The following parts have been implemented and verified.

    1. v2 dry-run planning pipeline

A clean v2 project structure has been created under:

   text
house_sitter_v2/
   

The mock planning pipeline has been tested successfully:

   text
natural-language prompt
-> JSON task plan
-> verifier
-> dry-run execution
-> task report
   

The tested example was:

   bash
python3 house_sitter_v2/scripts/run_mock_planner.py "patrol the living room and return to start"
   

The output included:

   text
Generated plan
Verified plan
Dry-run execution steps
Task report
No ROS 2 commands were sent
   

This confirms that the planner, verifier, dry-run executor, and reporting structure work without controlling the robot.

    2. TurtleBot 4 simulation readiness

TurtleBot 4 Gazebo simulation has been launched and inspected.

The following ROS 2 data streams were confirmed:

   text
/clock
/scan
/odom
/tf
/tf_static
   

The LiDAR frame and robot base frame were also checked through TF. This confirms that the basic sensor and transform inputs required for localization, SLAM, and Nav2 are available.

    3. Existing-map localization

The existing warehouse map was loaded using TurtleBot 4 localization:

   text
/opt/ros/jazzy/share/turtlebot4_navigation/maps/warehouse.yaml
   

AMCL localization was started successfully. After setting the initial pose, the following were confirmed:

   text
/map is available
/amcl is active
/amcl_pose has data
map -> odom transform is available
   

This means the existing-map localization route is working.

    4. Nav2 startup

Nav2 was started after localization was established.

The following were confirmed:

   text
Nav2 lifecycle nodes are active
/navigate_to_pose action exists
velocity command chain exists
global and local costmaps are available
   

This confirms that the navigation stack can be launched on top of the existing-map localization setup.

    5. Undock status

The official  /undock  action was tested after Gazebo, localization, and Nav2 were active.

The robot was able to leave the dock state, and  /dock_status  changed from:

   text
is_docked: true
   

to:

   text
is_docked: false
   

This means the robot is no longer blocked by the dock state and is closer to being ready for a small navigation-goal test.

    6. Nav2 real navigation

A real  /navigate_to_pose  test was run with the robot undocked and localization active.

Nav2 returned:

   text
SUCCEEDED
error_code: 0
   

The robot moved in Gazebo, the feedback showed motion from about  x=-0.306  to about  x=-0.562 , and no manual  /cmd_vel  or  /cmd_vel_unstamped  was published.

This confirms that the existing-map localization and Nav2 real navigation action chain works in Gazebo.

    7. SLAM map creation and saved-map localization/Nav2

The SLAM route was completed end to end:

   text
Gazebo + slam_toolbox -> /map -> map -> odom -> saved map -> AMCL localization -> Nav2 readiness
   

Key results:

   text
Phase A: Gazebo + slam_toolbox restored, /map had data, map -> odom existed
Phase B1: official /undock succeeded, /dock_status is_docked=false
Phase B2: /drive_distance was accepted and the robot moved about 0.047 m
   

The first  map_saver_cli  attempt failed due to map subscription / QoS issues, then the saved-map route succeeded with a more tolerant save configuration.

Saved artifacts:

   text
house_sitter_v2/maps/minimal_slam_map.yaml
house_sitter_v2/maps/minimal_slam_map.pgm
   

Saved map details:

   text
449x441
resolution 0.05 m
PGM about 194 KB
   

The saved map was then loaded successfully for localization and Nav2 readiness checks.

   Current Status

The current v2 project has reached the following stage:

   text
dry-run planner: working
JSON verifier: working
dry-run executor: working
task report: working
TurtleBot 4 Gazebo: working but may be unstable over longer periods
existing-map localization: working
AMCL pose: working
map -> odom TF: working
Nav2 stack: active
undock: tested successfully
small Nav2 goal execution: verified
SLAM map creation: working
saved-map localization: working
saved-map Nav2 readiness: verified
   

The project has completed both existing-map navigation and the SLAM-to-saved-map route.

The current verified state should therefore be described as:

   text
LLM-compatible planning layer + TurtleBot 4 existing-map localization/Nav2 navigation verified; SLAM/map creation and saved-map Nav2 verification also completed
   

rather than a complete autonomous house-sitter system.

   Phase 1: Safe Dry-Run MVP

Status: completed.

Implemented components:

   text
mock planner
JSON task schema
allowed action validation
waypoint validation
parameter safety checking
dry-run executor
task report generation
   

Purpose:

This phase proves that high-level language instructions can be converted into structured and verifiable task plans before any robot command is sent.

   Phase 2: TurtleBot 4 Diagnostic and Readiness Check

Status: completed for current basic readiness.

Verified items:

   text
/clock
/scan
/odom
/tf
/tf_static
velocity topic graph
dock status
wheel status
dock / undock actions
   

Purpose:

This phase confirms that the TurtleBot 4 simulation exposes the sensor, transform, and action interfaces needed for localization and navigation work.

   Phase 3: Existing-Map Localization + Nav2

Status: partially completed.

Completed:

   text
warehouse map loaded
AMCL active
initial pose set
/amcl_pose available
map -> odom available
Nav2 stack active
/navigate_to_pose action available
costmaps available
undock tested
   

Not completed yet:

   text
mapping verified waypoints to Nav2 goals
handling Nav2 failure / timeout / cancellation
   

Next step:

Implement the Nav2 action client wrapper and map verified  navigate_to_waypoint  actions to  PoseStamped  goals from  config/waypoints.json .

   Phase 4: LLM-to-Nav2 Integration

Status: completed for the minimal route.

Goal:

Connect the verified JSON task plan to map-based navigation actions.

The intended mapping is:

   text
navigate_to_waypoint
-> lookup waypoint in config/waypoints.json
-> convert waypoint to PoseStamped
-> send goal to Nav2 /navigate_to_pose
   

Important rule:

The LLM should not directly publish velocity commands. It should only generate structured task plans. The verifier should decide whether the plan is safe and executable.

   Phase 5: SLAM / Map Creation Route

Status: planned.

SLAM and map creation are a main project track, not an optional extension. The current completed route is existing-map localization plus Nav2 real navigation.

The next minimal SLAM route is:

   text
TurtleBot 4 Gazebo
-> /scan + /odom + /tf
-> slam_toolbox
-> generate /map
-> generate map -> odom
-> save map
-> use saved map for localization and Nav2
   

Purpose:

This phase supports scenarios where the robot does not already have a map and needs to build one during exploration, then save and reuse that map for localization and Nav2.

   Immediate Next Steps

The next technical steps are:

   text
1. Optional: run a very small Nav2 goal on the saved map to further validate navigation.
2. Main next step: implement navigate_to_waypoint -> Nav2 action client.
3. Then connect the mock planner / LLM planner to the real Nav2 executor.
   

   Safety Rules

The following actions should not be automated without confirmation:

   text
publishing /cmd_vel
publishing /cmd_vel_unstamped
sending /navigate_to_pose goals
sending /undock repeatedly
changing official launch files under /opt/ros
running destructive commands such as rm -rf, sudo apt purge, or git reset --hard
   

The current development principle is:

   text
Plan first, verify second, execute only after safety checks.
   

   Current Focus: LLM Planner Adapter

The JSON-only planner-provider boundary is now the active implementation track:

   text
PlannerProvider -> JSON object -> PlanVerifier -> verified TaskPlan
   

 MockPlannerProvider  remains the default.  RealLLMPlannerProvider  is a disabled placeholder and has no executor, Nav2, topic-publishing, or velocity-command access. Invalid actions, unknown waypoints, and direct velocity actions must be rejected before execution.

The real  nearby_test  waypoint is temporarily suspended because the Gazebo GUI is unstable with a  libMinimalScene  / OGRE rendering segmentation fault. The real waypoint test should not resume until rendering is stable and the normal localization/Nav2 safety checks pass again.

   Recommended Headless Gazebo Route

Gazebo stability testing isolated the recurring crash to the GUI / OGRE rendering path ( libMinimalScene.so ,  RenderThreadRhiOpenGL , and  OgreMaterial ) rather than the warehouse world or the basic NVIDIA OpenGL setup. The stock  turtlebot4_gz.launch.py  always constructs GUI arguments internally, so a top-level  gz_args:="-r -s"  does not provide reliable server-only operation.

The recommended route for subsequent saved-map Nav2 and  nearby_test  work is now:

   text
Gazebo warehouse server-only
-> dedicated Gazebo-to-ROS /clock bridge
-> turtlebot4_spawn.launch.py with rviz:=false
-> read-only headless readiness check
-> localization / Nav2 only after separate user confirmation
   

Project scripts:

   text
house_sitter_v2/scripts/bringup_headless_turtlebot4.sh
house_sitter_v2/scripts/check_headless_gazebo.sh
   

The bringup script writes per-run logs under  house_sitter_v2/logs/headless_<timestamp>/  and cleans up only its own process groups with SIGINT/SIGTERM. It never starts Nav2, AMCL, or SLAM and never sends robot commands.
