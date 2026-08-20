"""Planner breadth knobs, origin stamps, handles, and planner_routes rerank.

Screens stay 2^N − N − 1. No silent clamp of m or B. No live BioSTEAM.
"""
from __future__ import annotations

import inspect
import json
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from agent_tools import dispatch, result_read, tool_schemas
from dissolve import registry, tea
from dissolve.cli import CliApp, _parse_breadth_slash
from dissolve.contracts import parse_tool_result
from dissolve.session import bind_tool_session, load_handle, new_session, primary_row_key


_TRIPLE = ["LDPE", "PP", "PS"]
_QUAD = ["LDPE", "HDPE", "PP", "PS"]
_PAIR = ["LDPE", "PP"]
_PLAN = "plan_multistage_separation"


def _data(raw: str) -> dict:
    envelope = parse_tool_result(raw)
    return envelope["data"]


def _plan(**kwargs) -> dict:
    return _data(registry.BY_NAME[_PLAN].fn(**kwargs))


def _first_stage_identities(payload: dict) -> dict[str, set[tuple]]:
    seen: dict[str, set[tuple]] = {}
    for route in payload.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        polymer = step.get("dissolved_polymer")
        if not polymer:
            continue
        seen.setdefault(polymer, set()).add(
            (step.get("solvent"), step.get("temperature_c")),
        )
    return seen


def _ldpe_from_triple(payload: dict) -> dict | None:
    for route in payload.get("top_k_sequences") or []:
        steps = route.get("steps") or []
        if not steps:
            continue
        step = steps[0]
        if step.get("dissolved_polymer") != "LDPE":
            continue
        if set(step.get("retained_polymers") or []) == {"PP", "PS"}:
            return step
    return None


def test_triple_built_in_m1_screens_and_origin():
    payload = _plan(feed_polymers=_TRIPLE)
    assert payload.get("success") is True
    assert payload.get("subset_screens_evaluated") == 4
    assert payload.get("breadth_origin") == "built_in"
    assert payload.get("beam_origin") == "built_in"
    assert payload.get("branch_rule") == "count"
    assert payload.get("breadth") == 1
    assert payload.get("top_k_routes") == 5
    identities = _first_stage_identities(payload)
    assert identities
    assert all(len(items) == 1 for items in identities.values())
    for beam in (1, 6):
        other = _plan(feed_polymers=_TRIPLE, top_k_routes=beam)
        assert other.get("subset_screens_evaluated") == 4
        assert other.get("beam_origin") == "query"
        assert other.get("top_k_routes") == beam


def test_triple_breadth_5_still_four_screens_and_extra_first_stage():
    payload = _plan(feed_polymers=_TRIPLE, breadth=5, branch_rule="count")
    assert payload.get("success") is True
    assert payload.get("subset_screens_evaluated") == 4
    assert payload.get("breadth_origin") == "query"
    assert payload.get("breadth") == 5
    supplied = [
        step.get("stage_branch_supplied")
        for route in payload.get("top_k_sequences") or []
        for step in route.get("steps") or []
        if step.get("stage_branch_supplied") is not None
    ]
    assert supplied and max(supplied) > 1
    for route in payload.get("top_k_sequences") or []:
        for step in route.get("steps") or []:
            value = step.get("stage_branch_supplied")
            if value is None:
                continue
            assert value <= 5
            assert step.get("stage_branch_requested") == 5


def test_ldpe_split_window_keeps_span_relationship():
    tight = _plan(
        feed_polymers=_TRIPLE,
        branch_rule="window",
        selectivity_window_pct=1.8,
        top_k_routes=50,
    )
    wide = _plan(
        feed_polymers=_TRIPLE,
        branch_rule="window",
        selectivity_window_pct=2.0,
        top_k_routes=50,
    )
    assert tight.get("success") is True and wide.get("success") is True
    assert tight.get("subset_screens_evaluated") == wide.get("subset_screens_evaluated") == 4
    tight_step = _ldpe_from_triple(tight)
    wide_step = _ldpe_from_triple(wide)
    assert tight_step is not None and wide_step is not None
    assert tight_step["stage_branch_supplied"] == 4
    assert wide_step["stage_branch_supplied"] == 5
    assert tight_step["window_truncated_to_screen_cap"] is False
    assert wide_step["window_truncated_to_screen_cap"] is False


