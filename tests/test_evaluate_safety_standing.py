"""evaluate / lookup comparison rows stamp safety_standing.

Rank already copies a legal standing object. The producer default is
not_requested: no safety call was made. Not a GSK-fail filter.
"""
from __future__ import annotations

import json
from pathlib import Path


from dissolve import tea
from dissolve.session import bind_tool_session, new_session, store_handle


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("safety standing stamp must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


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


def test_evaluate_comparison_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(c1)], engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 1
    assert rows[0]["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["safety_standing"]["status"] != "evaluated"


def test_evaluate_process_evaluate_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(c1),
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload["comparison_rows"][0]["safety_standing"] == {
        "status": "not_requested",
    }


def test_two_row_evaluate_stamps_every_comparison_row(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(c1), _public_from_record(c2)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 2
    assert {row["energy_case"] for row in rows} == {"C1", "C2"}
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["energy_case"] != rows[1]["energy_case"]


def test_lookup_comparison_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1", "C2"],
    ))
    assert payload.get("success") is True
    rows = payload["comparison_rows"]
    assert len(rows) == 2
    cases = {row["energy_case"] for row in rows}
    assert cases == {"C1", "C2"}
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}


def test_evaluate_process_lookup_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "energy_cases": ["C1"],
        },
    ))
    assert payload.get("success") is True
    assert payload["comparison_rows"][0]["safety_standing"] == {
        "status": "not_requested",
    }


def test_rank_copies_producer_not_requested(monkeypatch):
    c1 = _record_by_label("ldpe-route-c1")
    c2 = _record_by_label("ldpe-route-c2")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        first = _data(tea.evaluate_tea_lca_scenarios(
            [_public_from_record(c1), _public_from_record(c2)],
            engine_mode="cache",
        ))
        handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=first,
        )
        ranked = _data(tea.rank_landscape(handle=handle))
    assert first["comparison_rows"][0]["safety_standing"]["status"] == (
        "not_requested"
    )
    assert ranked.get("success") is True
    for point in ranked["landscape_points"]:
        assert point["safety_standing"] == {"status": "not_requested"}


def test_illegal_bound_standing_is_not_copied():
    assert tea._comparison_safety_standing({
        "safety_standing": {"status": "fail", "excluded": True},
    }) == {"status": "not_requested"}
    evaluated = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    assert tea._comparison_safety_standing(
        {"safety_standing": evaluated},
    ) == evaluated
    assert tea._comparison_safety_standing(None) == {
        "status": "not_requested",
    }


def test_sensitivity_rows_stamp_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.analyze_tea_sensitivity(
        _public_from_record(c1),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    assert "safety_standing" not in payload
    values = {row["value"] for row in rows}
    assert len(values) >= 2
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
        assert row["safety_standing"]["status"] != "evaluated"


def test_evaluate_process_sensitivity_stamps_not_requested(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    payload = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config=_public_from_record(c1),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert "safety_standing" not in payload
    rows = payload["sensitivity_rows"]
    assert len(rows) >= 2
    for row in rows:
        assert row["safety_standing"] == {"status": "not_requested"}
    assert rows[0]["value"] != rows[1]["value"]


def test_incomplete_sensitivity_does_not_stamp_rows(monkeypatch):
    _forbid_live(monkeypatch)
    c1 = _record_by_label("ldpe-route-c1")
    cfg = c1["config"]
    payload = _data(tea.evaluate_process(
        mode="sensitivity",
        process_config={
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert "sensitivity_rows" not in payload


def test_sensitivity_row_helper_copies_legal_standing():
    c1 = _record_by_label("ldpe-route-c1")
    fields = tea._sensitivity_row_process_fields(c1["config"])
    assert fields["safety_standing"] == {"status": "not_requested"}
    evaluated = {
        "status": "evaluated",
        "safety_profile": {"ghs_signal_word": "Danger"},
    }
    copied = tea._sensitivity_row_process_fields(
        c1["config"],
        {"safety_standing": evaluated},
    )
    assert copied["safety_standing"] == evaluated
    skipped = tea._sensitivity_row_process_fields(
        c1["config"],
        {"safety_standing": {"status": "fail", "excluded": True}},
    )
    assert skipped["safety_standing"] == {"status": "not_requested"}
    assert "excluded" not in skipped["safety_standing"]
