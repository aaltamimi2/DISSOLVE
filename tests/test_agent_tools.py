"""Dispatch, result_read, handle issue, and the loop — chunk 2 obligations."""
from __future__ import annotations

import ast
import inspect
import json
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import agent_harness
import agent_tools
from agent_harness import TurnResult, run_turn
from agent_tools import dispatch, result_read, source_basis_for, tool_schemas
from dissolve import session as sess
from dissolve.session import (
    CompactionBudgetError, append_reported, bind_tool_session,
    compact_messages, context_window, current_tool_session,
    estimated_tokens, handle_rows, load_handle, load_turn_record,
    new_session, open_turn_record, store_handle,
)


def _loop_call_graph():
    """Functions in the harness/wrapper/session modules reachable from run_turn."""
    mods = (agent_harness, agent_tools, sess)
    catalog = {
        name: obj
        for mod in mods for name, obj in vars(mod).items()
        if inspect.isfunction(obj) and inspect.getmodule(obj) in mods
    }
    seen: set = set()
    stack = [agent_harness.run_turn]
    while stack:
        fn = stack.pop()
        if fn in seen:
            continue
        seen.add(fn)
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            nxt = catalog.get(name) if name else None
            if nxt is not None:
                stack.append(nxt)
    return seen


def _archive_readers_in_loop_graph():
    """Names in run_turn's graph that read turn_records, excluding the writers."""
    writers = {"open_turn_record", "record_tool_call", "bind_tool_session", "new_session"}
    found = []
    for fn in _loop_call_graph():
        if fn.__name__ in writers:
            continue
        src = inspect.getsource(fn)
        if "turn_records" in src or "load_turn_record" in src:
            found.append(fn.__name__)
    return found


class ArchiveReadError(RuntimeError):
    """Row read of the validator archive from inside the loop."""


class ArchiveTrap(dict):
    """A turn_records table writers may append to. Row reads raise.

    Installed as the *value* of session['turn_records'] so bind_tool_session's
    shallow copy keeps the same object. open_turn_record / record_tool_call
    use len, `in`, __setitem__, and setdefault. Row reads via get, item
    access, iteration, values, or items raise.
    """

    def values(self):
        raise ArchiveReadError("archive rows consumed via values")

    def items(self):
        raise ArchiveReadError("archive rows consumed via items")

    def keys(self):
        raise ArchiveReadError("archive rows consumed via keys")

    def __iter__(self):
        raise ArchiveReadError("archive rows consumed via iter")

    def __getitem__(self, key):
        raise ArchiveReadError("archive rows consumed via item access")

    def get(self, *args, **kwargs):
        raise ArchiveReadError("archive rows consumed via get")

    def setdefault(self, key, default=None):
        if super().__contains__(key):
            return super().__getitem__(key)
        super().__setitem__(key, default)
        return default


