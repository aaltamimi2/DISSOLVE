"""R1 plus fa34f42 leftover: precip 1 wt% is paper; 1/10 stay unsourced.

Does not invent a 10 wt% citation. Zhou workbook provenance is still
not the swelling or dissolution threshold basis.
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
        path = _ROOT / "audit" / "measure_threshold_disclosure.py"
        spec = importlib.util.spec_from_file_location(
            "measure_threshold_disclosure", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_r1_default_leaching_serves_unsourced_1_10_1():
    leaching = _measure().served_defaults()["leaching"]
    assert leaching["success"] is True
    assert leaching["swelling_min_wt_pct"] == 1.0
    assert leaching["swelling_max_wt_pct"] == 10.0
    assert leaching["dissolution_min_wt_pct"] == 10.0
    assert leaching["threshold_basis"] == "default_proxy"
    assert leaching["threshold_citation_status"] == "unsourced"
    assert leaching["warning_names_unsourced"] is True
    assert leaching["provenance_source_dataset"] == (
        "Zhou workbook miscibility and logD"
    )


def test_r1_strap_serves_precipitation_as_paper_and_1_10_as_unsourced():
    served = _measure().served_defaults()
    strap = served["strap"]
    compare = served["compare"]
    leach = served["leaching"]
    assert strap["precipitation_threshold_wt_pct"] == 1.0
    assert strap["threshold_sources"]["precipitation_threshold_wt_pct"] == "paper"
    assert strap["threshold_citations"]["precipitation_threshold_wt_pct"] == (
        "zhou_green_chem_2026"
    )
    assert strap["threshold_citation_status"] == "unsourced"
    assert strap["warning_names_unsourced"] is True
    assert strap["warning_names_paper_precipitation"] is True
    assert leach["warning_names_paper_precipitation"] is False
    assert compare["dissolution_min_wt_pct"] == 10.0
    assert compare["precipitation_threshold_wt_pct"] == 1.0
    assert compare["threshold_sources"]["precipitation_threshold_wt_pct"] == "paper"
    assert compare["threshold_citation_status"] == "unsourced"
    assert served["nested_compare_leaching_status"] == "unsourced"


def test_r1_user_requested_thresholds_are_not_labelled_unsourced():
    user = _measure().served_defaults()["user_requested_leaching"]
    assert user["success"] is True
    assert user["swelling_min_wt_pct"] == 2.0
    assert user["threshold_basis"] == "user_requested"
    assert user["threshold_citation_status"] == "user_requested"
    assert user["warning_names_unsourced"] is False
