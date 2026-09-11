"""§5.3.0 atomization for envelopes and free text."""

from __future__ import annotations

import re
from typing import Any

from answer_eval.canon import canonical_dumps, fold_ws, json_pointer, nfkc
from answer_eval.equal import load_conversions, label_key
from answer_eval.ids import PRODUCTION_HEX_WIDTH, slot_id
from answer_eval.order import order_claims
from answer_eval.tables import load_json

REQUIRED_LEAVES = {
    "Q2": ["value"],
    "Q1_step": ["conditions.T", "conditions.t"],
    "Q1_outcome": ["value"],
    "Q1_experiment": ["feedstock.source_type"],
    "Q3": ["proposition"],
}

Q2_M1 = {"value", "uncertainty", "method", "obtained_by", "attributed_as"}
Q1_EXP_M1 = {"source_type", "pre_treatment"}
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
QUANTITY_RE = None


def _unit_pattern() -> str:
    conv = load_json("fixtures/CONVERSIONS.v2.json")
    aliases = sorted(conv["aliases"].keys(), key=len, reverse=True)
    escaped = [re.escape(a) for a in aliases]
    escaped.append(re.escape("%"))
    return "(?:" + "|".join(escaped) + ")"


def quantity_regex() -> re.Pattern:
    global QUANTITY_RE
    if QUANTITY_RE is None:
        unit = _unit_pattern()
        QUANTITY_RE = re.compile(
            rf"(?P<number>{NUMBER})(?:\s*(?:to|–|-)\s*(?:{NUMBER}))?(?:\s*(?P<unit>{unit}))?",
            re.I,
        )
    return QUANTITY_RE


def _phrases() -> list[str]:
    return [p.casefold() for p in load_json("fixtures/REFUSAL_PHRASES.v1.json")["phrases"]]


def _qaliases() -> dict[str, str]:
    return {k.casefold(): v for k, v in load_json("fixtures/QUANTITY_ALIASES.v1.json")["aliases"].items()}


def _is_invalid_leaf(value: Any) -> bool:
    if not isinstance(value, dict):
        return True
    if value.get("unknown") is True:
        return False
    shape = value.get("shape")
    if value.get("dimension_status") == "dimensional" and not value.get("unit_reported") and "reported" in value:
        if shape in (None, "scalar"):
            return True
    return False


def _leaf_kind(claim_type: str, field_path: str, projection: str, promoted: bool) -> str:
    if claim_type == "Q3":
        return "proposition"
    if claim_type in {"Q2", "Q4"}:
        if field_path in Q2_M1 or field_path.endswith("value"):
            return "m1"
        return "descriptive"
    if claim_type == "Q1":
        if field_path == "step_identity" or field_path.endswith("step_identity"):
            return "m1"
        if field_path in {"feedstock.source_type", "feedstock.pre_treatment"} or field_path.endswith("/role") or field_path.endswith("role"):
            return "m1"
        if field_path.endswith("conditions.T") or field_path.endswith("/T") or field_path == "conditions.T":
            return "descriptive" if promoted else "m1"
        if "outcomes" in field_path and field_path.endswith("value"):
            return "m1"
        return "descriptive"
    return "m1"


def atomize(prediction: dict[str, Any], tables: dict[str, Any] | None = None, projection: str | None = None) -> dict[str, Any]:
    kind = prediction.get("kind")
    if kind == "free_text":
        return atomize_free_text(prediction, tables, projection)
    if kind in {"none", None} and prediction.get("status") in {None, "operational_failure"}:
        return {"assertions": [], "L0": 0, "L1": 0, "P_graph": 0, "operational_failure": True, "envelope": prediction}
    env = order_claims(prediction)
    from answer_eval.mutation import get as mut

    if mut() == "skip_order":
        env = prediction
    proj = projection or env.get("projection") or "entire_envelope"
    qid = (tables or {}).get("question_id") or env.get("question_id") or ""
    width = int((tables or {}).get("id_hex_width") or PRODUCTION_HEX_WIDTH)
    assertions = []
    canon_claims: dict[str, int] = {}
    claims = env.get("claims") or []
    for i, claim in enumerate(claims):
        ctype = claim.get("claim_type", "Q2")
        role_defaulted = False
        if "attributed_source_role" not in claim:
            claim = dict(claim)
            claim["attributed_source_role"] = "main_paper"
            role_defaulted = True
            env["claims"][i] = claim
        claim["_attributed_source_role_defaulted"] = role_defaulted
        full_canon = canonical_dumps(claim)
        repeat_ordinal = 1
        if full_canon in canon_claims:
            repeat_ordinal = canon_claims[full_canon] + 1
        canon_claims[full_canon] = canon_claims.get(full_canon, 0) + 1
        pre_dup = repeat_ordinal >= 2
        from answer_eval.mutation import get as mut

        if mut() == "post_match_repeats":
            pre_dup = False
        assertions.extend(
            _atoms_from_claim(claim, i, qid, width, proj, pre_dup, repeat_ordinal)
        )
    l0 = len(assertions)
    l1 = [a for a in assertions if not a.get("invalid")]
    graph = [a for a in l1 if a.get("kind") == "m1" and not a.get("pre_duplicate") and a.get("claim_type") != "Q3"]
    return {
        "envelope": env,
        "assertions": assertions,
        "L0": l0,
        "L1": len(l1),
        "P_graph": len(graph),
        "projection": proj,
    }


