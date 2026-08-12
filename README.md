# RaPToR-Lite House-Sitter

RaPToR-Lite is a simulation-first research prototype for capability-grounded robot task creation and verification. Its House-Sitter application creates a constrained `TaskSpec`, verifies it against a declared capability registry, and runs only approved tasks in a deterministic House2D environment.

The project evaluates three questions: verifier effectiveness, constrained natural-language task creation, and seeded House-Sitter reliability. It is an MSc research repository, not a physical-robot deployment.

## Scope and safety boundary

Natural-language input produces a candidate task only. The verifier checks schema, declared capabilities, parameters, bounded timeouts, and explicit safety requirements before an executor can run. House2D is deterministic and simulation-only; it models rooms, routes, observations, anomalies, Digital Twin updates, alerts, resource policy, and robot feedback.

`Create3ROS2Backend` is a separate ROS 2 deployment adapter. Its readiness command discovers interfaces and optionally validates a read-only plan. It defaults to `allow_motion=False`; it does not publish `/cmd_vel` or send dock/undock goals. The implementation has interface and mock tests only. **Physical robot validation was not performed.**

```text
request -> constrained planner -> TaskSpec -> verifier -> backend executor
                                                |              |
                                      capability registry    House2D observations
                                                               -> detector -> Digital Twin -> alerts/report
```

House2D is the reproducible experimental backend. Create3ROS2Backend is retained as an explicitly bounded integration path, not as evidence of real-world performance.

## Installation

Python 3.10+ is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
```

## Run and test

Run the focused research test suite:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q tests/raptor_lite
```

Run a deterministic House-Sitter task (it writes ignored local artifacts under `artifacts/raptor_lite/`):

```bash
python -m raptor_lite.cli run examples/raptor_lite/complete_house_sitter_demo.json \
  --profile configs/raptor_lite/create3_sim_capabilities.yaml \
  --backend house2d --seed 12345
```

Start the localhost-only browser demo and open the printed URL:

```bash
python scripts/run_raptor_lite_demo.py
```

The demo uses only House2D. It does not connect to ROS 2, Gazebo, or a robot.

To inspect a ROS 2 graph without commanding a robot, see [Create3 deployment notes](docs/raptor_lite_create3_ros2_deployment.md). That command requires a local ROS 2 installation and an already available graph.

## Reproducibility

The locked protocol and inputs are in `configs/raptor_lite/` and `docs/raptor_lite/phase6_formal_protocol.md`. Validate their integrity with:

```bash
PYTHONPATH=. python scripts/phase6_formal.py preflight
PYTHONPATH=. python scripts/phase6_replay_addendum.py preflight
PYTHONPATH=. python scripts/phase6_rq3_correction.py preflight
PYTHONPATH=. python scripts/phase6_rq3_corrected_materializer.py audit
```

The public results pack is [results/raptor_lite/phase6_formal/phase7_results_freeze](results/raptor_lite/phase6_formal/phase7_results_freeze). It contains the final machine-readable summary, tables, figures, claim audit, and SHA-256 manifest. The matching formal [raw records](results/raptor_lite/phase6_formal/raw) are also published for independent recomputation; pilot output and intermediate generated records remain excluded.

## Repository layout

```text
raptor_lite/                 Core models, planner, verifier, backends, House-Sitter, UI
configs/raptor_lite/         Capability profile, experiment inputs, protocol, seed manifests
examples/raptor_lite/        Valid, invalid, and dry-run task examples
scripts/phase6_*.py          Protocol validation and formal-analysis helpers
tests/raptor_lite/           Focused automated tests
docs/raptor_lite*.md         Architecture, backend, planner, UI, and deployment notes
results/.../phase7_results_freeze/
                             Public aggregate results, tables, figures, and manifest
```

## Limitations

- The House2D world is a deterministic room graph, not a physics, perception, or navigation simulator.
- The natural-language planner is a constrained offline grammar, not an open-ended language model evaluation.
- Formal results are limited to the locked protocol and its stated claim boundaries.
- No physical robot validation was performed.

See [architecture](docs/architecture.md), [House2D](docs/raptor_lite_house2d.md), [House-Sitter](docs/raptor_lite_house_sitter.md), [natural-language planner](docs/raptor_lite_nl_planner.md), and [formal protocol](docs/raptor_lite/phase6_formal_protocol.md) for concise technical detail.
