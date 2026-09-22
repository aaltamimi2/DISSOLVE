"""Sensitivity inherits its baseline from an economics handle, not a screen."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


from dissolve.agent_tools import dispatch, tool_schemas
from dissolve import tea
from dissolve.session import bind_tool_session, new_session, store_handle


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _route_c1() -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == "ldpe-route-c1"
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


def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("sensitivity inherit must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def _store_evaluate_handle(session, record):
    first = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)], engine_mode="cache",
    ))
    assert first.get("success") is True
    return store_handle(
        session,
            tool="evaluate_process",
        source_basis="tea_cache_exact",
        data=first,
    ), first


def test_sensitivity_rows_carry_the_executed_twelve(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    for row in rows:
        for name in tea._PUBLIC_REQUIRED_FIELDS:
            assert name in row
            assert row[name] is not None
        assert row["target_polymer"] == record["config"]["target_plastic"]
        assert "field_origin" not in row


def test_first_incomplete_sensitivity_still_refuses_without_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        {
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "sensitivity_rows" not in payload


def test_sensitivity_inherits_baseline_from_evaluate_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            {
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=handle,
            engine_mode="cache",
            analysis_mode="tornado",
        ))
    assert payload.get("success") is True
    origin = payload["field_origin"]
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    row = payload["sensitivity_rows"][0]
    assert row["field_origin"]["target_mass_percent"] == "inherited"
    prices = {float(item["value"]) for item in payload["sensitivity_rows"]}
    assert float(cfg["solvent_price"]) in prices
    assert len(prices) >= 2


def test_handle_only_sensitivity_inherits_the_executed_twelve(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            parameter="solvent_price",
            handle=handle,
            engine_mode="cache",
            analysis_mode="tornado",
        ))
    assert payload.get("success") is True
    origin = payload["field_origin"]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"
    assert payload["polymer"] == record["config"]["target_plastic"]


def test_complete_sensitivity_scenario_wins_over_leftover_handle(monkeypatch):
    record = _route_c1()
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            handle=handle,
            engine_mode="cache",
        ))
    assert payload.get("success") is True
    assert "field_origin" not in payload
    assert "field_origin" not in payload["sensitivity_rows"][0]


def test_screening_handle_cannot_be_sensitivity_baseline(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        payload = _data(tea.analyze_tea_sensitivity(
            {
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            },
            parameter="solvent_price",
            handle=screen["handle"],
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "not_economics_handle"


def test_evaluate_can_inherit_from_a_sensitivity_row(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        sweep = _data(tea.analyze_tea_sensitivity(
            _public_from_record(record),
            parameter="solvent_price",
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=sweep,
        )
        missing = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="cache",
        ))
        baseline_index = next(
            index
            for index, row in enumerate(sweep["sensitivity_rows"], 1)
            if abs(float(row["value"]) - float(cfg["solvent_price"])) < 1e-9
        )
        follow = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=baseline_index,
            engine_mode="cache",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    row = follow["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert row["field_origin"]["solvent_price_usd_per_kg"] == "inherited"


def test_dispatch_tornado_from_evaluate_handle(monkeypatch):
    record = _route_c1()
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        evaluated = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=_public_from_record(record),
            engine_mode="cache",
        )
        tornado = dispatch(
            "evaluate_process",
            mode="sensitivity",
            parameter="solvent_price",
            analysis_mode="tornado",
            handle=evaluated["handle"],
            engine_mode="cache",
        )
    assert tornado["available"] is True
    assert tornado.get("handle")
    assert "sensitivity_rows" in (tornado.get("data") or {})
    origin = tornado["data"]["field_origin"]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"
    assert tornado["data"]["polymer"] == cfg["target_plastic"]


def test_sensitivity_schema_names_handle_and_optional_scenario():
    schemas = {spec["name"]: spec for spec in tool_schemas()}
    assert "analyze_tea_sensitivity" not in schemas
    params = inspect.signature(tea.analyze_tea_sensitivity).parameters
    assert "handle" in params
    assert "row_id" in params
    assert "scenario" in params
    wrap = schemas["evaluate_process"]["parameters"]["properties"]
    assert wrap["process_config"]["type"] == "object"
