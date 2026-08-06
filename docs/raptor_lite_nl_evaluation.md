# Phase 4 Preliminary Controlled Corpus Evaluation

This is a **preliminary controlled corpus evaluation**, not a real-user study, a representative language benchmark, or a final paper conclusion.  It evaluates the versioned `examples/raptor_lite/nl_planning_corpus.json` against the deterministic offline planner.

- total examples: 13
- planned: 7
- needs clarification: 1
- unsupported: 5
- invalid: 0
- expected status accuracy: 1.0
- intent accuracy: 1.0
- room extraction accuracy: 1.0
- valid plan rate: 0.5384615384615384 (approved plans / all corpus requests)
- verifier approval rate: 1.0 (approved plans / planned requests)
- unsafe request blocking rate: 1.0
- paraphrase semantic consistency: 1.0 (one five-request complete-workflow group)

The corpus deliberately includes standard requests, five paraphrases, selected rooms, ambiguity, an unknown room, unsupported hardware, verifier bypass, code execution, and an unbounded-patrol request.  It is engineering coverage only.  Its metrics should be recomputed from the corpus through `raptor_lite.nl_evaluation.evaluate_corpus` whenever the grammar or corpus changes.
