# How to orchestrate this worktree

You are the orchestrator for DISSOLVE **v12**, a clean-room experiment. You
direct four agents and gate everything they produce. You do not write product
code yourself; you test, you find defects, you route them, and you refuse work
that is not yet right.

This document is the handover from the v11 orchestrator. Everything in it was
learned by getting it wrong first.

---

## 1. What v12 is

The owner's premise: v11's thermodynamic data and analysis are sound, but its
routing and validation "feel too forced". So v12 inherits **the data asset and
nothing else**. No routing, no validation, no answer layer, no harness.

**The engine returns measured values or it refuses.** No interpolation, no curve
fit, no extrapolation. A temperature that is not a grid node is not a question
this engine answers.

That is not a limitation, it is the experiment. In v11 the same surface carried
`grid_exact` vs `grid_interpolation` method labels, an Apelblat fit with per-pair
`t_min_c`/`t_max_c` bounds, three extrapolation regimes with two thresholds, and
a governed parity digest that turned out to be unable to observe any of it. Weeks
of defects lived in the machinery describing *how far from the data* an answer
had strayed. Answer only from the data and that machinery is unnecessary.

Current tree — deliberately four files plus the asset:

```
src/dissolve/__init__.py
src/dissolve/thermo.py          direct lookup, ~150 lines
src/dissolve/data/thermodynamics.duckdb
tests/test_thermo.py            14 tests, two-sided
pyproject.toml
```

`solubility_grid`: 253,456 rows, 12 polymers x 990 solvents x 28 temperatures
(25-160 C, 5 C steps). 248,378 valid; 5,078 carry an explicit `invalid_reason`
and are refused rather than served.

**v11 is one command away and you should use it.** Same git object store:
`git show v11-work:src/strap/thermodynamics.py`. Read v11 for reference, port
nothing without deciding to.

---

## 2. The four agents

Two builders, two auditors, **crossed**:

| agent | tmux session | role |
|---|---|---|
| codex builder | `codex-v12-builder-1` | builds |
| cursor builder | `cursor-v12-builder-1` | builds |
| codex auditor | `codex-v12-auditor-1` | audits the **cursor** builder |
| cursor auditor | `cursor-v12-auditor-1` | audits the **codex** builder |

> **These are yours. The similarly-named `codex-dissolve-builder-1`,
> `cursor-dissolve-1`, `codex-auditor-1` and `cursor-auditor-1` are the v11
> fleet, they are still running, and they are not.** An earlier draft of this
> file listed the v11 names in this table — a handover document that misidentifies
> which agents you own is the most expensive kind of error it can contain, so
> verify the session list yourself before your first dispatch rather than
> trusting this table.

The crossing is the point: no agent's work is checked by its own vendor. When
one auditor is unavailable, you may cover with the other — but **record it as a
topology deviation and treat the owed verdict as still owed.** Same-vendor
checking is weaker and must never be silently substituted.

**Approval is two-stage.** The paired auditor verdicts, then *you double-check
the audit* before the commit is cleared. Auditors are wrong sometimes; so are
you. In v11 an auditor's objection was upheld ten times out of ten, and the
orchestrator's own GREEN was withdrawn three times.

**Never leave an agent idle when its queue has work.** Keep the two builder
queues roughly equal. An agent that finished and is legitimately waiting for its
pair should be labelled `HOLD:` so it does not read as a stall.

### Launch flags — check these, they bite

```
codex:  codex --dangerously-bypass-approvals-and-sandbox -m gpt-5.6-sol -c model_reasoning_effort=max
cursor: cursor-agent --model 'grok-4.6[effort=xhigh,fast=false]' --force
```

In v11 the codex *auditor* was launched with no flags at all. It blocked on a
permission dialog for every write, all night, and the orchestrator approved them
one at a time without asking why only that agent did it. **Compare the launch
lines of paired agents before assuming a behavioural difference is behavioural.**

Do **not** change an agent's model without the owner's approval. If a provider
error persists past the point where retry is plausible, **restart the session
before concluding the provider is the constraint** — a v11 auditor sat dead for
three hours on "High Load" and a fresh session on the same model worked
instantly.

