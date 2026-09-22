"""§3.2.5 subpart states and §5.4 matrix."""

from __future__ import annotations

from typing import Any

OPERATIONAL_REASONS = {"tool_error", "budget_exhausted"}
REFUSAL_REASONS = {"no_evidence_in_scope", "conflicting_evidence", "parser_limited"}


def subpart_states(question: dict[str, Any], prediction: dict[str, Any], assertions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    subparts = question.get("subparts") or []
    unanswered = {u.get("subpart_id"): u.get("reason") for u in (prediction.get("unanswered_subparts") or [])}
    status = prediction.get("status")
    kind = prediction.get("kind")
    assertions = assertions or []
    flags: dict[str, list[str]] = {}
    states: dict[str, str] = {}
    reasons: dict[str, str] = {}

    envelope_fail = (
        kind in {"none", None}
        or status == "operational_failure"
        or prediction.get("operational_failure")
        or (kind == "envelope" and prediction.get("status") == "operational_failure")
    )

    for sp in subparts:
        sid = sp["subpart_id"]
        atoms = [a for a in assertions if a.get("subpart_id") == sid]
        l1 = [a for a in atoms if not a.get("invalid")]
        l0 = atoms
        listed = unanswered.get(sid)

        if envelope_fail:
            states[sid] = "operational_failure"
            continue
        if l1:
            states[sid] = "answered"
            if listed:
                flags.setdefault(sid, []).append("claim_plus_refusal")
            continue
        if l0:
            states[sid] = "answered"
            flags.setdefault(sid, []).append("invalid_only")
            continue
        if listed in OPERATIONAL_REASONS:
            states[sid] = "operational_failure"
            reasons[sid] = listed
            continue
        if listed in REFUSAL_REASONS:
            states[sid] = "refused"
            reasons[sid] = listed
            continue
        if status == "ambiguous_request":
            states[sid] = "refused"
            reasons[sid] = "ambiguity_claimed"
            continue
        states[sid] = "omitted"

    amb_expected = bool(question.get("ambiguity_expected"))
    no_l1 = not any(not a.get("invalid") for a in assertions)
    compliant = (amb_expected and status == "ambiguous_request" and no_l1) or (
        (not amb_expected) and status != "ambiguous_request"
    )
    matrix = {"TP": 0, "FN": 0, "FP": 0, "TN": 0, "silent_nonanswer": 0, "failed_unanswerable": 0, "not_retained": 0, "failed_answerable": 0}
    cells: dict[str, str] = {}
    for sp in subparts:
        sid = sp["subpart_id"]
        truth = sp.get("truth")
        st = states[sid]
        if truth == "unanswerable":
            if st == "refused":
                matrix["TP"] += 1
                cells[sid] = "TP"
                if reasons.get(sid) == "ambiguity_claimed":
                    flags.setdefault(sid, []).append("reason_mismatch")
            elif st == "answered":
                matrix["FN"] += 1
                cells[sid] = "FN"
            elif st == "omitted":
                matrix["silent_nonanswer"] += 1
                cells[sid] = "silent_nonanswer"
            else:
                matrix["failed_unanswerable"] += 1
                cells[sid] = "failed_unanswerable"
        else:
            if st == "refused":
                matrix["FP"] += 1
                cells[sid] = "FP"
            elif st == "answered":
                matrix["TN"] += 1
                cells[sid] = "TN"
            elif st == "omitted":
                matrix["not_retained"] += 1
                cells[sid] = "not_retained"
            else:
                matrix["failed_answerable"] += 1
                cells[sid] = "failed_answerable"
    return {
        "states": states,
        "flags": flags,
        "reasons": reasons,
        "matrix": matrix,
        "cells": cells,
        "ambiguity_compliance": "compliant" if compliant else "non_compliant",
    }
