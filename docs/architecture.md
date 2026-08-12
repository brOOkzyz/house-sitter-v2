# Architecture

RaPToR-Lite separates task creation, verification, and execution. A constrained planner may create a `TaskSpec`, but it cannot run it. `verify_task` checks the task against the declared capability registry before `BackendExecutor` accepts it.

```text
text or JSON task -> planner/schema -> capability registry + verifier -> executor
                                                                    -> House2D backend
                                                                       -> observations
                                                                       -> detector
                                                                       -> Digital Twin, alerts, report
```

`House2DBackend` is the deterministic, seeded experimental backend. It owns simulated scenario truth and emits separate sensor-facing observations; the detector and Digital Twin consume observations rather than simulator truth.

`Create3ROS2Backend` implements a separate ROS 2 interface-discovery and adapter boundary. It defaults to read-only discovery and does not establish physical-robot performance. No direct velocity publication is an executor skill.
