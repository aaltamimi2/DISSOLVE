# DISSOLVE v12 — agent runtime spec

**Author:** `cursor-v12-builder-1`  
**Status:** spec only. Do not build from this file until the orchestrator
clears a build pass by name.  
**Authority:** the owner's build spec and handle/bounds/compaction delta in
`HARNESS_BRIEF.md` Part 1, as amended by the post-brief decisions in
§11. Where this document and Part 1 still disagree, the disagreement is
named there. The post-brief decisions win. Auditor OBJECT findings at
seal are mapped in §14 (STANDING / SUPERSEDED / answered). Do not
re-derive the audit from this file; the table is the index.

This is a default plus its exceptions. The default is Claude Code. Where this
file is silent, do what Claude Code does. If a later question is not answered
here, the answer is not a new module — it is the Claude Code behaviour for
that question.

What this file rejects as a shape: a per-tool essay, a routing table, a
second session channel, a validator framework, and any design that would let
acceptance test 5 pass through a path real queries do not use.

---

## 0. The default

A model, a tool list, and a message array. The model either calls a tool or
it writes prose. A tool result appends. There is no plan object, no schema
for the conversation, no specialist, no DAG, no state machine, no router.

```
messages = [system, user]
for round in 1..30:
    reply = complete(messages, tools)
    if reply is prose:
        scan numerals against tool results; print any flags; do not gate
        stop
    for each tool call in reply, sequentially:
        run the tool
        append the model-facing result
stop if the round cap hits; do not invent an answer
```

Print is the caller's. The loop returns the scan; it does not write
stdout. The one-shot `__main__` is a caller.

State lives in two places, the same two Claude Code uses: the message array,
and a world the tools can read. Here the world is one plain dict, the session
record, already begun in `src/dissolve/session.py`. Tools see exact objects.
The model sees a small projection. A downstream tool never reads the
projection.

The three domain departures from Claude Code, and only these:

- **A.** Tools refuse instead of best-efforting. Exact match against alias
  tables. Ambiguous input refuses. No defaults, no nearest-neighbour
  substitution, no interpolation.
- **B.** Every success carries a `source_basis` string that is honest
  for that result. A tool with no honest source refuses. The key is
  not `basis`. That name is already taken, in three modules, with
  three meanings (§3.5). This is a correction to Part 1's contract
  key, not a question.
- **C.** **The agent does not verify itself.** Owner directive: no
  verification in the agent; whatever validates model results is an
  external harness. So Part 1's "you write the verifier, because
  there isn't one" no longer holds — there is one, it is outside,
  and it is not this system's.

  What replaces it is an obligation, not a component: **every tool
  result of a turn must be recoverable exactly** (§4.6), so an
  external validator can check an answer against what the tools
  actually returned. The agent keeps an honest record and forms no
  opinion about it. Building any in-agent grader, scorer or gate is
  the restrictiveness this directive removes.

Everything below is an instantiation of that default, or a named exception
to it. If a paragraph does not name what it rejected, it is unfinished.

---

## 1. File layout and budgets

| file | job | ceiling |
|---|---|---|
| `agent_harness.py` (repo root) | the loop, the provider call, the turn record (§4.6), `python agent_harness.py "<query>"` | none — orchestrator (D-2) lifted the 199-line ceiling |
| `agent_tools.py` (repo root) | the one generic wrapper, `result_read`, `source_basis` attachment, envelope reshape, handle issue, system prompt constant, turn-record plumbing (§4.6) | no fixed ceiling — see below |
| `src/dissolve/cli.py` | retained v11 presentation | not in budget |
| `src/dissolve/session.py` | already exists; add the binder and the handle table | not a new file |

**Ceilings, revised by owner (`agent_tools.py`) and orchestrator D-2 (`agent_harness.py`).** Part 1 set 200 / 250 / 80 when
`agent_tools.py` meant "the one generic wrapper" and a `research_agent.py`
existed. Since then the file also carries `result_read`, `source_basis`
attachment, handle issue and the §4.6 turn record, and the research sub-agent
was deleted (§3.3) — so its 80 lines were never spent. **Owner: more lines are
fine.** That sentence is about `agent_tools.py`; it has no fixed ceiling.
**Orchestrator (D-2), unconditional: the 199-line `agent_harness.py`
ceiling is lifted.** That is a different budget. The harness ceiling was
being paid in collapsed dataclasses and a dropped `Callable` annotation,
which made the six-field seam harder to audit than a longer readable loop.
The architectural claim that remains is one small loop file, not a number:
a fourth harness file is still stop-and-ask. The loop is presently 263
lines; that is not a breach. §1 and §14 row 7 both say this; neither
still claims 200 or 250 as a standing ceiling, and neither attributes
the lift to the owner.

What this does **not** relax is the stop-and-ask rule. The requirement was never
the number — it was saying so when you cross one. A fourth file is still a
stop-and-ask. A ceiling exceeded in silence is drift; a ceiling renegotiated with
a reason is a decision, and D-2 is that orchestrator decision.

`src/dissolve/tools.py` is 1,607 lines of thermodynamics engine (counted
this pass). The budget line the owner named `tools.py` is **not that
file**.

**Rejected:** naming the wrapper `src/dissolve/tools.py` (it is the engine).  
**Rejected:** naming the wrapper repo-root `tools.py` (a later `import tools`
from the wrong directory is a silent collision I will not leave on the
table).  
**Rejected:** `research_agent.py`. Part 1 asked for one sub-agent file.
The sub-agent is dropped (§3.3); a third harness file is not wanted
back under another name.  
**Rejected:** a fourth harness file. If the system prompt and the loop cannot
both fit in `agent_harness.py`, the prompt constant lives in
`agent_tools.py`.  
**Rejected:** putting the loop under `src/dissolve/` such that there is
no repo-root one-shot.  
**Rejected:** keeping a stub `main.py` that re-exports the loop. That
is a costume of the Part 1 filename, and a fourth file.  
**Rejected:** a class named `AgentHarness`. v11's is cut. This file
exports `run_turn` and `TurnResult`. The name is the module.

The CLI does not count against the budget. It is a retained v11 asset, not
new scaffolding. It may not grow a harness inside itself: the only call it
makes into the loop is `run_turn(...)`. See §9.

`session.py` is the session record. Handles live there. Editing it is in
scope for the later build pass and is not a new file. It is still subject to
the standing freeze on `thermo.py` / `test_thermo.py`, which this spec does
not lift.

Run with `PYTHONPATH=src`. Do not add LangChain, LangGraph, or a config
system. `pyproject.toml` today depends on `duckdb` only; provider SDKs are
CLI/loop dependencies and are declared when we build, not invented as a
plugin registry.

---

## 2. The loop

`agent_harness.py` exports one function and one result type. That is the seam.

```python
@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: dict
    result: dict          # the model-facing payload, not the exact object

@dataclass(frozen=True)
class TurnResult:
    answer: str
    status: str           # ok | round_cap | provider_error | compaction_error
    tool_trace: list[ToolEvent]
    turn_record: str          # id of the exact per-call record (§4.6); nothing here reads it
    tool_rounds: int          # display-only at the CLI (§9.1)
    usage: dict | None        # display-only at the CLI (§9.1); None means unmeasured
```

```python
def run_turn(
    query: str,
    *,
    session: dict,
    model: str,
    messages: list | None = None,
    on_event: Callable[[ToolEvent], None] | None = None,
    api_base: str | None = None,
    api_key_env: str | None = None,
) -> TurnResult:
    ...
```

`session` is the plain dict. `model` is the already-resolved id
(`openai:muse-spark-1.2`, `anthropic:…`, `google_genai:…`), not an
alias. The CLI resolves aliases; the loop does not call
`resolve_model`. `api_base` / `api_key_env` come from the same
`ModelSpec` row the CLI already has (muse needs
`https://api.meta.ai/v1`). The one-shot `__main__` lazy-imports that
table from `cli.py` so the files do not import each other at module
level.

`tool_rounds` is the number of tool-call rounds in this turn, counted
inside the loop, not inferred from a history compaction can edit.
`usage` is the folded provider usage for this turn (§9.4). Both exist
so `/cost` can print what the last turn did. They are not a decision
surface.

`messages` is the Claude Code message array, persisted by the CLI if
the CLI wants a multi-turn conversation, ignored by
`python agent_harness.py "<query>"` which starts empty. `on_event` is how the tool
trace reaches a console. The loop does not print. The caller prints
the answer and the tool events. Nothing carries a score.

### 2.1 One round

1. Bind the session dict for the duration of the turn
   (`session.bind_tool_session(session)` — see §4). This is the missing
   setter: today `_ACTIVE` is private and `contextmanager` is imported in
   `session.py` and never used. I measured `current_tool_session()` as
   `None` outside a binder.
2. `complete(messages, tools)` — one provider call. No LangChain.
3. If the reply is prose: that prose is the answer, `status="ok"`.
   Nothing inspects it. No scan, no bucketing, no grading. The turn
   record (§4.6) already holds every tool result exactly, which is
   what an external validator reads. Do not retry.
4. If the reply is one or more tool calls: run them **one at a time, in
   the order the model emitted them**. Append each model-facing result
   before the next call. Compact **once after the batch**, before the
   next `complete` — not between calls. A `compaction_error` return
   still requires every emitted tool-call id to have its matching
   result; do not invent a result for a call that did not run, and do
   not drop a half-answered assistant tool-call message. The turn
   record (§4.6) already holds every executed call. This is the TEA
   constraint (§T6) and also Claude Code. Fan-out is a crash, not a
   speedup.
5. After 30 tool-call rounds, stop with `status="round_cap"`. The answer
   names the cap and what was already retrieved. It does not guess.

A "round" is one model reply that requested tools, plus the sequential
execution of those tools. `result_read` counts.

**Rejected:** a retry / fallback framework around the scan or
round-cap.  
**Rejected:** parallel tool execution, `asyncio.gather`, worker threads.
The engines already run TEA live as a ~760 MB child process;
`evaluate_tea_lca_scenarios` already loops scenarios sequentially. The
harness must not introduce a second concurrent TEA path. I measured no
`flock` in v12 `tea.py` — the brief's flock shim lives in the v11 sweep
script, not in this tree. Sequential execution is therefore the only
lock.  
**Rejected:** a plan object, a completion-requirement check, a "should I
stop" classifier.

