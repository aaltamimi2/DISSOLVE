"""Unreached @parameter baselines join the serve key once they are public."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea, tea_worker


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
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


def test_omitted_and_explicit_coefficient_defaults_share_the_serve_key():
    record = _record_with_energy("C1")
    twelve = dict(record["config"])
    explicit = {
        **twelve,
        "irr": 0.10,
        "income_tax": 0.21,
        "operating_days": 350.4,
        "labor_burden": 0.90,
        "finance_interest": 0.08,
        "finance_years": 10,
        "finance_fraction": 0.0,
        "startup_months": 3,
        "startup_FOCfrac": 1,
        "startup_VOCfrac": 0.75,
        "feedstock_price_usd_per_kg": 0.01,
        "centrifuged_plastic_solvent_content_pct": 50.0,
        "natural_gas_price_usd_per_m3": tea._NATURAL_GAS_PRICE_USD_PER_M3,
    }
    assert tea._config_key(twelve) == tea._config_key(explicit)
    reconstructed = tea._scenario_config(_public_from_record(record))
    assert reconstructed["irr"] == pytest.approx(0.10)
    assert reconstructed["income_tax"] == pytest.approx(0.21)
    assert reconstructed["operating_days"] == pytest.approx(350.4)
    assert reconstructed["labor_burden"] == pytest.approx(0.90)
    assert reconstructed["finance_interest"] == pytest.approx(0.08)
    assert reconstructed["finance_years"] == pytest.approx(10)
    assert reconstructed["finance_fraction"] == pytest.approx(0.0)
    assert reconstructed["startup_months"] == pytest.approx(3)
    assert reconstructed["startup_FOCfrac"] == pytest.approx(1)
    assert reconstructed["startup_VOCfrac"] == pytest.approx(0.75)
    assert reconstructed["feedstock_price_usd_per_kg"] == pytest.approx(0.01)
    assert reconstructed["centrifuged_plastic_solvent_content_pct"] == pytest.approx(50.0)
    assert reconstructed["natural_gas_price_usd_per_m3"] == pytest.approx(
        tea._NATURAL_GAS_PRICE_USD_PER_M3
    )
    hit = tea._cache_index().get(tea._config_key(reconstructed))
    assert hit is not None
    assert hit["label"] == record["label"]


def test_c2_does_not_carry_natural_gas_price():
    record = _record_with_energy("C2")
    reconstructed = tea._scenario_config(_public_from_record(record))
    assert "natural_gas_price_usd_per_m3" not in reconstructed
    assert tea._cache_index().get(tea._config_key(reconstructed))["label"] == (
        record["label"]
    )


def test_irr_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "irr": 0.15}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_irr_override_instead_of_the_other_plant(monkeypatch):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("coefficient mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, irr=0.15)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    irr = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "irr"
    )
    assert irr["recorded_value"] == pytest.approx(0.10)
    assert irr["requested_value"] == pytest.approx(0.15)


def test_income_tax_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "income_tax": 0.30}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_income_tax_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("income_tax mismatch must not fall through to live")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, income_tax=0.30)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    tax = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "income_tax"
    )
    assert tax["recorded_value"] == pytest.approx(0.21)
    assert tax["requested_value"] == pytest.approx(0.30)
    assert row.get("income_tax") == pytest.approx(0.30)


def test_cache_evaluate_echoes_projected_income_tax(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("default income_tax must hit the cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    row = payload["comparison_rows"][0]
    assert row["income_tax"] == pytest.approx(0.21)
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_operating_days_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "operating_days": 300.0}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_operating_days_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "operating_days mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, operating_days=300.0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    days = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "operating_days"
    )
    assert days["recorded_value"] == pytest.approx(350.4)
    assert days["requested_value"] == pytest.approx(300.0)
    assert row.get("operating_days") == pytest.approx(300.0)
    assert not any(
        item["field"] == "income_tax" for item in row["flowsheet_switch_deltas"]
    )


def test_cache_evaluate_echoes_projected_operating_days(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError("default operating_days must hit the cache")

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    row = payload["comparison_rows"][0]
    assert row["operating_days"] == pytest.approx(350.4)
    assert row["income_tax"] == pytest.approx(0.21)
    assert row["labor_burden"] == pytest.approx(0.90)
    assert row["finance_interest"] == pytest.approx(0.08)
    assert row["finance_years"] == pytest.approx(10)
    assert row["finance_fraction"] == pytest.approx(0.0)
    assert row["startup_months"] == pytest.approx(3)
    assert row["startup_FOCfrac"] == pytest.approx(1)
    assert row["startup_VOCfrac"] == pytest.approx(0.75)
    assert row["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_nonpositive_operating_days_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, operating_days=0))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "operating_days"


def test_labor_burden_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "labor_burden": 0.50}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_labor_burden_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "labor_burden mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, labor_burden=0.50)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    burden = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "labor_burden"
    )
    assert burden["recorded_value"] == pytest.approx(0.90)
    assert burden["requested_value"] == pytest.approx(0.50)
    assert row.get("labor_burden") == pytest.approx(0.50)
    assert not any(
        item["field"] == "operating_days"
        for item in row["flowsheet_switch_deltas"]
    )


def test_negative_labor_burden_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, labor_burden=-0.1))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "labor_burden"


def test_finance_interest_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "finance_interest": 0.12}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_finance_interest_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "finance_interest mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, finance_interest=0.12)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    rate = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "finance_interest"
    )
    assert rate["recorded_value"] == pytest.approx(0.08)
    assert rate["requested_value"] == pytest.approx(0.12)
    assert row.get("finance_interest") == pytest.approx(0.12)
    assert not any(
        item["field"] == "labor_burden"
        for item in row["flowsheet_switch_deltas"]
    )


def test_negative_finance_interest_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_interest=-0.01))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_interest"


def test_percent_integer_finance_interest_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_interest=8))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_interest"


def test_finance_years_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "finance_years": 15}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_finance_years_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "finance_years mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, finance_years=15)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    years = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "finance_years"
    )
    assert years["recorded_value"] == pytest.approx(10)
    assert years["requested_value"] == pytest.approx(15)
    assert row.get("finance_years") == pytest.approx(15)
    assert not any(
        item["field"] == "finance_interest"
        for item in row["flowsheet_switch_deltas"]
    )


def test_nonpositive_finance_years_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_years=0))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_years"


def test_noninteger_finance_years_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_years=10.5))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_years"


def test_finance_fraction_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "finance_fraction": 0.4}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_finance_fraction_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "finance_fraction mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, finance_fraction=0.4)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "finance_fraction"
    )
    assert fraction["recorded_value"] == pytest.approx(0.0)
    assert fraction["requested_value"] == pytest.approx(0.4)
    assert row.get("finance_fraction") == pytest.approx(0.4)
    assert not any(
        item["field"] == "finance_years"
        for item in row["flowsheet_switch_deltas"]
    )


def test_full_debt_finance_fraction_is_a_legal_override(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "finance_fraction=1.0 is 100 percent debt, not invalid_scenario"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, finance_fraction=1.0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "finance_fraction"
    )
    assert fraction["requested_value"] == pytest.approx(1.0)


def test_negative_finance_fraction_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_fraction=-0.1))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_fraction"


def test_percent_integer_finance_fraction_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, finance_fraction=40))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "finance_fraction"


def test_startup_months_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "startup_months": 6}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_startup_months_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_months mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_months=6)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    months = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_months"
    )
    assert months["recorded_value"] == pytest.approx(3)
    assert months["requested_value"] == pytest.approx(6)
    assert row.get("startup_months") == pytest.approx(6)
    assert not any(
        item["field"] == "finance_fraction"
        for item in row["flowsheet_switch_deltas"]
    )


def test_zero_startup_months_is_a_legal_override(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_months=0 is a legal override, not invalid_scenario"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_months=0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    months = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_months"
    )
    assert months["requested_value"] == pytest.approx(0)


def test_negative_startup_months_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_months=-1))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_months"


def test_startup_months_above_one_year_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_months=13))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_months"


def test_startup_FOCfrac_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "startup_FOCfrac": 0.5}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_startup_FOCfrac_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_FOCfrac mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_FOCfrac=0.5)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_FOCfrac"
    )
    assert fraction["recorded_value"] == pytest.approx(1)
    assert fraction["requested_value"] == pytest.approx(0.5)
    assert row.get("startup_FOCfrac") == pytest.approx(0.5)
    assert not any(
        item["field"] == "startup_months"
        for item in row["flowsheet_switch_deltas"]
    )


def test_zero_startup_FOCfrac_is_a_legal_override(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_FOCfrac=0 is a legal override, not invalid_scenario"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_FOCfrac=0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_FOCfrac"
    )
    assert fraction["requested_value"] == pytest.approx(0)


def test_negative_startup_FOCfrac_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_FOCfrac=-0.1))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_FOCfrac"


def test_percent_integer_startup_FOCfrac_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_FOCfrac=50))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_FOCfrac"


def test_startup_VOCfrac_override_does_not_share_the_twelve_only_serve_key():
    record = _record_with_energy("C1")
    overridden = {**dict(record["config"]), "startup_VOCfrac": 0.5}
    assert tea._config_key(record["config"]) != tea._config_key(overridden)
    assert tea._cache_index().get(tea._config_key(overridden)) is None


def test_cache_mode_refuses_startup_VOCfrac_override_instead_of_the_other_plant(
    monkeypatch,
):
    record = _record_with_energy("C1")
    recorded_msp = float(record["result"]["tea"]["msp_usd_per_kg"])

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_VOCfrac mismatch must not fall through to live"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_VOCfrac=0.5)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    assert row.get("msp_usd_per_kg") is None
    assert row.get("msp_usd_per_kg") != recorded_msp
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_VOCfrac"
    )
    assert fraction["recorded_value"] == pytest.approx(0.75)
    assert fraction["requested_value"] == pytest.approx(0.5)
    assert row.get("startup_VOCfrac") == pytest.approx(0.5)
    assert not any(
        item["field"] == "startup_FOCfrac"
        for item in row["flowsheet_switch_deltas"]
    )


def test_full_startup_VOCfrac_is_a_legal_override(monkeypatch):
    record = _record_with_energy("C1")

    def forbidden_live(config, timeout_seconds):
        raise AssertionError(
            "startup_VOCfrac=1.0 is 100 percent VOC, not invalid_scenario"
        )

    monkeypatch.setattr(tea, "_live", forbidden_live)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, startup_VOCfrac=1.0)],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "cache_flowsheet_mismatch"
    row = (payload.get("failures") or [])[0]
    fraction = next(
        item for item in row["flowsheet_switch_deltas"]
        if item["field"] == "startup_VOCfrac"
    )
    assert fraction["requested_value"] == pytest.approx(1.0)


def test_negative_startup_VOCfrac_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_VOCfrac=-0.1))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_VOCfrac"


def test_percent_integer_startup_VOCfrac_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_VOCfrac=75))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "startup_VOCfrac"


def test_percent_integer_income_tax_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, income_tax=21))
    assert caught.value.error_code == "invalid_scenario"
    assert caught.value.details["field"] == "income_tax"


def test_other_g_constructor_coefficients_are_not_dumped():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, lang_factor=3.0))
    assert caught.value.error_code == "unknown_process_field"
    assert "lang_factor" in list(caught.value.details.get("extra_keys") or [])
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, WC_over_FCI=0.10))
    assert caught.value.error_code == "unknown_process_field"
    assert "WC_over_FCI" in list(caught.value.details.get("extra_keys") or [])
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, startup_salesfrac=0.8))
    assert caught.value.error_code == "unknown_process_field"
    assert "startup_salesfrac" in list(caught.value.details.get("extra_keys") or [])
    assert "lang_factor" not in tea._COEFFICIENT_DEFAULTS
    assert "WC_over_FCI" not in tea._COEFFICIENT_DEFAULTS
    assert "depreciation" not in tea._COEFFICIENT_DEFAULTS
    assert "startup_salesfrac" not in tea._COEFFICIENT_DEFAULTS
    assert "startup_VOCfrac" in tea._COEFFICIENT_DEFAULTS
    assert "startup_VOCfrac" in tea.public_process_field_names()
    assert "startup_FOCfrac" in tea._COEFFICIENT_DEFAULTS
    assert "startup_FOCfrac" in tea.public_process_field_names()
    assert "startup_months" in tea._COEFFICIENT_DEFAULTS
    assert "startup_months" in tea.public_process_field_names()
    assert "finance_fraction" in tea._COEFFICIENT_DEFAULTS
    assert "finance_fraction" in tea.public_process_field_names()
    assert "finance_years" in tea._COEFFICIENT_DEFAULTS
    assert "finance_years" in tea.public_process_field_names()
    assert "finance_interest" in tea._COEFFICIENT_DEFAULTS
    assert "finance_interest" in tea.public_process_field_names()
    assert "labor_burden" in tea.public_process_field_names()
    assert "operating_days" in tea.public_process_field_names()


def test_natural_gas_price_on_c2_is_energy_case_contract():
    record = _record_with_energy("C2")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(
            record, natural_gas_price_usd_per_m3=0.20,
        ))
    assert caught.value.error_code == "energy_case_contract"


def test_percent_integer_irr_is_refused():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, irr=10))
    assert caught.value.error_code == "invalid_scenario"


def test_polymer_ratio_is_not_on_single_step():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, polymer_ratio=0.5))
    assert caught.value.error_code == "field_not_on_this_instance"


def test_recovery_is_not_in_the_model():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, recovery=0.5))
    assert caught.value.error_code == "field_not_in_model"
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, recovery=0.9)],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "field_not_in_model"


def test_commented_out_setter_is_not_adjustable():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(
            record, set_boiling_point=400,
        ))
    assert caught.value.error_code == "field_not_adjustable"


def test_tau_is_expert_surface_only():
    record = _record_with_energy("C1")
    with pytest.raises(tea._ScenarioInputError) as caught:
        tea._scenario_config(_public_from_record(record, tau_h=4.0))
    assert caught.value.error_code == "expert_surface_only"
    assert "tea_polymer_parameters.py" in str(
        caught.value.details.get("expert_surface") or caught.value
    )


def test_d8_overlay_still_excludes_coefficients():
    for name in (
        "irr",
        "income_tax",
        "operating_days",
        "labor_burden",
        "finance_interest",
        "finance_years",
        "finance_fraction",
        "startup_months",
        "startup_FOCfrac",
        "startup_VOCfrac",
        "feedstock_price_usd_per_kg",
        "centrifuged_plastic_solvent_content_pct",
        "natural_gas_price_usd_per_m3",
    ):
        assert name not in tea._CONFIG_FIELDS
        assert name not in {
            public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS
        }


def test_worker_calls_the_unreached_setters():
    source = Path(tea_worker.__file__).read_text(encoding="utf-8")
    assert "set_IRR(" in source
    assert "process.tea.income_tax" in source
    assert "process.tea.operating_days" in source
    assert "process.tea.labor_burden" in source
    assert "process.tea.finance_interest" in source
    assert "process.tea.finance_years" in source
    assert "process.tea.finance_fraction" in source
    assert "process.tea.startup_months" in source
    assert "process.tea.startup_FOCfrac" in source
    assert "process.tea.startup_VOCfrac" in source
    assert "set_feedstock_price(" in source
    assert "set_centrifuged_plastic_solvent_content(" in source
    assert "set_natural_gas_price(" in source
    assert "if energy[\"facilities\"]:" in source
    assert "set_polymer_mass_fraction(" not in source