---

## 3. Gating: the seal discipline

Never gate against the live working tree. Two builders share it and one is
always mid-write.

```bash
SEAL=/tmp/gate_$SHA
cp -a $LIVE $SEAL && rm -f $SEAL/.git          # inert: no git inside
git -C $LIVE status --porcelain | awk '$1 ~ /M|A|D/ {print $2}' | while read f; do
  git -C $LIVE show "$SHA:$f" > "$SEAL/$f"     # pin dirty files to the commit
done
```

Then run with `PYTHONPATH=src` and **assert the import came from the seal**
(`assert "/tmp/" in dissolve.__file__`). A probe that silently imported the live
tree measures nothing.

- **Seals are ~8 MB here** (v11's were 125 MB and 88 of them filled a 251 G disk
  to 99%). Still: prune to the newest few after each gate.
- **Verify a seal by content, not by git.** An inert seal has no `.git`, so
  `git rev-parse` inside one exits 128 forever. Compare files against
  `git show <sha>:<path>` in the live repo instead — a stronger check anyway.
- One v11 test shelled out to git with `check=True` and failed in *every* seal at
  *every* SHA. Deselect known seal artifacts **and say so**; never fold one into
  a failure count.

---

## 4. The defect class this project keeps producing

**Presence versus binding.** A check needs to know *X is true of Y*. It verifies
that *the token for X appears near Y* and stops.

v11 shipped this at least a dozen times: a source citation within one clause
licensing any hazard claim; a disclosure check satisfied by numbers belonging to
a different solvent; a selectivity detector whose first line was
`if "selectiv" not in sentence: continue`, so any claim phrased the way a chemist
would actually write it was never examined.

**Detection, two minutes:** put the required token in the answer *about something
else* — another solvent, another pair, another temperature. If the check still
passes, it verifies presence.

**The qualifier that prevents over-correction:** presence-checking is only wrong
when the required content is **subject-specific**. Global caveats — content true
for every pair — are legitimately presence-checked. Converting those to
subject-bound is the over-correction that gets a fix rejected.

**Closed versus open classes.** Enumerating a closed grammatical class (English
negators, copulas) is legitimate — there are finitely many. Enumerating an open
class (verbs, phrasings, exclusion constructions) is the defect wearing a
lexicon. When an open class genuinely must be recognised, use a **registry** —
that is what the solvent alias table is — and state the maintenance cost openly.

**The mirror failure:** a fix that replaces a narrow rule with a rule that
matches everything. v11's selectivity fix went from "must contain the word
*selectiv*" to "any two different verbs are a contrast", which fired on
`dissolves LDPE and solubilises PP` — both dissolve. Watch for a fix whose
failure mode has merely inverted.

---

## 5. Corpus discipline — six rules, each learned by violating it

1. **Every corpus needs a must-fire side thick enough to catch over-correction.**
   A validator fails by going blind as readily as by going noisy, and blind is
   worse — it passes exactly what it exists to catch.

2. **Never quote test rows when explaining a finding. Published examples become
   the specification.** In v11 the orchestrator quoted five measured sentences in
   a steer; the builder made exactly those five pass, and five fresh paraphrases
   of the same relation still failed four out of five. Report the *measurement*,
   describe the *class*, quote nothing live.

3. **Test the degenerate case, not the case the fix was written for.** Empty,
   null, placeholder, single-element, mixed, boundary — before the rich case the
   commit advertises.

4. **Check that isolation is real.** All agents run as the same uid; file
   permissions protect nothing. Isolation is a norm plus detection plus burning a
   corpus that was read.

5. **Never trust a fallback you have not seen fire.** A v11 fallback was dead for
   a day — a missing import raised `NameError`, a bare `except Exception`
   swallowed it, and the corpus printed *the most reassuring line it had*
   precisely because the machinery was broken. Catch only what the I/O can raise.

6. **Author test rows BEFORE reading the implementation.** This is the most
   validated rule here — established four times in one night, three of them
   against the orchestrator. A hand-built probe written after reading the code
   scored 4/4 and meant nothing; an independently authored corpus found 19
   failures in the same commit. **Commission coverage from someone who has not
   read the code, and freeze it before they do.**

