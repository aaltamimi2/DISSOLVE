"""A5 accept tests: safety provenance and four registry tools.

Missing-solvent success is observation, not a refuse policy.
No live PubChem. No BioSTEAM.
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
        path = _ROOT / "audit" / "measure_safety_surface.py"
        spec = importlib.util.spec_from_file_location("measure_safety_surface", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_a5_two_function_sets_are_not_mixed():
    inv = _measure().provenance_inventory()
    twelve = inv["twelve_non_underscore_functions"]
    four = inv["four_registry_tools"]
    assert twelve == [
        "build_safety_profile",
        "condition_operability",
        "typed_solvent_safety_evidence",
        "attach_lower_hazard_disclosure",
        "merge_condition_operability",
        "recommended_condition_operability",
        "format_safety_card",
        "format_safety_comparison",
        "get_solvent_safety_card",
        "compare_solvent_safety_at_conditions",
        "screen_green_solvent_candidates",
        "screen_route_solvent_substitutions",
    ]
    assert four == [
        "get_solvent_safety_card",
        "compare_solvent_safety_at_conditions",
        "screen_green_solvent_candidates",
        "screen_route_solvent_substitutions",
    ]
    assert set(four).issubset(set(twelve))


def test_a5_gsk_and_greensolventdb_are_distinct_and_g_floor_is_unsourced():
    inv = _measure().provenance_inventory()
    gsk = inv["g_score"]["gsk_example"]
    green = inv["g_score"]["green_example"]
    assert gsk["source"] == "GSK_dataset.csv"
    assert gsk["ml_predicted"] is False
    assert gsk["table"] == "gsk_safety"
    assert green["source"] == "GreenSolventDB_10k.csv"
    assert green["ml_predicted"] is True
    assert green["table"] == "green_solvent"
    floor = inv["minimum_g_score_floor"]
    assert floor["default"] == 6.0
    assert floor["cited_in_function"] is False
    assert floor["status"] == "unsourced"
    db = inv["safety_duckdb"]
    assert db["match"] is True
    assert "v11-inherited" in db["v11_inheritance"]
    assert db["safety_metadata"]["source_commit"] == "4b7b513"
    assert inv["held_pubchem_pin"]["wired_into_safety_py"] is False


def test_a5_absent_solvent_is_observation_not_a_refuse():
    absent = _measure().absent_solvent()
    assert absent["local_properties"] == {"name": "xyzzy-not-a-solvent"}
    assert absent["card"]["success"] is True
    assert absent["card"]["must_refuse"] is False
    assert "data_gaps" in absent["card"]
    assert "g_score" in absent["card"]["data_gaps"]
    green = absent["green_screen"]
    assert green["success"] is True
    assert green["minimum_g_score"] == 6.0
    assert green["excluded_missing_g_score_count"] > 0
    sources = {row["g_score_source"] for row in green["ranked"]}
    predicted = {row["g_score_is_ml_predicted"] for row in green["ranked"]}
    assert "GSK_dataset.csv" in sources
    assert "GreenSolventDB_10k.csv" in sources
    assert predicted == {False, True}


def test_a5_lower_hazard_disclosure_binds_to_the_named_solvent():
    bind = _measure().lower_hazard_bind()
    assert bind["recommendation_solvent"] == "Toluene"
    assert bind["binds"] is True
    assert bind["alternatives_are_warning_only"] is True
    assert bind["recommendation_not_in_alternatives"] is True
    assert bind["alternative_solvents"] == ["Isobutyl Isobutyrate", "Octane"]


def test_a5_missing_flash_is_incomplete_known_flash_is_stated():
    flash = _measure().flash_condition()
    missing = flash["offline_missing_flash"]
    assert missing["include_pubchem"] is False
    assert missing["flash_point_c"] is None
    assert missing["operating_at_or_above_flash_point"] is None
    assert missing["heating_risk_level"] == "incomplete"
    assert missing["all_at_or_above_flash_point"] is None
    assert missing["silent_pass"] is False
    known = flash["held_pin_known_flash"]
    assert known["says_above_flash_when_known"] is True
    assert known["operating_at_or_above_flash_point"] is True
    assert known["flash_point_c"] is not None
    assert known["flash_point_c"] < 25
    assert _measure().registry_offline()["get_solvent_safety_card"]["success"] is True
    assert _measure().registry_offline()["screen_route_solvent_substitutions"]["success"] is True
