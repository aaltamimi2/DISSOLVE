"""Agent tests."""
from __future__ import annotations

import ast
import inspect
import io
import json
import math
import re
import sys
import textwrap
from contextlib import contextmanager
from pathlib import Path

import pytest
from rich.console import Console

from dissolve import agent, cli, contaminants, research, tea
from dissolve import session as sess
from dissolve.agent import (
    LITERATURE_AGENT_TOOLS,
    LITERATURE_CORPUS_TOOLS,
    LITERATURE_INGEST_TOOLS,
    LITERATURE_MODE_SURFACE,
    LITERATURE_NETWORK_TOOLS,
    LITERATURE_SCHOLARLY_TOOLS,
    SYSTEM_PROMPT,
    UNWIRED,
    ToolEvent,
    TurnResult,
    dispatch,
    literature_agent_mode,
    offered_tool_names,
    result_read,
    run_turn,
    source_basis_for,
    tool_schema_for,
    tool_schemas,
)
from dissolve.cli import (
    EXPECTED_REGISTRY_NAMES,
    CliApp,
    _format_literature_default,
    _parse_breadth_slash,
    _parse_literature_slash,
    doctor_report,
    main,
    resolve_model,
)
from dissolve.contracts import parse_tool_result
from dissolve.session import (
    CompactionBudgetError,
    append_reported,
    bind_tool_session,
    compact_messages,
    context_window,
    current_tool_session,
    estimated_tokens,
    handle_rows,
    load_handle,
    load_turn_record,
    new_session,
    open_turn_record,
    primary_row_key,
    store_handle,
)
from dissolve.thermodynamics import get_available_solvents


@pytest.fixture(autouse=True)
def _live_tea_works(monkeypatch, tmp_path):
    """TEA answers only when live TEA works; these tests stand in a working engine (tests of the check itself
    override this) and never see this checkout's own live environment (.venv-tea, vendor/plastics)."""
    monkeypatch.setattr(tea, "_live_tea_blocker", lambda: None)
    monkeypatch.setattr(tea, "_REPO_TEA_PYTHON", tmp_path / "no-venv-tea" / "python")
    monkeypatch.setattr(tea.tea_polymer_parameters, "VENDORED_PLASTICS", tmp_path / "no-vendored-plastics")

# --- from test_acceptance.py: §10 acceptance tests. Stub the provider. Test 5 is identity-sensitive.
_real_bind = agent.bind_handle_rows


Q1 = "What is the solubility of LDPE in dodecane at 140 C?"


Q2 = "What is the solubility of LDPE in dodecane at 137.3 C?"


Q3 = "What is the solubility of LDPE in dodecanel at 140 C?"


Q4 = "What is the solubility of polyethylene in dodecane at 140 C?"


Q5 = (
    "Screen solvents for separating LDPE from PP between 80 C and 140 C, "
    "then compare the safety of that shortlist at the temperatures the screen used."
)


Q6A = "Screen solvents for dissolving LDPE from 25 C to 160 C."


Q6B = "What is the third solvent on that shortlist?"


Q7 = (
    "Run TEA/LCA for HDPE in dodecane at a processing capacity of 1 Mt/yr "
    "and energy case C1. If the cache misses, say so."
)


Q_GAP = (
    "Screen solvents for separating polyethylene from PP, then HSP-join "
    "and TEA-cost a member of that shortlist at the temperatures the screen used."
)


Q_PAIR = (
    "HSP-join LDPE in dodecane at 145 C, then TEA-cache LDPE in dodecane "
    "at 20000 t/yr, 145 C, energy case C1."
)


def _solvents(rows):
    names = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("solvent") or row.get("solvent_name") or "").strip()
        if name:
            names.append(name)
    return names


def _unique(names):
    out, seen = [], set()
    for name in names:
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


def _ids(names):
    return {name.casefold() for name in names if name}


def _tool_json(messages, name):
    for msg in reversed(messages):
        if msg.get("role") == "tool" and msg.get("name") == name:
            return json.loads(msg["content"])
    raise AssertionError(f"no tool result for {name}")


def _complete_ldpe_dodecane_c1_cache_scenario(**overrides):
    record = next(
        item for item in tea._records()
        if str(item["config"]["target_plastic"]).casefold() == "ldpe"
        and "dodecane" in str(item["config"]["solvent"]).casefold()
        and str(item["config"]["energy_case"]).upper() == "C1"
        and abs(float(item["config"]["processing_capacity"]) - 20_000.0) < 1e-9
        and abs(float(item["config"]["dissolution_temperature_c"]) - 145.0) < 1e-9
    )
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temp_c": cfg["dissolution_temperature_c"],
        "precipitation_temp_c": cfg["precipitation_temperature_c"],
        "solvent_price": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def _safety_names(payload):
    if payload.get("top"):
        return _solvents(payload["top"])
    data = payload.get("data") or {}
    return _solvents(
        data.get("comparison_rows") or data.get("candidate_conditions") or []
    )


def _named_roster_solvents(text):
    """Identity-bounded, then longest non-overlapping, registry names."""
    lower = text.casefold()
    n = len(lower)

    def bounded(start, end):
        left = start == 0 or not lower[start - 1].isalnum()
        right = end == n or not lower[end].isalnum()
        return left and right

    spans = []
    for name in get_available_solvents():
        needle = name.casefold()
        cursor = 0
        while True:
            i = lower.find(needle, cursor)
            if i < 0:
                break
            j = i + len(needle)
            if bounded(i, j):
                spans.append((i, j, name, len(needle)))
            cursor = i + 1
    spans.sort(key=lambda item: (-item[3], item[0]))
    occupied = []
    found = []
    for start, end, name, _length in spans:
        if any(start < taken_end and end > taken_start for taken_start, taken_end in occupied):
            continue
        occupied.append((start, end))
        found.append(name)
    return _unique(found)


def _assert_inherited_identities(exact_ids, bound_ids):
    exact, bound = _ids(exact_ids), _ids(bound_ids)
    assert bound == exact, (
        "inherited solvent identities "
        f"({len(bound)} unique {sorted(bound)[:8]}) != exact set "
        f"({len(exact)} unique {sorted(exact)[:8]})"
    )


def _assert_coverage_disclosed(answer, compared, stored):
    compared, stored = int(compared), int(stored)
    if compared < stored:
        pat = rf"(?<!\d){compared} of {stored}(?!\d)"
        assert re.search(pat, answer), (
            f"answer omitted {compared} of {stored} coverage "
            "and presented a comparison page as the shortlist"
        )
        assert "comparison page is not the full shortlist" in answer, (
            "answer omitted the comparison-page disclosure "
            "and can still present the page as the inherited shortlist"
        )


def _assert_answer_stays_inside_exact(answer, exact_ids, must_name):
    exact = _ids(exact_ids)
    for name in must_name:
        assert name in answer, f"printed answer omitted {name!r}"
    foreign = [
        name for name in _named_roster_solvents(answer)
        if name.casefold() not in exact
    ]
    assert foreign == [], f"printed answer named solvents outside the exact rows: {foreign}"


@contextmanager
def _bind_copies_of_first_row(record, handle):
    stored = load_handle(record, handle)
    if stored is None:
        raise KeyError(handle)
    rows = handle_rows(stored)
    copies = [dict(rows[0]) for _ in range(len(rows))]
    source = {"handle": handle, "total": len(copies), "source_tool": stored["tool"]}
    try:
        record["last_candidates"] = copies
        record["last_candidates_source"] = source
        yield
    finally:
        record.pop("last_candidates", None)
        record.pop("last_candidates_source", None)


def _spy_bind(bucket, inner):
    @contextmanager
    def spy(record, handle):
        with inner(record, handle):
            bucket.append(_solvents(record.get("last_candidates") or []))
            yield
    return spy


def _play(monkeypatch, steps):
    n = {"i": 0}

    def fake(messages, tools, **kwargs):
        if n["i"] >= len(steps):
            raise AssertionError("complete called past the script")
        step = steps[n["i"]]
        n["i"] += 1
        return step(messages) if callable(step) else step

    monkeypatch.setattr(agent, "complete", fake)
    return n


def _turn(query, session, history):
    return run_turn(query, session=session, model="openai:stub", messages=history)


def _history():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _forty_copy_probe():
    """Auditor bind: 40 copies of one valid screen row. Counts pass; identities do not."""
    bound_ids = []
    session = new_session()
    agent.bind_handle_rows = _spy_bind(bound_ids, _bind_copies_of_first_row)
    try:
        with bind_tool_session(session) as rec:
            screen = dispatch(
                "screen_polymer_separation",
                feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
                temperature_max_c=140.0, top_k=20,
            )
            handle, total = screen["handle"], screen["total"]
            exact_ids = _unique(_solvents(handle_rows(load_handle(rec, handle))))
            safety = dispatch(
                "compare_solvent_safety_at_conditions",
                handle=handle, include_pubchem=False,
            )
            data = safety.get("data") or {}
            return {
                "handle": handle, "total": total, "shown": screen["shown"],
                "exact_ids": exact_ids, "bound_ids": bound_ids[-1],
                "safety_ids": _unique(_safety_names(safety)),
                "stored_count": data.get("candidate_scope_stored_count"),
                "candidate_count": data.get("candidate_count"),
                "transient": "last_candidates" in rec,
            }
    finally:
        agent.bind_handle_rows = _real_bind


def test_acceptance_5_identity_predicate_is_red_on_forty_copies_of_one_row():
    """Not Test 5. The bind Test 5 must fail on. Count guards still pass."""
    probe = _forty_copy_probe()
    assert probe["stored_count"] == probe["total"] == 40
    assert probe["candidate_count"] != probe["total"]
    assert probe["transient"] is False
    assert len(_unique(probe["safety_ids"])) == 1
    assert len(probe["exact_ids"]) == 40
    with pytest.raises(AssertionError, match="inherited solvent identities"):
        _assert_inherited_identities(probe["exact_ids"], probe["bound_ids"])


def test_acceptance_5_screen_then_safety_identities_and_answer(monkeypatch):
    bound_ids = []
    monkeypatch.setattr(
        "dissolve.agent.bind_handle_rows",
        _spy_bind(bound_ids, _real_bind),
    )

    def safety_call(messages):
        screen = _tool_json(messages, "screen_polymer_separation")
        return {
            "text": "",
            "tool_calls": [{
                "id": "s2", "name": "compare_solvent_safety_at_conditions",
                "args": {"handle": screen["handle"], "include_pubchem": False},
            }],
        }

    def answer(messages):
        safety = _tool_json(messages, "compare_solvent_safety_at_conditions")
        names = _unique(_safety_names(safety))
        data = safety.get("data") or {}
        compared = data["candidate_count"]
        stored = data["candidate_scope_stored_count"]
        return {
            "text": (
                f"Safety of {compared} of {stored} inherited solvents "
                f"(source_basis safety_local); the comparison page is not the full shortlist: "
                f"{', '.join(names)}."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "s1", "name": "screen_polymer_separation",
            "args": {
                "feed_polymers": ["LDPE", "PP"],
                "temperature_min_c": 80.0, "temperature_max_c": 140.0, "top_k": 20,
            },
        }]},
        safety_call,
        answer,
    ])
    session, history = new_session(), _history()
    result = _turn(Q5, session, history)
    assert result.status == "ok"
    screen_ev, safety_ev = result.tool_trace[0], result.tool_trace[1]
    assert screen_ev.name == "screen_polymer_separation"
    assert safety_ev.name == "compare_solvent_safety_at_conditions"
    handle = screen_ev.result["handle"]
    total, shown = screen_ev.result["total"], screen_ev.result["shown"]
    assert handle and total > shown
    assert safety_ev.args.get("handle") == handle
    assert not (
        not safety_ev.args.get("handle") and safety_ev.args.get("candidates") is not None
    )
    exact_ids = _unique(_solvents(handle_rows(load_handle(session, handle))))
    assert len(exact_ids) == total
    assert bound_ids, "safety never inherited a handle bind"
    _assert_inherited_identities(exact_ids, bound_ids[-1])
    data = safety_ev.result.get("data") or {}
    stored = data["candidate_scope_stored_count"]
    compared = data["candidate_count"]
    safety_ids = _unique(_safety_names(safety_ev.result))
    assert safety_ids, "safety ran on an empty list"
    assert stored == total
    assert compared == len(safety_ids)
    assert compared < stored
    assert _ids(safety_ids) <= _ids(exact_ids)
    _assert_coverage_disclosed(result.answer, compared, stored)
    _assert_answer_stays_inside_exact(result.answer, exact_ids, safety_ids)


@pytest.mark.parametrize(
    ('value', 'match'),
    [
        pytest.param('Safety of the inherited shortlist at the screen temperatures (source_basis safety_local): cyclohexane.', 'coverage', id='when_page_is_the_shortlist'),
        pytest.param('The full inherited shortlist consists of these 6 of 40 solvents.', 'comparison-page disclosure', id='on_full_shortlist_with_counts'),
    ],
)
def test_acceptance_5_coverage_predicate_is_red(value, match):
    with pytest.raises(AssertionError, match=match):
        _assert_coverage_disclosed(
            value,
            compared=6, stored=40,
        )


def test_acceptance_1_point_lookup_on_grid(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "solubility_query")
        row = payload["data"]["results"][0]
        return {
            "text": (
                f"LDPE in dodecane at 140 C is {row['solubility_pct']} wt% "
                f"(source_basis {payload['source_basis']})."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "q", "name": "solubility_query",
            "args": {"polymers": ["LDPE"], "solvents": ["dodecane"], "temperatures": [140.0]},
        }]},
        answer,
    ])
    result = _turn(Q1, new_session(), _history())
    assert result.status == "ok"
    assert len(result.tool_trace) == 1
    ev = result.tool_trace[0]
    assert ev.name == "solubility_query"
    assert ev.result["available"] is True
    assert ev.result["source_basis"] == "cosmo_rs_grid"
    pct = ev.result["data"]["results"][0]["solubility_pct"]
    assert str(pct) in result.answer
    assert "cosmo_rs_grid" in result.answer
    assert "interpolat" not in result.answer.lower()


def test_acceptance_2_off_grid_temperature_refuses_without_a_value(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "solubility_query")
        nodes = payload["data"]["nearest_grid_temperatures_c"][0]["nearest_nodes_c"]
        return {
            "text": (
                f"Refusal {payload['refusal']} at 137.3 C. "
                f"Neighbouring nodes: {nodes[0]} and {nodes[1]}. "
                "No solubility at 137.3."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "q", "name": "solubility_query",
            "args": {"polymers": ["LDPE"], "solvents": ["dodecane"], "temperatures": [137.3]},
        }]},
        answer,
    ])
    result = _turn(Q2, new_session(), _history())
    assert result.status == "ok"
    ev = result.tool_trace[0]
    assert ev.result["available"] is False
    assert ev.result["refusal"] == "temperature_off_grid"
    data = ev.result["data"]
    nodes = data["nearest_grid_temperatures_c"][0]["nearest_nodes_c"]
    assert "temperature_off_grid" in result.answer
    assert str(nodes[0]) in result.answer and str(nodes[1]) in result.answer
    assert "wt%" not in result.answer.lower()
    assert "solubility_pct" not in result.answer
    for row in (data.get("results") or []):
        raise AssertionError(f"off-grid payload carried a value: {row}")


def test_acceptance_3_unregistered_solvent_does_not_substitute(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "solubility_query")
        detail = (payload["data"].get("unsupported_solvent_details") or [{}])[0]
        cands = payload.get("near_miss_solvents") or []
        return {
            "text": (
                f"Refusal {payload['refusal']}. "
                f"identity {detail.get('solvent_identity_status')} "
                f"known_solvent_identity {detail.get('known_solvent_identity')}. "
                f"candidates {cands}. No solubility."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "q", "name": "solubility_query",
            "args": {"polymers": ["LDPE"], "solvents": ["dodecanel"], "temperatures": [140.0]},
        }]},
        answer,
    ])
    result = _turn(Q3, new_session(), _history())
    assert result.status == "ok"
    ev = result.tool_trace[0]
    assert ev.result["available"] is False
    assert ev.result["refusal"] == "unknown_solvents"
    detail = ev.result["data"]["unsupported_solvent_details"][0]
    assert detail["solvent_identity_status"] == "not_found"
    assert detail["known_solvent_identity"] is None
    cands = ev.result.get("near_miss_solvents") or []
    assert len(cands) <= 5
    assert "unknown_solvents" in result.answer
    assert "wt%" not in result.answer.lower()
    assert "dodecane at 140" not in result.answer.lower()


def test_acceptance_4_polyethylene_means_ldpe(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "solubility_query")
        if payload.get("available"):
            rows = payload["data"]["results"]
            parts = [
                f"{row['polymer']} {row['solubility_pct']} wt% "
                f"(source_basis {payload['source_basis']})"
                for row in rows
            ]
            return {"text": "Both members: " + "; ".join(parts) + ".", "tool_calls": []}
        roster = payload["data"].get("polymer_members") or payload["data"].get("roster") or []
        return {
            "text": f"Refusal {payload.get('refusal')} members {roster}.",
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "q", "name": "solubility_query",
            "args": {
                "polymers": ["polyethylene"], "solvents": ["dodecane"],
                "temperatures": [140.0],
            },
        }]},
        answer,
    ])
    result = _turn(Q4, new_session(), _history())
    assert result.status == "ok"
    ev = result.tool_trace[0]
    answer = result.answer
    # Owner rule (2026-09-23): polyethylene means LDPE unless the user names HDPE.
    assert ev.result.get("available") is True
    assert [row["polymer"] for row in ev.result["data"]["results"]] == ["LDPE"]
    assert "LDPE" in answer and "HDPE" not in answer
    assert str(ev.result["data"]["results"][0]["solubility_pct"]) in answer


def test_acceptance_6_large_screen_handle_and_follow_up(monkeypatch):
    def follow(messages):
        screen = _tool_json(messages, "screen_polymer_separation")
        return {
            "text": "",
            "tool_calls": [{
                "id": "r", "name": "result_read",
                "args": {"handle": screen["handle"], "offset": 0, "limit": 5},
            }],
        }

    def first_answer(messages):
        screen = _tool_json(messages, "screen_polymer_separation")
        return {
            "text": (
                f"Handle {screen['handle']}: shown {screen['shown']}, "
                f"total {screen['total']}."
            ),
            "tool_calls": [],
        }

    def second_answer(messages):
        page = _tool_json(messages, "result_read")
        third = page["data"]["rows"][2]["solvent"]
        return {"text": f"The third solvent is {third}.", "tool_calls": []}

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "s", "name": "screen_polymer_separation",
            "args": {
                "feed_polymers": ["LDPE"],
                "temperature_min_c": 25.0, "temperature_max_c": 160.0, "top_k": 100,
            },
        }]},
        first_answer,
        follow,
        second_answer,
    ])
    session, history = new_session(), _history()
    first = _turn(Q6A, session, history)
    second = _turn(Q6B, session, history)
    assert first.status == second.status == "ok"
    screen = first.tool_trace[0].result
    handle, shown, total = screen["handle"], screen["shown"], screen["total"]
    assert handle and shown is not None and total is not None
    assert total > shown
    assert len(screen.get("top") or []) == shown
    exact = handle_rows(load_handle(session, handle))
    assert len(exact) == total
    assert str(shown) in first.answer and str(total) in first.answer
    third = exact[2]["solvent"]
    read = second.tool_trace[0]
    assert read.name == "result_read"
    assert read.args["handle"] == handle
    assert read.result["data"]["rows"][2]["solvent"] == third
    assert third in second.answer


