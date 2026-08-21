"""R3: explicit minimum_g_score=6.0 is caller-supplied, not the omitted default."""
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
        path = _ROOT / "audit" / "measure_g_floor_supplied.py"
        spec = importlib.util.spec_from_file_location(
            "measure_g_floor_supplied", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_r3_omitted_floor_stays_unsourced_default():
    omitted = _measure().supplied_vs_omitted()["omitted"]
    assert omitted["success"] is True
    assert omitted["minimum_g_score"] == 6.0
    assert omitted["minimum_g_score_source"] == "default"
    assert omitted["minimum_g_score_citation_status"] == "unsourced"
    assert omitted["warning_names_unsourced"] is True


def test_r3_explicit_six_is_user_requested():
    explicit = _measure().supplied_vs_omitted()["explicit_six"]
    assert explicit["success"] is True
    assert explicit["minimum_g_score"] == 6.0
    assert explicit["minimum_g_score_source"] == "user"
    assert explicit["minimum_g_score_citation_status"] == "user_requested"
    assert explicit["warning_names_unsourced"] is False
