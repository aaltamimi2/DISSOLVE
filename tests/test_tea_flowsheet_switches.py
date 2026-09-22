"""Flowsheet switches belong in the serve key. The twelve-only cache is a trap."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest


from dissolve import tea, tea_worker


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


def test_cache_index_stays_unique_after_switch_projection():
    records = tea._records()
    index = tea._cache_index()
    assert len(records) == 24
    assert len(index) == len(records)


def test_omitted_and_explicit_production_defaults_share_the_serve_key():
    record = _record_with_energy("C1")
    twelve = dict(record["config"])
    explicit = {
        **twelve,
        "sell_leftover_plastic": False,
        "burn_leftover_plastic": False,
        "precipitation_temperature_format": "constant",
        "precipitation_configuration": "integrated heat transfer",
    }
    assert tea._config_key(twelve) == tea._config_key(explicit)
    hit = tea._cache_index().get(tea._config_key(twelve))
    assert hit is not None
    assert hit["label"] == record["label"]
    reconstructed = tea._scenario_config(_public_from_record(record))
    reconstructed_hit = tea._cache_index().get(tea._config_key(reconstructed))
    assert reconstructed_hit is not None
    assert reconstructed_hit["label"] == record["label"]
    assert reconstructed["sell_leftover_plastic"] is False
    assert reconstructed["burn_leftover_plastic"] is False
    assert reconstructed["precipitation_temperature_format"] == "constant"
    assert reconstructed["precipitation_configuration"] == (
        "integrated heat transfer"
    )


def test_burn_true_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    burned = {**dict(record["config"]), "burn_leftover_plastic": True}
    assert tea._config_key(record["config"]) != tea._config_key(burned)
    assert tea._cache_index().get(tea._config_key(burned)) is None
    analog = tea._record_for_design_point(burned)
    assert analog is not None
    assert analog["label"] == record["label"]


def test_cache_mode_refuses_flowsheet_mismatch_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = (record["result"].get("tea") or {}).get("msp_usd_per_kg")
    assert recorded_msp is not None

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("cache mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, burn_leftover_plastic=True)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    failures = list(payload.get("failures") or [])
    assert len(failures) == 1
    row = failures[0]
    assert row.get("error_type") == "cache_flowsheet_mismatch"
    assert row.get("msp_usd_per_kg") is None
    assert recorded_msp not in (row.get("msp_usd_per_kg"), row.get("tea"))
    deltas = list(row.get("flowsheet_switch_deltas") or [])
    burn = next(item for item in deltas if item["field"] == "burn_leftover_plastic")
    assert burn["recorded_value"] is False
    assert burn["requested_value"] is True
    assert row.get("burn_leftover_plastic") is True


def test_existing_twelve_lookup_still_hits_a_real_record(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("exact production-default lookup must stay on cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("engine_mode") == "cache"
    assert payload.get("cache_match_status") == "exact"
    rows = list(payload.get("comparison_rows") or [])
    assert len(rows) == 1
    assert rows[0]["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"])
    )
    assert rows[0]["burn_leftover_plastic"] is False


def test_leftover_disposition_conflict_refuses_both_true():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(
            record,
            sell_leftover_plastic=True,
            burn_leftover_plastic=True,
        ))
    assert caught.value.error_code == "leftover_disposition_conflict"
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(
            record,
            sell_leftover_plastic=True,
            burn_leftover_plastic=True,
        )],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "leftover_disposition_conflict"


def test_burn_requires_facilities_on_c2():
    record = _record_with_energy("C2")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(
            record, burn_leftover_plastic=True,
        ))
    assert caught.value.error_code == "burn_requires_facilities"
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, burn_leftover_plastic=True)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "burn_requires_facilities"


def test_independent_facilities_knob_is_energy_case_contract():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, facilities=False))
    assert caught.value.error_code == "energy_case_contract"


def test_drop_precipitation_format_is_not_on_this_instance():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(
            record, precipitation_temperature_format="drop",
        ))
    assert caught.value.error_code == "field_not_on_this_instance"


def test_d8_overlay_vocabulary_stays_the_twelve():
    public = [name for _internal, name in tea._DESIGN_POINT_PUBLIC_FIELDS]
    assert len(tea._CONFIG_FIELDS) == 12
    assert len(public) == 12
    for name in tea._FLOWSHEET_SWITCH_FIELDS:
        assert name not in tea._CONFIG_FIELDS
        assert name not in public


def test_worker_scenario_reads_switches_from_config():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    assert "sell_leftover_plastic=False, burn_leftover_plastic=False" not in source
    tree = ast.parse(source)
    keywords = None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Scenario"
        ):
            keywords = {kw.arg: kw.value for kw in node.keywords}
            break
    assert keywords is not None
    for name in (
        "sell_leftover_plastic",
        "burn_leftover_plastic",
        "precipitation_configuration",
    ):
        value = keywords[name]
        assert isinstance(value, ast.Call)
        dumped = ast.dump(value)
        assert "config" in dumped
    fmt = keywords["precipitation_temperature_format"]
    assert isinstance(fmt, ast.Name)
    assert fmt.id == "precipitation_format"