def test_acceptance_7_tea_outside_cache_needs_a_confirmed_live_run(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "evaluate_process")
        code = payload["data"].get("error_code")
        return {
            "text": (
                f"Refusal {payload.get('refusal')}. error_code {code}. "
                "No stored simulation of HDPE/dodecane at 1 Mt/yr exists; costing it "
                "needs a confirmed live TEA run."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "t", "name": "evaluate_process",
            "args": {
                "mode": "evaluate",
                "engine_mode": "auto",
                "process_config": {
                    "target_polymer": "HDPE", "solvent": "dodecane",
                    "target_mass_percent": 60.0,
                    "processing_capacity_mt_per_yr": 1.0,
                    "energy_case": "C1",
                    "dissolution_temp_c": 140.0,
                    "precipitation_temp_c": 25.0,
                    "solvent_price": 4.08,
                    "solvent_loss_pct": 0.01,
                    "feedstock_distance_km": 0.0,
                    "dissolution_capacity": 3.0,
                    "labor_cost": 120_000.0,
                },
            },
        }]},
        answer,
    ])
    result = _turn(Q7, new_session(), _history())
    assert result.status == "ok"
    ev = result.tool_trace[0]
    data = ev.result["data"]
    assert data.get("error_code") == "live_tea_cost_confirmation_required"
    assert (data.get("n_live"), data.get("n_cache")) == (1, 0)
    assert ev.result.get("available") is False
    assert "confirmed live tea run" in result.answer.lower()
    assert "tea_cache_exact" not in result.answer
    assert "msp_usd_per_kg" not in json.dumps(data)


def test_acceptance_composition_cannot_cost_the_ranked_screen(monkeypatch):
    """Current assembly: family+partner screen works; costing a top-page member refuses.

    The exact cache pair, asked directly, HSP-joins and TEA-cache-serves.
    When a sequence connector lands, the first turn's refusals become answers.
    """

    def hsp_call(messages):
        row = _tool_json(messages, "screen_polymer_separation")["top"][0]
        return {
            "text": "",
            "tool_calls": [{
                "id": "h", "name": "screen_hansen_compatibility",
                "args": {
                    "polymer_names": ["LDPE"],
                    "solvent_names": [row["solvent"]],
                    "temperature_c": row["temperature_c"],
                },
            }],
        }

    def tea_call(messages):
        row = _tool_json(messages, "screen_polymer_separation")["top"][0]
        return {
            "text": "",
            "tool_calls": [{
                "id": "t", "name": "evaluate_process",
                "args": {
                    "mode": "evaluate",
                    "process_config": {
                        "target_polymer": "LDPE",
                        "solvent": row["solvent"],
                        "dissolution_temp_c": row["temperature_c"],
                    },
                },
            }],
        }

    def refuse(messages):
        hsp = _tool_json(messages, "screen_hansen_compatibility")
        tea = _tool_json(messages, "evaluate_process")
        return {
            "text": (
                f"Screen succeeded. HSP of a top-page member refused "
                f"{hsp.get('refusal')}. TEA of that member refused "
                f"{tea.get('refusal')}. No price basis for the ranked screen."
            ),
            "tool_calls": [],
        }

    def pair_tea(messages):
        return {
            "text": "",
            "tool_calls": [{
                "id": "p2", "name": "evaluate_process",
                "args": {
                    "mode": "evaluate",
                    "engine_mode": "auto",
                    "process_config": _complete_ldpe_dodecane_c1_cache_scenario(),
                },
            }],
        }

    def pair_answer(messages):
        hsp = _tool_json(messages, "screen_hansen_compatibility")
        tea = _tool_json(messages, "evaluate_process")
        msp = tea["data"]["comparison_rows"][0]["msp_usd_per_kg"]
        return {
            "text": (
                f"Direct cache pair works. HSP available={hsp['available']} "
                f"(source_basis {hsp.get('source_basis')}). "
                f"TEA msp {msp} (source_basis {tea['source_basis']})."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "s", "name": "screen_polymer_separation",
            "args": {"feed_polymers": ["polyethylene", "PP"]},
        }]},
        hsp_call,
        tea_call,
        refuse,
        {"text": "", "tool_calls": [{
            "id": "p1", "name": "screen_hansen_compatibility",
            "args": {
                "polymer_names": ["LDPE"], "solvent_names": ["dodecane"],
                "temperature_c": 145.0,
            },
        }]},
        pair_tea,
        pair_answer,
    ])
    session, history = new_session(), _history()
    ranked = _turn(Q_GAP, session, history)
    direct = _turn(Q_PAIR, session, history)
    assert ranked.status == direct.status == "ok"
    screen = ranked.tool_trace[0].result
    assert screen["available"] is True
    top_ids = _ids(_solvents(screen.get("top") or []))
    assert "dodecane" not in top_ids
    hsp, tea_result = ranked.tool_trace[1].result, ranked.tool_trace[2].result
    assert hsp["available"] is False
    assert hsp["refusal"] == "hsp_resolution_failed"
    assert tea_result["available"] is False
    assert tea_result["refusal"] == "incomplete_process_config"
    assert list(tea_result["data"].get("missing") or []) == list(
        tea._NINE_HELD_PUBLIC_FIELDS
    )
    assert "hsp_resolution_failed" in ranked.answer
    assert "incomplete_process_config" in ranked.answer
    pair_hsp, pair_tea = direct.tool_trace[0].result, direct.tool_trace[1].result
    assert pair_hsp["available"] is True
    assert pair_tea["available"] is True
    assert pair_tea["source_basis"] == "tea_cache_exact"
    assert pair_tea["data"]["cache_match_status"] == "exact"
    msp = pair_tea["data"]["comparison_rows"][0]["msp_usd_per_kg"]
    assert str(msp) in direct.answer
    assert "tea_cache_exact" in direct.answer


def test_named_roster_longest_nonoverlapping_and_short_names():
    nested = _named_roster_solvents("5-methyl-2-hexanone")
    assert any(n.casefold() == "5-methyl-2-hexanone" for n in nested)
    assert not any(n.casefold() == "2-hexanone" for n in nested)
    cyclo = _named_roster_solvents("Cyclohexane")
    assert any(n.casefold() == "cyclohexane" for n in cyclo)
    assert not any(n.casefold() == "hexane" for n in cyclo)
    lone = _named_roster_solvents("hexane")
    assert any(n.casefold() == "hexane" for n in lone)
    short = {n.casefold() for n in _named_roster_solvents("thf and h2o and co2")}
    assert {"thf", "h2o", "co2"} <= short
    prose = _named_roster_solvents("The cost comparison is complete.")
    assert not any(n.casefold() == "cos" for n in prose)
    lone = _named_roster_solvents("cos")
    assert any(n.casefold() == "cos" for n in lone)


def test_answer_matcher_rejects_hexane_when_exact_has_cyclohexane():
    with pytest.raises(AssertionError, match="outside"):
        _assert_answer_stays_inside_exact(
            "Safety of Cyclohexane and hexane.",
            ["cyclohexane"],
            ["Cyclohexane"],
        )


def test_answer_matcher_does_not_split_a_nested_exact_name():
    _assert_answer_stays_inside_exact(
        "Compared 5-methyl-2-hexanone.",
        ["5-methyl-2-hexanone"],
        ["5-methyl-2-hexanone"],
    )


# --- from test_agent_tools.py: Dispatch, result_read, handle issue, and the loop — chunk 2 obligations.
def _loop_call_graph():
    """Functions in the harness/wrapper/session modules reachable from run_turn."""
    mods = (agent, agent, sess)
    catalog = {
        name: obj
        for mod in mods for name, obj in vars(mod).items()
        if inspect.isfunction(obj) and inspect.getmodule(obj) in mods
    }
    seen: set = set()
    stack = [agent.run_turn]
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

    monkeypatch.setattr(agent, "complete", fake_complete)
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


def test_a_contaminant_class_fits_the_context_and_pages_whole_rows():
    """PFAS on LDPE nests 26 records in each of 32 solvent rows. The 20-row first page was 250 KB, and the owner's
    turn ended at compaction. The first page now shows those lists as counts; result_read returns whole rows that fit."""
    with _bound() as rec:
        screen = dispatch("screen_contaminant_leaching", target_polymer="LDPE", contaminants=["PFAS"])
        exact = handle_rows(load_handle(rec, screen["handle"]))
        assert len(json.dumps(exact[:20])) > 10 * agent._PAGE_BYTES  # the defect's scale
        assert len(json.dumps(screen)) < 2 * agent._PAGE_BYTES
        assert screen["shown"] == len(screen["top"]) == 20 and screen["total"] == len(exact)
        for shown, row in zip(screen["top"], exact):
            assert shown["contaminants"] == f"{len(row['contaminants'])} entries; result_read returns this row whole"
            assert {k: v for k, v in shown.items() if k != "contaminants"} == {k: v for k, v in row.items() if k != "contaminants"}
        page = dispatch("result_read", handle=screen["handle"], offset=0, limit=20)
        assert 1 <= page["returned"] < 20 and page["data"]["rows"] == exact[:page["returned"]]
        assert len(json.dumps(page["data"]["rows"])) <= agent._PAGE_BYTES
        assert dispatch("result_read", handle=screen["handle"], offset=5, limit=1)["data"]["rows"] == exact[5:6]
        small = dispatch("screen_contaminant_leaching", target_polymer="LDPE", contaminants=["DEHP"])
        assert small["top"] == handle_rows(load_handle(rec, small["handle"]))[:20]  # a page that fits is unchanged


def test_a_family_screen_shows_the_model_what_the_family_lacks():
    """"Which antioxidants leach from PP into ethanol" paged its 120 rows, and the compact view dropped the family's
    coverage: the answer never said 81 PlastChem antioxidants are outside the release (2026-09-24)."""
    with _bound():
        screen = dispatch("screen_contaminant_partitioning", polymer="PP", solvent="ethanol",
                          contaminants=["antioxidants"])
        assert screen["handle"] and screen["total"] == 120 and screen["shown"] < 120
        (coverage,) = screen["data"]["family_coverage"]
        assert (coverage["screened"], coverage["not_computed"], coverage["outside_release"]) == (120, 18, 81)
        assert coverage["outside_release_by_reason"]["contains phosphorus"] == 39


def test_the_model_sees_a_log_ratio_past_six_as_its_bound_and_engines_keep_the_number():
    """DEHP into water is logD -8.21 in the workbook. Past ±6 a substance is effectively all in one phase, so the model
    gets "< -6" (owner, 2026-09-24). The separation planner compares logD by calling the tool directly: it keeps -8.21."""
    direct = _data(contaminants.screen_contaminant_leaching("LDPE", ["DEHP"]))
    water = next(row for row in direct["candidate_solvents"] if row["solvent"] == "water")
    assert water["contaminants"][0]["logd"] == water["contaminant_logd_min"] == -8.21
    with _bound() as rec:
        screen = dispatch("screen_contaminant_leaching", target_polymer="LDPE", contaminants=["DEHP"])
        seen = next(row for row in handle_rows(load_handle(rec, screen["handle"])) if row["solvent"] == "water")
        assert seen["contaminants"][0]["logd"] == seen["contaminant_logd_min"] == "< -6"
        assert "-8.21" not in json.dumps(screen) and "-8.21" not in json.dumps(dispatch(
            "result_read", handle=screen["handle"], offset=0, limit=50))
        toluene = next(row for row in handle_rows(load_handle(rec, screen["handle"])) if row["solvent"] == "toluene")
        assert toluene["contaminants"][0]["logd"] == 0.81  # values inside the bound are unchanged


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
        assert UNWIRED == frozenset()
        for name in (
            "evaluate_stored_route_tea_lca",
            "optimize_stored_route",
            "pareto_optimize_stored_route",
        ):
            retired = dispatch(name)
            assert retired["available"] is False
            assert retired["refusal"] == "unknown_tool"


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
    wrap = schemas["evaluate_process"]["parameters"]["properties"]
    assert wrap["process_configs"]["type"] == "array"
    assert wrap["process_configs"]["items"]["type"] == "object"
    assert wrap["process_config"]["type"] == "object"
    assert "evaluate_tea_lca_scenarios" not in schemas
    assert "evaluate_stored_route_tea_lca" not in schemas
    assert "optimize_stored_route" not in schemas
    assert "pareto_optimize_stored_route" not in schemas
    rm = schemas["screen_polymer_separation"]["parameters"]["properties"]["ranking_mode"]
    assert "target_dissolution" in rm["enum"]
    mt = schemas["lookup_hansen_parameters"]["parameters"]["properties"]["material_type"]
    assert set(mt["enum"]) == {"polymer", "solvent"}
    assert "ingest_literature_graph" not in schemas
    assert "ingest_literature_documents" not in schemas
    paths = tool_schema_for("ingest_literature_graph")["parameters"]["properties"]["paths"]
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

    monkeypatch.setattr(agent, "complete", fake_complete)
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
    src = Path(agent.__file__).read_text()
    assert "numeral_scan" not in src
    assert "Callable[[ToolEvent], None]" in inspect.getsource(run_turn)
    assert "bound[\"tool_rounds\"]" not in src
    assert "provider_tokens" not in src
    assert result.status != "verifier_failed"
    graph = _loop_call_graph()
    assert sess.compact_messages in graph
    assert sess._summary_text in graph
    assert agent._emit in graph
    assert sess.load_turn_record not in graph
    assert _archive_readers_in_loop_graph() == []
    assert "result_read" not in inspect.getsource(agent._emit)


def test_system_prompt_reports_engine_inclusivity_not_user_strictness():
    prompt = agent.SYSTEM_PROMPT
    assert "bounds_are_inclusive" in prompt
    assert "A count for >= 5 / <= 1 is not a count for > 5 / < 1" in prompt


def test_system_prompt_gives_a_partition_its_size_and_claims_only_the_validation_that_exists():
    """The LDPE/dodecane answer said "stays in polymer" for logP -0.15, a nearly even split; and the prompt called every
    PlastChem prediction validated, when the COSMOtherm workbook covers PVC with eight phthalates only."""
    prompt = " ".join(agent.SYSTEM_PROMPT.split())
    assert "When |logP| is below 0.5 (K 0.3 to 3), say the substance splits nearly evenly between the phases" in prompt
    assert 'A value shown as "> 6" or "< -6" means effectively all in one phase' in prompt
    assert "checked against the COSMOtherm workbook only for PVC with eight phthalates" in prompt
    assert "validated against the COSMOtherm workbook" not in prompt


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

    monkeypatch.setattr(agent, "complete", fake_complete)
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


def test_unknown_tool_and_polyethylene_means_ldpe():
    with _bound():
        assert dispatch("not_a_tool")["refusal"] == "unknown_tool"
        out = dispatch(
            "solubility_query",
            polymers=["polyethylene"], solvents=["dodecane"], temperatures=[140.0],
        )
        assert out["available"] is True
        polymers = {row.get("polymer") for row in out["data"].get("results") or []}
        assert polymers == {"LDPE"}  # PE means LDPE unless the user names HDPE
        named = dispatch("solubility_query", polymers=["HDPE"], solvents=["dodecane"], temperatures=[140.0])
        assert {row.get("polymer") for row in named["data"].get("results") or []} == {"HDPE"}


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
        admitted = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={"target_polymer": "LDPE"},
        )
        aexact = load_handle(rec, admitted["handle"])["exact"]
        assert "records" in aexact and "record_assumptions" in aexact
        assert admitted.get("handle")
        assert "comparison_rows" in (admitted.get("data") or {})
        assert "top" not in admitted
        assert admitted.get("total") == len(aexact["comparison_rows"])


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


def test_cached_pubchem_fields_are_not_labeled_live():
    """The comparison tool stamps each row's PubChem fields "snapshot"; the label only read the top level, so cached
    fields were called pubchem_live and answers said "fetched live from PubChem"."""
    row = {"solvent": "toluene", "provenance": {"pubchem": "https://pubchem.ncbi.nlm.nih.gov/compound/1140",
                                                "pubchem_failed_headings": []},
           "field_origin": {"flash_point_c": {"source": "snapshot"}}}
    cached = {"success": True, "comparison_rows": [row]}
    live = {"success": True, "comparison_rows": [{**row, "field_origin": {"flash_point_c": {"source": "live"}}}]}
    assert source_basis_for("compare_solvent_safety_at_conditions", cached, {"include_pubchem": True}) == "safety_local"
    assert source_basis_for("compare_solvent_safety_at_conditions", live, {"include_pubchem": True}) == "pubchem_live"
    with _bound():
        out = dispatch("compare_solvent_safety_at_conditions", include_pubchem=True, candidates=[
            {"solvent": "toluene", "temperature_c": 100.0}, {"solvent": "dimethyl sulfoxide", "temperature_c": 140.0}])
    assert out["available"] is True and out["source_basis"] == "safety_local"


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

    monkeypatch.setattr(agent, "complete", fake_complete)
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
    contents = agent._gen_contents(msgs)
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
    assert context_window("openai:gemini-3.1-flash") == 128_000
    assert context_window("openai:foo-8k") == 8_000
    assert context_window("openai:foo-256k") == 256_000
    # Muse Spark takes 1,048,576 tokens; 256k is the working window the owner chose for now.
    for muse in ("openai:muse-spark-1.3", "openai:muse-spark-1.2", "openai:muse-spark-1.3-contributor"):
        assert context_window(muse) == 256_000


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
    from dissolve import agent
    poly, temp = sess._subject_arg_names()
    found_poly, found_temp = set(), set()
    for spec in agent.REGISTRY:
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
    window = context_window("openai:muse-spark-1.2")
    history = [  # just past window - reserve once the tool round lands (4 characters per estimated token)
        {"role": "user", "content": "OLD " + ("x" * (4 * window - 22_000))},
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
        assert estimated_tokens(messages) <= window - 8_000
        return {"text": "done", "tool_calls": []}

    def spy(messages, record, **kwargs):
        windows.append(kwargs.get("window"))
        assert messages[-1].get("role") == "tool"
        compact_messages(messages, record, **kwargs)
        return None

    monkeypatch.setattr(agent, "complete", fake_complete)
    monkeypatch.setattr(agent, "compact_messages", spy)
    result = run_turn(
        "CURRENT-QUERY", session=new_session(),
        model="openai:muse-spark-1.2", messages=history,
    )
    assert result.status == "ok"
    assert n["i"] == 2
    assert windows == [window]
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

    monkeypatch.setattr(agent, "complete", fake_complete)
    monkeypatch.setattr(agent, "compact_messages", fake_compact)
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
    assert agent._usage("openai", SimpleNamespace(usage=SimpleNamespace(total_tokens=18))) == {
        "total_tokens": 18,
    }
    assert agent._usage(
        "anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=8)),
    ) == {"input_tokens": 10, "output_tokens": 8, "total_tokens": 18}
    assert agent._usage(
        "google_genai", SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=3, candidates_token_count=5, total_token_count=8,
        )),
    ) == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}
    assert agent._usage(
        "openai", SimpleNamespace(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5)),
    ) == {"input_tokens": 3, "output_tokens": 5}
    assert agent._usage(
        "google_genai", SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=3, candidates_token_count=5,
        )),
    ) == {"input_tokens": 3, "output_tokens": 5}
    assert agent._usage("openai", SimpleNamespace(usage=None)) is None
    assert agent._usage("openai", SimpleNamespace()) is None
    assert agent._usage("openai", SimpleNamespace(usage=SimpleNamespace())) is None
    assert agent._usage("anthropic", SimpleNamespace(usage=SimpleNamespace())) is None
    assert agent._usage(
        "anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=0, output_tokens=0)),
    ) == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert agent._usage("anthropic", SimpleNamespace(usage=SimpleNamespace(input_tokens=10))) == {
        "input_tokens": 10,
    }
    assert agent._usage("openai", SimpleNamespace(usage=zero)) == {
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
    out = agent.complete(
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

    monkeypatch.setattr(agent, "complete", fake_complete)
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

    monkeypatch.setattr(agent, "complete", fake_complete)
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

    monkeypatch.setattr(agent, "complete", fake_complete)
    monkeypatch.setattr(agent, "compact_messages", spy)
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

    monkeypatch.setattr(agent, "complete", fake_complete)
    monkeypatch.setattr(agent, "compact_messages", spy)
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
    oai = agent._oai_msgs(history)
    oai_call = [c["id"] for m in oai for c in (m.get("tool_calls") or [])]
    oai_resp = [m.get("tool_call_id") for m in oai if m.get("role") == "tool"]
    assert oai_call == oai_resp == emitted
    _, ant = agent._ant_msgs(history)
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
    for content in agent._gen_contents(history):
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
    assert rec.last_tea["analysis_type"] == "route"
    assert rec["last_tea"] is rec.last_tea
    assert sess.SessionRecord().last_tea is None
    with pytest.raises(AttributeError, match="set keys, not attributes"):
        rec.last_route = {"steps": [{"solvent": "dodecane"}]}
    assert "last_route" not in rec
    session = sess.new_session()
    with sess.bind_tool_session(session) as bound:
        with pytest.raises(AttributeError, match="set keys, not attributes"):
            bound.last_candidates = [{"solvent": "copied-projection"}]
    assert "last_candidates" not in session


# --- from test_cli.py: CLI surface: persistence, slash commands, doctor, and the run_turn seam.
def _console():
    buf = io.StringIO()
    return Console(file=buf, force_terminal=True, width=80, color_system=None), buf


def _app(tmp_path, monkeypatch, **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    console, buf = _console()
    app = CliApp(
        session_id="test-session",
        store_root=tmp_path,
        console=console,
        **kwargs,
    )
    return app, buf


def _ok_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
    if messages is not None:
        messages.append({"role": "user", "content": query})
        messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "call-1", "name": "screen_polymer_separation",
                            "args": {"feed": "LDPE"}}],
        })
        messages.append({
            "role": "tool", "tool_call_id": "call-1",
            "name": "screen_polymer_separation", "content": "{}",
        })
        messages.append({"role": "assistant", "content": "done"})
    session.setdefault("handles", {})
    session["handles"]["calm-blue-cat"] = {
        "tool": "screen_polymer_separation",
        "source_basis": "cosmo_rs_grid",
        "exact": {"ranked_candidates": [{"solvent": "dodecane"} for _ in range(40)]},
    }
    session.setdefault("polymers_in_play", []).append("LDPE")
    session.setdefault("temperatures_in_play", []).append(140.0)
    ev = ToolEvent("screen_polymer_separation", {"feed": "LDPE"}, {"handle": "calm-blue-cat", "total": 40})
    if on_event:
        on_event(ev)
    return TurnResult(answer="screened forty solvents", status="ok", tool_trace=[ev],
                      turn_record="turn-1", tool_rounds=1)


