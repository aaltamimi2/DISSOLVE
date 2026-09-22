"""CL-5: the STRAP stamp is inert to costing. Test-only if MSPs already match.

Same dissolutions with and without contaminant_mode=strap. Cache only —
no live BioSTEAM child. No tea.py edit.
"""
from __future__ import annotations

import json
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


def _data(raw: str) -> dict:
    return parse_tool_result(raw)["data"]


def _dissolutions(route: dict) -> list[tuple[str, str, float | None]]:
    out = []
    for item in route.get("steps") or []:
        if item.get("step_kind") == "wash":
            continue
        polymer = item.get("dissolved_polymer") or item.get("polymer")
        if not polymer:
            continue
        out.append((
            str(polymer),
            str(item.get("solvent") or ""),
            item.get("temperature_c"),
        ))
    return out


def _identities(route: dict) -> list[tuple[str, str, float | None]]:
    return [
        tea._stage_identity(polymer, solvent, temperature)
        for polymer, solvent, temperature in _dissolutions(route)
    ]


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


def _recovered_mt(payload: dict) -> float:
    return sum(
        float(row["modeled_stage_product_mt_per_yr"])
        for row in (payload.get("stage_results") or [])
        if row.get("modeled_stage_product_mt_per_yr") is not None
    )


def _bits(value: float) -> bytes:
    return struct.pack(">d", float(value))


def test_strap_stamp_is_inert_to_cached_msp(monkeypatch):
    composition, unset_route = _exact_planner_route()
    feed = list(composition)
    stamped_plan = _data(separation.plan_multistage_separation(
        feed,
        feed_mass_fractions=dict(composition),
        contaminants=_DEHP,
        contaminant_mode="strap",
        top_k_routes=10,
        breadth=1,
    ))
    assert stamped_plan["success"] is True
    assert stamped_plan["contaminant_mode"] == "strap"
    stamped_route = next(
        (
            route for route in (stamped_plan.get("top_k_sequences") or [])
            if _dissolutions(route) == _dissolutions(unset_route)
        ),
        None,
    )
    assert stamped_route is not None
    assert any(item.get("path") == "strap" for item in stamped_route["steps"])
    assert all(item.get("step_kind") != "wash" for item in stamped_route["steps"])
    assert _identities(stamped_route) == _identities(unset_route)

    unset_cost = _cost(monkeypatch, composition, unset_route)
    strap_cost = _cost(monkeypatch, composition, stamped_route)
    assert unset_cost["success"] is True
    assert strap_cost["success"] is True
    unset_msp = unset_cost["mass_weighted_recovered_msp_usd_per_kg"]
    strap_msp = strap_cost["mass_weighted_recovered_msp_usd_per_kg"]
    assert unset_msp is not None and strap_msp is not None
    assert _bits(strap_msp) == _bits(unset_msp)
    assert _recovered_mt(strap_cost) == _recovered_mt(unset_cost)
    recovered_names = json.dumps(strap_cost.get("stage_results") or [])
    assert "phthalate" not in recovered_names.casefold()
    assert "dehp" not in recovered_names.casefold()
    assert "contaminant" not in [
        str(row.get("polymer") or row.get("target_plastic") or "").casefold()
        for row in (strap_cost.get("stage_results") or [])
    ]
