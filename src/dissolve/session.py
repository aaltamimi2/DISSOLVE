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
    return SessionRecord(handles={}, reported=[])


def current_tool_session() -> dict[str, Any] | None:
    """The session record bound for this invocation, or None outside one."""
    return _ACTIVE.get()


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
    token = _ACTIVE.set(bound)
    try:
        yield bound
    finally:
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


def store_handle(
    record: dict[str, Any],
    *,
    tool: str,
    source_basis: str,
    data: Any,
    rows: list[dict[str, Any]],
) -> str:
    """Durable write. Does not also write last_candidates."""
    handles = record.setdefault("handles", {})
    name = _unused_handle(handles)
    handles[name] = {
        "tool": tool,
        "source_basis": source_basis,
        "total": len(rows),
        "exact": data,
        "rows": rows,
    }
    return name


def load_handle(record: dict[str, Any], handle: str) -> dict[str, Any] | None:
    stored = (record.get("handles") or {}).get(handle)
    return stored if isinstance(stored, dict) else None


@contextmanager
def bind_handle_rows(record: dict[str, Any], handle: str) -> Iterator[None]:
    """Per-call last_candidates bind from a handle. Unbinds in finally."""
    stored = load_handle(record, handle)
    if stored is None:
        raise KeyError(handle)
    record["last_candidates"] = stored["rows"]
    record["last_candidates_source"] = {
        "handle": handle,
        "total": stored["total"],
        "source_tool": stored["tool"],
    }
    try:
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