def test_resolve_model_aliases_and_default():
    alias, spec = resolve_model("muse")
    assert alias == "muse-spark"
    assert spec.model == "openai:muse-spark-1.3"
    assert spec.env_var == "META_MUSE_API_KEY"
    assert spec.base_url == "https://api.meta.ai/v1"
    with pytest.raises(ValueError, match="Unknown model alias"):
        resolve_model("not-a-model")


def test_cli_source_cuts_langchain_ingest_and_registry_call():
    src = inspect.getsource(cli)
    assert "langchain" not in src
    assert "ingest-graph" not in src
    assert "compare-rag" not in src
    assert "registry.call" not in src
    assert "AgentHarness" not in src
    assert "SessionState" not in src
    assert "candidate_evidence" not in src
    assert "true_alias" not in src
    assert "budget_profile" not in src
    assert "result.tool_rounds" in src
    assert "result.usage" in src
    assert "last_provider_tokens" not in src


def test_main_rejects_ingest_verbs():
    with pytest.raises(SystemExit):
        main(["ingest-graph", "--paths", "x"])
    with pytest.raises(SystemExit):
        main(["compare-rag", "--paths", "x"])


def test_doctor_checks_key_assets_registry_duckdb(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    names = [c["name"] for c in report["checks"]]
    assert names[:2] == ["Model provider", "Scientific assets"]
    assert "Tool registry" in names
    assert "duckdb" in names
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["Tool registry"]["status"] == "pass"
    assert by_name["Tool registry"]["detail"] == "31 registered names"
    assert by_name["Tool registry"]["registered"] == len(EXPECTED_REGISTRY_NAMES)
    assert len(EXPECTED_REGISTRY_NAMES) == 31
    assert "fetch_solvent_safety_by_cid" in EXPECTED_REGISTRY_NAMES
    assert "estimate_thermal_properties" not in EXPECTED_REGISTRY_NAMES
    assert "normalize_feed_composition" not in EXPECTED_REGISTRY_NAMES
    assert by_name["Scientific assets"]["status"] == "pass"
    assert by_name["Model provider"]["status"] == "fail"
    assert report["ready"] is False
    assert "specialist" not in json.dumps(report).lower()


def test_doctor_fails_when_a_declared_tool_is_missing(tmp_path, monkeypatch):

    shortened = tuple(t for t in agent.REGISTRY if t.name != "ingest_literature_graph")
    monkeypatch.setattr(agent, "REGISTRY", shortened)
    monkeypatch.setattr(agent, "BY_NAME", {t.name: t for t in shortened})
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Tool registry")
    assert check["status"] == "fail"
    assert "missing ingest_literature_graph" in check["detail"]
    assert "registered names" in check["detail"]


def test_doctor_registry_fails_on_duplicate_names(tmp_path, monkeypatch):

    monkeypatch.setattr(agent, "REGISTRY", agent.REGISTRY + agent.REGISTRY[:1])
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    report = doctor_report(tmp_path, model_alias="muse-spark")
    check = next(c for c in report["checks"] if c["name"] == "Tool registry")
    assert check["status"] == "fail"
    assert "REGISTRY" in check["detail"] and "BY_NAME" in check["detail"]


def test_persist_roundtrip_messages_and_handle(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    path = tmp_path / "sessions" / "test-session" / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["release"] == "dissolve-v12-0.1"
    assert payload["metadata"]["mode"] == "review"
    assert payload["metadata"]["model"] == "muse-spark"
    assert any(m.get("role") == "user" and m.get("content") == "screen LDPE" for m in payload["messages"])
    stored = payload["session"]["handles"]["calm-blue-cat"]
    assert stored["tool"] == "screen_polymer_separation"
    assert len(stored["exact"]["ranked_candidates"]) == 40
    trans = (tmp_path / "sessions" / "test-session" / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    roles = [json.loads(line)["role"] for line in trans]
    assert "user" in roles and "assistant" in roles and "tool" in roles
    app2, _ = _app(tmp_path, monkeypatch)
    assert "calm-blue-cat" in app2.session["handles"]
    assert len(app2.session["handles"]["calm-blue-cat"]["exact"]["ranked_candidates"]) == 40
    assert any(m.get("content") == "screen LDPE" for m in app2.messages)


def test_clear_drops_messages_and_handles_keeps_id(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    assert app.session["handles"]
    assert app.handle_command("/clear") is False
    assert app.store.session_id == "test-session"
    assert app.session["handles"] == {}
    assert [m["role"] for m in app.messages] == ["system"]
    payload = json.loads((tmp_path / "sessions" / "test-session" / "session.json").read_text())
    assert payload["session"]["handles"] == {}
    assert payload["session_id"] == "test-session"


def test_mode_rewrites_system_prompt(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    assert "Ask before making consequential process assumptions" in app.messages[0]["content"]
    app.handle_command("/mode auto")
    assert app.mode == "auto"
    assert "Make reasonable process assumptions when needed" in app.messages[0]["content"]
    assert "Ask before making consequential" not in app.messages[0]["content"]
    assert "auto" in buf.getvalue()


def test_the_prompt_says_how_to_write_an_answer():
    """Answers had become field dumps: a source tag on every value, raw keys, rules recited to the reader."""
    prompt = agent.SYSTEM_PROMPT
    assert "Report the source_basis with the number." not in prompt
    for rule in ("Writing the answer.", "Open with the answer", 'line that starts "Source:"',
                 "Never show field names", "Stop when the results answer the question",
                 "Do not describe your process", "a |---| separator row",
                 # comparisons and the scores they rank by (separation, safety, Hansen)
                 "Comparing alternatives", "as safe as its least safe solvent", "cross-check it with the\n  Hansen tools",
                 "Metrics. Explain each score", "G score:", "CHEM21 Safety, Health and Environment scores",
                 "RED is the Hansen distance", "any request for CHEM21",
                 "Missing data never counts in an option's favour", "apply it to every solvent in\n  the route"):
        assert rule in prompt


def test_answers_get_their_table_separators_back():
    """The model sometimes drops a table's |---| row; the CLI's Markdown and the web UI then show one paragraph."""
    assert agent._complete_tables("| A | B |\n| x | 1 |\n| y | 2 |\n\nSource: z") == (
        "| A | B |\n|---|---|\n| x | 1 |\n| y | 2 |\n\nSource: z")
    for untouched in ("| A | B |\n|---|---|\n| x | 1 |", "```\n| a | b |\n| c | d |\n```", "Value | here", "no table"):
        assert agent._complete_tables(untouched) == untouched


def test_a_repeated_table_separator_is_dropped():
    """The safety example came back with a second separator row carrying stray text in one cell (2026-09-24),
    which the web UI drew as a junk data row. Rows after the separator whose every cell holds --- are dropped."""
    glitch = "| Solvent | Band |\n|---|---|\n|---|すすめ---|\n| Ethyl acetate | problematic |\n\nSource: z"
    assert agent._complete_tables(glitch) == "| Solvent | Band |\n|---|---|\n| Ethyl acetate | problematic |\n\nSource: z"
    assert agent._complete_tables("| A | B |\n|---|---|\n|---|---|\n| x | 1 |") == "| A | B |\n|---|---|\n| x | 1 |"
    two_tables = "| A |\n|---|\n| x |\n\n| B |\n|---|\n| y |"
    assert agent._complete_tables(two_tables) == two_tables
    dashes_in_data = "| A | B |\n|---|---|\n| --- | 1 |"
    assert agent._complete_tables(dashes_in_data) == dashes_in_data  # one data cell of dashes is still data
    # the same glitch with the stray text in a cell of its own (the integrated example, 2026-09-24)
    wide = "| A | B | C | D |\n|---|---|---|---|\n|---|---|すすめます|---|\n| x | 1 | 2 | 3 |"
    assert agent._complete_tables(wide) == "| A | B | C | D |\n|---|---|---|---|\n| x | 1 | 2 | 3 |"
    placeholders = "| A | B | C |\n|---|---|---|\n| residue | — | — |"
    assert agent._complete_tables(placeholders) == placeholders  # em-dash placeholders are data


def test_text_right_under_a_table_is_moved_out_of_it():
    """The toluene safety card put its legend on the line after the table (2026-09-24); GitHub-flavoured Markdown
    reads that line as one more table row, so the web UI drew the legend inside the table."""
    assert agent._complete_tables("| Measure | Score |\n|---|---|\n| Safety | 4 |\nG score is the GSK score.") == (
        "| Measure | Score |\n|---|---|\n| Safety | 4 |\n\nG score is the GSK score.")
    spaced = "| A |\n|---|\n| x |\n\nText"
    assert agent._complete_tables(spaced) == spaced


def test_a_new_session_screens_the_common_solvents(tmp_path, monkeypatch):
    """The full grid holds gases and explosives whose predictions clip at 100 wt%; a person opts into it."""
    app, _buf = _app(tmp_path, monkeypatch)
    assert app.session["solvent_scope"]["scope"] == "common"
    app.handle_command("/solvents all")
    assert app.session["solvent_scope"]["scope"] == "all"
    app.handle_command("/clear")
    assert app.session["solvent_scope"]["scope"] == "common"


def test_context_lists_polymers_temps_and_handle_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    app.handle_command("/context")
    shown = buf.getvalue()
    assert "LDPE" in shown
    assert "140" in shown
    assert "calm-blue-cat" in shown
    assert "40" in shown


def test_harness_and_cost_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.handle_command("/harness")
    assert "flat loop, no specialists" in buf.getvalue()
    app.ask("q")
    app.handle_command("/cost")
    out = buf.getvalue()
    assert "1 tool call" in out
    assert "1 tool round" in out
    assert "status ok" in out
    assert "usage null" in out
    assert "token(s)" not in out
    assert "did not return" not in out.lower()


def test_cost_prints_provider_tokens_when_returned(tmp_path, monkeypatch):
    def fake(query, **kwargs):
        kwargs["messages"].append({"role": "user", "content": query})
        kwargs["messages"].append({"role": "assistant", "content": "done"})
        return TurnResult("done", "ok", [], "turn-1", 1, {"total_tokens": 18})

    monkeypatch.setattr(cli, "run_turn", fake)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("q")
    app.handle_command("/cost")
    out = buf.getvalue()
    assert '"total_tokens": 18' in out
    assert "usage null" not in out
    assert "did not return" not in out.lower()
    assert "Provider did not return a token count" not in out


def test_compaction_error_uses_notice_panel(tmp_path, monkeypatch):
    def fake(query, **kwargs):
        kwargs["messages"].append({"role": "user", "content": query})
        return TurnResult(
            answer="compaction cannot meet window-reserve target=0",
            status="compaction_error", tool_trace=[], turn_record="turn-1",
        )

    monkeypatch.setattr(cli, "run_turn", fake)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("q")
    assert result.status == "compaction_error"
    assert "Session budget stopped" in buf.getvalue()
    assert "compaction cannot meet" in buf.getvalue()


def test_startup_requires_named_key(tmp_path, monkeypatch):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="META_MUSE_API_KEY"):
        CliApp(session_id="key-check", store_root=tmp_path, persist=False, require_key=True)


def test_harness_without_query_launches_cli(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["agent.py"])
    monkeypatch.setattr(cli, "main", lambda argv=None: 17)
    with pytest.raises(SystemExit) as exc:
        agent._main()
    assert exc.value.code == 17


def test_cli_does_not_call_registry(monkeypatch, tmp_path):

    def boom(*a, **k):
        raise AssertionError("CLI must not call registry.call")

    monkeypatch.setattr(agent, "call", boom)
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("q")
    app.handle_command("/context")
    app.handle_command("/harness")
    app.handle_command("/clear")


def test_new_session_shape_unchanged():
    rec = new_session()
    assert set(rec) >= {"handles", "reported", "turn_records"}


def test_banner_subtitle_is_v12_release(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.banner()
    shown = buf.getvalue()
    assert "dissolve-v12-0.1" in shown
    assert "v0.4" not in shown


def test_interactive_path_draws_banner_models_and_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    console = Console(file=buf, width=96, force_terminal=False)
    prompts = []
    replies = iter(["/model", "quit"])

    def fake_ask(prompt, **kwargs):
        prompts.append(prompt)
        return next(replies)

    monkeypatch.setattr(cli.Prompt, "ask", fake_ask)
    app = CliApp(
        session_id="look-session",
        store_root=tmp_path,
        persist=False,
        console=console,
    )
    app.run()
    shown = buf.getvalue()
    assert "Advanced Recycling Agent" in shown
    assert "dissolve-v12-0.1" in shown
    assert "D I S S O L V E" in shown
    assert "v0.4" not in shown
    assert "Models" in shown
    assert "muse-spark" in shown
    assert "gemini-flash" in shown
    assert prompts == ["\n[bold cyan]>[/]", "\n[bold cyan]>[/]"]


def test_ask_prints_turnresult_answer_only(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("screen LDPE")
    shown = buf.getvalue()
    assert result.answer in shown
    assert shown.count(result.answer) >= 1
    assert "registry.call" not in shown


def test_load_strips_stale_turn_and_incomplete_tool_round(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "name": "no_such_tool", "args": {}},
                {"id": "call-2", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "name": "no_such_tool", "content": "{}"},
        ],
        "session": {
            "handles": {}, "reported": [],
            "_turn": "turn-1",
            "turn_records": {"turn-1": [{"tool": "old"}]},
            "polymers_in_play": [], "temperatures_in_play": [],
        },
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    app, _ = _app(tmp_path, monkeypatch)
    assert "_turn" not in app.session
    assert [m.get("role") for m in app.messages] == ["system"]
    assert app.session["turn_records"]["turn-1"] == [{"tool": "old"}]


def test_resume_missing_handle_is_named_refusal_not_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    n = {"i": 0}

    def fake_complete(messages, tools, **kwargs):
        n["i"] += 1
        if n["i"] == 1:
            return {"text": "", "tool_calls": [
                {"id": "1", "name": "result_read", "args": {"handle": "ghost-handle"}},
            ]}
        return {"text": "the handle is gone", "tool_calls": []}

    monkeypatch.setattr(agent, "complete", fake_complete)
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("screen LDPE")
    path = tmp_path / "sessions" / "test-session" / "session.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["session"]["handles"] = {}
    payload["session"]["_turn"] = "turn-1"
    payload["session"]["turn_records"] = {"turn-1": [{"tool": "old"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cli, "run_turn", agent.run_turn)
    app2, _ = _app(tmp_path, monkeypatch)
    assert app2.session.get("handles") == {}
    assert "_turn" not in app2.session
    result = app2.ask("read that shortlist")
    assert result.status == "ok"
    assert result.tool_trace[0].result.get("refusal") == "unknown_handle"
    assert result.turn_record != "turn-1"
    assert app2.session["turn_records"]["turn-1"] == [{"tool": "old"}]


def test_incomplete_round_drops_the_user_group(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    seen = []

    def fake_complete(messages, tools, **kwargs):
        seen.append([m.get("content") for m in messages if m.get("role") == "user"])
        return {"text": "new", "tool_calls": []}

    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "OLD MUTATING REQUEST"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call-1", "name": "no_such_tool", "args": {}},
                {"id": "call-2", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "call-1", "name": "no_such_tool", "content": "{}"},
        ],
        "session": {"handles": {}, "reported": [], "turn_records": {},
                    "polymers_in_play": [], "temperatures_in_play": []},
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(agent, "complete", fake_complete)
    app, _ = _app(tmp_path, monkeypatch)
    assert not any(m.get("content") == "OLD MUTATING REQUEST" for m in app.messages)
    app.ask("NEW REQUEST")
    assert seen
    assert "OLD MUTATING REQUEST" not in seen[0]
    assert any("NEW REQUEST" in str(c) for c in seen[0])


def test_resume_keeps_complete_empty_id_google_round(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "test-session"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "session_id": "test-session",
        "release": "dissolve-v12-0.1",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": "", "name": "no_such_tool", "content": "{}"},
            {"role": "assistant", "content": "done"},
        ],
        "session": {"handles": {}, "reported": [], "turn_records": {},
                    "polymers_in_play": [], "temperatures_in_play": []},
        "metadata": {"model": "muse-spark", "mode": "review"},
    }
    (root / "session.json").write_text(json.dumps(payload), encoding="utf-8")
    app, _ = _app(tmp_path, monkeypatch)
    assert [m.get("role") for m in app.messages] == [
        "system", "user", "assistant", "tool", "assistant",
    ]
    assert app.messages[-1]["content"] == "done"


def test_v11_session_file_is_refused_and_left_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    root = tmp_path / "sessions" / "legacy-sess"
    root.mkdir(parents=True)
    path = root / "session.json"
    v11 = {
        "schema_version": 1,
        "session_id": "legacy-sess",
        "state": {"handles": {"old": True}},
        "context": {"turns": ["keep-me"]},
        "metadata": {"model": "muse-spark"},
    }
    path.write_text(json.dumps(v11), encoding="utf-8")
    before = path.read_bytes()
    console, _ = _console()
    with pytest.raises(ValueError, match="incompatible"):
        CliApp(session_id="legacy-sess", store_root=tmp_path, persist=True, console=console)
    assert path.read_bytes() == before


def test_context_uses_canonical_handle_total(tmp_path, monkeypatch):
    app, buf = _app(tmp_path, monkeypatch)
    app.session["handles"]["route-sub"] = {
        "tool": "screen_route_solvent_substitutions",
        "source_basis": "safety_local",
        "exact": {
            "route_stage_assessments": [{"stage": 1}],
            "candidate_substitutions": [{"solvent": f"s{i}"} for i in range(11)],
            "comparison_rows": [{"solvent": "a"}, {"solvent": "b"}],
            "candidate_conditions": [{"c": i} for i in range(12)],
        },
    }
    app.handle_command("/context")
    shown = buf.getvalue()
    assert "route-sub" in shown
    assert re.search(r'"total":\s*11\b', shown)
    assert not re.search(r'"total":\s*1\b', shown)


def test_cost_survives_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn)
    app, _ = _app(tmp_path, monkeypatch)
    app.ask("q")
    app2, buf = _app(tmp_path, monkeypatch)
    app2.handle_command("/cost")
    out = buf.getvalue()
    assert "No model usage in this process yet" not in out
    assert "status ok" in out
    assert "1 tool round" in out


def test_cost_rounds_count_current_group_after_compaction(tmp_path, monkeypatch):
    def compacting_turn(query, *, messages, on_event, **kwargs):
        start = next(i for i, msg in enumerate(messages) if msg.get("role") == "user")
        end = next(
            i for i in range(start + 1, len(messages)) if messages[i].get("role") == "user"
        )
        del messages[start:end]
        messages.append({"role": "user", "content": query})
        messages.append({
            "role": "assistant", "content": "",
            "tool_calls": [{"id": "now", "name": "no_such_tool", "args": {}}],
        })
        ev = ToolEvent("no_such_tool", {}, {"ok": True})
        if on_event:
            on_event(ev)
        messages.append({
            "role": "tool", "tool_call_id": "now", "name": "no_such_tool", "content": "{}",
        })
        messages.append({"role": "assistant", "content": "done"})
        return TurnResult("done", "ok", [ev], "turn-now", 1)

    monkeypatch.setattr(cli, "run_turn", compacting_turn)
    app, buf = _app(tmp_path, monkeypatch)
    for i in range(6):
        app.messages.extend([
            {"role": "user", "content": f"old-{i}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"old-{i}", "name": "no_such_tool", "args": {}},
            ]},
            {"role": "tool", "tool_call_id": f"old-{i}", "name": "no_such_tool", "content": "{}"},
            {"role": "assistant", "content": f"ans-{i}"},
        ])
    before = sum(1 for m in app.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert before == 6
    app.ask("NEW REQUEST")
    after = sum(1 for m in app.messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert after == 6
    assert app.last_tool_rounds == 1
    assert app.last_tool_calls == 1
    app.handle_command("/cost")
    out = buf.getvalue()
    assert "1 tool round" in out
    assert "0 tool round" not in out


def test_oneshot_resolves_through_cli_table(monkeypatch, tmp_path):
    seen = {}

    def fake_run_turn(query, **kwargs):
        seen.update(kwargs)
        seen["query"] = query
        return TurnResult("ok", "ok", [], "turn-1")

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("DISSOLVE_HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["agent.py", "hello"])
    monkeypatch.setattr(cli, "run_turn", fake_run_turn)
    with pytest.raises(SystemExit) as exc:
        agent._main()
    assert exc.value.code == 0
    assert seen["query"] == "hello"
    assert seen["model"] == "openai:muse-spark-1.3"
    assert seen["api_base"] == "https://api.meta.ai/v1"
    assert seen["api_key_env"] == "META_MUSE_API_KEY"


def test_positional_oneshot_persists_exact_tool_result(tmp_path, monkeypatch, capsys):
    home = tmp_path / "dissolve-home"
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setenv("DISSOLVE_HOME", str(home))
    monkeypatch.setattr(sys, "argv", ["agent.py", "what is the safety of dodecane?"])
    monkeypatch.setattr(
        agent, "complete",
        _complete_one_tool(
            "get_solvent_safety_card",
            {"solvent_name": "dodecane", "include_pubchem": False},
        ),
    )
    try:
        agent._main()
    except SystemExit as exc:
        assert exc.code == 0
    sessions = list(home.glob("sessions/*/session.json"))
    transcripts = list(home.glob("sessions/*/transcript.jsonl"))
    assert sessions and transcripts
    session_text = sessions[0].read_text(encoding="utf-8")
    transcript_text = transcripts[0].read_text(encoding="utf-8")
    assert "physical_properties" in session_text
    assert "112-40-3" in session_text
    assert "physical_properties" in transcript_text
    assert "112-40-3" in transcript_text
    shown = capsys.readouterr().out
    assert "physical_properties" not in shown
    assert "112-40-3" not in shown
    assert "tool  get_solvent_safety_card" in shown
    assert "status=ok" in shown
    assert "Advanced Recycling Agent" not in shown


def test_positional_oneshot_missing_key_is_provider_error(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("META_MUSE_API_KEY", raising=False)
    monkeypatch.setenv("DISSOLVE_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(sys, "argv", ["agent.py", "hello"])
    try:
        agent._main()
    except SystemExit:
        pass
    except RuntimeError as exc:
        pytest.fail(f"RuntimeError escaped the positional entrypoint: {exc}")
    out, err = capsys.readouterr()
    assert "Traceback" not in err
    assert "RuntimeError" not in err
    assert "missing environment variable META_MUSE_API_KEY" in out
    assert "status=provider_error" in out
    assert "Traceback" not in out


def test_usage_is_display_only_except_the_cost_line(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 18, 6, 0, 0, tzinfo=tz or timezone.utc)

    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr(cli, "datetime", FrozenDateTime)
    monkeypatch.setattr(cli.time, "monotonic", lambda: 1000.0)

    injected = [
        None,
        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        {"total_tokens": 18},
    ]
    snapshots = []
    cost_lines = []
    for i, usage in enumerate(injected):
        def fake_complete(messages, tools, *, _usage=usage, **kwargs):
            return {"text": "same-answer", "tool_calls": [], "usage": _usage}

        monkeypatch.setattr(agent, "complete", fake_complete)
        console, buf = _console()
        app = CliApp(
            session_id=f"usage-{i}",
            store_root=tmp_path,
            console=console,
            persist=True,
        )
        result = app.ask("same query")
        ask_out = buf.getvalue()
        app.handle_command("/cost")
        cost_lines.append(buf.getvalue()[len(ask_out):])
        state = json.loads(app.store.state_path.read_text(encoding="utf-8"))
        state.pop("session_id", None)
        state.get("metadata", {}).pop("last_usage", None)
        snapshots.append({
            "answer": result.answer,
            "status": result.status,
            "tool_trace": [(e.name, e.args, e.result) for e in result.tool_trace],
            "messages": json.dumps(app.messages, sort_keys=True),
            "turn_records": json.dumps(app.session.get("turn_records"), sort_keys=True),
            "archive": json.dumps(state, sort_keys=True),
            "transcript": app.store.transcript_path.read_text(encoding="utf-8"),
            "ask": ask_out,
        })
    assert snapshots[1] == snapshots[0]
    assert snapshots[2] == snapshots[0]
    assert cost_lines[0] != cost_lines[1]
    assert cost_lines[0] != cost_lines[2]
    assert cost_lines[1] != cost_lines[2]
    assert "usage null" in cost_lines[0]
    assert '"input_tokens": 0' in cost_lines[1]
    assert '"output_tokens": 0' in cost_lines[1]
    assert '"total_tokens": 0' in cost_lines[1]
    assert '"total_tokens": 18' in cost_lines[2]
    assert "No model usage in this process yet" not in "".join(cost_lines)


_SAFETY_PAYLOAD = {
    "identity": {"name": "dodecane"},
    "physical_properties": {"bp_c": 216},
    "gscore": 4.1,
    "ghs": {"pictograms": ["flame"]},
    "toxicity": {"ld50": "none"},
    "occupational_exposure_limits": {"twa": "none"},
    "peroxide_risk": {"class": "none"},
    "process_temperature_assessment": {"ok": True},
    "data_gaps": [],
    "provenance": {"source_basis": "safety_local"},
}


def _safety_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
    if messages is not None:
        messages.append({"role": "user", "content": query})
        messages.append({"role": "assistant", "content": "dodecane is a hydrocarbon."})
    ev = ToolEvent(
        "get_solvent_safety_card",
        {"solvent_name": "dodecane", "include_pubchem": False},
        _SAFETY_PAYLOAD,
    )
    if on_event:
        on_event(ev)
    return TurnResult(
        "dodecane is a hydrocarbon.", "ok", [ev], "turn-1", 1, None,
    )


def test_tool_event_print_is_one_line_without_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _safety_turn)
    app, buf = _app(tmp_path, monkeypatch)
    result = app.ask("what is the safety of dodecane?")
    shown = buf.getvalue()
    blob = json.dumps(_SAFETY_PAYLOAD, ensure_ascii=False)
    size = len(blob.encode("utf-8"))
    summary = cli._tool_event_summary(result.tool_trace[0])
    assert summary in shown
    assert "get_solvent_safety_card(solvent_name=dodecane, include_pubchem=False" in summary
    assert f"-> {size} B" in summary
    tool_lines = [ln for ln in shown.splitlines() if "get_solvent_safety_card" in ln]
    assert len(tool_lines) == 1
    assert "\n" not in summary
    assert shown.count("get_solvent_safety_card") == 1
    for key in (
        "physical_properties", "gscore", "occupational_exposure_limits",
        "peroxide_risk", "process_temperature_assessment", "data_gaps",
    ):
        assert key not in shown
    assert result.tool_trace[0].result == _SAFETY_PAYLOAD
    rows = [
        json.loads(line)
        for line in app.store.transcript_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    tool_row = next(row for row in rows if row.get("role") == "tool")
    assert json.loads(tool_row["content"]) == _SAFETY_PAYLOAD


def test_stream_json_emits_complete_tool_result(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _safety_turn)
    events = []
    console, buf = _console()
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    app = CliApp(
        session_id="stream-json",
        store_root=tmp_path,
        console=console,
        persist=False,
        event_sink=events.append,
        quiet=True,
    )
    app.ask("what is the safety of dodecane?")
    shown = buf.getvalue()
    tool_events = [ev for ev in events if ev.get("event") == "tool"]
    assert len(tool_events) == 1
    assert tool_events[0]["name"] == "get_solvent_safety_card"
    assert tool_events[0]["result"] == _SAFETY_PAYLOAD
    assert tool_events[0]["result"]["gscore"] == 4.1
    assert "gscore" not in shown
    assert "physical_properties" not in shown
    assert "get_solvent_safety_card" not in shown


def test_tool_event_line_escapes_multiline_query(tmp_path, monkeypatch):
    event = ToolEvent(
        "search_scholarly_literature",
        {"query": "alpha\nRAW SECOND LINE"},
        {"hits": 0},
    )

    def fake_turn(query, *, session, model, messages, on_event, api_base, api_key_env):
        if messages is not None:
            messages.append({"role": "user", "content": query})
            messages.append({"role": "assistant", "content": "none"})
        if on_event:
            on_event(event)
        return TurnResult("none", "ok", [event], "turn-1", 1, None)

    monkeypatch.setattr(cli, "run_turn", fake_turn)
    app, buf = _app(tmp_path, monkeypatch)
    app.ask("literature")
    summary = cli._tool_event_summary(event)
    shown = buf.getvalue()
    assert "\n" not in summary
    assert "\\n" in summary
    assert "RAW SECOND LINE" not in shown.splitlines()
    assert sum(1 for ln in shown.splitlines() if "search_scholarly_literature" in ln) == 1


def test_tool_event_line_bounds_long_string():
    event = ToolEvent(
        "search_scholarly_literature",
        {"query": "Q" * 5000},
        {"hits": 0},
    )
    summary = cli._tool_event_summary(event)
    assert "\n" not in summary
    assert len(summary) <= 48 + 160 + 80
    assert ("Q" * 5000) not in summary
    assert summary.count("Q") <= cli._ARG_ITEM_MAX


def test_tool_event_line_keeps_container_identity():
    result = {"ok": True}
    left = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene"]},
        result,
    ))
    right = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["HDPE"], "solvent_names": ["hexane", "toluene"]},
        result,
    ))
    assert left != right
    assert "LDPE" in left and "HDPE" in right
    assert "dodecane" in left and "hexane" in right
    assert "<1>" not in left and "<2>" not in left


def _complete_one_tool(name, args):
    def fake_complete(messages, tools, **kwargs):
        if any(m.get("role") == "tool" for m in messages):
            return {"text": "done", "tool_calls": []}
        return {
            "text": "",
            "tool_calls": [{"id": "c1", "name": name, "args": args}],
        }
    return fake_complete


def _run_one_tool(monkeypatch, name, args):
    monkeypatch.setattr(agent, "complete", _complete_one_tool(name, args))
    events = []
    result = run_turn(
        "q", session=new_session(), model="openai:x", on_event=events.append,
    )
    return result, events


def _summary_args_body(summary: str) -> str:
    inner = summary.split("(", 1)[1].rsplit(") -> ", 1)[0]
    return re.sub(r" #[0-9a-f]+$", "", inner)


def test_tool_event_line_escapes_name_via_unknown_tool_refusal(monkeypatch):
    result, events = _run_one_tool(monkeypatch, "unknown\nTOOL", {})
    assert events and events[0].result.get("refusal") == "unknown_tool"
    assert result.tool_trace[0].result.get("refusal") == "unknown_tool"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert "\\n" in summary
    assert "unknown\\nTOOL" in summary


def test_tool_event_line_escapes_key_via_tool_exception_refusal(monkeypatch):
    result, events = _run_one_tool(
        monkeypatch, "solubility_query", {"bad\nKEY": "x"},
    )
    assert events and events[0].result.get("refusal") == "tool_exception"
    assert result.tool_trace[0].name == "solubility_query"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert "\\n" in summary
    assert "bad\\nKEY" in summary


def test_tool_event_line_bounds_unknown_tool_name_via_run_turn(monkeypatch):
    name = "U" * 5000
    result, events = _run_one_tool(monkeypatch, name, {})
    assert events and events[0].result.get("refusal") == "unknown_tool"
    summary = cli._tool_event_summary(events[0])
    assert "\n" not in summary
    assert len(summary) <= 48 + 160 + 80
    assert name not in summary
    assert summary.count("U") <= cli._ARG_ITEM_MAX
    assert result.tool_trace[0].name == name


def test_tool_event_line_distinguishes_unsampled_container_tail():
    result = {"ok": True}
    left = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene", "hexane"]},
        result,
    ))
    right = cli._tool_event_summary(ToolEvent(
        "screen_hansen_compatibility",
        {"polymer_names": ["LDPE"], "solvent_names": ["dodecane", "xylene", "toluene"]},
        result,
    ))
    assert _summary_args_body(left) == _summary_args_body(right)
    assert "hexane" not in left and "toluene" not in right
    assert left != right
    fps = re.findall(
        rf"(?<=#)[0-9a-f]{{{cli._FP_HEX}}}", left + " " + right,
    )
    assert len(set(fps)) == 2


def test_tool_event_line_distinguishes_args_past_global_truncation():
    padding = {f"arg{i:02d}": "xxxx" for i in range(30)}
    left = cli._tool_event_summary(ToolEvent("probe", {**padding, "zz": "one"}, {}))
    right = cli._tool_event_summary(ToolEvent("probe", {**padding, "zz": "two"}, {}))
    assert _summary_args_body(left) == _summary_args_body(right)
    assert "zz=" not in _summary_args_body(left)
    assert left != right


# --- from test_cli_process_sheet.py: CLI-direct process sheet: the submitted dict is the tool dict.
def _token_col(text, token):
    for visual in str(text).splitlines():
        idx = visual.find(token)
        if idx >= 0:
            return idx
    return -1


def _sheet_field_block(shown, name):
    lines = shown.splitlines()
    match = re.compile(rf"^{re.escape(name)}(?:\s|$)")
    start = next(i for i, line in enumerate(lines) if match.match(line))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if line.startswith(" "):
            block.append(line)
        else:
            break
    return "\n".join(block)


def _console_cli_process_sheet(width=80):
    buf = io.StringIO()
    return Console(
        file=buf,
        force_terminal=True,
        width=width,
        height=25,
        color_system=None,
    ), buf


def _app_cli_process_sheet(tmp_path, monkeypatch, *, console_width=80, **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    console, buf = _console_cli_process_sheet(console_width)
    app = CliApp(
        session_id="test-session",
        store_root=tmp_path,
        console=console,
        **kwargs,
    )
    return app, buf


def _ok_turn_cli_process_sheet(query, *, session, model, messages, on_event, api_base, api_key_env):
    if messages is not None:
        messages.append({"role": "user", "content": query})
        messages.append({"role": "assistant", "content": "done"})
    ev = ToolEvent("screen_polymer_separation", {"feed": "LDPE"}, {"ok": True})
    if on_event:
        on_event(ev)
    return TurnResult(
        answer="ok", status="ok", tool_trace=[ev],
        turn_record="turn-1", tool_rounds=1,
    )


def _accept_defaults(message, default=""):
    if str(message).startswith("edit"):
        return "run"
    return default


def test_first_run_precipitation_is_factory_35_not_scenario_25():
    defaults = tea.first_run_sheet_defaults()
    assert defaults["precipitation_temperature_c"] == pytest.approx(35.0)
    seeded = tea.seed_public_process_config({"target_polymer": "LDPE"})
    assert seeded["precipitation_temperature_c"] == pytest.approx(35.0)
    seeded_from_model = tea.seed_public_process_config({
        "precipitation_temperature_c": 25.0,
    })
    assert seeded_from_model["precipitation_temperature_c"] == pytest.approx(25.0)


def test_public_price_and_labor_aliases_reach_scenario_config():
    record = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case")) == "C1"
    )
    cfg = record["config"]
    normalized = tea._scenario_config({
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    })
    assert normalized["solvent_price"] == pytest.approx(float(cfg["solvent_price"]))
    assert normalized["labor_cost"] == pytest.approx(float(cfg["labor_cost"]))
    assert tea._cache_index().get(tea._config_key(normalized))["label"] == (
        record["label"]
    )


def test_ask_never_opens_the_process_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)

    def forbidden(*args, **kwargs):
        raise AssertionError("CliApp.ask must not prompt the process sheet")

    monkeypatch.setattr(cli.Prompt, "ask", forbidden)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    result = app.ask("screen LDPE")
    assert result.status == "ok"
    assert app._confirmation_sheet_submitted is False


def test_clear_drops_the_process_buffer_and_bit(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    app._process_buffer = tea.seed_public_process_config()
    app._confirmation_sheet_submitted = True
    app.handle_command("/clear")
    assert app._process_buffer is None
    assert app._confirmation_sheet_submitted is False


def test_cli_direct_dispatch_uses_the_same_sheet_object(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {
                "target_polymer": "LDPE", "solvent": "toluene",
            },
        },
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_process"
    dispatched = captured["kwargs"]["process_config"]
    assert dispatched is sheet
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_abort_does_not_set_the_bit_or_run_model_args(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: None)
    called = []

    def original(name, **kwargs):
        called.append(name)
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    assert called == []
    assert result["error_code"] == "process_confirmation_aborted"
    assert app._confirmation_sheet_submitted is False


def test_transcript_process_config_refuses_before_the_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("unknown process_config keys must not open the sheet")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    called = []

    def original(name, **kwargs):
        called.append(name)
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {
                "polymer": "LDPE",
                "solvent": "Dodecane",
                "temperature_c": 105.0,
                "solubility_pct": 14.54225715,
            },
        },
    )
    assert called == []
    assert result["success"] is False
    assert result["error_code"] == "unknown_process_field"
    assert result["extra_keys"] == ["polymer", "solubility_pct", "temperature_c"]
    assert result["error_code"] != "process_confirmation_aborted"
    assert app._confirmation_sheet_submitted is False


