"""Stage-1 TEA: shortlist of m first-step items, quote, then economics rank.

Thermodynamics screens; economics decides. No new registry name. No live
BioSTEAM child. Later-stage TEA and carry-over stay out.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import UNWIRED, dispatch, tool_schemas
from dissolve import registry, tea
from dissolve.session import bind_tool_session, load_handle, new_session, store_handle

from test_evaluate_process import (
    _forbid_live,
    _held_from_record,
    _item_from_record,
    _public_from_record,
    _shortlist,
)

_SECONDS = 15.132821729521634
_PLAN = "plan_multistage_separation"
_TRIPLE = ["LDPE", "PP", "PS"]
_PAIR = ["LDPE", "PP"]


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _admitted_ldpe_trio():
    return (
        _record_by_label("ldpe-dissolution-low"),
        _record_by_label("ldpe-route-c1"),
        _record_by_label("ldpe-dissolution-high"),
    )


def test_registry_stays_two_public_tea_names():
    names = [item["name"] for item in tool_schemas() if item["name"] != "result_read"]
    assert "evaluate_process" in names
    assert "rank_landscape" in names
    assert "plan_then_tea" not in names
    assert names.count("evaluate_process") == 1
    assert len(registry.REGISTRY) == 29
    assert "fetch_solvent_safety_by_cid" in registry.BY_NAME
    assert "estimate_thermal_properties" not in registry.BY_NAME
    assert UNWIRED == frozenset()


def test_shortlist_of_three_without_held_names_the_nine(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(low),
            _item_from_record(mid),
            _item_from_record(high),
        ),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert payload.get("error_code") != "tool_not_wired"
    assert "comparison_rows" not in payload
    assert payload.get("tool_name") == "evaluate_process"


def test_feed_basis_cache_serves_admitted_and_fails_unadmitted(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    held = _held_from_record(mid)
    miss = _item_from_record(mid, dissolution_temperature_c=999.0)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(low, thermo_rank=3),
            _item_from_record(mid, thermo_rank=2),
            miss,
        ),
        held_process_basis=held,
        engine_mode="cache",
    ))
    rows = list(payload.get("comparison_rows") or payload.get("failures") or [])
    assert len(rows) == 3
    assert payload.get("error_code") != "tool_not_wired"
    served = [row for row in rows if row.get("success") is True]
    failed = [row for row in rows if row.get("success") is not True]
    assert len(served) == 2
    assert len(failed) == 1
    assert failed[0].get("error_type") in {"cache_miss", "no_simulation_result"}
    assert failed[0].get("msp_usd_per_kg") is None
    origins = served[0]["field_origin"]
    assert origins["target_polymer"] == "from_screen"
    assert origins["solvent"] == "from_screen"
    assert origins["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origins[name] == "supplied"
        assert origins[name] != "from_screen"
    by_t = {row["dissolution_temperature_c"]: row for row in served}
    assert by_t[low["config"]["dissolution_temperature_c"]]["original_thermo_rank"] == 3
    assert by_t[mid["config"]["dissolution_temperature_c"]]["original_thermo_rank"] == 2


def test_unadmitted_auto_without_confirm_quotes_and_starts_no_child(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    calls = []

    def forbidden(config, timeout_seconds):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    miss_a = _item_from_record(mid, dissolution_temperature_c=998.0, thermo_rank=1)
    miss_b = _item_from_record(high, dissolution_temperature_c=999.0, thermo_rank=2)
    hit = _item_from_record(low, thermo_rank=3)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(miss_a, miss_b, hit),
        held_process_basis=_held_from_record(mid),
        engine_mode="auto",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "auto"
    assert payload.get("n_live") == 2
    assert payload.get("n_cache") == 1
    assert payload.get("seconds_per_pair") == pytest.approx(_SECONDS)
    assert payload.get("estimated_wall_seconds") == pytest.approx(2 * _SECONDS)
    assert payload.get("per_stage_per_ordering") is True
    assert payload.get("tool_name") == "evaluate_process"
    assert calls == []
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(miss_a, miss_b, hit),
        held_process_basis=_held_from_record(mid),
        engine_mode="auto",
        confirm_live_tea=False,
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("n_live") == 2
    assert calls == []


def test_confirm_live_tea_runs_children_one_at_a_time(monkeypatch):
    _mid = _record_by_label("ldpe-route-c1")
    order = []

    def planted(config, timeout_seconds):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    items = [
        _item_from_record(_mid, dissolution_temperature_c=997.0, thermo_rank=1),
        _item_from_record(_mid, dissolution_temperature_c=998.0, thermo_rank=2),
        _item_from_record(_mid, dissolution_temperature_c=999.0, thermo_rank=3),
    ]
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(*items),
        held_process_basis=_held_from_record(_mid),
        engine_mode="auto",
        confirm_live_tea=True,
    ))
    assert order == [997.0, 998.0, 999.0]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"
    assert payload.get("error_code") == "no_simulation_result" or payload.get(
        "failed"
    ) == 3


def test_confirm_live_tea_on_process_configs_quotes_live_without_starting(
    monkeypatch,
):
    record = _record_by_label("ldpe-route-c1")
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    twelve = _public_from_record(record)
    quoted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert quoted.get("error_code") == "live_tea_cost_confirmation_required"
    assert quoted.get("engine_mode") == "live"
    assert quoted.get("n_live") == 1
    assert quoted.get("n_cache") == 0
    assert quoted.get("per_stage_per_ordering") is True
    assert calls == []
    omitted = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="live",
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("engine_mode") == "live"
    assert calls == []
    cache_path = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=twelve,
        engine_mode="cache",
        confirm_live_tea=True,
    ))
    assert cache_path.get("success") is True
    assert cache_path.get("error_code") != "live_tea_cost_confirmation_required"
    assert cache_path.get("error_code") != "not_applicable_in_mode"


def test_confirm_live_tea_false_on_empty_scenarios_is_not_inapplicable():
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [], confirm_live_tea=False,
    ))
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") != ["confirm_live_tea"]


def test_scenarios_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == 1
    assert payload.get("n_cache") == 0
    assert payload.get("seconds_per_pair") == pytest.approx(_SECONDS)
    assert calls == []


def test_scenarios_confirm_live_tea_runs_children_one_at_a_time(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    first = _public_from_record(record, dissolution_temperature_c=997.0)
    second = _public_from_record(record, dissolution_temperature_c=998.0)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [first, second],
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == [997.0, 998.0]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def _one_step_route_from_record(record: dict) -> tuple[dict, dict]:
    cfg = record["config"]
    polymer = cfg["target_plastic"]
    composition = {polymer: 1.0}
    route = {
        "complete": True,
        "final_residue": None,
        "steps": [{
            "dissolved_polymer": polymer,
            "solvent": cfg["solvent"],
            "temperature_c": cfg["dissolution_temperature_c"],
        }],
    }
    return composition, route


def test_stored_route_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    cfg = record["config"]
    composition, route = _one_step_route_from_record(record)
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    monkeypatch.setattr(
        tea,
        "current_tool_session",
        lambda: SimpleNamespace(
            last_route=copy.deepcopy(route),
            feed_mass_fractions=dict(composition),
        ),
    )
    payload = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == 1
    assert payload.get("n_cache") == 0
    assert payload.get("per_stage_per_ordering") is True
    assert calls == []
    omitted = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert omitted.get("error_code") == "live_tea_cost_confirmation_required"
    assert omitted.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []


def test_stored_route_confirm_live_tea_proceeds_without_a_real_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    cfg = record["config"]
    composition, route = _one_step_route_from_record(record)
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("dissolution_temperature_c"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    monkeypatch.setattr(
        tea,
        "current_tool_session",
        lambda: SimpleNamespace(
            last_route=copy.deepcopy(route),
            feed_mass_fractions=dict(composition),
        ),
    )
    payload = _data(tea.evaluate_stored_route_tea_lca(
        feed_mass_fractions=dict(composition),
        processing_capacity_mt_per_yr=cfg["processing_capacity"],
        energy_case=cfg["energy_case"],
        precipitation_temperature_c=cfg["precipitation_temperature_c"],
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == [cfg["dissolution_temperature_c"]]
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def test_sensitivity_live_without_confirm_quotes_and_starts_no_child(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    baseline_price = float(record["config"]["solvent_price"])
    requested = [0.5, 1.5]
    expected_values = list(dict.fromkeys([baseline_price, *requested]))
    calls = []

    def forbidden(config, timeout_seconds=None, **_kwargs):
        calls.append(config)
        raise AssertionError("live TEA must not start before confirm")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
    ))
    assert payload.get("error_code") == "live_tea_cost_confirmation_required"
    assert payload.get("engine_mode") == "live"
    assert payload.get("n_live") == len(expected_values)
    assert payload.get("n_cache") == 0
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []
    accepted = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
        confirm_live_tea=False,
    ))
    assert accepted.get("error_code") == "live_tea_cost_confirmation_required"
    assert accepted.get("inapplicable_fields") != ["confirm_live_tea"]
    assert calls == []


def test_sensitivity_confirm_live_tea_proceeds_one_at_a_time(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    baseline_price = float(record["config"]["solvent_price"])
    requested = [0.5, 1.5]
    expected_values = list(dict.fromkeys([baseline_price, *requested]))
    order = []

    def planted(config, timeout_seconds=None, **_kwargs):
        order.append(config.get("solvent_price"))
        return {
            "success": False,
            "error": "planted live miss",
            "error_type": "planted_live",
            "config": config,
        }

    monkeypatch.setattr(tea, "_live", planted)
    monkeypatch.setattr(tea, "_record_for_pair", planted)
    monkeypatch.setattr(tea.tea_worker, "run", planted)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        values=requested,
        engine_mode="live",
        confirm_live_tea=True,
    ))
    assert order == expected_values
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"


def test_process_rows_rank_keeps_thermo_rank_and_stamps_disagreement(monkeypatch):
    low, mid, high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist=_shortlist(
                _item_from_record(high, thermo_rank=1),
                _item_from_record(low, thermo_rank=2),
                _item_from_record(mid, thermo_rank=3),
            ),
            held_process_basis=_held_from_record(mid),
            engine_mode="cache",
        ))
        assert first.get("success") is True
        assert [row.get("original_thermo_rank") for row in first["comparison_rows"]] == [
            1, 2, 3,
        ]
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="pareto_dominance",
            handle=handle,
        ))
        sorted_payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="sort",
            objective="msp_usd_per_kg",
            handle=handle,
        ))
    assert payload.get("success") is True
    points = payload["landscape_points"]
    frontier = payload["frontier_points"]
    assert payload["n_landscape_points"] == len(points) == 3
    assert payload["n_frontier_points"] == len(frontier)
    assert payload["n_frontier_points"] >= 1
    assert {point["original_thermo_rank"] for point in points} == {1, 2, 3}
    assert {point["rank"] for point in points} == {1, 2, 3}
    thermo_best = next(point for point in points if point["original_thermo_rank"] == 1)
    assert thermo_best["rank"] != 1
    assert thermo_best["thermo_econ_rank_disagreement"] is True
    for point in points:
        assert point["thermo_econ_rank_disagreement"] is (
            point["original_thermo_rank"] != point["rank"]
        )
    cheap = min(points, key=lambda point: float(point["msp_usd_per_kg"]))
    assert cheap["rank"] == 1
    assert cheap["original_thermo_rank"] == 2
    sort_points = sorted_payload["landscape_points"]
    assert [point["rank"] for point in sort_points] == [1, 2, 3]
    assert sort_points[0]["msp_usd_per_kg"] <= sort_points[1]["msp_usd_per_kg"]


def test_missing_thermo_rank_is_null_and_cannot_claim_disagreement(monkeypatch):
    low, mid, _high = _admitted_ldpe_trio()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist=_shortlist(
                _item_from_record(low),
                _item_from_record(mid),
            ),
            held_process_basis=_held_from_record(mid),
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        payload = _data(tea.rank_landscape(
            source="process_rows",
            operation="sort",
            handle=handle,
        ))
    points = payload["landscape_points"]
    assert all(point.get("original_thermo_rank") is None for point in points)
    assert all(point.get("thermo_econ_rank_disagreement") is False for point in points)


def test_planner_names_stage1_shortlists_from_keep_set_not_beam(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner must not start TEA")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "evaluate_process", forbidden)
    monkeypatch.setattr(tea, "evaluate_tea_lca_scenarios", forbidden)
    session = new_session()
    with bind_tool_session(session):
        out = dispatch(
            _PLAN,
            feed_polymers=_TRIPLE,
            breadth=5,
        )
        stored = load_handle(session, out["handle"])
        exact = stored["exact"]
    shortlists = exact.get("stage1_shortlists")
    assert isinstance(shortlists, list)
    assert shortlists
    max_keep = 0
    for block in shortlists:
        items = block.get("items") or []
        assert items
        ranks = [item.get("thermo_rank") for item in items]
        assert ranks == list(range(1, len(items) + 1))
        for item in items:
            assert item.get("target_polymer") == block.get("target_polymer")
            assert item.get("solvent")
            assert item.get("dissolution_temperature_c") is not None
        max_keep = max(max_keep, len(items))
    published = set()
    for route in exact.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        published.add((
            step.get("dissolved_polymer"),
            step.get("solvent"),
            step.get("temperature_c"),
        ))
    assert max_keep > 1
    visible = out.get("data") or {}
    assert "stage1_shortlists" in visible
    assert "top_k_sequences" not in visible
    assert exact.get("top_k_sequences")


def test_residue_polymer_that_is_not_a_first_stage_is_residue_was_costed(
    monkeypatch,
):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        planned = dispatch(_PLAN, feed_polymers=_PAIR, breadth=5)
        handle = planned["handle"]
        exact = load_handle(session, handle)["exact"]
        residue = exact["final_residue"]
        first = {
            (item.get("target_polymer"), item.get("solvent"), item.get("dissolution_temperature_c"))
            for block in exact.get("stage1_shortlists") or []
            for item in (block.get("items") or [])
        }
        payload = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist={
                "source": "plan_multistage_separation",
                "handle": handle,
                "items": [{
                    "target_polymer": residue,
                    "solvent": record["config"]["solvent"],
                    "dissolution_temperature_c": record["config"][
                        "dissolution_temperature_c"
                    ],
                    "thermo_rank": 1,
                }],
            },
            held_process_basis=_held_from_record(record),
            engine_mode="cache",
        ))
    assert (residue, record["config"]["solvent"], record["config"][
        "dissolution_temperature_c"
    ]) not in first
    assert payload.get("error_code") == "residue_was_costed"
    assert payload.get("polymer") == residue
    assert payload.get("final_residue") == residue
    assert "comparison_rows" not in payload


def test_later_stage_identity_at_feed_basis_refuses(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        planned = dispatch(_PLAN, feed_polymers=_TRIPLE, breadth=1)
        handle = planned["handle"]
        exact = load_handle(session, handle)["exact"]
        residue = tea._key(exact.get("final_residue"))
        first = set()
        for block in exact.get("stage1_shortlists") or []:
            for item in block.get("items") or []:
                first.add((
                    item.get("target_polymer"),
                    item.get("solvent"),
                    item.get("dissolution_temperature_c"),
                ))
        later_item = None
        for route in exact.get("top_k_sequences") or []:
            for step in (route.get("steps") or [])[1:]:
                ident = (
                    step.get("dissolved_polymer"),
                    step.get("solvent"),
                    step.get("temperature_c"),
                )
                if ident in first:
                    continue
                if tea._key(ident[0]) == residue:
                    continue
                later_item = ident
                break
            if later_item:
                break
        assert later_item is not None
        payload = _data(tea.evaluate_process(
            mode="evaluate",
            screening_shortlist={
                "source": "plan_multistage_separation",
                "handle": handle,
                "items": [{
                    "target_polymer": later_item[0],
                    "solvent": later_item[1],
                    "dissolution_temperature_c": later_item[2],
                    "thermo_rank": 1,
                }],
            },
            held_process_basis=_held_from_record(record),
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "stage_basis_not_derived"
    assert payload.get("named_blocker") == "incomplete_stage_basis_grid"
    assert "comparison_rows" not in payload


def test_cache_mode_unadmitted_does_not_quote_live(monkeypatch):
    mid = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        screening_shortlist=_shortlist(
            _item_from_record(mid, dissolution_temperature_c=999.0, thermo_rank=1),
            _item_from_record(mid, dissolution_temperature_c=998.0, thermo_rank=2),
        ),
        held_process_basis=_held_from_record(mid),
        engine_mode="cache",
    ))
    assert payload.get("error_code") != "live_tea_cost_confirmation_required"
    assert payload.get("error_code") == "no_simulation_result"
    failures = payload.get("failures") or []
    assert len(failures) == 2
    assert {row.get("original_thermo_rank") for row in failures} == {1, 2}