### 2.2 Where it stops

The loop stops when the model writes prose, or when the cap hits, or when
`complete` itself fails, or when compaction cannot meet `window-reserve`
without omitting numbers already reported or cutting the current
user/tool group. Provider error: `status="provider_error"`, the
answer names the failure, do not retry as a framework; a single
transport retry inside the provider SDK is the provider's business and
already exists in v11's `create_model`. Impossible compaction:
`status="compaction_error"`. The answer names the constraint that could
not be met, the target, and what the summary needed. `run_turn` returns
a `TurnResult`; it does not raise. If the reply emitted several tool
calls, the persisted message array is protocol-complete (one result per
emitted id) before that return. The CLI notice Panel that already
renders `round_cap` and `provider_error` renders this the same way.

There is no other stop condition.

### 2.3 `complete`

One function. Branch on the selected model's prefix (`anthropic:`,
`google_genai:`, `openai:`). Return `{text, tool_calls, usage}`.
`usage` is `None` when the provider returned no usage object, and
otherwise the normalized dict in §9.4. Absent is not zero: do not
replace a missing object with `0` or with a dict of zeros. This is not a
provider abstraction layer — it is the three `if`s Claude Code would
write. Do not introduce a `Provider` base class.

The OpenAI adapter must keep the `chat.completions.create` return
value long enough to read `.usage`. Binding only `.choices[0].message`
drops the object that carries tokens.

The CLI resolves the alias and checks the env var, then passes the
resolved `model` / `api_base` / `api_key_env` into `run_turn`. The
loop does not re-resolve. `/model` is a CLI alias change; the next
`run_turn` receives the new resolved id.

---

## 3. The tool table

### 3.1 Default: one wrapper, not thirty-four

`src/dissolve/registry.py` is already the flat list. After
`normalize_feed_composition` was deregistered (`9fef8bc` / `369d5ff`),
`REGISTRY` has 33 names. The helper remains for internal callers; it
is not on the public surface because it does not return the JSON
envelope. Nothing in that file decides anything. Keep it that way.

The model-facing tool list is every registry entry, plus
`result_read(handle: str, offset: int = 0, limit: int = 20)`.
All 33 engine functions are executable on the root. There is no
`research()` tool. See §3.3.

One generic wrapper, `agent_tools.dispatch(name, **kwargs)`, does the same
thing for every engine function:

1. If `name == "result_read"`: page the stored exact object (§4).
2. If `name` is in the unwired set (§5.4): return `tool_not_wired`
   and do not call the engine.
3. Otherwise: `registry.call(name, **engine_kwargs)` after the handle bind
   in §4.4. Catch only what a tool body can actually raise
   (`TypeError`, `ValueError`, `KeyError`, and the JSON errors
   `parse_tool_result` raises). A bare `except Exception` is how v11 kept
   a fallback dead. An unexpected exception becomes
   `{available: false, refusal: "tool_exception", error: <type and message>}`
   so the model can recover in one step.
4. Parse the inherited v11 envelope with `contracts.parse_tool_result`.
5. Reshape to the model-facing contract (§3.4).
6. Attach `source_basis` by the rule in §3.5.
7. Apply the ambiguous-family guard in §5.3.
8. Maybe issue a handle (§4.3).
9. Return the model-facing payload.

Schemas are generated from `inspect.signature` and the first docstring
line (`registry.Tool.summary` already has this). Do not hand-write 34
schemas. Omit parameters that are not model-facing. Measured:
`temperature_step_c` is annotated `InjectedToolArg` on
`screen_polymer_separation`, `screen_pairwise_solubility_overlap`,
and `plan_multistage_separation` (a langchain leftover in the
engines). Drop that name from the generated schema. Do not import
`langchain_core` to discover it, and do not add langchain as a
harness dependency.

If a model-facing tool accepts `handle`, add that parameter to
the generated schema; do not add it to the engine function. The
measured set is one engine function:
`compare_solvent_safety_at_conditions` (`candidates=None` inherit via
`candidate_evidence`). That is test 5's consumer. If another engine
later grows a `candidates` parameter that `candidate_evidence` can
fill, it gets `handle` the same way. Do not add `handle` to all 34.
If the name is in the unwired set (§5.4), prepend to the generated
description: `UNWIRED. Returns tool_not_wired. Reads session state
through an interface v12 removed; pending an engine pass to accept a
handle. Do not call this to recover a missing route.` The model can
decline the call instead of discovering the refusal per query.

**Rejected:** a wrapper function per tool.  
**Rejected:** a registry of specialists, keyword classifiers, or "this
query goes to safety".  
**Rejected:** an abstract `Tool` base class. The existing `NamedTuple` in
`registry.py` is a record, not a hierarchy; leave it.

### 3.2 `handle` on the model-facing schema

Add an optional `handle: str` to any generated schema whose engine
signature already has a parameter that `candidate_evidence` /
`resolve_candidate_argument` can fill. Measured today: `candidates`
on `compare_solvent_safety_at_conditions`. The wrapper strips `handle`
before `registry.call`.

A tool that does not consume upstream rows does not grow a `handle`
parameter. Point lookup stays `solubility_query(polymers, solvents,
temperatures, ...)`.

**Rejected:** adding `handle` to all 34 schemas "for uniformity". That is
an enumeration pretending to be a default, and it invites the model to
pass a screen handle into a tool that cannot use it.

### 3.3 No research sub-agent

Part 1 asked for exactly one sub-agent, `research(task) -> str`, and
for test 8 to assert that raw hits never reach the orchestrator.

The counterfactual in the previous draft settled it. If the four
retrieve/inspect tools are executable on the root, tests 1–7 still
pass and only test 8 becomes unenforceable — and test 8 exists to
assert the sub-agent property. That is circular: drop the sub-agent,
drop test 8, nothing else moves.

A tool visible on the list that refuses based on the current role is
a router with one rule in it. That is the shape this project has been
keeping out. I named the role-refusal a "boundary" in the last
revision. That was hedging. It is a router.

Claude Code's reason for a sub-agent is a side task whose output
would flood the parent context. Whether literature search does that
here is a measurement, not a premise. Take it later, on a real
conversation, against `shown`/`total` and token estimates. If it
floods, that is when a `research()` loop is justified. Building it
now is the specialist coming back through principle 3.

**Decision:** no `research()` tool. No `research_agent.py`. All 33
registry functions are executable on the root, including the six
research tools. `save_to_corpus` is an ordinary argument the user can
see fire. Test 8 is dropped. The root round cap (30) is the only cap.

**Rejected:** hiding the four names — a specialist door.  
**Rejected:** refusing them on the root role — a one-rule router.  
**Rejected:** a keyword classifier, an intercept, a projector.  
**Rejected:** keeping test 8 after dropping the property it asserts.

### 3.4 Envelope reshape (ratified)

Every engine returns v11's envelope, via `contracts.tool_result`:

```
{"display": str, "data": {tool_name, success, error_code?, ...}}
```

The model-facing contract is:

```
{"available": true,  "source_basis": <str>, "data": ...}
{"available": false, "refusal": <named reason>, ...detail}
```

`agent_tools.to_contract(raw, source_basis)` is the seam:

- `available = data.success`
- `refusal = data.error_code` on failure
- `data` is passed through unchanged
- `source_basis` is attached beside `data`, never written into `data`
- `display` is not copied onto the model-facing payload (§3.6)

This is the resolution, not a hedge. Engines keep the v11 envelope so
the port is purely subtractive and existing golden files stay valid.
The wrapper reshapes. `source_basis` attaches at the seam. It does
not interpret values, invent neighbours, pick a solvent, or default a
missing field.

**Rejected:** changing 34 engines in this pass so they emit the new
shape.  
**Rejected:** leaving the v11 envelope as the model-facing shape.  
**Rejected:** a per-tool translator.

### 3.5 T1 — `source_basis`

I verified the brief's seven-tool sample rather than taking it.

| tool | what is actually on a success |
|---|---|
| `solubility_query` | per-row `source_table` (I measured `extended_solubility`); no envelope `basis` |
| `lookup_hansen_parameters` | `evidence_class = qualitative_hansen_parameters`, `provenance` dict |
| `lookup_glass_transition` | per-row `evidence_class` (`measured_snapshot` or `precomputed_model_prediction`), top-level `provenance` |
| `get_solvent_safety_card` | `provenance` (`source_families`); a field literally named `basis` exists on exposure-limit rows as `"8-hour TWA"` |
| `lookup_admitted_process_records` | `cache_match_status`, `record_basis` (prose), `provenance`; inner `energy_normalization.basis` is a prose sentence |
| `screen_contaminant_leaching` | **has** `provenance` and `model_basis` on the success path (`_base_result`). The brief's "NO PROVENANCE FIELD" is wrong for success. A call with an unsupported contaminant refuses and then, yes, has neither field — that is a refusal, not a missing basis. |
| `lookup_material_database_membership` | **has** `model_basis` (prose). The brief's "NO PROVENANCE FIELD" is true only if you grep the word `provenance`. The tool states a basis. |

So: four field names, tea already using `basis` for a sentence, and the
two tools the brief proposed condemning both already state a source on
success. I will not ship a harness that refuses
`screen_contaminant_leaching` for a measurement error.

The owner's closed enum (`cosmo_rs_grid`, `pubchem_live`,
`tea_cache_exact`, `tea_screening_analog`, `hsp_fallback`,
`contaminant_workbook`, `optimization_workbook`) is the vocabulary the
system prompt must explain. It is not a squash list for 34 tools.
Forcing `lookup_glass_transition` into `hsp_fallback`, or
`lookup_material_database_membership` into `contaminant_workbook`, is
inventing provenance — the thing `registry.py` already refused to do.

**Decision:**

