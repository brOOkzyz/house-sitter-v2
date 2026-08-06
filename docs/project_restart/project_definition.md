# Project definition: RaPToR-Lite

## Working title

**RaPToR-Lite: Capability-Grounded Task Creation and Verification for ROS 2 Robots, Evaluated through an Idle-Time House-Sitter Application** — **PROPOSED**.

## Problem, objective, users and gap

- Domestic vacuums spend substantial time idle and their embedded sensing may support home awareness without new fixed infrastructure. **SOURCE-DERIVED** (Project25 brief).
- Non-experts face a ROS terminal interaction barrier. **SOURCE-DERIVED** (RaPToR draft; broad “no platforms exist” wording is not independently verified).
- Objective: turn a user request into a bounded structured task, validate it against declared capabilities/safety constraints, execute only approved tasks, and retain reproducible evidence. **REPOSITORY-EVIDENCED** (`raptor_lite/`).
- Primary users are research demonstrators and non-expert task authors working with a declared simulation robot profile. **PROPOSED**.
- Research gap: existing project material does not provide evidence for a small, testable task-verification layer coupled to a simulation-first House-Sitter reliability evaluation. **INFERENCE** from the source audit and repository audit.

## Original contribution and research questions

- Contribution: a deliberately small capability registry + strict task schema + explainable verifier + deterministic backend boundary, evaluated in one coherent House-Sitter application. **PROPOSED**.
- RQ1: How accurately does the verifier accept valid and reject invalid capability-grounded tasks? **PROPOSED**.
- RQ2: How reliably do constrained natural-language requests yield verifier-valid, same-intent structured tasks? **PROPOSED**.
- RQ3: Under seeded household variation, how reliably does the House-Sitter chain detect events, update the Digital Twin, alert, and report? **PROPOSED**.

## Boundaries

- Task creation (including any future natural-language adapter) produces `TaskSpec`; it cannot control a backend or ROS directly. **REPOSITORY-EVIDENCED** for the existing verification-before-execution pattern; **PROPOSED** for the RaPToR-Lite boundary.
- The main application is idle-time House-Sitter monitoring: patrol/room visit, observation, anomaly detection, Digital Twin update, alert, report, and safe return. **SOURCE-DERIVED** for the application requirements; **REPOSITORY-EVIDENCED** for the deterministic chain.
- Simulation-first execution is the primary evidence mode. Every current RaPToR-Lite task/execution result must retain `simulation_only=true` and `physical_robot_supported=false`. **REPOSITORY-EVIDENCED**.
- Physical-robot validation, unrestricted code generation, runtime LLM control, full general RaPToR platform, Nav2/SLAM research, multi-robot support and automatic ROS installation are non-goals. **PROPOSED**, consistent with source limits.

## Success criteria

1. Every executable task has a schema, capability resolution, bounded failure policy and reproducible trace. **REPOSITORY-EVIDENCED** in Phase 1; extension criterion **PROPOSED**.
2. RQ1/RQ2/RQ3 use held-out seeded scenarios, recorded ground truth and uncertainty intervals rather than identical deterministic repeats. **PROPOSED**.
3. House-Sitter remains the main evaluated application, not a decorative demo. **PROPOSED** and required for Project25 alignment.
4. No conclusion claims physical deployment or sensor validation without newly collected physical evidence. **PROPOSED** safety boundary.

## Relationship to sources

- Project25 contributes the household-vacuum, idle-time monitoring, anomaly, mapping/Digital Twin and demonstrator requirements. **SOURCE-DERIVED**.
- The RaPToR draft contributes an accessibility motivation and unverified Version 1 feature claims; it does not contribute runnable code. **SOURCE-DERIVED**.
- Capability-grounded models, stable verifier issue codes, mock execution, simulation-first evidence, backend choice and evaluation design are this repository’s new design. **REPOSITORY-EVIDENCED** for Phase 1 implementation; **PROPOSED** for later phases.
