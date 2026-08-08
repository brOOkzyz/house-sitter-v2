# RaPToR-Lite Phase 5.9 Pre-Experiment Freeze

Frozen at 2026-08-08T21:27:49Z from source branch `codex/raptor-lite-phase2-20260806`, source HEAD `a5dcd77c1d2be7f456827198bc3d79e3192c360c`. This report and the pilot entry are non-functional freeze records; the source HEAD above is the experiment-logic baseline.

## Frozen environment

- Test interpreter: CPython 3.12.3, Linux 7.0.0-28-generic x86_64 / glibc 2.39.
- Direct dependencies: pydantic 2.13.4, PyYAML 6.0.3, pytest 9.1.1.
- `requirements-raptor-lite.txt` SHA-256: `02000cfc5014263b676ba2d9868e45e67fdeb0dd627303e916ce86034b5552bc`.
- Sorted `.venv_raptor_lite` `pip freeze` SHA-256: `d6c4f5b924a50aac7f002c2e22ca92de6ab733660efdc18f57bd04d0b205fd51`.
- Required mainline command: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 rtk proxy .venv_raptor_lite/bin/python -m pytest -q tests/raptor_lite`.

## Frozen config and schemas

| Item | Logical hash | File SHA-256 |
| --- | --- | --- |
| Default experiment config | `bbb2c34fa4978a9c54a731ef992555a0d796fe095c5cf346abf976441cbe5951` | `d2381465473eca4c9e26c42f4f41a375a9729db8a70a74089d92f6374a8e2600` |
| Pilot experiment config | `cc2c0e5071c65c638e1399fda23b75250df10c03e0ee74f53224410ea8455417` | `c35e650dc0e76125823be3c9e9fe13f7a711bfed6082f06ce2c7bac550a83e3e` |
| Capability profile | `565bd883c49dc863760e8f53aae092ad22f24f08a500ccfe91dd2e7875f9be36` | `78646fc6003aa97430a651c5f5dadf4d7a63e50ac509751517af83db1a669a1a` |

- Experiment config and manifest schema: `raptor-lite-experiment-v1`.
- TaskSpec / verification schema source: `raptor_lite/models.py` (`3213d3d9204e28ebe7bdcbbb322ddbaecb63762f83f454686ff718b90dbdddaa`) and `raptor_lite/verifier.py` (`11f1d40be8f34eff638ff08abec6fca3fbe9e4561dcc50e40796cc4f98a7d988`).
- Scenario grammar and validation source: `raptor_lite/scenario.py` (`62663b7959b087bb1bdb2c2c2a2870e8ea4dac56c60b3bf61b0ddaaf94fee5af`).
- Twin history schemas remain `1.0`; ExperimentRunner records the versioned manifest and its deterministic `reproducibility_hash` separately.

## Result-affecting source freeze

The following files and their behavior are frozen for Phase 6 comparisons:

- `raptor_lite/experiment.py` `d14efeeb2c2fe1678c4ae1d182dee524c23f91590c6abc05e462fb5b2226c6b2`: scenario sampling, config hashing, manifest and ablation admission.
- `raptor_lite/planner.py` `727a72b356c72e30603250e3c0c58e61402c2939781f26746a01be760a2751a4`: task grammar and legal route ordering.
- `raptor_lite/house2d.py` `7e8201cdf01a9bfee24fc7cdf015d726991c69925e5d4cf327dadacb12b424e4`: world generation, legal graph, battery, sensor observations and ground truth.
- `raptor_lite/house_sitter.py` `145236c90c06f05590d1fc77141a77f52ec37bd3f4079bc6bfa593a7260261eb`: baseline/detection thresholds and Twin updates.
- `raptor_lite/phase56.py` `71eacc15cdd5afa3c54909ba257e96988e108b44a367a8cea6653f7997080536`, `raptor_lite/phase57.py` `51e62cd00e72e961e7e47125cdcd7ce8ac8baa5bd3bb51d0b6cc8f565a7159ac`, `raptor_lite/executor.py` `bea76a7c7ac6750f610961ddbb5e054c2e5702f7ace9928ab86e6210ae25dcb6`, `raptor_lite/capability_registry.py` `b2f6aaece2f34f1041944e05cebbd001beac1ef21fc84704a875077821b135e6`, and `raptor_lite/artifacts.py` `ac801a33008716814340729c3f1480f0c0b72ed4024e3021b44249ede172cd46`: confirmation, policy, execution, capability and persisted evidence.

Any change to these files; the capability profile; either experiment config; task/scenario grammar; detector thresholds; sensor noise/dropout; battery/resource values; route ordering; verifier/policy behavior; schema versions; or test environment requires Phase 6 runs using the affected condition to be re-run. Changing only this report or the empty pilot directory does not change simulation results.

## Experiment readiness and pilot

ExperimentRunner is ready for deterministic single-config runs: it records input/config/profile hashes, selected scenario, TaskSpec, verifier/resource decisions, ground truth, onboard observations, route, detections, Twin diff, final outcome, and a timestamp-independent reproducibility hash. Ground truth is only collected into the manifest after execution; detection continues to consume House2D onboard observations.

`configs/raptor_lite/pilot_experiment.yaml` is a small, two-scenario pilot entry with all ablations disabled. Its reserved output location is `results/raptor_lite/pilot/`; the directory contains only `.gitkeep`. No pilot or Phase 6 result was run during this freeze.

## Test boundary

The RaPToR-Lite mainline denominator is `tests/raptor_lite` only. Legacy ROS/Gazebo/3D suites, including `tests/test_gazebo_static_demo.py`, `tests/test_final_3d_house_sitter_demo.py`, Nav2 and other `tests/test_*.py` integrations, are archived for this freeze and are not included in the RaPToR-Lite pass rate.

## Freeze validation

The frozen test command completed with **72 passed**. `git diff --check` completed without whitespace errors. No Phase 6 or pilot output was generated.
