"""Feed-scale ranking gaps must be reachable and name complete design points."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest


from dissolve import optimization as O, tea
from dissolve.session import bind_tool_session, current_tool_session, new_session


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    return envelope["data"]


def _plant_feed_scale_gap(session) -> None:
    session["last_tea"] = {
        "analysis_type": "tea_feed_scale_basis_gap",
        "missing_basis_codes": ["probe"],
        "requested_feed_mass_fractions": {"LDPE": 1.0},
    }


def _gap_payload() -> dict:
    session = new_session()
    _plant_feed_scale_gap(session)
    with bind_tool_session(session):
        return _data(O.pareto_optimize_stored_route(
            x_metric="total_cost", y_metric="circularity",
        ))


def test_insufficient_feed_optimization_basis_is_unreachable_without_session():
    assert current_tool_session() is None
    data = _data(O.pareto_optimize_stored_route(
        x_metric="total_cost", y_metric="circularity",
    ))
    assert data.get("error_code") != "insufficient_feed_optimization_basis"
    assert data.get("error_code") != "insufficient_optimization_basis"


def test_insufficient_feed_optimization_basis_is_reachable_from_last_tea():
    data = _gap_payload()
    assert data["error_code"] == "insufficient_feed_optimization_basis"
    assert data["success"] is False


def test_insufficient_feed_optimization_basis_names_complete_design_points():
    data = _gap_payload()
    points = data.get("proposed_design_points")
    assert isinstance(points, list) and points
    for item in points:
        tea._scenario_config(dict(item), require_complete_twelve=True)
    broken = dict(points[0])
    broken.pop(sorted(broken)[0])
    with pytest.raises(Exception):
        tea._scenario_config(broken, require_complete_twelve=True)


def test_evaluate_feed_scale_gap_binds_last_tea_for_the_next_optimizer():
    session = new_session()
    with bind_tool_session(session) as bound:
        raw = tea.evaluate_stored_route_tea_lca(
            feed_mass_fractions={"LDPE": 1.0},
            processing_capacity_mt_per_yr=20_000.0,
            comparison_capacities_mt_per_yr=[10_000.0, 40_000.0],
            energy_case="C1",
        )
        planted = _data(raw)
        assert planted["analysis_type"] == "tea_feed_scale_basis_gap"
        assert bound.last_tea["analysis_type"] == "tea_feed_scale_basis_gap"
        assert bound["last_tea"] is bound.last_tea
        gap = _data(O.pareto_optimize_stored_route(
            x_metric="total_cost", y_metric="circularity",
        ))
    assert gap["error_code"] == "insufficient_feed_optimization_basis"
    assert gap.get("proposed_design_points")


def test_insufficient_optimization_basis_is_reachable_from_candidate_gap():
    session = new_session()
    session["last_tea"] = {
        "analysis_type": "candidate_lca_basis_gap",
        "missing_basis_codes": ["probe"],
        "missing_process_inputs": [],
        "target_product": "LDPE",
        "other_polymers": ["HDPE"],
    }
    session["last_screen_constraints"] = {"minimum_selectivity_points": 5.0}
    session["last_candidates"] = [
        {"solvent": "Toluene", "selectivity_pct": 12.0, "temperature_c": 80.0},
    ]
    session["last_safety"] = [
        {"solvent": "Toluene", "boiling_point_c": 110.6},
    ]
    with bind_tool_session(session):
        data = _data(O.pareto_optimize_stored_route(
            x_metric="emissions", y_metric="selectivity",
        ))
    assert data["error_code"] == "insufficient_optimization_basis"


def test_rank_landscape_does_not_advertise_epsilon():
    annotation = inspect.signature(tea.rank_landscape).parameters[
        "operation"
    ].annotation
    assert "epsilon" not in str(annotation)