def test_non_tty_skips_the_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {
        "mode": "evaluate",
        "process_config": {"target_polymer": "LDPE"},
    }
    app._cli_direct_dispatch(
        original, "evaluate_process", model_args,
    )
    assert captured["kwargs"]["process_config"] == model_args["process_config"]
    assert app._confirmation_sheet_submitted is False


def test_first_old_evaluate_name_shortlist_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "auto",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["engine_mode"] == "auto"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_old_evaluate_name_scenarios_still_win_the_seed(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {
                "target_polymer": "HDPE", "solvent": "Toluene",
            },
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert seen[0]["target_polymer"] == "HDPE"
    assert seen[0]["solvent"] == "Toluene"
    assert seen[0]["target_polymer"] != "LDPE"
    assert captured["kwargs"]["process_config"] is sheet
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]


def test_first_old_sensitivity_name_shortlist_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "parameter": "solvent_price",
            "engine_mode": "auto",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert captured["kwargs"]["engine_mode"] == "auto"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_old_sensitivity_name_scenario_still_wins_the_seed(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "process_config": {"target_polymer": "HDPE", "solvent": "Toluene"},
            "parameter": "solvent_price",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert seen[0]["target_polymer"] == "HDPE"
    assert seen[0]["solvent"] == "Toluene"
    assert seen[0]["target_polymer"] != "LDPE"
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]


