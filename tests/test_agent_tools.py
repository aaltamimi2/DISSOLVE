"""Dispatch, result_read, handle issue, and the loop — chunk 2 obligations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_harness
from agent_harness import TurnResult, run_turn
from agent_tools import dispatch, result_read, source_basis_for, tool_schemas
from dissolve.session import (
    bind_tool_session, current_tool_session, handle_rows, load_handle,
    load_turn_record, new_session, open_turn_record, store_handle,
)


def _bound(session=None):
    return bind_tool_session(session if session is not None else new_session())


def test_result_read_empty_and_unknown_are_named():
    with _bound() as rec:
        empty = dispatch("result_read", handle="")
        missing = dispatch("result_read")
        unknown = dispatch("result_read", handle="no-such-handle")
        none = result_read(handle=None)  # type: ignore[arg-type]
        assert empty["refusal"] == unknown["refusal"] == missing["refusal"] == "unknown_handle"
        assert none["refusal"] == "unknown_handle"
        assert empty["available"] is False
        assert rec.get("handles") == {}


def test_consumer_empty_unknown_missing_are_no_upstream_not_keyerror():
    with _bound():
        missing = dispatch("compare_solvent_safety_at_conditions")
        empty = dispatch("compare_solvent_safety_at_conditions", handle="")
        unknown = dispatch("compare_solvent_safety_at_conditions", handle="ghost-white-fox")
        assert missing["refusal"] == empty["refusal"] == unknown["refusal"] == "no_upstream_candidates"
        assert all(r["available"] is False for r in (missing, empty, unknown))


def test_result_read_pages_exact_rows_without_second_handle():
    with _bound() as rec:
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        assert screen["available"] and screen.get("handle")
        h = screen["handle"]
        stored = load_handle(rec, h)
        exact = handle_rows(stored)
        assert screen["total"] == len(exact)
        assert screen["shown"] == min(20, len(exact))
        assert screen["top"] is not exact
        assert screen["top"] == exact[:20]
        page = dispatch("result_read", handle=h, offset=0, limit=20)
        assert page["available"] is True
        assert "handle" not in page.get("data", {}) or page.get("handle") == h
        assert page["total"] == len(exact)
        assert page["returned"] == min(20, len(exact))
        assert page["data"]["rows"] == exact[0:20]
        assert page is not screen
        assert "handle" not in page or rec["handles"].get(page.get("handle")) is stored
        far = dispatch("result_read", handle=h, offset=len(exact), limit=20)
        assert far["returned"] == 0 and far["total"] == len(exact)
        clamped = dispatch("result_read", handle=h, offset=0, limit=99)
        assert clamped["returned"] <= 50


def test_large_unrecognised_contaminant_comparison_named_refusal():
    with _bound() as rec:
        out = dispatch(
            "compare_contaminant_removal_modes",
            target_polymer="LDPE", contaminants="PFAS",
        )
        assert out["available"] is False
        assert out["refusal"] == "unaddressable_result"
        assert rec.get("handles") == {}
        assert "handle" not in out
        assert len(json.dumps(out)) < 8192
        archived = rec["turn_records"][rec["_turn"]][-1]
        assert archived["handle"] is None
        assert archived["exact"].get("success") is True
        assert len(json.dumps(archived["exact"])) > 8192


def test_large_unrecognised_precipitation_fallback_named_refusal():
    with _bound() as rec:
        out = dispatch(
            "screen_precipitation_order",
            feed_polymers=["LDPE", "PP"], first_polymer="LDPE",
            second_polymer="PP", min_ordering_window_c=999.0, top_k=5,
        )
        assert out["available"] is False
        assert out["refusal"] == "unaddressable_result"
        assert rec.get("handles") == {}
        archived = rec["turn_records"][rec["_turn"]][-1]
        assert archived["handle"] is None
        assert archived["exact"].get("success") is True
        assert len(json.dumps(archived["exact"])) > 8192


def test_small_success_without_handle_dumps_data():
    with _bound() as rec:
        out = dispatch(
            "solubility_query",
            polymers=["LDPE"], solvents=["dodecane"], temperatures=[140.0],
        )
        assert out["available"] is True
        assert "handle" not in out
        assert out["source_basis"] == "cosmo_rs_grid"
        assert "source_basis" not in (out.get("data") or {})
        assert "display" not in out
        assert rec.get("handles") == {}


def test_handle_beats_copied_list_at_dispatch():
    with _bound() as rec:
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        h, total = screen["handle"], screen["total"]
        copy = [dict(screen["top"][0])]
        safety = dispatch(
            "compare_solvent_safety_at_conditions",
            handle=h, candidates=copy, include_pubchem=False,
        )
        assert safety["available"] is True
        data = safety["data"]
        assert data["candidate_scope_stored_count"] == total
        assert data["candidate_count"] != total or total <= 6
        assert "last_candidates" not in rec


def test_route_substitution_bound_count_separate_from_displayed():
    with _bound() as rec:
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        row = (load_handle(rec, screen["handle"]) and handle_rows(load_handle(rec, screen["handle"])))[0]
        steps = [{
            "dissolved_polymer": row.get("dissolved_polymer") or row.get("target_polymer") or row.get("polymer") or "LDPE",
            "solvent": row["solvent"],
            "temperature_c": row.get("temperature_c") or row.get("dissolution_temperature_c"),
        }]
        sub = dispatch(
            "screen_route_solvent_substitutions",
            feed_polymers=["LDPE", "PP"], route_steps=steps, include_pubchem=False,
        )
        assert sub["available"] is True
        h = sub["handle"]
        bound_n = sub["total"]
        assert bound_n == 11
        copy = [{"solvent_name": "xylene", "operating_temp_c": 80.0}]
        safety = dispatch(
            "compare_solvent_safety_at_conditions",
            handle=h, candidates=copy, include_pubchem=False,
        )
        data = safety["data"]
        assert data["candidate_scope_stored_count"] == bound_n == 11
        assert data["candidate_count"] == 6
        assert data["candidate_count"] != data["candidate_scope_stored_count"]
        assert "last_candidates" not in rec


def test_unwired_names_do_not_call_engine():
    with _bound():
        for name in (
            "evaluate_stored_route_tea_lca",
            "optimize_stored_route",
            "pareto_optimize_stored_route",
        ):
            out = dispatch(name)
            assert out["refusal"] == "tool_not_wired"
            assert out["available"] is False


def test_include_pubchem_wrapper_default_false():
    with _bound():
        out = dispatch("get_solvent_safety_card", solvent_name="dodecane")
        assert out["available"] is True
        assert out["source_basis"] == "safety_local"
        assert out["data"].get("include_pubchem") is False


def test_schemas_handle_only_on_consumer_and_omit_injected():
    schemas = {s["name"]: s for s in tool_schemas()}
    assert "result_read" in schemas
    assert "handle" in schemas["compare_solvent_safety_at_conditions"]["parameters"]["properties"]
    assert "handle" not in schemas["solubility_query"]["parameters"]["properties"]
    assert "temperature_step_c" not in schemas["screen_polymer_separation"]["parameters"]["properties"]
    sq = schemas["solubility_query"]["parameters"]["properties"]
    assert sq["polymers"]["type"] == "array"
    assert sq["polymers"]["items"]["type"] == "string"
    assert sq["temperatures"]["type"] == "array"
    assert sq["require_atmospheric"]["type"] == "boolean"
    assert sq["top_k"]["type"] == "integer"
    assert sq["min_solubility_pct"]["type"] == "number"
    assert sq["descending"]["default"] is True
    card = schemas["get_solvent_safety_card"]["parameters"]["properties"]
    assert card["include_pubchem"]["type"] == "boolean"
    assert card["include_pubchem"]["default"] is False
    tea = schemas["evaluate_tea_lca_scenarios"]["parameters"]["properties"]
    assert tea["scenarios"]["type"] == "array"
    assert tea["timeout_seconds"]["type"] == "integer"
    rm = schemas["screen_polymer_separation"]["parameters"]["properties"]["ranking_mode"]
    assert "target_dissolution" in rm["enum"]
    mt = schemas["lookup_hansen_parameters"]["parameters"]["properties"]["material_type"]
    assert set(mt["enum"]) == {"polymer", "solvent"}
    for name in (
        "evaluate_stored_route_tea_lca",
        "optimize_stored_route",
        "pareto_optimize_stored_route",
    ):
        assert schemas[name]["description"].startswith("UNWIRED.")


def test_active_record_reported_survives_and_original_write_fails():
    original = {"handles": {}, "reported": []}
    with bind_tool_session(original) as bound:
        dispatch(
            "solubility_query",
            polymers=["LDPE"], solvents=["dodecane"], temperatures=[140.0],
        )
        bound["reported"].append({"number": 92.5, "source_basis": "cosmo_rs_grid"})
        assert current_tool_session() is bound
        with pytest.raises(RuntimeError):
            store_handle(
                original, tool="x", source_basis="y",
                data={"results": [{"solvent": "dodecane"}]},
            )
    assert original["reported"] == [{"number": 92.5, "source_basis": "cosmo_rs_grid"}]


def test_loop_prose_does_not_gate_or_read_record(monkeypatch):
    session = new_session()

    def fake_complete(messages, tools, **kwargs):
        return {"text": "The recovery window is 46 C.", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn("q", session=session, model="openai:x")
    assert result.status == "ok"
    assert result.answer == "The recovery window is 46 C."
    assert isinstance(result, TurnResult)
    assert "numeral_scan" not in TurnResult.__dataclass_fields__
    assert result.turn_record
    assert load_turn_record(session, result.turn_record) == []
    assert "_turn" not in session
    src = Path(agent_harness.__file__).read_text()
    assert "load_turn_record" not in src
    assert "numeral_scan" not in src
    assert result.status != "verifier_failed"


def test_turn_record_every_result_exact_ordered_durable():
    session = new_session()
    with bind_tool_session(session) as rec:
        tid = open_turn_record(rec)
        small = dispatch(
            "solubility_query",
            polymers=["LDPE"], solvents=["dodecane"], temperatures=[140.0],
        )
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        refused = dispatch(
            "compare_contaminant_removal_modes",
            target_polymer="LDPE", contaminants="PFAS",
        )
        page = dispatch("result_read", handle=screen["handle"], offset=0, limit=5)
        empty = dispatch("result_read", handle="")
        rows = rec["turn_records"][tid]
        assert [r["tool"] for r in rows] == [
            "solubility_query", "screen_polymer_separation",
            "compare_contaminant_removal_modes", "result_read", "result_read",
        ]
        assert rows[0]["handle"] is None
        assert rows[0]["exact"] is small["data"]
        assert rows[0]["exact"].get("success") is True
        assert isinstance(rows[0]["display"], str) and rows[0]["display"]
        assert "display" not in small
        h = screen["handle"]
        stored = load_handle(rec, h)
        assert rows[1]["handle"] == h
        assert rows[1]["exact"] is stored["exact"]
        assert rows[1]["display"] == stored.get("display")
        assert isinstance(stored.get("display"), str) and stored["display"]
        assert rows[1]["exact"] is not screen.get("top")
        assert "display" not in screen
        assert refused["refusal"] == "unaddressable_result"
        assert rows[2]["handle"] is None
        assert rows[2]["exact"].get("success") is True
        assert isinstance(rows[2]["display"], str)
        assert len(json.dumps(rows[2]["exact"])) > 8192
        assert rows[3]["handle"] == h
        assert rows[3]["exact"] is stored["exact"]
        assert rows[3]["display"] == stored.get("display")
        assert page["data"]["rows"] is not rows[3]["exact"]
        assert rows[4]["handle"] is None
        assert rows[4]["exact"]["refusal"] == "unknown_handle"
    assert load_turn_record(session, tid)[2]["exact"].get("success") is True
    assert "_turn" not in session
    assert session["turn_records"][tid][0]["tool"] == "solubility_query"


def test_loop_turn_record_survives_bind_and_is_not_a_gate(monkeypatch):
    session = new_session()
    n = {"i": 0}

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {
                "text": "",
                "tool_calls": [{
                    "id": "1", "name": "solubility_query",
                    "args": {
                        "polymers": ["LDPE"], "solvents": ["dodecane"],
                        "temperatures": [140.0],
                    },
                }],
            }
        return {"text": "92.5 wt%", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn("q", session=session, model="openai:x")
    assert result.status == "ok"
    assert result.answer == "92.5 wt%"
    rows = load_turn_record(session, result.turn_record)
    assert len(rows) == 1
    assert rows[0]["tool"] == "solubility_query"
    assert rows[0]["handle"] is None
    assert rows[0]["exact"]["success"] is True
    assert result.tool_trace[0].result.get("data") is rows[0]["exact"]
    assert result.status != "verifier_failed"


def test_unknown_tool_and_polyethylene_not_picked():
    with _bound():
        assert dispatch("not_a_tool")["refusal"] == "unknown_tool"
        out = dispatch(
            "solubility_query",
            polymers=["polyethylene"], solvents=["dodecane"], temperatures=[140.0],
        )
        assert out["available"] is True
        polymers = {row.get("polymer") for row in out["data"].get("results") or []}
        assert polymers == {"LDPE", "HDPE"}


def _row_keys(data):
    return [
        k for k, v in (data or {}).items()
        if isinstance(v, list) and v and all(isinstance(i, dict) for i in v)
    ]


def test_handle_projection_strips_secondary_row_lists():
    with _bound() as rec:
        hansen = dispatch(
            "screen_hansen_compatibility",
            polymer_names=["LDPE"],
            solvent_names=["dodecane", "xylene", "toluene"],
            temperature_c=80.0,
        )
        assert hansen["available"] and hansen.get("handle")
        exact = load_handle(rec, hansen["handle"])["exact"]
        assert "joined_rows" in exact and "rows" in exact and "leading_matches" in exact
        assert _row_keys(hansen.get("data")) == []
        assert len(json.dumps(hansen)) < len(json.dumps(exact))
        leach = dispatch(
            "screen_contaminant_leaching",
            target_polymer="LDPE", contaminants="PFAS",
        )
        lexact = load_handle(rec, leach["handle"])["exact"]
        assert "contaminant_catalog" in lexact
        assert "contaminant_catalog" not in (leach.get("data") or {})
        assert _row_keys(leach.get("data")) == []
        assert len(json.dumps(leach)) < len(json.dumps(lexact))
        admitted = dispatch("lookup_admitted_process_records", target_polymer="LDPE")
        aexact = load_handle(rec, admitted["handle"])["exact"]
        assert "records" in aexact and "record_assumptions" in aexact
        assert _row_keys(admitted.get("data")) == []
        assert len(json.dumps(admitted)) < len(json.dumps(aexact))


def test_malformed_persisted_handle_named_refusal_not_valueerror():
    with _bound() as rec:
        rec["handles"]["calm-blue-cat"] = {
            "tool": "x", "source_basis": "y",
            "exact": {"success": True, "note": "no primary page"},
        }
        out = dispatch("compare_solvent_safety_at_conditions", handle="calm-blue-cat")
        assert out["refusal"] == "no_upstream_candidates"
        assert out["available"] is False
        page = dispatch("result_read", handle="calm-blue-cat")
        assert page["refusal"] == "unknown_handle"


def test_pubchem_basis_requires_live_contribution():
    headings = [
        "Flash Point", "Autoignition Temperature", "Vapor Pressure",
        "GHS Classification", "Non-Human Toxicity Values", "Biodegradation",
        "NIOSH Recommendations", "OSHA Standards",
    ]
    failed = {
        "success": True,
        "provenance": {
            "pubchem": "https://pubchem.ncbi.nlm.nih.gov/compound/1",
            "pubchem_failed_headings": headings,
            "pubchem_heading_errors": {h: {"failure_class": "http"} for h in headings},
        },
    }
    live = {
        "success": True,
        "provenance": {
            "pubchem": "https://pubchem.ncbi.nlm.nih.gov/compound/1",
            "pubchem_failed_headings": [],
        },
    }
    assert source_basis_for("get_solvent_safety_card", failed, {"include_pubchem": True}) == "safety_local"
    assert source_basis_for("get_solvent_safety_card", live, {"include_pubchem": True}) == "pubchem_live"
    assert source_basis_for("get_solvent_safety_card", live, {"include_pubchem": False}) == "safety_local"


def test_run_turn_appends_query_and_mutates_caller_messages(monkeypatch):
    session = new_session()
    history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "OLD-MARKER"},
        {"role": "assistant", "content": "old answer"},
    ]
    seen = {}

    def fake_complete(messages, tools, **kwargs):
        seen["same"] = messages is history
        seen["contents"] = [m.get("content") for m in messages]
        return {"text": "new answer", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn("NEW-MARKER", session=session, model="openai:x", messages=history)
    assert result.status == "ok"
    assert seen["same"] is True
    assert "NEW-MARKER" in seen["contents"]
    assert "OLD-MARKER" in seen["contents"]
    assert history[-1] == {"role": "assistant", "content": "new answer"}
    assert history[-2]["content"] == "NEW-MARKER"


def test_google_contents_keep_function_call_and_response():
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "1", "name": "solubility_query", "args": {"polymers": ["LDPE"]}}],
        },
        {
            "role": "tool", "name": "solubility_query", "tool_call_id": "1",
            "content": json.dumps({"available": True}),
        },
    ]
    contents = agent_harness._gen_contents(msgs)
    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[1].parts[-1].function_call.name == "solubility_query"
    assert contents[2].role == "user"
    assert contents[2].parts[0].function_response.name == "solubility_query"