def _atoms_from_claim(claim, index, qid, width, projection, pre_dup, repeat_ordinal) -> list[dict[str, Any]]:
    ctype = claim.get("claim_type", "Q2")
    out: list[dict[str, Any]] = []
    if ctype == "Q1":
        steps = claim.get("steps")
        if isinstance(steps, list) and steps and not isinstance(steps[0], dict):
            for fp in REQUIRED_LEAVES["Q1_step"]:
                out.append(_invalid_atom(claim, index, qid, width, fp, "Q1"))
            return out
        if projection == "entire_envelope":
            fs = claim.get("feedstock") or {}
            if "source_type" in fs:
                out.append(
                    _leaf_atom(
                        claim,
                        index,
                        qid,
                        width,
                        "feedstock.source_type",
                        json_pointer(["claims", index, "feedstock", "source_type"]),
                        fs.get("source_type"),
                        "m1",
                        pre_dup,
                        repeat_ordinal,
                    )
                )
            if "pre_treatment" in fs:
                out.append(
                    _leaf_atom(
                        claim,
                        index,
                        qid,
                        width,
                        "feedstock.pre_treatment",
                        json_pointer(["claims", index, "feedstock", "pre_treatment"]),
                        fs.get("pre_treatment"),
                        "m1",
                        pre_dup,
                        repeat_ordinal,
                    )
                )
            for mi, mat in enumerate(claim.get("materials") or []):
                if isinstance(mat, dict) and "role" in mat:
                    out.append(
                        _leaf_atom(
                            claim,
                            index,
                            qid,
                            width,
                            "materials.role",
                            json_pointer(["claims", index, "materials", mi, "role"]),
                            mat.get("role"),
                            "m1",
                            pre_dup,
                            repeat_ordinal,
                        )
                    )
        for si, step in enumerate(claim.get("steps") or []):
            if not isinstance(step, dict):
                continue
            promoted = bool(step.get("identity_conditions") or step.get("step_identity"))
            if step.get("step_identity") is not None:
                from answer_eval.mutation import get as mut

                kind_si = "descriptive" if mut() == "no_promoted_vertex" else "m1"
                out.append(
                    _leaf_atom(
                        claim,
                        index,
                        qid,
                        width,
                        "step_identity",
                        json_pointer(["claims", index, "steps", si, "step_identity"]),
                        step.get("step_identity"),
                        kind_si,
                        pre_dup,
                        repeat_ordinal,
                        extra={"identity_conditions": step.get("identity_conditions"), "step": step},
                    )
                )
            conds = step.get("conditions") or {}
            desc = step.get("descriptive_conditions") or {}
            for name, val in conds.items():
                fp = f"conditions.{name}"
                kind = "m1" if name == "T" and not promoted else "descriptive"
                path = json_pointer(["claims", index, "steps", si, "conditions", name])
                out.append(_leaf_atom(claim, index, qid, width, fp, path, val, kind, pre_dup, repeat_ordinal, extra={"step": step}))
            for name, val in desc.items():
                fp = f"conditions.{name}"
                path = json_pointer(["claims", index, "steps", si, "descriptive_conditions", name])
                out.append(_leaf_atom(claim, index, qid, width, fp, path, val, "descriptive", pre_dup, repeat_ordinal, extra={"step": step}))
        for oi, outcome in enumerate(claim.get("outcomes") or []):
            if isinstance(outcome, dict) and "value" in outcome:
                out.append(
                    _leaf_atom(
                        claim,
                        index,
                        qid,
                        width,
                        "outcome.value",
                        json_pointer(["claims", index, "outcomes", oi, "value"]),
                        outcome.get("value"),
                        "m1",
                        pre_dup,
                        repeat_ordinal,
                    )
                )
        return out
    if ctype == "Q3":
        path = json_pointer(["claims", index, "proposition"])
        val = claim.get("proposition")
        out.append(
            _leaf_atom(
                claim,
                index,
                qid,
                width,
                "proposition",
                path,
                val,
                "proposition",
                pre_dup,
                repeat_ordinal,
            )
        )
        return out
    leaves = dict(claim.get("leaves") or {})
    desc = dict(claim.get("descriptive_conditions") or {})
    known = set(Q2_M1) | {"proposition"}
    for key in list(leaves):
        nkey = nfkc(key)
        if nkey not in known and key not in Q2_M1 and key != "value":
            if nkey not in {"value", "uncertainty", "method", "obtained_by", "attributed_as"}:
                path = json_pointer(["claims", index, "leaves", key])
                out.append(_invalid_atom(claim, index, qid, width, key, ctype, path=path, value=leaves[key]))
                leaves.pop(key, None)
    for field, val in leaves.items():
        kind = "m1" if field in Q2_M1 or field == "value" else "descriptive"
        path = json_pointer(["claims", index, "leaves", field])
        invalid = _is_invalid_leaf(val) if field == "value" else False
        atom = _leaf_atom(claim, index, qid, width, field, path, val, kind, pre_dup, repeat_ordinal)
        if invalid:
            atom["invalid"] = True
        out.append(atom)
    for name, val in desc.items():
        path = json_pointer(["claims", index, "leaves", name]) if False else json_pointer(["claims", index, "descriptive_conditions", name])
        out.append(_leaf_atom(claim, index, qid, width, name, path, val, "descriptive", pre_dup, repeat_ordinal))
    return out


