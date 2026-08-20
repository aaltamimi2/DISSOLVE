"""rank_landscape source=residual_route from a mode=route handle. Never last_route."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch
from dissolve import optimization, tea
from dissolve.session import bind_tool_session, load_handle, new_session, store_handle

from test_evaluate_process import (
    _ROUTE_CAPACITY,
    _ROUTE_ENERGY,
    _ROUTE_PRECIP,
    _exact_planner_route,
    _store_planner_route_handle,
)


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _forbid_live(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("residual_route must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea, "_record_for_pair", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)


def _costed_route_handle(session, monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    plan = _store_planner_route_handle(session, composition, route)
    payload = _data(tea.evaluate_process(
        mode="route",
        handle=plan,
        processing_capacity_mt_per_yr=_ROUTE_CAPACITY,
        energy_case=_ROUTE_ENERGY,
        precipitation_temperature_c=_ROUTE_PRECIP,
        engine_mode="cache",
    ))
    assert payload.get("success") is True
    return store_handle(
        session,
        tool="evaluate_process",
        source_basis="tea_cache_exact",
        data=payload,
    ), payload


def test_residual_route_omitted_handle_is_unknown_handle(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(source="residual_route", operation="optimum"))
    assert payload.get("error_code") == "unknown_handle"
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("tool_name") == "rank_landscape"


def test_residual_route_planner_handle_is_not_the_costed_route(monkeypatch):
    composition, route = _exact_planner_route()
    _forbid_live(monkeypatch)
    session = new_session()
    with bind_tool_session(session):
        plan = _store_planner_route_handle(session, composition, route)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=plan,
        ))
    assert payload.get("error_code") == "not_route_handle"
    assert payload.get("error_code") != "tool_not_wired"


def test_residual_route_sort_is_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(source="residual_route", operation="sort"))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("operation") == "sort"
    assert payload.get("error_code") != "tool_not_wired"


def test_residual_route_does_not_read_getattr_source_state(monkeypatch):
    monkeypatch.setattr(
        optimization,
        "_source_state",
        lambda: (_ for _ in ()).throw(
            AssertionError("residual_route must not getattr last_route")
        ),
    )
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        assert session.get("last_route") is None
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("source") == "residual_route"
    assert payload.get("operation") == "optimum"
    assert payload.get("analysis_type") == "point_optimum"
    assert payload.get("selected_point")
    landscape = payload.get("landscape_points") or []
    assert len(landscape) == payload.get("n_landscape_points")
    assert payload.get("n_landscape_points") >= 2
    assert payload.get("cheapest_point")
    assert payload.get("solver")


def test_residual_route_pareto_returns_landscape_and_frontier(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="pareto_dominance",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert payload.get("error_code") != "tool_not_wired"
    assert payload.get("source") == "residual_route"
    assert payload.get("operation") == "pareto_dominance"
    landscape = payload.get("landscape_points") or []
    frontier = payload.get("frontier_points") or []
    assert landscape
    assert frontier
    assert payload.get("n_landscape_points") == len(landscape)
    assert payload.get("n_frontier_points") == len(frontier)
    assert payload.get("n_frontier_points") <= payload.get("n_landscape_points")
    assert payload.get("points") == frontier
    assert payload.get("knee_status") in {
        "endpoint_only_no_interior_knee",
        "interior_tradeoff",
        "not_calculated_no_comparable_designs",
    }
    assert payload.get("cheapest_point")
    assert "frontier_tradeoff" in payload


def test_dispatch_residual_route_issues_handle(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        out = dispatch(
            "rank_landscape",
            source="residual_route",
            operation="optimum",
            handle=handle,
        )
        assert out.get("available") is True
        assert out.get("handle")
        assert out.get("handle") != handle
        stored = load_handle(session, out["handle"])
        assert stored.get("tool") == "rank_landscape"
        assert stored["exact"]["source"] == "residual_route"
        old = dispatch("optimize_stored_route")
        assert old.get("available") is False
        assert old.get("refusal") == "tool_not_wired"


def test_objective_on_process_rows_is_not_applicable(monkeypatch):
    _forbid_live(monkeypatch)
    payload = _data(tea.rank_landscape(
        source="process_rows",
        operation="pareto_dominance",
        objective="min_cost",
    ))
    assert payload.get("error_code") == "not_applicable_in_source"
    assert payload.get("inapplicable_fields") == ["objective"]
    assert payload.get("source") == "process_rows"
