  House Sitter v2

 house_sitter_v2  is a clean v2 rewrite of the UCL MSc project prototype for an LLM-assisted house-sitter robot. It lives alongside the older Create3 demo in the same workspace and does not modify the legacy files.

   Project Goal

The project turns a natural-language task into a constrained JSON plan, verifies the plan before execution, and then executes map-based tasks with TurtleBot 4, Nav2, and SLAM in Gazebo.

Current pipeline:

   text
Natural-language prompt
  -> JSON-only planner provider (mock by default; real LLM disabled)
  -> JSON task plan
  -> allow-list verifier
  -> dry-run executor
  -> task report
   

   Current Status

The v2 planning and navigation line has reached the saved-map Nav2 verification stage:

- mock planner, verifier, and dry-run executor are complete;
- existing-map localization plus real Nav2 navigation in Gazebo has been verified;
- the minimal SLAM route has been completed:  Gazebo -> slam_toolbox -> generated map -> saved map -> AMCL localization -> Nav2 readiness ;
- the saved SLAM map is stored at  house_sitter_v2/maps/minimal_slam_map.yaml  and  house_sitter_v2/maps/minimal_slam_map.pgm .

Phase 1 dry-run remains available as the baseline capability, but the main focus has moved from pure dry-run to real map, localization, and navigation verification.

   Completed Milestones

- v2 dry-run planner, verifier, and executor are complete.
- Existing-map localization plus real Nav2 navigation has been verified in Gazebo.
- The minimal SLAM route is complete and the map has been saved.
- The saved SLAM map has been used successfully for localization and Nav2 readiness checks.

   Next Step

The next main step is:

1.  navigate_to_waypoint -> Nav2 action client .
2. Connect the mock planner or LLM planner to the real Nav2 executor.

Phase 1 does not import ROS 2 control interfaces, does not publish topics, and does not send action goals. The coordinates in  config/waypoints.json  are mock data and have not yet been calibrated against a real map.

   Run the Mock Planner

From  ~/create3_ws :

   bash
python3 house_sitter_v2/scripts/run_mock_planner.py "patrol the living room and return to start"
   

Expected output includes:

1.  Generated plan 
2.  Verified plan 
3.  Dry-run execution 
4.  Task report 

   Run the LLM Demo

The software-only end-to-end demo uses the mock path outside of the disabled  RealLLMPlannerProvider . It does not connect to ROS and does not call external APIs:

   bash
python3 house_sitter_v2/scripts/run_llm_demo.py "patrol the living room and return to start"
   

Expected output includes:

1.  User command 
2.  Generated JSON plan 
3.  Verification result 
4.  Dry-run execution steps 
5.  Final task report 

Invalid actions, unknown waypoints, and  cmd_vel -style requests are rejected by the verifier.

   Repository Layout

   text
house_sitter_v2/
├── README.md
├── AGENTS.md
├── config/
│   ├── allowed_actions.json
│   └── waypoints.json
├── docs/
│   ├── architecture.md
│   ├── current_plan.md
│   └── turtlebot4_notes.md
├── house_sitter_core/
│   ├── __init__.py
│   ├── executor.py
│   ├── planner.py
│   ├── reporting.py
│   ├── schemas.py
│   └── verifier.py
└── scripts/
    └── run_mock_planner.py
   

The read-only TurtleBot 4 diagnostic scripts belong to Phase 2. SLAM/Nav2 integration and saved-map verification are already complete, so the next emphasis is  navigate_to_waypoint  and the Nav2 action client.

   LLM Planner Adapter

 house_sitter_core/llm_provider.py  defines a JSON-only  PlannerProvider , keeps the deterministic  MockPlannerProvider , and provides a disabled  RealLLMPlannerProvider  placeholder. Every provider output must be parsed into JSON by  VerifiedPlannerAdapter  and pass  PlanVerifier  before being returned. The provider does not own an executor, Nav2 client, or velocity-topic interface.

The real  nearby_test  waypoint check is currently paused. Gazebo GUI stability is affected by a  libMinimalScene  / OGRE rendering segmentation fault. Real navigation should not resume until the rendering issue is resolved and the safety checks pass again.

   Recommended Headless TurtleBot 4 Bringup

The stock  turtlebot4_gz.launch.py  always loads the Gazebo GUI configuration and  MinimalScene . Even when  gz_args:="-r -s"  is passed at the top level, that behavior is not reliably overridden. For saved-map Nav2 work and the future  nearby_test  check, use the dedicated headless bringup instead:

   bash
./house_sitter_v2/scripts/bringup_headless_turtlebot4.sh
   

The script starts, in order, the Gazebo warehouse server only, the Gazebo-to-ROS  /clock  bridge, and  turtlebot4_spawn.launch.py rviz:=false . It does not start Gazebo GUI, Nav2, AMCL, or SLAM, and it does not send robot commands. Logs are written to:

   text
house_sitter_v2/logs/headless_<timestamp>/
   

When  Ctrl+C  is pressed or  SIGTERM  is sent, the script cleans up the processes it started in order. After startup, a separate read-only check can be run:

   bash
./house_sitter_v2/scripts/check_headless_gazebo.sh
   

That check verifies that  /clock ,  /scan ,  /odom , and  /dock_status  have data and confirms that no Gazebo GUI process or  libMinimalScene.so  is present.