def test_four_polymer_screens_independent_of_beam():
    narrow = _plan(feed_polymers=_QUAD, top_k_routes=5)
    wide = _plan(feed_polymers=_QUAD, top_k_routes=50)
    assert narrow.get("subset_screens_evaluated") == 11
    assert wide.get("subset_screens_evaluated") == 11
    assert len(wide.get("top_k_sequences") or []) > 10
    assert wide.get("top_k_routes") == 50


def test_refuses_named_cap_and_combination_misses():
    beam = _plan(feed_polymers=_PAIR, top_k_routes=51)
    assert beam.get("success") is False
    assert beam.get("error_code") == "planner_beam_exceeds_cap"
    assert beam.get("cap") == 50
    assert beam.get("requested") == 51

    branch = _plan(feed_polymers=_PAIR, breadth=51)
    assert branch.get("success") is False
    assert branch.get("error_code") == "stage_branch_exceeds_cap"
    assert branch.get("cap") == 50
    assert branch.get("requested") == 51

    invalid = _plan(feed_polymers=_PAIR, breadth=0)
    assert invalid.get("error_code") == "invalid_breadth"

    missing = _plan(feed_polymers=_PAIR, branch_rule="window")
    assert missing.get("error_code") == "missing_selectivity_window"

    mixed = _plan(
        feed_polymers=_PAIR, branch_rule="count", selectivity_window_pct=2.0,
    )
    assert mixed.get("error_code") == "not_applicable_in_branch_rule"

    window_plus_m = _plan(
        feed_polymers=_PAIR,
        branch_rule="window",
        selectivity_window_pct=2.0,
        breadth=3,
    )
    assert window_plus_m.get("error_code") == "not_applicable_in_branch_rule"

    bad_window = _plan(
        feed_polymers=_PAIR, branch_rule="window", selectivity_window_pct=0,
    )
    assert bad_window.get("error_code") == "invalid_selectivity_window"

    bad_rule = _plan(feed_polymers=_PAIR, branch_rule="cluster")
    assert bad_rule.get("error_code") == "invalid_branch_rule"


def test_session_default_origin_and_query_wins():
    session = new_session()
    session["planner_breadth"] = {"branch_rule": "count", "breadth": 5}
    with bind_tool_session(session):
        from_session = _plan(feed_polymers=_TRIPLE)
        from_query = _plan(feed_polymers=_TRIPLE, breadth=1)
    assert from_session.get("breadth_origin") == "session_default"
    assert from_session.get("breadth") == 5
    assert from_query.get("breadth_origin") == "query"
    assert from_query.get("breadth") == 1
    assert from_session.get("subset_screens_evaluated") == 4
    assert from_query.get("subset_screens_evaluated") == 4


def test_two_polymer_plan_issues_handle_and_result_read_pages():
    session = new_session()
    with bind_tool_session(session):
        out = dispatch("plan_multistage_separation", feed_polymers=_PAIR)
        assert out.get("available") is True
        handle = out.get("handle")
        assert handle
        stored = load_handle(session, handle)
        exact = stored["exact"]
        assert primary_row_key(exact) == "steps"
        assert exact.get("top_k_sequences")
        steps_page = result_read(handle=handle)
        routes_page = result_read(handle=handle, page="top_k_sequences")
        unknown = result_read(handle=handle, page="ranked_path_index")
    assert steps_page.get("available") is True
    assert steps_page["data"]["rows"] == exact["steps"][:steps_page["returned"]]
    assert routes_page.get("available") is True
    assert routes_page["total"] == len(exact["top_k_sequences"])
    assert routes_page["data"]["rows"] == exact["top_k_sequences"][:routes_page["returned"]]
    assert unknown.get("available") is False
    assert unknown.get("refusal") == "unknown_handle_page"


