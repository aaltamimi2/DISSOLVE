"""CL-4: a wash is visible to TEA and is not silently free.

Later-position washes refuse stage_basis_not_derived. Position 0 also
refuses incomplete_stage_basis_grid until solvent charge, vessel,
residence, and waste mass have field_origin — inventing those is the
fabrication the ADMIT forbade. No BioSTEAM child. No wash_train.
"""
from __future__ import annotations

import copy
from pathlib import Path


from dissolve import tea
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, new_session

from test_evaluate_process import (
    _ROUTE_CAPACITY,
    _ROUTE_ENERGY,
    _ROUTE_PRECIP,
    _exact_planner_route,
    _forbid_live,
    _store_planner_route_handle,
)


_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _wash(**kwargs) -> dict:
    step = {
        "step_kind": "wash",
        "path": "leaching",
        "solvent": "acetone",
        "temperature_c": 25.0,
        "contaminants_targeted": [_DEHP],
    }
    step.update(kwargs)
    return step


def _cost(monkeypatch, composition: dict, route: dict) -> dict:
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        handle = _store_planner_route_handle(session, composition, route)
        return _data(tea.evaluate_process(
            mode="route",
            handle=handle,
            processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
            energy_case=_ROUTE_ENERGY,
            precipitation_temperature_c=_ROUTE_PRECIP,
            engine_mode="cache",
        ))


def test_wash_identity_is_step_kind_not_a_polymer_sentinel():
    ident = tea._stage_identity(None, "acetone", 25.0, "wash")
    assert ident[3] == "wash"
    assert ident[0] == ""
    assert tea._stage_visible(ident) is True
    dissolution = tea._stage_identity("LDPE", "toluene", 145.0)
    assert dissolution[3] == "dissolution"
    assert dissolution[0] == "ldpe"


def test_later_position_wash_refuses_incomplete_stage_basis_grid(monkeypatch):
    composition, route = _exact_planner_route()
    later = copy.deepcopy(route)
    later["steps"] = [dict(item) for item in later["steps"]] + [_wash()]
    payload = _cost(monkeypatch, composition, later)
    assert payload["success"] is False
    assert payload["error_code"] == "stage_basis_not_derived"
    assert payload["named_blocker"] == "incomplete_stage_basis_grid"
    assert payload.get("step_kind") == "wash"
    assert payload.get("wash_position") == 1
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert not isinstance(payload.get("disposal_cost"), (int, float))
    assert payload.get("per_stage_usd_per_kg") is None


def test_position_zero_wash_refuses_rather_than_invent_a_basis(monkeypatch):
    composition, route = _exact_planner_route()
    first = copy.deepcopy(route)
    first["steps"] = [_wash()] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    assert payload["success"] is False
    assert payload["error_code"] == "stage_basis_not_derived"
    assert payload["named_blocker"] == "incomplete_stage_basis_grid"
    assert payload.get("wash_position") == 0
    assert set(payload.get("missing_wash_basis") or []) >= {
        "solvent_charge", "vessel", "residence_time", "waste_mass",
    }
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert payload.get("per_stage_usd_per_kg") is None


def test_wash_does_not_keyerror_or_count_as_polymer(monkeypatch):
    composition, route = _exact_planner_route()
    with_wash = copy.deepcopy(route)
    with_wash["steps"] = [_wash()] + [dict(item) for item in with_wash["steps"]]
    payload = _cost(monkeypatch, composition, with_wash)
    assert payload.get("error_code") != "route_feed_mismatch"
    assert "KeyError" not in str(payload.get("error") or "")
    consumed = payload.get("consumed_route") or {}
    for item in consumed.get("steps") or []:
        if item.get("step_kind") == "wash":
            assert item.get("dissolved_polymer") not in {"wash", "Wash"}


def test_formulation_wash_train_stays_unavailable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
    ))
    assert payload["error_code"] == "process_model_wash_train_unavailable"
    assert payload.get("formulation") == "wash_train"
