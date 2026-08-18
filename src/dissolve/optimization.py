"""Route-constrained pathway optimization over sourced process evidence."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
import shutil
import subprocess
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version as package_version
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from .contracts import tool_error, tool_success
from . import tea_contracts
from .session import (
    candidate_evidence, current_tool_session,
    resolve_candidate_argument,
)

_ASSET = Path(str(files("dissolve").joinpath("data/optimization.json.gz")))
_ASSET_SHA256 = "ffa6141a23ef0364f9d8e7734399922a71fc421e3a581105b7b4da75370b1258"
_DIRECTIONS = {
    "total_cost": "min", "emissions": "min", "profit": "max",
    "circularity": "max",
}
_OBJECTIVES = {
    "min_cost": ("total_cost", "min"),
    "min_emissions": ("emissions", "min"),
    "max_profit": ("profit", "max"),
    "max_circularity": ("circularity", "max"),
}
_TECHNOLOGY_LABELS = {
    "lf": "Landfill", "we": "Waste-to-energy", "py": "Pyrolysis",
    "gas_er": "Gasification with energy recovery",
    "gas_h2": "Gasification to hydrogen",
    "gas_h2cc": "Gasification to hydrogen with carbon capture and storage",
}
Objective = Literal["max_profit", "min_emissions", "min_cost", "max_circularity"]
Metric = Literal["total_cost", "emissions", "profit", "circularity", "selectivity"]
Scenario = Literal["A", "B"]
SolverName = Literal["scip", "appsi_highs", "highs"]


@lru_cache(maxsize=1)
def asset_payload() -> dict[str, Any]:
    raw = _ASSET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _ASSET_SHA256:
        raise RuntimeError(f"Optimization asset checksum mismatch: {digest}")
    return json.loads(gzip.decompress(raw))


def _number(value: Any, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _scenario_key(value: Any) -> str:
    token = " ".join(str(value or "B").strip().casefold().replace("_", " ").split())
    aliases = {"a": "A", "scenario a": "A", "b": "B", "scenario b": "B"}
    if token not in aliases:
        raise ValueError("scenario must be A or B")
    return aliases[token]


def _metric_key(value: Any) -> str:
    token = " ".join(str(value or "").strip().casefold().replace("_", " ").split())
    aliases = {
        "cost": "total_cost", "total cost": "total_cost",
        "annual cost": "total_cost", "total annual cost": "total_cost",
        "emission": "emissions", "emissions": "emissions",
        "profit": "profit", "annual profit": "profit",
        "circularity": "circularity", "circularity proxy": "circularity",
        "selectivity": "selectivity", "selectivity margin": "selectivity",
    }
    if token not in aliases:
        raise ValueError(
            "Metric must be total_cost, emissions, profit, circularity, or selectivity"
        )
    return aliases[token]


def _objective_key(value: Any) -> str:
    token = " ".join(str(value or "max_profit").strip().casefold().replace("_", " ").split())
    aliases = {
        "max profit": "max_profit", "maximize profit": "max_profit",
        "min emissions": "min_emissions", "minimize emissions": "min_emissions",
        "min cost": "min_cost", "minimize cost": "min_cost",
        "min total cost": "min_cost", "max circularity": "max_circularity",
        "maximize circularity": "max_circularity",
    }
    if token not in aliases:
        raise ValueError("Unsupported objective")
    return aliases[token]


def _solver_key(value: Any) -> str:
    token = str(value or "scip").strip().casefold().replace("-", "_")
    aliases = {
        "scip": "scip", "highs": "highs", "appsi_highs": "appsi_highs",
    }
    if token not in aliases:
        raise ValueError("solver_name must be scip, highs, or appsi_highs")
    return aliases[token]


def _composition(values: dict[str, Any]) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError("A feed composition is required")
    result = {str(key): _number(value, f"fraction for {key}") for key, value in values.items()}
    total = sum(result.values())
    if total > 1.000001:
        if abs(total - 100.0) > 0.01:
            raise ValueError("Feed percentages must sum to 100")
        result = {key: value / 100.0 for key, value in result.items()}
    elif abs(total - 1.0) > 0.0001:
        raise ValueError("Feed mass fractions must sum to 1")
    if any(value <= 0 for value in result.values()):
        raise ValueError("Every feed mass fraction must be positive")
    return result


def _source_state() -> dict[str, Any]:
    state = current_tool_session()
    route = copy.deepcopy(getattr(state, "last_route", None)) if state else None
    tea = copy.deepcopy(getattr(state, "last_tea", None)) if state else None
    if not route or not route.get("complete"):
        raise ValueError("A complete stored separation route is required")
    if not tea or tea.get("route_source") != "typed_session_state":
        raise ValueError("A route-backed stored TEA/LCA result is required")
    rows = list(tea.get("comparison_rows") or [])
    if not rows or any(row.get("stage") is None for row in rows):
        raise ValueError("Stored TEA/LCA state lacks stage metrics")
    expected_signature = tea_contracts.route_evidence_signature(route)
    if tea.get("route_signature") != expected_signature:
        raise ValueError("Stored TEA/LCA state is stale for the current route")
    route_steps = list(route.get("steps") or [])
    ordered_rows = sorted(rows, key=lambda row: int(row["stage"]))
    if len(route_steps) != len(ordered_rows) or any(
        (
            str(step.get("dissolved_polymer")), str(step.get("solvent")),
            float(step.get("temperature_c")),
        ) != (
            str(row.get("polymer")), str(row.get("solvent")),
            float(row.get("dissolution_temperature_c")),
        )
        for step, row in zip(route_steps, ordered_rows)
    ):
        raise ValueError("Stored TEA/LCA stages no longer match the current route")
    composition = _composition(tea.get("feed_mass_fractions") or {})
    route_polymers = {
        str(step.get("dissolved_polymer")) for step in route.get("steps") or []
    } | {str(route.get("final_residue"))}
    if set(composition) != route_polymers:
        raise ValueError("Stored TEA composition no longer matches the stored route")
    return {
        "route": route, "tea": tea, "stages": ordered_rows,
        "composition": composition,
        "feed_mt_per_yr": _number(tea.get("processing_capacity_mt_per_yr"), "processing capacity"),
    }


def _stored_optimization_gap(x_metric: str, y_metric: str) -> str | None:
    state = current_tool_session()
    prior = dict(getattr(state, "last_tea", None) or {}) if state else {}
    if prior.get("analysis_type") == "tea_feed_scale_basis_gap":
        tool = "pareto_optimize_stored_route"
        if {x_metric, y_metric} != {"total_cost", "circularity"}:
            return tool_error(
                tool,
                "The unresolved feed-scale basis supports only a cost-versus-circularity basis-gap assessment.",
                error_code="invalid_pareto_basis",
            )
        return tool_error(
            tool,
            "No cost-versus-circularity frontier or knee can be calculated without comparable route-backed designs.",
            error_code="insufficient_feed_optimization_basis",
            analysis_type="feed_optimization_basis_gap",
            can_optimize=False, can_build_frontier=False,
            requested_objective="cost_vs_circularity",
            x_metric=x_metric, y_metric=y_metric,
            requested_feed_mass_fractions=dict(
                prior.get("requested_feed_mass_fractions") or {}
            ),
            unresolved_polymer_identities=list(
                prior.get("unresolved_polymer_identities") or []
            ),
            supported_interpretations=dict(prior.get("supported_interpretations") or {}),
            requested_scale_capacities_mt_per_yr=list(
                prior.get("requested_scale_capacities_mt_per_yr") or []
            ),
            available_cache_energy_cases=list(
                prior.get("available_cache_energy_cases") or []
            ),
            energy_case_descriptions=dict(prior.get("energy_case_descriptions") or {}),
            conditional_cache_coverage=dict(prior.get("conditional_cache_coverage") or {}),
            cache_capacity_ranges_mt_per_yr=dict(
                prior.get("cache_capacity_ranges_mt_per_yr") or {}
            ),
            n_frontier_points=0, knee_status="not_calculated_no_comparable_designs",
            missing_basis_codes=[
                *list(prior.get("missing_basis_codes") or []),
                "route_backed_scale_energy_landscape", "circularity_basis",
                "recovery_residual_pathway_basis",
            ],
            missing_process_inputs=[
                "Resolve the PE grade and define a complete solvent/setpoint separation route.",
                "Evaluate one common route-backed cost basis at every capacity and energy case.",
                "Define recovery, product basis, circularity metric, and residual pathway assumptions.",
            ],
            warnings=[
                "Conditional stage-cache coverage is not a cost or circularity design point.",
                "No optimum, Pareto point, knee, solvent selection, or frontier artifact was calculated.",
            ],
        )
    if prior.get("analysis_type") != "candidate_lca_basis_gap":
        return None
    tool = "pareto_optimize_stored_route"
    if {x_metric, y_metric} != {"emissions", "selectivity"}:
        return tool_error(tool, "The stored candidate screen supports only an emissions-versus-selectivity basis-gap assessment.", error_code="invalid_pareto_basis")
    floor = (getattr(state, "last_screen_constraints", None) or {}).get("minimum_selectivity_points")
    # Shape selection belongs to the harness. The optimization engine accepts
    # bound rows only when the selectivity fields below establish its basis.
    candidates, _ = candidate_evidence(state)
    if floor is None or not candidates or any(row.get("selectivity_pct") is None for row in candidates):
        return tool_error(tool, "The stored screen lacks a selectivity floor or candidate values.", error_code="invalid_pareto_basis")
    safety = {
        str(row.get("solvent") or "").casefold(): row
        for row in getattr(state, "last_safety", None) or []
    }
    rows = []
    for candidate in candidates:
        solvent, temperature = str(candidate.get("solvent") or ""), candidate.get("temperature_c")
        boiling, selectivity = safety.get(solvent.casefold(), {}).get("boiling_point_c"), float(candidate["selectivity_pct"])
        rows.append({"solvent": solvent, "temperature_c": temperature,
            "selectivity_percentage_points": selectivity, "meets_selectivity_floor": selectivity >= float(floor),
            "boiling_point_c": boiling, "atmospheric_feasible": (
                float(temperature) < float(boiling) if temperature is not None and boiling is not None else None)})
    qualifying = [row["solvent"] for row in rows if row["meets_selectivity_floor"]]
    atmospheric = sum(row["meets_selectivity_floor"] and row["atmospheric_feasible"] is True for row in rows)
    return tool_error(
        tool,
        "No defensible emissions optimization or Pareto frontier can be calculated from the stored candidate screen.",
        error_code="insufficient_optimization_basis",
        analysis_type="optimization_basis_gap", can_optimize=False,
        can_build_frontier=False, requested_objective="min_emissions",
        x_metric=x_metric, y_metric=y_metric, constraint_metric="selectivity",
        minimum_selectivity_points=float(floor), constraint_unit="percentage_points",
        target_product=prior.get("target_product"), other_polymers=list(prior.get("other_polymers") or []),
        temperature_min_c=getattr(state, "temperature_min_c", None), temperature_max_c=getattr(state, "temperature_max_c", None),
        candidate_conditions=rows, qualifying_candidates=qualifying,
        atmospheric_qualifying_candidate_count=atmospheric,
        missing_basis_codes=[
            *list(prior.get("missing_basis_codes") or []),
            "comparable_candidate_gwp", "complete_process_route",
            "feed_composition", "process_capacity", "decision_variables",
        ],
        missing_process_inputs=list(prior.get("missing_process_inputs") or []) +
        ["Define a complete process route, feed composition, capacity, recovery, and decision variables."],
        warnings=["No candidate GWP, emissions winner, Pareto point, or frontier artifact was calculated.",
                  "The selectivity floor is a screening heuristic; modeled solution concentrations are not recovery or purity."],
    )


def _stored_point_optimization_gap(objective: str) -> str | None:
    """Preserve an uncostable product-portfolio decision without defaulting it."""
    state = current_tool_session()
    prior = dict(getattr(state, "last_tea", None) or {}) if state else {}
    if prior.get("analysis_type") != "tea_route_portfolio_basis_gap":
        return None
    missing_codes = list(dict.fromkeys([
        *list(prior.get("missing_basis_codes") or []),
        "route_backed_product_economics", "polymer_market_values",
        "recovery_residual_pathway_basis", "landfill_cost_emissions_basis",
    ]))
    return tool_error(
        "optimize_stored_route",
        "No max-profit recovery or landfill allocation can be calculated from the uncosted product-portfolio basis.",
        error_code="insufficient_portfolio_optimization_basis",
        analysis_type="portfolio_optimization_basis_gap",
        can_optimize=False, can_select_recovery_portfolio=False,
        can_assign_landfill=False, profit_calculated=False,
        requested_objective=objective,
        requested_product_count=prior.get("requested_product_count"),
        product_selection_basis=prior.get("product_selection_basis"),
        route_products=list(prior.get("route_products") or []),
        target_products=list(prior.get("target_products") or []),
        requested_capacity_mt_per_yr=prior.get("requested_capacity_mt_per_yr"),
        requested_capacity_basis=prior.get("requested_capacity_basis"),
        selected_recovered_polymers=[], selected_landfilled_polymers=[],
        n_landscape_points=0, missing_basis_codes=missing_codes,
        missing_process_inputs=[
            *list(prior.get("missing_process_inputs") or []),
            "Define polymer-specific residual or landfill costs, emissions, and constraints on a common route-backed basis.",
        ],
        warnings=[
            "Thermodynamic route order and solubility do not establish product value, profit, recovery, or landfill allocation.",
            "No optimum, recovered-polymer set, landfilled-polymer set, profit, or design point was calculated.",
        ],
    )


def _technology_rows(scenario: str) -> dict[str, dict[str, float]]:
    key = _scenario_key(scenario)
    rows = (asset_payload().get("technology_scenarios") or {}).get(key)
    if not rows:
        raise ValueError("scenario must be A or B")
    return rows


def _usable_economics(point: dict[str, Any]) -> bool:
    """P1.3: exclude zero-capital anchors from an executable landscape."""
    return (
        float(point.get("capital_cost") or 0.0) > 0.0
        and (
            float(point.get("operational_cost") or 0.0) > 0.0
            or float(point.get("emissions") or 0.0) > 0.0
        )
    )


def _market_values(
    overrides: Optional[dict[str, float]], polymers: Sequence[str] = (),
) -> dict[str, float]:
    defaults = dict((asset_payload().get("policy") or {}).get("default_market_values_usd_per_mt") or {})
    for polymer, value in (overrides or {}).items():
        number = _number(value, f"market value for {polymer}")
        if number < 0:
            raise ValueError("Polymer market values must be nonnegative")
        defaults[str(polymer)] = number
    return {
        polymer: float(defaults.get(polymer, 0.0)) for polymer in polymers
    } if polymers else defaults


def _landscape(
    source: dict[str, Any], *, scenario: str, recovery_yield: float,
    market_values: dict[str, float], composition: Optional[dict[str, float]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    composition = composition or source["composition"]
    feed = source["feed_mt_per_yr"]
    stages = source["stages"]
    technologies = _technology_rows(scenario)
    policy = asset_payload().get("policy") or {}
    distances = policy.get("distances_mile") or {}
    diversion = policy.get("residual_diversion_factors") or {}
    fixed_transport = float(policy.get("transport_fixed_usd_per_mt") or 0.0)
    variable_transport = float(policy.get("transport_variable_usd_per_mt_mile") or 0.0)
    all_points: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for active_count in range(len(stages) + 1):
        active = stages[:active_count]
        for technology, tech in sorted(technologies.items()):
            recovered_by_polymer = {
                str(stage["polymer"]): feed * composition[str(stage["polymer"])] * recovery_yield
                for stage in active
            }
            recovered = sum(recovered_by_polymer.values())
            residual = max(0.0, feed - recovered)
            stage_tci = sum(float(stage["tci_usd"]) for stage in active)
            allocation_years = int(policy["strap_capital_allocation_years"])
            # The workbook's residual-technology CAPEX coefficients are already
            # USD/yr. BioSTEAM TCI is installed USD and follows the v10 adapter's
            # explicit straight-line allocation before entering an annual objective.
            annual_capital = stage_tci / allocation_years + float(tech["capex"])
            operating = sum(float(stage["aoc_usd_per_yr"]) for stage in active) + residual * float(tech["opex"])
            transport = residual * (
                fixed_transport + variable_transport * float(distances.get(technology, 0.0))
            )
            emissions = (
                sum(
                    float(stage["gwp_kg_co2e_per_kg"]) * recovered_by_polymer[str(stage["polymer"])]
                    for stage in active
                )
                + residual * float(tech["gwp"])
            )
            energy = (
                sum(
                    float(stage.get("total_energy_mj_per_kg") or 0.0)
                    * recovered_by_polymer[str(stage["polymer"])] * 1000.0
                    for stage in active
                )
                + residual * float(tech["total_energy"])
            )
            revenue = sum(
                mass * float(market_values.get(polymer, 0.0))
                for polymer, mass in recovered_by_polymer.items()
            )
            total_cost = annual_capital + operating + transport
            circularity = (recovered + residual * float(diversion.get(technology, 0.0))) / feed
            selected_polymers = [str(stage["polymer"]) for stage in active]
            point = {
                "design_id": f"r{active_count}-{technology}",
                "active_recovery_stages": active_count,
                "recovered_polymers": selected_polymers,
                "recovered_mass_mt_per_yr_by_polymer": recovered_by_polymer,
                "residual_polymers": [name for name in composition if name not in selected_polymers],
                "residual_mass_mt_per_yr": residual,
                "residual_technology": technology,
                "residual_technology_label": _TECHNOLOGY_LABELS[technology],
                "stage_conditions": [{
                    "stage": stage["stage"], "polymer": stage["polymer"],
                    "solvent": stage["solvent"],
                    "temperature_c": stage["dissolution_temperature_c"],
                } for stage in active],
                "stage_tci_usd": stage_tci,
                "capital_cost": annual_capital, "operational_cost": operating,
                "transportation_cost": transport, "total_cost": total_cost,
                "emissions": emissions, "energy_mj_per_yr": energy,
                "revenue": revenue, "profit": revenue - total_cost,
                "circularity": circularity,
                "economics_usable": False,
            }
            point["economics_usable"] = _usable_economics(point)
            (all_points if point["economics_usable"] else rejected).append(point)
    for index, point in enumerate(all_points, 1):
        point["landscape_point_id"] = index
    return all_points, rejected


def _dominates(candidate: dict[str, Any], baseline: dict[str, Any], x: str, y: str) -> bool:
    comparisons = []
    strict = False
    for metric in (x, y):
        first, second = float(candidate[metric]), float(baseline[metric])
        better = first <= second if _DIRECTIONS[metric] == "min" else first >= second
        comparisons.append(better)
        strict = strict or (first < second if _DIRECTIONS[metric] == "min" else first > second)
    return all(comparisons) and strict


def _pareto_sweep(
    landscape: Sequence[dict[str, Any]], x_metric: str, y_metric: str,
) -> list[dict[str, Any]]:
    """One dominance template for all supported objective pairs (P3.2)."""
    if x_metric not in _DIRECTIONS or y_metric not in _DIRECTIONS or x_metric == y_metric:
        raise ValueError("Choose two distinct metrics from total_cost, emissions, profit, circularity")
    frontier = [
        point for point in landscape
        if not any(
            other is not point and _dominates(other, point, x_metric, y_metric)
            for other in landscape
        )
    ]
    reverse = _DIRECTIONS[x_metric] == "max"
    frontier.sort(key=lambda point: float(point[x_metric]), reverse=reverse)
    result = []
    for index, point in enumerate(frontier, 1):
        copied = copy.deepcopy(point)
        copied.update({"point_id": index, "point_status": "frontier", "is_frontier": True})
        result.append(copied)
    return result


def _knee(frontier: Sequence[dict[str, Any]], x: str, y: str) -> Optional[dict[str, Any]]:
    if not frontier:
        return None
    if len(frontier) == 1:
        return copy.deepcopy(frontier[0])
    values = {metric: [float(point[metric]) for point in frontier] for metric in (x, y)}

    def loss(metric: str, value: float) -> float:
        low, high = min(values[metric]), max(values[metric])
        if high - low <= 1e-12:
            return 0.0
        return (
            (value - low) / (high - low)
            if _DIRECTIONS[metric] == "min" else (high - value) / (high - low)
        )

    return copy.deepcopy(min(
        frontier,
        key=lambda point: math.hypot(loss(x, float(point[x])), loss(y, float(point[y]))),
    ))


def _cost_emissions_tradeoff(
    frontier: Sequence[dict[str, Any]], x_metric: str, y_metric: str,
) -> Optional[dict[str, Any]]:
    """Return decision arithmetic for a cost/emissions frontier without choosing a preference."""
    if {x_metric, y_metric} != {"total_cost", "emissions"} or len(frontier) < 2:
        return None
    cheapest = min(frontier, key=lambda point: float(point["total_cost"]))
    lower_emissions = min(frontier, key=lambda point: float(point["emissions"]))
    cost_increase = float(lower_emissions["total_cost"]) - float(cheapest["total_cost"])
    emissions_reduction = float(cheapest["emissions"]) - float(lower_emissions["emissions"])
    if cheapest is lower_emissions or cost_increase <= 0 or emissions_reduction <= 0:
        return None
    return {
        "cheapest_design_id": cheapest["design_id"],
        "lower_emissions_design_id": lower_emissions["design_id"],
        "incremental_annual_cost_usd": cost_increase,
        "annual_emissions_reduction_t_co2e": emissions_reduction,
        "emissions_reduction_percent": (
            100.0 * emissions_reduction / float(cheapest["emissions"])
        ),
        "incremental_cost_usd_per_t_co2e_avoided": cost_increase / emissions_reduction,
        "selected_to_cheapest_cost_ratio": (
            float(lower_emissions["total_cost"]) / float(cheapest["total_cost"])
        ),
    }


def _solver_version(name: str) -> Optional[str]:
    if name in {"appsi_highs", "highs"}:
        try:
            return f"highspy {package_version('highspy')}"
        except PackageNotFoundError:
            return None
    executable = shutil.which(name)
    if not executable:
        return None
    try:
        line = subprocess.run(
            [executable, "--version"], capture_output=True, text=True,
            timeout=10, check=False,
        ).stdout.splitlines()[0]
        return line.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _verify_point(
    landscape: Sequence[dict[str, Any]], objective: str, solver_name: str,
) -> dict[str, Any]:
    version = _solver_version(solver_name)
    try:
        import pyomo.environ as pyo
    except ImportError:
        return {"status": "skipped", "reason": "pyomo_not_installed", "solver": solver_name, "version": version}
    solver = pyo.SolverFactory(solver_name)
    if not solver.available(False):
        return {"status": "skipped", "reason": "solver_unavailable", "solver": solver_name, "version": version}
    metric, direction = _OBJECTIVES[objective]
    model = pyo.ConcreteModel("DISSOLVE_route_landscape")
    model.designs = pyo.RangeSet(0, len(landscape) - 1)
    model.choose = pyo.Var(model.designs, domain=pyo.Binary)
    model.one = pyo.Constraint(expr=sum(model.choose[index] for index in model.designs) == 1)
    expression = sum(float(landscape[index][metric]) * model.choose[index] for index in model.designs)
    model.objective = pyo.Objective(
        expr=expression, sense=pyo.minimize if direction == "min" else pyo.maximize,
    )
    result = solver.solve(model, tee=False)
    selected = [index for index in model.designs if pyo.value(model.choose[index]) > 0.5]
    termination = str(result.solver.termination_condition)
    return {
        "status": "verified" if len(selected) == 1 else "failed",
        "reason": None if len(selected) == 1 else "no_unique_design",
        "solver": solver_name, "version": version,
        "termination_condition": termination,
        "selected_design_id": landscape[selected[0]]["design_id"] if len(selected) == 1 else None,
    }


def _deterministic_optimum(landscape: Sequence[dict[str, Any]], objective: str) -> dict[str, Any]:
    metric, direction = _OBJECTIVES[objective]
    return copy.deepcopy(
        min(landscape, key=lambda point: float(point[metric]))
        if direction == "min" else max(landscape, key=lambda point: float(point[metric]))
    )


def _payload_path(kind: str, payload: dict[str, Any]) -> str:
    root = Path(os.getenv("DISSOLVE_OUTPUT_DIR") or Path.cwd() / "plots") / "optimization_payloads"
    root.mkdir(parents=True, exist_ok=True)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    path = (root / f"{kind}_{hashlib.sha256(canonical.encode()).hexdigest()[:12]}.json").resolve()
    path.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return str(path)


def optimize_stored_route(
    objective: Objective = "max_profit",
    scenario: Scenario = "B",
    recovery_yield: Optional[float] = None,
    polymer_market_values_usd_per_mt: Optional[dict[str, float]] = None,
    solver_name: SolverName = "scip",
) -> str:
    """Select one route/residual design for cost, emissions, profit, or circularity."""
    tool = "optimize_stored_route"
    try:
        objective = _objective_key(objective)
    except ValueError:
        return tool_error(tool, "Unsupported objective.", error_code="unsupported_objective", supported_objectives=sorted(_OBJECTIVES))
    inherited_gap = _stored_point_optimization_gap(objective)
    if inherited_gap is not None:
        return inherited_gap
    try:
        scenario_key = _scenario_key(scenario)
        solver_key = _solver_key(solver_name)
        source = _source_state()
        recovery = _number(
            recovery_yield if recovery_yield is not None else
            (asset_payload().get("policy") or {}).get("default_recovery_yield"),
            "recovery_yield",
        )
        if not 0 < recovery <= 1:
            raise ValueError("recovery_yield must be above 0 and at most 1")
        values = _market_values(
            polymer_market_values_usd_per_mt, tuple(source["composition"]),
        )
        landscape, rejected = _landscape(
            source, scenario=scenario_key, recovery_yield=recovery, market_values=values,
        )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_optimization_basis")
    if not landscape:
        return tool_error(tool, "No design has usable economics.", error_code="no_usable_designs")
    selected = _deterministic_optimum(landscape, objective)
    verification = _verify_point(landscape, objective, solver_key)
    verification["agrees_with_deterministic_optimum"] = (
        verification.get("selected_design_id") == selected["design_id"]
        if verification.get("status") == "verified" else None
    )
    return tool_success(
        tool, analysis_type="point_optimum", objective=objective,
        source_route="typed_session_state", source_route_signature=source["tea"].get("route_signature"),
        source_tea_engine_mode=source["tea"].get("engine_mode"),
        feed_mass_fractions=source["composition"], feed_mt_per_yr=source["feed_mt_per_yr"],
        scenario=scenario_key, recovery_yield=recovery,
        polymer_market_values_usd_per_mt=values,
        capital_allocation_years=int((asset_payload().get("policy") or {})["strap_capital_allocation_years"]),
        residual_product_revenue_included=False,
        selected_point=selected, n_landscape_points=len(landscape),
        n_rejected_phantom_designs=len(rejected), solver=verification,
        economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
        metric_units={
            "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
            "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
            "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
            "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
            "circularity": "mass-diversion fraction",
        },
        circularity_basis=(asset_payload().get("policy") or {}).get("circularity_note"),
        provenance={
            "asset_sha256": _ASSET_SHA256,
            **(asset_payload().get("provenance") or {}),
        },
        warnings=[
            "This is a route-constrained superstructure screen, not a final integrated-facility design.",
            "Recovery yield and market values are explicit optimization assumptions, not measured route performance.",
            "BioSTEAM TCI is allocated over 10 years without discounting; residual-technology CAPEX is sourced in USD/yr.",
            "Residual-technology product revenue is not credited in this screen.",
            "Circularity is a mass-diversion screening proxy, not a validated circularity assessment.",
        ],
    )


def pareto_optimize_stored_route(
    x_metric: Metric = "total_cost",
    y_metric: Metric = "emissions",
    scenario: Scenario = "B",
    recovery_yield: Optional[float] = None,
    polymer_market_values_usd_per_mt: Optional[dict[str, float]] = None,
    composition_slices: Optional[list[dict[str, float]]] = None,
    solver_name: SolverName = "scip",
) -> str:
    """Build a route Pareto frontier or expose an inherited candidate-basis gap."""
    tool = "pareto_optimize_stored_route"
    try:
        x_metric, y_metric = _metric_key(x_metric), _metric_key(y_metric)
        candidate_gap = _stored_optimization_gap(x_metric, y_metric)
        if candidate_gap is not None:
            return candidate_gap
        if "selectivity" in {x_metric, y_metric}:
            raise ValueError("selectivity is not a route-landscape metric")
        scenario_key = _scenario_key(scenario)
        solver_key = _solver_key(solver_name)
        source = _source_state()
        recovery = _number(
            recovery_yield if recovery_yield is not None else
            (asset_payload().get("policy") or {}).get("default_recovery_yield"),
            "recovery_yield",
        )
        if not 0 < recovery <= 1:
            raise ValueError("recovery_yield must be above 0 and at most 1")
        values = _market_values(
            polymer_market_values_usd_per_mt, tuple(source["composition"]),
        )
        slices = composition_slices or [source["composition"]]
        normalized_slices = [_composition(item) for item in slices]
        if any(set(item) != set(source["composition"]) for item in normalized_slices):
            raise ValueError("Every composition slice must cover the stored-route polymers")
        slice_payloads = []
        for index, composition in enumerate(normalized_slices, 1):
            landscape, rejected = _landscape(
                source, scenario=scenario_key, recovery_yield=recovery,
                market_values=values, composition=composition,
            )
            frontier = _pareto_sweep(landscape, x_metric, y_metric)
            if not frontier:
                continue
            cheapest = copy.deepcopy(min(frontier, key=lambda point: float(point["total_cost"])))
            knee = _knee(frontier, x_metric, y_metric)
            knee_status = (
                "interior_tradeoff"
                if len(frontier) > 2 and knee and knee.get("design_id") not in {
                    frontier[0].get("design_id"), frontier[-1].get("design_id"),
                }
                else "endpoint_only_no_interior_knee"
            )
            slice_payloads.append({
                "slice_id": f"slice-{index}", "feed_mass_fractions": composition,
                "landscape_points": landscape, "points": frontier,
                "n_landscape_points": len(landscape), "n_frontier_points": len(frontier),
                "n_rejected_phantom_designs": len(rejected),
                "knee_point": knee, "knee_status": knee_status,
                "cheapest_point": cheapest,
                "frontier_tradeoff": _cost_emissions_tradeoff(
                    frontier, x_metric, y_metric,
                ),
            })
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_pareto_basis")
    if not slice_payloads:
        return tool_error(tool, "No feasible Pareto frontier was found.", error_code="no_pareto_points")
    full = {
        "schema": "dissolve.optimization-payload.v1", "analysis_type": "pareto_slices" if len(slice_payloads) > 1 else "pareto_front",
        "x_metric": x_metric, "y_metric": y_metric, "scenario": scenario_key,
        "source_route_signature": source["tea"].get("route_signature"),
        "slices": slice_payloads,
    }
    primary = slice_payloads[0]
    objective_for_verification = {
        "total_cost": "min_cost", "emissions": "min_emissions",
        "profit": "max_profit", "circularity": "max_circularity",
    }[x_metric]
    verification = _verify_point(
        primary["landscape_points"], objective_for_verification, solver_key,
    )
    full["solver"] = verification
    full["assumptions"] = {
        "recovery_yield": recovery,
        "polymer_market_values_usd_per_mt": values,
        "capital_allocation_years": int(
            (asset_payload().get("policy") or {})["strap_capital_allocation_years"]
        ),
        "residual_product_revenue_included": False,
    }
    path = _payload_path("pareto", full)
    points = primary["points"]
    return tool_success(
        tool, display=f"Full route-constrained Pareto payload: {path}",
        artifact={"kind": "optimization_frontier", "format": "json", "title": "Route-constrained Pareto payload", "path": path},
        analysis_type=full["analysis_type"], x_metric=x_metric, y_metric=y_metric,
        source_route="typed_session_state", source_route_signature=source["tea"].get("route_signature"),
        source_tea_engine_mode=source["tea"].get("engine_mode"),
        feed_mt_per_yr=source["feed_mt_per_yr"], scenario=scenario_key,
        recovery_yield=recovery, n_slices_requested=len(normalized_slices),
        polymer_market_values_usd_per_mt=values,
        capital_allocation_years=int((asset_payload().get("policy") or {})["strap_capital_allocation_years"]),
        residual_product_revenue_included=False,
        n_slices_solved=len(slice_payloads), n_landscape_points=primary["n_landscape_points"],
        n_frontier_points=primary["n_frontier_points"], points=points,
        knee_point=primary["knee_point"], knee_status=primary["knee_status"],
        cheapest_point=primary["cheapest_point"],
        frontier_tradeoff=primary["frontier_tradeoff"],
        slices=[{
            key: item[key] for key in (
                "slice_id", "feed_mass_fractions", "n_landscape_points",
                "n_frontier_points", "knee_point", "cheapest_point",
                "knee_status", "frontier_tradeoff",
            )
        } for item in slice_payloads],
        pareto_payload_path=path, points_are_subset_of_landscape=True,
        economics_guard="annualized CAPEX>0 AND (OPEX>0 OR GWP>0)",
        metric_units={
            "capital_cost": "USD/yr (BioSTEAM TCI allocated over 10 years)",
            "operational_cost": "USD/yr", "transportation_cost": "USD/yr",
            "total_cost": "USD/yr", "revenue": "USD/yr", "profit": "USD/yr",
            "emissions": "t CO2e/yr", "energy_mj_per_yr": "MJ/yr",
            "circularity": "mass-diversion fraction",
        },
        solver=verification,
        circularity_basis=(asset_payload().get("policy") or {}).get("circularity_note"),
        sensitivity_basis=(
            "fixed stored-TEA stage economics/intensities across composition slices"
            if len(normalized_slices) > 1 else None
        ),
        provenance={"asset_sha256": _ASSET_SHA256, **(asset_payload().get("provenance") or {})},
        warnings=[
            "Frontier points are dominance-filtered from the feasible landscape; zero-capital phantom anchors are excluded.",
            "This is a route-constrained superstructure screen, not a final integrated-facility design.",
            "Recovery yield and market values are explicit assumptions, not measured route performance.",
            "BioSTEAM TCI is allocated over 10 years without discounting; residual-technology CAPEX is sourced in USD/yr.",
            "Residual-technology product revenue is not credited in this screen.",
            "Composition slices hold stored TEA stage economics/intensities fixed and are sensitivity slices, not re-simulations."
            if len(normalized_slices) > 1 else
            "Circularity is a mass-diversion screening proxy, not a validated circularity assessment.",
        ],
    )
