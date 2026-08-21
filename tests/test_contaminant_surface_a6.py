"""A6 accept tests: unsourced 1/10/1, absent-case split, input diff.

Does not call tea._config_key. No BioSTEAM.
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
        path = _ROOT / "audit" / "measure_contaminant_surface.py"
        spec = importlib.util.spec_from_file_location(
            "measure_contaminant_surface", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_a6_default_thresholds_are_unsourced_1_10_1():
    item = _measure().thresholds()
    assert item["citation_on_function"] is False
    assert item["status"] == "unsourced"
    assert item["spec_1_10_1"] == {
        "swelling_min_wt_pct": 1.0,
        "dissolution_min_wt_pct": 10.0,
        "precipitation_threshold_wt_pct": 1.0,
    }
    assert item["defaults_with_precipitation"]["precipitation_threshold_wt_pct"] == 1.0
    assert item["defaults_with_precipitation"]["dissolution_min_wt_pct"] == 10.0
    assert item["success_path_provenance"]["source_dataset"] == (
        "Zhou workbook miscibility and logD"
    )
    assert item["success_path_provenance"]["evidence_class"] == "screening_proxy"
    assert item["success_path_provenance_is_not_the_threshold_basis"] is True
    assert item["duckdb"]["match"] is True


def test_a6_all_unsupported_refuses_known_plus_unknown_continues():
    split = _measure().absent_split()
    assert split["all_unsupported"]["refuses"] is True
    assert split["all_unsupported"]["error_code"] == "unsupported_contaminants"
    assert split["all_unsupported_strap"]["refuses"] is True
    mixed = split["one_known_one_unknown"]
    assert mixed["continues"] is True
    assert mixed["supported_contaminants"] == ["Perfluorooctanoic Acid"]
    assert mixed["unsupported_contaminants"] == ["xyzzy-not-a-contaminant"]
    assert mixed["lists_unknown"] is True
    assert split["absent_refuses_rather_than_defaults_only_on_all_unsupported"] is True


def test_a6_removal_modes_differ_only_by_precipitation_membership():
    diff = _measure().input_diff()
    assert diff["only_precipitation_membership_differs"] is True
    assert diff["non_precipitation_diffs"] == []
    assert diff["only_in_leaching"] == {}
    assert "precipitation_threshold_wt_pct" in diff["only_in_strap"]
    assert diff["tea_config_key_calls"] == []
    compare = _measure().comparison()
    assert compare["success"] is True
    assert all(compare["shared_served_fields_equal"].values())
    assert compare["leaching_has_precipitation_threshold"] is False
    assert compare["strap_only_served_threshold_fields"][
        "precipitation_threshold_wt_pct"
    ] == 1.0
