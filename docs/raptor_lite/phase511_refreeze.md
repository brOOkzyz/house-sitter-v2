# RaPToR-Lite Phase 5.11 Re-Freeze

Frozen from branch `codex/raptor-lite-phase2-20260806`, source HEAD `943e592b483be0bfdf4bd61a59b2d576c6d9b71f`, after the Create3 ROS 2 deployment backend implementation. This is a non-functional baseline record; no pilot result or Phase 6 run was performed.

## Frozen scope and boundary

- **House2D** (`house2d`, version `1.0`) is the experimental backend. It remains the only `ExperimentRunner` backend and produces all research ground truth, observations, routes, detections, and Twin evidence.
- **Create3ROS2** (`create3_ros2`, version `5.10`) is a deployment backend. Its dynamically discovered ROS graph profile and observation adapter are frozen with the core, but it is not an experiment backend and does not alter House2D defaults or results.
- `physical_robot_validated=false` for both backend records. The deployment backend is interface/mock tested only; no physical robot validation occurred.
- Every experiment manifest now records its House2D `backend` identity/version/role and includes it in the timestamp-independent `reproducibility_hash`.

## Frozen environment

- Test interpreter: CPython 3.12.3, Linux 7.0.0-28-generic x86_64 / glibc 2.39.
- Direct dependencies: pydantic 2.13.4, PyYAML 6.0.3, pytest 9.1.1.
- `requirements-raptor-lite.txt` SHA-256: `02000cfc5014263b676ba2d9868e45e67fdeb0dd627303e916ce86034b5552bc`.
- Sorted `.venv_raptor_lite` `pip freeze` SHA-256: `d6c4f5b924a50aac7f002c2e22ca92de6ab733660efdc18f57bd04d0b205fd51`.

## Frozen config, profile, schema and source hashes

| Item | Logical hash | File SHA-256 |
| --- | --- | --- |
| Default experiment config | `bbb2c34fa4978a9c54a731ef992555a0d796fe095c5cf346abf976441cbe5951` | `d2381465473eca4c9e26c42f4f41a375a9729db8a70a74089d92f6374a8e2600` |
| Pilot experiment config | `cc2c0e5071c65c638e1399fda23b75250df10c03e0ee74f53224410ea8455417` | `c35e650dc0e76125823be3c9e9fe13f7a711bfed6082f06ce2c7bac550a83e3e` |
| House2D capability profile | `565bd883c49dc863760e8f53aae092ad22f24f08a500ccfe91dd2e7875f9be36` | `78646fc6003aa97430a651c5f5dadf4d7a63e50ac509751517af83db1a669a1a` |

- Experiment/manifest schema: `raptor-lite-experiment-v1`.
- `raptor_lite/models.py`: `3213d3d9204e28ebe7bdcbbb322ddbaecb63762f83f454686ff718b90dbdddaa`.
- `raptor_lite/verifier.py`: `6a527b366fe3f14499a1382aedf124e4628c032a2250f3cb1fbb76cd4839e678`.
- `raptor_lite/experiment.py`: `7234801d8b6c19a7325bffdf013bafc2be2b89e44bd94f1cd0d1b2a0f32a6d19`.
- `raptor_lite/house2d.py`: `7e8201cdf01a9bfee24fc7cdf015d726991c69925e5d4cf327dadacb12b424e4`.
- `raptor_lite/create3_ros2.py`: `874645cba154dc84ba197138a1aeb9a1d7460a4ab9b2592dc6dead30e1650130`.
- `raptor_lite/scenario.py`: `62663b7959b087bb1bdb2c2c2a2870e8ea4dac56c60b3bf61b0ddaaf94fee5af`.
- `raptor_lite/planner.py`: `727a72b356c72e30603250e3c0c58e61402c2939781f26746a01be760a2751a4`.
- `raptor_lite/executor.py`: `bea76a7c7ac6750f610961ddbb5e054c2e5702f7ace9928ab86e6210ae25dcb6`.
- `raptor_lite/capability_registry.py`: `b2f6aaece2f34f1041944e05cebbd001beac1ef21fc84704a875077821b135e6`.
- `raptor_lite/artifacts.py`: `ac801a33008716814340729c3f1480f0c0b72ed4024e3021b44249ede172cd46`.

Any modification to the House2D experimental backend, core schema/verifier/planner, experiment configuration/profile, or the listed Create3 deployment interface requires a new freeze. Pilot-only data is excluded from this baseline.

## Validation

The required mainline command is `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 rtk proxy .venv_raptor_lite/bin/python -m pytest -q tests/raptor_lite`.

Result: **81 passed**. `git diff --check`: passed. Critical/High review: no blocking finding.
