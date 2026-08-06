# RaPToR-Lite Phase 3: House-Sitter Application

House-Sitter is the primary RaPToR-Lite application.  A verifier-approved task establishes four room baselines, activates controlled House2D events, revisits affected rooms, detects changes from simulated onboard observations, updates only observation-supported Digital Twin fields, generates alerts, returns to the charging area, and writes a monitoring report.

## Boundaries and flow

The application is simulation-only: every observation, anomaly, Twin, alert, and report records `simulation_only=true` and `physical_robot_validated=false`.  It imports neither ROS nor the archived Gazebo/Nav2 path.  House2D owns ground truth and converts it into observations; `HouseSitterApplication` receives only observations and baselines.  Event type, expected label, and simulator event records are not detector inputs.

The verified full example is:

```bash
python3 -m raptor_lite.cli run examples/raptor_lite/complete_house_sitter_demo.json \
  --profile configs/raptor_lite/create3_sim_capabilities.yaml --backend house2d --seed 12345
```

It injects a kitchen obstacle and bathroom humidity event from task scenario configuration.  Use `--no-events` for a normal patrol, or `--event unexpected_obstacle` / `--event high_humidity` to override the configured scenario.  The `detected_anomaly` alert reference resolves only to an anomaly actually detected in that room; an explicit incorrect anomaly reference fails closed.  Scenario input is a controlled simulator input, never an anomaly-detector input.

## Detection, Twin, and evidence

Detectors compare a post-event observation to its stored baseline and can emit `unexpected_obstacle`, `high_temperature`, `high_humidity`, `blocked_transition`, or `missing_observation`.  A dropout produces `missing_observation` but cannot update the Digital Twin.  Twin revisions contain only fields supported by the latest valid observation; repeated identical observations do not create a revision.

Runs preserve baseline and sensor observations, scenario ground truth, anomalies, Twin before/after and updates, alerts, route/execution traces, summary, and English Markdown report under `artifacts/raptor_lite/<run-id>/`.  A seed reproduces scenario ground truth, route trace, and deterministic detection results.  This model supports task and monitoring evaluation; it is not a physical robot, perception, physics, or navigation-performance claim.
