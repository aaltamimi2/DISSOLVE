"""§10 acceptance tests. Stub the provider. Test 5 is identity-sensitive."""
from __future__ import annotations

import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dissolve import agent_harness
from dissolve import agent_tools
from dissolve.agent_harness import run_turn
from dissolve.agent_tools import SYSTEM_PROMPT, dispatch
_real_bind = agent_tools.bind_handle_rows
from dissolve.session import (
    bind_tool_session, handle_rows, load_handle, new_session,
)
from dissolve.thermodynamics import get_available_solvents
from dissolve import tea

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

    monkeypatch.setattr(agent_harness, "complete", fake)
    return n


def _turn(query, session, history):
    return run_turn(query, session=session, model="openai:stub", messages=history)


def _history():
    return [{"role": "system", "content": SYSTEM_PROMPT}]


def _forty_copy_probe():
    """Auditor bind: 40 copies of one valid screen row. Counts pass; identities do not."""
    bound_ids = []
    session = new_session()
    agent_tools.bind_handle_rows = _spy_bind(bound_ids, _bind_copies_of_first_row)
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
        agent_tools.bind_handle_rows = _real_bind


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
        "dissolve.agent_tools.bind_handle_rows",
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


def test_acceptance_5_coverage_predicate_is_red_when_page_is_the_shortlist():
    """Not Test 5. The answer Test 5 must fail on: 6-row page, no coverage."""
    with pytest.raises(AssertionError, match="coverage"):
        _assert_coverage_disclosed(
            "Safety of the inherited shortlist at the screen temperatures "
            "(source_basis safety_local): cyclohexane.",
            compared=6, stored=40,
        )


def test_acceptance_5_coverage_predicate_is_red_on_full_shortlist_with_counts():
    """Not Test 5. Counts without the page disclosure still name the shortlist."""
    with pytest.raises(AssertionError, match="comparison-page disclosure"):
        _assert_coverage_disclosed(
            "The full inherited shortlist consists of these 6 of 40 solvents.",
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


def test_acceptance_4_polyethylene_expands_or_refuses_both_members(monkeypatch):
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
    if ev.result.get("available"):
        polymers = [row["polymer"] for row in ev.result["data"]["results"]]
        assert set(polymers) == {"LDPE", "HDPE"}
        assert "LDPE" in answer and "HDPE" in answer
        for row in ev.result["data"]["results"]:
            assert str(row["solubility_pct"]) in answer
    else:
        assert ev.result.get("refusal") == "ambiguous_polymer"
        blob = json.dumps(ev.result)
        assert "LDPE" in blob and "HDPE" in blob
        assert "LDPE" in answer and "HDPE" in answer
    assert not (answer.count("HDPE") and "LDPE" not in answer)


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


def test_acceptance_7_tea_outside_cache_is_a_miss(monkeypatch):
    def answer(messages):
        payload = _tool_json(messages, "evaluate_process")
        status = payload["data"].get("cache_match_status")
        return {
            "text": (
                f"Refusal {payload.get('refusal')}. cache_match_status {status}. "
                "This is a miss, not an exact prior simulation of HDPE/dodecane "
                "at 1 Mt/yr."
            ),
            "tool_calls": [],
        }

    _play(monkeypatch, [
        {"text": "", "tool_calls": [{
            "id": "t", "name": "evaluate_process",
            "args": {
                "mode": "evaluate",
                "engine_mode": "cache",
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
    assert data.get("cache_match_status") in {"miss", "surrogate"}
    if data.get("cache_match_status") == "surrogate":
        assert ev.result.get("source_basis") == "tea_screening_analog"
        assert "analog" in result.answer.lower()
        assert "simulation of" not in result.answer.lower()
    else:
        assert ev.result.get("available") is False
        assert "miss" in result.answer.lower()
        assert "tea_cache_exact" not in result.answer
        assert "cache_match_status exact" not in result.answer.lower()


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
                    "engine_mode": "cache",
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
