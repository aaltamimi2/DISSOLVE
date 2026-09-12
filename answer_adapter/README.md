# answer_adapter

Synthetic two-arm adapter for WP-2b. Injected model and executor only. No product import, network, keys, corpus, or real guard.

## Exports

`build_offer`, `load_prompt`, `check_draft`, `new_ledger`, `present_result`, `normalize_draft`, `run_question`, `config_echo`, `resolve_substrate`

## Pins

- package `cf950a384de54f700789c3e1d5634cc7d15679e8`
- handoff `HANDOFF.wp2b.v3.md` `a6cf298`
- spec `RAG_AUDIT_SPEC.v2.5.2.md`
- fixtures `FIXTURES.adapter.v3.json` (38)
- runtime `/home/aaltamimi2/anaconda3/bin/python` (3.11.9)

## Reproduction

Git identity is a preflight outside this process. Pass that 40-hex SHA as `--candidate`. From this checkout, with the pinned interpreter:

```
/home/aaltamimi2/anaconda3/bin/python tests/answer_adapter/reproduce.py --candidate <40-hex-commit>
```

The runner derives ROOT from its own path and does not spawn git. Adversarial demonstrations are the same command. Caches, if any, stay under `answer_adapter/.cache/`.
