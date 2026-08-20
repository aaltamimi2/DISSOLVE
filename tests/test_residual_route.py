"""rank_landscape source=residual_route from a mode=route handle. Never last_route.

Pareto keeps F's quality schema including frontier_fraction, sparse_frontier,
and safety_standing on every point. Safety is carried, not a usable filter.
"""
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
    _public_from_record,
    _store_planner_route_handle,
)
from test_rank_evaluate_handle import _record_by_label


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
    for point in landscape:
        assert point["safety_standing"]["status"] in {
            "evaluated", "not_requested", "unavailable",
        }
    assert payload["selected_point"]["safety_standing"]["status"] in {
        "evaluated", "not_requested", "unavailable",
    }
    assert payload["cheapest_point"]["safety_standing"]["status"] in {
        "evaluated", "not_requested", "unavailable",
    }


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
    n_land = payload["n_landscape_points"]
    n_front = payload["n_frontier_points"]
    assert payload.get("frontier_fraction") == n_front / n_land
    assert payload.get("sparse_frontier") is (
        n_front == 1 or payload.get("cheapest_equals_lowest_y") is True
    )
    assert isinstance(payload.get("cheapest_equals_lowest_y"), bool)
    assert payload.get("sparse_frontier") is not None
    for point in landscape + frontier + [payload["cheapest_point"]]:
        assert point["safety_standing"]["status"] in {
            "evaluated", "not_requested", "unavailable",
        }
    assert all(
        point["safety_standing"]["status"] == "not_requested"
        for point in landscape
    )
    x_key = payload.get("x_metric") or "total_cost"
    y_key = payload.get("y_metric") or "emissions"
    spans = payload.get("axis_spans") or {}
    assert set(spans) == {x_key, y_key}
    costs = [float(point[x_key]) for point in landscape]
    emissions = [float(point[y_key]) for point in landscape]
    assert spans[x_key]["min"] == min(costs)
    assert spans[x_key]["max"] == max(costs)
    assert spans[y_key]["min"] == min(emissions)
    assert spans[y_key]["max"] == max(emissions)
    for axis, values in ((x_key, costs), (y_key, emissions)):
        span = spans[axis]
        assert span["min"] <= span["p05"] <= span["p95"] <= span["max"]
        assert span == optimization.axis_span(values)
    slice0 = (payload.get("slices") or [{}])[0]
    assert slice0.get("axis_spans") == spans
    tradeoff = payload.get("frontier_tradeoff")
    if payload.get("cheapest_equals_lowest_y") or n_front < 2:
        assert tradeoff is None
    else:
        assert tradeoff is not None
        assert tradeoff["x_metric"] == x_key
        assert tradeoff["y_metric"] == y_key
        assert tradeoff["x_direction"] == "min"
        assert tradeoff["y_direction"] == "min"
        assert tradeoff["x_units"] == "USD/yr"
        assert tradeoff["y_units"] == "t CO2e/yr"
        assert "incremental_annual_cost_usd" not in tradeoff
        cheapest_x = min(float(point[x_key]) for point in frontier)
        best_y = min(float(point[y_key]) for point in frontier)
        assert tradeoff["x_at_cheapest"] == cheapest_x
        assert tradeoff["y_at_best_y"] == best_y
        assert tradeoff["delta_x"] == tradeoff["x_at_best_y"] - tradeoff["x_at_cheapest"]
        assert tradeoff["delta_y"] == tradeoff["y_at_best_y"] - tradeoff["y_at_cheapest"]
        assert slice0.get("frontier_tradeoff") == tradeoff


