"""Preflight must refuse a four-field plant and not invent BioSTEAM runs."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea, tea_worker
from tea_preflight.preflight import (
    check,
    complete_public_twelve,
    preflight,
)

TRANSCRIPT = {
    "polymer": "LDPE",
    "solvent": "Dodecane",
    "temperature_c": 105.0,
    "solubility_pct": 14.54225715,
}
FOUR = {
    "target_polymer": "LDPE",
    "solvent": "Dodecane",
    "dissolution_temperature_c": 105.0,
    "dissolution_capacity": 14.54225715,
}
INTENT = dict(FOUR)


def _boom(*_args, **_kwargs):
    raise AssertionError("preflight must not start a BioSTEAM child")


@pytest.fixture(autouse=True)
def refuse_biosteam(monkeypatch):
    monkeypatch.setattr(tea, "_live", _boom)
    monkeypatch.setattr(tea, "_run", _boom)
    monkeypatch.setattr(tea, "_record_for_pair", _boom)
    monkeypatch.setattr(tea_worker, "run", _boom)


def test_transcript_dict_refuses_and_names_screening_vocabulary():
    report = check(INTENT, TRANSCRIPT)
    assert report.verdict == "REFUSE"
    assert report.unrecognised == ["polymer", "solubility_pct", "temperature_c"]
    assert report.screening_vocabulary == [
        "polymer", "solubility_pct", "temperature_c",
    ]
    assert "dissolution_capacity" in report.lost
    assert report.silent_defaults == {}
    assert report.runnable is False


def test_four_public_names_are_not_safe_to_run():
    report = check(INTENT, FOUR)
    assert report.verdict == "REFUSE"
    assert report.unrecognised == []
    assert report.lost == []
    assert report.sheet_missing == []
    assert "solvent_price_usd_per_kg" in report.evaluate_missing
    assert report.runnable is False
    assert report.run_error_code == "incomplete_process_config"
    assert report.serve_key_n is None


def test_silent_defaults_are_a_predicate_not_a_nine_name_watch_list():
    report = check(INTENT, FOUR)
    assert len(report.silent_defaults) == 40
    assert "dissolution_capacity" not in report.silent_defaults
    assert "lang_factor" not in report.silent_defaults
    watch = {
        "dissolution_capacity", "target_mass_percent",
        "processing_capacity_mt_per_yr", "labor_cost_usd_per_employee_yr",
        "energy_case", "precipitation_temperature_c", "solvent_loss_pct",
        "feedstock_distance_km", "feedstock_price_usd_per_kg",
    }
    unlisted = set(report.silent_defaults) - watch
    assert len(unlisted) == 32


def test_omitted_capacity_matching_the_default_is_a_lost_substitution():
    config = {
        "target_polymer": "LDPE",
        "solvent": "Dodecane",
        "dissolution_temperature_c": 105.0,
    }
    intent = {**config, "dissolution_capacity": 3.0}
    report = check(intent, config)
    assert report.verdict == "REFUSE"
    assert "dissolution_capacity" in report.lost
    assert "not named" in report.lost_reasons["dissolution_capacity"]
    assert report.silent_defaults["dissolution_capacity"] == 3.0


def test_internal_target_plastic_survives_on_a_complete_twelve():
    config = complete_public_twelve()
    config["target_plastic"] = config.pop("target_polymer")
    report = check({"target_plastic": "LDPE"}, config)
    assert "target_plastic" not in report.lost
    assert report.unrecognised == []
    assert report.runnable is True
    assert report.verdict == "SAFE TO RUN"


def test_seed_aliases_are_accepted_and_survive():
    config = complete_public_twelve()
    config["dissolution_temp_c"] = config.pop("dissolution_temperature_c")
    config["precipitation_temp_c"] = config.pop("precipitation_temperature_c")
    report = check(
        {
            "dissolution_temp_c": 105.0,
            "precipitation_temp_c": config["precipitation_temp_c"],
        },
        config,
    )
    assert report.unrecognised == []
    assert report.lost == []
    assert report.verdict == "SAFE TO RUN"


def test_scenario_allowed_extras_are_not_unrecognised():
    config = {
        **FOUR,
        "label": "probe",
        "lca_cfs": {"natural_gas_gwp": 1.0},
        "facilities": (),
        "turbogenerator": (),
    }
    report = check(INTENT, config)
    assert report.unrecognised == []
    assert report.verdict == "REFUSE"
    assert report.runnable is False


def test_lowercase_dodecane_canonicalizes():
    config = complete_public_twelve()
    config["solvent"] = "dodecane"
    report = check({"solvent": "Dodecane"}, config)
    assert report.lost == []
    assert report.verdict == "SAFE TO RUN"


def test_complete_twelve_is_safe_and_quotes_a_46_field_c1_key():
    config = complete_public_twelve()
    report = check(config, config)
    assert report.verdict == "SAFE TO RUN"
    assert report.runnable is True
    assert report.evaluate_missing == []
    assert report.serve_key_n == 46
    assert "solvent_price_usd_per_kg" in config
    assert config["solvent_price_usd_per_kg"] == 4.08


def test_conflicting_dual_keys_refuse():
    config = complete_public_twelve()
    config["target_plastic"] = "HDPE"
    report = check({"target_polymer": "LDPE"}, config)
    assert report.verdict == "REFUSE"
    assert report.collisions
    assert report.collisions[0]["public"] == "target_polymer"


def test_agreeing_dual_keys_are_not_a_collision():
    config = complete_public_twelve()
    config["target_plastic"] = "LDPE"
    report = check({"target_polymer": "LDPE"}, config)
    assert report.collisions == []
    assert report.verdict == "SAFE TO RUN"


def test_intent_matching_an_unnamed_coefficient_default_is_still_lost():
    config = complete_public_twelve()
    defaults = tea.first_run_sheet_defaults(energy_case="C1")
    report = check(
        {"labor_burden": defaults["labor_burden"]},
        config,
    )
    assert report.runnable is True
    assert "labor_burden" in report.lost
    assert report.verdict == "REFUSE"


def test_preflight_return_code_matches_verdict():
    assert preflight(INTENT, FOUR) == 1
    twelve = complete_public_twelve()
    assert preflight(twelve, twelve) == 0