def test_ask_path_does_not_arm_cli_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    assert app._cli_direct_active is False
    app.ask("hello")
    assert app._cli_direct_active is False


def test_sheet_accept_defaults_keeps_the_buffer_object(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 130.0,
    })
    submitted = app._edit_process_sheet(seed, prompt_fn=_accept_defaults)
    assert submitted is seed
    shown = buf.getvalue()
    assert "10%" in shown
    assert "expert_surface_only" in shown
    assert tea.missing_public_process_fields(submitted) == []


def test_later_evaluate_runs_the_confirmed_buffer_not_model_args(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    prompts = []

    def record_prompt(seed, **kwargs):
        prompts.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = []

    def original(name, **kwargs):
        captured.append({"name": name, "kwargs": kwargs})
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {"target_polymer": "LDPE", "irr": 0.10},
        },
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "engine_mode": "live",
            "process_config": {"target_polymer": "LDPE", "irr": 0.15},
        },
    )
    assert len(prompts) == 1
    assert len(captured) == 2
    second = captured[1]["kwargs"]
    assert second["process_config"] is sheet
    assert second["process_config"]["irr"] == pytest.approx(0.10)
    assert second["engine_mode"] == "live"
    assert sheet["irr"] != 0.15
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "process_config": {"irr": 0.20},
            "parameter": "solvent_price",
        },
    )
    assert len(prompts) == 1
    third = captured[2]["kwargs"]
    assert third["process_config"] is sheet
    assert third["parameter"] == "solvent_price"


def test_later_evaluate_drops_screening_shortlist_from_model_args(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{"target_polymer": "LDPE", "solvent": "toluene"}],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    assert "screening_shortlist" not in captured[1]
    assert "held_process_basis" not in captured[1]
    assert captured[1]["process_config"] is sheet


def test_process_buffer_is_the_first_confirm_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    edited = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.12,
    })
    app._process_buffer = edited
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return seed

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {"target_polymer": "HDPE", "irr": 0.10},
        },
    )
    assert seen[0] is edited
    assert captured["kwargs"]["process_config"] is edited
    assert captured["kwargs"]["process_config"]["irr"] == pytest.approx(0.12)


def test_evaluate_process_evaluate_mode_uses_the_same_sheet_object(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {"target_polymer": "LDPE", "solvent": "toluene"},
        },
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert "process_configs" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_first_evaluate_process_shortlist_pops_handoff_and_seeds_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    seen = []

    def record_prompt(seed, **kwargs):
        seen.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "auto",
        },
    )
    assert result == {"success": True}
    assert seen[0]["target_polymer"] == "LDPE"
    assert seen[0]["solvent"] == "Dodecane"
    assert seen[0]["dissolution_temperature_c"] == 145.0
    assert seen[0]["energy_case"] == "C1"
    assert seen[0]["target_mass_percent"] == 55.0
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["engine_mode"] == "auto"
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    assert "process_configs" not in captured["kwargs"]
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_non_tty_evaluate_process_keeps_the_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {
        "mode": "evaluate",
        "screening_shortlist": {
            "source": "explicit",
            "items": [{"target_polymer": "LDPE", "solvent": "Dodecane"}],
        },
        "held_process_basis": {"energy_case": "C1"},
        "engine_mode": "auto",
    }
    app._cli_direct_dispatch(original, "evaluate_process", model_args)
    assert captured["kwargs"] == model_args
    assert app._confirmation_sheet_submitted is False


def test_evaluate_process_lookup_mode_does_not_open_the_sheet(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {"success": True}

    def forbidden(*args, **kwargs):
        raise AssertionError("lookup mode must not prompt the process sheet")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    model_args = {
        "mode": "lookup",
        "lookup_filter": {"target_polymer": "LDPE"},
    }
    app._cli_direct_dispatch(original, "evaluate_process", model_args)
    assert captured["kwargs"] == model_args
    assert app._confirmation_sheet_submitted is False


def test_later_evaluate_process_runs_the_confirmed_buffer(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    prompts = []

    def record_prompt(seed, **kwargs):
        prompts.append(seed)
        return sheet

    monkeypatch.setattr(app, "_edit_process_sheet", record_prompt)
    captured = []

    def original(name, **kwargs):
        captured.append({"name": name, "kwargs": kwargs})
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_configs": [{"target_polymer": "LDPE", "irr": 0.10}],
        },
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "engine_mode": "live",
            "process_config": {"target_polymer": "LDPE", "irr": 0.15},
        },
    )
    assert len(prompts) == 1
    assert len(captured) == 2
    second = captured[1]["kwargs"]
    assert second["process_config"] is sheet
    assert second["process_config"]["irr"] == pytest.approx(0.10)
    assert second["engine_mode"] == "live"
    assert "process_configs" not in second
    assert sheet["irr"] != 0.15


def test_evaluate_process_sensitivity_mode_uses_the_same_sheet_object(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "process_config": {"target_polymer": "LDPE"},
            "parameter": "solvent_price",
        },
    )
    assert result == {"success": True}
    assert captured["kwargs"]["process_config"] is sheet
    assert captured["kwargs"]["parameter"] == "solvent_price"
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_later_sensitivity_keeps_parameter_and_uses_confirmed_buffer(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "irr": 0.10,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "sensitivity",
            "parameter": "solvent_price",
            "engine_mode": "live",
            "process_config": {"irr": 0.20},
        },
    )
    assert captured[1]["process_config"] is sheet
    assert captured[1]["parameter"] == "solvent_price"
    assert captured[1]["engine_mode"] == "live"
    assert sheet["irr"] != 0.20


def test_evaluate_process_route_mode_opens_the_sheet_without_process_config(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": "C1",
        "precipitation_temperature_c": 25.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["name"] = name
        captured["kwargs"] = kwargs
        return {"success": True}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "route", "handle": "plan-1", "engine_mode": "auto"},
    )
    assert result == {"success": True}
    assert captured["name"] == "evaluate_process"
    assert captured["kwargs"]["handle"] == "plan-1"
    assert captured["kwargs"]["engine_mode"] == "auto"
    assert "process_config" not in captured["kwargs"]
    assert "process_configs" not in captured["kwargs"]
    assert captured["kwargs"]["processing_capacity_mt_per_yr"] == 20_000.0
    assert captured["kwargs"]["energy_case"] == "C1"
    assert captured["kwargs"]["precipitation_temperature_c"] == 25.0
    assert app._confirmation_sheet_submitted is True
    assert app._process_buffer is sheet


def test_later_route_keeps_handle_and_does_not_inject_process_config(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": "C1",
        "precipitation_temperature_c": 25.0,
        "irr": 0.10,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = []

    def original(name, **kwargs):
        captured.append(kwargs)
        return {"success": True}

    app._cli_direct_active = True
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "route", "handle": "plan-1", "engine_mode": "live"},
    )
    assert captured[0]["process_config"] is sheet
    assert captured[1]["handle"] == "plan-1"
    assert "process_config" not in captured[1]
    assert captured[1]["engine_mode"] == "live"
    assert captured[1]["processing_capacity_mt_per_yr"] == 20_000.0


def test_confirmation_sheet_origin_helper_from_screen_and_default():
    held = {"energy_case": "C1", "target_mass_percent": 55.0}
    submitted = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        **held,
    })
    origin = tea.confirmation_sheet_field_origin(
        submitted,
        snapshot=submitted,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "temperature_c": 145.0,
        },
        held_keys=list(held),
    )
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["energy_case"] == "supplied"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"
    assert origin["processing_capacity_mt_per_yr"] == "default"
    assert "from_screen" not in {
        origin[name] for name in tea._NINE_HELD_PUBLIC_FIELDS
        if name in origin
    }


def test_confirmation_sheet_origin_helper_edit_is_supplied():
    seed = tea.seed_public_process_config({"energy_case": "C1"})
    submitted = dict(seed)
    submitted["target_mass_percent"] = 55.0
    origin = tea.confirmation_sheet_field_origin(
        submitted, snapshot=seed,
    )
    assert origin["target_mass_percent"] == "supplied"
    assert origin["processing_capacity_mt_per_yr"] == "default"


def test_flatten_shortlist_mints_from_screen_on_the_envelope(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)
    captured = {}

    def original(name, **kwargs):
        captured["kwargs"] = kwargs
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["process_config"])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
            "engine_mode": "auto",
        },
    )
    assert "screening_shortlist" not in captured["kwargs"]
    assert "held_process_basis" not in captured["kwargs"]
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["energy_case"] == "supplied"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"
    assert result["field_origin"]["solvent"] == "from_screen"


def test_flatten_scenarios_do_not_mint_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "HDPE",
        "solvent": "Toluene",
        "dissolution_temperature_c": 90.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["process_config"])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {"target_polymer": "HDPE", "solvent": "Toluene"},
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {"energy_case": "C1"},
        },
    )
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["target_polymer"] != "from_screen"
    assert origin["precipitation_temperature_c"] == "default"


def test_non_tty_flatten_does_not_mint_default(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [{"target_polymer": "LDPE"}],
        }

    def forbidden(*args, **kwargs):
        raise AssertionError("non-TTY must not prompt")

    monkeypatch.setattr(app, "_edit_process_sheet", forbidden)
    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original, "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    assert "field_origin" not in result
    assert "field_origin" not in result["comparison_rows"][0]


def test_flatten_evaluate_process_mints_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["process_config"])],
        }

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["precipitation_temperature_c"] == "default"


def test_process_edit_refreshes_sheet_field_origin():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    previous = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    submitted = dict(seed)
    submitted["precipitation_temperature_c"] = 25.0
    origin = tea.confirmation_sheet_field_origin_after_edit(
        submitted, snapshot=seed, previous=previous,
    )
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["precipitation_temperature_c"] != "default"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["processing_capacity_mt_per_yr"] == "default"
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["energy_case"] == "supplied"


def test_process_command_edit_is_supplied_on_later_stamp(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        if kwargs.get("scenarios"):
            row = dict(kwargs["scenarios"][0])
        else:
            row = dict(kwargs.get("process_config") or {})
        return {"success": True, "comparison_rows": [row]}

    app._cli_direct_active = True
    first = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    assert first["comparison_rows"][0]["field_origin"]["target_mass_percent"] == (
        "supplied"
    )
    assert first["comparison_rows"][0]["field_origin"][
        "precipitation_temperature_c"
    ] == "default"

    def edit_precip(seed, **kwargs):
        seed["precipitation_temperature_c"] = 25.0
        return seed

    monkeypatch.setattr(app, "_edit_process_sheet", edit_precip)
    app.handle_command("/process")
    later = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {"mode": "evaluate", "process_config": {"target_polymer": "LDPE"}},
    )
    origin = later["comparison_rows"][0]["field_origin"]
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["precipitation_temperature_c"] != "default"
    assert origin["target_mass_percent"] == "supplied"
    assert origin["target_polymer"] == "from_screen"
    assert later["comparison_rows"][0]["precipitation_temperature_c"] == (
        pytest.approx(25.0)
    )


def test_process_abort_does_not_refresh_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    app, _buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    sheet = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: sheet)

    def original(name, **kwargs):
        return {
            "success": True,
            "comparison_rows": [dict(kwargs["process_config"])],
        }

    app._cli_direct_active = True
    first = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "process_config": {
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
                "dissolution_temperature_c": 145.0,
            },
        },
    )
    before = dict(app._sheet_field_origin)
    monkeypatch.setattr(app, "_edit_process_sheet", lambda seed, **kwargs: None)
    app.handle_command("/process")
    assert app._sheet_field_origin == before
    assert first["comparison_rows"][0]["field_origin"][
        "precipitation_temperature_c"
    ] == "default"


def test_confirmation_sheet_row_origin_recorded_map_wins():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    assert tea.confirmation_sheet_row_origin(
        "target_polymer", seed, origin=origin,
    ) == "from_screen"
    assert tea.confirmation_sheet_row_origin(
        "precipitation_temperature_c", seed, origin=origin,
    ) == "default"
    assert tea.confirmation_sheet_row_origin(
        "target_mass_percent", seed, origin=origin,
    ) == "supplied"
    assert tea.confirmation_sheet_row_origin("target_polymer", seed) == (
        "supplied"
    )
    assert tea.confirmation_sheet_row_origin("target_polymer", seed) != (
        "from_screen"
    )
    assert tea.confirmation_sheet_row_origin(
        "precipitation_temperature_c", seed,
    ) == "default"
    empty = dict(seed)
    empty.pop("target_polymer")
    assert tea.confirmation_sheet_row_origin("target_polymer", empty) == (
        "missing"
    )
    assert tea.confirmation_sheet_row_origin(
        "facilities", seed, origin=origin,
    ) == "missing"
    assert tea.confirmation_sheet_row_origin(
        "solvent", seed, origin={"solvent": "fail"},
    ) == "missing"
    assert tea.confirmation_sheet_row_origin(
        "solvent", seed, origin={"solvent": "inherited"},
    ) == "inherited"


def test_sheet_print_shows_origin_column(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    app._print_process_sheet(seed, origin=origin)
    shown = buf.getvalue()
    assert "origin" in shown
    assert "from_screen" in shown
    assert "default" in shown
    assert "supplied" in shown
    assert "inherited" not in shown


def test_sheet_print_without_map_does_not_mint_from_screen(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
    })
    app._print_process_sheet(seed)
    shown = buf.getvalue()
    assert "from_screen" not in shown
    assert "inherited" not in shown
    assert "default" in shown
    assert "supplied" in shown


def test_first_run_sheet_print_shows_from_screen(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.Prompt, "ask", _accept_defaults)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)

    def original(name, **kwargs):
        if kwargs.get("scenarios"):
            row = dict(kwargs["scenarios"][0])
        else:
            row = dict(kwargs.get("process_config") or {})
        return {"success": True, "comparison_rows": [row]}

    app._cli_direct_active = True
    result = app._cli_direct_dispatch(
        original,
        "evaluate_process",
        {
            "mode": "evaluate",
            "screening_shortlist": {
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
            "held_process_basis": {
                "energy_case": "C1",
                "target_mass_percent": 55.0,
            },
        },
    )
    shown = buf.getvalue()
    assert "from_screen" in shown
    assert "default" in shown
    assert "supplied" in shown
    origin = result["comparison_rows"][0]["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["precipitation_temperature_c"] == "default"
    assert origin["target_mass_percent"] == "supplied"


def test_confirmation_sheet_row_units_bind_per_field():
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    assert tea.confirmation_sheet_row_units(
        "precipitation_temperature_c", seed,
    ) == "°C"
    assert tea.confirmation_sheet_row_units(
        "dissolution_temperature_c", seed,
    ) == "°C"
    assert tea.confirmation_sheet_row_units("target_polymer", seed) == ""
    assert tea.confirmation_sheet_row_units("target_polymer", seed) != "°C"
    assert tea.confirmation_sheet_row_units("irr", seed) == "fraction"
    assert tea.confirmation_sheet_row_units("irr", seed) != "%"
    assert tea.confirmation_sheet_row_units("irr", seed) != "°C"
    assert tea.confirmation_sheet_row_units(
        "processing_capacity_mt_per_yr", seed,
    ) == "MT/yr"
    assert tea.confirmation_sheet_row_units(
        "target_mass_percent", seed,
    ) == "wt%"
    assert tea.confirmation_sheet_row_units(
        "solvent_price_usd_per_kg", seed,
    ) == "USD/kg"
    assert tea.confirmation_sheet_row_units(
        "labor_cost_usd_per_employee_yr", seed,
    ) == "USD/employee/yr"
    assert tea.confirmation_sheet_row_units("finance_years", seed) == "years"
    assert tea.confirmation_sheet_row_units("startup_months", seed) == (
        "months"
    )
    assert tea.confirmation_sheet_row_units("finance_years", seed) != (
        tea.confirmation_sheet_row_units("startup_months", seed)
    )
    assert tea.confirmation_sheet_row_units(
        "natural_gas_price_usd_per_m3", seed,
    ) == "USD/m3"
    c2 = dict(seed)
    c2["energy_case"] = "C2"
    assert tea.confirmation_sheet_row_units(
        "natural_gas_price_usd_per_m3", c2,
    ) == ""
    assert tea.confirmation_sheet_row_units("facilities", seed) == ""
    assert tea.confirmation_sheet_row_units("sell_leftover_plastic", seed) == (
        ""
    )


def test_sheet_print_shows_units_column(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    seed = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
        "target_mass_percent": 55.0,
    })
    origin = tea.confirmation_sheet_field_origin(
        seed,
        snapshot=seed,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case", "target_mass_percent"],
    )
    app._print_process_sheet(seed, origin=origin)
    shown = buf.getvalue()
    assert "units" in shown
    assert "°C" in shown
    assert "wt%" in shown
    assert "MT/yr" in shown
    assert "fraction" in shown
    assert "from_screen" in shown
    assert "origin" in shown


def test_c2_not_on_this_instance_lists_ng_and_steam_power():
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    c3 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C3",
    })
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    assert tea.confirmation_sheet_not_on_this_instance(c1) == ()
    assert tea.confirmation_sheet_not_on_this_instance(c3) == ()
    gated = tea.confirmation_sheet_not_on_this_instance(c2)
    assert gated == (
        "natural_gas_price_usd_per_m3",
        "steam_power_depreciation",
    )
    assert "facilities" not in gated
    assert "natural_gas_price_usd_per_m3" not in tea.public_process_field_names(
        energy_case="C2",
    )
    assert "natural_gas_price_usd_per_m3" not in tea.missing_public_process_fields(
        c2,
    )
    assert tea.confirmation_sheet_not_on_this_instance_label(c2) == (
        "not on this instance (energy_case=C2)"
    )
    assert "C1" not in tea.confirmation_sheet_not_on_this_instance_label(c2)


