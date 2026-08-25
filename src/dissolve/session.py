"""The session record: a dict, a binder, and a handle table.

The record stays a dict. Handles are the durable store. `last_candidates`
is a per-call bind from a handle, then gone. `candidate_evidence` and
`resolve_candidate_argument` are unchanged.

WHAT WAS DELIBERATELY NOT PORTED: the candidate *shape-code* machinery.
`candidate_evidence` returns the stored source dict unchanged.
"""

from __future__ import annotations

import json
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Sequence

_ACTIVE: ContextVar[dict[str, Any] | None] = ContextVar(
    "dissolve_active_tool_session", default=None,
)

# Closed lists. Not derived from solvent names (a handle that contains
# "dodecane" is a leak, not an address). 32 of each is enough.
_HANDLE_ADJECTIVES = (
    "amber", "brisk", "calm", "clear", "crisp", "dapper", "eager", "faint",
    "gentle", "glad", "hazy", "keen", "kind", "lively", "lucid", "merry",
    "nimble", "pale", "plaid", "proud", "quiet", "rapid", "rusty", "sly",
    "stark", "steady", "sunny", "swift", "tidy", "vivid", "warm", "witty",
)
_HANDLE_COLORS = (
    "amber", "azure", "beige", "black", "blue", "brass", "brown", "coral",
    "cream", "cyan", "gold", "gray", "green", "ivory", "jade", "khaki",
    "lemon", "lilac", "maroon", "navy", "olive", "peach", "pearl", "pink",
    "plum", "red", "rose", "rust", "silver", "teal", "violet", "white",
)
_HANDLE_ANIMALS = (
    "auk", "bass", "bear", "boar", "carp", "cat", "colt", "crab",
    "crow", "deer", "dove", "duck", "elk", "finch", "fox", "frog",
    "gnat", "goat", "hare", "hawk", "ibis", "jay", "kite", "lark",
    "lynx", "mole", "moth", "newt", "owl", "pike", "puma", "wren",
)

# First matching non-empty list[dict] wins. Not longest: payloads can carry
# a `resolution_issues` (or similar sidecar) longer than the result page,
# and longest-wins would silently make the error list the answer.
#
# `joined_rows` before `rows`: screen_hansen_compatibility with a temperature
# publishes both; the displayed answer is the joined page. Empty joined_rows
# are skipped, so the no-temperature branch still selects `rows`.
#
# This tuple is not a complete catalog of registered list[dict] keys.
# `sensitivity_rows` is included because evaluate_process mode=sensitivity
# (and the analyze_tea_sensitivity engine) publishes that page. Nested
# pages (compare_contaminant_removal_modes) are not
# selected here — dispatch must still attempt a handle when either
# threshold is crossed and primary_row_key is None.
# `candidate_substitutions` before `comparison_rows`: route-substitution
# displays the full condition objects (temperature, selectivity, G-score
# change). `comparison_rows` on that tool is the reduced safety card and
# would drop temperatures the safety consumer reads as `temperature_c`.
# Safety comparison still selects `comparison_rows` because it has no
# `candidate_substitutions` page.
# Sidecars that are list[dict] but are not the answer —
# `resolution_issues`, `record_assumptions`, `source_records`,
# `family_summaries`, `family_red_summaries`, `screened_directions`,
# `ranked_path_index` — are not in this tuple.
# `top_k_sequences` is last: a plan's winner `steps` stays the primary
# page when both lists are non-empty.
_PRIMARY_KEYS = (
    "ranked_candidates",
    "ranked_pairs",
    "results",
    "joined_rows",
    "rows",
    "candidate_solvents",
    "candidate_substitutions",
    "comparison_rows",
    "records",
    "matches",
    "safety_profiles",
    "steps",
    "leading_matches",
    "candidate_conditions",
    "scale_comparison_rows",
    "sensitivity_rows",
    "landscape_points",
    "frontier_points",
    "grouped_fronts",
    "top_k_sequences",
)


class SessionRecord(dict):
    """A dict that does not AttributeError on `state.last_contaminant`.

    Present keys are readable as attributes so `getattr(state, "last_tea")`
    sees the store. Missing names return None. This is not SessionState:
    it writes nothing. Keys are the store. Do not set attributes.
    """

    def __getattr__(self, name: str) -> Any:
        return self.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"session record is a dict; set keys, not attributes ({name!r})"
        )


def new_session() -> SessionRecord:
    return SessionRecord(
        handles={}, reported=[], turn_records={},
        polymers_in_play=[], temperatures_in_play=[],
    )


def current_tool_session() -> dict[str, Any] | None:
    """The session record bound for this invocation, or None outside one."""
    return _ACTIVE.get()