def _forced_compact_turn(monkeypatch, session):
    """A reachable over-budget tool round. Last message at compact time is tool."""
    n = {"i": 0}
    history = [
        {"role": "user", "content": "OLD " + ("x" * 490_000)},
        {"role": "assistant", "content": "old answer"},
    ]

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}]}
        return {"text": "done", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn(
        "CURRENT-QUERY", session=session,
        model="openai:muse-spark-1.2", messages=history,
    )
    return result, n["i"]


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
        assert set(archived["exact"]) == {"display", "data"}
        assert archived["exact"]["data"].get("success") is True
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
        assert set(archived["exact"]) == {"display", "data"}
        assert archived["exact"]["data"].get("success") is True
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
    paths = schemas["ingest_literature_graph"]["parameters"]["properties"]["paths"]
    assert paths["type"] == "array" and paths["items"]["type"] == "string"
    empty = []
    for spec in tool_schemas():
        props = spec["parameters"]["properties"]
        for name in spec["parameters"].get("required") or []:
            schema = props.get(name) or {}
            if not (schema.get("type") or schema.get("anyOf") or schema.get("enum")):
                empty.append(f"{spec['name']}.{name}")
            if schema.get("type") == "array" and "items" not in schema:
                empty.append(f"{spec['name']}.{name}")
    assert empty == []
    assert [
        f"{spec['name']}.{n}"
        for spec in tool_schemas()
        for n, schema in spec["parameters"]["properties"].items()
        if schema.get("default", "MISSING") is None
    ] == []


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
    assert original["reported"][-1] == {"number": 92.5, "source_basis": "cosmo_rs_grid"}
    assert any(isinstance(r.get("number"), (int, float)) for r in original["reported"][:-1])


def test_loop_prose_does_not_gate_or_read_record(monkeypatch):
    session = new_session()

    def fake_complete(messages, tools, **kwargs):
        return {"text": "The recovery window is 46 C.", "tool_calls": []}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn("q", session=session, model="openai:x")
    assert result.status == "ok"
    assert result.answer == "The recovery window is 46 C."
    assert isinstance(result, TurnResult)
    assert list(TurnResult.__dataclass_fields__) == [
        "answer", "status", "tool_trace", "turn_record", "tool_rounds", "usage",
    ]
    assert "numeral_scan" not in TurnResult.__dataclass_fields__
    assert result.turn_record
    assert load_turn_record(session, result.turn_record) == []
    assert "_turn" not in session
    src = Path(agent_harness.__file__).read_text()
    assert "numeral_scan" not in src
    assert result.status != "verifier_failed"
    graph = _loop_call_graph()
    assert sess.compact_messages in graph
    assert sess._summary_text in graph
    assert agent_tools._emit in graph
    assert sess.load_turn_record not in graph
    assert _archive_readers_in_loop_graph() == []
    assert "result_read" not in inspect.getsource(agent_tools._emit)


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
        assert set(rows[0]["exact"]) == {"display", "data"}
        assert rows[0]["exact"]["data"] is small["data"]
        assert rows[0]["source_basis"] == small["source_basis"] == "cosmo_rs_grid"
        assert isinstance(rows[0]["exact"]["display"], str) and rows[0]["exact"]["display"]
        assert "display" not in small
        h = screen["handle"]
        stored = load_handle(rec, h)
        assert rows[1]["handle"] == h
        assert "exact" not in rows[1]
        assert rows[1]["source_basis"] == screen["source_basis"] == stored.get("source_basis")
        assert isinstance(stored.get("display"), str) and stored["display"]
        assert stored["exact"] is not screen.get("top")
        assert "display" not in screen
        assert refused["refusal"] == "unaddressable_result"
        assert rows[2]["handle"] is None
        assert set(rows[2]["exact"]) == {"display", "data"}
        assert rows[2]["exact"]["data"].get("success") is True
        assert isinstance(rows[2]["exact"]["display"], str)
        assert len(json.dumps(rows[2]["exact"])) > 8192
        assert rows[3]["handle"] == h
        assert rows[3]["exact"] is page
        assert rows[3]["exact"]["returned"] == 5
        assert rows[3]["exact"] is not stored["exact"]
        assert page["data"]["rows"] is not stored["exact"]
        assert rows[4]["handle"] is None
        assert rows[4]["exact"]["refusal"] == "unknown_handle"
    assert load_turn_record(session, tid)[2]["exact"]["data"].get("success") is True
    assert "_turn" not in session
    assert session["turn_records"][tid][0]["tool"] == "solubility_query"


def test_turn_record_json_one_canonical_population_and_page():
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
        page = dispatch("result_read", handle=screen["handle"], offset=5, limit=2)
        h = screen["handle"]
        stored = load_handle(rec, h)
    blob = json.dumps(session)
    assert len(blob) < 80_000
    reloaded = json.loads(blob)
    rows = reloaded["turn_records"][tid]
    assert set(rows[0]["exact"]) == {"display", "data"}
    assert isinstance(rows[0]["exact"]["display"], str) and rows[0]["exact"]["display"]
    assert rows[0]["source_basis"] == small["source_basis"]
    assert rows[0]["exact"]["data"]["success"] is True
    assert "exact" not in rows[1]
    assert rows[1]["handle"] == h
    assert rows[1]["source_basis"] == screen["source_basis"]
    assert "ranked_candidates" in reloaded["handles"][h]["exact"]
    assert reloaded["handles"][h]["display"] == stored["display"]
    assert isinstance(reloaded["handles"][h]["display"], str) and reloaded["handles"][h]["display"]
    assert "ranked_candidates" not in json.dumps(reloaded["turn_records"])
    assert rows[2]["handle"] == h
    assert rows[2]["exact"]["offset"] == 5
    assert rows[2]["exact"]["returned"] == 2
    assert len(rows[2]["exact"]["data"]["rows"]) == 2
    assert rows[2]["exact"]["data"]["rows"] == stored["exact"]["ranked_candidates"][5:7]
    reloaded["handles"][h]["exact"]["ranked_candidates"] = []
    assert json.loads(blob)["handles"][h]["exact"]["ranked_candidates"]
    assert len(rows[2]["exact"]["data"]["rows"]) == 2
    assert len(json.dumps(reloaded["turn_records"][tid][1])) < 2048


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
    assert set(rows[0]["exact"]) == {"display", "data"}
    assert rows[0]["exact"]["data"]["success"] is True
    assert rows[0]["source_basis"] == "cosmo_rs_grid"
    assert result.tool_trace[0].result.get("data") is rows[0]["exact"]["data"]
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


def test_missing_provider_key_names_the_environment_variable(monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    result = run_turn(
        "q", session=new_session(), model="openai:muse-spark-1.2",
        api_base="https://api.meta.ai/v1", api_key_env="META_MUSE_API_KEY",
    )
    assert result.status == "provider_error"
    assert result.answer == "missing environment variable META_MUSE_API_KEY"
    assert "OPENAI_API_KEY" not in result.answer
    monkeypatch.setenv("META_MUSE_API_KEY", "   ")
    blank = run_turn(
        "q", session=new_session(), model="openai:muse-spark-1.2",
        api_key_env="META_MUSE_API_KEY",
    )
    assert blank.status == "provider_error"
    assert blank.answer == "missing environment variable META_MUSE_API_KEY"
    monkeypatch.delenv("NONEXISTENT_AUDIT_KEY", raising=False)
    ghost = run_turn(
        "q", session=new_session(), model="openai:muse-spark-1.2",
        api_key_env="NONEXISTENT_AUDIT_KEY",
    )
    assert ghost.status == "provider_error"
    assert ghost.answer == "missing environment variable NONEXISTENT_AUDIT_KEY"
    assert "OPENAI_API_KEY" not in ghost.answer


def test_unknown_prefix_and_omitted_key_env_do_not_invent_api_key_env(monkeypatch):
    missing = run_turn("q", session=new_session(), model="nope:x")
    assert missing.status == "provider_error"
    assert "unknown model prefix" in missing.answer
    assert "api_key_env" not in missing.answer
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_ADMIN_KEY", raising=False)
    omitted = run_turn("q", session=new_session(), model="openai:x")
    assert omitted.status == "provider_error"
    assert "api_key_env" not in omitted.answer


def test_omitted_key_env_does_not_pass_empty_string_to_anthropic(monkeypatch):
    import types
    captured = {}
    built = {"n": 0}

    class FakeMessages:
        def create(self, **kwargs):
            raise RuntimeError("no network")

    class FakeAnthropic:
        def __init__(self, **kwargs):
            built["n"] += 1
            captured.update(kwargs)
            self.messages = FakeMessages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "native-secret")
    omitted = run_turn("q", session=new_session(), model="anthropic:claude-x")
    assert omitted.status == "provider_error"
    assert built["n"] == 1
    assert captured.get("api_key") is None
    monkeypatch.setenv("EXPLICIT_ANTHROPIC_KEY", "named-secret")
    captured.clear()
    built["n"] = 0
    named = run_turn(
        "q", session=new_session(), model="anthropic:claude-x",
        api_key_env="EXPLICIT_ANTHROPIC_KEY",
    )
    assert named.status == "provider_error"
    assert built["n"] == 1
    assert captured.get("api_key") == "named-secret"


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


def _production_tool_msgs(old="old " + ("x" * 4000), query="CURRENT-QUERY"):
    """Shape at compact time: last role is tool. No later user/prose."""
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": old},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": query},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "name": "t", "args": {}}]},
        {"role": "tool", "tool_call_id": "1", "name": "t", "content": "{}"},
    ]


