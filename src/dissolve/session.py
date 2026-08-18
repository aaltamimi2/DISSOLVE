"""The session record: a dict, a binder, and a handle table.

The record stays a dict. Handles are the durable store. `last_candidates`
is a per-call bind from a handle, then gone. `candidate_evidence` and
`resolve_candidate_argument` are unchanged.

WHAT WAS DELIBERATELY NOT PORTED: the candidate *shape-code* machinery.
`candidate_evidence` returns the stored source dict unchanged.
"""

from __future__ import annotations

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
# `sensitivity_rows` is included because analyze_tea_sensitivity publishes
# that page. Nested pages (compare_contaminant_removal_modes) are not
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
# `family_summaries`, `family_red_summaries`, `screened_directions` —
# are not in this tuple.
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
)


class SessionRecord(dict):
    """A dict that does not AttributeError on `state.last_contaminant`.

    Missing names return None. This is not SessionState: it writes nothing.
    Keys are the store. Do not set attributes.
    """

    def __getattr__(self, name: str) -> Any:
        return None

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"session record is a dict; set keys, not attributes ({name!r})"
        )


def new_session() -> SessionRecord:
    return SessionRecord(handles={}, reported=[], turn_records={})


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
    exact: Any,
    handle: str | None = None,
    display: str | None = None,
) -> None:
    """Append one exact call to the active turn. Same object a handle stores.

    Opens a turn if dispatch ran outside run_turn (tests). Not a second row store:
    when `handle` is set, `exact` must be handles[handle]['exact'] (engine data).
    `display` is the engine string; together they are the `{display, data}` envelope
    without copying the row population.
    """
    record = _bound_record(record)
    tid = record.get("_turn")
    if not tid:
        tid = open_turn_record(record)
    table = record.setdefault("turn_records", {})
    table.setdefault(tid, []).append({
        "tool": tool,
        "args": dict(args),
        "exact": exact,
        "handle": handle,
        "display": display,
    })


def load_turn_record(record: dict[str, Any], turn_id: str) -> list | None:
    """Validator/test read. The loop must not call this."""
    record = _bound_record(record)
    rows = (record.get("turn_records") or {}).get(turn_id)
    return rows if isinstance(rows, list) else None


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