def test_compact_keeps_ranked_path_index_on_triple():
    session = new_session()
    with bind_tool_session(session):
        out = dispatch("plan_multistage_separation", feed_polymers=_TRIPLE)
    assert out.get("handle")
    assert "top" in out
    visible = out.get("data") or {}
    assert "ranked_path_index" in visible
    assert isinstance(visible["ranked_path_index"], list)
    assert visible["ranked_path_index"]
    assert "top_k_sequences" not in visible
    stored = load_handle(session, out["handle"])
    assert stored["exact"].get("top_k_sequences")


def test_m1_rerank_refuses_and_m5_keeps_original_thermo_rank(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        parent = dispatch("plan_multistage_separation", feed_polymers=_PAIR)
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        parent_rank = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=parent["handle"],
        )
        branched_rank = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=branched["handle"],
        )
        optimum = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="optimum",
            handle=branched["handle"],
        )
        missing = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            handle=branched["handle"],
        )
    assert parent_rank.get("available") is False
    assert parent_rank.get("refusal") == "rerank_requires_stage_branch"
    assert branched_rank.get("available") is True
    points = (branched_rank.get("data") or {}).get("landscape_points") or []
    assert points
    sort_data = branched_rank.get("data") or {}
    assert sort_data.get("n_returned") == len(points)
    assert sort_data.get("n_returned") == sort_data.get("n_landscape_points")
    originals = [point.get("original_thermo_rank") for point in points]
    assert all(isinstance(item, int) and item >= 1 for item in originals)
    assert [point.get("rank") for point in points] == list(range(1, len(points) + 1))
    assert all(point.get("safety_standing", {}).get("status") == "not_requested" for point in points)
    assert optimum.get("refusal") == "not_applicable_in_source"
    assert missing.get("refusal") == "missing_objective"


def test_planner_routes_pareto_has_f_quality_schema(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=branched["handle"],
        )
        sort_payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=branched["handle"],
        )
    assert payload.get("available") is True
    data = payload.get("data") or {}
    assert data.get("source") == "planner_routes"
    assert data.get("operation") == "pareto_dominance"
    landscape = data.get("landscape_points") or []
    frontier = data.get("frontier_points") or []
    assert landscape
    assert frontier
    n_land = data["n_landscape_points"]
    n_front = data["n_frontier_points"]
    assert n_land == len(landscape)
    assert n_front == len(frontier)
    assert data.get("frontier_fraction") == n_front / n_land
    assert data.get("sparse_frontier") is (
        n_front == 1 or data.get("cheapest_equals_lowest_y") is True
    )
    assert data.get("knee_status") in {
        "endpoint_only_no_interior_knee",
        "interior_tradeoff",
        "not_calculated_no_comparable_designs",
    }
    x_key = "bottleneck_selectivity_pct"
    y_key = "min_stage_g_score"
    assert data.get("x_metric") == x_key
    assert data.get("y_metric") == y_key
    spans = data.get("axis_spans") or {}
    assert set(spans) == {x_key, y_key}
    xs = [float(point[x_key]) for point in landscape]
    ys = [float(point[y_key]) for point in landscape]
    assert spans[x_key]["min"] == min(xs)
    assert spans[x_key]["max"] == max(xs)
    assert spans[y_key]["min"] == min(ys)
    assert spans[y_key]["max"] == max(ys)
    for point in landscape:
        assert point["safety_standing"]["status"] == "not_requested"
        assert point.get("original_thermo_rank")
    cheapest = data.get("cheapest_point") or {}
    assert cheapest.get(x_key) == max(float(point[x_key]) for point in frontier)
    tradeoff = data.get("frontier_tradeoff")
    if data.get("cheapest_equals_lowest_y") or n_front < 2:
        assert tradeoff is None
    else:
        assert tradeoff["x_metric"] == x_key
        assert tradeoff["y_metric"] == y_key
        assert tradeoff["x_direction"] == "max"
        assert tradeoff["y_direction"] == "max"
        assert tradeoff["x_units"] == "percentage_points"
        assert tradeoff["y_units"] == "dimensionless"
        assert "incremental_annual_cost_usd" not in tradeoff
        assert "msp_usd_per_kg" not in (tradeoff.get("x_metric"), tradeoff.get("y_metric"))
    grouping = data.get("grouping") or {}
    assert "polymer_grouping" not in grouping
    plan_exact = load_handle(session, branched["handle"])["exact"]
    assert grouping.get("polymers") == plan_exact.get("polymers")
    assert grouping.get("breadth") == plan_exact.get("breadth")
    assert grouping.get("branch_rule") == (
        plan_exact.get("branch_rule") or "count"
    )
    if "feed_mass_fractions" in plan_exact:
        assert grouping.get("feed_mass_fractions") == plan_exact.get(
            "feed_mass_fractions",
        )
    else:
        assert "feed_mass_fractions" not in grouping
    sort_data = sort_payload.get("data") or {}
    assert "frontier_fraction" not in sort_data
    assert "axis_spans" not in sort_data
    assert "sparse_frontier" not in sort_data
    assert "grouping" not in sort_data
    assert "n_frontier_points" not in sort_data
    assert sort_data.get("n_returned") == len(sort_data.get("landscape_points") or [])
    assert sort_data.get("n_returned") == sort_data.get("n_landscape_points")
    assert "n_returned" not in data