def test_compact_postcondition_holds_on_production_timing():
    rec = new_session()
    rec["handles"]["calm-blue-cat"] = {
        "tool": "solubility_query", "source_basis": "cosmo_rs_grid",
        "exact": {"results": [{"solvent": "dodecane", "solubility_wt_pct": 92.5}]},
    }
    rec["reported"] = [{"number": 92.5, "source_basis": "cosmo_rs_grid", "handle": "calm-blue-cat"}]
    rec["polymers_in_play"] = ["LDPE"]
    rec["temperatures_in_play"] = [140.0]
    msgs = _production_tool_msgs()
    compact_messages(msgs, rec, window=400, reserve=0)
    assert estimated_tokens(msgs) <= 400
    assert msgs[0]["role"] == "system"
    assert str(msgs[1].get("content") or "").startswith("Summary (do not continue the conversation")
    assert "LDPE" in msgs[1]["content"]
    assert "calm-blue-cat" in msgs[1]["content"]
    assert msgs[-1]["role"] == "tool"
    assert msgs[2]["role"] == "user" and "CURRENT-QUERY" in str(msgs[2].get("content"))
    assert msgs[3].get("tool_calls")
    compact_messages(msgs, rec, window=400, reserve=0)
    assert sum(1 for m in msgs if str(m.get("content") or "").startswith("Summary (do not continue")) == 1