def _bound_record(record: dict[str, Any]) -> dict[str, Any]:
    """Writes and handle reads go through the active wrapper, or `record`.

    A plain dict is copied at bind. Passing the original while the binder
    is active is a hard error, not a silent no-op.
    """
    active = _ACTIVE.get()
    if active is None:
        return record
    if record is not active:
        raise RuntimeError(
            "session writes must use the bound record from bind_tool_session"
        )
    return active


@contextmanager
def bind_tool_session(record: dict[str, Any]) -> Iterator[SessionRecord]:
    """Bind `record` for one turn.

    Engines see a SessionRecord (missing attributes are None, not
    AttributeError). A plain dict is wrapped for the duration and copied
    back on exit so the caller's object still holds handles.
    """
    bound = record if isinstance(record, SessionRecord) else SessionRecord(record)
    bound.setdefault("handles", {})
    bound.setdefault("reported", [])
    bound.setdefault("turn_records", {})
    bound.setdefault("polymers_in_play", [])
    bound.setdefault("temperatures_in_play", [])
    token = _ACTIVE.set(bound)
    try:
        yield bound
    finally:
        bound.pop("_turn", None)
        _ACTIVE.reset(token)
        if bound is not record:
            record.clear()
            record.update(bound)


def _unused_handle(handles: dict[str, Any]) -> str:
    for _ in range(64):
        name = (
            f"{secrets.choice(_HANDLE_ADJECTIVES)}-"
            f"{secrets.choice(_HANDLE_COLORS)}-"
            f"{secrets.choice(_HANDLE_ANIMALS)}"
        )
        if name not in handles:
            return name
    raise RuntimeError("handle namespace exhausted")


def primary_row_key(data: Any) -> str | None:
    """Name of the primary row list inside exact `data`, or None.

    Walks `_PRIMARY_KEYS` in order. First non-empty list[dict] wins.
    """
    if not isinstance(data, dict):
        return None
    for name in _PRIMARY_KEYS:
        value = data.get(name)
        if not isinstance(value, list) or not value:
            continue
        if all(isinstance(item, dict) for item in value):
            return name
    return None


def handle_rows(stored: dict[str, Any]) -> list[dict[str, Any]]:
    """The primary list inside `stored['exact']`. A reference, not a copy."""
    exact = stored.get("exact")
    key = primary_row_key(exact)
    if key is None:
        raise ValueError("handle has no primary row list")
    return exact[key]


def handle_total(stored: dict[str, Any]) -> int:
    return len(handle_rows(stored))


def open_turn_record(record: dict[str, Any]) -> str:
    """Start an ordered exact archive for one turn. Returns its id.

    Nothing in the loop reads the list this id names.
    """
    record = _bound_record(record)
    table = record.setdefault("turn_records", {})
    n = len(table) + 1
    name = f"turn-{n}"
    while name in table:
        n += 1
        name = f"turn-{n}"
    table[name] = []
    record["_turn"] = name
    return name


def record_tool_call(
    record: dict[str, Any],
    *,
    tool: str,
    args: dict[str, Any],
    exact: Any = None,
    handle: str | None = None,
    display: str | None = None,
    source_basis: str | None = None,
) -> None:
    """Append one call to the active turn.

    Handled producers name the handle and do not embed `exact` — that object
    lives once in handles[handle]['exact']. Unhandled producers store the
    engine envelope `{display, data}`. result_read stores the page payload
    and names the source handle.
    """
    record = _bound_record(record)
    tid = record.get("_turn")
    if not tid:
        tid = open_turn_record(record)
    row: dict[str, Any] = {
        "tool": tool,
        "args": dict(args),
        "handle": handle,
        "source_basis": source_basis,
    }
    if exact is not None:
        row["exact"] = exact
    record.setdefault("turn_records", {}).setdefault(tid, []).append(row)


def load_turn_record(record: dict[str, Any], turn_id: str) -> list | None:
    """Validator/test read. The loop must not call this."""
    record = _bound_record(record)
    rows = (record.get("turn_records") or {}).get(turn_id)
    return rows if isinstance(rows, list) else None


_NOT_REPORTED = frozenset({
    "shown", "total", "available_count", "returned", "offset",
    "available", "success",
})
_POLY_FROM_SIG: frozenset[str] | None = None
_TEMP_FROM_SIG: frozenset[str] | None = None


class CompactionBudgetError(RuntimeError):
    """§7 template plus the protected current group cannot fit in window-reserve.

    Not a license to omit reported numbers or to call the provider over budget.
    """