1. The harness key is `source_basis`, beside `data`. It is not
   `basis`. That name is already taken, in three modules, with three
   meanings:

   - `safety.py:295` — averaging time: `f"{hours}-hour TWA"`
   - `analysis.py:1335–1338` — capability description:
     `"weights absent at v10 baseline"`
   - `literature_ingest.py` — a quantity's basis (`"reported"`, etc.)

   Tea also has `energy_normalization.basis` as a prose sentence. One
   required provenance field colliding with any of these is the
   laundering a presence-check cannot catch: the model reads `basis`
   and cannot tell source from TWA from a capability note.

   A one-tool probe of `get_solvent_safety_card` can return zero
   `basis` keys (no exposure-limit rows on that solvent). That is not
   the surface. Grep is. This is a correction to Part 1's contract
   key, not a question.

   Inner `basis` fields stay inside `data`, untouched.
2. Attachment is a **declared default by engine module**, plus a short
   override table, plus one tea special case. It is a lookup, not an
   adapter that reads the payload and invents a label.

Module defaults:

| `registry.Tool.engine` | envelope `source_basis` |
|---|---|
| `thermodynamics` | `cosmo_rs_grid` |
| `separation` | `cosmo_rs_grid` except `lookup_material_database_membership` → `identity_registry` |
| `safety` | `pubchem_live` when the call passed `include_pubchem=True`; `safety_local` when it passed `False` or omitted it (harness-facing default is `False`, see below) |
| `tea` | see below |
| `optimization` | `optimization_workbook` |
| `analysis` | the engine's own `evidence_class` when present, else `analysis_asset` |
| `contaminants` | `contaminant_workbook` |
| `research` | `provider_metadata` |

Tea, the one tool family that legitimately produces more than one of the
owner's tokens:

- `cache_match_status == "exact"` → `tea_cache_exact`
- `cache_match_status == "surrogate"` or `engine_mode == "screening_estimate"` → `tea_screening_analog`
- a live run (`engine_mode == "live"`) → `tea_live` (not in the
  owner's seven; a live BioSTEAM run is a simulation of the named
  configuration and must not be labelled a cache hit).
  **Default parent cannot run live:** Python 3.11.9, BioSTEAM not
  importable in-process. `live_engine_status()` refuses unless
  `DISSOLVE_TEA_PYTHON` points at a ≥3.12 worker (`tea.py:290–325`).
  Live is a subprocess, not `import biosteam` in the harness process.
  The branch stays in the table. Do not skip writing it. A green
  in-process basis-map test is not evidence the live path works. A
  worker live run is.
- a miss is already a refusal; no basis
- mixed rows: envelope `source_basis` is the strongest claim that is
  true of *every* row (if any analog is present,
  `tea_screening_analog`; else if any live, `tea_live`; else
  `tea_cache_exact`). Per-row `engine_mode` remains in `data` so the
  model can report the mix. The system prompt says to.

Hansen: if `evidence_class` is `hsp_random_forest_fallback`, use the
owner's `hsp_fallback`. Curated Hansen stays
`qualitative_hansen_parameters`. Do not rename a curated row into a
fallback.

Safety `include_pubchem`: the engine default is `True`, so the default
card is networked and non-reproducible — two identical runs can
differ. `source_basis` making that visible is the right design. The
**harness-facing schema default is `False`** (wrapper default, engine
unchanged) so hand-validation compares like with like. If the model
omits the argument, the wrapper **passes `False` into the engine
call**. Do not omit and let the engine default (`True`) fire. The
model passes `True` when the live PubChem fields are the thing under
test; then `source_basis` is `pubchem_live`. Do not silently flip a
passed `True` back to `False`. Same default for every registered
safety tool that takes `include_pubchem`:
`get_solvent_safety_card`, `compare_solvent_safety_at_conditions`,
`screen_route_solvent_substitutions`. The fourth,
`screen_green_solvent_candidates`, does not take the argument and
does not fetch PubChem (G-score path). Do not invent a flag for it.

**Rejected:** a per-tool literal of 34 invented enum members.  
**Rejected:** a module-level `BASIS = "..."` constant on `tea` (one
constant cannot cover exact vs analog vs live).  
**Rejected:** changing 34 engines in this pass.  
**Rejected:** refusing `screen_contaminant_leaching` or
`lookup_material_database_membership` on success.  
**Rejected:** naming the harness key `basis` (collides with safety TWA
rows and tea prose in the same payload).  
**Rejected:** writing `source_basis` into `data.basis`.

A tool whose success still has no honest token after that table should
refuse with `refusal: "no_honest_basis"`. I do not currently know of one
on the success path. If the builder finds one while wiring the table,
they stop and ask; they do not invent a token.

### 3.6 T3 — `display`

I confirmed the brief: `~/dissolve-v11-work/src/strap/cli.py` has zero
occurrences of `display`. I confirmed the size: `solubility_query`
LDPE @ 140 °C, `top_k=200` → `display` 14,096 B, `data` 71,215 B,
envelope 107,837 B. `display` is 13% of the envelope and restates `data`.

**Decision:** the model-facing success payload does not include `display`.
The exact object stored under a handle keeps the engine envelope, display
and all, so nothing is deleted from the engines. `result_read` returns
rows from `data`, not the markdown table.

Ratified: drop `display` from the model-facing payload. Do not send
the 14 kB table. Handles do the job `display` was imagined to do.

**Rejected:** sending `display` plus `top` (the duplicate the brief
warned would be discovered later).  
**Rejected:** deleting `display` from the engines in this pass.

---

## 4. Handles and the session record

This is the load-bearing section. If it is wrong, test 5 is theatre.

### 4.1 What already exists

`src/dissolve/session.py` (74 lines) is already the session record: a
plain dict, bound per invocation through a `ContextVar`, with
`candidate_evidence()` handing a downstream tool the exact rows stored
under `last_candidates` / `last_candidates_source`.
`resolve_candidate_argument` already treats an explicit empty list as an
override (`is not None` is load-bearing). Shape-code gating was
deliberately not ported.

I measured the producer side: **nothing in v12 writes `last_candidates`.**
A grep for `last_candidates =` in `src/dissolve/` is empty. The writer
was the v11 harness (`SessionState` in `runtime.py`). The readers are
still here: `candidate_evidence()` (dict keys — works) and a set of
`getattr(state, "last_candidates"|"last_route"|"last_tea"|...)` sites in
`tea.py` and `optimization.py` (object attributes — **dead on a dict**).
`evaluate_stored_route_tea_lca` documents that it "consumes typed session
state" and then does `getattr(state, "last_route", None)`. On today's
dict that is always `None`.

So the owner's handle design is not overlapping a working channel. It is
overlapping a *reader* whose writer was deleted with v11. Designing
handles as a second store next to a revived `last_candidates` would
recreate the v11 pair, and test 5 would pass through whichever one the
test author remembered to use.

### 4.2 One channel

**Handles replace `last_candidates` as the durable store.**
`last_candidates` is not a parallel channel. It is a per-call binding
the wrapper makes from a handle, for the duration of one engine call, so
`candidate_evidence()` keeps working without a second write path.

```
session = {
    "handles": {
        "brisk-amber-fox": {
            "tool": "screen_polymer_separation",
            "source_basis": "cosmo_rs_grid",
            "total": 17,
            "exact": <the engine data dict, unmodified>,
            "rows": <the primary row list, exact>,
        },
        ...
    },
    "reported": [{number, source_basis, handle?}, ...],   # for compaction, §7
}
```

There is one write site: `session.store_handle(...)`. It does not also
write a durable `last_candidates` key.

**Rejected:** handles as a second channel beside durable
`last_candidates`. That is the defect the brief named: test 5 passes
through the handle, real queries inherit `last_candidates`, and they
diverge the first time a projection is wrong.  
**Rejected:** deleting `candidate_evidence()` and making every engine
take a handle (34-tool engine pass, and the other builder is not on this).  
**Rejected:** reviving `SessionState` so `getattr(state, "last_route")`
starts working again. That is the v11 session object the clean-room
pass removed. Tools that still `getattr` last_route/last_tea are
unwired (§5.4) and refuse `tool_not_wired`. They grow a `handle`
parameter in a later engine pass. I will not silently make them work
through a shape the spec deleted.  
**Rejected:** the model passing a copied row list as `candidates` *and*
a handle, with the copy winning. Explicit user-typed subjects are
allowed (see below). A handle, when present, is the source of inherited
rows.

### 4.3 When a handle is issued

Default, like Claude Code `Read(offset, limit)`: if the exact object is
larger than one `result_read` page, it is addressable, not truncated.

A "primary row list" is the longest `list[dict]` in `data` among the
names engines actually use (`results`, `ranked_candidates`,
`comparison_rows`, `candidate_solvents`, `records`, `matches`,
`safety_profiles`). If two lists tie, the first of those names wins.
If there is no such list, there is no handle.

Issue a handle when `len(rows) > 20` (the `result_read` default) **or**
`len(json.dumps(data)) > 8192`. Always store the exact `data` (and the
engine `display`) under the handle when one is issued. `total` is
`len(rows)` and is never inferred from what was shown.

Model-facing success with a handle:

```
{"available": true, "source_basis": <str>,
 "handle": "brisk-amber-fox",
 "total": 17,
 "shown": 20,
 "top": [<first min(20, total) rows>],
 "data": {<the non-row fields: warnings, selection, totals, ...>}}
```

`shown` is `len(top)`. Both `shown` and `total` are present. Test 6
asserts this.

Model-facing success without a handle: `{available, source_basis, data}`
— the full `data`, still without `display`.

Handle names are `adj-color-animal` from three small closed word lists
(32 of each is enough). Generated, not derived from solvent names (a
handle that contains `dodecane` is a leak, not an address). Collision:
retry. Do not add a config file for the lists; they live next to the
generator.

Lifetime: the session dict. A `python agent_harness.py` invocation has one.
A CLI session has one, persisted with the messages if the CLI persists
(§9). `/clear` drops handles.

### 4.4 What a downstream tool receives

`result_read(handle, offset=0, limit=20)` returns exact rows
`rows[offset:offset+limit]` with `total`, `offset`, `returned`. It does
not issue a second handle. Unknown handle → `refusal: "unknown_handle"`.
`limit` is clamped to 50. This is a harness tool, not an engine.