def test_compact_refuses_when_reported_template_cannot_fit():
    rec = new_session()
    rec["reported"] = [{"number": i, "source_basis": "cosmo_rs_grid"} for i in range(1000)]
    rec["polymers_in_play"] = ["LDPE"]
    msgs = _production_tool_msgs()
    with pytest.raises(CompactionBudgetError, match="numbers already reported"):
        compact_messages(msgs, rec, window=400, reserve=0)
    rec_real = new_session()
    rec_real["reported"] = [{"number": i, "source_basis": "cosmo_rs_grid"} for i in range(20_000)]
    with pytest.raises(CompactionBudgetError):
        compact_messages(
            _production_tool_msgs(old="old " + ("x" * 490_000)), rec_real,
            window=128_000, reserve=8_000,
        )
    rec_poly = new_session()
    rec_poly["polymers_in_play"] = ["P" + ("z" * 1000) + str(i) for i in range(600)]
    with pytest.raises(CompactionBudgetError):
        compact_messages(
            _production_tool_msgs(old="old " + ("x" * 490_000)), rec_poly,
            window=128_000, reserve=8_000,
        )


def test_context_window_defaults_only_when_alias_omits_it():
    assert context_window("openai:muse-spark-1.2") == 128_000
    assert context_window("openai:foo-8k") == 8_000
    assert context_window("openai:foo-256k") == 256_000


def test_compaction_does_not_read_turn_records():
    assert "turn_records" not in inspect.getsource(sess._summary_text)
    assert "turn_records" not in inspect.getsource(sess.compact_messages)
    assert "turn_records" not in inspect.getsource(sess.append_reported)
    assert "turn_records" not in inspect.getsource(sess.note_in_play)
    assert _archive_readers_in_loop_graph() == []


def test_reachable_compact_does_not_consume_archive_rows(monkeypatch):
    session = new_session()
    session["turn_records"] = ArchiveTrap()
    result, rounds = _forced_compact_turn(monkeypatch, session)
    assert result.status == "ok"
    assert rounds == 2
    trap = session["turn_records"]
    assert isinstance(trap, ArchiveTrap)
    assert len(trap) >= 1
    with pytest.raises(ArchiveReadError, match="item access"):
        trap[result.turn_record]
    with pytest.raises(ArchiveReadError, match=" via get"):
        trap.get(result.turn_record)
    with pytest.raises(ArchiveReadError, match="values"):
        trap.values()
    with pytest.raises(ArchiveReadError, match="items"):
        trap.items()
    with pytest.raises(ArchiveReadError, match="iter"):
        iter(trap)


def test_wrapper_appends_reported_from_row_fields():
    with _bound() as rec:
        dispatch(
            "solubility_query",
            polymers=["LDPE"], solvents=["dodecane"], temperatures=[140.0],
        )
        assert rec["reported"]
        assert all("number" in row and "source_basis" in row for row in rec["reported"])
        assert rec["reported"][0]["source_basis"] == "cosmo_rs_grid"
        assert "shown" not in {row.get("number") for row in rec["reported"]}
        assert "LDPE" in rec["polymers_in_play"]
        assert 140.0 in rec["temperatures_in_play"]


