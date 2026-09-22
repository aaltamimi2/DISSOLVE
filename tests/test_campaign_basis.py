"""campaign_basis.v1 projection: held switches join without a campaign rerun."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


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


@pytest.mark.parametrize(
    ('value', 'key', 'value_2', 'value_3', 'value_4'),
    [
        pytest.param('irr', 'irr', 0.15, 0.1, 0.15, id='irr'),
        pytest.param('income_tax', 'income_tax', 0.3, 0.21, 0.3, id='income_tax'),
        pytest.param('operating_days', 'operating_days', 300.0, 350.4, 300.0, id='operating_days'),
        pytest.param('labor_burden', 'labor_burden', 0.5, 0.9, 0.5, id='labor_burden'),
        pytest.param('finance_interest', 'finance_interest', 0.12, 0.08, 0.12, id='finance_interest'),
        pytest.param('finance_years', 'finance_years', 15, 10, 15, id='finance_years'),
        pytest.param('finance_fraction', 'finance_fraction', 0.4, 0.0, 0.4, id='finance_fraction'),
        pytest.param('startup_months', 'startup_months', 6, 3, 6, id='startup_months'),
        pytest.param('startup_FOCfrac', 'startup_FOCfrac', 0.5, 1, 0.5, id='startup_FOCfrac'),
        pytest.param('startup_VOCfrac', 'startup_VOCfrac', 0.5, 0.75, 0.5, id='startup_VOCfrac'),
        pytest.param('startup_salesfrac', 'startup_salesfrac', 0.8, 0.5, 0.8, id='startup_salesfrac'),
        pytest.param('WC_over_FCI', 'WC_over_FCI', 0.1, 0.05, 0.1, id='wc_over_fci'),
        pytest.param('warehouse', 'warehouse', 0.08, 0.04, 0.08, id='warehouse'),
        pytest.param('site_development', 'site_development', 0.18, 0.09, 0.18, id='site_development'),
        pytest.param('additional_piping', 'additional_piping', 0.09, 0.045, 0.09, id='additional_piping'),
        pytest.param('proratable_costs', 'proratable_costs', 0.2, 0.1, 0.2, id='proratable_costs'),
        pytest.param('field_expenses', 'field_expenses', 0.2, 0.1, 0.2, id='field_expenses'),
        pytest.param('construction', 'construction', 0.4, 0.2, 0.4, id='construction'),
        pytest.param('contingency', 'contingency', 0.8, 0.4, 0.8, id='contingency'),
        pytest.param('other_indirect_costs', 'other_indirect_costs', 0.2, 0.1, 0.2, id='other_indirect_costs'),
        pytest.param('property_insurance', 'property_insurance', 0.014, 0.007, 0.014, id='property_insurance'),
        pytest.param('maintenance', 'maintenance', 0.06, 0.03, 0.06, id='maintenance'),
    ],
)
def test_override_is_coefficient_mismatch(value, key, value_2, value_3, value_4):
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {key: value_2},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == value
    assert row["campaign_value"] == pytest.approx(value_3)
    assert row["requested_value"] == pytest.approx(value_4)


@pytest.mark.parametrize(
    ('value', 'value_2', 'value_3', 'key', 'value_4'),
    [
        pytest.param('depreciation', 'MACRS7', 'MACRS5', 'depreciation', 'MACRS5', id='depreciation_override_is_coefficient_mismatch'),
        pytest.param('steam_power_depreciation', 'MACRS20', 'MACRS7', 'steam_power_depreciation', 'MACRS7', id='steam_power'),
    ],
)
def test_depreciation_override_is_coefficient_mismatch_2(value, value_2, value_3, key, value_4):
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {key: value_4},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == value
    assert row["campaign_value"] == value_2
    assert row["requested_value"] == value_3


@pytest.mark.parametrize(
    ('key', 'value'),
    [
        pytest.param('depreciation', 'MACRS7', id='default_depreciation'),
        pytest.param('steam_power_depreciation', 'MACRS20', id='default_steam_power_depreciation'),
        pytest.param('lang_factor', None, id='none_lang_factor'),
    ],
)
def test_matching_is_not_a_held_mismatch(key, value):
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {key: value},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_duration_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"duration": [2025, 2045]},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "duration"
    assert tuple(row["campaign_value"]) == (2025, 2055)
    assert tuple(row["requested_value"]) == (2025, 2045)


def test_matching_default_duration_is_not_a_held_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"duration": [2025, 2055]},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_construction_schedule_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"construction_schedule": [0.5, 0.5]},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    row = result["mismatches"][0]
    assert row["field"] == "construction_schedule"
    assert tuple(row["campaign_value"]) == (0.08, 0.60, 0.32)
    assert tuple(row["requested_value"]) == (0.5, 0.5)


def test_matching_default_construction_schedule_is_not_a_held_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"construction_schedule": [0.08, 0.60, 0.32]},
    )
    assert result["mismatches"] == []
    assert "error_code" not in result


def test_lang_factor_override_is_coefficient_mismatch():
    projected = campaign_basis.project_campaign_basis_v1(_minimal_v2())
    result = campaign_basis.held_field_mismatches(
        projected, {"lang_factor": 3.0},
    )
    assert result["error_code"] == "campaign_basis_mismatch"
    assert [row["field"] for row in result["mismatches"]] == ["lang_factor"]
    row = result["mismatches"][0]
    assert row["campaign_value"] is None
    assert row["requested_value"] == pytest.approx(3.0)


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
