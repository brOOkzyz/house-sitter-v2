# Reproducibility

The formal study is defined by `configs/raptor_lite/phase6_formal_protocol.yaml`, its locked RQ1/RQ2 inputs and RQ3 seed manifest, and the scripts under `scripts/phase6_*.py`.

Run the protocol checks from the repository root:

```bash
PYTHONPATH=. python scripts/phase6_formal.py preflight
PYTHONPATH=. python scripts/phase6_replay_addendum.py preflight
PYTHONPATH=. python scripts/phase6_rq3_correction.py preflight
PYTHONPATH=. python scripts/phase6_rq3_corrected_materializer.py audit
```

The published evidence pack is `results/raptor_lite/phase6_formal/phase7_results_freeze/`; `results_manifest.json` hashes every published summary, table, figure, and claim audit. The matching raw RQ1/RQ2/RQ3 records are published in `results/raptor_lite/phase6_formal/raw/` for independent recomputation. Each `raw_hash` is the SHA-256 of canonical JSON experiment payload (`records` for RQ1/RQ2; `{records, replays}` for RQ3), rather than the byte hash of its provenance wrapper. Pilot and intermediate generated records remain excluded because they are not part of the final evidence pack.