def _eligible_reported_ids(payload):
    skip = {"shown", "total", "available_count", "returned", "offset", "available", "success"}
    rows = []
    if isinstance(payload.get("top"), list):
        rows.extend(r for r in payload["top"] if isinstance(r, dict))
    data = payload.get("data")
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and all(isinstance(i, dict) for i in value):
                rows.extend(value)
    basis, handle = payload.get("source_basis"), payload.get("handle")
    ids = set()
    for row in rows:
        for key, value in row.items():
            if key in skip or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            ids.add((value, basis, handle))
    return ids


def test_result_read_later_page_appends_reported():
    with _bound() as rec:
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        first = _eligible_reported_ids(screen)
        before = len(rec["reported"])
        page = dispatch("result_read", handle=screen["handle"], offset=20, limit=20)
        assert page["returned"] == 20
        assert len(rec["reported"]) > before
        later = _eligible_reported_ids(page)
        have = {
            (row.get("number"), row.get("source_basis"), row.get("handle"))
            for row in rec["reported"]
        }
        assert first
        assert later
        assert first <= have
        assert later <= have
        assert len(have) >= len(first | later)


def test_append_reported_skips_count_fields():
    rec = new_session()
    with bind_tool_session(rec) as bound:
        append_reported(bound, {
            "source_basis": "cosmo_rs_grid",
            "handle": "calm-blue-cat",
            "shown": 20,
            "total": 100,
            "available_count": 100,
            "top": [{
                "solvent": "dodecane", "solubility_wt_pct": 92.5,
                "shown": 20, "total": 100, "available_count": 3,
            }],
        })
    numbers = [row["number"] for row in rec["reported"]]
    assert numbers == [92.5]
    assert rec["reported"][0]["handle"] == "calm-blue-cat"


def test_append_reported_dedupes_overlapping_page_identities():
    rec = new_session()
    payload = {
        "source_basis": "cosmo_rs_grid",
        "handle": "calm-blue-cat",
        "top": [{"rank": 1, "solubility_wt_pct": 92.5}],
    }
    with bind_tool_session(rec) as bound:
        append_reported(bound, payload)
        n = len(bound["reported"])
        append_reported(bound, payload)
        assert len(bound["reported"]) == n


def test_in_play_keys_come_from_registered_signatures():
    from dissolve import registry
    poly, temp = sess._subject_arg_names()
    found_poly, found_temp = set(), set()
    for spec in registry.REGISTRY:
        for name in inspect.signature(spec.fn).parameters:
            low = name.lower()
            if "polymer" in low:
                found_poly.add(name)
            if "temp" in low:
                found_temp.add(name)
    assert found_poly == set(poly)
    assert found_temp == set(temp)
    assert "polymer_query" in poly
    assert "operating_temperature_c" in temp


def test_lookup_glass_transition_notes_polymer_query():
    with _bound() as rec:
        out = dispatch("lookup_glass_transition", polymer_query="LDPE")
        assert out["available"] is True
        assert "LDPE" in rec["polymers_in_play"]
        assert "LDPE" in sess._summary_text(rec)
        assert "(none)" not in sess._summary_text(rec).split("Polymers in play:")[1].splitlines()[0]


def test_data_scope_notes_operating_temperature():
    with _bound() as rec:
        dispatch(
            "resolve_polymer_data_scope",
            feed_polymers=["LDPE"], operating_solvent="dodecane",
            operating_temperature_c=140.0,
        )
        assert "LDPE" in rec["polymers_in_play"]
        assert 140.0 in rec["temperatures_in_play"]


def test_run_turn_production_timing_keeps_current_group_and_meets_budget(monkeypatch):
    n = {"i": 0}
    windows = []
    history = [
        {"role": "user", "content": "OLD " + ("x" * 490_000)},
        {"role": "assistant", "content": "old answer"},
    ]

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}]}
        assert messages[-1]["role"] == "tool"
        lead = 2 if str(messages[1].get("content") or "").startswith("Summary") else 1
        assert messages[lead]["role"] == "user"
        assert "CURRENT-QUERY" in str(messages[lead].get("content"))
        assert not (
            messages[lead].get("role") == "assistant" and messages[lead].get("tool_calls")
        )
        target = context_window("openai:muse-spark-1.2") - 8_000
        assert estimated_tokens(messages) <= target
        return {"text": "done", "tool_calls": []}

    def spy(messages, record, **kwargs):
        windows.append(kwargs.get("window"))
        assert messages[-1].get("role") == "tool"
        compact_messages(messages, record, **kwargs)
        return None

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    monkeypatch.setattr(agent_harness, "compact_messages", spy)
    result = run_turn(
        "CURRENT-QUERY", session=new_session(),
        model="openai:muse-spark-1.2", messages=history,
    )
    assert result.status == "ok"
    assert n["i"] == 2
    assert windows == [128_000]
    assert history[-1]["role"] == "assistant"
    users = [m for m in history if m.get("role") == "user"]
    assert any("CURRENT-QUERY" in str(u.get("content")) for u in users)
    assert not any(str(u.get("content", "")).startswith("OLD ") for u in users)


