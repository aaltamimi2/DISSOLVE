"""§5.2 matching: max cardinality, lex pair list."""

from __future__ import annotations

from typing import Any, Callable

from answer_eval.canon import fold_ws
from answer_eval.equal import equal


def field_identity(claim_type: str, field_path: str) -> tuple[str, str]:
    row = claim_type
    from answer_eval.mutation import get as mut

    if claim_type == "Q4" and mut() != "unprojected_q4":
        row = "Q2"
    path = field_path
    if claim_type == "Q1":
        if field_path.endswith("step_identity") or field_path == "step_identity":
            path = "step_identity"
        elif field_path.endswith("conditions.T") or field_path.endswith("/T") or field_path == "conditions.T":
            path = "conditions.T"
        elif "outcomes" in field_path or field_path == "outcome.value":
            path = "outcome.value"
        elif "source_type" in field_path:
            path = "feedstock.source_type"
        elif "pre_treatment" in field_path:
            path = "feedstock.pre_treatment"
        elif field_path.endswith("role") or field_path == "materials.role":
            path = "materials.role"
        elif field_path.startswith("conditions.") or "descriptive_conditions" in field_path:
            path = field_path.split("/")[-1] if "/" in field_path else field_path
    return (row, path)


def _material_mention(atom: dict[str, Any]) -> str | None:
    if atom.get("material_mention") is not None:
        return fold_ws(str(atom["material_mention"]))
    if atom.get("material_ref") is not None:
        return fold_ws(str(atom["material_ref"]))
    return None


def material_equal(g: dict[str, Any], p: dict[str, Any]) -> bool:
    rg, rp = g.get("material_registry_id"), p.get("material_registry_id")
    if rg and rp:
        return rg == rp
    mg, mp = _material_mention(g), _material_mention(p)
    if mg is None and mp is None:
        return True
    if mg is None or mp is None:
        return False
    return mg == mp


def is_unknown_identity(value: Any) -> bool:
    if isinstance(value, dict) and value.get("unknown"):
        return True
    if isinstance(value, str) and fold_ws(value) == "unknown":
        return True
    return False


def _q1_aligned(g: dict[str, Any], p: dict[str, Any]) -> bool:
    gc = g.get("container") or ""
    if not str(gc).startswith("Q1") and g.get("claim_type") != "Q1":
        return True
    gexp = (g.get("comparison_key") or {}).get("experiment") or g.get("feedstock_description")
    pfs = ((p.get("claim") or {}).get("feedstock") or {})
    pdesc = pfs.get("description")
    if gexp and pdesc and fold_ws(str(gexp)) != fold_ws(str(pdesc)):
        return False
    gstep = (g.get("comparison_key") or {}).get("step")
    pstep = p.get("step") or {}
    if gstep and pstep:
        want = str(gstep)
        got = f"{pstep.get('order')}:{pstep.get('kind')}"
        if want != got and want != f"{pstep.get('kind')}":
            if ":" in want:
                o, k = want.split(":", 1)
                if str(pstep.get("order")) != o or str(pstep.get("kind")) != k:
                    return False
    return True


def eligible(g: dict[str, Any], p: dict[str, Any], run: str, ignore_unknown_identity: bool = False) -> bool:
    if p.get("invalid") or p.get("pre_duplicate"):
        return False
    if p.get("kind") == "proposition" or g.get("kind") == "proposition":
        return False
    if p.get("kind") == "descriptive" or g.get("kind") == "descriptive_binding":
        return False
    gt = g.get("claim_type") or "Q2"
    pt = p.get("claim_type") or "Q2"
    if gt != pt:
        if not (pt == "Q4" and gt == "Q2") and not (pt == "Q2" and gt == "Q4"):
            return False
    if field_identity(gt, g.get("field_path", "")) != field_identity(pt, p.get("field_path", "")):
        return False
    if not material_equal(g, p):
        return False
    if (g.get("quantity") or g.get("property")) != (p.get("quantity") or p.get("property")):
        if g.get("field_path") in {"step_identity", "feedstock.source_type", "feedstock.pre_treatment", "materials.role"}:
            pass
        elif g.get("claim_type") == "Q1":
            pass
        else:
            return False
    if g.get("basis") is not None and p.get("basis") is not None and g.get("basis") != p.get("basis"):
        if g.get("claim_type") == "Q1":
            pass
        else:
            return False
    g_ident = g.get("identity_conditions") or (g.get("comparison_key") or {}).get("identity_conditions") or {}
    p_ident = p.get("identity_conditions") or {}
    for k, val in (g_ident or {}).items():
        pv = (p_ident or {}).get(k)
        if pv is None:
            step = p.get("step") or {}
            pv = (step.get("identity_conditions") or {}).get(k)
        if ignore_unknown_identity and is_unknown_identity(pv):
            continue
        if pv is None:
            return False
        if isinstance(val, dict) or isinstance(pv, dict):
            if not equal(val, pv, run):
                return False
        elif str(val) != str(pv) and not equal({"shape": "scalar", "reported": str(val), "dimension_status": "dimensionless"}, {"shape": "scalar", "reported": str(pv), "dimension_status": "dimensionless"}, run):
            return False
    if gt == "Q4" or pt == "Q4":
        if g.get("study_family_id") and p.get("study_family_id") and g["study_family_id"] != p["study_family_id"]:
            if gt == "Q4" or pt == "Q4":
                if (g.get("claim_type") == "Q4" or p.get("claim_type") == "Q4") and g.get("study_family_id") != p.get("study_family_id"):
                    return False
    if not _q1_aligned(g, p):
        return False
    return True


