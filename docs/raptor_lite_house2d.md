# RaPToR-Lite Phase 2: Seeded House2D Backend

`house2d` is a pure-Python, headless-first backend for evaluating verified RaPToR-Lite House-Sitter tasks.  It is a deterministic room graph, not a high-fidelity physics simulator, ROS 2 integration, Nav2 implementation, or evidence of physical-robot performance.

## Model and safety boundary

The world has five rectangular room regions (`charging_area`, `living_room`, `kitchen`, `bedroom`, and `bathroom`) and five explicit door connections.  Movement uses bounded BFS on this door graph: no edge means no traversal, every door costs five simulation seconds and four battery percentage points, and a task step fails if its declared timeout or available battery is insufficient.  `stop` and failure-policy `stop` invoke the backend emergency stop; no loop is unbounded.

The backend records static furniture, temperature, humidity, battery, visits, routes, and requested events in `scenario_ground_truth.json`.  `inspect_room` returns a separate simulated onboard observation with the strict fields `synthetic=true`, `simulated_onboard_sensor=true`, `simulation_only=true`, and `physical_robot_validated=false`.  It contains sensor-facing values and event identifiers, not the ground-truth event records or room internals.

## Reproducibility and artifacts

`--seed` fixes generated room conditions and event state.  The selected seed is emitted by the CLI and written to `scenario_seed.json`; equal seed plus equal task/events produces equal ground truth, routes, and simulation-time trace.  Supported Phase 2 events are `unexpected_obstacle`, `high_temperature`, `high_humidity`, `blocked_transition`, `observation_dropout`, and `low_initial_battery`.

Runs write simulator configuration, seed, ground truth, initial/final state, observations, route trace, execution trace/result, and a summary under `artifacts/raptor_lite/`.  Example:

```bash
python3 -m raptor_lite.cli run examples/raptor_lite/valid_house_sitter_task.json \
  --profile configs/raptor_lite/create3_sim_capabilities.yaml --backend house2d --seed 12345
```

The executor revalidates the `TaskSpec` against the selected capability registry before either backend runs, so a forged or mismatched approval report is refused; `mock` remains available.  Phase 3 may add House-Sitter detection, Digital Twin, alert, and report logic on top of these observations.  Path planning itself is deliberately not a research contribution.