def test_run_turn_counts_rounds_not_history_delta(monkeypatch):
    n = {"i": 0}
    history = [{"role": "system", "content": "sys"}]
    for i in range(6):
        history.extend([
            {"role": "user", "content": f"old-{i}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"old-{i}", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": f"old-{i}", "name": "no_such_tool", "content": "{}"},
            {"role": "assistant", "content": f"ans-{i}"},
        ])

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [{"id": "now", "name": "no_such_tool", "args": {}}]}
        return {"text": "done", "tool_calls": []}

    def fake_compact(messages, record, **kwargs):
        start = next(i for i, msg in enumerate(messages) if msg.get("role") == "user")
        end = next(
            i for i in range(start + 1, len(messages)) if messages[i].get("role") == "user"
        )
        del messages[start:end]

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    monkeypatch.setattr(agent_harness, "compact_messages", fake_compact)
    before = sum(1 for m in history if m.get("role") == "assistant" and m.get("tool_calls"))
    session = new_session()
    result = run_turn("NOW", session=session, model="openai:x", messages=history)
    after = sum(1 for m in history if m.get("role") == "assistant" and m.get("tool_calls"))
    assert before == 6
    assert after - before == 0
    assert result.status == "ok"
    assert result.tool_rounds == 1
    assert result.usage is None
    assert "tool_rounds" not in session
    assert "provider_tokens" not in session
    assert "usage" not in session
    assert len(result.tool_trace) == 1


def test_usage_reads_each_adapter_shape_and_keeps_absent_distinct_from_zero():
    from types import SimpleNamespace
    zero = SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    assert agent_harness._usage("openai", SimpleNamespace(usage=SimpleNamespace(total_tokens=18))) == {
        "total_tokens": 18,
    }
    assert agent_harness._usage(
        "anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=8)),
    ) == {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}
    assert agent_harness._usage(
        "google_genai", SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=3, candidates_token_count=5, total_token_count=8,
        )),
    ) == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    assert agent_harness._usage("openai", SimpleNamespace(usage=None)) is None
    assert agent_harness._usage("openai", SimpleNamespace()) is None
    assert agent_harness._usage("openai", SimpleNamespace(usage=SimpleNamespace())) is None
    assert agent_harness._usage("anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=10))) == {
        "input_tokens": 10,
    }
    assert agent_harness._usage("openai", SimpleNamespace(usage=zero)) == {
        "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
    }


def test_complete_keeps_openai_usage(monkeypatch):
    from types import SimpleNamespace
    import openai
    msg = SimpleNamespace(content="done", tool_calls=[])
    resp = SimpleNamespace(usage=SimpleNamespace(total_tokens=18), choices=[SimpleNamespace(message=msg)])
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **k: resp))
    monkeypatch.setattr(openai, "OpenAI", Client)
    monkeypatch.setenv("K", "x")
    out = agent_harness.complete(
        [{"role": "user", "content": "q"}], [], model="openai:x", api_key_env="K",
    )
    assert out["text"] == "done"
    assert out["tool_calls"] == []
    assert out["usage"] == {"total_tokens": 18}


def test_run_turn_folds_usage_and_does_not_write_session_keys(monkeypatch):
    n = {"i": 0}

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {
                "text": "", "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}],
                "usage": {"total_tokens": 7},
            }
        return {"text": "done", "tool_calls": [], "usage": {"total_tokens": 11}}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    session = new_session()
    result = run_turn("q", session=session, model="openai:x")
    assert result.status == "ok"
    assert result.tool_rounds == 1
    assert result.usage == {"total_tokens": 18}
    assert "tool_rounds" not in session
    assert "provider_tokens" not in session
    assert "usage" not in session