When the model calls a consumer tool with `handle`:

1. Resolve the handle. Missing → `refusal: "no_upstream_candidates"`
   (the owner's name; do not invent `unknown_handle` here — that is for
   `result_read`).
2. For the duration of `registry.call` only, bind
   `session["last_candidates"] = exact rows of that handle` and
   `session["last_candidates_source"] = {handle, total, source_tool}`.
   `candidate_evidence()` then returns those exact rows.
3. Strip `handle` from kwargs. Leave the engine's `candidates` argument
   as the model sent it. `resolve_candidate_argument` already does the
   right thing: an explicit list wins, including an explicit empty list;
   `None` inherits the bound rows.
4. Unbind `last_candidates` in a `finally`. Between calls the key is
   absent. That is what makes implicit inheritance *not* a second
   channel.

When the model calls a consumer tool with **neither** a handle **nor**
an explicit subject list: do not call the engine in the hope that a
stale `last_candidates` will fill in. Refuse `no_upstream_candidates`.
The engine's own `missing_candidates` is what happens today when the
session is empty; the harness names it the way the owner named it, and
does not first proceed on an empty list.

When the model calls a consumer tool with an explicit subject list and
no handle: that is a new question (the user typed two solvents), not a
shortlist handoff. Call the engine with that list. Test 5 is the
shortlist handoff and must use the handle.

**Rejected:** durable `last_candidates` that silently feeds the next
tool if the model forgets the handle. That is the projection defect
with the lights off.  
**Rejected:** refusing every explicit `candidates` list (the user must
still be able to say "compare toluene and xylene at 80 C").  
**Rejected:** the wrapper copying `top` into the engine. The engine
reads the exact rows of the handle.

### 4.5 `session.py` changes (later build pass, named now)

Add, and nothing else:

- `bind_tool_session(record) -> Iterator[dict]` — the contextmanager the
  file already imports and does not define.
- `store_handle(record, *, tool, source_basis, data, rows) -> str`
- `load_handle(record, handle) -> dict | None`
- `bind_handle_rows(record, handle) -> Iterator[None]` — the per-call
  `last_candidates` bind.

The bound record stays a dict. `candidate_evidence` reads keys. One
engine site does not: `contaminants.py:284` is `state.last_contaminant`
(attribute, no `getattr` default). A non-empty plain dict is truthy, so
once handles exist that line AttributeErrors on every contaminant
call, including ones that passed explicit solvents. Bind a **dict
subclass** whose `__getattr__` returns `None` for missing names.
That is not `SessionState`. It writes nothing. Contaminant inherit
stays empty. Stored-route tools still see `None` and are still
intercepted as `tool_not_wired`. Do not add attribute writers.

`candidate_evidence` and `resolve_candidate_argument` do not change.
Shape gating stays out.

---

### 4.6 The turn record — every tool result is recoverable

**Owner directive: every tool result should be recoverable.** This is
what replaces in-agent verification (§0 C). The agent does not check
its own answers; it leaves a record complete enough that something
outside can.

The requirement, and each clause is load-bearing:

1. **Every** tool result of the turn, not only the ones large enough to
   earn a handle. A numeral in the answer that came from a small
   unhandled result must still be checkable, or an external validator
   is blind precisely where the compact results live — and those are
   the majority of calls.
2. The **exact** object, not the model-facing compact view. Validating
   an answer against what the model was shown proves the model was
   self-consistent, not that it was right. Handles already store exact
   data (§4.2); this extends the same guarantee to results that never
   got one.
3. **Associable and ordered**: which call produced which result, in the
   order they ran. An external check of "did this number come from a
   tool" is answerable from an unordered bag; "did this number come
   from *that* call" is not.
4. **Durable past the turn.** A record that dies with the process
   cannot be validated after the fact, and after the fact is when
   validation happens.

A handle is the addressing mechanism for what the *model* can page
back (§4.3). The turn record is what a *validator* reads. They share
storage and must not fork into two populations — that is the one-channel
rule (§4.2) applied to the archive rather than to inheritance.

**Rejected:** retaining only handled results — leaves the blind spot in
clause 1 exactly where most calls are.
**Rejected:** recording the model-facing view — answers clause 2 with
the wrong object.
**Rejected:** any in-agent consumer of this record. Nothing in the loop
reads it, scores it or gates on it. If the harness ever branches on its
own record, the verifier has come back through the archive.

---

## 5. Refusals

### 5.1 Vocabulary

The engine already names its refusals (`temperature_off_grid`,
`unknown_solvents`, `unknown_polymers` / `unknown_polymer`,
`missing_candidates`, …). The isomorphism passes `error_code` through as
`refusal`. The harness adds only these, and only when it is the one
refusing:

| refusal | when |
|---|---|
| `no_upstream_candidates` | consumer tool, no handle, no explicit subjects |
| `unknown_handle` | `result_read` on a name that is not live |
| `no_honest_basis` | success path, basis table has nothing honest (should not fire) |
| `ambiguous_polymer` | the guard in §5.3 |
| `round_cap` | §2.1 |
| `tool_not_wired` | tool reads v11 `SessionState` attributes this tree removed (§5.4) |
| `tool_exception` | unexpected exception from a tool body |
| `unknown_tool` | `registry.call` `KeyError` |

Do not build an exception hierarchy. These are strings.

### 5.2 Recoverable in one step (T8)

Principle 6: a refusal returns what is available.

**Off-grid.** Already good. I measured
`solubility_query(LDPE, dodecane, 137.3)` → `temperature_off_grid`,
`nearest_nodes_c: [135.0, 140.0]`. Pass it through. The model must not
report a value at 137.3. The harness does not interpolate "to be
helpful".

**Unknown solvent.** I measured `dodecanel` → `unknown_solvents`,
`solvent_identity_status: "not_found"`, `known_solvent_identity: null`,
`available_count: 990`, and **no candidate names**. The brief is right
that inlining 990 names was a 20,860-byte defect and must not return.

The identity distinction stays: *nonsense name* vs *real chemical we
have no grid row for* (`known_without_grid_values`). A caller who cannot
tell those apart retries a spelling that was never wrong.

Candidates: on `unknown_solvents`, if the engine did not already attach
a bounded near-miss list, the wrapper attaches at most **5** names from
`thermodynamics.get_available_solvents()`, ranked by
(1) casefold prefix of length ≥ 4, then
(2) casefold Levenshtein ≤ 2.
Do not import `src/dissolve/thermo.py`. The freeze stands. The live
function is on `thermodynamics.py`.
This is principle 6, the same family as neighbouring nodes, not a
resolver: the harness never substitutes the top hit. If the engine later
grows this list, use the engine's list and do not add a second.

**Rejected:** dumping the roster.  
**Rejected:** silently resolving to `dodecane`.  
**Rejected:** waiting on the other builder for test 3 to be possible.
The owner's test is on this runtime.

### 5.3 T7 — a tool that returns a value for an ambiguous name

`57acf74` merged family expansion. A family label no longer resolves
to a single polymer. Re-measured on this tree:

- `expand_polymer_identity("polyethylene")` → `('LDPE', 'HDPE')`
- `resolve_polymer("polyethylene")` still returns `'HDPE'` (`point_default`
  remains). Tools must not use that pick as the answer.
- `solubility_query(polymers=["polyethylene"], solvents=["dodecane"], temperatures=[140.0])` → `success: true`, **two** rows, `polymer` in `{LDPE, HDPE}`. Not a pick. Not a refusal.
- `expand_polymer_identity("nylon")` → `('NYLON6', 'NYLON66')`. Bare nylon is a family, not an unknown.
- `expand_polymer_identity("HDPE")` → `('HDPE',)` — not ambiguous
- Hansen thermodynamic join: an ambiguous catalog row is left unjoined
  (`joined_rows` empty / `thermodynamic_join_status` =
  `ambiguous_polymer_family_not_joined` with both members). The catalog
  HSP row stays the family label (`PE`). Do not treat that as a pick.

Scalar tools that still refuse do so as `ambiguous_polymer` with
`supplied_polymer`, `polymer_field`, and `polymer_members`. Collection
boundaries expand to every stored-grid member. The harness does not
collapse either path to one polymer.

**Decision:** after a successful engine return, if any polymer-name
argument (`polymers`, `feed_polymers`, `target_polymer`,
`target_polymers`) expands via `expand_polymer_identity` to **more than
one** stored-grid member, and the result rows' polymer identities are
not the full member set (they are a proper subset, including a single
pick), convert the success into

```
{"available": false,
 "refusal": "ambiguous_polymer",
 "requested": <the family name>,
 "roster": <the thermodynamic_members>,
 "detail": "this name names more than one stored-grid polymer"}
```

If the engine returned every member, the guard does not fire. If the
user asked for `HDPE`, `expand` has length 1 and the guard does not
fire. The guard is a post-condition on a remaining subset pick, not
a second family walker.

**Rejected:** prompt-only.  
**Rejected:** the harness calling `resolve_polymer` and then asking for
both (that would *use* the picker).  
**Rejected:** applying the guard to open-class English ("the polyolefin").
`POLYOLEFINS` has no `thermodynamic_members` that expand to two grid
rows in the way `PE` does; do not invent a family walk.

### 5.4 Unwired session readers

A refusal that names a recoverable cause for an unrecoverable
condition is the same defect class as a value returned for an
ambiguous name. `evaluate_stored_route_tea_lca`,
`optimize_stored_route`, and `pareto_optimize_stored_route` read
`getattr(state, "last_route"|"last_tea", ...)` on an interface v12
removed. If they return the engine's "no stored route" / "complete
stored route is required", the model will do exactly what a
recoverable refusal asks of it: recover in one step by running a route
first, then call
again, and get the identical refusal. That is a guaranteed two-round
loop against a 30-round cap, on a tool that can never succeed no
matter what precedes it.

The wrapper intercepts those three names **before** `registry.call`
and returns:

```
{"available": false,
 "refusal": "tool_not_wired",
 "detail": "reads session state through an interface v12 removed; pending an engine pass to accept a handle"}
```

