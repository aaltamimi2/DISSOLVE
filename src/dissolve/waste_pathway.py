"""optimize_waste_pathway: the agent's tool for choosing how to process a mixed plastic feed.

The tool chains three engines. The thermodynamic screen says which solvents and temperatures take each polymer out of
the stream that is left; the TEA/LCA engine costs each such wash; the pathway MILP then picks the wash sequence and the
downstream technology for the residual by profit, emissions or MICRON circularity, or traces the trade-off between two
of them. See waste_pathway_milp.py for the formulation and waste_pathway_data.py for how washes become candidates.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from . import thermodynamics, waste_pathway_data as data_layer, waste_pathway_milp as milp, waste_pathway_plot as plotting
from .figure_support import RESULTS_DIR_ENV, _matplotlib_here, _open_file, _python_with_matplotlib, _results_dir  # noqa: F401
from .contracts import tool_error, tool_success

_SECONDS_PER_LIVE_STAGE = 15  # measured: a full 8,000 t/yr STRAP simulation takes 13 to 25 s
_PARETO_PAIRS = {
    "emissions_vs_profit": ("profit", "emissions"),  # (primary, bounded): maximize profit under an emissions limit
    "circularity_vs_profit": ("profit", "circularity"),
    "emissions_vs_circularity": ("circularity", "emissions"),
}
_ASSUMPTIONS = {
    "annualized_capital": "wash total capital investment x capital recovery factor at the plant's own finance rate and term",
    "recovered_mass": "the process model's own recovered share of the target polymer (97% when a result lacks it); "
                      "polymer a wash does not recover stays in the waste stream and goes downstream with the residual",
    "renewable_energy_share": "12% of each wash's energy, the published case study's grid mix",
    "waste_to_disposal": "every kilogram of wash waste generated goes to disposal",
    "emissions_boundary": "gate to gate: the washes' and the downstream technology's emissions, with no credit for recovered "
                          "resin replacing virgin resin or for recovered energy, as in the published case study",
    "circularity": "MICRON five-category index, equal weights; upper bounds are 1.5 times the candidate mean, and "
                   "substitutability runs from zero to the sales of recovering every polymer in the feed",
    "solvent_state": "a solvent whose melting point (PubChem) is above the process's cooling temperature is excluded; a "
                     "solvent with no melting point on file is not checked",
    "washes": "a wash dissolves one polymer; a wash qualifies only if the target reaches 10 wt% in the solvent, every polymer "
              "left in the stream stays at or below 3 wt%, and the target falls to 1 wt% or less once cooled to the process's "
              "precipitation temperature (and is dissolved above it)",
    "purity": "the retention limit is a screening criterion: at the process model's solvent-to-polymer ratio it does not "
              "guarantee purity (see retained_pickup_upper_bound_pct_of_product); no product purity is predicted",
    "resale": "after at least one wash, a residual that is a single priced polymer can be sold as that resin (at "
              "residual_resale_price_fraction of the recovered-resin price, 1.0 by default) with no further processing cost; an "
              "unwashed feed is never resold; a result that depends on it carries resale_dependence",
    "circularity_and_solvents": "the MICRON index scales energy, emissions, water and waste by bounds taken over whole pathways, "
                                "so it barely changes with the solvent chosen for a wash (about 0.01 between solvents for the same "
                                "resin) and differs mostly between resins and downstream technologies; compare solvents on "
                                "wash_comparison (energy, emissions, cost, solvent make-up, boiling margin and hazard per kg of resin)",
    "downstream_revenue": "incineration and hydrogen gasification earn $110 per tonne of residual, energy-recovery gasification "
                          "$147.98, landfill and pyrolysis earn nothing (downstream_technologies lists them); incineration and "
                          "gasification have no operating cost in the recovered data",
    "downstream_capital": "only gasification for energy recovery has a recorded capital cost (scaled by residual mass to the power 0.6 "
                          "from 2,400 t); the two hydrogen routes, landfill, incineration and pyrolysis carry none in the recovered "
                          "data (capital_cost_recorded in downstream_technologies), which favors the hydrogen routes over energy "
                          "recovery gasification when they are compared",
    "transport": "$3.01 per tonne into every wash plus $0.07 per tonne-mile to each downstream site, as in the published case study",
}


def _feed(fractions: dict[str, float]) -> tuple[dict[str, float], dict[str, str]]:
    """Resolve polymer labels to the stored-grid identities and normalize percentages to fractions."""
    if not isinstance(fractions, dict) or not fractions:
        raise ValueError("feed_mass_fractions must name each polymer and its mass fraction")
    resolved: dict[str, float] = {}
    labels: dict[str, str] = {}
    for label, value in fractions.items():
        identity = thermodynamics.resolve_polymer(str(label))
        if identity is None:
            grades = thermodynamics.expand_polymer_identity(str(label))
            if len(grades) > 1:
                raise ValueError(f"{label!r} names several grades ({', '.join(grades)}); say which one")
            raise ValueError(f"polymer {label!r} is not in the thermodynamic grid")
        if identity in resolved:
            raise ValueError(f"{label!r} and another label both resolve to {identity}")
        resolved[identity] = float(value)
        labels[identity] = str(label)
    total = sum(resolved.values())
    if total > 1.000001:
        if abs(total - 100.0) > 0.01:
            raise ValueError("feed fractions must sum to 1, or percentages to 100")
        resolved = {k: v / 100.0 for k, v in resolved.items()}
    elif abs(total - 1.0) > 1e-4:
        raise ValueError("feed fractions must sum to 1")
    if any(v <= 0 for v in resolved.values()):
        raise ValueError("every feed fraction must be positive")
    total = sum(resolved.values())
    return {k: v / total for k, v in resolved.items()}, labels  # rounding slack of up to 1e-4 is spread over the feed


def _candidate_row(cand: milp.Candidate) -> dict[str, Any]:
    return {
        "pathway": cand.label,
        "washes": [
            {"wash": i, "polymer": s.polymer, "solvent": s.solvent, "temperature_c": s.temperature_c,
             "capex_usd_per_yr": s.capex_usd_yr, "opex_usd_per_yr": s.opex_usd_yr,
             "gwp_t_co2e_per_yr": s.impacts.gwp_t, "energy_mj_per_yr": s.impacts.energy_mj,
             "recovered_fraction": s.recovered_fraction}
            for i, s in enumerate(cand.pathway.stages, 1)
        ],
        "downstream": {"key": cand.technology.key, "label": cand.technology.label,
                       "residual_t_per_yr": cand.residual_t, "distance_mile": cand.technology.distance_mile},
        "profit_usd_per_yr": cand.profit_usd, "emissions_t_co2e_per_yr": cand.emissions_t,
        "circularity_index": cand.circularity, "circularity_scores": dict(cand.scores),
        "sales_usd_per_yr": cand.sales_usd, "capex_usd_per_yr": cand.capex_usd, "opex_usd_per_yr": cand.opex_usd,
        "transport_usd_per_yr": cand.transport_usd,
    }


_FRONT_PAIRS = {"emissions_vs_profit": ("emissions", "profit"), "circularity_vs_profit": ("circularity", "profit"),
                "emissions_vs_circularity": ("emissions", "circularity")}


def _save_figure_data(problem: milp.PathwayProblem, common: dict[str, Any], selected: milp.Candidate | None,
                      selected_for: str) -> str | None:
    """Write every candidate pathway and the three exact fronts of this request, for scripts/plot_pareto.py.

    The file is the plain result of the request being answered (all candidates, not just the top few), so a user or the
    agent can have the fronts drawn without rerunning anything. Returns the path, or None when the file cannot be written
    (a plotting aid must never fail the optimization).
    """
    def point(c: milp.Candidate) -> dict[str, Any]:
        return {"label": c.label, "washes": [{"polymer": s.polymer, "solvent": s.solvent, "temperature_c": s.temperature_c}
                                              for s in c.pathway.stages],
                "downstream": c.technology.label, "downstream_key": c.technology.key,
                "profit_usd_per_yr": c.profit_usd, "emissions_t_co2e_per_yr": c.emissions_t, "circularity_index": c.circularity}

    candidates = problem.candidates
    try:
        optima = {name: milp.optimize(problem, name)["candidate"] for name in milp.OBJECTIVES}
        payload = {
            "schema": "dissolve.waste-pathway-figure-data.v1",
            "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "request": selected_for,
            "feed": common["feed"], "plant": common["wash_plant"], "basis": common["basis"],
            "downstream_sites": common["downstream_sites_in_words"], "energy_case": common["energy_case"],
            "circularity_upper_bounds": common["circularity_upper_bounds"],
            "candidates": [point(c) for c in candidates],
            "fronts": {name: [c.label for c in milp.pareto_front(candidates, *pair)] for name, pair in _FRONT_PAIRS.items()},
            "optima": {name: (c.label if c else None) for name, c in optima.items()},
            "selected": selected.label if selected else None,
            "work_summary": common.get("work_summary"),
        }
        directory = _results_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        text = json.dumps(payload, indent=1, default=str)
        (directory / f"pathways_{stamp}.json").write_text(text)
        (directory / "latest.json").write_text(text)
        return str(directory / "latest.json")
    except (OSError, ValueError):
        return None


def _work_summary(table: data_layer.StageTable, problem: milp.PathwayProblem, pool: list[str], started: float) -> dict[str, Any]:
    """What the request cost in work, stage by stage: the solvent pool, what each screen let through, the simulations run or
    reused, the routes and candidates built, and the sizes of the three fronts. Counts only; nothing here is a result."""
    modes = [table.engine_modes[k] for k in table.stages]
    reused = sum(1 for k in table.stages if k in table.session_reused)
    live_now = sum(1 for k in table.stages if table.engine_modes[k] == "live" and k not in table.session_reused)
    routes = {p.pathway.label for p in problem.candidates}
    by_washes: dict[int, int] = {}
    for label in routes:
        count = 0 if label == "no wash" else label.count(" > ") + 1
        by_washes[count] = by_washes.get(count, 0) + 1
    by_destination: dict[str, int] = {}
    for cand in problem.candidates:
        by_destination[cand.technology.key] = by_destination.get(cand.technology.key, 0) + 1
    outside = data_layer.solvents_outside_scope()
    return {
        "solvent_pool": {"scope": thermodynamics.resolve_solvent_scope()[0], "costable_in_scope": len(pool),
                         "costable_outside_scope": len(outside)},
        "screens": [dict(item) for item in table.screen_log],
        "polymers_without_wash": sorted({item["polymer"] for item in table.no_option}),
        # live_simulations counts every live wash behind this answer; a repeat call reuses them, so reused_from_earlier_call and
        # simulation_seconds (each wash's own measured run time) keep the repeat from understating the work
        "simulations": {"washes_costed": len(table.stages), "live_simulations": modes.count("live"), "simulated_in_this_call": live_now,
                        "stored_records": modes.count("cache"), "reused_from_earlier_call": reused, "failed": len(table.failed),
                        "simulation_seconds": round(sum(table.wall_seconds.get(k, 0.0) for k in table.stages if table.engine_modes[k] == "live"), 1)},
        "routes": {"total": len(routes), "by_number_of_washes": {str(k): v for k, v in sorted(by_washes.items())}},
        "candidates": {"total": len(problem.candidates), "by_destination": dict(sorted(by_destination.items()))},
        "fronts": {name: len(milp.pareto_front(problem.candidates, *pair)) for name, pair in _FRONT_PAIRS.items()},
        "seconds": round(time.monotonic() - started, 1),
    }


def _render_figure(data_file: str | None, *, summary: bool = False) -> dict[str, Any]:
    """Draw the three fronts (or, with ``summary``, the work summary) of the saved request and open the figure for the user."""
    prefix = "summary_figure" if summary else "figure"
    if not data_file:
        return {f"{prefix}_file": None, f"{prefix}_note": "the results folder could not be written, so nothing was drawn"}
    source = Path(data_file)
    figure = source.with_name(f"{source.stem}_work.png" if summary else f"{source.stem}_fronts.png")
    exe = _python_with_matplotlib()
    if exe is None:
        return {f"{prefix}_file": None, f"{prefix}_note": "no Python with matplotlib was found: install it (python3 -m pip install "
                "matplotlib) and ask again, or run python3 scripts/plot_pareto.py after installing it"}
    try:
        if exe == sys.executable:
            (plotting.write_summary if summary else plotting.write)(source, figure)
        else:
            command = [exe, str(Path(plotting.__file__)), str(source), "--out", str(figure)] + (["--summary"] if summary else [])
            done = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if done.returncode != 0:
                return {f"{prefix}_file": None, f"{prefix}_note": f"the plot could not be drawn: {done.stderr.strip()[-300:]}"}
    except Exception as error:  # a plotting aid must never fail the optimization
        return {f"{prefix}_file": None, f"{prefix}_note": f"the plot could not be drawn: {type(error).__name__}: {error}"}
    out = {f"{prefix}_file": str(figure), f"{prefix}_opened": _open_file(figure)}
    if not summary:
        out["figure_table_file"] = str(figure.with_name(f"{figure.stem}.md"))
    return out


def _ranked_row(rank: int, cand: milp.Candidate) -> dict[str, Any]:
    """One line of a ranking: the pathway and its three metrics. The full detail of the winner is in ``selected``."""
    return {"rank": rank, "pathway": cand.label, "profit_usd_per_yr": cand.profit_usd,
            "emissions_t_co2e_per_yr": cand.emissions_t, "circularity_index": cand.circularity}


def _wash_screen(cand: milp.Candidate, table: data_layer.StageTable) -> list[dict[str, Any]]:
    """For each wash of a pathway: what the thermodynamic screen found and where its recovery came from."""
    out: list[dict[str, Any]] = []
    removed: frozenset[str] = frozenset()
    for stage in cand.pathway.stages:
        key = (removed, stage.polymer, stage.solvent)
        spec = table.specs.get(key)
        if spec is not None:
            out.append({"polymer": stage.polymer, "solvent": stage.solvent, **spec.thermo,
                        "recovery_source": table.recovery_sources.get(key), "operating_temperature_c": stage.temperature_c, "solvent_indicators": table.indicators.get(key),
                        "safety": data_layer.solvent_hazard(stage.solvent, stage.temperature_c)})
        removed = removed | {stage.polymer}
    return out


def _wash_comparison(table: data_layer.StageTable) -> list[dict[str, Any]]:
    """Every costed wash with its solvent-specific figures per kg of recovered resin and its hazard, for comparing solvents.

    The circularity index barely moves with the solvent, so these figures are where solvents differ.
    """
    rows = []
    for key, stage in table.stages.items():
        indicators = table.indicators.get(key) or {}
        rows.append({"polymer": stage.polymer, "solvent": stage.solvent, "temperature_c": stage.temperature_c,
                     "stream_without": sorted(key[0]), **indicators,
                     "safety": data_layer.solvent_hazard(stage.solvent, stage.temperature_c or 0.0)})
    return sorted(rows, key=lambda r: (r["polymer"], len(r["stream_without"]), r.get("gwp_kg_co2e_per_kg_resin") or 0.0))


def _basis(table: data_layer.StageTable) -> str:
    """What the answer rests on: the TEA basis of its washes, or the thermodynamic screen alone when none was costed."""
    if not table.stages:
        return "cosmo_rs_grid"  # no wash qualified: the answer is the screen plus the downstream technology table
    statuses = set(table.match_status.values())
    modes = set(table.engine_modes.values())
    if "surrogate" in statuses or "screening_estimate" in modes:
        return "tea_screening_analog"
    return "tea_live" if "live" in modes else "tea_cache_exact"


def _pending_payload(table: data_layer.StageTable) -> list[dict[str, Any]]:
    return [
        {"removed_before": sorted(s.removed_before), "polymer": s.polymer, "solvent": s.solvent,
         "dissolution_temperature_c": s.temperature_c, "processing_capacity_mt_per_yr": round(s.plant_capacity_t or s.mass_in_t, 3),
         "stream_mass_mt_per_yr": round(s.mass_in_t, 3),
         "target_mass_percent": round(s.target_mass_percent, 3)}
        for s in table.pending
    ]


def optimize_waste_pathway(
    feed_mass_fractions: dict[str, float],
    feed_tonnes_per_year: float,
    objective: Literal["max_profit", "min_emissions", "max_circularity"] = "max_profit",
    pareto: Optional[Literal["emissions_vs_profit", "circularity_vs_profit", "emissions_vs_circularity"]] = None,
    max_washes: int = 2,
    max_solvents_per_stage: int = 3,
    location_scenario: Literal["A", "B"] = "A",
    energy_case: Literal["C1", "C2", "C3"] = "C1",
    confirm_live_tea: Optional[bool] = None,
    polymer_prices_usd_per_t: Optional[dict[str, float]] = None,
    min_solubility_pct: float = data_layer.MIN_SOLUBILITY_PCT,
    max_retained_pct: float = data_layer.MAX_RETAINED_PCT,
    solvents: Optional[list[str]] = None,
    shared_plant_capacity_mt_per_yr: Optional[float] = None,
    circularity_upper_bounds: Optional[dict[str, float]] = None,
    downstream_distances_mile: Optional[dict[str, float]] = None,
    residual_resale_price_fraction: float = 1.0,
    avoid_danger_solvents: bool = False,
    draw_plot: bool = False,
    draw_summary: bool = False,
) -> str:
    """Choose the solvent-wash sequence and downstream technology for a mixed plastic feed by profit, emissions or circularity. Call it only when the user has given the feed: the polymers, their mass fractions or percentages, and the amount per year. Never assume or invent a feed, a composition or a tonnage, and never split a generic name such as nylon or PE into grades yourself; if something is missing or unclear, ask. If the percentages do not add up to 100, ask which one is wrong instead of correcting it. Do not use it for a question about one solvent, a solubility, a solvent's price, safety or hazard, or the economics of one specified process: the screening, safety and process tools answer those. Use it when the user asks how a mixed or multilayer plastic stream should be processed: which polymers to recover by solvent washes, with which solvents, and where the rest should go (landfill, incineration, pyrolysis, three gasification routes, or selling a clean single-polymer residual as resin). feed_mass_fractions maps polymer names to fractions (or percentages summing to 100) and feed_tonnes_per_year is the annual amount (convert per-day or per-month amounts and say so). objective picks one answer and every result also carries the other two optima; pareto instead traces the trade-off between two metrics. Each wash is costed from stored records or a live BioSTEAM simulation; live simulations take about 15 seconds each and run only when confirm_live_tea is true, otherwise the tool lists how many it still needs and stops. Only solvents with a price and a life-cycle record can be costed; solvents limits the pool to the named ones, in any common spelling. A wash qualifies only if the target reaches min_solubility_pct in the solvent, every polymer left in the stream stays at or below max_retained_pct, and the target falls out of solution on cooling. Washes cap at max_washes per sequence and max_solvents_per_stage solvents are costed per polymer and stream. shared_plant_capacity_mt_per_yr sizes the wash plant for a larger shared facility (at least the feed) and charges the feed only its throughput share; omit it for a plant just for this feed. downstream_distances_mile gives miles from the plant to each downstream site (landfill, incineration, pyrolysis, gasification_energy, gasification_hydrogen, gasification_hydrogen_ccs); ones not given keep the published study's defaults (landfill 9.2, incineration 151, pyrolysis 1,034, hydrogen gasification 2,036, energy gasification on site); location_scenario B moves pyrolysis and hydrogen gasification to 76.1 miles. Ask the user for their distances or say which you assumed, in miles, not by scenario letter. polymer_prices_usd_per_t overrides recovered-resin prices (a polymer without a price earns nothing: the result warns). Revenue comes only from recovered resin and the downstream_technologies table in the result (incineration and hydrogen gasification earn a small amount per tonne, landfill and pyrolysis earn nothing): never say a technology earns nothing without checking that table, and answer a burn-or-recycle question from unwashed_feed_options (the feed sent whole to each destination) next to the washed pathways. residual_resale_price_fraction (default 1.0) sets the price of the washed residual sold as resin relative to recovered resin; when the answer sells it, the result carries resale_dependence (profit at full, half and no resale price), so say how much of the profit rests on that assumption. The thermodynamic screen does not judge solvent hazard, but each selected wash carries safety data and the result warns about GHS Danger solvents and high heating risk: report those warnings, and set avoid_danger_solvents to true when the user wants safer solvents. The circularity index barely changes with the solvent (about 0.01 between solvents for one resin), so to compare solvents or answer which solvent is best for a resin use wash_comparison, which gives each costed wash's energy, emissions, cost, solvent make-up, boiling margin, solvent life-cycle factor and hazard per kg of recovered resin, and say that the index does not separate them. Every result is also saved with all its candidate pathways and the three exact fronts (figure_data_file). Set draw_plot to true when the user asks to see, plot or draw the trade-off or Pareto front: the tool then draws emissions against profit, circularity against profit and emissions against circularity, with the numbered routes listed under each panel, and opens the figure in the user's image viewer (figure_file; the terminal cannot show it inline, so give the user the path and say it has opened, or if figure_note says it could not be drawn, say why). Every result carries work_summary (the solvent pool, what each screen let through, the simulations run or reused, the routes and candidates built, the front sizes): when the user asks what was done, how much work it took, how many options were considered or why there are that many candidates, answer from it and set draw_summary to true to draw it as a figure and open it (summary_figure_file; repeating a request in the same session costs no new simulations). circularity_upper_bounds takes the bounds an earlier answer reported, to compare scenarios of one feed on one scale. A prediction is not a plant result: circularity is a screening index, the retention limit does not guarantee purity, and the downstream-technology data are partly recovered; read the warnings in the result and report them."""
    tool = "optimize_waste_pathway"
    started = time.monotonic()
    try:
        fractions, labels = _feed(feed_mass_fractions)
        tonnes = float(feed_tonnes_per_year)
        if tonnes <= 0:
            raise ValueError("feed_tonnes_per_year must be positive")
        if not 0 <= int(max_washes) <= 4:
            raise ValueError("max_washes must be 0 to 4 (0: no washing, only the downstream destination is chosen)")
        if not 1 <= int(max_solvents_per_stage) <= 6:
            raise ValueError("max_solvents_per_stage must be 1 to 6")
        feed = milp.Feed(tonnes, fractions)
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_input")

    in_scope = data_layer.admitted_solvents()
    solvent_notes: list[str] = []
    if solvents:
        wanted: dict[str, str] = {}
        for name in solvents:
            identity = thermodynamics.resolve_solvent(str(name))
            if identity is None:
                solvent_notes.append(f"solvent {name!r} is not recognized and was ignored")
            else:
                wanted.setdefault(identity, str(name))
        in_scope_ids = {thermodynamics.resolve_solvent(n): n for n in in_scope}
        pool = [in_scope_ids[i] for i in wanted if i in in_scope_ids]
        solvent_notes += [f"solvent {name!r} cannot be costed (no price or life-cycle record, or outside the solvent scope) "
                          "and was left out" for i, name in wanted.items() if i not in in_scope_ids]
    else:
        pool = in_scope
    if solvents and not pool:
        return tool_error(tool, "none of the named solvents can be costed in the active solvent scope",
                          error_code="no_costable_solvents", notes=solvent_notes,
                          solvents_outside_scope=data_layer.solvents_outside_scope())
    try:
        economics = data_layer.default_economics(feed, polymer_prices_usd_per_t,
                                                 residual_resale_fraction=float(residual_resale_price_fraction))
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_input")
    distances = dict(data_layer.LOCATION_SCENARIOS[location_scenario])
    try:
        for name, miles in (downstream_distances_mile or {}).items():
            key = data_layer.DISTANCE_KEYS.get(str(name).strip().casefold().replace(" ", "_"))
            if key is None:
                raise ValueError(f"unknown downstream site {name!r}; use {sorted(data_layer.DISTANCE_KEYS)}")
            if float(miles) < 0:
                raise ValueError("distances cannot be negative")
            distances[key] = float(miles)
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_input")
    technologies = milp.with_location(data_layer.DOWNSTREAM_TECHNOLOGIES, distances)
    bounds = None
    if circularity_upper_bounds:
        try:
            bounds = milp.CircularityBounds(
                energy_mj=float(circularity_upper_bounds["energy_mj"]), ghg_t=float(circularity_upper_bounds["ghg_t_co2e"]),
                water_m3=float(circularity_upper_bounds["water_m3"]), waste_kg=float(circularity_upper_bounds["waste_kg"]),
                sales_low_usd=float(circularity_upper_bounds.get("sales_low_usd", 0.0)),
                sales_high_usd=float(circularity_upper_bounds.get("sales_high_usd", milp.SUBSTITUTABILITY_NORM_USD)))
            if min(bounds.energy_mj, bounds.ghg_t, bounds.water_m3, bounds.waste_kg) <= 0 or \
                    bounds.sales_high_usd <= bounds.sales_low_usd:
                raise ValueError("upper bounds must be positive and sales_high_usd above sales_low_usd")
        except (KeyError, TypeError, ValueError) as error:
            return tool_error(tool, "circularity_upper_bounds needs positive energy_mj, ghg_t_co2e, water_m3 and waste_kg "
                                    f"({error})", error_code="invalid_input")

    try:
        table = data_layer.build_stage_table(
            feed, max_washes=int(max_washes), max_options=int(max_solvents_per_stage), solvents=pool,
            confirm_live_tea=bool(confirm_live_tea), energy_case=energy_case,
            min_solubility_pct=float(min_solubility_pct), max_retained_pct=float(max_retained_pct),
            plant_capacity_t=float(shared_plant_capacity_mt_per_yr) if shared_plant_capacity_mt_per_yr else None,
            avoid_danger_solvents=bool(avoid_danger_solvents),
        )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_input")
    broken = [item for item in table.failed if item.get("reason") == "thermodynamic_screen_failed"]
    if broken:
        return tool_error(tool, f"the thermodynamic screen failed: {broken[0]['detail']}",
                          error_code="thermodynamic_screen_failed", detail=broken[:3])
    if table.pending:
        return tool_error(
            tool,
            f"{len(table.pending)} wash(es) need a live BioSTEAM simulation (about "
            f"{_SECONDS_PER_LIVE_STAGE * len(table.pending)} s in all). Run again with confirm_live_tea=true to simulate them.",
            error_code="live_tea_cost_confirmation_required",
            pending_stages=_pending_payload(table), stages_costed=len(table.stages),
        )

    paths = data_layer.pathways_from_table(table, feed, int(max_washes))
    problem = milp.PathwayProblem.build(paths, technologies, feed, economics, bounds=bounds)

    modes = sorted(set(table.engine_modes.values()))
    basis = _basis(table)
    unpriced = data_layer.polymers_without_price(feed, economics)
    warnings: list[str] = list(solvent_notes)
    for polymer in sorted({f["polymer"] for f in table.failed if f.get("reason") != "thermodynamic_screen_failed"}):
        why = next(f["detail"] for f in table.failed if f["polymer"] == polymer and f.get("reason") != "thermodynamic_screen_failed")
        warnings.append(f"washes of {polymer} could not be simulated ({str(why)[:140]}); {polymer} is left in the residual")
    for identity, given in labels.items():
        # a family label that DISSOLVE resolves to one grade by default (PE, polyethylene -> LDPE) is a guess; an alias for the
        # polymer itself (polystyrene, ldpe) is not
        if thermodynamics.resolve_polymer_identity(given) != identity:
            warnings.append(f"read the polymer {given!r} as {identity}; name the grade (LDPE, HDPE, ...) if that is not what you meant")
    if len(fractions) == 1:
        warnings.append("the feed is a single polymer, so there is nothing to separate by washing; only its downstream "
                        "destination is optimized")
    if unpriced:
        warnings.append(f"no price for {', '.join(unpriced)}: recovering them earns nothing, so no wash of them can pay; "
                        "pass polymer_prices_usd_per_t to value them")
    assumed = sorted({k[1] for k, v in table.recovery_sources.items() if v == "assumed"})
    if assumed:
        warnings.append(f"the simulation reported no usable recovery for {', '.join(assumed)} washes; 97% was assumed")
    common: dict[str, Any] = {
        "source_basis_note": "cosmo_rs_grid screen, then TEA/LCA process model per wash, then the pathway MILP",
        "basis": basis,
        "feed": {"tonnes_per_year": tonnes, "mass_fractions": fractions, "labels_as_given": labels},
        "downstream_distances_mile": distances,
        "downstream_sites_in_words": data_layer.describe_distances(distances),
        "downstream_sites_origin": ("the distances the caller gave, the rest the published study's defaults"
                                    if downstream_distances_mile else "the published study's sites (the default)"),
        "energy_case": energy_case,
        "wash_plant": ({"shared_capacity_mt_per_yr": float(shared_plant_capacity_mt_per_yr),
                        "cost_basis": "each wash pays its throughput share of the plant"}
                       if shared_plant_capacity_mt_per_yr else {"cost_basis": "plant dedicated to this feed"}),
        "washes_costed": len(table.stages), "stage_engine_modes": modes,
        "wash_comparison": _wash_comparison(table),
        "work_summary": _work_summary(table, problem, pool, started),
        "candidates_considered": len(problem.candidates),
        "circularity_upper_bounds": {"energy_mj": problem.bounds.energy_mj, "ghg_t_co2e": problem.bounds.ghg_t,
                                     "water_m3": problem.bounds.water_m3, "waste_kg": problem.bounds.waste_kg,
                                     "sales_low_usd": problem.bounds.sales_low_usd,
                                     "sales_high_usd": problem.bounds.sales_high_usd},
        "circularity_bounds_origin": "supplied by the caller" if bounds else "1.5 times the mean over this call's pathways",
        "thermodynamic_limits": {"min_solubility_pct": min_solubility_pct, "max_retained_pct": max_retained_pct},
        "polymer_prices_usd_per_t": dict(economics.polymer_price_usd_per_t),
        "solvents_without_cost_records": "excluded: only solvents with a price and a life-cycle record are costed",
        "solvent_pool": {"scope": thermodynamics.resolve_solvent_scope()[0], "costable_solvents_in_scope": len(pool),
                         "costable_solvents_outside_scope": data_layer.solvents_outside_scope()},
        "polymers_with_no_admissible_wash": sorted({item["polymer"] for item in table.no_option}),
        "washes_that_failed": table.failed, "warnings": warnings,
        "assumptions": _ASSUMPTIONS, "downstream_data_notes": data_layer.DOWNSTREAM_NOTES,
        "seconds": round(time.monotonic() - started, 1),
    }
    if pareto:
        primary, bounded = _PARETO_PAIRS[pareto]
        exact = milp.pareto_front(problem.candidates, *{"emissions_vs_profit": ("emissions", "profit"),
                                  "circularity_vs_profit": ("circularity", "profit"),
                                  "emissions_vs_circularity": ("emissions", "circularity")}[pareto])
        sweep = milp.epsilon_front(problem, primary=primary, bounded=bounded, steps=30)
        common["figure_data_file"] = _save_figure_data(problem, common, None, f"pareto={pareto}")
        if draw_plot:
            common.update(_render_figure(common["figure_data_file"]))
        if draw_summary:
            common.update(_render_figure(common["figure_data_file"], summary=True))
        return tool_success(
            tool, display=(f"Pareto front ({pareto.replace('_', ' ')}): {len(exact)} non-dominated pathways."
                       + (f" Plot: {common['figure_file']}" if common.get("figure_file") else "")),
            analysis_type="waste_pathway_pareto", pareto=pareto,
            frontier_points=[_candidate_row(c) for c in exact],
            epsilon_sweep_agrees_with_enumeration=all(p["agrees_with_enumeration"] for p in sweep),
            epsilon_sweep_distinct_points=len(sweep), **common,
        )
    answer = milp.optimize(problem, objective)
    best = answer["candidate"]
    if best is None:
        return tool_error(tool, "no feasible pathway", error_code="no_feasible_pathway", **common)
    common["figure_data_file"] = _save_figure_data(problem, common, best, f"objective={objective}")
    if draw_plot:
        common.update(_render_figure(common["figure_data_file"]))
    if draw_summary:
        common.update(_render_figure(common["figure_data_file"], summary=True))
    ranked = sorted(problem.candidates, key=lambda c: c.metric(milp.OBJECTIVES[objective][0]),
                    reverse=milp.OBJECTIVES[objective][1])[:5]
    # The other objectives cost nothing once the washes are costed, so every answer carries all three optima.
    others = {name: milp.optimize(problem, name) for name in milp.OBJECTIVES if name != objective}
    left = milp.residual_polymers(best.pathway, feed)
    if len(left) == 1 and best.technology.key != "resale":
        only = next(iter(left))
        if not best.pathway.stages:
            pass  # an unwashed single polymer: the single-polymer note above says it
        elif economics.polymer_price_usd_per_t.get(only, 0.0) <= 0:
            warnings.append(f"the selected pathway leaves pure {only}, which has no price, so it cannot be valued as resin")
        else:
            warnings.append(f"the selected pathway leaves pure {only} but another destination earns more than selling it as resin")
    resale_dependence = None
    if best.technology.key == "resale":
        sensitivity = []
        for fraction in (1.0, 0.5, 0.0):
            at = milp.PathwayProblem.build(paths, technologies, feed, replace(economics, residual_resale_fraction=fraction),
                                           bounds=problem.bounds)
            pick = milp.optimize(at, "max_profit")["candidate"]
            sensitivity.append({"residual_sold_at_share_of_resin_price": fraction, "best_pathway": pick.label,
                                "profit_usd_per_yr": pick.profit_usd})
        resale_dependence = {
            "resale_share_of_sales_pct": 100.0 * best.technology.revenue_usd_per_t * best.residual_t / best.sales_usd if best.sales_usd else None,
            "residual_price_assumed": f"{economics.residual_resale_fraction:.0%} of the recovered-resin price",
            "sensitivity_max_profit": sensitivity,
        }
        warnings.append(f"the selected pathway sells the washed {next(iter(left))} residual as resin ({resale_dependence['resale_share_of_sales_pct']:.0f}% of "
                        "its sales) with no further processing cost; its profit depends on that residual being clean enough to sell "
                        "(see resale_dependence for the result at half the price and at none)")
    screen = _wash_screen(best, table)
    dangerous = [f"{w['solvent']} at {w['operating_temperature_c']:g} C" for w in screen
                 if (w.get("safety") or {}).get("ghs_signal_word") == "Danger"]
    if dangerous:
        warnings.append("the selected washes use solvents with the GHS Danger signal word: " + "; ".join(dangerous)
                        + ". Pass avoid_danger_solvents=true to keep them out (the tool does not otherwise screen solvent hazard)")
    risky = [f"{w['solvent']} ({', '.join((w['safety'].get('heating_flags') or [])) or 'see safety'})" for w in screen
             if (w.get("safety") or {}).get("heating_risk") in ("high", "critical")]
    if risky:
        warnings.append("heating risk is high or critical at the operating temperature for " + "; ".join(risky))
    unchecked = sorted({w["solvent"] for w in _wash_screen(best, table) if w.get("solvent_melting_point_c") is None})
    if unchecked:
        warnings.append(f"no melting point is on file for {', '.join(unchecked)}, so it was not checked that the solvent stays liquid when "
                        "the process cools")
    if any((w.get("retained_pickup_upper_bound_pct_of_product") or 0.0) > 10.0 for w in _wash_screen(best, table)):
        warnings.append("at the process model's solvent-to-polymer ratio a wash could take up a sizable share of the retained "
                        "polymers (retained_pickup_upper_bound_pct_of_product): the screen does not establish purity")
    downstream_technologies = [
        {"key": t.key, "label": t.label, "distance_mile": t.distance_mile, "revenue_usd_per_t_of_residual": t.revenue_usd_per_t,
         "operating_cost_usd_per_t": t.opex_usd_per_t,
         "capital_cost_recorded": t.capex_ref_usd_yr > 0.0, "gwp_t_co2e_per_t": t.impacts_per_t.gwp_t,
         "cannot_take": sorted(t.excluded_polymers)} for t in technologies]
    unwashed = sorted((c for c in problem.candidates if not c.pathway.stages), key=lambda c: -c.profit_usd)
    unwashed_options = [{"destination": c.technology.label, "key": c.technology.key, "profit_usd_per_yr": c.profit_usd,
                         "emissions_t_co2e_per_yr": c.emissions_t, "circularity_index": c.circularity} for c in unwashed]
    optima = {objective: _candidate_row(best)}
    optima.update({name: _candidate_row(o["candidate"]) for name, o in others.items() if o["candidate"] is not None})
    return tool_success(
        tool,
        display=f"{objective}: {best.label} (profit ${best.profit_usd:,.0f}/yr, {best.emissions_t:,.1f} t CO2e/yr, "
                f"circularity {best.circularity:.3f}).",
        analysis_type="waste_pathway_optimum", objective=objective, selected=_candidate_row(best),
        selected_wash_screen=screen, resale_dependence=resale_dependence,
        downstream_technologies=downstream_technologies, unwashed_feed_options=unwashed_options,
        ranked_candidates=[_ranked_row(i, c) for i, c in enumerate(ranked, 1)],
        optima_by_objective=optima, solver=answer["solver"],
        milp_agrees_with_enumeration=answer["agrees_with_enumeration"], **common,
    )