def test_planner_routes_process_rows_locators_are_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    locators = (
        {"target_polymer": "LDPE"},
        {"solvent": "Toluene"},
        {"energy_cases": ["C2"]},
        {"allow_partial_campaign": True},
    )
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        refused = [
            dispatch(
                "rank_landscape",
                source="planner_routes",
                operation="pareto_dominance",
                handle=handle,
                **kwargs,
            )
            for kwargs in locators
        ]
    assert served.get("available") is True
    assert (served.get("data") or {}).get("source") == "planner_routes"
    for kwargs, payload in zip(locators, refused):
        data = payload.get("data") or {}
        assert payload.get("available") is False, kwargs
        assert payload.get("refusal") == "not_applicable_in_source", kwargs
        assert data.get("inapplicable_fields") == list(kwargs), kwargs
        assert data.get("source") == "planner_routes"


def test_planner_routes_residual_kwargs_are_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    residual_kwargs = (
        {"scenario": "B"},
        {"recovery_yield": 0.9},
        {"polymer_market_values_usd_per_mt": {"LDPE": 100.0}},
        {"solver_name": "scip"},
        {"composition_slices": [{"LDPE": 0.55, "PP": 0.45}]},
    )
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        refused = [
            dispatch(
                "rank_landscape",
                source="planner_routes",
                operation="pareto_dominance",
                handle=handle,
                **kwargs,
            )
            for kwargs in residual_kwargs
        ]
    assert served.get("available") is True
    for kwargs, payload in zip(residual_kwargs, refused):
        data = payload.get("data") or {}
        assert payload.get("available") is False, kwargs
        assert payload.get("refusal") == "not_applicable_in_source", kwargs
        assert data.get("inapplicable_fields") == list(kwargs), kwargs
        assert data.get("source") == "planner_routes"


def test_planner_routes_objective_on_pareto_is_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        handle = branched["handle"]
        served = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
        )
        sorted_paths = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="sort",
            objective="min_stage_g_score",
            handle=handle,
        )
        refused = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=handle,
            objective="min_stage_g_score",
        )
    assert served.get("available") is True
    assert sorted_paths.get("available") is True
    data = refused.get("data") or {}
    assert refused.get("available") is False
    assert refused.get("refusal") == "not_applicable_in_source"
    assert data.get("inapplicable_fields") == ["objective"]
    assert data.get("source") == "planner_routes"
    assert data.get("operation") == "pareto_dominance"


