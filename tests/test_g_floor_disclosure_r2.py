"""R2: default G-score floor 6.0 is served and labelled unsourced.

Does not invent a citation.
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
        path = _ROOT / "audit" / "measure_g_floor_disclosure.py"
        spec = importlib.util.spec_from_file_location(
            "measure_g_floor_disclosure", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_r2_default_g_floor_is_labelled_unsourced():
    default = _measure().served_floors()["default"]
    assert default["success"] is True
    assert default["minimum_g_score"] == 6.0
    assert default["minimum_g_score_source"] == "default"
    assert default["minimum_g_score_citation_status"] == "unsourced"
    assert default["warning_names_unsourced"] is True


def test_r2_user_floor_is_not_the_unsourced_default():
    user = _measure().served_floors()["user_requested"]
    assert user["success"] is True
    assert user["minimum_g_score"] == 8.0
    assert user["minimum_g_score_source"] == "user"
    assert user["minimum_g_score_citation_status"] == "user_requested"
    assert user["warning_names_unsourced"] is False


def test_r2_scope_gap_still_names_the_unsourced_default():
    gap = _measure().served_floors()["scope_gap"]
    assert gap["success"] is True
    assert gap["analysis_type"] == "green_first_solvent_scope_gap"
    assert gap["minimum_g_score"] == 6.0
    assert gap["minimum_g_score_citation_status"] == "unsourced"
    assert gap["warning_names_unsourced"] is True