def _value_equal(g: dict[str, Any], p: dict[str, Any], run: str) -> bool:
    gv, pv = g.get("value"), p.get("value")
    if g.get("applicability", "").startswith("unknown") or (isinstance(gv, dict) and gv.get("unknown")):
        return _is_unknown_decl(pv)
    return equal(gv, pv, run)


def _is_unknown_decl(value: Any) -> bool:
    if isinstance(value, dict) and (value.get("unknown") is True or "unknown" in value and value.get("unknown")):
        return True
    return False


def full_correct(g: dict[str, Any], p: dict[str, Any], run: str) -> bool:
    return eligible(g, p, run) and _value_equal(g, p, run)


def max_card_lex_matching(gs: list[dict[str, Any]], ps: list[dict[str, Any]], run: str) -> list[tuple[str, str]]:
    g_ids = [g["atom_id"] for g in gs]
    p_ids = [p["slot_id"] for p in ps]
    g_by = {g["atom_id"]: g for g in gs}
    p_by = {p["slot_id"]: p for p in ps}

    def is_edge(gid, pid):
        return full_correct(g_by[gid], p_by[pid], run)

    best = None
    max_size = -1

    def rec(gi: int, used: set[int], pairs: list[tuple[str, str]]) -> None:
        nonlocal best, max_size
        if gi == len(g_ids):
            if len(pairs) > max_size:
                max_size = len(pairs)
                best = list(pairs)
            elif len(pairs) == max_size and pairs:
                sp = sorted(pairs)
                if best is None or sp < sorted(best):
                    best = list(pairs)
            elif len(pairs) == max_size and max_size == 0:
                best = []
            return
        rec(gi + 1, used, pairs)
        gid = g_ids[gi]
        for pj, pid in enumerate(p_ids):
            if pj in used:
                continue
            if is_edge(gid, pid):
                pairs.append((gid, pid))
                used.add(pj)
                rec(gi + 1, used, pairs)
                used.remove(pj)
                pairs.pop()

    rec(0, set(), [])
    return [[a, s] for a, s in (best or [])]


def first_fit_matching(gs: list[dict[str, Any]], ps: list[dict[str, Any]], run: str) -> list[tuple[str, str]]:
    ordered_g = sorted(gs, key=lambda g: g["atom_id"])
    used = set()
    pairs = []
    for g in ordered_g:
        candidates = sorted(
            [p for p in ps if p["slot_id"] not in used and full_correct(g, p, run)],
            key=lambda p: p["slot_id"],
        )
        if candidates:
            pairs.append((g["atom_id"], candidates[0]["slot_id"]))
            used.add(candidates[0]["slot_id"])
    return pairs


def greedy_card(gs, ps, run) -> int:
    """Greedy matching on lexicographically largest remaining eligible edge (counterexample vs max-card lex-smallest)."""
    edges = sorted(
        ((g["atom_id"], p["slot_id"]) for g in gs for p in ps if full_correct(g, p, run)),
        reverse=True,
    )
    used_g: set[str] = set()
    used_p: set[str] = set()
    n = 0
    for gid, pid in edges:
        if gid in used_g or pid in used_p:
            continue
        used_g.add(gid)
        used_p.add(pid)
        n += 1
    return n


def match(reference_atoms: list[dict[str, Any]], prediction_atoms: list[dict[str, Any]], run: str = "exact_reported") -> dict[str, Any]:
    g_m1 = [
        g
        for g in reference_atoms
        if g.get("kind") != "descriptive_binding" and g.get("applicability") != "not_applicable"
    ]
    p_graph = [
        p
        for p in prediction_atoms
        if not p.get("invalid")
        and not p.get("pre_duplicate")
        and p.get("kind") == "m1"
        and p.get("claim_type") != "Q3"
    ]
    pairing = max_card_lex_matching(g_m1, p_graph, run)
    matched_g = {a for a, _ in pairing}
    matched_p = {s for _, s in pairing}
    adjacency = []
    for g in g_m1:
        for p in p_graph:
            if full_correct(g, p, run):
                adjacency.append([g["atom_id"], p["slot_id"]])
    adjacency.sort()
    return {
        "pairing": sorted(pairing),
        "matched": len(pairing),
        "adjacency": adjacency,
        "matched_g": matched_g,
        "matched_p": matched_p,
        "P_graph": len(p_graph),
        "first_fit": first_fit_matching(g_m1, p_graph, run),
        "greedy_counterexample_cardinality": greedy_card(g_m1, p_graph, run),
        "atom_id_order": sorted(g["atom_id"] for g in g_m1),
    }