def test_planner_routes_mixed_polymer_grouping_is_not_applicable(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("planner_routes must not start BioSTEAM")

    monkeypatch.setattr(tea, "_live", forbidden)
    monkeypatch.setattr(tea.tea_worker, "run", forbidden)
    session = new_session()
    with bind_tool_session(session):
        branched = dispatch(
            "plan_multistage_separation",
            feed_polymers=_PAIR,
            breadth=5,
        )
        payload = dispatch(
            "rank_landscape",
            source="planner_routes",
            operation="pareto_dominance",
            handle=branched["handle"],
            polymer_grouping="mixed_polymer",
        )
    data = payload.get("data") or {}
    assert payload.get("available") is False
    assert payload.get("refusal") == "not_applicable_in_source"
    assert data.get("inapplicable_fields") == ["polymer_grouping"]
    assert data.get("source") == "planner_routes"


def test_planner_pareto_quality_star_is_sparse_and_null_tradeoff():
    better_x = {
        "bottleneck_selectivity_pct": 90.0,
        "min_stage_g_score": 5.0,
    }
    better_y = {
        "bottleneck_selectivity_pct": 80.0,
        "min_stage_g_score": 8.0,
    }
    star = tea._planner_pareto_quality(
        [better_x, better_y], [better_x],
        "bottleneck_selectivity_pct", "min_stage_g_score",
    )
    assert star["frontier_fraction"] == 0.5
    assert star["sparse_frontier"] is True
    assert star["frontier_tradeoff"] is None
    trade = tea._planner_pareto_quality(
        [better_x, better_y], [better_x, better_y],
        "bottleneck_selectivity_pct", "min_stage_g_score",
    )
    assert trade["sparse_frontier"] is False
    assert trade["frontier_tradeoff"]["x_at_cheapest"] == 90.0
    assert trade["frontier_tradeoff"]["y_at_best_y"] == 8.0
    assert trade["frontier_tradeoff"]["delta_x"] == -10.0
    assert trade["frontier_tradeoff"]["delta_y"] == 3.0
    assert trade["frontier_tradeoff"]["y_direction"] == "max"


def test_registry_still_two_public_tea_names_and_planner_is_not_new():
    names = {spec["name"] for spec in tool_schemas()}
    assert "evaluate_process" in names
    assert "rank_landscape" in names
    assert "plan_multistage_separation" in names
    assert "plan_then_tea" not in names
    props = next(
        spec["parameters"]["properties"]
        for spec in tool_schemas()
        if spec["name"] == "plan_multistage_separation"
    )
    assert "breadth" in props
    assert "branch_rule" in props
    assert "selectivity_window_pct" in props
    page = next(
        spec["parameters"]["properties"]
        for spec in tool_schemas()
        if spec["name"] == "result_read"
    )
    assert set(page["page"]["enum"]) == {"steps", "top_k_sequences"}


def test_slash_breadth_sets_and_clear_drops(tmp_path, monkeypatch):
    monkeypatch.setenv("META_MUSE_API_KEY", "test-key")
    from rich.console import Console
    import io
    console = Console(file=io.StringIO(), force_terminal=True, width=80, color_system=None)
    app = CliApp(
        session_id="breadth-session",
        store_root=tmp_path,
        console=console,
    )
    assert app.handle_command("/breadth 5") is False
    assert app.session.get("planner_breadth") == {
        "branch_rule": "count", "breadth": 5,
    }
    assert app.handle_command("/breadth window 1.8") is False
    assert app.session["planner_breadth"]["branch_rule"] == "window"
    assert math.isclose(float(app.session["planner_breadth"]["selectivity_window_pct"]), 1.8)
    assert app.handle_command("/breadth all") is False
    assert app.session["planner_breadth"]["breadth"] == "all"
    assert app.handle_command("/breadth nope") is False
    assert app.session["planner_breadth"]["breadth"] == "all"
    assert app.handle_command("/clear") is False
    assert "planner_breadth" not in app.session
    assert "handle_command" not in inspect.getsource(app.ask)
    assert _parse_breadth_slash([]) is None
    assert _parse_breadth_slash(["3"])["breadth"] == 3
