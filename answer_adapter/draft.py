"""Draft-profile and draft-structure checks."""

from __future__ import annotations

import re
from typing import Mapping

from answer_adapter.constants import (
    DRAFT_STATUS,
    DRAFT_TOP_LEVEL_KEYS,
    UNANSWERED_REASONS,
)

TOKEN_RE = re.compile(r"^E[1-9][0-9]*$")


def json_pointer_claim(index: int) -> str:
    return f"/claims/{index}"


def unanswered_errors(draft: Mapping) -> list[dict]:
    errors = []
    entries = draft.get("unanswered_subparts")
    if not isinstance(entries, list):
        return errors
    for i, entry in enumerate(entries):
        path = f"/unanswered_subparts/{i}"
        if not isinstance(entry, Mapping):
            errors.append({"path": path, "code": "missing_subpart_id"})
            continue
        if "subpart_id" not in entry:
            errors.append({"path": path, "code": "missing_subpart_id"})
            continue
        reason = entry.get("reason")
        if reason not in UNANSWERED_REASONS:
            errors.append({"path": path, "code": "invalid_reason"})
    return errors


def valid_unanswered_entries(draft: Mapping) -> list[dict]:
    entries = draft.get("unanswered_subparts")
    if not isinstance(entries, list):
        return []
    kept = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        if "subpart_id" not in entry:
            continue
        if entry.get("reason") not in UNANSWERED_REASONS:
            continue
        item = {"subpart_id": entry["subpart_id"], "reason": entry["reason"]}
        if "resolvable_by" in entry:
            item["resolvable_by"] = entry["resolvable_by"]
        kept.append(item)
    return kept


def draft_structure(draft: object) -> tuple[bool, list]:
    if not isinstance(draft, Mapping):
        return False, [{"path": "", "code": "not_object"}]
    errors = []
    for key in DRAFT_TOP_LEVEL_KEYS:
        if key not in draft:
            errors.append({"path": f"/{key}", "code": "missing_key"})
    status = draft.get("status") if isinstance(draft, Mapping) else None
    if status not in DRAFT_STATUS:
        errors.append({"path": "/status", "code": "invalid_status"})
    for key in ("claims", "unanswered_subparts", "limitations"):
        if key in draft and not isinstance(draft.get(key), list):
            errors.append({"path": f"/{key}", "code": "invalid_type"})
    return (len(errors) == 0), errors


def profile_claim_outcomes(claim: Mapping, profile: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    flags: list[str] = []
    refs = claim.get("evidence_refs")
    empty_refs = not isinstance(refs, list) or len(refs) == 0
    source_basis = claim.get("source_basis")
    if profile == "AnswerEnvelope.v1":
        if empty_refs:
            errors.append("empty_evidence_refs")
        if source_basis == "model_knowledge":
            errors.append("illegal_source_basis")
    elif profile == "ENVELOPE.closed_book.v1":
        if source_basis != "model_knowledge":
            errors.append("illegal_source_basis")
        if not empty_refs:
            flags.append("citation_without_access")
    return errors, flags


def check_draft(draft: object, profile: str) -> dict:
    structure_ok, structure_errors = draft_structure(draft)
    claim_errors: dict[str, list[str]] = {}
    claim_flags: dict[str, list[str]] = {}
    draft_errors = []
    if structure_ok and isinstance(draft, Mapping):
        draft_errors.extend(unanswered_errors(draft))
        claims = draft.get("claims") if isinstance(draft.get("claims"), list) else []
        for i, claim in enumerate(claims):
            if not isinstance(claim, Mapping):
                continue
            errs, flags = profile_claim_outcomes(claim, profile)
            pointer = json_pointer_claim(i)
            if errs:
                claim_errors[pointer] = errs
            if flags:
                claim_flags[pointer] = flags
    else:
        draft_errors.extend(structure_errors)
    return {
        "check_scope": "draft_profile",
        "draft_structure_valid": structure_ok,
        "draft_errors": draft_errors,
        "claim_errors": claim_errors,
        "claim_flags": claim_flags,
    }


def discarded_draft_keys(draft: Mapping) -> list[str]:
    extra = [k for k in draft.keys() if k not in DRAFT_TOP_LEVEL_KEYS]
    return sorted(extra)
