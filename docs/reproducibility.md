# Reproducibility

The formal study is defined by `configs/raptor_lite/phase6_formal_protocol.yaml`, its locked RQ1/RQ2 inputs and RQ3 seed manifest, and the scripts under `scripts/phase6_*.py`.

Run the protocol checks from the repository root:

```bash
python scripts/phase6_formal.py preflight
python scripts/phase6_replay_addendum.py preflight
python scripts/phase6_rq3_correction.py preflight
python scripts/phase6_rq3_corrected_materializer.py audit
```

The published evidence pack is `results/raptor_lite/phase6_formal/phase7_results_freeze/`. `results_manifest.json` hashes every published summary, table, figure, and claim audit. Raw per-run records are excluded because they are large, redundant, and include local execution provenance; they can be regenerated from the retained protocol, source, inputs, and seed manifests.
