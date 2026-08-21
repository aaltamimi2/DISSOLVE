"""Finding 30: stored cache records are not the 44/46 serve key.

No live BioSTEAM. Reconstructed defaults are not stored values.
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
        path = _ROOT / "audit" / "measure_cache_stored_basis.py"
        spec = importlib.util.spec_from_file_location(
            "measure_cache_stored_basis", path,
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_f30_every_cache_record_stores_twelve_and_keys_44_or_46():
    item = _measure().census()
    assert item["record_count"] == 24
    assert item["all_incomplete"] is True
    assert item["stored_field_counts"] == [12]
    assert item["serve_key_field_counts"] == [44, 46]
    for row in item["rows"]:
        assert row["reconstructed_defaults_are_not_stored"] is True
        assert row["missing_from_stored_record"]
        expected = 44 if row["energy_case"] == "C2" else 46
        assert row["serve_key_field_count"] == expected
        assert row["expected_public_basis_count"] == expected


def test_f30_lookup_names_incomplete_stored_basis():
    lookup = _measure().lookup_stamp()
    assert lookup["success"] is True
    assert lookup["cache_stored_basis_status"] == "incomplete_stored_basis"
    assert lookup["records_with_incomplete_stored_basis"] == lookup["record_count"]
    assert lookup["first_status"] == "incomplete_stored_basis"
    assert lookup["first_stored_field_count"] == 12
    assert lookup["first_serve_key_field_count"] in {44, 46}
    assert lookup["first_reconstructed_defaults_are_not_stored"] is True
    assert lookup["warning_names_twelve"] is True