def test_c2_sheet_print_states_not_on_this_instance(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    app._print_process_sheet(c2)
    shown = buf.getvalue()
    assert "not on this" in shown
    assert "energy_case=C2" in shown
    assert "USD/m3" not in shown
    assert "energy_case=C1" not in shown
    assert re.search(r"^natural_gas_price_usd_per_m3\b", shown, re.M)
    assert re.search(r"^steam_power_depreciation\b", shown, re.M)
    ng = next(
        line for line in shown.splitlines()
        if line.startswith("natural_gas_price_usd_per_m3")
    )
    steam = next(
        line for line in shown.splitlines()
        if line.startswith("steam_power_depreciation")
    )
    phrase = tea.confirmation_sheet_not_on_this_instance_label(c2)
    assert phrase in ng
    assert len(ng) <= 80
    assert len(steam) <= 80
    assert phrase in steam
    fac = _sheet_field_block(shown, "facilities")
    assert any(
        "derived from energy_case" in line for line in fac.splitlines()
    )
    assert all(len(line) <= 80 for line in fac.splitlines())


def test_c1_sheet_print_does_not_state_not_on_this_instance(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    assert "not on this instance" not in shown
    assert "USD/m3" in shown


def test_derived_energy_case_rows_bind_c1_c2_c3():
    c1 = tea.seed_public_process_config({"energy_case": "C1"})
    c2 = tea.seed_public_process_config({"energy_case": "C2"})
    c3 = tea.seed_public_process_config({"energy_case": "C3"})
    assert tea.confirmation_sheet_derived_energy_case_rows(c1) == (
        ("facilities", True),
        ("turbogenerator", True),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c2) == (
        ("facilities", False),
        ("turbogenerator", False),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c3) == (
        ("facilities", True),
        ("turbogenerator", False),
    )
    assert tea.confirmation_sheet_derived_energy_case_rows(c1) != (
        tea.confirmation_sheet_derived_energy_case_rows(c2)
    )
    assert tea.confirmation_sheet_derived_energy_case_label() == (
        "derived from energy_case"
    )
    names = tea.public_process_field_names(energy_case="C1")
    assert "facilities" not in names
    assert "turbogenerator" not in names
    gated = tea.confirmation_sheet_not_on_this_instance(c2)
    assert "facilities" not in gated
    assert "turbogenerator" not in gated


def test_c1_sheet_print_shows_derived_facilities_true(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    assert re.search(r"^facilities\s+true\b", shown, re.M)
    assert re.search(r"^turbogenerator\s+true\b", shown, re.M)
    assert re.search(r"^facilities\s+false\b", shown, re.M) is None


def test_c2_sheet_print_shows_derived_facilities_false(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c2 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C2",
    })
    app._print_process_sheet(c2)
    shown = buf.getvalue()
    assert re.search(r"^facilities\s+false\b", shown, re.M)
    assert re.search(r"^turbogenerator\s+false\b", shown, re.M)
    assert re.search(r"^facilities\s+true\b", shown, re.M) is None
    assert "not on this" in shown
    assert "energy_case=C2" in shown


def test_sheet_print_keeps_long_public_names_contiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    app._print_process_sheet(c1)
    shown = buf.getvalue()
    for name in (
        "precipitation_temperature_format",
        "labor_cost_usd_per_employee_yr",
        "natural_gas_price_usd_per_m3",
        "processing_capacity_mt_per_yr",
        "centrifuged_plastic_solvent_content_pct",
    ):
        assert re.search(rf"^{name}\b", shown, re.M)
        assert f"{name}…" not in shown
    for name in tea.public_process_field_names(energy_case="C1"):
        assert re.search(rf"^{re.escape(name)}\b", shown, re.M)
    assert "…" not in shown
    assert re.search(r"^facilities\s+true\b", shown, re.M)
    assert all(
        len(line) <= 80
        for line in shown.splitlines()
        if line.startswith(tuple(tea.public_process_field_names(energy_case="C1")))
        or line.startswith(("facilities", "turbogenerator"))
    )


def test_confirmation_sheet_format_row_aligns_columns():
    rows = (
        ("target_polymer", "LDPE", "", "supplied"),
        ("solvent", "Dodecane", "", "supplied"),
        ("precipitation_temperature_c", "35", "°C", "default"),
        ("dissolution_temperature_c", "145", "°C", "supplied"),
        ("facilities", "true", "", "derived from energy_case"),
        (
            "natural_gas_price_usd_per_m3",
            "not on this instance (energy_case=C2)",
            "",
            "",
        ),
    )
    widths = tea.confirmation_sheet_column_widths(rows)
    lines = [
        tea.confirmation_sheet_format_row(*row, widths) for row in rows
    ]
    assert _token_col(lines[0], "LDPE") == _token_col(lines[1], "Dodecane")
    assert _token_col(lines[0], "LDPE") == _token_col(lines[4], "true")
    assert _token_col(lines[2], "°C") == _token_col(lines[3], "°C")
    assert _token_col(lines[0], "supplied") == _token_col(lines[1], "supplied")
    assert _token_col(lines[0], "supplied") == _token_col(
        lines[4], "derived from energy_case",
    )
    assert _token_col(lines[4], "derived from energy_case") != _token_col(
        lines[2], "°C",
    )
    assert "not on this instance (energy_case=C2)" in lines[5]
    assert len(widths) == 4
    field_width, value_width, units_width, origin_width = widths
    four_slot = (
        field_width + 2 + value_width + 2 + units_width + 2 + origin_width
    )
    assert four_slot > 80
    assert all(
        len(visual) <= 80
        for row in lines
        for visual in row.splitlines()
    )
    wide = [
        tea.confirmation_sheet_format_row(*row, widths, line_width=200)
        for row in rows
    ]
    assert _token_col(wide[2], "°C") == _token_col(wide[3], "°C")
    assert all("\n" not in row for row in wide)
    assert "derived from energy_case" not in tea._SHEET_ORIGIN_TOKENS
    assert "…" not in "".join(lines)


def test_confirmation_sheet_splits_unbreakable_value():
    unbreakable = "V" * 90
    rows = (
        ("short_field", unbreakable, "u", "supplied"),
        ("precipitation_temperature_c", "35", "°C", "default"),
        ("facilities", "true", "", "derived from energy_case"),
    )
    widths = tea.confirmation_sheet_column_widths(rows)
    lines = [
        tea.confirmation_sheet_format_row(*row, widths) for row in rows
    ]
    visuals = [visual for row in lines for visual in row.splitlines()]
    assert all(len(visual) <= 80 for visual in visuals)
    assert not any(unbreakable in visual for visual in visuals)
    assert "…" not in "".join(visuals)
    assert any(
        "derived from energy_case" in visual for visual in lines[2].splitlines()
    )
    assert _token_col(lines[2], "derived from energy_case") == _token_col(
        lines[0], "supplied",
    )
    assert _token_col(lines[2], "derived from energy_case") != _token_col(
        lines[1], "°C",
    )
    assert "derived from energy_case" not in tea._SHEET_ORIGIN_TOKENS
    wide = tea.confirmation_sheet_format_row(
        *rows[0], widths, line_width=200,
    )
    assert "\n" not in wide
    assert unbreakable in wide


def test_confirmation_sheet_format_row_honors_line_width():
    rows = (
        ("centrifuged_plastic_solvent_content_pct", "50.0", "wt%", "default"),
        ("precipitation_temperature_c", "35", "°C", "default"),
        ("facilities", "true", "", "derived from energy_case"),
    )
    widths = tea.confirmation_sheet_column_widths(rows)
    narrow = [
        tea.confirmation_sheet_format_row(*row, widths, line_width=40)
        for row in rows
    ]
    typical = [
        tea.confirmation_sheet_format_row(*row, widths, line_width=80)
        for row in rows
    ]
    narrow_visuals = [visual for row in narrow for visual in row.splitlines()]
    typical_visuals = [visual for row in typical for visual in row.splitlines()]
    assert all(len(visual) <= 40 for visual in narrow_visuals)
    assert all(len(visual) <= 80 for visual in typical_visuals)
    assert max(len(visual) for visual in typical_visuals) > 40
    assert any(
        visual.startswith("centrifuged_plastic_solvent_content_pct")
        for visual in narrow_visuals
    )
    assert any(
        "derived from energy_case" in visual for visual in narrow[2].splitlines()
    )
    assert _token_col(narrow[2], "derived from energy_case") != _token_col(
        narrow[1], "°C",
    )
    assert "…" not in "".join(narrow_visuals)
    assert "derived from energy_case" not in tea._SHEET_ORIGIN_TOKENS


def test_sheet_print_follows_console_width(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch, console_width=40)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    origin = tea.confirmation_sheet_field_origin(
        c1,
        snapshot=c1,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case"],
    )
    app._print_process_sheet(c1, origin=origin)
    shown = buf.getvalue()
    names = list(tea.public_process_field_names(energy_case="C1"))
    names.extend(["facilities", "turbogenerator"])
    blocks = [_sheet_field_block(shown, name) for name in names]
    assert all(
        len(line) <= 40
        for block in blocks
        for line in block.splitlines()
    )
    assert all(len(line) <= 40 for line in shown.splitlines())
    assert re.search(r"^centrifuged_plastic_solvent_content_pct\b", shown, re.M)
    assert re.search(r"^labor_cost_usd_per_employee_yr\b", shown, re.M)
    fac = _sheet_field_block(shown, "facilities")
    precip = _sheet_field_block(shown, "precipitation_temperature_c")
    assert any(
        "derived from energy_case" in line for line in fac.splitlines()
    )
    assert _token_col(fac, "derived from energy_case") != _token_col(
        precip, "°C",
    )
    assert "…" not in shown
    assert "derived from energy_case" not in tea._SHEET_ORIGIN_TOKENS


def test_sheet_print_aligns_value_units_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "run_turn", _ok_turn_cli_process_sheet)
    app, buf = _app_cli_process_sheet(tmp_path, monkeypatch)
    c1 = tea.seed_public_process_config({
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 145.0,
        "energy_case": "C1",
    })
    origin = tea.confirmation_sheet_field_origin(
        c1,
        snapshot=c1,
        screening_item={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        },
        held_keys=["energy_case"],
    )
    app._print_process_sheet(c1, origin=origin)
    shown = buf.getvalue()
    poly = _sheet_field_block(shown, "target_polymer")
    solv = _sheet_field_block(shown, "solvent")
    precip = _sheet_field_block(shown, "precipitation_temperature_c")
    diss = _sheet_field_block(shown, "dissolution_temperature_c")
    fac = _sheet_field_block(shown, "facilities")
    header_units = next(
        line for line in shown.splitlines()
        if line.strip().startswith("units") and "origin" in line
    )
    assert _token_col(poly, "LDPE") == _token_col(solv, "Dodecane")
    assert _token_col(fac, "true") == _token_col(poly, "LDPE")
    assert _token_col(precip, "°C") == _token_col(diss, "°C")
    assert header_units.find("units") == _token_col(precip, "°C")
    assert _token_col(poly, "from_screen") == _token_col(solv, "from_screen")
    assert _token_col(fac, "derived from energy_case") == _token_col(
        poly, "from_screen",
    )
    assert _token_col(fac, "derived from energy_case") != _token_col(
        precip, "°C",
    )
    assert any(
        "derived from energy_case" in line for line in fac.splitlines()
    )
    assert all(len(line) <= 80 for line in shown.splitlines())
    assert "…" not in shown
    assert re.search(r"^labor_cost_usd_per_employee_yr\b", shown, re.M)


# --- from test_planner_breadth.py: Planner breadth knobs, origin stamps, handles, and planner_routes rerank.
_TRIPLE = ["LDPE", "PP", "PS"]


_QUAD = ["LDPE", "HDPE", "PP", "PS"]


_PAIR = ["LDPE", "PP"]


_PLAN = "plan_multistage_separation"


def _data(raw: str) -> dict:
    envelope = parse_tool_result(raw)
    return envelope["data"]


def _plan(**kwargs) -> dict:
    return _data(agent.BY_NAME[_PLAN].fn(**kwargs))


def _first_stage_identities(payload: dict) -> dict[str, set[tuple]]:
    seen: dict[str, set[tuple]] = {}
    for route in payload.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        polymer = step.get("dissolved_polymer")
        if not polymer:
            continue
        seen.setdefault(polymer, set()).add(
            (step.get("solvent"), step.get("temperature_c")),
        )
    return seen


def _ldpe_from_triple(payload: dict) -> dict | None:
    for route in payload.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        if step.get("dissolved_polymer") != "LDPE":
            continue
        if set(step.get("retained_polymers") or []) == {"PP", "PS"}:
            return step
    return None


def test_triple_built_in_m1_screens_and_origin():
    payload = _plan(feed_polymers=_TRIPLE)
    assert payload.get("success") is True
    assert payload.get("subset_screens_evaluated") == 4
    assert payload.get("breadth_origin") == "built_in"
    assert payload.get("beam_origin") == "built_in"
    assert payload.get("branch_rule") == "count"
    assert payload.get("breadth") == 1
    assert payload.get("top_k_routes") == 5
    identities = _first_stage_identities(payload)
    assert identities
    assert all(len(items) == 1 for items in identities.values())
    for beam in (1, 6):
        other = _plan(feed_polymers=_TRIPLE, top_k_routes=beam)
        assert other.get("subset_screens_evaluated") == 4
        assert other.get("beam_origin") == "query"
        assert other.get("top_k_routes") == beam


def test_triple_breadth_5_still_four_screens_and_extra_first_stage():
    payload = _plan(feed_polymers=_TRIPLE, breadth=5, branch_rule="count")
    assert payload.get("success") is True
    assert payload.get("subset_screens_evaluated") == 4
    assert payload.get("breadth_origin") == "query"
    assert payload.get("breadth") == 5
    supplied = [
        step.get("stage_branch_supplied")
        for route in payload.get("top_k_sequences") or []
        for step in route.get("steps") or []
        if step.get("stage_branch_supplied") is not None
    ]
    assert supplied and max(supplied) > 1
    for route in payload.get("top_k_sequences") or []:
        for step in route.get("steps") or []:
            value = step.get("stage_branch_supplied")
            if value is None:
                continue
            assert value <= 5
            assert step.get("stage_branch_requested") == 5


def test_ldpe_split_window_keeps_span_relationship():
    tight = _plan(
        feed_polymers=_TRIPLE,
        branch_rule="window",
        selectivity_window_pct=1.8,
        top_k_routes=50,
    )
    wide = _plan(
        feed_polymers=_TRIPLE,
        branch_rule="window",
        selectivity_window_pct=2.0,
        top_k_routes=50,
    )
    assert tight.get("success") is True and wide.get("success") is True
    assert tight.get("subset_screens_evaluated") == wide.get("subset_screens_evaluated") == 4
    tight_step = _ldpe_from_triple(tight)
    wide_step = _ldpe_from_triple(wide)
    assert tight_step is not None and wide_step is not None
    assert tight_step["stage_branch_supplied"] == 4
    assert wide_step["stage_branch_supplied"] == 5
    assert tight_step["window_truncated_to_screen_cap"] is False
    assert wide_step["window_truncated_to_screen_cap"] is False


def test_four_polymer_screens_independent_of_beam():
    narrow = _plan(feed_polymers=_QUAD, top_k_routes=5)
    wide = _plan(feed_polymers=_QUAD, top_k_routes=50)
    assert narrow.get("subset_screens_evaluated") == 11
    assert wide.get("subset_screens_evaluated") == 11
    assert len(wide.get("top_k_sequences") or []) > 10
    assert wide.get("top_k_routes") == 50


def test_refuses_named_cap_and_combination_misses():
    beam = _plan(feed_polymers=_PAIR, top_k_routes=51)
    assert beam.get("success") is False
    assert beam.get("error_code") == "planner_beam_exceeds_cap"
    assert beam.get("cap") == 50
    assert beam.get("requested") == 51

    branch = _plan(feed_polymers=_PAIR, breadth=51)
    assert branch.get("success") is False
    assert branch.get("error_code") == "stage_branch_exceeds_cap"
    assert branch.get("cap") == 50
    assert branch.get("requested") == 51

    invalid = _plan(feed_polymers=_PAIR, breadth=0)
    assert invalid.get("error_code") == "invalid_breadth"

    missing = _plan(feed_polymers=_PAIR, branch_rule="window")
    assert missing.get("error_code") == "missing_selectivity_window"

    mixed = _plan(
        feed_polymers=_PAIR, branch_rule="count", selectivity_window_pct=2.0,
    )
    assert mixed.get("error_code") == "not_applicable_in_branch_rule"

    window_plus_m = _plan(
        feed_polymers=_PAIR,
        branch_rule="window",
        selectivity_window_pct=2.0,
        breadth=3,
    )
    assert window_plus_m.get("error_code") == "not_applicable_in_branch_rule"

    bad_window = _plan(
        feed_polymers=_PAIR, branch_rule="window", selectivity_window_pct=0,
    )
    assert bad_window.get("error_code") == "invalid_selectivity_window"

    bad_rule = _plan(feed_polymers=_PAIR, branch_rule="cluster")
    assert bad_rule.get("error_code") == "invalid_branch_rule"


def test_session_default_origin_and_query_wins():
    session = new_session()
    session["planner_breadth"] = {"branch_rule": "count", "breadth": 5}
    with bind_tool_session(session):
        from_session = _plan(feed_polymers=_TRIPLE)
        from_query = _plan(feed_polymers=_TRIPLE, breadth=1)
    assert from_session.get("breadth_origin") == "session_default"
    assert from_session.get("breadth") == 5
    assert from_query.get("breadth_origin") == "query"
    assert from_query.get("breadth") == 1
    assert from_session.get("subset_screens_evaluated") == 4
    assert from_query.get("subset_screens_evaluated") == 4


def test_two_polymer_plan_issues_handle_and_result_read_pages():
    session = new_session()
    with bind_tool_session(session):
        out = dispatch("plan_multistage_separation", feed_polymers=_PAIR)
        assert out.get("available") is True
        handle = out.get("handle")
        assert handle
        stored = load_handle(session, handle)
        exact = stored["exact"]
        assert primary_row_key(exact) == "steps"
        assert exact.get("top_k_sequences")
        steps_page = result_read(handle=handle)
        routes_page = result_read(handle=handle, page="top_k_sequences")
        unknown = result_read(handle=handle, page="ranked_path_index")
    assert steps_page.get("available") is True
    assert steps_page["data"]["rows"] == exact["steps"][:steps_page["returned"]]
    assert routes_page.get("available") is True
    assert routes_page["total"] == len(exact["top_k_sequences"])
    assert routes_page["data"]["rows"] == exact["top_k_sequences"][:routes_page["returned"]]
    assert unknown.get("available") is False
    assert unknown.get("refusal") == "unknown_handle_page"


def test_compact_keeps_ranked_path_index_on_triple():
    session = new_session()
    with bind_tool_session(session):
        out = dispatch("plan_multistage_separation", feed_polymers=_TRIPLE)
    assert out.get("handle")
    assert "top" in out
    visible = out.get("data") or {}
    assert "ranked_path_index" in visible
    assert isinstance(visible["ranked_path_index"], list)
    assert visible["ranked_path_index"]
    assert "top_k_sequences" not in visible
    stored = load_handle(session, out["handle"])
    assert stored["exact"].get("top_k_sequences")


def test_m1_rerank_refuses_and_m5_keeps_original_thermo_rank(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        parent = dispatch("plan_multistage_separation", feed_polymers=_PAIR)
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        parent_rank = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=parent["handle"],
        )
        branched_rank = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=branched["handle"],
        )
        optimum = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="optimum",
            handle=branched["handle"],
        )
        missing = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            handle=branched["handle"],
        )
    assert parent_rank.get("available") is False
    assert parent_rank.get("refusal") == "rerank_requires_stage_branch"
    assert branched_rank.get("available") is True
    points = (branched_rank.get("data") or {}).get("landscape_points") or []
    assert points
    sort_data = branched_rank.get("data") or {}
    assert sort_data.get("n_returned") == len(points)
    assert sort_data.get("n_returned") == sort_data.get("n_landscape_points")
    originals = [point.get("original_thermo_rank") for point in points]
    assert all(isinstance(item, int) and item >= 1 for item in originals)
    assert [point.get("rank") for point in points] == list(range(1, len(points) + 1))
    assert all(point.get("safety_standing", {}).get("status") == "not_requested" for point in points)
    assert optimum.get("refusal") == "not_applicable_in_source"
    assert missing.get("refusal") == "missing_objective"


