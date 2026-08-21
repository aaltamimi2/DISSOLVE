"""A7 accept tests: analyze_numeric_samples only.

Independent of HSP and Tg. SciPy absence is not a defect.
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
        path = _ROOT / "audit" / "measure_numeric_samples.py"
        spec = importlib.util.spec_from_file_location("measure_numeric_samples", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _MEASURE = module
    return _MEASURE


def test_a7_must_serve_summary_and_correlation_separately():
    served = _measure().must_serve()
    summary = served["summary"]
    correlation = served["correlation"]
    assert served["independent_calls"] is True
    assert summary["success"] is True
    assert summary["analysis_type"] == "summary"
    assert summary["sample_source"] == "explicit_user_supplied_numeric_series"
    assert summary["series_names"] == ["temp_c", "solubility"]
    assert correlation["success"] is True
    assert correlation["analysis_type"] == "correlation"
    assert correlation["x_name"] == "temp_c"
    assert correlation["y_name"] == "solubility"
    assert correlation["n_pairs"] == 4
    assert correlation["coefficient"] == 1.0


def test_a7_must_refuse_nonfinite_boolean_short_unpaired_and_too_many():
    refused = _measure().must_refuse()
    assert refused["max_numeric_series"] == 20
    assert refused["non_finite_nan"]["success"] is False
    assert refused["non_finite_nan"]["error_code"] == "invalid_statistical_input"
    assert "non-finite" in (refused["non_finite_nan"]["error"] or "")
    assert refused["non_finite_inf"]["success"] is False
    assert refused["boolean_in_series"]["success"] is False
    assert "non-numeric" in (refused["boolean_in_series"]["error"] or "")
    assert refused["n_lt_2"]["success"] is False
    assert refused["correlation_unpaired_lengths"]["success"] is False
    assert refused["regression_unpaired_lengths"]["success"] is False
    assert refused["more_than_max_series"]["success"] is False


def test_a7_refuses_to_treat_series_names_as_sql_and_does_not_open_hsp():
    names = _measure().names_are_not_sql()
    assert names["success"] is True
    assert names["served_as_label"] is True
    assert names["treated_as_sql"] is False
    assert names["asset_payload_calls"] == 0
    assert names["duckdb_connect_calls"] == []
    scipy = _measure().scipy_status()
    assert "scipy_present" in scipy
    assert scipy["degradation_is_not_a_defect"] is True
