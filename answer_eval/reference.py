"""§3.2.0(h) reference typing, derivation, assignment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from answer_eval.canon import canonical_dumps, fold_ws
from answer_eval.errors import ReferenceAssignmentAmbiguous, ReferenceDuplicateAtom
from answer_eval.ids import PRODUCTION_HEX_WIDTH, atom_id, observation_id

SOURCE_RANK = {"main_paper": 0, "supplement": 1, "correction": 2}
IDENTITY_PRIORITY = ["T", "P", "solvent_composition", "solids_loading", "t", "medium", "method"]


def _as_file(file: Any) -> dict[str, Any]:
    if isinstance(file, dict):
        return file
    path = Path(file)
    return json.loads(path.read_text(encoding="utf-8"))


def derive_identity_conditions(observations: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for obs in observations:
        if obs.get("identity_conditions_supplied", True):
            continue
        ck = obs.get("comparison_key") or {}
        key = (
            obs.get("study_family_id"),
            ck.get("material_ref"),
            ck.get("quantity"),
            ck.get("basis"),
        )
        groups.setdefault(key, []).append(obs)
    derived_names: list[str] | None = None
    occurrence_needed = False
    for group in groups.values():
        chosen: list[str] = []
        for name in IDENTITY_PRIORITY:
            chosen.append(name)
            keys = []
            for obs in group:
                conds = obs.get("reported_conditions") or {}
                keys.append(tuple(sorted((k, str(conds.get(k))) for k in chosen if k in conds)))
            if len(set(keys)) == len(group):
                break
        else:
            occurrence_needed = True
        derived_names = chosen
        for obs in group:
            conds = obs.get("reported_conditions") or {}
            ident = {k: conds[k] for k in chosen if k in conds}
            obs["identity_conditions_derived_set"] = ident
            desc = [k for k in (obs.get("reported_conditions") or {}) if k not in ident]
            obs["descriptive_binding_fields_derived"] = desc
    return {
        "identity_conditions_derived": derived_names or [],
        "descriptive_binding_fields_derived": [k for k in IDENTITY_PRIORITY if derived_names and k not in derived_names],
        "priority_order_used": IDENTITY_PRIORITY,
        "occurrence_index_needed": occurrence_needed,
    }


def derive_occurrence(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for obs in observations:
        if obs.get("occurrence_index_supplied") is False:
            ck = canonical_dumps(obs.get("comparison_key") or {})
            groups.setdefault((obs.get("study_family_id"), ck), []).append(obs)
    for group in groups.values():
        group.sort(
            key=lambda o: (
                SOURCE_RANK.get(o.get("source_role"), 9),
                int((o.get("page") or {}).get("pdf_index") or 0),
                canonical_dumps(o.get("source_locator")),
                canonical_dumps(o),
            )
        )
        for i, obs in enumerate(group, 1):
            obs["occurrence_index_derived"] = i
    return observations


def _claim_type_of(obs: dict[str, Any], question: dict[str, Any] | None) -> str:
    if obs.get("claim_type"):
        return obs["claim_type"]
    container = obs.get("container")
    if container in {"Q1 step", "Q1 outcome", "Q1 experiment"}:
        obs["claim_type_derived"] = True
        return "Q1"
    subparts = (question or {}).get("subparts") or []
    if any(s.get("answer_form") == "synthesis_table" for s in subparts):
        obs["claim_type_derived"] = True
        return "Q4"
    obs["claim_type_derived"] = True
    return "Q2"


def load_reference(file: Any, question: dict[str, Any] | None = None, question_set: list | None = None, id_hex_width: int = PRODUCTION_HEX_WIDTH) -> dict[str, Any]:
    payload = _as_file(file)
    observations = list(payload.get("observations") or [])
    questions = question_set
    if question and not questions:
        questions = [question]
    if question_set and observations and any("question_id" not in o for o in observations):
        if len(observations) != len(question_set):
            raise ReferenceAssignmentAmbiguous("positional assignment count mismatch")
        for obs, q in zip(observations, question_set):
            obs["question_id"] = q["question_id"]
            obs["_assigned_question"] = q
    elif question:
        for obs in observations:
            obs.setdefault("question_id", question.get("question_id"))
            obs.setdefault("_assigned_question", question)
    identity_info = derive_identity_conditions(observations)
    derive_occurrence(observations)

    canon_seen: dict[str, int] = {}
    loaded = []
    for obs in observations:
        qid = obs.get("question_id") or (question or {}).get("question_id")
        qobj = obs.get("_assigned_question") or question or {}
        ctype = _claim_type_of(obs, qobj)
        obs["claim_type"] = ctype
        occ = obs.get("occurrence_index_derived") or obs.get("occurrence_index") or 1
        oid = observation_id(
            qid,
            obs.get("study_family_id"),
            obs.get("source_role") or "main_paper",
            obs.get("comparison_key") or {},
            int(occ),
            id_hex_width,
        )
        obs["observation_id_computed"] = oid
        atoms = obs.get("atoms") or []
        if isinstance(atoms, dict):
            atoms = list(atoms.values()) if False else atoms
        computed_atoms = []
        for atom in atoms:
            aid = atom_id(qid, oid, atom.get("field_path"), id_hex_width)
            atom = dict(atom)
            atom["atom_id_computed"] = aid
            computed_atoms.append(atom)
        obs = dict(obs)
        obs["atoms"] = computed_atoms or atoms
        body = {k: v for k, v in obs.items() if k not in {"observation_id", "observation_id_computed", "_assigned_question"}}
        canon = canonical_dumps(body)
        if canon in canon_seen:
            from answer_eval.mutation import get as mut

            if mut() != "accept_ref_dup":
                raise ReferenceDuplicateAtom("repeated reference occurrence")
        canon_seen[canon] = 1
        loaded.append(obs)
    return {"observations": loaded, "identity_info": identity_info}
