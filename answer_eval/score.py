"""§5.3.6 scoring over a question (or set)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from answer_eval.atomize import atomize
from answer_eval.canon import canonical_dumps, fold_ws
from answer_eval.equal import equal
from answer_eval.errors import EvalHalt
from answer_eval.match import field_identity, match, material_equal
from answer_eval.reference import load_reference
from answer_eval.states import subpart_states
from answer_eval.support import PRECEDENCE, classify_support

M7_CLASSES = {
    "supported_not_visible",
    "misattributed",
    "contradicted",
    "neutral_resolving",
    "unresolvable_locator",
    "unsupported_no_evidence",
    "invalid",
}

OUT_OF_SCOPE_WP1 = {"M-11", "M-12", "weighted_M-4", "latency", "cost"}


def _in_scope(atom: dict[str, Any], question: dict[str, Any]) -> bool:
    if atom.get("out_of_scope"):
        return False
    mats, qtys = set(), set()
    for sp in question.get("subparts") or []:
        sc = sp.get("resolved_scope") or {}
        mats.update(sc.get("materials") or [])
        qtys.update(sc.get("quantities") or [])
        mats.update(sc.get("families") or [])
    mat = atom.get("material_ref") or atom.get("material_mention")
    qty = atom.get("quantity")
    if atom.get("claim_type") == "Q1":
        return True
    if atom.get("kind") == "proposition":
        return True
    if mat and mats and mat not in mats:
        return False
    if qty and qtys and qty not in qtys:
        return False
    return True


def _parent_key(atom: dict[str, Any]) -> tuple:
    return (
        atom.get("claim_index"),
        atom.get("claim_type"),
        atom.get("study_family_id"),
        atom.get("material_ref"),
        atom.get("quantity"),
    )


def _g_parent_key(g: dict[str, Any]) -> tuple:
    ck = g.get("comparison_key") or {}
    return (g.get("observation_id"), g.get("claim_type"), g.get("study_family_id"))


def score_question(
    question: dict[str, Any],
    reference: dict[str, Any],
    prediction: dict[str, Any],
    run: str = "exact_reported",
    arm: str | None = None,
    projection: str | None = None,
    id_hex_width: int = 16,
) -> dict[str, Any]:
    width = int(prediction.get("id_hex_width") or id_hex_width)
    loaded = load_reference(reference, question=question, id_hex_width=width)
    obs = loaded["observations"]
    g_atoms = []
    for o in obs:
        for atom in o.get("atoms") or []:
            ga = dict(atom)
            ga["observation_id"] = o.get("observation_id_computed") or o.get("observation_id")
            ga["atom_id"] = atom.get("atom_id_computed") or atom.get("atom_id")
            ga["study_family_id"] = o.get("study_family_id")
            ga["source_role"] = o.get("source_role")
            ga["comparison_key"] = o.get("comparison_key") or {}
            ga["identity_conditions"] = (o.get("comparison_key") or {}).get("identity_conditions") or o.get("identity_conditions") or {}
            ga["claim_type"] = o.get("claim_type")
            ga["container"] = o.get("container")
            ga["material_ref"] = (o.get("comparison_key") or {}).get("material_ref") or o.get("material_ref")
            ga["material_mention"] = o.get("material_mention")
            ga["material_registry_id"] = o.get("material_registry_id")
            ga["quantity"] = (o.get("comparison_key") or {}).get("quantity") or o.get("quantity")
            ga["basis"] = (o.get("comparison_key") or {}).get("basis") or o.get("basis")
            ga["question_id"] = question.get("question_id")
            g_atoms.append(ga)

    tables = {"question_id": question.get("question_id"), "question": question, "id_hex_width": width}
    pred = dict(prediction)
    if projection:
        pred["projection"] = projection
    az = atomize(pred, tables, projection or pred.get("projection"))
    assertions = az["assertions"]
    from answer_eval.errors import CanonicalizationCollision, IdCollision
    from answer_eval.canon import canonical_dumps as cd
    from answer_eval.ids import assert_unique_ids

    id_pairs = [(a["slot_id"], a.get("envelope_path") or "", a.get("envelope_path")) for a in assertions]
    try:
        assert_unique_ids(id_pairs)
    except IdCollision as exc:
        exc.payload["id_hex_width"] = width
        raise

    if arm == "evidence_off" or pred.get("source_basis") == "model_knowledge":
        for a in assertions:
            for ref in a.get("evidence_refs") or []:
                if ref.get("visible") is True:
                    from answer_eval.errors import StubVisibilityConflict
                    from answer_eval.mutation import get as mut

                    if mut() != "skip_stub_visible":
                        raise StubVisibilityConflict("off-arm stub visible")
        for a in assertions:
            if a.get("evidence_refs"):
                a["citation_without_access"] = True

    classified = classify_support(assertions)
    p_m1 = [p for p in classified if not p.get("invalid") and p.get("kind") == "m1" and p.get("claim_type") != "Q3"]
    g_m1 = [g for g in g_atoms if g.get("kind") != "descriptive_binding" and g.get("applicability") != "not_applicable"]
    matched = match(g_atoms, classified, run)
    matched_g, matched_p = matched["matched_g"], matched["matched_p"]

    parents_matched = set()
    for g in g_m1:
        if g["atom_id"] in matched_g:
            parents_matched.add(g.get("observation_id"))

    truth: dict[str, str] = {}
    p_by_slot = {p["slot_id"]: p for p in classified}
    for p in classified:
        path = p["envelope_path"]
        if p.get("invalid"):
            truth[path] = "invalid"
            continue
        if p.get("kind") == "proposition":
            truth[path] = "rubric_only"
            continue
        if p.get("pre_duplicate"):
            truth[path] = "duplicate"
            continue
        if p["slot_id"] in matched_p:
            truth[path] = "matched"
            continue
        if p.get("kind") == "descriptive":
            parent = p.get("claim")
            parent_matched = False
            for q in classified:
                if q.get("claim_index") == p.get("claim_index") and q.get("kind") == "m1" and q["slot_id"] in matched_p:
                    parent_matched = True
            g_expected = None
            for g in g_atoms:
                if g.get("kind") == "descriptive_binding" and g.get("field_path") in {p.get("field_path"), f"conditions.{p.get('field_path')}", p.get("field_path")}:
                    if g.get("observation_id") in parents_matched or parent_matched:
                        g_expected = g
                        break
            if parent_matched and g_expected:
                truth[path] = "binding_correct" if equal(g_expected.get("value"), p.get("value"), run) else "binding_incorrect"
            elif parent_matched:
                truth[path] = "binding_extra"
            else:
                truth[path] = "binding_unanchored"
            continue
        if not _in_scope(p, question):
            truth[path] = "out_of_scope"
            continue
        under = False
        for g in g_m1:
            if g["atom_id"] in matched_g:
                continue
            from answer_eval.match import eligible, full_correct, is_unknown_identity

            if not any(is_unknown_identity(v) for v in (p.get("identity_conditions") or {}).values()):
                continue
            if eligible(g, p, run, ignore_unknown_identity=True) and equal(g.get("value"), p.get("value"), run):
                under = True
                break
        if under:
            truth[path] = "underbound"
            continue
        matched_same = None
        for q in classified:
            if q["slot_id"] in matched_p and q.get("claim_index") != p.get("claim_index"):
                if (
                    q.get("material_ref") == p.get("material_ref")
                    and q.get("quantity") == p.get("quantity")
                    and q.get("study_family_id") == p.get("study_family_id")
                    and equal(q.get("value"), p.get("value"), run)
                ):
                    q_regs = {(r.get("region"), r.get("family")) for r in (q.get("evidence_refs") or []) if r.get("resolves")}
                    p_regs = {(r.get("region"), r.get("family")) for r in (p.get("evidence_refs") or []) if r.get("resolves")}
                    if not p_regs or p_regs <= q_regs:
                        truth[path] = "duplicate"
                        matched_same = q
                        break
                    truth[path] = "additional_candidate"
                    matched_same = q
                    break
        if path not in truth:
            truth[path] = "additional_candidate"

    for p in classified:
        p["truth_class"] = truth.get(p["envelope_path"])
        p["in_scope"] = _in_scope(p, question)

    tickets = 0
    adj_add = 0
    for p in classified:
        if p.get("truth_class") == "additional_candidate" and p.get("support_class") == "supported_visible" and p.get("in_scope"):
            p["adjudicated_supported_additional"] = True
            adj_add += 1
            tickets += 1

    states = subpart_states(question, az.get("envelope") or prediction, classified)

    expected_g = [g for g in g_atoms if g.get("kind") != "descriptive_binding" and g.get("applicability") != "not_applicable"]
    unknown_g = [g for g in expected_g if str(g.get("applicability", "")).startswith("unknown")]
    m1_num = 0
    reason_agree = 0
    for g in expected_g:
        if g["atom_id"] in matched_g:
            m1_num += 1
            if str(g.get("applicability", "")).startswith("unknown"):
                pass
    reason_den = 0
    for g in expected_g:
        if str(g.get("applicability", "")).startswith("unknown"):
            reason_den += 1
            partner = None
            for a, s in matched["pairing"]:
                if a == g["atom_id"]:
                    partner = p_by_slot.get(s)
            if partner:
                gv = g.get("value") if isinstance(g.get("value"), dict) else {}
                pv = partner.get("value") if isinstance(partner.get("value"), dict) else {}
                gr = (gv or {}).get("reason") or str(g.get("applicability"))
                pr = (pv or {}).get("reason")
                if gr and pr and gr == pr:
                    reason_agree += 1

    R = az["L0"]
    L1 = az["L1"]
    cpr_num = 0
    for p in classified:
        sc = p.get("support_class")
        tc = p.get("truth_class")
        if p.get("pre_duplicate") or tc == "duplicate":
            continue
        if tc == "matched" and sc == "supported_visible" and p.get("in_scope"):
            cpr_num += 1
        elif p.get("adjudicated_supported_additional"):
            cpr_num += 1
        elif tc == "binding_correct" and sc == "supported_visible":
            cpr_num += 1
        elif tc == "rubric_only" and sc == "supported_visible":
            cpr_num += 1

    m7_num = 0
    subtypes: Counter[str] = Counter()
    for p in classified:
        if p.get("pre_duplicate") or p.get("truth_class") == "duplicate":
            continue
        sc = p.get("support_class")
        if sc in M7_CLASSES:
            m7_num += 1
            subtypes[sc] += 1

    desc_g = [g for g in g_atoms if g.get("kind") == "descriptive_binding" and g.get("applicability") == "expected"]
    m4_num = m4_den = 0
    for g in desc_g:
        m4_den += 1
        parent_ok = g.get("observation_id") in parents_matched
        if not parent_ok:
            continue
        found = False
        for p in classified:
            if p.get("kind") == "descriptive" and p.get("truth_class") == "binding_correct":
                if p.get("field_path") in {g.get("field_path"), g.get("field_path").replace("conditions.", "")}:
                    m4_num += 1
                    found = True
                    break
        _ = found

    cited_l1 = [p for p in classified if not p.get("invalid") and (p.get("evidence_refs") or p.get("field_evidence"))]
    m6_num = len(cited_l1)
    m6_den = sum(1 for p in classified if not p.get("invalid"))
    m5_num = sum(1 for p in cited_l1 if p.get("support_class") in {"supported_visible", "supported_not_visible"})
    m5_den = len(cited_l1)

    cr_sup = 0
    for g in expected_g:
        if g["atom_id"] not in matched_g:
            continue
        slot = None
        for a, s in matched["pairing"]:
            if a == g["atom_id"]:
                slot = s
        p = p_by_slot.get(slot)
        if p and p.get("support_class") == "supported_visible":
            cr_sup += 1

    unans = [s for s in (question.get("subparts") or []) if s.get("truth") == "unanswerable"]
    ans = [s for s in (question.get("subparts") or []) if s.get("truth") == "answerable"]
    m8_num = states["matrix"]["TP"]
    m8_den = len(unans)
    m9_num = states["matrix"]["TP"]
    m9_den = states["matrix"]["TP"] + states["matrix"]["FP"]
    ret_num = sum(1 for s in ans if states["states"].get(s["subpart_id"]) == "answered")
    ret_den = len(ans)

    attempted_failure = bool(states["states"]) and all(
        st == "operational_failure" for st in states["states"].values()
    )
    partial_fault = (
        any(st == "operational_failure" for st in states["states"].values())
        and not attempted_failure
        and any(st != "operational_failure" for st in states["states"].values())
    )

    e2e = True
    if not ans and unans and not ans:
        e2e_applicable = False
    else:
        e2e_applicable = bool(ans)
    if e2e_applicable:
        for sp in ans:
            sid = sp["subpart_id"]
            g_for = [g for g in expected_g]
            all_matched_sup = True
            for g in expected_g:
                if g["atom_id"] not in matched_g:
                    all_matched_sup = False
                    break
                slot = next((s for a, s in matched["pairing"] if a == g["atom_id"]), None)
                p = p_by_slot.get(slot)
                if not p or p.get("support_class") != "supported_visible":
                    all_matched_sup = False
                    break
            if not all_matched_sup:
                e2e = False
        for sp in unans:
            if states["states"].get(sp["subpart_id"]) != "refused":
                e2e = False
        if any(st == "omitted" for st in states["states"].values()):
            e2e = False
        if m7_num:
            e2e = False
        if attempted_failure:
            e2e = False
    else:
        e2e = False

    m3t = True
    m3t_applicable = bool(ans)
    if m3t_applicable:
        for g in expected_g:
            if g["atom_id"] not in matched_g:
                m3t = False
        for sp in unans:
            if states["states"].get(sp["subpart_id"]) != "refused":
                m3t = False
        if any(st in {"omitted", "operational_failure"} for st in states["states"].values()):
            m3t = False
        if attempted_failure:
            m3t = False
    else:
        m3t = False

    extra_correct = sum(
        1
        for p in classified
        if p.get("truth_class") == "out_of_scope" and p.get("support_class") == "supported_visible"
    )

    def ratio(num, den, empty="UNDEF"):
        if den == 0:
            return empty if empty == "UNDEF" else {"status": empty}
        return {"num": num, "den": den}

    m1 = ratio(m1_num, len(expected_g), "NA" if not expected_g else "UNDEF")
    if not expected_g:
        m1 = "NA"

    m2 = ratio(cpr_num, R) if R else "UNDEF"
    m7 = ratio(m7_num, R) if R else "UNDEF"
    m4 = ratio(m4_num, m4_den, "NA") if m4_den else "NA"
    m5 = ratio(m5_num, m5_den) if m5_den else "UNDEF"
    m6 = ratio(m6_num, m6_den) if m6_den else "UNDEF"
    m8 = ratio(m8_num, m8_den, "NA") if m8_den else "NA"
    m9 = ratio(m9_num, m9_den) if m9_den else "UNDEF"
    retention = ratio(ret_num, ret_den, "NA") if ret_den else "NA"
    m3 = ratio(int(e2e), 1, "NA") if e2e_applicable else "NA"
    m3t_m = ratio(int(m3t), 1, "NA") if m3t_applicable else "NA"

    add_counts = Counter(p.get("truth_class") for p in classified if p.get("truth_class") in {"additional_candidate", "out_of_scope", "underbound", "duplicate"} or p.get("pre_duplicate"))
    if any(p.get("pre_duplicate") for p in classified):
        add_counts["duplicate"] = sum(1 for p in classified if p.get("pre_duplicate") or p.get("truth_class") == "duplicate")

    flags = {}
    if arm == "evidence_off":
        flags["consequence_of_intervention"] = ["M-3", "M-2", "M-7"]
        if any(p.get("citation_without_access") for p in classified):
            flags["citation_without_access"] = True
    mixed = sum(1 for p in classified if p.get("mixed_evidence"))
    if mixed:
        flags["mixed_evidence"] = mixed

    support_classes = {p["envelope_path"]: p.get("support_class") for p in classified}
    truth_classes = {p["envelope_path"]: p.get("truth_class") for p in classified}
    slot_ids = {p["envelope_path"]: p["slot_id"] for p in classified}

    return {
        "az": az,
        "loaded": loaded,
        "g_atoms": g_atoms,
        "assertions": classified,
        "match": matched,
        "states": states,
        "L0": az["L0"],
        "L1": az["L1"],
        "P_graph": az["P_graph"],
        "matched": matched["matched"],
        "pairing": matched["pairing"],
        "adjacency": matched["adjacency"],
        "slot_ids": slot_ids,
        "truth_classes": truth_classes,
        "support_classes": support_classes,
        "duplicates": sum(1 for p in classified if p.get("pre_duplicate") or p.get("truth_class") == "duplicate"),
        "adjudicated_supported_additional": adj_add,
        "revision_tickets": tickets,
        "M-1": m1,
        "M-2": m2,
        "M-3": m3,
        "M-3t": m3t_m,
        "M-3t_additional": {
            "additional_candidate": sum(1 for p in classified if p.get("truth_class") == "additional_candidate"),
            "out_of_scope": sum(1 for p in classified if p.get("truth_class") == "out_of_scope"),
            "underbound": sum(1 for p in classified if p.get("truth_class") == "underbound"),
            "duplicate": sum(1 for p in classified if p.get("pre_duplicate") or p.get("truth_class") == "duplicate"),
        },
        "M-4": m4,
        "M-5": m5,
        "M-6": m6,
        "M-7": m7,
        "M-7_subtypes": dict(subtypes),
        "M-8": m8,
        "M-9_RP": m9,
        "retention": retention,
        "CR_supported": ratio(cr_sup, len(expected_g), "NA") if expected_g else "NA",
        "unknown_reason_agreement": ratio(reason_agree, reason_den, "NA") if reason_den else None,
        "extra_correct_out_of_scope": extra_correct,
        "operational_failure": attempted_failure,
        "partial_fault_questions": int(partial_fault),
        "flags": flags,
        "parent_matched": bool(parents_matched),
        "identity_info": loaded.get("identity_info"),
        "status_internal": {},
    }


def score(question_set: Any, arm: str | None = None) -> dict[str, Any]:
    if isinstance(question_set, dict) and "questions" in question_set:
        questions = question_set["questions"]
    elif isinstance(question_set, list):
        questions = question_set
    else:
        questions = [question_set]
    results = []
    for q in questions:
        results.append(
            score_question(
                q["question"],
                q["reference"],
                q["prediction"],
                q.get("run") or "exact_reported",
                arm=arm or q.get("arm"),
            )
        )
    return {"questions": results}
