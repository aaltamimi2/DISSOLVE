"""Lookup selectors on evaluate refuse not_applicable_in_mode. Not a rename."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch, tool_schemas
from dissolve import tea
from dissolve.session import bind_tool_session, new_session


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _record_by_label(label: str) -> dict:
    return next(
        record for record in tea._records()
        if str(record.get("label") or "").casefold() == label
    )


def _public_from_record(record: dict, **overrides) -> dict:
    cfg = record["config"]
    scenario = {
        "target_polymer": cfg["target_plastic"],
        "solvent": cfg["solvent"],
        "target_mass_percent": cfg["target_plastic_percent"],
        "processing_capacity_mt_per_yr": cfg["processing_capacity"],
        "energy_case": cfg["energy_case"],
        "dissolution_temperature_c": cfg["dissolution_temperature_c"],
        "precipitation_temperature_c": cfg["precipitation_temperature_c"],
        "solvent_price_usd_per_kg": cfg["solvent_price"],
        "solvent_loss_pct": cfg["solvent_loss_pct"],
        "feedstock_distance_km": cfg["feedstock_distance_km"],
        "dissolution_capacity": cfg["dissolution_capacity"],
        "labor_cost_usd_per_employee_yr": cfg["labor_cost"],
    }
    scenario.update(overrides)
    return scenario


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("evaluate must not start BioSTEAM for this refuse")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _schema(name: str) -> dict:
    return next(item for item in tool_schemas() if item["name"] == name)


def test_lookup_selectors_are_absent_from_evaluate_schema():
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    lookup_props = _schema("lookup_admitted_process_records")[
        "parameters"
    ]["properties"]
    for name in tea._LOOKUP_MODE_SELECTORS:
        assert name in lookup_props
        assert name not in eval_props


def test_evaluate_refuses_lookup_selectors_before_missing_scenarios(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("success") is False
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("error_code") != "missing_scenarios"
    assert payload.get("mode") == "evaluate"
    assert payload.get("applicable_mode") == "lookup"
    assert payload.get("inapplicable_fields") == ["sensitivity_axes"]
    assert "comparison_rows" not in payload


def test_evaluate_refuses_each_lookup_selector(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    sent = {
        "sensitivity_labels": ["ldpe-price-low"],
        "sensitivity_axes": ["solvent_price"],
        "sensitivity_level_selector": "low_high",
    }
    for name, value in sent.items():
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="cache",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("inapplicable_fields") == [name]
        assert payload.get("msp_usd_per_kg") is None
        assert "comparison_rows" not in payload
    empty = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        sensitivity_axes=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    assert empty.get("inapplicable_fields") == ["sensitivity_axes"]


def test_nested_selector_stays_unknown_process_field(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, sensitivity_axes=["solvent_price"])],
        engine_mode="cache",
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert "sensitivity_axes" in list(payload.get("extra_keys") or [])


def test_unknown_top_level_kwarg_is_still_typeerror(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    with pytest.raises(TypeError):
        tea.evaluate_tea_lca_scenarios(
            [_public_from_record(record)],
            engine_mode="cache",
            not_a_lookup_selector=True,
        )


def test_evaluate_without_selectors_still_serves_cache(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "not_applicable_in_mode"
    assert payload["comparison_rows"][0]["msp_usd_per_kg"] == pytest.approx(
        float(record["result"]["tea"]["msp_usd_per_kg"]),
    )


def test_lookup_still_accepts_the_selectors(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("success") is True
    assert payload["analysis_type"] == "admitted_process_sensitivity_records"


def test_dispatch_evaluate_selector_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            sensitivity_axes=["solvent_price"],
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        served = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
        )
        assert served.get("available") is True
        assert served.get("source_basis") == "tea_cache_exact"
        assert served.get("handle")
