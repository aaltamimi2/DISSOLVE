"""§3.2.0(d) claim and collection total order."""

from __future__ import annotations

from typing import Any

from answer_eval.canon import canonical_dumps, fold_ws, nfkc

CLAIM_RANK = {"Q1": 0, "Q2": 1, "Q3": 2, "Q4": 3, "Q5": 4}


def _target_materials(claim: dict[str, Any]) -> list[str]:
    mats = claim.get("materials") or []
    refs = []
    for m in mats:
        if isinstance(m, dict):
            refs.append(nfkc(str(m.get("material_ref", ""))))
        else:
            refs.append(nfkc(str(m)))
    return sorted(refs)


def collection_key(claim: dict[str, Any]) -> tuple:
    ctype = claim.get("claim_type", "Q2")
    if ctype == "Q1":
        desc = fold_ws(str((claim.get("feedstock") or {}).get("description", "")))
        return (CLAIM_RANK[ctype], desc, tuple(_target_materials(claim)))
    if ctype == "Q2":
        ident = canonical_dumps(claim.get("identity_conditions") or {})
        return (
            CLAIM_RANK[ctype],
            nfkc(str(claim.get("material_ref", ""))),
            nfkc(str(claim.get("quantity") or claim.get("property", ""))),
            nfkc(str(claim.get("basis", ""))),
            ident,
        )
    if ctype == "Q3":
        return (CLAIM_RANK[ctype], fold_ws(str(claim.get("proposition", ""))))
    if ctype == "Q4":
        ck = claim.get("comparison_key") or {
            "material_ref": claim.get("material_ref"),
            "quantity": claim.get("quantity"),
            "basis": claim.get("basis"),
            "identity_conditions": claim.get("identity_conditions") or {},
        }
        return (
            CLAIM_RANK[ctype],
            canonical_dumps(ck),
            nfkc(str(claim.get("study_family_id", ""))),
            int(claim.get("occurrence_index") or 0),
        )
    return (CLAIM_RANK.get(ctype, 9),)


def _sort_children(claim: dict[str, Any]) -> dict[str, Any]:
    out = dict(claim)
    if isinstance(out.get("materials"), list):
        out["materials"] = sorted(
            out["materials"],
            key=lambda m: (
                nfkc(str(m.get("material_ref", "") if isinstance(m, dict) else m)),
                canonical_dumps(m),
            ),
        )
    if isinstance(out.get("steps"), list) and out["steps"] and isinstance(out["steps"][0], dict):
        out["steps"] = sorted(
            out["steps"],
            key=lambda s: (
                int(s.get("order") or 0),
                nfkc(str(s.get("kind", ""))),
                canonical_dumps(s),
            ),
        )
    if isinstance(out.get("outcomes"), list):
        out["outcomes"] = sorted(
            out["outcomes"],
            key=lambda o: (
                nfkc(str(o.get("property", "") if isinstance(o, dict) else "")),
                nfkc(str(o.get("basis", "") if isinstance(o, dict) else "")),
                nfkc(str(o.get("specimen_state", "") if isinstance(o, dict) else "")),
                canonical_dumps(o),
            ),
        )
    return out


def order_claims(envelope: dict[str, Any]) -> dict[str, Any]:
    env = dict(envelope)
    claims = list(env.get("claims") or [])
    ordered = []
    for claim in claims:
        ordered.append(_sort_children(claim))
    ordered.sort(key=lambda c: (collection_key(c), canonical_dumps(c)))
    env["claims"] = ordered
    return env
