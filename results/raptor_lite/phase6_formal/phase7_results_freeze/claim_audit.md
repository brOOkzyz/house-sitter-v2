# Phase 7 claim audit

Approved wording:
- “Mission completion was 61.7% (185/300 held-out seeds).”
- “All 300 held-out outcomes were completed, safely deferred, or safely terminated for a blocked route.”
- “The locked primary per-seed F1 was 0.403; 0.629 is post-hoc dropout-semantic sensitivity only.”
- “Resource policy prevented 22.0% hypothetical unsafe attempts; no real unsafe execution was performed.”

Prohibited wording:
- “100% mission success/reliability.”
- “0.629 is the primary F1.”
- “Resource policy observed unsafe execution.”
- “RQ1 safe-repair success was established from the frozen raw data.”

Traceability limitations:
- RQ1 safe-repair success is not represented in raw/rq1.json and is therefore N/A in this evidence pack.
- Dropout uses `missing_observation` at detection time while the locked primary truth excludes `observation_dropout`; the sensitivity is explicitly exploratory.