def _leaf_atom(claim, index, qid, width, field_path, path, value, kind, pre_dup, repeat_ordinal, extra=None) -> dict[str, Any]:
    sid = slot_id(qid, path, width)
    atom = {
        "claim_index": index,
        "claim_type": claim.get("claim_type"),
        "subpart_id": claim.get("subpart_id"),
        "study_family_id": claim.get("study_family_id"),
        "attributed_source_role": claim.get("attributed_source_role") or "main_paper",
        "material_ref": claim.get("material_ref"),
        "material_mention": claim.get("material_mention"),
        "material_registry_id": claim.get("material_registry_id"),
        "quantity": claim.get("quantity") or claim.get("property"),
        "basis": claim.get("basis"),
        "identity_conditions": claim.get("identity_conditions") or {},
        "field_path": field_path,
        "envelope_path": path,
        "slot_id": sid,
        "value": value,
        "kind": "proposition" if kind == "proposition" else kind,
        "pre_duplicate": pre_dup,
        "repeat_ordinal": repeat_ordinal,
        "evidence_refs": list(claim.get("evidence_refs") or []),
        "field_evidence": (claim.get("field_evidence") or {}).get(field_path),
        "claim": claim,
        "invalid": False,
    }
    if extra:
        atom.update(extra)
    return atom


def _invalid_atom(claim, index, qid, width, field_path, ctype, path=None, value=None) -> dict[str, Any]:
    path = path or json_pointer(["claims", index, field_path])
    atom = _leaf_atom(claim, index, qid, width, field_path, path, value, "m1", False, 1)
    atom["invalid"] = True
    atom["kind"] = "m1"
    return atom


