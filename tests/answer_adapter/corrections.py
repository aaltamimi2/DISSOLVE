"""Bounded C-2/C-3/C-4 checks at exported adapter functions."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

from answer_adapter import check_draft, normalize_draft, run_question
from answer_adapter.halt import AdapterHalt
from answer_adapter.ledger import new_ledger, normalize_reference

HERE = Path(__file__).resolve().parent


def _load_harness():
    spec = importlib.util.spec_from_file_location("aa_harness_corr", HERE / "harness.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_harness = _load_harness()
ScriptedExecutor = _harness.ScriptedExecutor
ScriptedModel = _harness.ScriptedModel


def _clone(bundle, fixture_id: str) -> dict:
    return copy.deepcopy(next(f for f in bundle["fixtures"] if f["fixture_id"] == fixture_id))


def _row(test_id: str, ok: bool) -> dict:
    return {"id": test_id, "ok": bool(ok)}


def _run_cloned(fx: dict):
    inp = fx["input"]
    model = ScriptedModel(inp.get("model_script") or [])
    executor = ScriptedExecutor(inp.get("executor_script") or [])
    try:
        result = run_question(
            inp["question"],
            inp["arm"],
            model,
            executor,
            inp["config"],
            inp.get("clock"),
            inp.get("run_ordinal"),
        )
        return result, None, model, executor
    except AdapterHalt as exc:
        return None, exc, model, executor


def test_c2_round_cap(bundle) -> dict:
    fx = _clone(bundle, "F-RUN-5")
    fx["input"]["config"]["max_tool_rounds"] = 9
    result, halt, model, executor = _run_cloned(fx)
    ok = (
        halt is None
        and isinstance(result, dict)
        and result.get("adapter_error") == "budget_exhausted"
        and len(result.get("executor_calls") or []) == 8
        and len(executor.calls) == 8
        and len(model.calls) == 9
    )
    return _row("C-2.max_tool_rounds", ok)


def test_c2_closed_book_model_id(bundle) -> dict:
    fx = _clone(bundle, "F-RUN-3")
    fx["input"]["config"]["model_id"] = "openai:not-the-pinned-model"
    result, halt, model, executor = _run_cloned(fx)
    ok = (
        result is None
        and halt is not None
        and halt.halt == "stamp_value_mismatch"
        and halt.info.get("field") == "model_id"
        and len(model.calls) == 0
        and len(executor.calls) == 0
    )
    return _row("C-2.closed_book_model_id", ok)


def test_c2_empty_by_name(bundle) -> dict:
    fx = _clone(bundle, "F-RUN-1")
    fx["input"]["config"]["by_name"] = []
    result, halt, model, executor = _run_cloned(fx)
    ok = (
        result is None
        and halt is not None
        and halt.halt == "registry_drift"
        and len(model.calls) == 0
        and len(executor.calls) == 0
    )
    return _row("C-2.empty_by_name", ok)


def _type_codes(errors: object, path: str) -> bool:
    if not isinstance(errors, list):
        return False
    return any(isinstance(e, dict) and e.get("path") == path and e.get("code") == "invalid_type" for e in errors)


def test_c3_claims_object(bundle) -> dict:
    fx = _clone(bundle, "F-ENV-1")
    draft = copy.deepcopy(fx["input"]["draft"])
    draft["claims"] = {"not": "a-list"}
    checked = check_draft(draft, fx["input"]["profile"])
    normalized = normalize_draft(draft, new_ledger(), "rag_alone", "unused")
    ok = (
        checked.get("draft_structure_valid") is False
        and _type_codes(checked.get("draft_errors"), "/claims")
        and checked.get("claim_errors") == {}
        and normalized.get("claims") == {"not": "a-list"}
        and any(isinstance(e, dict) and e.get("code") == "invalid_type" for e in (normalized.get("draft_errors") or []))
    )
    return _row("C-3.claims_object", ok)


def test_c3_unanswered_object(bundle) -> dict:
    fx = _clone(bundle, "F-ENV-1")
    draft = copy.deepcopy(fx["input"]["draft"])
    draft["unanswered_subparts"] = {"not": "a-list"}
    checked = check_draft(draft, fx["input"]["profile"])
    normalized = normalize_draft(draft, new_ledger(), "rag_alone", "unused")
    ok = (
        checked.get("draft_structure_valid") is False
        and _type_codes(checked.get("draft_errors"), "/unanswered_subparts")
        and normalized.get("unanswered_subparts") == {"not": "a-list"}
    )
    return _row("C-3.unanswered_object", ok)


def test_c3_limitations_object(bundle) -> dict:
    fx = _clone(bundle, "F-ENV-1")
    draft = copy.deepcopy(fx["input"]["draft"])
    draft["limitations"] = {"not": "a-list"}
    checked = check_draft(draft, fx["input"]["profile"])
    normalized = normalize_draft(draft, new_ledger(), "rag_alone", "unused")
    ok = (
        checked.get("draft_structure_valid") is False
        and _type_codes(checked.get("draft_errors"), "/limitations")
        and normalized.get("limitations") == {"not": "a-list"}
    )
    return _row("C-3.limitations_object", ok)


def test_c3_raw_record(bundle) -> dict:
    fx = _clone(bundle, "F-RUN-1")
    result, halt, model, executor = _run_cloned(fx)
    ok = (
        halt is None
        and isinstance(result, dict)
        and result.get("adapter_error") is None
        and "raw_response" in result
        and "raw_draft" in result
        and result.get("raw_response") is not None
        and isinstance(result.get("raw_draft"), dict)
        and len(model.calls) > 0
        and len(executor.calls) > 0
    )
    return _row("C-3.raw_record", ok)


def test_c4_closed_book_refs(bundle) -> dict:
    malformed = {"not": "a-token"}
    grammar = "C1"
    token_out = normalize_reference(grammar, "closed_book", {}, "unused")
    obj_out = normalize_reference(malformed, "closed_book", {}, "unused")
    fx = _clone(bundle, "F-ENV-3")
    draft = copy.deepcopy(fx["input"]["draft"])
    draft["claims"][0] = dict(draft["claims"][0])
    draft["claims"][0]["evidence_refs"] = [grammar, malformed]
    normalized = normalize_draft(draft, new_ledger(), "closed_book", "unused")
    refs = (normalized.get("claims") or [{}])[0].get("evidence_refs") if normalized.get("claims") else None
    ok = (
        token_out.get("kind") == "unresolved_ref.v1"
        and token_out.get("reason") == "no_visible_evidence"
        and token_out.get("draft_ref") == grammar
        and obj_out.get("reason") == "no_visible_evidence"
        and obj_out.get("draft_ref") is malformed
        and isinstance(refs, list)
        and len(refs) == 2
        and refs[0].get("reason") == "no_visible_evidence"
        and refs[0].get("draft_ref") == grammar
        and refs[1].get("reason") == "no_visible_evidence"
        and refs[1].get("draft_ref") is malformed
    )
    return _row("C-4.closed_book_refs", ok)


def run_all(bundle) -> dict:
    rows = [
        test_c2_round_cap(bundle),
        test_c2_closed_book_model_id(bundle),
        test_c2_empty_by_name(bundle),
        test_c3_claims_object(bundle),
        test_c3_unanswered_object(bundle),
        test_c3_limitations_object(bundle),
        test_c3_raw_record(bundle),
        test_c4_closed_book_refs(bundle),
    ]
    failed_ids = [r["id"] for r in rows if not r["ok"]]
    return {
        "n": len(rows),
        "passed": sum(1 for r in rows if r["ok"]),
        "failed_ids": failed_ids,
        "rows": rows,
    }
