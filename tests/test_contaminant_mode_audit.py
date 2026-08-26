"""v3 §7: unspecified-only is not a STRAP basis; the (B) pair still passes.

Does not embed washes. No tea._config_key. No BioSTEAM.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

_MEASURE = None


def _measure():
    global _MEASURE
    if _MEASURE is None:
        path = _ROOT / "audit" / "measure_contaminant_mode.py"
        spec = importlib.util.spec_from_file_location(
            "measure_contaminant_mode", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_leaching_and_strap_invert_the_polymer_requirement():
    item = _measure().inverted_screens()
    assert item["leaching_success"] is True
    assert item["strap_success"] is True
    assert item["leaching_mode"] == "leaching"
    assert item["strap_mode"] == "strap_contaminant_removal"
    assert item["pass_sets_differ"] is True


def test_b_pair_flips_passes_on_specified_rt():
    item = _measure().constructed_b_pair()
    assert item["fail_pet_evoh"]["success"] is True
    assert item["fail_pet_evoh"]["passes"] is False
    assert item["fail_pet_evoh"]["target_polymer_status"] == (
        "no_feasible_dissolution_precipitation_window"
    )
    assert item["pass_evoh"]["success"] is True
    assert item["pass_evoh"]["passes"] is True
    assert item["pass_evoh"]["operating_temperature_c"] == 105.0
    assert item["pass_evoh"]["precipitation_temperature_c"] == 60.0
    assert item["pass_evoh"]["contaminant_logd_min"] == 0.81
    assert item["pass_evoh"]["miscibility_regimes"] == ["rt", "rt"]
    assert item["pass_evoh"]["unspecified_not_a_strap_basis"] is False


def test_withdrawn_pfoa_case_refuses_unspecified_basis():
    item = _measure().withdrawn_pfoa_case()
    assert item["refuses"] is True
    assert item["error_code"] == "unspecified_not_a_strap_basis"
    assert item["toluene_pfoa_asked_rt"]["temperature_regime"] == "unspecified"


def test_asked_t_higher_may_return_specified_rt():
    item = _measure().regimes()
    assert item["histogram_256_256_832"] is True
    assert item["pfas_specified_rows"] == 0
    assert item["asked_t_higher_returns_rt"] is True


def test_threshold_split_and_thp_remain():
    thresholds = _measure().thresholds()
    assert thresholds["precipitation_is_paper"] is True
    assert thresholds["swell_dissolve_unsourced"] is True
    assert _measure().thp()["both_below_default_dissolution_min"] is True


def test_identity_and_refuse_codes_stay_distinct():
    item = _measure().identity_and_refusals()
    assert item["dehp_supported"] == [
        "di-(2-ethylhexyl) phthalate (DEHP)",
    ]
    assert item["ethylhexyl_unsupported"] == ["2-ethylhexyl"]
    assert item["bfr_error_code"] == "unsupported_contaminant_family"
    assert item["junk_error_code"] == "unsupported_contaminants"
    assert item["unknown_solvent_error_code"] == "unknown_contaminant_solvent"
    assert item["codes_are_distinct"] is True