def atomize_free_text(prediction: dict[str, Any], tables: dict[str, Any] | None, projection: str | None) -> dict[str, Any]:
    from answer_eval.mutation import get as mut

    text = prediction.get("text") or ""
    sentences = [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]
    phrases = _phrases()
    qaliases = _qaliases()
    qre = quantity_regex()
    mat_stub = prediction.get("material_resolution_stub") or {}
    qty_stub = prediction.get("quantity_resolution_stub") or {}
    question = (tables or {}).get("question") or {}
    subparts = question.get("subparts") or []
    qid = question.get("question_id") or ""
    width = int((tables or {}).get("id_hex_width") or PRODUCTION_HEX_WIDTH)
    assertions = []
    refusal_sentences = 0
    prose_sentences = 0
    prose_in_numeric = 0
    quantity_mentions = 0
    basis_unbound = 0
    subpart_ambiguous = 0
    assignment = {}
    unanswered = []
    for sent in sentences:
        folded = sent.casefold()
        mentions = list(qre.finditer(sent))
        is_refusal = any(p in folded for p in phrases) and not mentions
        if is_refusal:
            refusal_sentences += 1
            unanswered.append({"subpart_id": (subparts[0]["subpart_id"] if subparts else "s1"), "reason": "no_evidence_in_scope"})
            continue
        if mentions:
            prose_in_numeric += 1
            quantity_mentions += len(mentions)
            for mi, m in enumerate(mentions):
                unit = m.group("unit")
                number = m.group("number")
                start = m.start()
                preceding = sent[: m.start()]
                material = None
                registry = None
                best_pos = -1
                for name, rid in mat_stub.items():
                    pos = preceding.rfind(name)
                    if pos > best_pos:
                        best_pos = pos
                        material = name
                        registry = rid
                qty_name = None
                for alias, canon in qaliases.items():
                    if alias in sent.casefold():
                        qty_name = canon
                        break
                if qty_stub:
                    for token, resolved in qty_stub.items():
                        if token.casefold() in sent.casefold() and resolved:
                            qty_name = resolved
                            break
                    for token, resolved in qty_stub.items():
                        if token.casefold() in sent.casefold() and resolved is None and qty_name is None:
                            qty_name = None
                typed = bool(qty_name) and bool(registry)
                scope_basis = None
                if subparts:
                    bases = (subparts[0].get("resolved_scope") or {}).get("basis") or []
                    if len(bases) == 1:
                        scope_basis = bases[0]
                    else:
                        scope_basis = "unbound"
                        basis_unbound += 1
                value = {
                    "shape": "scalar",
                    "reported": number,
                    "dimension_status": "dimensional" if unit else "dimensionless",
                }
                if unit:
                    value["unit_reported"] = unit
                claim = {
                    "claim_type": "Q2",
                    "subpart_id": subparts[0]["subpart_id"] if subparts else "s1",
                    "study_family_id": question.get("primary_family"),
                    "material_ref": material,
                    "material_mention": material,
                    "material_registry_id": registry,
                    "quantity": qty_name,
                    "basis": scope_basis,
                    "identity_conditions": {},
                    "leaves": {"value": value},
                    "evidence_refs": [],
                    "attributed_source_role": "main_paper",
                }
                path = json_pointer(["claims", len(assertions), "leaves", "value"])
                atom = _leaf_atom(claim, len(assertions), qid, width, "value", path, value, "m1", False, 1)
                if not typed:
                    atom["invalid"] = True
                    atom["invalid_subtype"] = "untyped_quantity"
                else:
                    eligible_sub = []
                    for sp in subparts:
                        sc = sp.get("resolved_scope") or {}
                        mats = sc.get("materials") or []
                        qtys = sc.get("quantities") or []
                        if material in mats and qty_name in qtys:
                            eligible_sub.append(sp)
                    if not eligible_sub:
                        atom["out_of_scope"] = True
                    elif len(eligible_sub) > 1:
                        subpart_ambiguous += 1
                        atom["subpart_id"] = min(eligible_sub, key=lambda s: s.get("subpart_ordinal", 0)).get("subpart_id")
                    atom["basis_source"] = "resolved_scope.basis single member"
                from answer_eval.mutation import get as mut

                if mut() == "basis_from_reference":
                    atom["basis_source"] = "reference"
                    atom["basis"] = "borrowed"
                assertions.append(atom)
        else:
            from answer_eval.mutation import get as mut

            if mut() == "discard_nonnumeric_prose":
                continue
            prose_sentences += 1
            lowest = None
            if subparts:
                lowest = min(subparts, key=lambda s: s.get("subpart_ordinal", 10**9))
            prop = fold_ws(nfkc(sent))
            claim = {
                "claim_type": "Q3",
                "subpart_id": (lowest or {}).get("subpart_id"),
                "proposition": prop,
                "evidence_refs": [],
                "attributed_source_role": "main_paper",
                "study_family_id": question.get("primary_family"),
            }
            path = json_pointer(["claims", len(assertions), "proposition"])
            atom = _leaf_atom(claim, len(assertions), qid, width, "proposition", path, prop, "proposition", False, 1)
            atom["assignment_rule"] = "lowest_ordinal_regardless_of_truth"
            assertions.append(atom)
            if lowest:
                assignment[lowest.get("subpart_id")] = "lowest_ordinal_regardless_of_truth"
    l1 = [a for a in assertions if not a.get("invalid")]
    graph = [a for a in l1 if a.get("kind") == "m1" and a.get("claim_type") != "Q3"]
    invalid = [a for a in assertions if a.get("invalid")]
    breakdown = {"Q2": sum(1 for a in l1 if a.get("claim_type") == "Q2"), "Q3_proposition": sum(1 for a in l1 if a.get("kind") == "proposition")}
    return {
        "envelope": {"kind": "envelope", "status": "complete", "claims": [a["claim"] for a in assertions], "unanswered_subparts": unanswered},
        "assertions": assertions,
        "L0": len(assertions),
        "L1": len(l1),
        "P_graph": len(graph),
        "sentences": len(sentences),
        "refusal_sentences": refusal_sentences,
        "prose_sentences": prose_sentences,
        "prose_in_numeric_sentences": prose_in_numeric,
        "quantity_mentions": quantity_mentions,
        "basis_unbound": basis_unbound,
        "subpart_ambiguous": subpart_ambiguous,
        "L1_breakdown": breakdown,
        "invalid": len(invalid),
        "invalid_subtype": {"untyped_quantity": sum(1 for a in invalid if a.get("invalid_subtype") == "untyped_quantity")},
        "assignment": assignment,
        "basis_source": "reference" if mut() == "basis_from_reference" else "resolved_scope.basis single member",
        "unanswered_subparts": unanswered,
    }
