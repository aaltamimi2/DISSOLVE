"""Waste-pathway case study: a mixed post-consumer packaging stream, 12,000 t/yr of 40/25/20/15 HDPE/LDPE/PET/EVOH.

This feed is not the one of the published study (Munoz-Briones et al., 8,000 t/yr of 60/20/10/10 LDPE/PET/nylon 6/EVOH, whose
run is kept in archive_published_feed/); the scenario grid, indicators and data are the same.

Runs the optimize_waste_pathway chain (thermodynamic screen, TEA/LCA per wash, pathway MILP) over the scenario grid
of the published case study (Munoz-Briones et al.): a wash plant dedicated to this feed or shared at 20,000 t/yr, and
downstream locations A and B, for three objectives and three Pareto fronts, plus a sweep of the EVOH resin price (the recovered resin that pays for most washes).

    .venv/bin/python campaigns/waste-pathway-film-case/run_case_study.py [--live]

Without --live a wash that is not already in the TEA cache stops the run and is listed; with --live it is simulated
(about 15 s each). Results go to results.json next to this file.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from dissolve import waste_pathway as tool
from dissolve import waste_pathway_data as dl
from dissolve import waste_pathway_milp as wp

HERE = Path(__file__).resolve().parent
FEED = wp.Feed(12000.0, {"HDPE": 0.40, "LDPE": 0.25, "PET": 0.20, "EVOH": 0.15})
PLANTS = {"dedicated": None, "shared_20kt": 20000.0}
LOCATIONS = ("A", "B")
SWEEP_POLYMER = "EVOH"
SWEEP_PRICES = (2000.0, 4000.0, 6000.0, 8100.0, 10000.0)
PARETO_PAIRS = (("emissions", "profit"), ("circularity", "profit"), ("emissions", "circularity"))


def _row(cand: wp.Candidate) -> dict:
    return json.loads(json.dumps(tool._candidate_row(cand)))


def run(live: bool) -> dict:
    out: dict = {"sweep_polymer": SWEEP_POLYMER, "feed": {"tonnes_per_year": FEED.tonnes_per_year, "fractions": dict(FEED.fractions)}, "plants": {}}
    tables, paths = {}, {}
    for plant, capacity in PLANTS.items():
        started = time.monotonic()
        table = dl.build_stage_table(FEED, max_washes=2, max_options=3, confirm_live_tea=live, plant_capacity_t=capacity)
        if table.pending:
            raise SystemExit(f"{len(table.pending)} washes still need simulating; rerun with --live")
        tables[plant], paths[plant] = table, dl.pathways_from_table(table, FEED, 2)
        out["plants"][plant] = {"seconds": round(time.monotonic() - started, 1)}
    # One set of circularity upper bounds for every scenario, as in the published case study, so indices compare across
    # plants and locations: 1.5 times the mean over the pathways of both plants.
    economics = dl.default_economics(FEED)
    bounds = wp.default_bounds([p for plant in PLANTS for p in paths[plant]], dl.DOWNSTREAM_TECHNOLOGIES, FEED, economics)
    out["circularity_upper_bounds"] = vars(bounds)
    for plant in PLANTS:
        table = tables[plant]
        block = out["plants"][plant]
        block.update({
            "washes": [
                {"removed_before": sorted(k[0]), "polymer": k[1], "solvent": k[2], "temperature_c": s.temperature_c,
                 "capex_usd_yr": s.capex_usd_yr, "opex_usd_yr": s.opex_usd_yr, "gwp_t": s.impacts.gwp_t,
                 "energy_mj": s.impacts.energy_mj, "engine_mode": table.engine_modes[k],
                 "thermo": dict(table.specs[k].thermo)}
                for k, s in sorted(table.stages.items(), key=lambda kv: (len(kv[0][0]), kv[0][1], kv[0][2]))
            ],
            "polymers_with_no_admissible_wash": sorted({n["polymer"] for n in table.no_option}),
            "locations": {},
        })
        for location in LOCATIONS:
            techs = wp.with_location(dl.DOWNSTREAM_TECHNOLOGIES, dl.LOCATION_SCENARIOS[location])
            problem = wp.PathwayProblem.build(paths[plant], techs, FEED, economics, bounds=bounds)
            entry: dict = {"candidates": len(problem.candidates), "optima": {}, "pareto": {}}
            for objective in wp.OBJECTIVES:
                answer = wp.optimize(problem, objective)
                entry["optima"][objective] = {**_row(answer["candidate"]), "solver": answer["solver"],
                                              "agrees_with_enumeration": answer["agrees_with_enumeration"]}
            for x, y in PARETO_PAIRS:
                entry["pareto"][f"{x}_vs_{y}"] = [_row(c) for c in wp.pareto_front(problem.candidates, x, y)]
            sweep = {}
            for price in SWEEP_PRICES:
                priced = wp.PathwayProblem.build(paths[plant], techs, FEED, dl.default_economics(FEED, {SWEEP_POLYMER: price}),
                                                 bounds=bounds)
                best = wp.optimize(priced, "max_profit")["candidate"]
                sweep[str(price)] = {"pathway": best.label, "profit_usd_yr": best.profit_usd,
                                     "emissions_t": best.emissions_t, "circularity": best.circularity}
            entry["price_sweep_max_profit"] = sweep
            block["locations"][location] = entry
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="simulate washes that are not in the TEA cache")
    args = parser.parse_args()
    result = run(args.live)
    (HERE / "results.json").write_text(json.dumps(result, indent=1))
    for plant, block in result["plants"].items():
        for location, entry in block["locations"].items():
            best = entry["optima"]["max_profit"]
            print(f"{plant:12s} {location}  max profit: {best['pathway']:62s} ${best['profit_usd_per_yr']:12,.0f}  "
                  f"{best['emissions_t_co2e_per_yr']:9,.0f} t  CE {best['circularity_index']:.3f}")
