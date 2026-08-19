"""Headless evaluate refuses an incomplete twelve instead of filling it."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

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


def _public_from_record(record: dict, **overrides) -> dict:
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


def _forbid_pair_fill(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("headless evaluate must not pair-default")

    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea, "_live", forbidden)


def test_polymer_and_solvent_alone_list_missing_public_fields(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
        }],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    missing = list(payload.get("missing") or [])
    assert "dissolution_temperature_c" in missing
    assert missing == [
        name for name in tea._PUBLIC_REQUIRED_FIELDS
        if name not in {"target_polymer", "solvent"}
    ]
    assert payload.get("msp_usd_per_kg") is None
    assert "comparison_rows" not in payload


def test_polymer_solvent_and_t_lists_the_nine(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
    }
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(scenario)
    assert caught.value.error_code == "incomplete_process_config"
    assert caught.value.details["missing"] == list(tea._NINE_HELD_PUBLIC_FIELDS)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [scenario], engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)
    assert payload.get("comparison_rows") is None or "comparison_rows" not in payload


def test_temperature_c_is_not_an_alias_and_does_not_fill(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(
            record,
            temperature_c=record["config"]["dissolution_temperature_c"],
        )],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])
    assert payload.get("cache_match_status") is None


def test_temperature_c_rename_does_not_supply_dissolution_t(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    scenario = _public_from_record(record)
    renamed = scenario.pop("dissolution_temp_c")
    scenario["temperature_c"] = renamed
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [scenario], engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "temperature_c" in list(payload.get("extra_keys") or [])
    assert payload.get("error_code") != "incomplete_process_config"


def test_unknown_extra_key_refuses_a_complete_twelve(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, not_a_process_field=1)],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert "not_a_process_field" in list(payload.get("extra_keys") or [])


def test_complete_twelve_still_hits_the_cache(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("complete twelve must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"
    assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        recorded_msp
    )


def test_complete_twelve_at_unmatched_t_is_a_miss_not_a_fill(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("cache miss must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, dissolution_temp_c=999.0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("cache_match_status") == "miss"
    assert payload.get("error_code") != "incomplete_process_config"
    failures = list(payload.get("failures") or [])
    assert failures
    assert failures[0].get("msp_usd_per_kg") is None
    assert recorded_msp not in (
        failures[0].get("msp_usd_per_kg"), failures[0].get("tea"),
    )


def test_omitted_switches_on_a_complete_twelve_keep_production(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("production defaults must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    reconstructed = tea._scenario_config(_public_from_record(record))
    assert reconstructed["sell_leftover_plastic"] is False
    assert reconstructed["burn_leftover_plastic"] is False
    assert reconstructed["irr"] == pytest.approx(0.10)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("cache_match_status") == "exact"


def test_sensitivity_refuses_the_same_incomplete_nine(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]
    _forbid_pair_fill(monkeypatch)
    payload = _data(tea.analyze_tea_sensitivity(
        {
            "target_polymer": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        },
        parameter="target_mass_percent",
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "incomplete_process_config"
    assert payload.get("missing") == list(tea._NINE_HELD_PUBLIC_FIELDS)


def test_worker_internal_names_satisfy_the_public_twelve(monkeypatch):
    record = _record_with_energy("C1")
    cfg = record["config"]

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("internal names of a complete twelve stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [{
            "target_plastic": cfg["target_plastic"],
            "solvent": cfg["solvent"],
            "target_plastic_percent": cfg["target_plastic_percent"],
            "processing_capacity": cfg["processing_capacity"],
            "energy_case": cfg["energy_case"],
            "dissolution_temperature_c": cfg["dissolution_temperature_c"],
            "precipitation_temperature_c": cfg["precipitation_temperature_c"],
            "solvent_price": cfg["solvent_price"],
            "solvent_loss_pct": cfg["solvent_loss_pct"],
            "feedstock_distance_km": cfg["feedstock_distance_km"],
            "dissolution_capacity": cfg["dissolution_capacity"],
            "labor_cost": cfg["labor_cost"],
        }],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("cache_match_status") == "exact"
