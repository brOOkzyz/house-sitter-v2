# RaPToR-Lite Phase 6A: formal experiment protocol

This is Revision 2 of the pre-registered execution plan, not a result. It is pinned to frozen
HEAD `3540c8922a840f43df5292e3df81ce662d66be20`. Every formal artifact must be
written below `results/raptor_lite/phase6_formal/` with
`phase6_formal=true`, the frozen HEAD, protocol/analysis-plan hashes,
RQ/condition/seed/cohort, ground truth, manifest and reproducibility hash.
Pilot directories are excluded. Future formal manifests must copy every field
listed in `locks.future_manifest_fields` in the protocol config.

Revision reason: pre-execution clarification of non-executing counterfactual
ablation semantics; no formal results existed before revision. The locked
inputs are `phase6_rq1_cases.json`, `phase6_rq2_corpus.yaml` and
`phase6_rq3_seed_manifest.json`; the deterministic generator and integrity
audit are `scripts/phase6_materialize_revision1.py`.

RQ1 grounding and verifier ablations emit only `would_accept`, `would_reject`,
`would_clarify`, counterfactual issues and ground-truth correctness. They never
invoke an executor. RQ3 route optimisation is a real paired House2D execution;
the resource-policy ablation records only whether a no-policy system *would*
attempt a constraint-violating task. It never executes a full-policy DEFER
case, and its result must not be called real unsafe execution.

## RQ1 — Verifier effectiveness

Use 240 held-out, manually labelled task cases: 48 each valid, invalid,
unsafe, unsupported and ambiguous. Execute the same cases under full system,
capability-grounding ablation and verifier ablation (720 logical evaluations).
The 80 repairable rejected cases receive one bounded safe repair proposal.

Ground truth is the allowed/rejected decision, expected TaskSpec where allowed,
and issue family where rejected. Primary metrics are acceptance and rejection
precision/recall/F1, false rejection/accept rates, issue-family confusion and
safe-repair success. Ablations are counterfactual decision records; they never
bypass the frozen executor safety gate.

## RQ2 — Natural-language reliability

The locked corpus source is `configs/raptor_lite/phase6_rq2_corpus.yaml`. Its
40 target rows crossed with eight named forms define 320 predeclared language
forms: 40 each of
canonical, paraphrase, synonym, explicit-order, unordered-room, ambiguity,
unsupported and unsafe/verifier-bypass requests. It covers 40 semantic targets
with a locked target TaskSpec or expected clarification/rejection decision.

Report micro-level metrics over all 320 utterances. For macro metrics, CIs and
all comparisons, group the eight forms by their 40 semantic targets; the eight
paraphrases are not treated as independent observations. Report intent and
parse correctness, exact TaskSpec match after canonical JSON normalisation,
clarification/rejection accuracy and end-to-end correctness. Slice all metrics
by language form and semantic target; no claim is made about open-ended natural
language beyond this corpus.

## RQ3 — Stochastic House-Sitter reliability

Use 400 independent scenario seeds (100 development, 300 held-out). The 100
development seeds are only for implementation checks: they are never included
in paper success, F1, CI, p-value or effect-size results. Confirmatory RQ3
results use only the 300 held-out seeds, with 300 full-system records, 300
paired route-optimisation counterfactuals and 300 paired resource-policy
counterfactuals. The development checks use the same three conditions over
their 100 disjoint seeds; 40 replays are selected from held-out seeds. This is
1,240 logical records total. Replays test determinism and are not independent
observations.

Measure mission outcome, event TP/FP/FN/F1, Twin correctness, feedback leakage
(any detector/feedback use of ground truth is a failure), route efficiency,
safe failure and reproducibility. The resource-policy ablation remains
decision-only because the frozen core deliberately makes unsafe ablations
non-executing.

## Analysis and stopping rules

Primary metrics are locked in the protocol config; remaining named metrics are
secondary. For binary primary metrics report exact counts and 95% Wilson
intervals. For seed-level rates, paths and durations report
n/mean/std/median/IQR/p05/p95 plus 10,000-resample percentile bootstrap 95%
CIs. Every primary comparison also reports its effect size: accept/reject,
unsafe-catch and false-rejection absolute differences (percentage points);
paired route-cost and route-efficiency differences; and safe-defer and
unsafe-execution absolute differences (percentage points). Compare paired
conditions via paired bootstrap deltas (and exact McNemar as secondary for
binary outcomes). Per RQ, use Benjamini–Hochberg FDR at q=0.05 for the declared
primary family; use a separate BH q=0.05 family for secondary and slice claims.
Intervals remain unadjusted descriptive 95% CIs.

Failure taxonomy is locked in the protocol config: false accept/reject, unsafe
miss, unsupported-capability claim, clarification error, TaskSpec mismatch,
unavailable/stale observation, detector TP/FP/FN, feedback leakage,
illegal/blocked route, safe-return/dock failure, Twin mismatch, resource-policy
unsafe execution, timeout/communication failure and reproducibility mismatch.
Preserve every failure with trace links. Stop formal execution and do not alter
frozen core if a Critical/High safety defect, absent seed/ground truth, missing
locked metadata, or reproducibility failure occurs.

## Lock and analysis entry

`python scripts/phase6_formal.py lock-hashes` derives the two SHA-256 canonical
JSON hashes; `preflight` verifies them and `analysis-plan` emits the locked
primary/secondary metrics, taxonomy and FDR plan. Formal execution remains
disabled in Phase 6A.

The 240 and 320 classification samples have worst-case 95% Wilson half-widths
of roughly 6.3 and 5.5 percentage points. Three hundred held-out RQ3 seeds
allow bootstrap uncertainty and useful failure strata without treating replay
runs as evidence.
