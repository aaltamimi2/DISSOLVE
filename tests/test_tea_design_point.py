"""Fast D-8 regression coverage; the external gate remains the admission bar."""
from __future__ import annotations

import copy
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _path in (str(_ROOT), str(_ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from dissolve import registry, tea, thermodynamics


TARGET_FRACTION = 0.55
EQUAL_FRACTION = 0.50
CAPACITY = 20_000.0
ENERGY_CASE = "C1"
PRECIPITATION_C = 25.0


@dataclass(frozen=True)
class RouteProbe:
    feed: tuple[str, str]
    composition: dict[str, float]
    route: dict


def _data(raw: str) -> dict:
    envelope = json.loads(raw)
    assert set(envelope) == {"display", "data"}
    assert isinstance(envelope["data"], dict)
    return envelope["data"]


def _plan(feed: tuple[str, str], composition: dict[str, float]) -> list[dict]:
    result = _data(registry.BY_NAME["plan_multistage_separation"].fn(
        feed_polymers=list(feed),
        feed_mass_fractions=dict(composition),
        top_k_routes=10,
    ))
    assert result.get("success") is True
    routes = list(result.get("top_k_sequences") or [])
    assert routes, "public planner published no routes"
    return routes


def _route_key(route: dict) -> tuple:
    return (
        tuple(route.get("sequence") or []),
        tuple(
            (
                step.get("dissolved_polymer"),
                step.get("solvent"),
                float(step.get("temperature_c")),
            )
            for step in route.get("steps") or []
        ),
        route.get("final_residue"),
    )


def _one_step_route(
    routes: list[dict], target: str, residue: str,
) -> dict | None:
    return next((
        route for route in routes
        if route.get("complete") is True
        and len(route.get("steps") or []) == 1
        and route["steps"][0].get("dissolved_polymer") == target
        and route.get("final_residue") == residue
    ), None)


def _stage_config(route: dict, target: str, fraction: float) -> dict:
    step = route["steps"][0]
    return tea._scenario_config({
        "target_polymer": target,
        "solvent": step["solvent"],
        "target_mass_percent": 100.0 * fraction,
        "processing_capacity_mt_per_yr": CAPACITY,
        "energy_case": ENERGY_CASE,
        "dissolution_temp_c": step["temperature_c"],
        "precipitation_temp_c": PRECIPITATION_C,
    })


@pytest.fixture(scope="module")
def exact_probe() -> tuple[RouteProbe, RouteProbe]:
    targets = sorted({
        str(record["config"]["target_plastic"])
        for record in tea._records()
        if math.isclose(
            float(record["config"]["target_plastic_percent"]),
            100.0 * TARGET_FRACTION,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and math.isclose(
            float(record["config"]["processing_capacity"]),
            CAPACITY,
            rel_tol=0,
            abs_tol=1e-9,
        )
        and str(record["config"]["energy_case"]).upper() == ENERGY_CASE
        and math.isclose(
            float(record["config"]["precipitation_temperature_c"]),
            PRECIPITATION_C,
            rel_tol=0,
            abs_tol=1e-9,
        )
    })
    for target in targets:
        for residue in sorted(thermodynamics.get_available_polymers()):
            if residue == target:
                continue
            feed = tuple(sorted((target, residue)))
            composition = {
                target: TARGET_FRACTION,
                residue: 1.0 - TARGET_FRACTION,
            }
            route = _one_step_route(_plan(feed, composition), target, residue)
            if route is None:
                continue
            try:
                config = _stage_config(route, target, TARGET_FRACTION)
            except (TypeError, ValueError):
                continue
            if tea._cache_index().get(tea._config_key(config)) is None:
                continue

            equal = {target: EQUAL_FRACTION, residue: EQUAL_FRACTION}
            wanted = _route_key(route)
            equal_route = next((
                item for item in _plan(feed, equal)
                if _route_key(item) == wanted
            ), None)
            assert equal_route is not None, (
                "the exact route was not emitted by the public planner under "
                "the equal-weight counterfactual"
            )
            return (
                RouteProbe(feed, composition, copy.deepcopy(route)),
                RouteProbe(feed, equal, copy.deepcopy(equal_route)),
            )
    pytest.fail("no planner-emitted exact D-8 design-point route was reachable")


@pytest.fixture(scope="module")
def no_pair_probe() -> RouteProbe:
    recorded_targets = {
        str(record["config"]["target_plastic"])
        for record in tea._records()
    }
    polymers = sorted(thermodynamics.get_available_polymers())
    for target in (polymer for polymer in polymers if polymer not in recorded_targets):
        for residue in polymers:
            if residue == target:
                continue
            feed = tuple(sorted((target, residue)))
            composition = {
                target: TARGET_FRACTION,
                residue: 1.0 - TARGET_FRACTION,
            }
            route = _one_step_route(_plan(feed, composition), target, residue)
            if route is not None:
                assert not any(
                    record["config"]["target_plastic"] == target
                    for record in tea._records()
                )
                return RouteProbe(feed, composition, copy.deepcopy(route))
    pytest.fail("no planner-emitted route for a polymer without pair records was reachable")


def _evaluate(monkeypatch, probe: RouteProbe) -> dict:
    calls = 0

    def current_state():
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            last_route=copy.deepcopy(probe.route),
            feed_mass_fractions=dict(probe.composition),
        )

    monkeypatch.setattr(tea, "current_tool_session", current_state)
    result = _data(registry.BY_NAME["evaluate_stored_route_tea_lca"].fn(
        feed_mass_fractions=dict(probe.composition),
        processing_capacity_mt_per_yr=CAPACITY,
        energy_case=ENERGY_CASE,
        precipitation_temperature_c=PRECIPITATION_C,
        engine_mode="cache",
        allow_screening_estimate=False,
    ))
    assert calls > 0, "stored-route TEA did not consume the supplied session route"
    return result


def test_d8_planner_route_exact_design_point_still_costs(
    monkeypatch, exact_probe,
):
    exact, _equal = exact_probe
    result = _evaluate(monkeypatch, exact)

    assert result.get("success") is True
    assert result.get("engine_mode") == "cache"
    assert result.get("cache_match_status") == "exact"
    stages = list(result.get("stage_results") or [])
    assert len(stages) == 1


def test_d8_equal_weight_route_refuses_reference_design_point_substitution(
    monkeypatch, exact_probe,
):
    _exact, equal = exact_probe
    result = _evaluate(monkeypatch, equal)

    assert result.get("success") is False
    assert result.get("error_code") == "route_stage_design_point_unavailable"
    assert result.get("can_cost_route") is False
    assert result.get("can_rank_route") is False
    assert (
        result.get("reference_evidence_role")
        == "context_only_not_route_cost_or_ranking"
    )
    requested = result.get("requested_stage_basis") or {}
    assert requested.get("target_mass_percent") == pytest.approx(50.0)
    assert requested.get("processing_capacity_mt_per_yr") == pytest.approx(CAPACITY)
    assert result.get("available_reference_design_points")
    differences = list(result.get("basis_differences") or [])
    assert differences
    assert any(
        "target_mass_percent" in (item.get("differing_fields") or [])
        for item in differences
    )


def test_d8_no_pair_route_keeps_generic_process_basis_refusal(
    monkeypatch, no_pair_probe,
):
    result = _evaluate(monkeypatch, no_pair_probe)

    assert result.get("success") is False
    assert result.get("error_code") == "uncostable_route_stage"
    for field in (
        "can_cost_route",
        "can_rank_route",
        "reference_evidence_role",
        "requested_stage_basis",
        "available_reference_design_points",
        "basis_differences",
    ):
        assert field not in result
