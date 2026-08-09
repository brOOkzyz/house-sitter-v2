# Evidence Inventory

## Authority order

1. Frozen Phase 6 raw records and their manifests/hashes.
2. Locked `analysis/summary.json` for confirmatory intervals, effects and tests.
3. Phase 7 Results Freeze for outcome reconciliation, claim wording and presentation artefacts.
4. Current implementation and tests at Git HEAD `85179bdbe996053ec69ef5b0aca748812fdb8019`.
5. Freeze/protocol/deployment documentation.
6. Project25 brief and early RaPToR Overleaf material for motivation/history only.

## Formal sources

- Protocol hash: `25a16e15fc07c2c9d3c76e52de067ca47f09950ac532bbbf1f14e611753c2847`.
- Analysis-plan hash: `fc1e1e1e20817435a80c0886715dcb25ce4ee3844e0ecf64f15c7189f34f9594`.
- RQ1 logical raw hash: `76e4753f367596b3bda762badf2eb9b350779e469ae8e9e7fb520ee0ac055661`.
- RQ2 logical raw hash: `c4572ccd180ad454d15725351284497fa6f3118d817b00d283986a81ac89351a`.
- RQ3 logical raw hash: `ec9d077491c6e705456d84ce0c46be2bd241bad7eb3420401ef495c3db65e99a`.
- RQ3 implementation-correction hash: `cae4f103e4777485397ec3b239d108c428fc20c2ebea994d1aa0c0afc9f550f9`.
- Replay addendum hash: `68dd987fe6d8a4f9fd1e06b281f119e75d2640a481bc977f06a9dcae0767d57a`.
- Canonical Phase 7 results-manifest hash: `dc249cfff4b8f9bab90173555fb9860f96b22381fee64ff792ad9599e5499dec`.

## Resolved source conflicts

- The current `RobotObservation` allow-list and RQ3 correction provenance supersede the older House2D document that mentioned simulator identifiers in observations.
- Confirmatory confidence intervals and statistical tests use the locked Phase 6 analysis summary. Phase 7 table endpoints produced by a different presentation bootstrap are not mixed into confirmatory prose.
- The Project25 brief contains a transposed DOI for *Beyond Vacuuming*. The dissertation uses the verified ACM DOI `10.1145/3706598.3714266`.
- The original RaPToR Overleaf archive is a design-motivation draft, not evidence of runnable source or experimental results.

## Protection

The build reads but never writes `results/raptor_lite/phase6_formal`. A complete SHA-256 snapshot was taken before dissertation work and is checked again at submission audit. Pilot directories remain separate and untracked.

