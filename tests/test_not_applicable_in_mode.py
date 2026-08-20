"""Wrong-mode scalars on evaluate refuse not_applicable_in_mode. Not a rename."""
from __future__ import annotations

import inspect
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
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    for name in tea._LOOKUP_MODE_SELECTORS:
        assert name in lookup_params
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


def test_unknown_top_level_kwarg_is_unknown_process_field(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    leftover = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        not_a_lookup_selector=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_selector"]
    assert leftover.get("error_code") != "not_applicable_in_mode"
    assert leftover.get("tool_name") == "evaluate_tea_lca_scenarios"
    assert "comparison_rows" not in leftover
    other = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        also_not_a_field=1,
    ))
    assert other.get("extra_keys") == ["also_not_a_field"]
    assert other.get("extra_keys") != leftover.get("extra_keys")
    pair = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        not_a_lookup_selector=True,
        also_not_a_field=1,
    ))
    assert pair.get("extra_keys") == ["also_not_a_field", "not_a_lookup_selector"]
    mixed = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        sensitivity_axes=["solvent_price"],
        not_a_lookup_selector=True,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["sensitivity_axes"]
    assert mixed.get("error_code") != "unknown_process_field"


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
        extra = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            not_a_lookup_selector=True,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert extra.get("refusal") != "tool_exception"
        assert "handle" not in extra


def test_evaluate_refuses_sensitivity_scalars(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    sent = {
        "parameter": "solvent_price",
        "values": [0.5, 1.5],
        "analysis_mode": "tornado",
        "metric": "msp_usd_per_kg",
    }
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    sensitivity_props = _schema("analyze_tea_sensitivity")["parameters"]["properties"]
    for name, value in sent.items():
        assert name in sensitivity_props
        assert name not in eval_props
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="cache",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("applicable_mode") == "sensitivity"
        assert payload.get("inapplicable_fields") == [name]
        assert payload["applicable_mode_by_field"] == {name: "sensitivity"}
        assert "comparison_rows" not in payload


def test_evaluate_refuses_lookup_energy_cases_list(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    assert "energy_cases" not in eval_props
    assert "energy_cases" in inspect.signature(
        tea.lookup_admitted_process_records,
    ).parameters
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
        energy_cases=["C1"],
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("applicable_mode") == "lookup"
    assert payload.get("inapplicable_fields") == ["energy_cases"]
    empty = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
        energy_cases=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    nested = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, energy_cases=["C1"])],
        engine_mode="cache",
    ))
    assert nested.get("error_code") == "unknown_process_field"
    assert nested.get("error_code") != "not_applicable_in_mode"
    assert "energy_cases" in list(nested.get("extra_keys") or [])


def test_mixed_wrong_mode_scalars_name_each_home(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record)],
        engine_mode="cache",
        energy_cases=["C1"],
        parameter="solvent_price",
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["energy_cases", "parameter"]
    assert payload.get("applicable_mode") is None
    assert payload["applicable_mode_by_field"] == {
        "energy_cases": "lookup",
        "parameter": "sensitivity",
    }


def test_sensitivity_schema_omits_screening_handoff():
    props = _schema("analyze_tea_sensitivity")["parameters"]["properties"]
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    assert "screening_shortlist" in eval_props
    assert "held_process_basis" in eval_props
    assert "screening_shortlist" not in props
    assert "held_process_basis" not in props


