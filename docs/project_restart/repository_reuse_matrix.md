# Repository reuse matrix

Status is a restart decision, not deletion authority. “Evidence” refers to checked repository files/tests, not unreviewed historical claims.

| Component | Decision | Evidence and restart rationale |
|---|---|---|
| `raptor_lite/` Phase 1 models, registry, verifier, mock executor, artifacts, CLI | **KEEP** | New focused core at `6c069a2`; Pydantic schemas, stable issue codes and `tests/raptor_lite` provide a direct capability-grounded basis. ADAPT only if Phase 2 needs backend interfaces. |
| `configs/raptor_lite/` and examples | **KEEP** | Small declared capability surface maps directly to the House-Sitter case; do not merge the legacy 50-skill catalog without evidence-driven need. |
| `house_sitter_core/house_sitter_patrol.py`, `digital_twin.py`, `environment_monitoring.py`, `simulated_onboard_sensors.py`, `simulation_boundary.py` | **ADAPT** | Deterministic, simulation-only monitoring, anomaly, twin and alert chain with explicit boundaries. Reuse behind RaPToR-Lite adapter rather than duplicate it. |
| `evaluation/monitoring_scenarios_v1.json`, robustness modules/scenarios | **ADAPT** | Useful labelled cases and missing/noise cases, but repeated deterministic trials are functional regression evidence, not main statistical evidence. Add seeded generators in Phase 6. |
| patrol strategy modules/evaluations | **REGRESSION_ONLY** | Explicitly deterministic/untuned policy baselines. Preserve for comparison and tests; do not present fixed repeats as new reliability evidence. |
| existing natural-language adapter/pipeline and mock/real LLM providers | **ADAPT** | Enforces offline bounded vocabulary and existing verification, but has a legacy 50-capability surface and provider paths. Optional Phase 4 adapter must emit `raptor_lite.TaskSpec` only; never execute directly. |
| legacy schema/planner/verifier/skill catalog/runtime/bridge (50 skills) | **REGRESSION_ONLY** | Valuable safety/history evidence, but overlaps task representation and is wider than RaPToR-Lite. Avoid parallel primary execution semantics. |
| house maps, semantic regions, accepted safe goals, static visual tools | **CASE_STUDY** | Ground the household setting and room semantics; records already label themselves simulation/review only. Keep as case-study inputs, not generic capability truth. |
| 3D `house_v1` preview/final-demo/Gazebo static demo | **CASE_STUDY** | Useful optional visual evidence, but docs record instability and it is not reliable primary experimental infrastructure. |
| Nav2/AMCL/Gazebo bridges, clients, launch/check scripts | **ARCHIVE** | Preserve untouched for historical/optional integration regression. Past readiness/localisation instability makes them unsuitable as the primary contribution or evaluation backend. |
| warehouse navigation environment | **REGRESSION_ONLY** | Retain as technical navigation regression evidence only; it is not residential monitoring evidence. |
| paper-results pipeline, benchmark docs/results renderers | **ADAPT** | Reuse rendering/provenance patterns; revise claims and inputs for seeded stochastic experiments. |
| supervisor demos, screenshots and narrative evidence | **ARCHIVE** | Demonstration/history material; do not use it as quantitative evidence for the restart. |
| old experimental scripts and frozen datasets | **ARCHIVE** | Preserve without edits. They remain reproducibility context and can support regression checks. |
| duplicated eventual fallback/demo experiments on rescue branches | **REMOVE_LATER** | Do not delete now. Evaluate after a single RaPToR-Lite backend route replaces duplicate runtime concepts. |

## Audit conclusions

- **Project25 alignment:** the monitoring, anomaly, Digital Twin, actionable-alert chain is the strongest repository-supported correspondence to the brief. Autonomous physical patrol and onboard-only sensing are not established by this audit.
- **RaPToR alignment:** mandatory verification before execution and constrained structured task creation are aligned with the draft’s accessibility motivation; the registry/verifier design is new repository evidence, not a recovered RaPToR implementation.
- **Debt/risk:** the repository contains overlapping task/skill abstractions, optional LLM/provider code, and ROS/Gazebo integration paths. New work must adapt rather than expand them.
- **Simulation-first credibility:** deterministic monitoring is credible as functional regression with explicit synthetic labels. Claims of reliability require future seeded, ground-truth-preserving experiments.
