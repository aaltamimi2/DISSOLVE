"""The session record: a plain dict, and the three helpers tools need to read it.

This replaces every engine's `from .runtime import ...` in v12. In v11 those
three names lived in a 6,000-line module alongside compaction, context
projection, plan validation and the specialist harness, so importing a solvent
helper dragged the whole orchestration layer with it. They are ~40 lines.

**The session record is a plain dict.** v11 passed a `SessionState` dataclass and
the helpers branched on its type; the dict branch already existed and is the only
one kept here. Tools read and write exact objects. Nothing projects, validates or
compacts on the way past.

WHAT WAS DELIBERATELY NOT PORTED: the candidate *shape-code* machinery
(`_candidate_source_from_prompt`, `_candidate_source`, and their reverse code
tables). That decides whether a producer's rows are an acceptable shape for a
consumer, which is a routing decision, not a tool one. `candidate_evidence`
returns the stored source dict unchanged and lets the caller decide. If a flat
harness later needs shape gating, it belongs in the harness.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Sequence

_ACTIVE: ContextVar[dict[str, Any] | None] = ContextVar(
    "dissolve_active_tool_session", default=None,
)


def current_tool_session() -> dict[str, Any] | None:
    """The session record bound for this invocation, or None outside one."""
    return _ACTIVE.get()


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
