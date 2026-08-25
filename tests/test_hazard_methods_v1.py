"""HAZARD_METHODS_SPEC.v1: published Methods text, not a score rewrite.

No safety.py edit. No duckdb edit. No live PubChem. No BioSTEAM.
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
        path = _ROOT / "audit" / "measure_hazard_methods.py"
        spec = importlib.util.spec_from_file_location(
            "measure_hazard_methods", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_named_universe_is_get_available_solvents_all_990():
    census = _measure().roster_census()
    bindings = _measure().published_bindings()
    assert census["enumerating_function"] == "get_available_solvents"
    assert census["scope_token"] == "all"
    assert census["scope_origin"] == "built_in"
    assert census["n"] == 990
    assert census["served_gsk"] + census["served_green"] + census["served_neither"] == 990
    assert census["served_gsk"] == 130
    assert census["served_green"] == 840
    assert census["served_neither"] == 20
    assert bindings["names_enumerating_function"] is True
    assert bindings["names_scope_all"] is True


def test_identity_leftover_is_the_two_gsk_only_keys():
    census = _measure().roster_census()
    bindings = _measure().published_bindings()
    keys = [row["interp_key"] for row in census["gsk_only"]]
    cas = [row["cas"] for row in census["gsk_only"]]
    assert keys == ["1,2-dimethoxyethane", "cis-decalin"]
    assert cas == ["110-71-4", "493-01-6"]
    assert census["served_gsk"] - census["both_table_hits"] == 2
    assert bindings["leftover_keys_in_text"] is True
    assert bindings["leftover_cas_in_text"] is True
    assert bindings["census_leftover_keys"] == [
        "1,2-dimethoxyethane", "cis-decalin",
    ]


def test_average_is_mae_0_30_not_signed_mean_and_green_has_no_order_by():
    census = _measure().roster_census()
    bindings = _measure().published_bindings()
    sql = _measure().gscore_sql_shape()
    assert census["both_table_hits"] == 128
    assert census["mae_2dp"] == 0.30
    assert census["signed_mean_2dp"] == 0.01
    assert census["mae_2dp"] != census["signed_mean_2dp"]
    assert bindings["names_mae"] is True
    assert bindings["names_signed_mean"] is True
    assert bindings["names_limit_1"] is True
    assert bindings["names_no_order_by"] is True
    assert sql["has_limit_1"] is True
    assert sql["has_order_by"] is False
    assert sql["gsk_first"] is True


def test_published_methods_does_not_reintroduce_the_false_partition():
    bindings = _measure().published_bindings()
    assert bindings["forbidden_claim_hits"] == []


def test_green_screen_is_offline_and_route_cached_path_skips_live_pubchem():
    net = _measure().network_by_tool()
    bindings = _measure().published_bindings()
    assert net["green_screen_completes_with_pubchem_raising"] is True
    assert net["route_default_raises"] is False
    assert net["route_default_completes"] is True
    assert net["route_include_pubchem_false_completes"] is True
    assert bindings["names_green_screen"] is True
    assert bindings["names_route_screen"] is True
    assert bindings["names_include_pubchem"] is True


def test_failed_headings_are_provenance_not_a_data_gaps_key():
    bindings = _measure().published_bindings()
    text = _measure().published_text()
    assert bindings["names_failed_headings"] is True
    assert "not a distinct `data_gaps` key" in text
    assert "card text does not distinguish them" in text


def test_unsourced_default_g_floor_is_named_and_still_unsourced():
    floor = _measure().served_floor()
    census = _measure().roster_census()
    bindings = _measure().published_bindings()
    assert census["default_minimum_g_score"] == 6.0
    assert floor["minimum_g_score"] == 6.0
    assert floor["minimum_g_score_source"] == "default"
    assert floor["minimum_g_score_citation_status"] == "unsourced"
    assert bindings["names_unsourced_floor"] is True


def test_duckdb_pin_holds_and_is_not_rewritten_to_the_draft_counts():
    census = _measure().roster_census()
    assert census["duckdb_sha256"] == census["duckdb_pin"]
    assert census["duckdb_pin"].startswith("88ce0d09")
    assert census["served_gsk"] != 102
    assert census["served_green"] != 452
    assert census["served_neither"] != 8
    assert census["both_table_hits"] != 100
    assert census["mae_2dp"] != 0.28
