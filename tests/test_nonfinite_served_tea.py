"""Refuse a non-finite served TEA metric instead of success-with-null.

Bound to the metric property, not to an input such as 100 wt%. Homogeneous
evaluate all-fail of a classified type is that type on the envelope, not
the ``no_simulation_result`` catch-all. No live BioSTEAM. The nan-cashflow /
leftover-mass physics question stays open.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import tea, tea_worker
from dissolve.session import bind_tool_session, new_session

from test_evaluate_process import (
    _ROUTE_CAPACITY,
    _ROUTE_ENERGY,
    _ROUTE_PRECIP,
    _data,
    _exact_planner_route,
    _forbid_live,
    _public_from_record,
    _record_with_energy,
    _store_planner_route_handle,
)


def _plant_success_with_null_msp(
    monkeypatch,
    *,
    error=None,
    poison=None,
    msp=None,
):
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        if poison is not None and not poison(config, result):
            return result
        planted_result = dict(result)
        tea_block = dict(result.get("tea") or {})
        tea_block["msp_usd_per_kg"] = msp
        planted_result["tea"] = tea_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        if error is not None:
            planted_result["error"] = error
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)


def _plant_success_with_null_gwp(monkeypatch, *, poison=None):
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        if poison is not None and not poison(config, result):
            return result
        planted_result = dict(result)
        lca_block = dict(result.get("lca") or {})
        lca_block["gwp_kg_co2e_per_kg"] = None
        planted_result["lca"] = lca_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)


def _two_c1_records():
    c1 = _record_with_energy("C1")
    sibling = next(
        item for item in tea._records()
        if str(item["config"].get("energy_case") or "").upper() == "C1"
        and item["config"]["target_plastic"] != c1["config"]["target_plastic"]
    )
    return c1, sibling


def test_served_tea_value_keeps_cashflow_runtime_error():
    def boom():
        raise RuntimeError("nan encountered in cashflow array")

    value, exc_name, context = tea_worker._served_tea_value(boom)
    assert value is None
    assert exc_name == "RuntimeError"
    assert "nan encountered in cashflow array" in context
    refusal = tea_worker._nonfinite_served_tea_refusal(
        ["msp_usd_per_kg"],
        [f"{exc_name}: {context}"],
    )
    assert refusal["error_type"] == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in refusal["error"]
    assert refusal["nonfinite_served_metrics"] == ["msp_usd_per_kg"]


def test_served_tea_value_rejects_missing_and_nonfinite():
    for raw in (None, float("nan"), float("inf"), float("-inf"), "not-a-number"):
        value, _exc_name, context = tea_worker._served_tea_value(raw)
        assert value is None
        assert context
    finite, exc_name, context = tea_worker._served_tea_value(1.25)
    assert finite == 1.25
    assert exc_name is None
    assert context is None


def test_safe_still_swallows_for_equipment():
    assert tea_worker._safe(lambda: 1.5) == 1.5
    assert tea_worker._safe(lambda: float("nan")) is None
    assert tea_worker._safe(
        lambda: (_ for _ in ()).throw(RuntimeError("equipment blew up")),
    ) is None


def test_nonfinite_tci_without_msp_is_tea_cashflow_undefined():
    refusal = tea_worker._nonfinite_served_tea_refusal(
        ["tci_usd", "aoc_usd_per_yr"],
        ["served metric was None"],
    )
    assert refusal["error_type"] == "tea_cashflow_undefined"


def test_coerce_binds_to_the_metric_not_the_input_mass_percent():
    missing = tea._coerce_nonfinite_served_tea({
        "success": True,
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": 1.0,
            "aoc_usd_per_yr": 1.0,
        },
        "config": {"target_plastic_percent": 55.0},
    })
    assert missing["success"] is False
    assert missing["error_type"] == "no_finite_msp"
    assert missing["nonfinite_served_metrics"] == ["msp_usd_per_kg"]

    cashflow = tea._coerce_nonfinite_served_tea({
        "success": True,
        "error": "RuntimeError: nan encountered in cashflow array",
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": None,
            "aoc_usd_per_yr": None,
        },
        "config": {"target_plastic_percent": 55.0},
    })
    assert cashflow["success"] is False
    assert cashflow["error_type"] == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in cashflow["error"]

    finite_at_hundred = tea._coerce_nonfinite_served_tea({
        "success": True,
        "tea": {
            "msp_usd_per_kg": 1.2,
            "tci_usd": 3.0,
            "aoc_usd_per_yr": 4.0,
        },
        "config": {"target_plastic_percent": 100.0},
    })
    assert finite_at_hundred["success"] is True
    already = {"success": False, "error_type": "cache_miss"}
    assert tea._coerce_nonfinite_served_tea(already) is already
    assert tea._coerce_nonfinite_served_gwp(already) is already

    gwp_missing = tea._coerce_nonfinite_served_gwp({
        "success": True,
        "tea": {
            "msp_usd_per_kg": 1.2,
            "tci_usd": 3.0,
            "aoc_usd_per_yr": 4.0,
        },
        "lca": {"gwp_kg_co2e_per_kg": None},
    })
    assert gwp_missing["success"] is False
    assert gwp_missing["error_type"] == "no_finite_gwp"
    assert gwp_missing["nonfinite_served_metrics"] == ["gwp_kg_co2e_per_kg"]

    tea_first = tea._coerce_nonfinite_served_gwp(
        tea._coerce_nonfinite_served_tea({
            "success": True,
            "tea": {
                "msp_usd_per_kg": None,
                "tci_usd": 1.0,
                "aoc_usd_per_yr": 1.0,
            },
            "lca": {"gwp_kg_co2e_per_kg": None},
        })
    )
    assert tea_first["error_type"] == "no_finite_msp"


def test_all_fail_error_code_promotes_only_homogeneous_classified():
    assert tea._all_fail_error_code(
        [{"error_type": "tea_cashflow_undefined"}]
    ) == "tea_cashflow_undefined"
    assert tea._all_fail_error_code(
        [{"error_type": "no_finite_msp"}]
    ) == "no_finite_msp"
    assert tea._all_fail_error_code(
        [{"error_type": "no_finite_gwp"}]
    ) == "no_finite_gwp"
    assert tea._all_fail_error_code(
        [{"error_type": "priced_solvent_unmodellable"}]
    ) == "priced_solvent_unmodellable"
    assert tea._all_fail_error_code(
        [{"error_type": "cache_flowsheet_mismatch"}]
    ) == "cache_flowsheet_mismatch"
    assert tea._all_fail_error_code(
        [{"error_type": "cache_miss"}]
    ) == "no_simulation_result"
    assert tea._all_fail_error_code([
        {"error_type": "no_finite_msp"},
        {"error_type": "tea_cashflow_undefined"},
    ]) == "no_simulation_result"
    assert tea._all_fail_error_code(
        [{"error_type": ""}]
    ) == "no_simulation_result"


def test_complete_twelve_cache_still_serves_finite_msp(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    row = (payload.get("comparison_rows") or [None])[0]
    assert row is not None
    assert math.isfinite(float(row["msp_usd_per_kg"]))
    assert math.isfinite(float(row["tci_usd"]))
    assert math.isfinite(float(row["aoc_usd_per_yr"]))


def test_evaluate_injected_null_msp_is_typed_not_typeerror(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "tea_cashflow_undefined"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "tea_cashflow_undefined"
    assert "nan encountered in cashflow array" in str(failures[0].get("error") or "")
    assert failures[0].get("nonfinite_served_metrics") == ["msp_usd_per_kg"]


def test_evaluate_injected_null_msp_without_cashflow_promotes_no_finite_msp(
    monkeypatch,
):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_msp"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_msp"


def test_evaluate_injected_inf_msp_promotes_no_finite_msp(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(monkeypatch, msp=float("inf"))
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_msp"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_msp"


def test_evaluate_mixed_d23_types_stay_no_simulation_result(monkeypatch):
    c1, sibling = _two_c1_records()
    cashflow_polymer = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    real_run = tea._run

    def planted(config, engine_mode, timeout_seconds):
        result = copy.deepcopy(real_run(config, engine_mode, timeout_seconds))
        planted_result = dict(result)
        tea_block = dict(result.get("tea") or {})
        tea_block["msp_usd_per_kg"] = None
        planted_result["tea"] = tea_block
        planted_result["success"] = True
        planted_result.pop("error", None)
        planted_result.pop("error_type", None)
        planted_result.pop("nonfinite_served_metrics", None)
        planted_result.pop("underlying_errors", None)
        if config.get("target_plastic") == cashflow_polymer:
            planted_result["error"] = (
                "RuntimeError: nan encountered in cashflow array"
            )
        return planted_result

    monkeypatch.setattr(tea, "_run", planted)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_simulation_result"
    types = {
        str(row.get("error_type") or "")
        for row in (payload.get("failures") or [])
    }
    assert types == {"tea_cashflow_undefined", "no_finite_msp"}


def test_evaluate_injected_null_gwp_is_typed_not_untyped_drop(monkeypatch):
    record = _record_with_energy("C1")
    _forbid_live(monkeypatch)
    _plant_success_with_null_gwp(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=_public_from_record(record),
        engine_mode="cache",
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "no_finite_gwp"
    assert payload.get("error_code") != "no_simulation_result"
    failures = payload.get("failures") or []
    assert failures
    assert failures[0].get("error_type") == "no_finite_gwp"
    assert failures[0].get("nonfinite_served_metrics") == ["gwp_kg_co2e_per_kg"]
    assert math.isfinite(float(
        (record["result"].get("tea") or {}).get("msp_usd_per_kg")
    ))


def test_evaluate_mixed_null_gwp_keeps_the_finite_sibling(monkeypatch):
    c1, sibling = _two_c1_records()
    poisoned = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    _plant_success_with_null_gwp(
        monkeypatch,
        poison=lambda config, _result: config.get("target_plastic") == poisoned,
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("completed") == 1
    assert payload.get("failed") == 1
    failed = [
        row for row in (payload.get("comparison_rows") or [])
        if row.get("success") is not True
    ]
    assert failed[0].get("error_type") == "no_finite_gwp"


def test_evaluate_mixed_batch_keeps_the_finite_sibling(monkeypatch):
    c1, sibling = _two_c1_records()
    poisoned = c1["config"]["target_plastic"]
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
        poison=lambda config, _result: config.get("target_plastic") == poisoned,
    )
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_configs=[
            _public_from_record(c1),
            _public_from_record(sibling),
        ],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("completed") == 1
    assert payload.get("failed") == 1
    rows = payload.get("comparison_rows") or []
    assert len(rows) == 2
    usable = [row for row in rows if row.get("success") is True]
    failed = [row for row in rows if row.get("success") is not True]
    assert len(usable) == 1
    assert math.isfinite(float(usable[0]["msp_usd_per_kg"]))
    assert failed[0].get("error_type") == "tea_cashflow_undefined"


def test_route_injected_null_msp_is_route_stage_failed(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "route_stage_failed"
    failed = payload.get("failed_stage") or {}
    assert failed.get("error_type") == "tea_cashflow_undefined"
    assert failed.get("success") is False
    assert "nan encountered in cashflow array" in str(failed.get("error") or "")


def test_route_cache_miss_still_reaches_uncostable_or_design_point(
    monkeypatch,
):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)

    def planted(config, engine_mode, timeout_seconds):
        return {
            "success": False,
            "error": "No exact cached simulation matches this configuration.",
            "error_type": "cache_miss",
            "engine_mode": "cache",
            "cache_match_status": "miss",
            "config": config,
        }

    monkeypatch.setattr(tea, "_run", planted)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))
    assert payload.get("success") is False
    assert payload.get("error_code") in {
        "uncostable_route_stage",
        "route_stage_design_point_unavailable",
    }
    assert payload.get("error_code") != "route_stage_failed"


def test_route_nonfinite_metric_is_not_papered_by_screening_estimate(
    monkeypatch,
):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    _plant_success_with_null_msp(
        monkeypatch,
        error="RuntimeError: nan encountered in cashflow array",
    )
    called = {"n": 0}
    real_estimate = tea._screening_estimate

    def traced(config, result):
        called["n"] += 1
        return real_estimate(config, result)

    monkeypatch.setattr(tea, "_screening_estimate", traced)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
            allow_screening_estimate=True,
        ))
    assert called["n"] == 0
    assert payload.get("error_code") == "route_stage_failed"


def test_json_roundtrip_of_worker_refusal_allows_null_metrics():
    payload = {
        "success": False,
        "error_type": "tea_cashflow_undefined",
        "error": "RuntimeError: nan encountered in cashflow array",
        "tea": {
            "msp_usd_per_kg": None,
            "tci_usd": None,
            "aoc_usd_per_yr": None,
        },
        "lca": {"gwp_kg_co2e_per_kg": 1.0},
    }
    encoded = json.dumps(payload, allow_nan=False)
    decoded = json.loads(encoded)
    assert decoded["success"] is False
    assert decoded["tea"]["msp_usd_per_kg"] is None
    assert decoded["lca"]["gwp_kg_co2e_per_kg"] == 1.0