def append_reported(record: dict[str, Any], payload: dict[str, Any]) -> None:
    """Book-keeping for §7. Row-field numbers only, not shown/total/counts."""
    record = _bound_record(record)
    basis, handle = payload.get("source_basis"), payload.get("handle")
    rows: list[dict[str, Any]] = []
    top = payload.get("top")
    if isinstance(top, list):
        rows.extend(i for i in top if isinstance(i, dict))
    data = payload.get("data")
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
                rows.extend(value)
    store = record.setdefault("reported", [])
    seen = {(r.get("number"), r.get("source_basis"), r.get("handle")) for r in store}
    for row in rows:
        for key, value in row.items():
            if key in _NOT_REPORTED or isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)):
                continue
            item: dict[str, Any] = {"number": value, "source_basis": basis}
            if handle:
                item["handle"] = handle
            ident = (item.get("number"), item.get("source_basis"), item.get("handle"))
            if ident in seen:
                continue
            seen.add(ident)
            store.append(item)


def _subject_arg_names() -> tuple[frozenset[str], frozenset[str]]:
    """Polymer/temperature argument names from registered signatures."""
    global _POLY_FROM_SIG, _TEMP_FROM_SIG
    if _POLY_FROM_SIG is None:
        import inspect as _inspect
        from dissolve import registry as _registry
        poly, temp = set(), set()
        for spec in _registry.REGISTRY:
            for name in _inspect.signature(spec.fn).parameters:
                low = name.lower()
                if "polymer" in low:
                    poly.add(name)
                if "temp" in low:
                    temp.add(name)
        _POLY_FROM_SIG, _TEMP_FROM_SIG = frozenset(poly), frozenset(temp)
    return _POLY_FROM_SIG, _TEMP_FROM_SIG


def note_in_play(record: dict[str, Any], args: dict[str, Any]) -> None:
    """Polymers/temperatures from tool args. Not the validator archive."""
    record = _bound_record(record)
    polys = record.setdefault("polymers_in_play", [])
    temps = record.setdefault("temperatures_in_play", [])
    poly_keys, temp_keys = _subject_arg_names()
    for key, val in (args or {}).items():
        if key in poly_keys:
            if isinstance(val, str) and val and val not in polys:
                polys.append(val)
            elif isinstance(val, (list, tuple)):
                for item in val:
                    if isinstance(item, str) and item and item not in polys:
                        polys.append(item)
        elif key in temp_keys:
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, float)) and val not in temps:
                temps.append(val)
            elif isinstance(val, (list, tuple)):
                for item in val:
                    if isinstance(item, bool):
                        continue
                    if isinstance(item, (int, float)) and item not in temps:
                        temps.append(item)


def estimated_tokens(messages: list) -> int:
    return sum(len(json.dumps(m, default=str)) for m in messages) // 4


def context_window(model: str) -> int:
    """Tokens; 128k unless the alias itself names a *k window."""
    ident = (model.partition(":")[2] or model).replace("-", "_")
    for part in reversed(ident.split("_")):
        if part.endswith("k") and part[:-1].isdigit() and int(part[:-1]):
            return int(part[:-1]) * 1000
    return 128_000


def _summary_text(record: dict[str, Any]) -> str:
    record = _bound_record(record)
    polys = [str(p) for p in (record.get("polymers_in_play") or [])]
    temps = [str(t) for t in (record.get("temperatures_in_play") or [])]
    live = []
    for name, stored in (record.get("handles") or {}).items():
        if not isinstance(stored, dict):
            continue
        try:
            total = handle_total(stored)
        except (ValueError, KeyError, TypeError):
            total = "?"
        live.append(
            f"{name} (total={total}, tool={stored.get('tool')}, "
            f"source_basis={stored.get('source_basis')})"
        )
    bits = []
    for row in (record.get("reported") or []):
        bit = f"{row.get('number')} (source_basis={row.get('source_basis')}"
        if row.get("handle"):
            bit += f", handle={row['handle']}"
        bits.append(bit + ")")
    return (
        "Summary (do not continue the conversation, do not answer questions):\n"
        f"- Polymers in play: {', '.join(polys) or '(none)'}\n"
        f"- Temperatures in play: {', '.join(temps) or '(none)'}\n"
        f"- Live handles: {', '.join(live) or '(none)'}\n"
        f"- Numbers already reported: {', '.join(bits) or '(none)'}"
    )


