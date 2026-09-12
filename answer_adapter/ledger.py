"""Run ledger, presentation, and draft-reference normalization."""

from __future__ import annotations

import copy
import hashlib
from typing import Mapping

from answer_adapter.constants import (
    LEDGER_SCHEMA,
    PRESENTED_ROW_FIELDS,
    SERVED_FIELDS,
)
from answer_adapter.draft import (
    TOKEN_RE,
    discarded_draft_keys,
    unanswered_errors,
    valid_unanswered_entries,
)
from answer_adapter.halt import AdapterHalt


def new_ledger() -> dict:
    return {"schema": LEDGER_SCHEMA, "executed_calls": 0, "entries": []}


def excerpt_sha256(excerpt: object) -> str:
    return hashlib.sha256(str(excerpt).encode("utf-8")).hexdigest()


def _call_tool(call: Mapping) -> str:
    if not isinstance(call, Mapping):
        return ""
    tool = call.get("tool")
    if tool is None:
        return ""
    return str(tool)


def _call_ordinal(call: Mapping) -> object:
    if not isinstance(call, Mapping):
        return None
    return call.get("call_ordinal")


def _walk_passage_rows(node: object):
    if isinstance(node, Mapping):
        if "chunk_id" in node and "excerpt" in node:
            yield node
            return
        for value in node.values():
            yield from _walk_passage_rows(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_passage_rows(item)


def passage_rows(result: Mapping) -> list[dict]:
    rows: list[dict] = []
    if not isinstance(result, Mapping):
        return rows
    data = result.get("data")
    if isinstance(data, Mapping) and "results" in data:
        rows.extend(_walk_passage_rows(data.get("results")))
    if "top" in result:
        rows.extend(_walk_passage_rows(result.get("top")))
    return rows


def _served_from_row(row: Mapping) -> dict:
    served = {}
    for key in SERVED_FIELDS:
        if key in row:
            served[key] = row[key]
    return served


def _next_token(entry_count: int) -> str:
    return f"E{entry_count + 1}"


def _presented_row(row: Mapping, token: str) -> dict:
    presented = {"citation_id": token}
    for key in PRESENTED_ROW_FIELDS:
        if key == "citation_id":
            continue
        presented[key] = row.get(key)
    return presented


def _message_with_passages(result: Mapping, presented_rows: list[dict]) -> dict:
    return {
        "available": result.get("available"),
        "source_basis": result.get("source_basis"),
        "result_count": len(presented_rows),
        "results": presented_rows,
    }


def present_result(result: object, ledger: object, call: object) -> dict:
    tool = _call_tool(call if isinstance(call, Mapping) else {})
    if tool == "":
        raise AdapterHalt("call_tool_missing")
    if not isinstance(ledger, Mapping):
        raise AdapterHalt("call_ordinal_mismatch")
    expected_ordinal = int(ledger.get("executed_calls") or 0) + 1
    ordinal = _call_ordinal(call if isinstance(call, Mapping) else {})
    if ordinal != expected_ordinal:
        raise AdapterHalt("call_ordinal_mismatch")
    new = {
        "schema": ledger.get("schema", LEDGER_SCHEMA),
        "executed_calls": ordinal,
        "entries": copy.deepcopy(list(ledger.get("entries") or [])),
    }
    rows = passage_rows(result if isinstance(result, Mapping) else {})
    presented_rows = []
    for row_i, row in enumerate(rows, start=1):
        token = _next_token(len(new["entries"]))
        entry = {
            "evidence_token": token,
            "tool": tool,
            "call_ordinal": ordinal,
            "row_ordinal": row_i,
            "retrieval_unit_id": row.get("chunk_id"),
            "served": _served_from_row(row),
            "excerpt_sha256": excerpt_sha256(row.get("excerpt")),
        }
        new["entries"].append(entry)
        presented_rows.append(_presented_row(row, token))
    if presented_rows:
        message = _message_with_passages(result if isinstance(result, Mapping) else {}, presented_rows)
    else:
        data = result.get("data") if isinstance(result, Mapping) else None
        message = copy.deepcopy(data)
    return {"message": message, "ledger": new}


def adapter_ref_from_entry(entry: Mapping, substrate_manifest_sha256: str) -> dict:
    return {
        "kind": "adapter_ref.v1",
        "evidence_token": entry["evidence_token"],
        "tool": entry["tool"],
        "call_ordinal": entry["call_ordinal"],
        "row_ordinal": entry["row_ordinal"],
        "retrieval_unit": {
            "id": entry["retrieval_unit_id"],
            "version": substrate_manifest_sha256,
        },
        "served": copy.deepcopy(entry.get("served") or {}),
        "excerpt_sha256": entry["excerpt_sha256"],
        "binding_status": "unbound_pending",
    }


def _entry_by_token(ledger: Mapping) -> dict:
    return {e["evidence_token"]: e for e in ledger.get("entries") or [] if isinstance(e, Mapping) and "evidence_token" in e}


def _token_and_form(ref: object) -> tuple[str | None, object]:
    if isinstance(ref, str):
        return ref, ref
    if isinstance(ref, Mapping) and isinstance(ref.get("citation_id"), str):
        return ref["citation_id"], ref
    return None, ref


def normalize_reference(ref: object, arm: str, entries: Mapping, substrate_manifest_sha256: str) -> dict:
    token, draft_ref = _token_and_form(ref)
    if token is None:
        return {"kind": "unresolved_ref.v1", "reason": "malformed_ref", "draft_ref": draft_ref}
    if arm == "closed_book":
        return {"kind": "unresolved_ref.v1", "reason": "no_visible_evidence", "draft_ref": draft_ref}
    if not TOKEN_RE.match(token):
        return {"kind": "unresolved_ref.v1", "reason": "malformed_ref", "draft_ref": draft_ref}
    entry = entries.get(token)
    if entry is None:
        return {"kind": "unresolved_ref.v1", "reason": "unknown_token", "draft_ref": draft_ref}
    return adapter_ref_from_entry(entry, substrate_manifest_sha256)


def normalize_draft(draft: object, ledger: object, arm: str, substrate_manifest_sha256: str) -> dict:
    if not isinstance(draft, Mapping):
        return {
            "claims": [],
            "unanswered_subparts": [],
            "limitations": [],
            "discarded_draft_keys": [],
            "draft_errors": [{"path": "", "code": "not_object"}],
            "support_refs": [],
        }
    entries = _entry_by_token(ledger if isinstance(ledger, Mapping) else {})
    claims_out = []
    claims = draft.get("claims") if isinstance(draft.get("claims"), list) else []
    for claim in claims:
        if not isinstance(claim, Mapping):
            claims_out.append(claim)
            continue
        copied = dict(claim)
        refs = claim.get("evidence_refs")
        if isinstance(refs, list):
            copied["evidence_refs"] = [
                normalize_reference(ref, arm, entries, substrate_manifest_sha256) for ref in refs
            ]
        claims_out.append(copied)
    if arm == "closed_book":
        support_refs = []
    else:
        ledger_entries = list((ledger or {}).get("entries") or []) if isinstance(ledger, Mapping) else []
        support_refs = [adapter_ref_from_entry(e, substrate_manifest_sha256) for e in ledger_entries]
    return {
        "claims": claims_out,
        "unanswered_subparts": valid_unanswered_entries(draft),
        "limitations": list(draft.get("limitations") or []),
        "discarded_draft_keys": discarded_draft_keys(draft),
        "draft_errors": unanswered_errors(draft),
        "support_refs": support_refs,
    }
