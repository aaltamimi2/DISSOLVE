"""CL-6: the contaminant-step TEA routing table, as measured.

This slice does not cost a wash. Both wash positions refuse
incomplete_stage_basis_grid. A STRAP stamp is an ordinary dissolution.
Cache only — no BioSTEAM child.
"""
from __future__ import annotations

import copy
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src"), str(_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import separation, tea
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
_REFUSE = "stage_basis_not_derived"
_BLOCKER = "incomplete_stage_basis_grid"
_WASH_BASIS = ("solvent_charge", "vessel", "residence_time", "waste_mass")

# Measured on fff5c2c. The spec draft said position 0 would be costed.
# Hold-to 5 refused that number; both wash rows refuse. Write it that way.
ROUTING_TABLE = (
    {
        "step_kind": "leaching wash, position 0",
        "reaches_tea": True,
        "identity": "step_kind=wash",
        "recovers": 0.0,
        "before_cl4": "free — dropped by ident[0]",
        "after_cl4": "refuse incomplete_stage_basis_grid",
    },
    {
        "step_kind": "leaching wash, later position",
        "reaches_tea": True,
        "identity": "step_kind=wash",
        "recovers": 0.0,
        "before_cl4": "free — dropped by ident[0]",
        "after_cl4": "refuse incomplete_stage_basis_grid",
    },
    {
        "step_kind": "STRAP dissolution w/ contaminants",
        "reaches_tea": True,
        "identity": "dissolution (polymer, solvent, T)",
        "recovers": "the polymer",
        "before_cl4": "costed",
        "after_cl4": "unchanged — stamp inert",
    },
)


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


def _refuse_fields(payload: dict) -> None:
    assert payload["success"] is False
    assert payload["error_code"] == _REFUSE
    assert payload["named_blocker"] == _BLOCKER
    assert payload.get("step_kind") == "wash"
    assert payload.get("recovered_polymer_kg") == 0.0
    assert payload.get("disposal_cost") == "not_costed"
    assert not isinstance(payload.get("disposal_cost"), (int, float))
    assert payload.get("per_stage_usd_per_kg") is None


def test_routing_table_names_three_contaminant_step_kinds():
    kinds = [row["step_kind"] for row in ROUTING_TABLE]
    assert kinds == [
        "leaching wash, position 0",
        "leaching wash, later position",
        "STRAP dissolution w/ contaminants",
    ]
    assert all(row["reaches_tea"] is True for row in ROUTING_TABLE)
    assert ROUTING_TABLE[0]["after_cl4"] == ROUTING_TABLE[1]["after_cl4"] == (
        "refuse incomplete_stage_basis_grid"
    )
    assert "costed" not in ROUTING_TABLE[0]["after_cl4"]
    assert ROUTING_TABLE[2]["after_cl4"].startswith("unchanged")


def test_leaching_wash_position_0_reaches_tea_and_refuses(monkeypatch):
    ident = tea._stage_identity(None, "acetone", 25.0, "wash")
    assert ident[3] == "wash"
    assert ident[0] == ""
    assert tea._stage_visible(ident) is True
    composition, route = _exact_planner_route()
    first = copy.deepcopy(route)
    first["steps"] = [_wash()] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    _refuse_fields(payload)
    assert payload.get("wash_position") == 0
    assert set(payload.get("missing_wash_basis") or []) >= set(_WASH_BASIS)


def test_leaching_wash_later_position_reaches_tea_and_refuses(monkeypatch):
    composition, route = _exact_planner_route()
    later = copy.deepcopy(route)
    later["steps"] = [dict(item) for item in later["steps"]] + [_wash()]
    payload = _cost(monkeypatch, composition, later)
    _refuse_fields(payload)
    assert payload.get("wash_position") == 1


def test_cited_wash_basis_still_has_no_cost_path(monkeypatch):
    composition, route = _exact_planner_route()
    cited = _wash(
        solvent_charge=1,
        vessel=1,
        residence_time=1,
        waste_mass=1,
        field_origin={name: "constructed" for name in _WASH_BASIS},
    )
    first = copy.deepcopy(route)
    first["steps"] = [cited] + [dict(item) for item in first["steps"]]
    payload = _cost(monkeypatch, composition, first)
    _refuse_fields(payload)
    assert payload.get("missing_wash_basis") == []
    assert set(payload.get("cited_wash_basis") or []) >= set(_WASH_BASIS)


def test_strap_dissolution_is_ordinary_and_stamp_inert(monkeypatch):
    composition, unset_route = _exact_planner_route()
    stamped_plan = _data(separation.plan_multistage_separation(
        list(composition),
        feed_mass_fractions=dict(composition),
        contaminants=_DEHP,
        contaminant_mode="strap",
        top_k_routes=10,
        breadth=1,
    ))
    assert stamped_plan["success"] is True
    unset_dissolutions = [
        (
            item.get("dissolved_polymer") or item.get("polymer"),
            item.get("solvent"),
            item.get("temperature_c"),
        )
        for item in unset_route["steps"]
        if item.get("step_kind") != "wash"
        and (item.get("dissolved_polymer") or item.get("polymer"))
    ]
    stamped_route = next(
        route
        for route in (stamped_plan.get("top_k_sequences") or [])
        if [
            (
                item.get("dissolved_polymer") or item.get("polymer"),
                item.get("solvent"),
                item.get("temperature_c"),
            )
            for item in route.get("steps") or []
            if item.get("step_kind") != "wash"
            and (item.get("dissolved_polymer") or item.get("polymer"))
        ] == unset_dissolutions
    )
    assert all(item.get("step_kind") != "wash" for item in stamped_route["steps"])
    unset_cost = _cost(monkeypatch, composition, unset_route)
    strap_cost = _cost(monkeypatch, composition, stamped_route)
    assert unset_cost["success"] is True
    assert strap_cost["success"] is True
    unset_msp = unset_cost["mass_weighted_recovered_msp_usd_per_kg"]
    strap_msp = strap_cost["mass_weighted_recovered_msp_usd_per_kg"]
    assert struct.pack(">d", float(strap_msp)) == struct.pack(">d", float(unset_msp))


def test_wash_train_formulation_stays_unavailable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="superstructure",
        formulation="wash_train",
    ))
    assert payload["error_code"] == "process_model_wash_train_unavailable"
