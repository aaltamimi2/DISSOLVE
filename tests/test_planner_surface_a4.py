"""A4 accept tests: planner characterisation, greedy/window, precipitation/getter.

Table 2 mismatch is not a product FAIL. No BioSTEAM.
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
        path = _ROOT / "audit" / "measure_planner_surface.py"
        spec = importlib.util.spec_from_file_location("measure_planner_surface", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_a4_pa66_6_is_not_an_identity_and_the_paper_feed_refuses():
    ids = _measure().identities()
    seq = _measure().engine_sequence()
    assert ids["PA66_6_in_POLYMER_IDENTITIES"] is False
    assert ids["PA66_6_expand"] == []
    assert ids["PA66_6_identity"] is None
    assert ids["PA66_identity"] == "NYLON66"
    assert "ABS" not in ids["available_thermodynamic_polymers"]
    paper = seq["paper_like_feed_with_PA66_6"]
    assert paper["success"] is False
    assert paper["error_code"] == "unknown_polymer"
    assert paper["unsupported_polymers"] == ["PA66/6"]


def test_a4_engine_sequence_is_characterisation_not_table2():
    seq = _measure().engine_sequence()
    defaults = seq["defaults_observed"]
    assert defaults["branch_rule"] == "count"
    assert defaults["breadth"] == 1
    assert defaults["top_k_routes"] == 5
    assert defaults["subset_screens_evaluated"] == 26
    assert seq["complete"] is True
    assert seq["best_sequence"] == ["NYLON66", "PET", "PS", "LDPE", "PP"]
    assert seq["final_residue"] == "PP"
    paper_solvents = {
        str(step["solvent"]).casefold()
        for step in seq["paper_table2"]["sequence"]
    }
    engine_solvents = {
        str(name).casefold() for name in (seq["solvent_mapping"] or {}).values()
    }
    assert paper_solvents.isdisjoint(engine_solvents)


def test_a4_default_is_greedy_window_is_reachable_g_score_does_not_rerank():
    rule = _measure().selection_rule()
    assert rule["default"]["branch_rule"] == "count"
    assert rule["default"]["breadth"] == 1
    assert rule["default"]["top_k_routes"] == 5
    assert rule["window"]["reachable"] is True
    assert rule["all_token"]["stage_branch_token"] == "all"
    assert rule["score_uses_g_score"] is False
    assert rule["ranked_path_index_records_min_stage_g_score"] is True
    assert rule["rerank_by_g_score"] is False


def test_a4_degenerates_planner_refuses_unknown_empty_and_singleton():
    deg = _measure().degenerates()
    assert deg["empty_feed"]["error_code"] == "insufficient_feed_polymers"
    assert deg["singleton_feed"]["error_code"] == "insufficient_feed_polymers"
    assert deg["non_list_feed"]["error_code"] == "invalid_feed_polymers"
    absent = deg["absent_from_grid"]
    assert absent["planner"]["error_code"] == "unknown_polymer"
    assert absent["planner"]["unsupported_polymers"] == ["PMMA"]
    assert absent["scope"]["supported"] == ["LDPE"]
    assert absent["scope"]["hsp_fallback"] == ["PMMA"]
    assert deg["identical_solubility"]["hdpe_ldpe_equal_cells"]
    pair = deg["identical_solubility"]["planner_on_HDPE_LDPE"]
    assert pair["success"] is True
    assert pair["best_sequence"]


def test_a4_precipitation_and_getter_actually_execute():
    pg = _measure().precipitation_and_getter()
    precip = pg["precipitation"]
    assert precip["executed"] is True
    assert precip["serves_cloud_point_field"] is False
    assert precip["must_refuse_cloud_point"] is True
    assert "not a measured cloud point" in precip["warning"]
    getter = pg["getter_named_cycle"]
    assert getter["executed"] is True
    assert getter["serves_cloud_point_field"] is False
    same = pg["getter_recovered_equals_getter"]
    assert same["success"] is False
    assert same["reason"] == "recovered_and_getter_roles_must_be_distinct"
    omitted = pg["getter_omitted_searches_polymer_catalog"]
    assert omitted["success"] is True
    assert omitted["analysis_type"] == "cool_then_reheat_getter_discovery"
    assert omitted["getter_search_axis"] == "stored_grid_polymer_catalog"
    invented = pg["named_cycle_does_not_invent_solvent"]
    assert invented["success"] is False
    assert invented["error_code"] == "unsupported_process_cycle"
    assert invented["invented_solvent"] in (None, "None")