def test_pareto_name_retired_both_successors_serve(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        route_handle, _costed = _costed_route_handle(session, monkeypatch)
        residual = dispatch(
            "rank_landscape",
            source="residual_route",
            operation="pareto_dominance",
            handle=route_handle,
        )
        assert residual.get("available") is True
        assert residual["data"]["source"] == "residual_route"
        assert residual["data"]["operation"] == "pareto_dominance"
        assert residual["data"].get("n_frontier_points") >= 1
        c1 = _record_by_label("ldpe-route-c1")
        c2 = _record_by_label("ldpe-route-c2")
        batch = _data(tea.evaluate_process(
            mode="evaluate",
            process_configs=[
                _public_from_record(c1),
                _public_from_record(c2),
            ],
            engine_mode="cache",
        ))
        assert batch.get("success") is True
        batch_handle = store_handle(
            session,
            tool="evaluate_process",
            source_basis="tea_cache_exact",
            data=batch,
        )
        process_rows = dispatch(
            "rank_landscape",
            source="process_rows",
            operation="pareto_dominance",
            handle=batch_handle,
        )
        assert process_rows.get("available") is True
        assert process_rows["data"]["source"] == "process_rows"
        assert process_rows["data"]["operation"] == "pareto_dominance"
        assert process_rows["data"].get("n_frontier_points") >= 1
        old = dispatch("pareto_optimize_stored_route")
        assert old.get("available") is False
        assert old.get("refusal") == "unknown_tool"
    assert callable(optimization.pareto_optimize_stored_route)
    engine = _data(optimization.pareto_optimize_stored_route())
    assert engine.get("error_code") == "invalid_pareto_basis"
    assert engine.get("tool_name") == "pareto_optimize_stored_route"


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
        assert old.get("refusal") == "unknown_tool"
        assert callable(optimization.optimize_stored_route)
        engine = _data(optimization.optimize_stored_route())
        assert engine.get("error_code") == "invalid_optimization_basis"
        assert engine.get("tool_name") == "optimize_stored_route"


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


def test_residual_pareto_quality_matches_fraction_and_sparse_definition():
    one = {"total_cost": 1.0, "emissions": 2.0}
    two = {"total_cost": 3.0, "emissions": 1.0}
    star = optimization._residual_pareto_quality(
        [one, two], [one], "total_cost", "emissions",
    )
    assert star["frontier_fraction"] == 0.5
    assert star["sparse_frontier"] is True
    assert star["cheapest_equals_lowest_y"] is True
    tradeoff = optimization._residual_pareto_quality(
        [one, two], [one, two], "total_cost", "emissions",
    )
    assert tradeoff["frontier_fraction"] == 1.0
    assert tradeoff["cheapest_equals_lowest_y"] is False
    assert tradeoff["sparse_frontier"] is False
    spans = star["axis_spans"]
    assert spans["total_cost"]["min"] == 1.0
    assert spans["total_cost"]["max"] == 3.0
    assert spans["emissions"]["min"] == 1.0
    assert spans["emissions"]["max"] == 2.0
    singleton = optimization._residual_pareto_quality(
        [one], [one], "total_cost", "emissions",
    )
    cost_span = singleton["axis_spans"]["total_cost"]
    assert cost_span["min"] == cost_span["p05"] == cost_span["p95"] == cost_span["max"] == 1.0
    generic = optimization._metric_generic_tradeoff(
        [one, two], "total_cost", "emissions", cheapest_equals_lowest_y=False,
    )
    assert generic is not None
    assert generic["x_metric"] == "total_cost"
    assert generic["y_metric"] == "emissions"
    assert generic["x_at_cheapest"] == 1.0
    assert generic["y_at_cheapest"] == 2.0
    assert generic["x_at_best_y"] == 3.0
    assert generic["y_at_best_y"] == 1.0
    assert generic["delta_x"] == 2.0
    assert generic["delta_y"] == -1.0
    assert generic["x_ratio"] == 3.0
    assert "incremental_annual_cost_usd" not in generic
    assert optimization._metric_generic_tradeoff(
        [one], "total_cost", "emissions", cheapest_equals_lowest_y=False,
    ) is None
    assert optimization._metric_generic_tradeoff(
        [one, two], "total_cost", "emissions", cheapest_equals_lowest_y=True,
    ) is None


def test_residual_route_optimum_omits_frontier_fraction(monkeypatch):
    session = new_session()
    with bind_tool_session(session):
        handle, _costed = _costed_route_handle(session, monkeypatch)
        payload = _data(tea.rank_landscape(
            source="residual_route",
            operation="optimum",
            handle=handle,
        ))
    assert payload.get("success") is True
    assert "frontier_fraction" not in payload
    assert "sparse_frontier" not in payload
    assert "axis_spans" not in payload
    landscape = payload.get("landscape_points") or []
    assert landscape
    assert all(
        point["safety_standing"]["status"] == "not_requested"
        for point in landscape
    )