def test_planner_routes_pareto_has_f_quality_schema(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=branched["handle"],
        )
        sort_payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=branched["handle"],
        )
    assert payload.get("available") is True
    data = payload.get("data") or {}
    assert data.get("source") == "planner_routes"
    assert data.get("operation") == "pareto_dominance"
    landscape = data.get("landscape_points") or []
    frontier = data.get("frontier_points") or []
    assert landscape
    assert frontier
    n_land = data["n_landscape_points"]
    n_front = data["n_frontier_points"]
    assert n_land == len(landscape)
    assert n_front == len(frontier)
    assert data.get("frontier_fraction") == n_front / n_land
    assert data.get("sparse_frontier") is (
        n_front == 1 or data.get("cheapest_equals_lowest_y") is True
    )
    assert data.get("knee_status") in {
        "endpoint_only_no_interior_knee",
        "interior_tradeoff",
        "not_calculated_no_comparable_designs",
    }
    x_key = "bottleneck_selectivity_pct"
    y_key = "min_stage_g_score"
    assert data.get("x_metric") == x_key
    assert data.get("y_metric") == y_key
    spans = data.get("axis_spans") or {}
    assert set(spans) == {x_key, y_key}
    xs = [float(point[x_key]) for point in landscape]
    ys = [float(point[y_key]) for point in landscape]
    assert spans[x_key]["min"] == min(xs)
    assert spans[x_key]["max"] == max(xs)
    assert spans[y_key]["min"] == min(ys)
    assert spans[y_key]["max"] == max(ys)
    for point in landscape:
        assert point["safety_standing"]["status"] == "not_requested"
        assert point.get("original_thermo_rank")
    cheapest = data.get("cheapest_point") or {}
    assert cheapest.get(x_key) == max(float(point[x_key]) for point in frontier)
    tradeoff = data.get("frontier_tradeoff")
    if data.get("cheapest_equals_lowest_y") or n_front < 2:
        assert tradeoff is None
    else:
        assert tradeoff["x_metric"] == x_key
        assert tradeoff["y_metric"] == y_key
        assert tradeoff["x_direction"] == "max"
        assert tradeoff["y_direction"] == "max"
        assert tradeoff["x_units"] == "percentage_points"
        assert tradeoff["y_units"] == "dimensionless"
        assert "incremental_annual_cost_usd" not in tradeoff
        assert "msp_usd_per_kg" not in (tradeoff.get("x_metric"), tradeoff.get("y_metric"))
    grouping = data.get("grouping") or {}
    assert "polymer_grouping" not in grouping
    plan_exact = load_handle(session, branched["handle"])["exact"]
    assert grouping.get("polymers") == plan_exact.get("polymers")
    assert grouping.get("breadth") == plan_exact.get("breadth")
    assert grouping.get("branch_rule") == (
        plan_exact.get("branch_rule") or "count"
    )
    if "feed_mass_fractions" in plan_exact:
        assert grouping.get("feed_mass_fractions") == plan_exact.get(
            "feed_mass_fractions",
        )
    else:
        assert "feed_mass_fractions" not in grouping
    sort_data = sort_payload.get("data") or {}
    assert "frontier_fraction" not in sort_data
    assert "axis_spans" not in sort_data
    assert "sparse_frontier" not in sort_data
    assert "grouping" not in sort_data
    assert "n_frontier_points" not in sort_data
    assert sort_data.get("n_returned") == len(sort_data.get("landscape_points") or [])
    assert sort_data.get("n_returned") == sort_data.get("n_landscape_points")
    assert "n_returned" not in data


def test_planner_routes_process_rows_locators_are_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    locators = (
        {"target_polymer": "LDPE"},
        {"solvent": "Toluene"},
        {"energy_cases": ["C2"]},
        {"allow_partial_campaign": True},
    )
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        refused = [
            dispatch(
                "rank_landscape",
                source="planner_routes",
                operation="pareto_dominance",
                handle=handle,
                **kwargs,
            )
            for kwargs in locators
        ]
    assert served.get("available") is True
    assert (served.get("data") or {}).get("source") == "planner_routes"
    for kwargs, payload in zip(locators, refused):
        data = payload.get("data") or {}
        assert payload.get("available") is False, kwargs
        assert payload.get("refusal") == "not_applicable_in_source", kwargs
        assert data.get("inapplicable_fields") == list(kwargs), kwargs
        assert data.get("source") == "planner_routes"


def test_planner_routes_residual_kwargs_are_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    residual_kwargs = (
        {"scenario": "B"},
        {"recovery_yield": 0.9},
        {"polymer_market_values_usd_per_mt": {"LDPE": 100.0}},
        {"solver_name": "scip"},
        {"composition_slices": [{"LDPE": 0.55, "PP": 0.45}]},
    )
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        refused = [
            dispatch(
                "rank_landscape",
                source="planner_routes",
                operation="pareto_dominance",
                handle=handle,
                **kwargs,
            )
            for kwargs in residual_kwargs
        ]
    assert served.get("available") is True
    for kwargs, payload in zip(residual_kwargs, refused):
        data = payload.get("data") or {}
        assert payload.get("available") is False, kwargs
        assert payload.get("refusal") == "not_applicable_in_source", kwargs
        assert data.get("inapplicable_fields") == list(kwargs), kwargs
        assert data.get("source") == "planner_routes"


def test_planner_routes_objective_on_pareto_is_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        sorted_paths = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=handle,
        )
        refused = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
            objective="min_stage_g_score",
        )
    assert served.get("available") is True
    assert sorted_paths.get("available") is True
    data = refused.get("data") or {}
    assert refused.get("available") is False
    assert refused.get("refusal") == "not_applicable_in_source"
    assert data.get("inapplicable_fields") == ["objective"]
    assert data.get("source") == "planner_routes"
    assert data.get("operation") == "pareto_dominance"


def test_planner_routes_axes_on_sort_are_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    axis_kwargs = (
        {"x_metric": "bottleneck_selectivity_pct"},
        {"y_metric": "min_stage_g_score"},
        {
            "x_metric": "bottleneck_selectivity_pct",
            "y_metric": "min_stage_g_score",
        },
    )
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served_sort = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=handle,
        )
        served_pareto = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
            x_metric="bottleneck_selectivity_pct",
            y_metric="min_stage_g_score",
        )
        refused = [
            dispatch(
                "rank_landscape",
                source="planner_routes",
                operation="sort",
                objective="min_stage_g_score",
                handle=handle,
                **kwargs,
            )
            for kwargs in axis_kwargs
        ]
    assert served_sort.get("available") is True
    assert served_pareto.get("available") is True
    for kwargs, payload in zip(axis_kwargs, refused):
        data = payload.get("data") or {}
        assert payload.get("available") is False, kwargs
        assert payload.get("refusal") == "not_applicable_in_source", kwargs
        assert data.get("inapplicable_fields") == list(kwargs), kwargs
        assert data.get("source") == "planner_routes"
        assert data.get("operation") == "sort"


def test_planner_routes_mixed_polymer_grouping_is_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=branched["handle"],
            polymer_grouping="mixed_polymer",
        )
    data = payload.get("data") or {}
    assert payload.get("available") is False
    assert payload.get("refusal") == "not_applicable_in_source"
    assert data.get("inapplicable_fields") == ["polymer_grouping"]
    assert data.get("source") == "planner_routes"


def test_planner_pareto_quality_star_is_sparse_and_null_tradeoff():
    better_x = {
        "bottleneck_selectivity_pct": 90.0,
        "min_stage_g_score": 5.0,
    }
    better_y = {
        "bottleneck_selectivity_pct": 80.0,
        "min_stage_g_score": 8.0,
    }
    star = tea._planner_pareto_quality(
        [better_x, better_y], [better_x],
        "bottleneck_selectivity_pct", "min_stage_g_score",
    )
    assert star["frontier_fraction"] == 0.5
    assert star["sparse_frontier"] is True
    assert star["frontier_tradeoff"] is None
    trade = tea._planner_pareto_quality(
        [better_x, better_y], [better_x, better_y],
        "bottleneck_selectivity_pct", "min_stage_g_score",
    )
    assert trade["sparse_frontier"] is False
    assert trade["frontier_tradeoff"]["x_at_cheapest"] == 90.0
    assert trade["frontier_tradeoff"]["y_at_best_y"] == 8.0
    assert trade["frontier_tradeoff"]["delta_x"] == -10.0
    assert trade["frontier_tradeoff"]["delta_y"] == 3.0
    assert trade["frontier_tradeoff"]["y_direction"] == "max"


def test_registry_still_two_public_tea_names_and_planner_is_not_new():
    names = {spec["name"] for spec in tool_schemas()}
    assert "evaluate_process" in names
    assert "rank_landscape" in names
    assert "plan_multistage_separation" in names
    assert "plan_then_tea" not in names
    props = next(
        spec["parameters"]["properties"]
        for spec in tool_schemas()
        if spec["name"] == "plan_multistage_separation"
    )
    assert "breadth" in props
    assert "branch_rule" in props
    assert "selectivity_window_pct" in props
    page = next(
        spec["parameters"]["properties"]
        for spec in tool_schemas()
        if spec["name"] == "result_read"
    )
    assert set(page["page"]["enum"]) == {"steps", "top_k_sequences"}


def test_slash_breadth_sets_and_clear_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    import io

    from rich.console import Console
    console = Console(file=io.StringIO(), force_terminal=True, width=80, color_system=None)
    app = CliApp(
        session_id="breadth-session",
        store_root=tmp_path,
        console=console,
    )
    assert app.handle_command("/breadth 5") is False
    assert app.session.get("planner_breadth") == {
        "branch_rule": "count", "breadth": 5,
    }
    assert app.handle_command("/breadth window 1.8") is False
    assert app.session["planner_breadth"]["branch_rule"] == "window"
    assert math.isclose(float(app.session["planner_breadth"]["selectivity_window_pct"]), 1.8)
    assert app.handle_command("/breadth all") is False
    assert app.session["planner_breadth"]["breadth"] == "all"
    assert app.handle_command("/breadth nope") is False
    assert app.session["planner_breadth"]["breadth"] == "all"
    assert app.handle_command("/clear") is False
    assert "planner_breadth" not in app.session
    assert "handle_command" not in inspect.getsource(app.ask)
    assert _parse_breadth_slash([]) is None
    assert _parse_breadth_slash(["3"])["breadth"] == 3


def test_bare_breadth_non_tty_prints_status(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    import io

    from rich.console import Console
    buf = io.StringIO()
    app = CliApp(
        session_id="breadth-status",
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
    )
    assert app.handle_command("/breadth") is False
    assert "planner_breadth" not in app.session
    assert "breadth=1" in buf.getvalue()


def test_bare_breadth_picker_presets_and_all(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    import io

    from rich.console import Console

    from dissolve.cli import _PICKER_BREADTH_PRESETS, _breadth_picker_options

    options, selected = _breadth_picker_options(None)
    assert selected == 0
    values = [value for value, _label in options]
    assert values[:4] == list(_PICKER_BREADTH_PRESETS)
    assert "all" in values
    assert "window" in values
    assert "custom" in values
    labels = " ".join(label for _value, label in options)
    assert "window" in labels
    assert "custom" in labels

    app = CliApp(
        session_id="breadth-pick",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    app._handle_breadth_command([], picker_fn=lambda **_k: 5)
    assert app.session["planner_breadth"] == {
        "branch_rule": "count", "breadth": 5,
    }
    app._handle_breadth_command([], picker_fn=lambda **_k: "all")
    assert app.session["planner_breadth"]["breadth"] == "all"


def test_bare_breadth_picker_custom_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    import io

    from rich.console import Console

    from dissolve.cli import _parse_breadth_slash, _parse_picker_custom_breadth

    assert _parse_picker_custom_breadth("20")["breadth"] == 20
    try:
        _parse_picker_custom_breadth("21")
        raise AssertionError("expected refuse above picker bound")
    except ValueError as error:
        text = str(error)
        assert "20" in text
        assert "50" in text
    assert _parse_breadth_slash(["30"])["breadth"] == 30

    buf = io.StringIO()
    app = CliApp(
        session_id="breadth-custom",
        store_root=tmp_path,
        console=Console(file=buf),
    )
    app._handle_breadth_command(
        [],
        picker_fn=lambda **_k: "custom",
        input_fn=lambda _m: "7",
    )
    assert app.session["planner_breadth"] == {
        "branch_rule": "count", "breadth": 7,
    }
    app._handle_breadth_command(
        [],
        picker_fn=lambda **_k: "custom",
        input_fn=lambda _m: "21",
    )
    assert app.session["planner_breadth"]["breadth"] == 7
    assert "picker custom bound is 20" in buf.getvalue()


def test_bare_breadth_picker_window_followup(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    import io

    from rich.console import Console
    app = CliApp(
        session_id="breadth-window",
        store_root=tmp_path,
        console=Console(file=io.StringIO()),
    )
    app._handle_breadth_command(
        [],
        picker_fn=lambda **_k: "window",
        input_fn=lambda _m: "1.8",
    )
    assert app.session["planner_breadth"]["branch_rule"] == "window"
    assert math.isclose(
        float(app.session["planner_breadth"]["selectivity_window_pct"]), 1.8,
    )


# --- from test_not_applicable_in_mode.py: Wrong-mode scalars on evaluate refuse not_applicable_in_mode. Not a rename.
def _data_not_applicable_in_mode(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _public_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("evaluate must not start BioSTEAM for this refuse")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _schema(name: str) -> dict:
    return next(item for item in tool_schemas() if item["name"] == name)


def test_lookup_selectors_are_absent_from_evaluate_schema():
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    for name in tea._LOOKUP_MODE_SELECTORS:
        assert name in lookup_params
        assert name not in eval_props


def test_evaluate_refuses_lookup_selectors_before_missing_scenarios(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("error_code") != "missing_scenarios"
    assert payload.get("mode") == "evaluate"
    assert payload.get("applicable_mode") == "lookup"
    assert payload.get("inapplicable_fields") == ["sensitivity_axes"]
    assert "comparison_rows" not in payload


def test_evaluate_refuses_each_lookup_selector(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    sent = {
        "sensitivity_labels": ["ldpe-price-low"],
        "sensitivity_axes": ["solvent_price"],
        "sensitivity_level_selector": "low_high",
    }
    for name, value in sent.items():
        payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="auto",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("inapplicable_fields") == [name]
        assert payload.get("msp_usd_per_kg") is None
        assert "comparison_rows" not in payload
    empty = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        sensitivity_axes=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    assert empty.get("inapplicable_fields") == ["sensitivity_axes"]


def test_nested_selector_stays_unknown_process_field(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, sensitivity_axes=["solvent_price"])],
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert "sensitivity_axes" in list(payload.get("extra_keys") or [])


def test_unknown_top_level_kwarg_is_unknown_process_field(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    leftover = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        not_a_lookup_selector=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_selector"]
    assert leftover.get("error_code") != "not_applicable_in_mode"
    assert leftover.get("tool_name") == "evaluate_tea_lca_scenarios"
    assert "comparison_rows" not in leftover
    other = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        also_not_a_field=1,
    ))
    assert other.get("extra_keys") == ["also_not_a_field"]
    assert other.get("extra_keys") != leftover.get("extra_keys")
    pair = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        not_a_lookup_selector=True,
        also_not_a_field=1,
    ))
    assert pair.get("extra_keys") == ["also_not_a_field", "not_a_lookup_selector"]
    mixed = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        sensitivity_axes=["solvent_price"],
        not_a_lookup_selector=True,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["sensitivity_axes"]
    assert mixed.get("error_code") != "unknown_process_field"
    wrap = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="auto",
        not_a_lookup_selector=True,
    ))
    assert wrap.get("error_code") == "unknown_process_field"
    assert wrap.get("extra_keys") == ["not_a_lookup_selector"]
    assert wrap.get("tool_name") == "evaluate_process"


def test_evaluate_without_selectors_still_serves_cache(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="auto",
    ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_lookup_still_accepts_the_selectors(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "admitted_process_sensitivity_records"


def test_dispatch_evaluate_selector_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
            sensitivity_axes=["solvent_price"],
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        served = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
        )
        assert served.get("available") is True
        assert served.get("source_basis") == "tea_cache_exact"
        assert served.get("handle")
        extra = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
            not_a_lookup_selector=True,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert extra.get("refusal") != "tool_exception"
        assert "handle" not in extra


def test_evaluate_refuses_sensitivity_scalars(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    sent = {
        "parameter": "solvent_price",
        "values": [0.5, 1.5],
        "analysis_mode": "tornado",
        "metric": "msp_usd_per_kg",
    }
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    sensitivity_params = inspect.signature(tea.analyze_tea_sensitivity).parameters
    for name, value in sent.items():
        assert name in sensitivity_params
        assert name not in eval_props
        payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="auto",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("applicable_mode") == "sensitivity"
        assert payload.get("inapplicable_fields") == [name]
        assert payload["applicable_mode_by_field"] == {name: "sensitivity"}
        assert "comparison_rows" not in payload


def test_evaluate_refuses_lookup_energy_cases_list(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    assert "energy_cases" not in eval_props
    assert "energy_cases" in inspect.signature(
        tea.lookup_admitted_process_records,
    ).parameters
    payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="auto",
        energy_cases=["C1"],
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("applicable_mode") == "lookup"
    assert payload.get("inapplicable_fields") == ["energy_cases"]
    empty = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="auto",
        energy_cases=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    nested = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, energy_cases=["C1"])],
        engine_mode="auto",
    ))
    assert nested.get("error_code") == "unknown_process_field"
    assert nested.get("error_code") != "not_applicable_in_mode"
    assert "energy_cases" in list(nested.get("extra_keys") or [])


