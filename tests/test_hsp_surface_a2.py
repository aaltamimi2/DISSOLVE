"""A2 accept tests: HSP surface, RF mutation, leakage, call sites, pins.

No BioSTEAM. Does not treat §2 numbers as expected values — remesures the pin.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import analysis, separation
from dissolve.contracts import parse_tool_result


def _measure():
    path = _ROOT / "audit" / "measure_hsp_surface.py"
    spec = importlib.util.spec_from_file_location("measure_hsp_surface", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def test_a2_asset_pins_match_disk_and_timestamp_gap_is_unexplained():
    report = _measure().pin_report()
    assert report["all_pins_match"] is True
    assert report["stem"] == "corrected_Random_Forest_20251231_212903"
    assert report["metadata_timestamp"] == "20260124_145027"
    assert report["timestamp_discrepancy"] == "unexplained"
    assert report["metadata_target"] == "RED < 1.0 = Soluble"
    assert report["metadata_threshold"] == 0.85
    assert report["metadata_performance"] == {
        "test_accuracy": 1.0,
        "test_precision": 1.0,
        "test_recall": 1.0,
        "test_f1": 1.0,
        "test_roc_auc": 1.0,
    }


def test_a2_forest_is_not_a_red_only_stump():
    report = _measure().forest_report()
    assert report["n_estimators"] == 100
    assert report["red_only_stumps"] == 0
    assert report["trees_that_split_only_on_red"] == 0
    assert report["tree0"]["node_count"] == 171
    assert report["tree0"]["max_depth"] == 12
    assert report["gini_importance"]["RED"] > report["gini_importance"]["Ra"]
    assert report["gini_importance"]["Ra"] > report["gini_importance"]["R0"]
    assert report["molar_volume_gini"] == 0.0
    assert report["hsp_component_gini_sum"] < 0.1


def test_a2_lookup_and_screen_never_call_the_forest():
    lookup = json.dumps(_data(analysis.lookup_hansen_parameters(["PMMA"], "polymer")))
    screen = json.dumps(_data(analysis.screen_hansen_compatibility(
        ["PMMA"], ["Toluene", "THF"],
    )))
    assert "hsp_ml_prediction" not in lookup
    assert "hsp_ml_prediction" not in screen
    assert "predicted_class" not in lookup
    assert "predicted_class" not in screen


def test_a2_planner_refuses_unknown_polymer_before_fallback():
    payload = _data(separation.plan_multistage_separation(["PMMA", "LDPE"]))
    assert payload["success"] is False
    assert payload["error_code"] == "unknown_polymer"
    assert payload["unsupported_polymers"] == ["PMMA"]
    assert "hsp_ml_prediction" not in json.dumps(payload)


def test_a2_scope_uses_fallback_only_off_grid_and_quotes_the_red_target():
    payload = _data(separation.resolve_polymer_data_scope(["PMMA", "LDPE"]))
    assert payload["supported_requested_polymers"] == ["LDPE"]
    assert payload["hsp_fallback_requested_polymers"] == ["PMMA"]
    paths = {row["requested_polymer"]: row["evidence_path"] for row in payload["results"]}
    assert paths["LDPE"] == "thermodynamic"
    assert paths["PMMA"] == "hsp_fallback"
    assert _measure()._SCOPE_WARNING in payload["warnings"]
    assert payload["model_basis"] == _measure()._MODEL_BASIS


def test_a2_omit_rf_is_payload_decoration_not_a_decision():
    report = _measure().mutation_report()
    assert report["unused_pin_on_lookup_and_screen"] is True
    assert report["decision_dependence"] is False
    assert report["classification"] == "payload_decoration"
    assert report["omit_changed_all_under_hsp_ml_prediction"] is True
    assert report["omit_changed_field_count"] > 0
    assert report["baseline"]["plan_error_code"] == "unknown_polymer"
    assert report["baseline"]["scope_supported"] == ["LDPE"]
    assert report["baseline"]["scope_fallback"] == ["PMMA"]
    assert report["raise_disable"]["lookup"] == "unchanged_no_exception"
    assert report["raise_disable"]["screen"] == "unchanged_no_exception"
    assert report["raise_disable"]["plan"] == "unchanged_no_exception"
    assert report["raise_disable"]["fallback_no_solvent"] == "unchanged_no_exception"
    assert report["raise_disable"]["fallback_ldpe_with_solvent"] == "unchanged_no_exception"
    assert report["raise_disable"]["fallback_with_solvent"] == "exception:A2 RF disabled"
    assert report["raise_disable"]["scope_with_solvent"] == "exception:A2 RF disabled"


def test_a2_leakage_disagreement_and_residual_are_measured_not_assumed_zero_skill():
    report = _measure().leakage_report()
    disagree = report["disagreement"]
    residual = report["residual_capacity"]
    assert report["population"]["scored_pairs"] > 0
    assert disagree["predicted_class_vs_RED_lt_1"] == 0
    assert disagree["predicted_class_vs_RED_le_1"] == 0
    assert disagree["fallback_rows_disagreeing_with_either_RED_rule"] == 0
    assert residual["not_solubility_skill"] is True
    assert residual["zero_R0_Ra_RED_remaining_accuracy_vs_RED_lt_1"] < 1.0
    assert residual["zero_R0_Ra_RED_predicted_soluble"] == report["population"]["scored_pairs"]


def test_a2_user_facing_accuracy_quotes_include_the_existing_caveats():
    sites = _measure().call_sites_report()
    served = sites["served_on_fallback_rows"]
    assert served["validation_caveat"] == _measure()._VALIDATION_CAVEAT
    assert served["perfect_test_metrics_reported"] is True
    assert served["target_definition"] == "RED < 1.0 = Soluble"
    assert served["reported_test_metrics"]["test_accuracy"] == 1.0
    texts = [item["text"] for item in sites["verbatim"]]
    assert _measure()._VALIDATION_CAVEAT in texts
    assert _measure()._SCOPE_WARNING in texts
    assert _measure()._MODEL_BASIS in texts
    assert sites["lookup_and_screen_do_not_quote_model_accuracy"] is True