def compact_messages(
    messages: list, record: dict[str, Any],
    *, window: int = 128_000, reserve: int = 8_000,
) -> None:
    """Trigger/cut/template. No model call. Does not consult the validator feed."""
    target = window - reserve
    if estimated_tokens(messages) <= target:
        return
    record = _bound_record(record)
    insert_at = 1 if messages and messages[0].get("role") == "system" else 0
    existing = (
        insert_at < len(messages)
        and str(messages[insert_at].get("content") or "").startswith(
            "Summary (do not continue the conversation"
        )
    )
    text = _summary_text(record)
    if existing:
        messages[insert_at]["content"] = text
    else:
        messages.insert(insert_at, {"role": "assistant", "content": text})
    keep = insert_at + 1
    while estimated_tokens(messages) > target:
        protected = next(
            (i for i in range(len(messages) - 1, keep - 1, -1)
             if messages[i].get("role") == "user"),
            None,
        )
        if protected is None or keep >= protected:
            break
        end = keep + 1
        while end < protected and messages[end].get("role") != "user":
            end += 1
        del messages[keep:end]
    if estimated_tokens(messages) > target:
        summary = messages[insert_at].get("content") if insert_at < len(messages) else ""
        raise CompactionBudgetError(
            "compaction cannot meet window-reserve without omitting "
            "numbers already reported or cutting the current user/tool group: "
            f"estimated_tokens={estimated_tokens(messages)} target={target} "
            f"(window={window} reserve={reserve}); "
            f"summary needs {len(str(summary))} characters"
        )


def store_handle(
    record: dict[str, Any],
    *,
    tool: str,
    source_basis: str,
    data: Any,
    display: str | None = None,
) -> str:
    """Durable write of exact engine `data`. Rows and total are derived.

    Does not also write last_candidates. Refuses if `data` has no primary
    row list — there is then no handle. `display` is stored beside `exact`
    so the `{display, data}` envelope is recoverable without a second copy.
    """
    record = _bound_record(record)
    if primary_row_key(data) is None:
        raise ValueError("no primary row list; no handle")
    handles = record.setdefault("handles", {})
    name = _unused_handle(handles)
    handles[name] = {
        "tool": tool,
        "source_basis": source_basis,
        "exact": data,
        "display": display,
    }
    return name


def load_handle(record: dict[str, Any], handle: str) -> dict[str, Any] | None:
    record = _bound_record(record)
    stored = (record.get("handles") or {}).get(handle)
    return stored if isinstance(stored, dict) else None


def engine_kwargs_for_handle(kwargs: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
    """Split `handle` from engine kwargs. Handle present => candidates is None.

    `resolve_candidate_argument` is unchanged and still lets an explicit
    list win. The wrapper must call this so a copied list cannot win over
    a handle. Without a handle, the explicit list is left intact (new question).
    """
    out = dict(kwargs)
    handle = out.pop("handle", None)
    if handle:
        out["candidates"] = None
    return (str(handle) if handle else None), out


@contextmanager
def bind_handle_rows(record: dict[str, Any], handle: str) -> Iterator[None]:
    """Per-call last_candidates bind from exact rows of a handle.

    Builds the source record before either transient write. Unbinds in
    finally even if the body raises. Unknown handle: KeyError, no write.
    """
    record = _bound_record(record)
    stored = load_handle(record, handle)
    if stored is None:
        raise KeyError(handle)
    rows = handle_rows(stored)
    source = {
        "handle": handle,
        "total": len(rows),
        "source_tool": stored["tool"],
    }
    try:
        record["last_candidates"] = rows
        record["last_candidates_source"] = source
        yield
    finally:
        record.pop("last_candidates", None)
        record.pop("last_candidates_source", None)


def candidate_evidence(
    context: dict[str, Any] | None,
    accepted_shapes: set[str] | frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Rows a previous tool left behind, plus whatever it recorded about them.

    Returns `(rows, source)`. `source` is passed through as stored — see the
    module docstring on why shape gating is not done here. `accepted_shapes` is
    accepted and honoured only when the stored source already carries a `shape`,
    so a caller that relies on it still works and a caller that does not is
    unaffected.
    """
    if not context:
        return [], None
    rows = [dict(item) for item in (context.get("last_candidates") or [])]
    source = context.get("last_candidates_source")
    source = dict(source) if isinstance(source, dict) else None
    if accepted_shapes and source is not None and source.get("shape") is not None:
        if source["shape"] not in accepted_shapes:
            return [], source
    return rows, source


def resolve_candidate_argument(
    explicit: Sequence[Any] | None,
    inherited: Sequence[Any] | None,
) -> tuple[list[Any], bool]:
    """Inherit stored candidates only when the caller omitted an explicit set.

    Ported verbatim from v11 — it was already pure. Note the distinction the
    v11 canonicalization bug turned on: an explicit empty list is an explicit
    override and must NOT fall through to inherited values. `explicit is not
    None` is load-bearing; `if explicit:` would be wrong.
    """
    if explicit is not None:
        return list(explicit), False
    values = list(inherited or ())
    return values, bool(values)