def test_mixed_wrong_mode_scalars_name_each_home(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="auto",
        energy_cases=["C1"],
        parameter="solvent_price",
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["energy_cases", "parameter"]
    assert payload.get("applicable_mode") is None
    assert payload["applicable_mode_by_field"] == {
        "energy_cases": "lookup",
        "parameter": "sensitivity",
    }


def test_sensitivity_schema_omits_screening_handoff():
    props = inspect.signature(tea.analyze_tea_sensitivity).parameters
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    assert "screening_shortlist" in eval_props
    assert "held_process_basis" in eval_props
    assert "screening_shortlist" not in props
    assert "held_process_basis" not in props


def test_sensitivity_refuses_screening_handoff(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    shortlist = {
        "source": "explicit",
        "items": [{
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        }],
    }
    payload = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="auto",
        screening_shortlist=shortlist,
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["screening_shortlist"]
    assert payload.get("applicable_mode") == "evaluate"
    assert payload["applicable_mode_by_field"] == {
        "screening_shortlist": "evaluate",
    }
    assert payload.get("tool_name") == "analyze_tea_sensitivity"
    assert "sensitivity_rows" not in payload
    both = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="auto",
        screening_shortlist=shortlist,
        held_process_basis={"energy_case": "C1"},
    ))
    assert both.get("error_code") == "not_applicable_in_mode"
    assert both.get("inapplicable_fields") == [
        "screening_shortlist", "held_process_basis",
    ]
    assert both.get("applicable_mode") == "evaluate"
    held = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="auto",
        held_process_basis={"energy_case": "C1"},
    ))
    assert held.get("error_code") == "not_applicable_in_mode"
    assert held.get("inapplicable_fields") == ["held_process_basis"]
    leftover = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="auto",
        not_a_sensitivity_field=1,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_sensitivity_field"]
    assert leftover.get("error_code") != "not_applicable_in_mode"
    assert leftover.get("tool_name") == "analyze_tea_sensitivity"
    assert "sensitivity_rows" not in leftover
    wrap = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="sensitivity",
        process_config=scenario,
        parameter="solvent_price",
        engine_mode="auto",
        not_a_sensitivity_field=1,
    ))
    assert wrap.get("error_code") == "unknown_process_field"
    assert wrap.get("extra_keys") == ["not_a_sensitivity_field"]
    assert wrap.get("tool_name") == "evaluate_process"
    mixed = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="auto",
        screening_shortlist=shortlist,
        not_a_sensitivity_field=1,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["screening_shortlist"]
    assert mixed.get("error_code") != "unknown_process_field"


def test_dispatch_sensitivity_handoff_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=scenario,
            parameter="solvent_price",
            engine_mode="auto",
            screening_shortlist={
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        served = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=scenario,
            parameter="solvent_price",
            engine_mode="auto",
        )
        assert served.get("available") is True
        assert served.get("source_basis") == "tea_cache_exact"
        assert served.get("handle")
        extra = dispatch(
            "evaluate_process",
            mode="sensitivity",
            process_config=scenario,
            parameter="solvent_price",
            engine_mode="auto",
            not_a_sensitivity_field=1,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert "handle" not in extra


def test_lookup_energy_cases_and_sensitivity_parameter_still_serve(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    lookup = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1"],
    ))
    assert lookup.get("success") is True
    assert lookup["requested_energy_cases"] == ["C1"]
    sensitivity = _data_not_applicable_in_mode(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="auto",
    ))
    assert sensitivity.get("success") is True
    assert sensitivity.get("error_code") != "not_applicable_in_mode"


@pytest.mark.parametrize(
    "field, list_field, value, list_value",
    [
        pytest.param("parameter", "energy_cases", "solvent_price", "C1", id="parameter"),
        pytest.param("record_form", "requested_metrics", "per_record", "gwp", id="record_form"),
    ],
)
def test_dispatch_evaluate_refuses_scenario_only_arguments(monkeypatch, field, list_field, value, list_value):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
            **{field: value},
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        energy = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="auto",
            **{list_field: [list_value]},
        )
        assert energy.get("available") is False
        assert energy.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in energy


def test_evaluate_refuses_record_form_and_requested_metrics(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    eval_props = _schema("evaluate_process")["parameters"]["properties"]
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    sent = {
        "record_form": "grouped_comparison",
        "requested_metrics": ["etox", "energy"],
    }
    for name, value in sent.items():
        assert name in lookup_params
        assert name not in eval_props
        payload = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="auto",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("applicable_mode") == "lookup"
        assert payload.get("inapplicable_fields") == [name]
        assert payload["applicable_mode_by_field"] == {name: "lookup"}
        assert "comparison_rows" not in payload
    empty = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="auto",
        requested_metrics=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    assert empty.get("inapplicable_fields") == ["requested_metrics"]
    nested = _data_not_applicable_in_mode(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, record_form="per_record")],
        engine_mode="auto",
    ))
    assert nested.get("error_code") == "unknown_process_field"
    assert nested.get("error_code") != "not_applicable_in_mode"
    assert "record_form" in list(nested.get("extra_keys") or [])


def test_lookup_still_accepts_record_form_and_requested_metrics(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        record_form="grouped_comparison",
        requested_metrics=["etox", "energy"],
    ))
    assert payload.get("success") is True
    assert payload["record_form"] == "grouped_comparison"
    assert payload["requested_metrics"] == ["etox", "energy"]


def test_lookup_leftover_extra_is_unknown_process_field(monkeypatch):
    _forbid_live(monkeypatch)
    leftover = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        not_a_lookup_field=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_field"]
    assert leftover.get("error_code") != "missing_target_polymer"
    assert leftover.get("tool_name") == "lookup_admitted_process_records"
    assert "comparison_rows" not in leftover
    other = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        also_not_a_field=1,
    ))
    assert other.get("extra_keys") == ["also_not_a_field"]
    assert other.get("extra_keys") != leftover.get("extra_keys")
    pair = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        not_a_lookup_field=True,
        also_not_a_field=1,
    ))
    assert pair.get("extra_keys") == ["also_not_a_field", "not_a_lookup_field"]
    missing_polymer = _data_not_applicable_in_mode(tea.lookup_admitted_process_records(
        not_a_lookup_field=True,
    ))
    assert missing_polymer.get("error_code") == "unknown_process_field"
    assert missing_polymer.get("extra_keys") == ["not_a_lookup_field"]
    assert missing_polymer.get("error_code") != "missing_target_polymer"
    wrap = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        not_a_lookup_field=True,
    ))
    assert wrap.get("error_code") == "unknown_process_field"
    assert wrap.get("extra_keys") == ["not_a_lookup_field"]
    assert wrap.get("tool_name") == "evaluate_process"


def test_dispatch_lookup_leftover_is_named_refuse(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
                "not_a_lookup_field": True,
            },
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "unknown_process_field"
        assert refused.get("refusal") != "tool_exception"
        assert "handle" not in refused
        served = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
            },
        )
        assert served.get("available") is True
        assert served.get("handle")


def test_evaluate_process_top_level_selector_is_not_applicable(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    payload = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="auto",
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["sensitivity_axes"]
    assert payload.get("applicable_mode") == "lookup"
    assert payload["applicable_mode_by_field"] == {
        "sensitivity_axes": "lookup",
    }
    assert payload.get("tool_name") == "evaluate_process"
    assert payload.get("error_code") != "unknown_process_field"
    energy = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="auto",
        energy_cases=["C1"],
    ))
    assert energy.get("error_code") == "not_applicable_in_mode"
    assert energy.get("inapplicable_fields") == ["energy_cases"]
    leftover = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="auto",
        not_a_lookup_selector=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_selector"]
    mixed = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="auto",
        sensitivity_axes=["solvent_price"],
        not_a_lookup_selector=True,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["sensitivity_axes"]
    assert mixed.get("error_code") != "unknown_process_field"
    filtered = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "sensitivity_axes": ["solvent_price"],
        },
    ))
    assert filtered.get("success") is True
    assert filtered.get("error_code") != "not_applicable_in_mode"
    top_level_lookup = _data_not_applicable_in_mode(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        sensitivity_axes=["solvent_price"],
    ))
    assert top_level_lookup.get("error_code") == "not_applicable_in_mode"
    assert top_level_lookup.get("inapplicable_fields") == ["sensitivity_axes"]
    assert top_level_lookup.get("error_code") != "unknown_process_field"


def test_dispatch_evaluate_process_selector_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="auto",
            sensitivity_axes=["solvent_price"],
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        extra = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="auto",
            not_a_lookup_selector=True,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert "handle" not in extra
        served = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="auto",
        )
        assert served.get("available") is True
        assert served.get("handle")


# --- from test_literature_mode.py: /literature is a tool-list gate. Ingest is never offered. Off is the default.
def _app_literature_mode(tmp_path, monkeypatch, *, session_id: str = "literature-cli", **kwargs):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    buf = io.StringIO()
    app = CliApp(
        session_id=session_id,
        store_root=tmp_path,
        console=Console(file=buf, force_terminal=True, width=80, color_system=None),
        **kwargs,
    )
    return app, buf


def _literature_names(session=None) -> set[str]:
    return {item["name"] for item in tool_schemas(session)} & set(LITERATURE_AGENT_TOOLS)


def test_parse_rejects_on_and_unknown():
    assert _parse_literature_slash([]) is None
    assert _parse_literature_slash(["off"]) == {"mode": "off"}
    assert _parse_literature_slash(["corpus"]) == {"mode": "corpus"}
    assert _parse_literature_slash(["scholarly"]) == {"mode": "scholarly"}
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["on"])
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["network"])
    with pytest.raises(ValueError, match="usage: /literature"):
        _parse_literature_slash(["ingest"])
    assert _format_literature_default(None, origin="built-in") == (
        "literature_mode=off  (built-in)"
    )


def test_off_default_six_literature_tools_absent_from_agent_list():
    names = _literature_names()
    assert names == set()
    assert LITERATURE_AGENT_TOOLS.isdisjoint(offered_tool_names())
    assert "ingest_literature_documents" in agent.BY_NAME
    assert "ingest_literature_graph" in agent.BY_NAME
    assert len(EXPECTED_REGISTRY_NAMES) == 31
    assert len(agent.REGISTRY) == 31


def test_corpus_offers_exactly_two_local_tools_and_network_does_not_fire(monkeypatch, tmp_path):
    def boom(*_args, **_kwargs):
        raise AssertionError("corpus mode must not hit scholarly/patent network")

    monkeypatch.setattr(research, "_search_arxiv", boom)
    monkeypatch.setattr(research, "_search_google_scholar", boom)
    monkeypatch.setattr(research, "_search_wos", boom)
    monkeypatch.setattr(research, "_search_google_patents", boom)
    monkeypatch.setattr(research, "_search_patentsview", boom)
    monkeypatch.setattr(research, "search_scholarly_literature", boom)
    monkeypatch.setattr(research, "search_patent_literature", boom)
    session = {"literature_mode": {"mode": "corpus"}}
    assert _literature_names(session) == set(LITERATURE_CORPUS_TOOLS)
    assert LITERATURE_NETWORK_TOOLS.isdisjoint(_literature_names(session))
    assert LITERATURE_INGEST_TOOLS.isdisjoint(_literature_names(session))
    monkeypatch.setenv("DISSOLVE_RESEARCH_HOME", str(tmp_path))
    payload = parse_tool_result(
        research.inspect_literature_corpus(operation="status"),
    )
    assert payload["data"]["success"] is True


def test_scholarly_is_named_surface_not_registry_minus_ingest():
    assert set(LITERATURE_MODE_SURFACE) == {"off", "corpus", "scholarly"}
    assert "ingest" not in LITERATURE_MODE_SURFACE
    assert LITERATURE_MODE_SURFACE["off"] == frozenset()
    assert LITERATURE_MODE_SURFACE["corpus"] == LITERATURE_CORPUS_TOOLS
    assert LITERATURE_MODE_SURFACE["scholarly"] == LITERATURE_SCHOLARLY_TOOLS
    assert LITERATURE_SCHOLARLY_TOOLS == LITERATURE_CORPUS_TOOLS | LITERATURE_NETWORK_TOOLS
    offered_across_modes = frozenset().union(*LITERATURE_MODE_SURFACE.values())
    assert LITERATURE_INGEST_TOOLS.isdisjoint(offered_across_modes)
    assert literature_agent_mode({"literature_mode": {"mode": "ingest"}}) == "off"


def test_scholarly_offers_four_and_ingest_never():
    session = {"literature_mode": {"mode": "scholarly"}}
    names = _literature_names(session)
    assert names == set(LITERATURE_SCHOLARLY_TOOLS)
    assert len(names) == 4
    for mode in (None, {"literature_mode": {"mode": "off"}}, session, {"literature_mode": {"mode": "corpus"}}):
        assert LITERATURE_INGEST_TOOLS.isdisjoint(_literature_names(mode))
    scholarly = {item["name"]: item for item in tool_schemas(session)}
    assert "save_to_corpus" not in scholarly["search_scholarly_literature"]["parameters"]["properties"]
    assert "save_to_corpus" not in scholarly["search_patent_literature"]["parameters"]["properties"]


def test_mode_does_not_leak_across_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    app_a, _buf_a = _app_literature_mode(tmp_path, monkeypatch, session_id="lit-a")
    assert app_a.handle_command("/literature corpus") is False
    assert app_a.session.get("literature_mode") == {"mode": "corpus"}
    app_b, buf_b = _app_literature_mode(tmp_path, monkeypatch, session_id="lit-b")
    assert "literature_mode" not in app_b.session
    assert _literature_names(app_b.session) == set()
    assert app_b.handle_command("/literature") is False
    assert "literature_mode=off" in buf_b.getvalue()
    assert app_a.handle_command("/clear") is False
    assert "literature_mode" not in app_a.session
    assert "handle_command" not in inspect.getsource(app_a.ask)


def test_dispatch_ingest_refuses_in_every_mode():
    from dissolve.session import bind_tool_session, new_session

    for stored in (None, {"mode": "corpus"}, {"mode": "scholarly"}):
        record = new_session()
        if stored is not None:
            record["literature_mode"] = stored
        with bind_tool_session(record):
            ingest = dispatch("ingest_literature_documents", paths=["/tmp/nope.pdf"])
            graph = dispatch("ingest_literature_graph", paths=["/tmp/nope.json"])
        assert ingest["available"] is False
        assert ingest["refusal"] == "literature_tools_not_offered"
        assert graph["available"] is False
        assert graph["refusal"] == "literature_tools_not_offered"


def test_slash_sets_and_on_does_not_write(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    app, buf = _app_literature_mode(tmp_path, monkeypatch)
    assert app.handle_command("/literature scholarly") is False
    assert app.session.get("literature_mode") == {"mode": "scholarly"}
    assert app.handle_command("/literature on") is False
    assert app.session.get("literature_mode") == {"mode": "scholarly"}
    assert "usage: /literature" in buf.getvalue()
    assert app.handle_command("/literature off") is False
    assert app.session.get("literature_mode") == {"mode": "off"}


def test_emptying_ingest_tools_does_not_put_ingest_on_scholarly(monkeypatch):
    from dissolve import agent as tools
    monkeypatch.setattr(tools, "LITERATURE_INGEST_TOOLS", frozenset())
    scholarly = {"literature_mode": {"mode": "scholarly"}}
    offered = {item["name"] for item in tools.tool_schemas(scholarly)}
    assert "ingest_literature_documents" not in offered
    assert "ingest_literature_graph" not in offered
    assert "promote_ingested_paper" not in offered
    assert tools.LITERATURE_MODE_SURFACE["scholarly"] == tools.LITERATURE_SCHOLARLY_TOOLS


def test_a_briefly_overloaded_provider_is_retried_before_the_turn_fails(monkeypatch):
    """Muse Spark answered 503 service_overloaded mid-turn on 2026-09-24, and with the SDK's default two retries the
    user's turn ended as a provider error. The client now retries with the SDK's backoff before giving up."""
    import openai
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            raise RuntimeError("constructed")

    monkeypatch.setattr(openai, "OpenAI", Recorder)
    with pytest.raises(RuntimeError, match="constructed"):
        agent.complete([{"role": "user", "content": "hi"}], [], model="openai:muse-spark-1.3")
    assert seen["max_retries"] == agent._PROVIDER_RETRIES >= 5


def test_system_prompt_screens_once_and_treats_requirements_as_filters():
    """The served examples ran 7 to 14 tools where 2 to 4 answered them: solubility screened again with the
    atmospheric filter, safety cards fetched after the safety comparison, Hansen parameters looked up before the
    Hansen screen. And "keep LDPE insoluble" came back with DMF, which dissolves 16 wt% LDPE (2026-09-24)."""
    prompt = " ".join(agent.SYSTEM_PROMPT.split())
    assert "Call a tool only for what no earlier result holds." in prompt
    assert "ask the first screen for options that stay liquid there at 1 atm (require_atmospheric)" in prompt
    assert "A requirement in the question is a filter." in prompt
    assert "above the engine's insolubility level (1 wt%, the precipitation threshold)" in prompt
    assert 'a record marked unreviewed_raw is "not yet reviewed"' in prompt


def test_system_prompt_samples_ranges_and_names_hazards_in_words():
    """"How does LDPE's solubility in dodecane change between 80 and 140 °C" was answered from its two ends; a safety
    table listed bare H-codes; one table said "Sustainability score" where its legend explained "G score" (2026-09-24)."""
    prompt = " ".join(agent.SYSTEM_PROMPT.split())
    assert "query the range in steps (every 10 °C for temperatures), not only its ends" in prompt
    assert "Call each score by the same name in the table header and the legend." in prompt
    assert "GHS hazard statements are written in words (H225: highly flammable liquid and vapour), never as bare codes." in prompt


def test_system_prompt_weighs_a_contradicting_hansen_check_and_skips_membership_lookups():
    """The HDPE/PP/PS example led with HDPE "dissolving" in propylene glycol while the Hansen check put that solvent
    at RED 11.7, far outside the sphere; and most example answers opened with a membership lookup the screens do
    themselves (2026-09-24). The owner asked for Hansen to count in the assessment."""
    prompt = " ".join(agent.SYSTEM_PROMPT.split())
    assert "A step the Hansen check contradicts (the solvent far outside the sphere) is weaker evidence" in prompt
    assert "when another option works on both counts, lead with that one." in prompt
    assert "Screens resolve polymer and solvent names themselves; look up database membership only when that is the question." in prompt


def test_the_hansen_screen_names_the_solvents_inside_a_sphere_on_its_own():
    """"Which solvents fall inside PVDF's interaction sphere" names no solvents. The screen required a list, so the
    agent assembled 69 names, half with no Hansen link, and found only DMF (2026-09-24). With no list it now screens
    DISSOLVE's curated Hansen solvents, which puts the textbook PVDF solvents inside."""
    data = _data(agent.BY_NAME["screen_hansen_compatibility"].fn(polymer_names=["PVDF"]))
    inside = {row["solvent"] for row in data["rows"] if row["inside_hansen_sphere"]}
    assert {"DMF", "NMP"} <= inside and data["inside_sphere_count"] == len([r for r in data["rows"] if r["inside_hansen_sphere"]])
    assert data["solvent_set"].startswith("DISSOLVE's") and "curated Hansen solvents" in data["solvent_set"]
    named = _data(agent.BY_NAME["screen_hansen_compatibility"].fn(polymer_names=["PVDF"], solvent_names=["acetone"]))
    assert named["solvent_set"] == "requested" and {row["solvent"] for row in named["rows"]} == {"Acetone"}