The generated schema description says the same (§3.1), so the model
can decline the call rather than discovering it per query. That is
what makes the §12.6 enumeration operational instead of a note in a
document. Do not let the engine's recoverable-looking string reach
the model.

Unwired set (closed, named — not a query router):

- `evaluate_stored_route_tea_lca`
- `optimize_stored_route`
- `pareto_optimize_stored_route`

**Rejected:** presenting this as `no_stored_route` or
`missing_candidates`.  
**Rejected:** reviving `SessionState` so the getattr starts working.

---

## 6. The system prompt

Full text. This is the constant. It is not a description of a prompt.

```
You are DISSOLVE v12, a thermodynamic analysis agent. You have tools. You
do not have a calculator.

The architecture is a loop: you call a tool, you read the result, you
call another tool or you answer. There is no plan to fill in, no
specialist to route to, no phase to complete.

Never compute, interpolate, average, or estimate. Every numeral in your
answer came back from a tool call. If a number is not in a tool result,
you do not have it.

Report the source_basis token as given. Never paraphrase it into a
claim that a number is computed versus measured, or live versus
cached, except where this prompt already defines that token. An
unexplained token is worse than none: it gets turned into a
confident claim about where a number came from.

Report the source_basis with the number.
- Solubility values are COSMO-RS predictions (source_basis
  cosmo_rs_grid), not measurements.
- PubChem safety fields are fetched live (source_basis pubchem_live)
  with no retrieval date. Local-only safety cards are source_basis
  safety_local. include_pubchem defaults to false; pass true only
  when the live fields are the question. A row field named basis on
  a safety card is an averaging time (for example 8-hour TWA), not
  the source of the card.
- A TEA cache hit is an exact prior simulation (source_basis
  tea_cache_exact).
- A TEA screening analog is a cache-derived estimate (source_basis
  tea_screening_analog), not a simulation of the named configuration.
  Say so.
- A live BioSTEAM run (source_basis tea_live) is a simulation of the
  named configuration. Say so. It may not appear in this environment.
- Hansen curated rows are qualitative_hansen_parameters, not
  solubilities. An HSP random-forest row is hsp_fallback.
- Contaminant screens are contaminant_workbook screening proxies.
- Optimization figures are optimization_workbook.
- identity_registry, safety_local, provider_metadata, analysis_asset:
  report the token. Do not invent a gloss.
- Screening thresholds (1, 5, 10 wt%) are engine defaults, not process
  claims. If you mention them, say that.
- When a tool result states bound inclusivity (bounds_are_inclusive),
  report qualifying counts with those bounds. Do not restate the
  user's strict inequality if the engine applied an inclusive one.
  A count for >= 5 / <= 1 is not a count for > 5 / < 1. If you have
  not read a boundary-equal row off the handle, do not claim the two
  are the same. D-5 (`3698afe`): prompt-fixed; compliance unverified
  until a live rerun.
- evaluate_stored_route_tea_lca, optimize_stored_route, and
  pareto_optimize_stored_route are unwired. Do not call them to
  recover a missing route.

A refusal is final. Report it, say what is available, and do not retry
the same call with a nudged argument. Off-grid temperatures come back
with neighbouring nodes — report the neighbours, do not invent a value
at the requested temperature. Unknown solvents come back with an
identity verdict (nonsense vs known chemical without grid values) and
at most five near-miss names — do not substitute the top hit.
Ambiguous polymer family names expand to every stored-grid member
or refuse ambiguous_polymer with the members (polyethylene is LDPE
and HDPE; nylon is NYLON6 and NYLON66). Report every member. Do not
pick HDPE. Do not treat a Hansen catalog row labelled PE as a
single polymer.

Large results come back as a handle, a total, and the first page
(top). total is not something you infer from how many rows you can
see. To read more rows, call result_read(handle, offset, limit). To
hand a shortlist to a downstream tool, pass the handle. Do not retype
the rows. If you do not have a handle and the user did not name the
subjects, you do not have candidates.

Literature search and ingest tools are ordinary tools on your list.
Call them directly. There is no research sub-agent.

Do not continue a refusal by calling the same tool with a spelling
you invented. Do not interpolate. Do not average two grid nodes. Do
not answer a temperature that is not a grid node. A difference
between two tool results (a recovery window, a gap) is arithmetic
you must not perform; if the user needs the difference, say the
two numbers and that you did not subtract them.
```

`/mode review` appends: `Ask before making consequential process
assumptions the user did not specify.`  
`/mode auto` appends: `Make reasonable process assumptions when needed
and label them explicitly.`

That is all `/mode` is. See §9.

---

## 7. Compaction

Only once a conversation runs long. The seven acceptance queries will
not hit this. Do not build a compaction framework in anticipation;
build the trigger, the cut rule, and the template.

**Trigger.** After appending a tool result, if
`estimated_tokens(messages) > window - reserve`, compact before the
next `complete`. When a reply emits more than one tool call, finish the
sequential batch first, then compact once. Do not compact between
calls in that batch (an unmatched `tool_use` must not be persisted).
Estimate is `len(text)/4` unless the provider already returned a token
count, in which case use that. `window` is the selected model's
context window (default 128k if the alias does not say). `reserve` is
8,000 tokens — one more tool round and an answer.

**Cut rule.** Drop the oldest messages after the system prompt and the
in-place summary, but **never cut such that a tool result is the first
kept message**. If the cut would land on a tool result, walk forward to
the next user or assistant prose message. This is the owner's rule and
also Claude Code's.

**Summary.** One message, immediately after the system prompt, updated
in place, not regenerated as a new object each time. Fixed template:

```
Summary (do not continue the conversation, do not answer questions):
- Polymers in play: ...
- Temperatures in play: ...
- Live handles: <name> (total=N, tool=..., source_basis=...), ...
- Numbers already reported: <value> (source_basis=..., handle=...), ...
```

The fields come from the session record (`handles`, `reported`) and from
the arguments of tools already called, not from the model inventing a
narrative. A tiny model call may fill the template only if the template
cannot be filled from those records; its prompt is exactly
`Do not continue the conversation. Do not answer questions. Fill this
template from the supplied records. Output only the template.` Prefer
filling it from records with no model call.

**Rejected:** a summarizer that "helps" by answering.  
**Rejected:** compacting on every turn.  
**Rejected:** cutting a tool result to the front of the kept window
(the model then sees an orphan result and hallucinates the call).

`reported` is appended by the wrapper from numeric values already on
the model-facing payload (row fields, not internal counts such as
`shown` / `total` / `available_count`). Mechanical, not a judgment of
what the model "might quote." Book-keeping for the template, not a
validator.

---

## 8. Validation is external

**Owner directive: no verification in the agent.** Whatever validates
model results is an external harness. Nothing in this system scans,
buckets, scores or gates its own output.

What this system owes that harness is §4.6: every tool result of a turn,
exact, associable, ordered, durable. That is the whole contract.

The bucketing rule this section used to specify is kept below as a note
for whoever builds the external validator, because the definitions are
worth not re-deriving — but it is **not code in this repo** and no chunk
builds it.

> Numeral token: `-?\d+\.?\d*(?:[eE][+-]?\d+)?`, excluding handle names
> and CAS-shaped `\d{2,7}-\d{2}-\d`.
>
> | bucket | when |
> |---|---|
> | `matched_nothing` | no verbatim substring in any tool result, and no tool number `R` with `round(R, D) == float(token)` |
> | `rounding_false_friend` | not verbatim, but some `R` satisfies the round test (`5` from `5.497`) |
> | `present_but_suspect` | verbatim somewhere. Presence cannot bind subject or unit — the wrong-solvent class. |
>
> One thing the external validator must know rather than discover:
> `matched_nothing` mixes invented numerals with **legitimate
> cross-result arithmetic** — cool from 107 to 61, the answer says 46.
> No bucket separates those and none mechanically can, because deciding
> that 46 came from 107−61 requires knowing what the model was doing.
> A validator that refuses every `matched_nothing` refuses correct work.

**Rejected:** an in-agent verifier of any kind, gating or advisory.
**Rejected:** a second model call that "checks" the answer.
**Rejected:** any scan or score field on `TurnResult`.
**Rejected:** treating Part 1's "refuse otherwise" as still live. There
is a verifier; it is outside; §4.6 is how it is fed.

---

## 9. The CLI

### 9.1 The seam

v11 `cli.py` is 797 lines. I read it. The loop is touched in one place:
`result = self.agent.invoke(query)`, then `console.print(Markdown(result.answer))`.
Everything else it prints, it builds itself.

v12 interface, and only this:

