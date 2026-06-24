  TurtleBot 4 Notes

This file is used to keep reproducible Phase 2 and Phase 3 experiment notes. It does not contain startup or control commands.

Known integration prerequisites:

- the Gazebo simulation clock advances continuously;
-  /scan  has valid LaserScan data;
-  /odom  has continuous odometry data;
- the  odom -> base_link  TF chain is consistent with the map-related transforms;
- the robot namespace, frame names, and topic remapping used by Nav2 are explicit;
-  waypoints.json  only contains real coordinates after the map has been fixed.

Open items to confirm:

- the Gazebo simulation launch command and parameters for TurtleBot 4 Jazzy;
- the packages and launch files used for SLAM Toolbox and Nav2 bringup;
- the effect of starting in a docked state on navigation;
- whether  /cmd_vel  in simulation is consumed by Nav2, a velocity smoother, or a controller.
