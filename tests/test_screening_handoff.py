"""Screening-to-economics handoff: shortlist plus held nine, not a cache fill."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


from dissolve import tea


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


def _shortlist(*items, source="explicit"):
    return {"source": source, "items": list(items)}


def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("handoff must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_shortlist_without_held_basis_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(_item_from_record(record)),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert "comparison_rows" not in payload


def test_held_basis_missing_one_field_names_only_that_field(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    held = _held_from_record(record)
    held.pop("energy_case")
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=held,
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "screening_basis_incomplete"
    assert payload.get("missing") == ["energy_case"]


def test_shortlist_item_missing_t_is_screening_shortlist_incomplete(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    item = _item_from_record(record)
    item.pop("dissolution_temperature_c")
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(item),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "screening_shortlist_incomplete"
    assert payload.get("item_index") == 0
    assert payload.get("missing") == ["dissolution_temperature_c"]


def test_temperature_c_on_shortlist_item_maps_and_hits_cache(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("mapped handoff T must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden_live)
    item = _item_from_record(record)
    t = item.pop("dissolution_temperature_c")
    item["temperature_c"] = t
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(item),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"
    row = payload["comparison_rows"][0]
    assert row["msp_usd_per_kg"] == pytest.approx(recorded_msp)
    assert row["dissolution_temperature_c"] == pytest.approx(t)
    origin = row["field_origin"]
    assert origin["target_polymer"] == "from_screen"
    assert origin["solvent"] == "from_screen"
    assert origin["dissolution_temperature_c"] == "from_screen"
    for name in tea._NINE_HELD_PUBLIC_FIELDS:
        assert origin[name] == "supplied"
        assert origin[name] != "from_screen"


def test_temperature_c_on_process_config_still_refuses(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    cfg = record["config"]
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            "temperature_c": cfg["dissolution_temperature_c"],
            **_held_from_record(record),
        }],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])


def test_screening_shortlist_inside_process_config_is_unknown(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            **_item_from_record(record),
            **_held_from_record(record),
            "screening_shortlist": {"source": "explicit", "items": []},
        }],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "screening_shortlist" in list(payload.get("extra_keys") or [])


def test_scenarios_and_shortlist_together_conflict(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{**_item_from_record(record), **_held_from_record(record)}],
        screening_shortlist=_shortlist(_item_from_record(record)),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "conflicting_evaluate_composition"


def test_expansion_cap_matches_evaluate(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    items = [_item_from_record(record)] * 21
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(*items),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "too_many_scenarios"


def test_two_item_expansion_is_two_rows_not_one_fill(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("cache-only expansion must not start live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        screening_shortlist=_shortlist(
            _item_from_record(record),
            _item_from_record(record, dissolution_temperature_c=999.0),
        ),
        held_process_basis=_held_from_record(record),
        engine_mode="cache",
    ))
    rows = list(payload.get("comparison_rows") or payload.get("failures") or [])
    assert payload.get("error_code") == "no_simulation_result" or payload.get(
        "completed"
    ) == 1
    if payload.get("success") is True:
        assert payload.get("completed") == 1
        assert payload.get("failed") == 1
        assert len(payload["comparison_rows"]) == 2
        assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
            recorded_msp
        )
        assert payload["comparison_rows"][1]["msp_usd_per_kg"] is None
    else:
        failures = list(payload.get("failures") or [])
        assert len(failures) == 2
        assert recorded_msp not in (
            failures[1].get("msp_usd_per_kg"), failures[1].get("tea"),
        )


def test_polymer_solvent_t_as_process_config_is_still_incomplete(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_item_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
