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
        "feedstock_price_usd_per_kg": 0.01,
        "centrifuged_plastic_solvent_content_pct": 50.0,
        "natural_gas_price_usd_per_m3": tea._NATURAL_GAS_PRICE_USD_PER_M3,
    }
    assert tea._config_key(twelve) == tea._config_key(explicit)
    reconstructed = tea._scenario_config(_public_from_record(record))
    assert reconstructed["irr"] == pytest.approx(0.10)
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
    assert "set_feedstock_price(" in source
    assert "set_centrifuged_plastic_solvent_content(" in source
    assert "set_natural_gas_price(" in source
    assert "if energy[\"facilities\"]:" in source
    assert "set_polymer_mass_fraction(" not in source
