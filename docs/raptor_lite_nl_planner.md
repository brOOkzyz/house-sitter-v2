# RaPToR-Lite Phase 4: Constrained Natural-Language Task Creation

`OfflineHouseSitterPlanner` is an offline deterministic grammar, not a general language model.  It normalizes English case and punctuation, recognizes a deliberately small House-Sitter vocabulary, extracts supported room aliases, and produces a `TaskSpec` only for a mapped intent.  It never imports a backend, ROS, or a code-execution interface.

Supported intents are complete House-Sitter monitoring, patrol, inspection, baseline establishment, room-scoped change detection, and safe return.  Supported rooms are living room, kitchen, bedroom, bathroom, and charging area.  The complete workflow establishes all household baselines, activates only controlled simulation events, revisits requested rooms, then detects, updates the Digital Twin, alerts, returns, stops, and reports.

Every generated task has declared capability timeouts.  The planner adds `return_to_start`, `stop`, and `generate_monitoring_report` when absent, and records each automatic addition in `PlanningResult`; it does not imply that those words came from the user.  `planned` candidates always go through `verify_task` before `BackendExecutor` or `MockExecutor` can run.  Clarification, unsupported, invalid, and verifier-rejected results never execute.

Requests for unknown rooms, cameras, arms, doors, physical robots, arbitrary code, verifier bypasses, safety disablement, omitted return/stop, or unbounded patrol are rejected or clarified rather than guessed.  The text `ignore the verifier` is data for the policy rule, not an instruction that changes it.

`normalized_task()` compares semantic task contents while excluding display identifiers: skill order, parameters/rooms/checks, timeouts, failure policies, return, stop, and report remain significant.  This supports controlled paraphrase checks.

The optional future extension point is a provider that may propose a `TaskSpec`; it must still return `PlanningResult` and use the same Verifier boundary.  No LLM provider, credentials, prompts, or external API are used in Phase 4.  All execution remains deterministic, simulation-only, and `physical_robot_validated=false`; this does not establish real-robot performance or broad natural-language coverage.
