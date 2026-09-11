# answer_eval (WP-1, synthetic)

Pins (rehashed at load): HANDOFF.wp1.v8 `955b45ae…d928b9`; package `4038579`; DISPATCH.wp1.v1 `f60bb847…1fe7`; candidate parent `8b3ed58`; runtime Python 3.11.9 (`97f591df…`) / numpy 2.4.6 / jsonschema 4.19.2 (dispatch erratum ADMIT). Production writer `EVAL_PUBLIC.v7`. 101 fixtures / 55 adversarial. Allowlist: `answer_eval/**`, `tests/answer_eval/**`.

## Checkpoint

- Guard stand-in copied (`tests/answer_eval/synthetic_guard.py`, digest `8f434541…`).
- Components: canon, ids, equal, order, resample, emit, perturb, stratum, reference, atomize, match, support, states, score, evaluate, reproduce, adversarial, mutation.
- A-2/A-3/A-4 and row 35 closed. A-1 remainder: `score_question` computes M-10 row recall/precision (observation rows, all M-1 atoms, family/dedup/merge) and the adapter copies those fields; exported `score`/`emit_public` project `M-10` and `M-10_precision`. M-7 `subtypes` and `partial_fault_questions` are pooled into the public aggregate.
- Fixtures 101/101. Adversarial 55/55 active. Production B=2000 unit-tested. Guard 0 on RESULTS/MANIFEST.
- Artifacts: `RESULTS.fixtures.v1.json` `b20c6ee3…c11f38`; `MANIFEST.v1.json` `66c65a0d…84ef35`.
- Builder pass is not acceptance. Do not edit author fixtures. No src imports, no real guard exec, no network.
