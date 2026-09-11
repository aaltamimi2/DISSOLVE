"""§5.3.2 support classification from author stubs."""

from __future__ import annotations

from typing import Any

PRECEDENCE = [
    "supported_visible",
    "supported_not_visible",
    "misattributed",
    "contradicted",
    "neutral_resolving",
    "unresolvable_locator",
    "unsupported_no_evidence",
]


def _class_for_ref(ref: dict[str, Any], family: str | None, role: str | None) -> str:
    if not ref.get("resolves"):
        return "unresolvable_locator"
    supports = ref.get("supports")
    fam_ok = (family is None or ref.get("family") == family)
    role_ok = (role is None or ref.get("role") == role)
    if supports is True:
        if fam_ok and role_ok:
            return "supported_visible" if ref.get("visible") else "supported_not_visible"
        return "misattributed"
    if supports is False:
        return "contradicted"
    return "neutral_resolving"


def classify_one(assertion: dict[str, Any], stubs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    refs = stubs if stubs is not None else assertion.get("field_evidence") or assertion.get("evidence_refs") or []
    if assertion.get("field_evidence"):
        fe = assertion["field_evidence"]
        if isinstance(fe, list) and fe and not isinstance(fe[0], dict):
            all_refs = assertion.get("evidence_refs") or []
            refs = [all_refs[i] if isinstance(i, int) and i < len(all_refs) else {"resolves": False} for i in fe]
        elif isinstance(fe, list):
            refs = fe
    if assertion.get("invalid"):
        return {"support_class": "invalid", "mixed_evidence": False}
    if not refs:
        return {"support_class": "unsupported_no_evidence", "mixed_evidence": False}
    classes = [_class_for_ref(r, assertion.get("study_family_id"), assertion.get("attributed_source_role")) for r in refs]
    has_support = any(c in {"supported_visible", "supported_not_visible"} for c in classes)
    has_contra = any(c == "contradicted" for c in classes)
    mixed = has_support and has_contra
    chosen = None
    for name in PRECEDENCE:
        if name in classes:
            chosen = name
            break
    if chosen is None:
        chosen = "unsupported_no_evidence"
    return {"support_class": chosen, "mixed_evidence": mixed}


def classify_support(assertions: list[dict[str, Any]], stubs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    out = []
    for a in assertions:
        info = classify_one(a, stubs)
        a = dict(a)
        a["support_class"] = info["support_class"]
        a["mixed_evidence"] = info["mixed_evidence"]
        out.append(a)
    return out