def test_sensitivity_refuses_screening_handoff(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    shortlist = {
        "source": "explicit",
        "items": [{
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "dissolution_temperature_c": 145.0,
        }],
    }
    payload = _data(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="cache",
        screening_shortlist=shortlist,
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["screening_shortlist"]
    assert payload.get("applicable_mode") == "evaluate"
    assert payload["applicable_mode_by_field"] == {
        "screening_shortlist": "evaluate",
    }
    assert payload.get("tool_name") == "analyze_tea_sensitivity"
    assert "sensitivity_rows" not in payload
    both = _data(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="cache",
        screening_shortlist=shortlist,
        held_process_basis={"energy_case": "C1"},
    ))
    assert both.get("error_code") == "not_applicable_in_mode"
    assert both.get("inapplicable_fields") == [
        "screening_shortlist", "held_process_basis",
    ]
    assert both.get("applicable_mode") == "evaluate"
    held = _data(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="cache",
        held_process_basis={"energy_case": "C1"},
    ))
    assert held.get("error_code") == "not_applicable_in_mode"
    assert held.get("inapplicable_fields") == ["held_process_basis"]
    leftover = _data(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="cache",
        not_a_sensitivity_field=1,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_sensitivity_field"]
    assert leftover.get("error_code") != "not_applicable_in_mode"
    assert leftover.get("tool_name") == "analyze_tea_sensitivity"
    assert "sensitivity_rows" not in leftover
    mixed = _data(tea.analyze_tea_sensitivity(
        scenario,
        parameter="solvent_price",
        engine_mode="cache",
        screening_shortlist=shortlist,
        not_a_sensitivity_field=1,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["screening_shortlist"]
    assert mixed.get("error_code") != "unknown_process_field"


def test_dispatch_sensitivity_handoff_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "analyze_tea_sensitivity",
            scenario=scenario,
            parameter="solvent_price",
            engine_mode="cache",
            screening_shortlist={
                "source": "explicit",
                "items": [{
                    "target_polymer": "LDPE",
                    "solvent": "Dodecane",
                    "dissolution_temperature_c": 145.0,
                }],
            },
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        served = dispatch(
            "analyze_tea_sensitivity",
            scenario=scenario,
            parameter="solvent_price",
            engine_mode="cache",
        )
        assert served.get("available") is True
        assert served.get("source_basis") == "tea_cache_exact"
        assert served.get("handle")
        extra = dispatch(
            "analyze_tea_sensitivity",
            scenario=scenario,
            parameter="solvent_price",
            engine_mode="cache",
            not_a_sensitivity_field=1,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert "handle" not in extra


def test_lookup_energy_cases_and_sensitivity_parameter_still_serve(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    lookup = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        energy_cases=["C1"],
    ))
    assert lookup.get("success") is True
    assert lookup["requested_energy_cases"] == ["C1"]
    sensitivity = _data(tea.analyze_tea_sensitivity(
        _public_from_record(record),
        parameter="solvent_price",
        engine_mode="cache",
    ))
    assert sensitivity.get("success") is True
    assert sensitivity.get("error_code") != "not_applicable_in_mode"


def test_dispatch_evaluate_parameter_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            parameter="solvent_price",
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        energy = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            energy_cases=["C1"],
        )
        assert energy.get("available") is False
        assert energy.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in energy


def test_evaluate_refuses_record_form_and_requested_metrics(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    eval_props = _schema("evaluate_tea_lca_scenarios")["parameters"]["properties"]
    lookup_params = inspect.signature(tea.lookup_admitted_process_records).parameters
    sent = {
        "record_form": "grouped_comparison",
        "requested_metrics": ["etox", "energy"],
    }
    for name, value in sent.items():
        assert name in lookup_params
        assert name not in eval_props
        payload = _data(tea.evaluate_tea_lca_scenarios(
            [scenario],
            engine_mode="cache",
            **{name: value},
        ))
        assert payload.get("error_code") == "not_applicable_in_mode"
        assert payload.get("applicable_mode") == "lookup"
        assert payload.get("inapplicable_fields") == [name]
        assert payload["applicable_mode_by_field"] == {name: "lookup"}
        assert "comparison_rows" not in payload
    empty = _data(tea.evaluate_tea_lca_scenarios(
        [scenario],
        engine_mode="cache",
        requested_metrics=[],
    ))
    assert empty.get("error_code") == "not_applicable_in_mode"
    assert empty.get("inapplicable_fields") == ["requested_metrics"]
    nested = _data(tea.evaluate_tea_lca_scenarios(
        [_public_from_record(record, record_form="per_record")],
        engine_mode="cache",
    ))
    assert nested.get("error_code") == "unknown_process_field"
    assert nested.get("error_code") != "not_applicable_in_mode"
    assert "record_form" in list(nested.get("extra_keys") or [])


def test_lookup_still_accepts_record_form_and_requested_metrics(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        record_form="grouped_comparison",
        requested_metrics=["etox", "energy"],
    ))
    assert payload.get("success") is True
    assert payload["record_form"] == "grouped_comparison"
    assert payload["requested_metrics"] == ["etox", "energy"]


def test_dispatch_evaluate_record_form_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            record_form="per_record",
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        metrics = dispatch(
            "evaluate_tea_lca_scenarios",
            scenarios=[_public_from_record(record)],
            engine_mode="cache",
            requested_metrics=["gwp"],
        )
        assert metrics.get("available") is False
        assert metrics.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in metrics


def test_lookup_leftover_extra_is_unknown_process_field(monkeypatch):
    _forbid_live(monkeypatch)
    leftover = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        solvent="Dodecane",
        not_a_lookup_field=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_field"]
    assert leftover.get("error_code") != "missing_target_polymer"
    assert leftover.get("tool_name") == "lookup_admitted_process_records"
    assert "comparison_rows" not in leftover
    other = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        also_not_a_field=1,
    ))
    assert other.get("extra_keys") == ["also_not_a_field"]
    assert other.get("extra_keys") != leftover.get("extra_keys")
    pair = _data(tea.lookup_admitted_process_records(
        target_polymer="LDPE",
        not_a_lookup_field=True,
        also_not_a_field=1,
    ))
    assert pair.get("extra_keys") == ["also_not_a_field", "not_a_lookup_field"]
    missing_polymer = _data(tea.lookup_admitted_process_records(
        not_a_lookup_field=True,
    ))
    assert missing_polymer.get("error_code") == "unknown_process_field"
    assert missing_polymer.get("extra_keys") == ["not_a_lookup_field"]
    assert missing_polymer.get("error_code") != "missing_target_polymer"
    wrap = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        not_a_lookup_field=True,
    ))
    assert wrap.get("error_code") == "unknown_process_field"
    assert wrap.get("extra_keys") == ["not_a_lookup_field"]
    assert wrap.get("tool_name") == "evaluate_process"


