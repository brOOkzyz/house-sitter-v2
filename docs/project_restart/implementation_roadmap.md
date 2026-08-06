# Implementation roadmap and checkpoints

| Phase | Goals / scope | Inputs and modules | Acceptance evidence | Stop condition / fallback / risk |
|---|---|---|---|---|
| 0 — audit | Source audit, restart definition, backend decision | ZIPs, repository, docs | This document set; clean tests | Stop if source claims cannot be separated from evidence; preserve archive. |
| 1 — verified core | Audit/fix only Phase 1 registry/schema/verifier/mock/artifacts | `raptor_lite/`, tests | CLI + unit tests; no ROS imports | Stop if strict rejection/traceability regresses; keep mock-only core. |
| 2 — 2D backend | One seedable backend protocol and household generator | New isolated backend package | Headless seeded trace + ground truth + paired deterministic test | Stop if protocol requires ROS; fallback to existing deterministic monitoring fixtures. |
| 3 — House-Sitter integration | Adapt existing monitor/twin/alert/report through backend | Existing monitoring modules | End-to-end approved-task traces, simulation boundaries | Stop if logic must be duplicated; keep adapter thin. |
| 4 — NL creation | Optional constrained adapter -> `TaskSpec` | Existing offline adapter first | Prompt corpus, rejection and consistency tests | No direct execution; fallback CLI task authoring. |
| 5 — visual demo | Optional best-effort visual backend | Existing Gazebo-without-Nav2 or 2D renderer | Separate smoke artifact; cannot gate experiments | Stop at instability; fallback deterministic 2D visualization. |
| 6 — experiments | Seeded RQ1–RQ3 suite, CIs, failure corpus | Generator, evaluator, reports | Held-out seeds, paired tables, reproducible manifests | Stop if ground truth/seed absent; retain regression-only claims. |
| 7 — paper/final audit | Results, limits, independent review | Docs, artifacts, code revision | Re-run from clean environment; claim audit | Do not claim physical validation without new evidence. |
