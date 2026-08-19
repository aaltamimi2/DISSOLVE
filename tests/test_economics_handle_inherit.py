"""Fill omitted process fields from an economics handle, not a screen or pair."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch, tool_schemas
from dissolve import tea
from dissolve.session import (
    bind_tool_session, handle_rows, load_handle, new_session, store_handle,
)


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _record_with_energy(energy_case: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record["config"].get("energy_case") or "").upper() == energy_case
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


def _held_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    held = {
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    held.update(overrides)
    return held


def _item_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    item = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    item.update(overrides)
    return item


def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("inherit must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def _store_evaluate_handle(session, record):
    first = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)], engine_mode="cache",
    ))
    assert first.get("success") is True
    return store_handle(
        session,
        tool="evaluate_tea_lca_scenarios",
        source_basis="tea_cache_exact",
        data=first,
    ), first


def test_evaluate_comparison_rows_carry_the_lookup_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)], engine_mode="cache",
    ))
    row = payload["comparison_rows"][0]
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert name in row
        assert row[name] is not None
    assert row["target_polymer"] == row["polymer"] == record["config"]["target_plastic"]
    assert "field_origin" not in row


def test_first_incomplete_evaluate_still_refuses_without_handle(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        }],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "comparison_rows" not in payload


def test_omitted_nine_inherit_from_evaluate_handle(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert payload.get("success") is True
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert origin["target_polymer"] == "supplied"
    assert origin["solvent"] == "supplied"
    assert origin["dissolution_temperature_c"] == "supplied"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"
    assert first["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        recorded_msp,
    )


def test_override_is_supplied_and_rest_inherited(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
                "precipitation_temperature_c": 999.0,
            }],
            handle=handle,
            engine_mode="cache",
        ))
    row = (payload.get("comparison_rows") or payload.get("failures") or [None])[0]
    assert row is not None
    origin = row["field_origin"]
    assert origin["precipitation_temperature_c"] == "supplied"
    assert origin["target_mass_percent"] == "inherited"
    assert row["precipitation_temperature_c"] == pytest.approx(999.0)
    assert row.get("success") is not True


def test_handle_only_reruns_the_executed_twelve(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            handle=handle, engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._PUBLIC_REQUIRED_FIELDS:
        assert origin[name] == "inherited"


def test_complete_scenarios_win_over_leftover_handle(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(record)],
            handle=handle,
            engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    assert "field_origin" not in row
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_shortlist_without_held_inherits_nine_from_handle(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            screening_shortlist={
                "source": "explicit",
                "items": [_item_from_record(record)],
            },
            handle=handle,
            engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "inherited"


def test_held_basis_beats_handle_for_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle, _first = _store_evaluate_handle(session, record)
        payload = _data(tea.evaluate_tea_lca_scenarios(
            screening_shortlist={
                "source": "explicit",
                "items": [_item_from_record(record)],
            },
            held_process_basis=_held_from_record(record),
            handle=handle,
            engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    origin = row["field_origin"]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "supplied"
    assert origin["target_polymer"] == "from_screen"


def test_screening_handle_cannot_be_the_inherit_source(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        screen = dispatch(
            "screen_polymer_separation",
            feed_polymers=["LDPE", "PP"], temperature_min_c=80.0,
            temperature_max_c=140.0, top_k=20,
        )
        assert screen.get("handle")
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=screen["handle"],
            engine_mode="cache",
        ))
    assert payload.get("error_code") == "not_economics_handle"
    assert "comparison_rows" not in payload


def test_multi_row_handle_without_row_id_is_ambiguous(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [
                _public_from_record(record),
                _public_from_record(record, dissolution_temperature_c=999.0),
            ],
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_tea_lca_scenarios",
            source_basis="tea_cache_exact",
            data=first,
        )
        missing = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
            }],
            handle=handle,
            engine_mode="cache",
        ))
        selected = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=1,
            engine_mode="cache",
        ))
    assert missing.get("error_code") == "ambiguous_handle_row"
    assert missing.get("n_rows") == 2
    row = selected["comparison_rows"][0]
    assert row["dissolution_temperature_c"] == pytest.approx(
        cfg["dissolution_temperature_c"],
    )
    assert row["field_origin"]["dissolution_temperature_c"] == "supplied"
    assert row["field_origin"]["target_mass_percent"] == "inherited"


def test_unknown_handle_refuses_incomplete_follow_up(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
        }],
        handle="ghost-white-fox",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_handle"


def test_lookup_row_is_an_economics_inherit_source(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        lookup = _data(tea.lookup_admitted_process_records(
            target_polymer=cfg["target_plastic"],
            solvent=cfg["solvent"],
            energy_cases=[cfg["energy_case"]],
            dissolution_temperature_c=cfg["dissolution_temperature_c"],
            processing_capacity_mt_per_yr=cfg["processing_capacity"],
        ))
        handle = store_handle(
            session,
            tool="lookup_admitted_process_records",
            source_basis="tea_cache_exact",
            data=lookup,
        )
        rows = handle_rows(load_handle(session, handle))
        assert len(rows) >= 1
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [{
                "target_polymer": cfg["target_plastic"],
                "solvent": cfg["solvent"],
                "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            }],
            handle=handle,
            row_id=1 if len(rows) > 1 else None,
            engine_mode="cache",
        ))
    row = payload["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert row["field_origin"][name] == "inherited"


def test_dispatch_one_scenario_evaluate_issues_handle_and_keeps_rows(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    session = new_session()
    with bind_tool_session(session) as rec:
        out = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
        )
        assert out["available"] is True
        assert out.get("handle")
        assert "comparison_rows" in (out.get("data") or {})
        assert "top" not in out
        stored = load_handle(rec, out["handle"])
        assert stored["tool"] == "evaluate_tea_lca_scenarios"
        assert stored["exact"]["comparison_rows"][0]["target_polymer"]
        follow = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[{
                "target_polymer": record["config"]["target_plastic"],
                "solvent": record["config"]["solvent"],
                "dissolution_temperature_c": record["config"][
                    "dissolution_temperature_c"
                ],
            }],
            handle=out["handle"],
            engine_mode="cache",
        )
    assert follow["available"] is True
    origin = follow["data"]["comparison_rows"][0]["field_origin"]
    assert origin["target_mass_percent"] == "inherited"
    assert origin["target_polymer"] == "supplied"


def test_evaluate_schema_names_handle_and_row_id():
    schemas = {spec["name"]: spec for spec in tool_schemas()}
    props = schemas["evaluate_tea_lca_scenarios"]["parameters"]["properties"]
    assert props["handle"]["type"] == "string"
    assert "handle" not in (
        schemas["evaluate_tea_lca_scenarios"]["parameters"].get("required") or []
    )
    assert "row_id" in props
    assert "handle" not in schemas["solubility_query"]["parameters"]["properties"]
