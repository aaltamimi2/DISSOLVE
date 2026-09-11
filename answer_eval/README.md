# answer_eval (WP-1, synthetic)

Pins (rehashed at load): HANDOFF.wp1.v8 `955b45ae…d928b9`; package `4038579`; DISPATCH.wp1.v1 `f60bb847…1fe7`; worktree `849ecdc`; runtime Python 3.11.9 (`97f591df…`) / numpy 2.4.6 / jsonschema 4.19.2 (dispatch erratum ADMIT). Production writer `EVAL_PUBLIC.v7`. 101 fixtures / 55 adversarial. Allowlist: `answer_eval/**`, `tests/answer_eval/**`.

## Checkpoint

- Guard stand-in copied (`tests/answer_eval/synthetic_guard.py`, digest `8f434541…`).
- Components: canon, ids, equal, order, resample, emit, perturb, stratum, reference, atomize, match, support, states, score, evaluate, reproduce, adversarial, mutation.
- Fixtures 101/101. Adversarial 55/55 active (nonzero demonstrations; row 35 is the string-valued-endpoint mutation on F-RS-1). Production B=2000 unit-tested. Guard 0 on RESULTS/MANIFEST.
- Artifacts: `RESULTS.fixtures.v1.json` `b20c6ee3…c11f38`; `MANIFEST.v1.json` `66c65a0d…84ef35`.
- Next: allowlisted commit when asked. Builder pass is not acceptance.
- Do not edit author fixtures. No src imports, no real guard exec, no network.

