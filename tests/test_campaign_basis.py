"""campaign_basis.v1 projection: held switches join without a campaign rerun."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import campaign_basis, tea

_PAIR_CAMPAIGN = Path(
    "/home/aaltamimi2/dissolve-v12-campaign/"
    "polymer-solvent-tea-lca-20260818/run_definition.json"
)
_SECONDS_PER_PAIR = 6991.363639038995 / 462


def _minimal_v2(**overrides):
    definition = {
        "schema": campaign_basis.CAMPAIGN_DEFINITION_SCHEMA_V2,
        "fixed_fields": {
            "target_plastic_percent": 55.0,
            "processing_capacity": 20_000.0,
            "energy_case": "C1",
            "precipitation_temperature_c": 35.0,
            "solvent_loss_pct": 0.01,
            "feedstock_distance_km": 0.0,
            "dissolution_capacity": 3.0,
            "labor_cost": 120_000.0,
        },
        "setpoint_rule": {
            "dissolution_temperature_c": (
                "lowest stored grid node from 25 through 160 C"
            ),
        },
        "pair_definitions": [
            {"config_sent": {"target_plastic": "LDPE", "solvent": "toluene"}},
            {"config_sent": {"target_plastic": "HDPE", "solvent": "dodecane"}},
        ],
    }
    definition.update(overrides)
    return definition


def test_pair_campaign_projects_and_is_not_incomplete():
    run = json.loads(_PAIR_CAMPAIGN.read_text(encoding="utf-8"))
    projected = campaign_basis.project_campaign_basis_v1(run)
    basis = projected["campaign_basis"]
    assert projected["campaign_basis_projection"] == "campaign_basis.v1"
    assert basis["complete"] is True
    assert basis["n_pairs"] == 462
    roles = basis["field_role"]
    assert roles["target_mass_percent"] == {
        "role": "held", "value": 55.0, "projection": "fixed_fields",
    }
    assert roles["processing_capacity_mt_per_yr"]["value"] == pytest.approx(20_000)
    assert roles["energy_case"]["value"] == "C1"
    assert roles["target_polymer"]["role"] == "varied"
    assert roles["solvent"]["role"] == "varied"
    assert roles["dissolution_temperature_c"]["role"] == "derived"
    assert roles["solvent_price_usd_per_kg"]["role"] == "derived"
    assert roles["burn_leftover_plastic"] == {
        "role": "held",
        "value": False,
        "projection": "worker_production",
    }
    assert "burn_leftover_plastic" not in (run.get("fixed_fields") or {})
    twelve = {public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS}
    assert twelve <= set(roles)


def test_sixty_fifteen_c2_is_exactly_three_held_mismatches():
    run = json.loads(_PAIR_CAMPAIGN.read_text(encoding="utf-8"))
    projected = campaign_basis.project_campaign_basis_v1(run)
    result = campaign_basis.held_field_mismatches(
        projected,
        {
            "target_mass_percent": 60.0,
            "processing_capacity_mt_per_yr": 15_000.0,
            "energy_case": "C2",
        },
        seconds_per_pair=_SECONDS_PER_PAIR,
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    fields = [row["field"] for row in result["mismatches"]]
    assert fields == [
        "target_mass_percent",
        "processing_capacity_mt_per_yr",
        "energy_case",
    ]
    by_field = {row["field"]: row for row in result["mismatches"]}
    assert by_field["target_mass_percent"]["campaign_value"] == pytest.approx(55)
    assert by_field["target_mass_percent"]["requested_value"] == pytest.approx(60)
    assert by_field["processing_capacity_mt_per_yr"]["campaign_value"] == pytest.approx(
        20_000
    )
    assert by_field["energy_case"]["campaign_value"] == "C1"
    assert by_field["energy_case"]["requested_value"] == "C2"
    assert "burn_leftover_plastic" not in fields
    quote = result["live_rerun_quote"]
    assert quote["n_pairs"] == 462
    assert quote["estimated_wall_seconds"] == pytest.approx(6991.363639038995)


def test_burn_true_is_switch_mismatch_not_a_ranking():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"burn_leftover_plastic": True},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert result["mismatches"] == [
        {
            "field": "burn_leftover_plastic",
            "campaign_value": False,
            "requested_value": True,
            "delta": {"campaign": False, "requested": True},
        }
    ]


def test_irr_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"irr": 0.15},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "irr"
    assert row["campaign_value"] == pytest.approx(0.10)
    assert row["requested_value"] == pytest.approx(0.15)


def test_income_tax_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"income_tax": 0.30},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "income_tax"
    assert row["campaign_value"] == pytest.approx(0.21)
    assert row["requested_value"] == pytest.approx(0.30)


def test_operating_days_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"operating_days": 300.0},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "operating_days"
    assert row["campaign_value"] == pytest.approx(350.4)
    assert row["requested_value"] == pytest.approx(300.0)


def test_labor_burden_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"labor_burden": 0.50},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "labor_burden"
    assert row["campaign_value"] == pytest.approx(0.90)
    assert row["requested_value"] == pytest.approx(0.50)


def test_finance_interest_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"finance_interest": 0.12},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "finance_interest"
    assert row["campaign_value"] == pytest.approx(0.08)
    assert row["requested_value"] == pytest.approx(0.12)


def test_finance_years_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"finance_years": 15},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "finance_years"
    assert row["campaign_value"] == pytest.approx(10)
    assert row["requested_value"] == pytest.approx(15)


def test_finance_fraction_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"finance_fraction": 0.4},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "finance_fraction"
    assert row["campaign_value"] == pytest.approx(0.0)
    assert row["requested_value"] == pytest.approx(0.4)


def test_startup_months_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"startup_months": 6},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "startup_months"
    assert row["campaign_value"] == pytest.approx(3)
    assert row["requested_value"] == pytest.approx(6)


def test_startup_FOCfrac_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"startup_FOCfrac": 0.5},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "startup_FOCfrac"
    assert row["campaign_value"] == pytest.approx(1)
    assert row["requested_value"] == pytest.approx(0.5)


def test_startup_VOCfrac_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"startup_VOCfrac": 0.5},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "startup_VOCfrac"
    assert row["campaign_value"] == pytest.approx(0.75)
    assert row["requested_value"] == pytest.approx(0.5)


def test_startup_salesfrac_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"startup_salesfrac": 0.8},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "startup_salesfrac"
    assert row["campaign_value"] == pytest.approx(0.5)
    assert row["requested_value"] == pytest.approx(0.8)


def test_wc_over_fci_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"WC_over_FCI": 0.10},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "WC_over_FCI"
    assert row["campaign_value"] == pytest.approx(0.05)
    assert row["requested_value"] == pytest.approx(0.10)


def test_warehouse_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"warehouse": 0.08},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "warehouse"
    assert row["campaign_value"] == pytest.approx(0.04)
    assert row["requested_value"] == pytest.approx(0.08)


def test_site_development_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"site_development": 0.18},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "site_development"
    assert row["campaign_value"] == pytest.approx(0.09)
    assert row["requested_value"] == pytest.approx(0.18)


def test_polymer_only_is_not_a_held_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"target_polymer": "LDPE", "solvent": "Toluene"},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_matching_held_values_are_not_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected,
        {
            "target_mass_percent": 55.0,
            "processing_capacity_mt_per_yr": 20_000.0,
            "energy_case": "C1",
            "burn_leftover_plastic": False,
            "irr": 0.10,
        },
    )
    assert result["mismatches"] == []


def test_held_values_come_from_the_run_definition_not_a_constant():
    definition = _minimal_v2()
    definition["fixed_fields"]["target_plastic_percent"] = 50.0
    projected = campaign_basis.project_campaign_basis_v1(definition)
    result = campaign_basis.held_field_mismatches(
        projected, {"target_mass_percent": 55.0},
    )
    assert result["mismatches"][0]["campaign_value"] == pytest.approx(50)
    assert result["mismatches"][0]["requested_value"] == pytest.approx(55)


def test_missing_v2_pieces_are_incomplete_not_mismatch():
    with pytest.raises(campaign_basis.CampaignBasisIncomplete) as caught:
        campaign_basis.project_campaign_basis_v1({"schema": "other"})
    assert caught.value.error_code == "campaign_basis_incomplete"
    assert set(caught.value.details["missing"]) == {
        "schema", "fixed_fields", "setpoint_rule", "pair_definitions",
    }


def test_wrong_schema_is_incomplete_even_with_the_rest():
    definition = _minimal_v2(
        schema="dissolve.ldpe_feed_quality_campaign_definition.v1",
    )
    with pytest.raises(campaign_basis.CampaignBasisIncomplete) as caught:
        campaign_basis.project_campaign_basis_v1(definition)
    assert "schema" in caught.value.details["missing"]


def test_absence_of_switches_in_fixed_fields_is_not_incomplete():
    definition = _minimal_v2()
    assert "burn_leftover_plastic" not in definition["fixed_fields"]
    projected = campaign_basis.project_campaign_basis_v1(definition)
    assert projected["campaign_basis"]["complete"] is True
    assert projected["campaign_basis"]["field_role"]["sell_leftover_plastic"][
        "value"
    ] is False


def test_two_switch_request_names_both_fields():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected,
        {
            "sell_leftover_plastic": True,
            "precipitation_configuration": "solvent mixing",
        },
    )
    fields = [row["field"] for row in result["mismatches"]]
    assert fields == [
        "sell_leftover_plastic",
        "precipitation_configuration",
    ]
    assert "burn_leftover_plastic" not in fields


def test_declared_switch_in_fixed_fields_is_held_not_stamped():
    definition = _minimal_v2()
    definition["fixed_fields"]["burn_leftover_plastic"] = True
    projected = campaign_basis.project_campaign_basis_v1(definition)
    role = projected["campaign_basis"]["field_role"]["burn_leftover_plastic"]
    assert role == {
        "role": "held",
        "value": True,
        "projection": "fixed_fields",
    }
    result = campaign_basis.held_field_mismatches(
        projected, {"burn_leftover_plastic": False},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert result["mismatches"][0]["campaign_value"] is True
    assert result["mismatches"][0]["requested_value"] is False