def test_dispatch_lookup_leftover_is_named_refuse(monkeypatch):
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
                "not_a_lookup_field": True,
            },
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "unknown_process_field"
        assert refused.get("refusal") != "tool_exception"
        assert "handle" not in refused
        served = dispatch(
            "evaluate_process",
            mode="lookup",
            lookup_filter={
                "target_polymer": "LDPE",
                "solvent": "Dodecane",
            },
        )
        assert served.get("available") is True
        assert served.get("handle")


def test_evaluate_process_top_level_selector_is_not_applicable(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    payload = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="cache",
        sensitivity_axes=["solvent_price"],
    ))
    assert payload.get("error_code") == "not_applicable_in_mode"
    assert payload.get("inapplicable_fields") == ["sensitivity_axes"]
    assert payload.get("applicable_mode") == "lookup"
    assert payload["applicable_mode_by_field"] == {
        "sensitivity_axes": "lookup",
    }
    assert payload.get("tool_name") == "evaluate_process"
    assert payload.get("error_code") != "unknown_process_field"
    energy = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="cache",
        energy_cases=["C1"],
    ))
    assert energy.get("error_code") == "not_applicable_in_mode"
    assert energy.get("inapplicable_fields") == ["energy_cases"]
    leftover = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="cache",
        not_a_lookup_selector=True,
    ))
    assert leftover.get("error_code") == "unknown_process_field"
    assert leftover.get("extra_keys") == ["not_a_lookup_selector"]
    mixed = _data(tea.evaluate_process(
        mode="evaluate",
        process_config=scenario,
        engine_mode="cache",
        sensitivity_axes=["solvent_price"],
        not_a_lookup_selector=True,
    ))
    assert mixed.get("error_code") == "not_applicable_in_mode"
    assert mixed.get("inapplicable_fields") == ["sensitivity_axes"]
    assert mixed.get("error_code") != "unknown_process_field"
    filtered = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={
            "target_polymer": "LDPE",
            "solvent": "Dodecane",
            "sensitivity_axes": ["solvent_price"],
        },
    ))
    assert filtered.get("success") is True
    assert filtered.get("error_code") != "not_applicable_in_mode"
    top_level_lookup = _data(tea.evaluate_process(
        mode="lookup",
        lookup_filter={"target_polymer": "LDPE"},
        sensitivity_axes=["solvent_price"],
    ))
    assert top_level_lookup.get("error_code") == "not_applicable_in_mode"
    assert top_level_lookup.get("inapplicable_fields") == ["sensitivity_axes"]
    assert top_level_lookup.get("error_code") != "unknown_process_field"


def test_dispatch_evaluate_process_selector_is_named_refuse(monkeypatch):
    record = _record_by_label("ldpe-route-c1")
    scenario = _public_from_record(record)
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        refused = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="cache",
            sensitivity_axes=["solvent_price"],
        )
        assert refused.get("available") is False
        assert refused.get("refusal") == "not_applicable_in_mode"
        assert "handle" not in refused
        extra = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="cache",
            not_a_lookup_selector=True,
        )
        assert extra.get("available") is False
        assert extra.get("refusal") == "unknown_process_field"
        assert "handle" not in extra
        served = dispatch(
            "evaluate_process",
            mode="evaluate",
            process_config=scenario,
            engine_mode="cache",
        )
        assert served.get("available") is True
        assert served.get("handle")


