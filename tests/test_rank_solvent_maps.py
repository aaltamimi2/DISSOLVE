"""planner_solvent_map / allowed_solvents are rank_landscape arguments.

Not a third public economics name, not a remnant-grid ranking, and not a
scan of top_k_sequences.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import CONSUMERS, dispatch, tool_schemas
from dissolve import tea
from dissolve.cli import EXPECTED_REGISTRY_NAMES


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("superstructure maps must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


_PLAN_MAP = {"LDPE": "Toluene", "EVOH": "DMSO"}
_ALLOWED_MAP = {"LDPE": ["Toluene"], "EVOH": ["DMSO", "Toluene"]}


def test_sequence_without_map_is_missing_planner_solvent_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("formulation") == "sequence"
    assert payload.get("success") is False
    assert "landscape_points" not in payload


def test_empty_planner_map_is_missing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert payload.get("polymers") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_nested_temperature_value_is_not_a_planner_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={"LDPE": {"solvent": "Toluene", "temperature_c": 25}},
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_feed_polymer_without_solvent_is_missing_planner_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map={"LDPE": "Toluene"},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert payload.get("polymers") == ["EVOH"]
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_allowed_solvents_does_not_substitute_for_planner_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_complete_planner_map_does_not_rank(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("source") == "superstructure"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_map_hidden_in_process_config_is_still_missing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        process_config={"planner_solvent_map": _PLAN_MAP},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_planner_solvent_map"
    assert "landscape_points" not in payload


def test_solvent_without_allowed_set_is_missing_allowed_solvents(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("formulation") == "solvent"
    assert "landscape_points" not in payload


def test_empty_allowed_solvents_is_missing(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=[],
        target_polymer=["LDPE"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("feed") == ["LDPE"]
    assert "landscape_points" not in payload


def test_planner_map_does_not_substitute_for_allowed_solvents(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        planner_solvent_map=_PLAN_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert "landscape_points" not in payload


def test_flat_allowed_list_is_bound_then_unwired(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=["Toluene", "DMSO"],
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("source") == "superstructure"
    assert payload.get("error_code") != "missing_allowed_solvents"
    assert "landscape_points" not in payload


def test_per_polymer_allowed_map_missing_a_stage_polymer(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents={"LDPE": ["Toluene"]},
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "missing_allowed_solvents"
    assert payload.get("polymers") == ["EVOH"]
    assert payload.get("feed") == ["LDPE", "EVOH"]
    assert "landscape_points" not in payload


def test_complete_allowed_map_does_not_rank(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="solvent",
        allowed_solvents=_ALLOWED_MAP,
        target_polymer=["LDPE", "EVOH"],
    ))
    assert payload.get("error_code") == "tool_not_wired"
    assert "landscape_points" not in payload


def test_superstructure_without_formulation_stays_unwired(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(source="superstructure"))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("source") == "superstructure"
    assert payload.get("error_code") != "missing_planner_solvent_map"
    assert payload.get("error_code") != "missing_allowed_solvents"


def test_residual_route_stays_unwired_without_requiring_maps(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        formulation="sequence",
    ))
    assert payload.get("error_code") == "tool_not_wired"
    assert payload.get("source") == "residual_route"


def test_maps_on_process_rows_are_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        planner_solvent_map=_PLAN_MAP,
        allowed_solvents=_ALLOWED_MAP,
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "process_rows"
    assert payload.get("inapplicable_fields") == [
        "planner_solvent_map",
        "allowed_solvents",
    ]
    assert "landscape_points" not in payload


def test_maps_on_residual_route_are_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="residual_route",
        planner_solvent_map=_PLAN_MAP,
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("source") == "residual_route"
    assert payload.get("inapplicable_fields") == ["planner_solvent_map"]


def test_top_k_sequences_is_unknown_extra_not_the_map(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        top_k_sequences=[{"steps": [{"dissolved_polymer": "LDPE", "solvent": "Toluene"}]}],
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["top_k_sequences"]
    assert payload.get("error_code") != "missing_planner_solvent_map"


def test_exclude_safety_fail_stays_unknown_extra(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="sequence",
        planner_solvent_map=_PLAN_MAP,
        exclude_safety_fail=True,
    ))
    assert payload.get("error_code") == "unknown_process_field"
    assert payload.get("extra_keys") == ["exclude_safety_fail"]


def test_maps_are_optional_schema_args_not_a_35th_name(monkeypatch):
    _forbid_live(monkeypatch)
    assert "rank_landscape" not in CONSUMERS
    assert len(EXPECTED_REGISTRY_NAMES) == 34
    props = {
        item["name"]: item["parameters"]["properties"]
        for item in tool_schemas()
    }["rank_landscape"]
    assert "planner_solvent_map" in props
    assert "allowed_solvents" in props
    assert "default" not in props["planner_solvent_map"]
    assert "default" not in props["allowed_solvents"]
    required = next(
        item["parameters"].get("required") or []
        for item in tool_schemas()
        if item["name"] == "rank_landscape"
    )
    assert "planner_solvent_map" not in required
    assert "allowed_solvents" not in required
    assert "evaluate_process" not in EXPECTED_REGISTRY_NAMES


def test_dispatch_names_missing_planner_map(monkeypatch):
    _forbid_live(monkeypatch)
    ranked = dispatch(
        "rank_landscape",
        source="superstructure",
        formulation="sequence",
    )
    assert ranked.get("available") is False
    assert ranked.get("refusal") == "missing_planner_solvent_map"
    assert ranked.get("handle") is None
