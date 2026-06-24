  v2 Architecture

   Design Principles

- Keep planning and execution separate.
- Require the verifier before any execution step.
- Never let natural-language output or LLM output call ROS 2 directly.
- Use stable waypoint names and resolve map coordinates from configuration.
- Keep Phase 1 deterministic and dry-run friendly for testing and demos.

   Current Architecture (Phase 1)

   text
User prompt
    |
    v
MockPlanner
    |  TaskPlan JSON
    v
PlanVerifier  <--- allowed_actions.json
    |          <--- waypoints.json
    v
DryRunExecutor
    |
    v
Task report
   

     planner.py 

Maps a small set of supported natural-language patterns into JSON-compatible  TaskPlan  objects. It acts as a stand-in for the future LLM adapter and does not access the network.

     schemas.py 

Defines the minimum fields for  TaskPlan  and  ActionStep . Phase 1 uses the standard library  TypedDict  to avoid adding third-party dependencies.

     verifier.py 

Performs strict validation:

- top-level fields and schema version;
- maximum number of steps;
- action allow-list;
- required and unknown parameters;
- parameter types and ranges;
- waypoint existence.

     executor.py 

Phase 1 only prints verified steps. It has no ROS 2 publisher, Nav2 client, or action client.

   Target Architecture (Later Phases)

Phase 3 will add a ROS 2 / Nav2 adapter. It will only accept a verified plan, resolve the name in  navigate_to_waypoint  into a pose in the  map  frame, and then send a Nav2 navigation goal. The adapter must report success, failure, timeout, or cancellation, and it must not bypass the verifier.

SLAM, localization, Nav2 bringup, and the real LLM provider are handled as separate integration layers so the core planning and verification code stays isolated.
