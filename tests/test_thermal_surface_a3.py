"""A3 accept tests: Tg snapshot identity, group-contribution Tm, Tm-vs-Tg.

Must-fire is unique identity names. The PE/HDPE collapse and
polyethylene→PS are surfaced, not blessed. No BioSTEAM.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import analysis
from dissolve.contracts import parse_tool_result


_MEASURE = None


def _measure():
    global _MEASURE
    if _MEASURE is None:
        path = _ROOT / "audit" / "measure_thermal_surface.py"
        spec = importlib.util.spec_from_file_location("measure_thermal_surface", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_a3_snapshot_population_and_dead_prediction_column():
    census = _measure().snapshot_census()
    assert census["snapshot_entries"] == 7736
    assert census["named_entries"] == 382
    assert census["tier3_tags_only"] == 7354
    assert census["both_tg_and_tg_predicted"] == 7736
    assert census["predicted_selected_while_measured_exists"] == 0
    assert census["asset_pin_match"] is True
    assert census["model_info"]["model_type"] == "polyBERT+MLP_ensemble"
    assert census["model_info"]["r_squared"] == 0.888


def test_a3_must_fire_unique_identity_names_not_tags_or_defects():
    table = _measure().identity_table()
    assert table["must_fire_failures"] == []
    assert table["must_fire_verified"] == table["must_fire_unique_identity_names"]
    assert table["must_fire_verified"] >= 300
    collisions = table["raw_unique_but_lookup_key_collides"]
    assert {row["query"] for row in collisions} >= {
        "poly(dimethylsiloxane)", "Polypropylene", "PNIPAAM",
    }
    assert any(row["query"] == "polyester" for row in table["unique_name_that_is_also_a_tag"])
    excluded = set(table["must_fire_excluded_from_blessing"])
    assert excluded == {"PE", "HDPE", "LDPE", "UHMWPE", "LLDPE", "polyethylene"}
    fired = {row["query"] for row in table["must_fire_short_identities"]}
    assert fired == {"PDMS", "PET", "PEG", "PEI", "PES", "PIB"}
    for row in table["must_fire_short_identities"]:
        assert row["fired"] is True
        assert row["actual_match_type"] == "exact_name_or_tag"


def test_a3_must_refuse_pe_cross_identity_and_surface_the_collapse():
    table = _measure().identity_table()
    refuse = next(row for row in table["table"] if row["query"] == "PE")
    collapse = next(
        row for row in table["table"]
        if row["class"] == "snapshot_defect_collapse_not_must_fire"
    )
    assert refuse["class"] == "must_refuse_cross_identity"
    assert refuse["primary_is_pet_peg_pei_or_pes"] is False
    assert refuse["actual_primary_psmiles"] == "[*]CC[*]"
    assert "PE" in refuse["actual_primary_names"]
    assert refuse["actual_primary_tg_k"] == 248.2219178
    extras = refuse["substring_extras_in_full_candidate_set"]
    assert extras == {"PET": True, "PEG": True, "PEI": True, "PES": True}
    assert collapse["shared_psmiles"] == "[*]CC[*]"
    assert set(collapse["each_name_primary_psmiles"].values()) == {"[*]CC[*]"}


def test_a3_polyethylene_to_ps_is_a_snapshot_defect_not_a_must_fire():
    table = _measure().identity_table()
    defect = next(row for row in table["table"] if row["query"] == "polyethylene")
    assert defect["class"] == "snapshot_defect_not_must_fire"
    assert defect["product_currently_serves_ps"] is True
    assert defect["actual_primary_psmiles"] == "[*]C(C[*])C1=CC=CC=C1"
    assert "PS" in defect["actual_primary_names"]
    assert defect["actual_match_type"] == "exact_name_or_tag"
    assert "polyethylene" not in {
        row["query"] for row in table["must_fire_short_identities"]
    }


def test_a3_group_contribution_over_the_7736_and_one_tm_on_pe_psmiles():
    census = _measure().group_contribution_census()
    assert census["population"] == 7736
    assert census["estimate_success"] == 7736
    assert census["tm_k_returned_when_reliable_false"] > 0
    assert census["reliable_within_group_method_true"] == 2600
    collapse = census["one_tm_on_star_CC_star"]
    assert collapse["psmiles"] == "[*]CC[*]"
    assert collapse["names_on_this_row"] == ["PE", "HDPE", "LDPE", "UHMWPE", "LLDPE"]
    assert collapse["tm_k"] == 404.04040404040404
    assert collapse["reliable_within_group_method"] is True
    assert census["same_tm_values"] is True
    names = {row["polymer_name"] for row in census["same_tm_under_five_resin_names"]}
    assert names == {"PE", "HDPE", "LDPE", "UHMWPE", "LLDPE"}


def test_a3_tm_versus_tg_is_labeled_on_tools_and_predicted_column_is_dead():
    report = _measure().distinction_report()
    lookup = report["lookup_glass_transition"]
    estimate = report["estimate_thermal_properties"]
    inventory = report["list_thermal_evidence"]
    assert lookup["display_names_tg"] is True
    assert lookup["display_names_tm"] is False
    assert lookup["runtime_model_loaded"] is False
    assert lookup["selected_equals_measured_on_PDMS"] is True
    assert lookup["predicted_column_present_on_match"] is True
    assert estimate["display_names_tm"] is True
    assert estimate["display_names_tg"] is False
    assert inventory["names_tg_snapshot"] is True
    assert inventory["names_van_krevelen"] is True
    vk = next(
        row for row in inventory["rows"]
        if row["capability"] == "Van Krevelen group contribution"
    )
    assert vk["available"] is False
    assert "not offered to the agent" in vk["basis"]
    tg = next(
        row for row in inventory["rows"]
        if row["capability"] == "Tg snapshot lookup"
    )
    assert tg["available"] is True
    assert "7736" in tg["basis"]
    assert len(inventory["rows"]) == 4
    assert inventory["polyBERT_residual_available"] is False
    assert inventory["states_tm_is_not_tg"] is False
    assert report["dead_Tg_K_predicted"]["never_selected_while_measured_exists"] is True
    payload = _data(analysis.lookup_glass_transition("LDPE"))
    assert payload["runtime_model_loaded"] is False
    match = payload["matches"][0]
    assert match["tg_k"] == match["measured_tg_k"]
    assert match["predicted_tg_k"] is not None
    assert match["tg_k"] != match["predicted_tg_k"]
