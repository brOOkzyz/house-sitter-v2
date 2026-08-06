# Reproducible evaluation plan

## Common protocol

Every run writes code revision, schema version, profile, seed, generated scenario, ground truth, task, verification report, trace, result and first failure. Split generator seeds into development and held-out test seeds. Report point estimates with bootstrap 95% confidence intervals over independent seeds; do not count repeated identical deterministic executions as independent evidence. For paired comparisons, run both methods on the identical scenario/seed and analyse per-seed differences.

Existing fixed deterministic scenarios and paper-result renderers are retained as regression tests and fixture validation only.

## RQ1 — verifier effectiveness

Generate labelled valid/invalid `TaskSpec` cases over: unknown skills, missing/unknown parameters, wrong types, numeric bounds, illegal enums, unavailable dependencies, missing/nonpositive timeouts, invalid failure policies, absent/finally missing safe return, duplicate step IDs, unsupported adapters and simulation/physical mismatches. Include boundary values exactly at and just beyond limits.

- **Ground truth:** generator label and violated rule set.
- **Metrics:** acceptance precision/recall/F1; invalid-rejection recall by issue code; false accepts; false rejects; issue-code localisation accuracy; verification latency.
- **Analysis:** confusion matrix and failure slices; mutation families kept disjoint between development and held-out cases.

## RQ2 — task creation and natural-language reliability

Use a constrained natural-language adapter as an optional candidate generator, never executor. Construct paraphrase sets for supported intents, ambiguous requests, multi-intent requests, unsupported requests and requests that imply physical control. For each intent, retain a canonical expected `TaskSpec` or expected rejection class.

- **Metrics:** parse/plan validity, verifier approval rate, same-intent consistency, correct rejection rate, execution success conditional on approval, unsupported/ambiguous safety rejection recall.
- **Paired comparison:** deterministic rule adapter versus any future model-backed adapter on the same prompts/seeds; report abstention separately from incorrect plans.
- **Evidence limit:** model/version/prompt and raw structured output must be stored. No LLM output bypasses the verifier.

## RQ3 — House-Sitter reliability

Create a seeded 2D household scenario generator. Per seed it samples rooms/route, one or more obstacle events, temperature/humidity deviations, event start/duration, sensor noise, missing observations and battery state. It emits ground-truth event timelines independently of detector output.

- **Metrics:** event precision/recall/F1 by type/room/timestep; time-to-detection; false-alert rate; Digital Twin field precision/recall and stale-state duration; alert action correctness; report factual consistency; safe-return completion; task/execution failure rate.
- **Paired comparisons:** detector thresholds/temporal policies or task policies under identical seeds; use paired bootstrap CIs for metric deltas.
- **Failure analysis:** retain every false positive/negative, unavailable observation, verifier rejection, missed event and unsafe-return failure with trace links.
- **Scenario controls:** documented distribution, seed range, event prevalence, room topology, battery policy and noise model. Fixed `evaluation/*.json` cases remain regression anchors, not the sampling distribution.