```python
result = run_turn(
    query,
    session=self.session,
    model=self.model,          # resolved id, not alias
    messages=self.messages,
    on_event=self._print_tool_event,
    api_base=self.api_base,
    api_key_env=self.api_key_env,
)
self.console.print(Markdown(result.answer))
# nothing to print: the agent produces no score, flag or scan.
# The answer is the answer (§0 C, §8).

The CLI may read the six `TurnResult` fields. It may print
`tool_rounds` and `usage`. It must never branch on them: no control
flow that changes the answer, status, tool_trace, archive, or message
history. The four-field rule existed to stop the display layer becoming
a decision layer. That purpose stands; the number four does not.

It does not import `registry.call`,
does not call an engine to answer a query, and does not grow a second
loop.

### 9.2 What is retained, what is cut

Retained from `~/dissolve-v11-work/src/strap/cli.py` — the parts that
draw, with the subtitle string replaced:

| v11 region | fate |
|---|---|
| readline / rich imports (L16–26) | keep |
| `ModelSpec`, `MODELS`, `MODEL_ALIASES`, `DEFAULT_MODEL` (L48–91) | keep the table and the aliases; drop `budget_profile` / `required_tool_choice_supported` if nothing reads them. Default stays `muse-spark` until the owner says otherwise (§11). |
| `print_doctor` table (L258–268) | keep verbatim |
| `resolve_model` (L271–275) | keep |
| `banner` Panel (L430–441) | keep the drawing; subtitle **`dissolve-v12-0.1`**; drop the v11 token-budget line (that economy is not in this design). Session id and mode stay. |
| `_show_models` Table (L443–456) | keep |
| slash commands `/model`, `/mode`, `/context`, `/cost`, `/harness`, quit (L474–523) | keep the *commands*; rewrite the bodies that called `AgentHarness` |
| `Prompt.ask("\n[bold cyan]>[/]")` loop (L666–682) | keep |
| `Panel(..., title="Session budget stopped")` style of notices (L534) | keep the drawing; the 500,000-token breaker is a v11 budget object and does not come across. A notice Panel is how `round_cap`, `provider_error`, and `compaction_error` render. The CLI prints no scan, flag or score - the agent produces none. |
| argparse `--session / --model / --mode / --home / --no-persist / --once / --stream-json` (L685–703) | keep these flags |
| `doctor` command | keep the *command*; rewrite the checks |
| `if __name__` / `main()` structure | keep, but the loop file is `agent_harness.py`; the CLI is `python -m dissolve.cli` or `python agent_harness.py` with no query launching `cli.main()` via a lazy import. There is no `main.py`. |

Cut, and do not reimplement under a new name:

| v11 region | why |
|---|---|
| 10 `*_SPECIALIST` imports and `PRODUCTION_SPECIALISTS` (L28–45, L92–98) | principle 2 |
| `AgentHarness`, `ROOT_PROMPT`, `TurnResult` from `harness`, `AnswerSynthesisFailure`, `TOOL_PLAN_FAILURE_ANSWER` (L30–33, L358–383, L552–600) | that harness is the thing being replaced |
| `BudgetConfig`, `AnswerBudgetExceeded`, `WorkingBudgetExceeded`, `configure_langsmith_terminal_logging` (L37–41, L371–378, L601–618, L686) | v11 turn-budget economy |
| `VISUALIZATION_SPECIALIST`, `build_terminal_plot_panel`, artifact plot branch (L45, L640–659) | visualization is gone (9,406 lines deleted) |
| `SessionStore` / `StoredSession` as they stand (L36, L340–342) | they import v11 `SessionState` / `ConversationContext` from `runtime.py`, which is not in v12 |
| `/claude-session` (L519–520) | quarantined v11 path |
| `ingest-graph` / `compare-rag` argparse branch (L721–763) | **cut both as CLI verbs.** The test is not "does it import the harness." The test is: does the command mutate state the agent later reads? If yes, a user who runs the verb and then asks a question has changed the world outside the loop, and nothing in the conversation records it. Measured: `ingest-graph` calls `ingest_literature_graph_data` — the same function the registered tool wraps, a second door into a write. `compare-rag` is not read-only: `compare_literature_engines` calls `research.ingest_literature_documents` *and* `ingest_literature_graph_data`. Same-function is not enough; it still writes outside the message array. Ingest through the registered tool so the turn records it. Do not keep a parallel path. |

`create_model` (L284–321) is LangChain `init_chat_model`. **Cut the
LangChain call.** Keep the env-var and alias checks. The actual
completion lives in `agent_harness.complete`. The CLI may not import `langchain`.

### 9.3 `/mode review | auto`

I measured this rather than taking the brief. In v11 `cli.py`, `/mode`
does one thing: it rebuilds the agent with a different sentence appended
to the system prompt (L360–364, L493–502). A repo-wide search for a
review-mode tool-confirmation gate in `harness.py` found nothing. The
brief's "gated tool execution behind confirmation" is not what this CLI
does.

**Decision:** keep `/mode review | auto` as those two prompt sentences
(already in §6). Do not invent a confirmation gate. Inventing one would
be a control-flow layer the new loop does not have, and it would not be
"retaining" anything the owner has been using — they have been using a
prompt switch.

**Rejected:** silently dropping `/mode`.  
**Rejected:** building a y/n tool gate and calling it retention.

### 9.4 Persistence

The owner has been using `--session`. Silently dropping it is a product
change I will not make.

**Decision:** a thin store *inside* `cli.py` (not a fifth harness file,
not a port of v11 `persistence.py`):

```
~/.dissolve/sessions/<id>/session.json     # messages, session dict, metadata
~/.dissolve/sessions/<id>/transcript.jsonl # user/assistant/tool lines
```

The session dict includes the handle table (exact objects). That is the
filesystem analogue. It will be large after a 990-row screen; that is
correct. Metadata: model alias, mode, release `dissolve-v12-0.1`.

**Rejected:** porting `SessionStore` raw (it constructs `SessionState`).  
**Rejected:** persisting v11 `ConversationContext` turns of 2,400
characters (that is a projector).  
**Rejected:** a new `persistence.py`.

`/clear` drops messages and handles, keeps the session id.  
`/context` prints polymers, temperatures, and live handles with their
totals — not a v11 `prompt_block()`.  
`/harness` prints `flat loop, no specialists`.  
`/cost` prints the last turn's tool-round count and its usage.

Normalized `usage` is a dict with whichever of `input_tokens`,
`output_tokens`, and `total_tokens` the provider actually measured.
Omit keys that were not present. Do not fill a missing key with `0`.

The three adapters disagree on field names. Map them, then stop:

| prefix | object | native fields | normalized |
|---|---|---|---|
| `anthropic` | `resp.usage` | `input_tokens`, `output_tokens` | same names; if both are present, `total_tokens` is their sum |
| `google_genai` | `resp.usage_metadata` | `prompt_token_count`, `candidates_token_count`, `total_token_count` | `input_tokens`, `output_tokens`, `total_tokens` |
| `openai` | `resp.usage` on the `chat.completions.create` return value, not on `.choices[0].message` | `prompt_tokens`, `completion_tokens`, `total_tokens` | `input_tokens`, `output_tokens`, `total_tokens` |

Absent is not zero. No usage object → `None`. An object whose every
mapped field is missing → `None`. Measured zeros stay zeros. `/cost`
must print a different string for `None` than for a zero-valued dict.
Printing `json.dumps(usage)` (`null` vs `{"input_tokens": 0, …}`) is
one way to keep that distinction without branching on the field.

If any `complete()` in the turn returned `None` usage, the turn's
`usage` is `None`. Do not treat a missing round as `0` when folding.
When every round measured a dict, sum each key only where that key
appeared.

This six-field seam is a provisional orchestrator decision (D-1), flagged
to the owner and reversible by them at one field's cost. The CLI may
print `tool_rounds` and `usage`. It must never branch on them (§9.1).

### 9.5 What the CLI is allowed to know

Allowed: `TurnResult`, `ToolEvent`, `run_turn`, `RELEASE`, the model
table, rich, argparse, readline, engine modules **only** for doctor
checksums (`_ASSET` / `_ASSET_SHA256` on thermodynamics, safety,
contaminants, analysis, tea, optimization).

Forbidden: `registry.call` for answering, `candidate_evidence`, handle
generation, running the numeral scan itself (it may print
an in-agent verifier of any kind), a research sub-agent, specialists, budgets,
visualization, LangChain, LangSmith.

Doctor checks: provider key for the selected model; those asset
checksums; live `REGISTRY` names equal the declared 33-name
post-consolidation roster in `cli.EXPECTED_REGISTRY_NAMES` (detail
prints the live count); duckdb import. No specialist-contract
check. `normalize_feed_composition` is not on that roster.

---

## 10. Acceptance tests

These are the done criterion. `python agent_harness.py "<query>"` prints every
tool call and result, then an answer. No pytest file is required for
this pass; if one is wanted later, ask, because that is a fourth
(fifth) file.

Test 5 is first, as instructed. Test 4 is written as specified.
`57acf74` already expands `polyethylene` to both members on
`solubility_query`; the harness must still not pick.

Each test names the query, the behaviour, and the assertion. None of
them quote a live corpus row as a golden sentence. Numbers below are
shapes, not a second specification of the data asset.

### Test 5 — screen then safety on the shortlist (D-4; `5dc8c5d`)
*(regression for the projection defect; write and run this first)*

**Query:**
`Screen solvents for separating LDPE from PP between 80 C and 140 C, then compare the safety of that shortlist at the temperatures the screen used.`

**Behaviour:**
- A `screen_polymer_separation` (or equivalent screen) call returns a
  handle. `total` and `shown` are both present. The exact
  `ranked_candidates` (or primary row list) in the session record has
  length `total`. On this fixture `total > shown`, so binding `top`
  is not binding exact.
- A later `compare_solvent_safety_at_conditions` call is made **with
  that handle** and without a copied, truncated `candidates` list.
- The identities **inherited into that call** are the same set as the
  screen's exact rows, not `top`, not empty, not a silently shortened
  page. That is the bind. The safety **output** on this fixture is a
  comparison page (`candidate_count` < `candidate_scope_stored_count`,
  measured 6 of 40 under the tool's default limit). That is coverage,
  not a second bind. Do not treat the page as the shortlist, and do
  not repair the bind by weakening an assertion or by reaching into
  the engine.

**Assert:**
- `handle` used on the safety call equals the screen's handle.
- `set(inherited solvents) == set(exact screen row solvents)`.
- `candidate_scope_stored_count` equals `total`.
- If `candidate_count` < `candidate_scope_stored_count`, the printed
  answer discloses both counts, names the compared solvents, and does
  not present the page as the inherited shortlist. A `6 of 40`
  substring inside a sentence that still calls the page the shortlist
  is not disclosure. The scripted acceptance answer must say the
  comparison page is not the full shortlist.
- The printed answer does not name a solvent that was not in the exact
  rows. Extraction is a longest non-overlapping registry-name match,
  including names shorter than five characters; do not suppress a
  foreign name because it is a substring of an exact row.
- Fail the test if safety ran on an empty list, or if it ran on `top`
  while `total > shown`, or if it ran with no handle and a copied list.

**Must not:** answer over an empty or silently truncated **bind**.
**Must not:** treat a comparison page as set-equal to the exact rows,
or bless a green test whose answer omits the coverage loss.

**Chunk 7 obligation — this is not Test 5.**
`test_handle_beats_copied_list_at_dispatch` must not be counted as
Test 5. It is count-sensitive and identity-blind: replacing the
transient population with 40 copies of one valid screen row still
satisfies `candidate_scope_stored_count == total` and
`candidate_count != total` with transients absent. A count is a proxy
for "the right rows arrived." Chunk 7's Test 5 must compare inherited
and exact solvent identities plus the printed answer, and must be
shown red under that broken bind before it is green. The current
low-level `total=40, shown=20` handoff is a valid fixture for that
future test, not a substitute for it.

### Test 1 — point lookup on grid

**Query:**
`What is the solubility of LDPE in dodecane at 140 C?`

**Behaviour:** one `solubility_query` (or equivalent point lookup). A
value comes back. `source_basis` is `cosmo_rs_grid` (or the model
states COSMO-RS / stored grid). I measured this cell at
`92.52845825` wt% today; that number is an observation, not a golden
sentence. The test asserts the answer contains the `solubility_pct`
from **that call's tool result**.

**Assert:** exactly one engine call that returns `available: true`;
answer contains that call's value and states the source; no interpolation
language.

### Test 2 — off-grid temperature

**Query:**
`What is the solubility of LDPE in dodecane at 137.3 C?`

**Behaviour:** refusal `temperature_off_grid` with neighbouring nodes
135 and 140. I measured this already on the engine.

**Assert:** answer reports the refusal and the neighbours. Answer does
**not** contain a solubility at 137.3. A numeral `137.3` may appear as
the requested temperature being refused.

**Must not:** report a value at the requested temperature.

### Test 3 — unregistered solvent

**Query:**
`What is the solubility of LDPE in dodecanel at 140 C?`

**Behaviour:** refusal `unknown_solvents`. Identity
`not_found` / `known_solvent_identity: null`. At most five near-miss
names. `available_count` may still be 990; the roster is not inlined.

**Assert:** no solubility value. No substitution to dodecane. Candidates
≤ 5. Identity distinction present in the tool result the model saw.

**Must not:** substitute.

### Test 4 — `"polyethylene"`

**Query:**
`What is the solubility of polyethylene in dodecane at 140 C?`

**Behaviour:** report both members, or refuse with a roster that
includes LDPE and HDPE. I measured `57acf74`: `solubility_query`
returns two rows, LDPE and HDPE, not a pick. Scalar tools that still
refuse use `ambiguous_polymer` with `polymer_members`. The §5.3 guard
fires only if some path still returns a proper subset.

**Assert:** no single-polymer HDPE (or LDPE) value presented as *the*
answer. Both members are named, or a roster containing both is.

**Must not:** pick.

### Test 6 — large screen, handle, follow-up

**Query, turn 1:**
`Screen solvents for dissolving LDPE from 25 C to 160 C.`

**Behaviour:** handle issued. Full object in the session record
(`len(rows) == total`). Model-facing payload has `shown`, `total`,
`top`, `handle`. `total` is not `len(top)` when `total > shown`.

**Query, turn 2 (same session dict):**
`What is the third solvent on that shortlist?`

**Behaviour:** resolves via `result_read` or via `top` if the third row
is in `top`. Does not invent a solvent.

**Assert:** session record `total` equals the exact row count; both
`shown` and `total` appeared in the first model-facing result; turn 2
names a solvent that is actually `rows[2]`.

### Test 7 — TEA outside the cache

**Query:**
`Run TEA/LCA for HDPE in dodecane at a processing capacity of 1 Mt/yr and energy case C1. If the cache misses, say so.`

(Adjust the capacity or temperature until the engine's own
`cache_match_status` is `miss` or `surrogate` on this tree. I did not
find an exact admitted record for HDPE/dodecane in a quick call; the
test is the miss path, not that pair.)

**Behaviour:** a miss is reported as a miss, or an analog is returned
with `basis tea_screening_analog` and labelled an analog. A live run is
`tea_live` and labelled a simulation of the named configuration.
Default parent (Python 3.11.9, no in-process BioSTEAM) refuses live
unless `DISSOLVE_TEA_PYTHON` points at a ≥3.12 worker. A miss or analog
is the expected path here without that worker. A green basis-map test
is not evidence the live path works.

**Assert:** the answer does not call an analog a simulation. The answer
does not call a miss a hit.

**Must not:** present an analog as a simulation.

Test 8 (literature sub-agent) is dropped. See §3.3.

---

## 11. Post-brief decisions

These supersede Part 1 of the brief where they conflict.

**Ratified (no longer questions):**

1. **Drop `display` from the model-facing payload.** Do not send the
   14 kB table. Stored exact object still has it.
2. **`source_basis` vocabulary stays open.** A closed enum forces
   honest tools to lie. The owner's seven tokens remain the ones the
   prompt *defines*. The extras (`tea_live`, `identity_registry`,
   `safety_local`, `provider_metadata`, `analysis_asset`, and
   `evidence_class` pass-through) stay. The prompt rule is: report
   the token as given; never paraphrase an undefined token into a
   claim about computed versus measured. Enumeration is not the fix;
   that sentence is.
3. **No research sub-agent. Test 8 is gone.** Question 3 is moot.
4. **Default model stays `muse-spark`.**
5. **Envelope reshape is ratified** (§3.4). Engines keep the v11
   envelope; the wrapper reshapes; `source_basis` attaches at the
   seam. Subtractive port, golden files stay valid. The hedge is out.
6. **Harness key is `source_basis`, not `basis`.** Correction to
   Part 1, not a question. Three modules already own `basis` with
   three meanings (§3.5). Colliding a required provenance field with
   any of them is laundering.
7. **`TurnResult` is six fields (provisional).** The original four
   plus `tool_rounds: int` and `usage: dict | None` (§2, §9.1, §9.4).
   The CLI may print those two and must never branch on them. Flagged
   to the owner; reversible at one field's cost. The four-field rule's
   purpose (display layer is not a decision layer) stands; the number
   four does not.

**Owner decision — Part 1 divergence C: (b) now. Later pick is a
measurement.**

**Superseded by owner directive: no verification in the agent.**

Part 1 asked for a verifier because nothing else could check a number.
Something else can: an external harness. So the (a)/(b)/(c) trade this
section used to cost — a bound verifier against the budget, a scan that
only observes, a scan that refuses `matched_nothing` — is not a choice
this system makes. None of them are built here.

The reason (c) was never as cheap as it looked is worth keeping, because
it is a property of the problem rather than of this design:
`matched_nothing` contains invented numerals **and** legitimate
cross-result arithmetic — cool from 107 to 61, the answer says 46 — and
nothing mechanical separates them. Any external validator will meet the
same wall, so §8 records it for them.

What this system does instead is §4.6: every tool result of a turn,
exact, associable, ordered, durable. Verifiable, not self-verifying.

**Closed this pass (were still-open):**

- **`ingest-graph` / `compare-rag`.** Cut both as CLI verbs. The
  author's "keep if cheap, cut if it imports the harness" measures
  the wrong property. Import cost is not the risk. The risk is a
  second door into a mutation: RAG ingest writes state later tool
  calls read, and all six research tools are executable on the
  root. A user who ingests through the CLI and then asks a question
  has changed the world outside the loop, and nothing in the
  conversation records it. Measured: `ingest-graph` calls
  `ingest_literature_graph_data` (same function the registered tool
  wraps). `compare-rag` is not read-only —
  `compare_literature_engines` calls
  `research.ingest_literature_documents` *and*
  `ingest_literature_graph_data`. Same-function is not enough; it
  still writes outside the message array. Ingest through the
  registered tool. Do not keep a parallel path. Do not decide the
  pair together by import cost; the mutation test happens to cut
  both.
- **`evaluate_stored_route_tea_lca` / `optimize_stored_route` /
  `pareto_optimize_stored_route`.** Refuse `tool_not_wired` before
  `registry.call`. Schema description names the status. Not
  `no_stored_route` — that string is indistinguishable from "the
  user has not run a route yet" and produces a guaranteed two-round
  loop (§5.4). Pending an engine pass to accept a handle. Do not
  revive `SessionState`.

**Disagreements with Part 1, named so they are not silent:**

- Part 1 principle 3 and test 8 (the sub-agent) — dropped.
- Part 1 principle C — **(b) now**; later pick empirical from §8
  buckets. Not unsettled.
- Part 1 contract key `basis` — renamed `source_basis`. Correction,
  not a question.
- Part 1 `main.py` — renamed `agent_harness.py`. The property the
  brief wanted is a repo-root one-shot that prints every tool call
  and result, then an answer. The filename was not that property.
  `main.py` also collides with `cli.main()`. No stub `main.py`.

---

## 12. Corrections to the brief

Measured, not mood:

1. **`screen_contaminant_leaching` has provenance on success**
   (`_base_result` sets `provenance` and `model_basis`). Do not condemn
   it. An unsupported-contaminant *refusal* has neither field, which is
   correct for a refusal.
2. **`lookup_material_database_membership` has `model_basis`.** "No
   provenance field" is a grep of the word `provenance`.
3. **v11 `/mode` is a prompt sentence, not a tool-confirmation gate.**
   I will not build the gate the brief described; it is not what the
   retained CLI does.
4. **No `flock` in v12 `tea.py`.** Sequential execution in the loop is
   the constraint. The v11 sweep script is a different process.
5. **`last_candidates` has no writer in v12, and two of its three
   readers are structurally dead.** `optimization.py:236` and
   `tea.py:1247` do `getattr(state, "last_candidates", None)` on what
   `session.py` made a plain dict, so they can only ever return
   `None`. The third reader, `candidate_evidence()`, would work if
   anyone wrote the dict keys *and* a session were bound. Neither is
   true. Handles are the writer, not a second writer.
6. **`session.py` cannot bind a session at all.** `_ACTIVE` is read in
   `current_tool_session()` and set nowhere. `contextmanager` is
   imported and unused. There is no lost write to audit for: the
   channel never bound. What there is instead is a set of engine paths
   that *look* like they depend on session state and have silently
   been no-ops in v12. Enumerated so a later refusal is not read as a
   design choice. Class B stored-route tools are intercepted as
   `tool_not_wired` (§5.4); the rest of Class A become inherit-via-
   handle once the binder exists.

   **Class A — `current_tool_session()` is always `None` today.**
   Adding the binder makes the *dict-key* readers start working.
   `getattr` readers stay dead (Class B).

   | site | what it thinks it is doing | what it does today |
   |---|---|---|
   | `safety.py:1036` `compare_solvent_safety_at_conditions` | inherit last screen via `candidate_evidence(current_tool_session())` | `candidate_evidence(None)` → `[], None`. Explicit `candidates` still work. Inherit is a no-op. |
   | `contaminants.py:282` `_inherited_candidate_solvents`, called from `:470` | inherit `state.last_contaminant` | `state` is `None`, `prior = {}`. Explicit solvents still work. |
   | `tea.py:2014` `evaluate_stored_route_tea_lca` | read `last_route` | `state` is `None`, route is `None`. The engine then takes the "no stored route" / basis-gap path. That refusal looks recoverable. The wrapper intercepts this name with `tool_not_wired` before `registry.call` (§5.4), so the recoverable string never reaches the model. |
   | `tea.py:1211–1256` `_stored_candidate_screen` | recover a prior screen as a TEA basis | `candidate_evidence` on None; `getattr last_candidates` None. |
   | `optimization.py:134` `_source_state` | require `last_route` + `last_tea` | raises `ValueError("A complete stored separation route is required")`. Looks like a precondition the model can satisfy. It is an unbound session. Wrapper intercepts `optimize_stored_route` and `pareto_optimize_stored_route` with `tool_not_wired` (§5.4). |
   | `optimization.py:174`, `:297` | read `last_tea` | `None`. |
   | `separation.py:14–15` | imports `candidate_evidence`, `current_tool_session`, `resolve_candidate_argument` | no call sites. Dead imports, plus a leftover `InjectedToolArg`. |

   **Class B — `getattr(state, "last_*")` on a dict, even after bind.**
   A bound dict does not grow attributes. These stay no-ops unless
   the engines change or `SessionState` is revived (rejected):

   - `tea.py`: `last_result` `:1211`, `last_candidates` `:1247`,
     `last_screen_constraints` `:1256`, `temperature_min_c` /
     `temperature_max_c` / `strict_maximum` `:1278–1280` and
     `:1294–1296`, `last_tea` `:1283`, `:1313`, `:2252`,
     `last_contaminant` `:1454`, `feed_mass_fractions` `:1471`,
     `:2172`, `:2377`, `:2588`, `last_route` `:2015`,
     `route_substitution_basis` `:2038`.
   - `optimization.py`: `last_route` / `last_tea` `:135–136`,
     `last_tea` `:175`, `:298`, `last_screen_constraints` `:231`,
     `last_candidates` `:236`, `last_safety` `:259`,
     `temperature_min_c` / `temperature_max_c` `:280`.

   After this harness binds a dict and writes handle rows into
   `last_candidates` as a *key*, only `candidate_evidence()` (safety
   inherit) comes alive. The `getattr` tools will still refuse. That
   refusal is the leftover object-shaped session, not a product
   decision that stored-route TEA is out of scope.

7. **Search tools mutate** via `save_to_corpus`. That is an ordinary
   argument on a root tool. The user can see it fire. There is no
   sub-agent to force it false.
8. **33 tools, 8 imported engine modules** (`analysis`, `contaminants`,
   `optimization`, `research`, `safety`, `separation`, `tea`, `tools`)
   plus the `thermodynamics` kernel. The brief's "9 modules" is that
   plus the kernel. `thermo.py` is orphaned (zero registered tools) and
   is not part of this design; the freeze on it stands.

---

## 13. What I did not do

- I did not write product code. I did not touch `~/dissolve-v12-work`.
- I did not open `src/dissolve/thermo.py` or `tests/test_thermo.py`.
  (A directory-wide grep for `ambiguous` printed three lines of
  `thermo.py`; I did not use them. The live kernel is
  `thermodynamics.py` / `tools.py`.)
- I did not read `GATE_AUDITS.md`.
- I did not port v11 routing, validation, answer synthesis, budgets, or
  visualization.
- I did not design a second candidate channel.
- I did not enumerate 34 wrappers.
- I did not invent provenance for tools that already state a source.
- I did not specify a confirmation-gated review mode the retained CLI
  does not have.
- I did not put interpolation, curve fits, or nearest-neighbour
  substitution back.
- I did not keep `display` on the model-facing payload.
- I did not verify a TEA cache-miss configuration end-to-end; test 7
  says to find one against the tree at build time.
- I did not author the seven queries as a frozen corpus of paraphrases.
  They are executable shapes. If they are quoted back to a builder as
  the only sentences that must pass, they will become the specification
  and fresh paraphrases will fail. That is corpus rule 2 and I am
  saying it here so it is harder to do.
- I first hid four research tools, then refused them on the root role.
  Both were the specialist / one-rule router coming back. The
  sub-agent is now out. I did not keep test 8 after dropping the
  property it asserted.
- I first treated Part 1's "refuse otherwise" as a live constraint
  the spec had to honor or drop, recommended (c), and left §0
  answering a question it said it was not answering. That was
  defending a document. The instruction was (b): hold validation,
  keep the hooks, hand-check. §0 / §2 / §8 now agree. The later
  pick between (c) and a bound verifier is a firing-rate
  measurement, not another argument.
- I did not keep `ingest-graph` / `compare-rag` as CLI verbs. Both
  mutate corpus state the agent later reads; ingest through the
  registered tool so the conversation records it.
- I did not let `evaluate_stored_route_tea_lca` (or the stored-route
  optimizers) refuse as `no_stored_route`. That names a recoverable
  cause for an unrecoverable condition. The refusal is
  `tool_not_wired`.
- I did not keep Part 1's `main.py` filename. The loop is
  `agent_harness.py`. That is not v11's `AgentHarness`.
- I did not send the wrapper to `thermo.get_available_solvents()`. The
  freeze stands; the live function is `thermodynamics.get_available_solvents()`.
- I did not invent closure for auditor findings 2, 3, or the ungrounded
  extra `source_basis` tokens. §14 is the index.

---

## 14. Auditor OBJECT findings at seal

`codex-v12-auditor-1` filed six STANDING findings and one SUPERSEDED.
This table is the index. STANDING means the finding is not closed by
this spec. Do not treat a row as a fix.

| # | finding | disposition |
|---|---|---|
| **1** | One channel claimed; rows live in `exact` + `rows` + `last_candidates` + an explicit argument that wins over a handle. | **Answered** in §4.2–4.4: one durable store (handles). `last_candidates` is a per-call bind, then gone. `exact`/`rows` are that handle's payload. Explicit subjects without a handle are a new question. When both a handle and a copied `candidates` list are present, the Rejected line in §4.4 says the handle wins. |
| **2** | The chain stops before TEA and contaminants; the handle-schema rule misses those consumers. | **STANDING**, engine work, outside this spec. Got worse after the binder design, not better. `gate_surface.py` now sweeps all 34 and reports FAIL(7): `evaluate_stored_route_tea_lca` raises `NameError` on `CANDIDATE_SHAPE_SCREEN` with **no** session bound (not latent). All three contaminant tools raise `AttributeError('dict' object has no attribute 'last_contaminant')` the moment a session record **is** bound. Four of those break precisely when the harness does the thing §4 does first (bind). Spec mitigation only: those three stored-route names are `tool_not_wired` (§5.4); the dict subclass in §4.5 stops the contaminant AttributeError from crashing the turn; inherit stays empty. Handle on the generated schema is still only `compare_solvent_safety_at_conditions` (§3.1–3.2). `57acf74` did not close this. |
| **3** | Acceptance test 5 has `shown == total`, so binding the projection passes both broken and fixed. | **Answered** for the projection-bind theatre (D-4; `5dc8c5d`): §10 test 5 requires `total > shown` on the 80–140 LDPE/PP screen, inherited identities equal the exact set, and a retained forty-copy control that keeps the identity predicate red. Test 6 is still a large screen and does not re-run safety on the exact set. Output set-equality of safety rows to exact rows is **not** the bind — the engine's default comparison page is 6 of 40; the answer must disclose that coverage rather than present the page as the shortlist. |
| **4** | `basis` launders provenance; only 2 of 5 added tokens ground to a real engine distinction. | **Answered** for the collision and for those two distinctions: key is `source_basis` (§3.5); `include_pubchem` wrapper default `False` on every safety tool that takes the flag (`get_solvent_safety_card`, `compare_solvent_safety_at_conditions`, `screen_route_solvent_substitutions`) so `safety_local` vs `pubchem_live` is visible; tea per-call `tea_cache_exact` / `tea_screening_analog` / `tea_live`. **STANDING** on the other added tokens (`identity_registry`, `provider_metadata`, `analysis_asset`): they are module defaults, not engine-mode distinctions. Open vocabulary plus §6 (report the token as given, never paraphrase) is the mitigation, not a claim they ground. `screen_green_solvent_candidates` has no `include_pubchem`. |
| **5** | The verifier is a presence check. | **MOOT.** Owner directive: no verification in the agent. There is no in-agent verifier to be a presence check. §8 is now the external-validation contract and §4.6 is what this system owes it — every tool result exact, associable, ordered, durable. The finding was correct and is retired by removing its subject, not by answering it. |
| **6** | Hidden tools / role-refusal on the root. | **SUPERSEDED**. The hidden-tools decision no longer exists. §3.3: all 34 executable on the root; no `research()`; test 8 dropped. |
| **7** | Budgets not build-ready: no line allocation, no provider seam, `run_turn` has no model parameter. | **Answered** on the seam: `run_turn` takes resolved `model` / `api_base` / `api_key_env` (§2); `complete()` is three prefix `if`s, not a Provider class (§2.3). **Answered** on the numeric ceiling (D-2): the orchestrator lifted the 199-line `agent_harness.py` budget unconditionally (§1) so the six-field loop could stay readable; a fourth harness file is still stop-and-ask. Neither §1 nor this row still claims 200 or 250 as a standing ceiling, or attributes the lift to the owner. |

Nothing in this table authorizes engine edits, a fourth harness file, or starting the build.
