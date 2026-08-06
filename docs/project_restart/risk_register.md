# Risk register

| Risk | Likelihood / impact | Mitigation and stop condition |
|---|---|---|
| Source draft claims mistaken for code evidence | Medium / high | Maintain source labels and audit; stop publication claims not backed by repository/artifacts. |
| Scope grows into a general RaPToR platform | High / high | One registry/schema/backend protocol; reject UI, installation, multi-robot and unrestricted generation work. |
| Deterministic repeats overstated as reliability | High / high | Seeded generator, held-out scenarios, CIs and paired tests; downgrade old runs to regression-only. |
| LLM bypasses verification | Medium / high | Candidate-only interface; executor accepts approved TaskSpec only; test rejection path. |
| Gazebo/Nav2 destabilises research schedule | High / medium | Make it non-gating visual/archive backend; use pure 2D experiments. |
| Synthetic observations presented as physical sensors | Medium / high | Preserve simulation boundary fields and paper limits; stop any physical claim without evidence. |
| Duplicate legacy/new task abstractions drift | Medium / medium | Keep RaPToR-Lite authoritative for new work; adapt, do not merge, legacy skills. |
| Backend generator lacks credible ground truth | Medium / high | Generate truth independently and store it; no RQ3 metric without it. |
| Phase 1 dependency/environment drift | Medium / medium | Keep `requirements-raptor-lite.txt`, test in an isolated environment, and add packaging/CI only when the core API stabilises. |