def test_run_turn_usage_is_none_if_any_round_omits_it(monkeypatch):
    n = {"i": 0}

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {
                "text": "", "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}],
                "usage": {"total_tokens": 7},
            }
        return {"text": "done", "tool_calls": [], "usage": None}

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    result = run_turn("q", session=new_session(), model="openai:x")
    assert result.status == "ok"
    assert result.usage is None


def test_run_turn_passes_alias_window(monkeypatch):
    n = {"i": 0}
    hits = []

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [{"id": "1", "name": "no_such_tool", "args": {}}]}
        return {"text": "done", "tool_calls": []}

    def spy(messages, record, **kwargs):
        hits.append((messages[-1].get("role"), kwargs.get("window")))
        return compact_messages(messages, record, **kwargs)

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    monkeypatch.setattr(agent_harness, "compact_messages", spy)
    result = run_turn("q", session=new_session(), model="openai:foo-8k")
    assert result.status == "compaction_error"
    assert "target=" in result.answer
    assert "estimated_tokens=" in result.answer
    assert "summary needs" in result.answer
    assert hits == [("tool", 8000)]


def test_compaction_error_matches_every_emitted_tool_id(monkeypatch):
    n = {"i": 0}
    history = []
    session = new_session()
    compact_ids = []

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [
                {"id": "call-1", "name": "no_such_tool", "args": {"n": 1}},
                {"id": "call-2", "name": "no_such_tool", "args": {"n": 2}},
            ]}
        raise AssertionError("provider called a second time")

    def spy(messages, record, **kwargs):
        compact_ids.append([m.get("tool_call_id") for m in messages if m.get("role") == "tool"])
        return compact_messages(messages, record, **kwargs)

    monkeypatch.setattr(agent_harness, "complete", fake_complete)
    monkeypatch.setattr(agent_harness, "compact_messages", spy)
    result = run_turn("q", session=session, model="openai:foo-8k", messages=history)
    assert result.status == "compaction_error"
    assert n["i"] == 1
    assert compact_ids == [["call-1", "call-2"]]
    assert [ev.name for ev in result.tool_trace] == ["no_such_tool", "no_such_tool"]
    emitted, received = [], []
    for msg in history:
        if msg.get("role") == "assistant":
            emitted.extend(c.get("id") for c in (msg.get("tool_calls") or []) if c.get("id"))
        if msg.get("role") == "tool":
            received.append(msg.get("tool_call_id"))
    assert emitted == ["call-1", "call-2"]
    assert received == emitted
    rows = load_turn_record(session, result.turn_record)
    assert [row["tool"] for row in rows] == ["no_such_tool", "no_such_tool"]
    oai = agent_harness._oai_msgs(history)
    oai_call = [c["id"] for m in oai for c in (m.get("tool_calls") or [])]
    oai_resp = [m.get("tool_call_id") for m in oai if m.get("role") == "tool"]
    assert oai_call == oai_resp == emitted
    _, ant = agent_harness._ant_msgs(history)
    uses, tres = [], []
    for msg in ant:
        blocks = msg.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if block.get("type") == "tool_use":
                uses.append(block["id"])
            if block.get("type") == "tool_result":
                tres.append(block.get("tool_use_id"))
    assert uses == tres == emitted
    calls = frs = 0
    for content in agent_harness._gen_contents(history):
        for part in content.parts:
            fc, fr = getattr(part, "function_call", None), getattr(part, "function_response", None)
            if fc is not None and getattr(fc, "name", None):
                calls += 1
            if fr is not None and getattr(fr, "name", None):
                frs += 1
    assert calls == frs == 2


def test_session_record_missing_reads_none_writes_raise():
    rec = sess.SessionRecord()
    rec["last_tea"] = {"analysis_type": "route"}
    assert rec.last_tea is None
    assert rec["last_tea"]["analysis_type"] == "route"
    with pytest.raises(AttributeError, match="set keys, not attributes"):
        rec.last_route = {"steps": [{"solvent": "dodecane"}]}
    assert "last_route" not in rec
    session = sess.new_session()
    with sess.bind_tool_session(session) as bound:
        with pytest.raises(AttributeError, match="set keys, not attributes"):
            bound.last_candidates = [{"solvent": "copied-projection"}]
    assert "last_candidates" not in session