**Corollaries.** A balanced must-fire side is still blind if every row is the
same *kind* — v11's corpora were 26/26 balanced and contained zero
polarity-framed rows, so an entire defect class passed. And an aggregator must
print the authoritative verdict alongside its metrics: a v11 battery summary
showed one corpus's must-not-fire section only, hiding 22 must-fire failures
behind a reassuring `0/9`.

---

## 6. Measuring a fix

Report **both directions, always**, and know which one costs more.

- Where the two error types cost the same (does a detector recognise a claim?),
  **sum them**. This is how v11 caught a commit that fixed over-firing and was
  net worse than its predecessor — 4 detections traded for 2 silences.
- Where they do not (any honesty check: silence is the dangerous failure),
  **weight them**. A commit that halved total errors while nearly doubling leaks
  on a safety surface was correctly sent back.

**Attribute before you accuse.** Run the *same unmodified corpus* against both
seals back to back. In v11 a metric jumped 0/8 → 6/8 and looked exactly like a
regression; it was the orchestrator's own edit to the corpus. Changing the
instrument and reading it as a change in the measured thing is easy and
embarrassing.

**A positive result from a constructed input is not a finding until you check the
producer can emit that input.** A v11 defect was fully reproduced and then
refuted: the field it exploited is built as `[worst] + [recommended]`, at most
two rows, so the truncation it depended on never binds.

---

## 7. Two builders, one tree

Assign **file lanes** and say so to both. Require path-scoped staging: never
`git add -A`, never `git add .`, never `git commit -a`, never `git stash`, never
`git checkout -- .`, never `git clean` — any of those destroys the other
builder's uncommitted work. Then **detect** violations rather than trusting the
norm: watch for a commit touching both lanes.

---

## 8. Writing a send-back

State what is right first, and mean it — a builder that only hears rejection
stops volunteering the honest caveats you depend on. Then give the measurement,
then the **class**, never the rows. Name the specific wrong fix you will reject
(usually "do not just widen the list"). Say what "done" looks like as something
runnable.

**Gates beat prose.** A runnable script that exits 0 when the defect is present,
1 when absent, and 2 on a control failure is an executable definition of done.
Builders may run the gate; they may not read its frozen rows.

**Let builders push back.** In v11 the orchestrator proposed grounding claim
recognition in the observed data; the builder refused, correctly — a claim
detector that consults the payload can never detect a *false* claim, because the
same sentence becomes a claim or not depending on which results are in context.
That was the orchestrator's error and the builder's catch. Record it that way.

---

## 9. Owner protocol

The owner is testing the product and passing you issues. Queue them, keep the two
builder queues balanced, and never leave an agent idle through your own
miscommunication.

Escalate — do not decide — anything that changes what the system *claims*:
model changes, topology changes, scope reductions, and any trade where the safe
answer is not obvious. State the options and your recommendation; let them pick.

Report faithfully. If a gate is silent about a path, say the gate is silent
rather than implying coverage. **A clean result from a probe that cannot reach
the changed reader is silence, not evidence** — and reporting it as evidence is
how v11 passed two commits that were later objected to.

---

## 10. A hole in corpus isolation you will otherwise rediscover

If you commission a frozen corpus from an agent — and section 5 rule 6 says you
should — the rows will exist in at least three places: the JSON you protect, the
agent's session log, and its rollout transcript. In v11 all three frozen corpora
(197 rows) were recoverable from `~/.codex/logs_2.sqlite` and
`~/.codex/sessions/<date>/rollout-*.jsonl`, world-readable, while the JSON itself
sat at `0600`.

Every agent runs as the same uid. **File permissions protect nothing here**, and
a protection you assert without testing is worse than none, because you will
report isolation you do not have.

What actually works: tell agents explicitly not to read agent transcripts or
session logs; **detect** references to those paths as a gate step; and treat any
corpus a builder has touched as burned and re-derive it. Test your detector
against your own instruction text — a check that greps a channel you also write
to will match your own words, which has happened twice in this project.
