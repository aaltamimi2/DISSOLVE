#!/usr/bin/env python3
"""Every number quoted in the Results text of the waste-pathway campaign, recomputed from the campaign tables.

    python results_numbers.py [--data ../data] [--resale]

Routes that sell the washed residual as resin are excluded unless --resale is given (the manuscript basis: the purity of
that residual is not established). Each table may be one .parquet file or a directory of parts. Needs duckdb only; the
candidate tables are aggregated inside DuckDB and never loaded whole (2.5 GB is enough).
"""
import sys
from pathlib import Path

import duckdb

args = sys.argv[1:]
DATA = Path(args[args.index("--data") + 1]) if "--data" in args else Path(__file__).resolve().parent.parent / "data"
KEEP = "true" if "--resale" in args else "downstream_key <> 'resale'"
SYSTEM = ("LDPE", "Toluene")  # the wash shown in the figure
FEED = "published"  # 60/20/10/10 LDPE/PET/NYLON6/EVOH at 8,000 t/yr

con = duckdb.connect()
con.execute("set threads=2; set memory_limit='2500MB'")


def table(name):
    single = DATA / f"{name}.parquet"
    return f"'{single}'" if single.exists() else f"read_parquet('{DATA / name}/*.parquet')"


def show(title, sql, limit=40):
    result = con.execute(sql)
    print(f"\n## {title}")
    print(" | ".join(column[0] for column in result.description))
    for row in result.fetchall()[:limit]:
        print(" | ".join(f"{value:.4g}" if isinstance(value, float) else str(value) for value in row))


FS, FC, MS, MC = table("feed_scenarios"), table("feed_candidates"), table("mc_scenarios"), table("mc_candidates")
SURFACE, OAT_RUNS, TORNADO, SOBOL = table("washes_surface"), table("washes_oat"), table("oat_tornado"), table("sobol_indices")
WASH = f"cfg_target_polymer = '{SYSTEM[0]}' and cfg_solvent = '{SYSTEM[1]}'"
PER_T = "/ tonnes_per_yr"

print(f"data: {DATA}\nroutes kept: {KEEP}")

# ---- 1. Size of the campaign
show("BioSTEAM runs by block (rows, successful)", " union all ".join(
    f"select '{block}' block, count(*) runs, sum(ok::int) ok from {table('washes_' + block)}"
    for block in ("feeds", "mc", "surface", "oat", "oatfix", "sobol")))
show("Feed scenarios", f"select count(distinct scenario_id) scenarios, count(*) scenario_locations, "
     f"count(distinct family) families from {FS}")
show("Distinct feed compositions", f"select count(*) from (select distinct frac_LDPE, frac_HDPE, frac_PET, frac_NYLON6, "
     f"frac_EVOH from {FS})")
show("Wash systems in the base surface", f"select count(*) systems, sum(any_ok::int) with_a_successful_run from "
     f"(select cfg_target_polymer, cfg_solvent, bool_or(ok) any_ok from {SURFACE} group by all)")

# ---- 2. What one wash costs
show(f"{SYSTEM[0]} in {SYSTEM[1]}, C1: minimum selling price and GWP at chosen plants",
     f"select cfg_target_mass_percent target_share, cfg_processing_capacity_mt_per_yr capacity, msp_usd_per_kg, gwp_kg_co2e_per_kg "
     f"from {SURFACE} where ok and {WASH} and cfg_energy_case = 'C1' and cfg_target_mass_percent in (5, 20, 60, 95) "
     f"and cfg_processing_capacity_mt_per_yr in (1000, 20000, 200000) order by 1, 2")
show("All systems at 60% share, 20,000 t/yr, C1: spread of the selling price",
     f"select count(*) systems, min(msp_usd_per_kg), median(msp_usd_per_kg), max(msp_usd_per_kg) from {SURFACE} "
     f"where ok and cfg_target_mass_percent = 60 and cfg_processing_capacity_mt_per_yr = 20000 and cfg_energy_case = 'C1'")
show("Energy case at 60% share, 20,000 t/yr (medians over systems)",
     f"select cfg_energy_case, median(msp_usd_per_kg) msp, median(gwp_kg_co2e_per_kg) gwp, count(*) systems from {SURFACE} "
     f"where ok and cfg_target_mass_percent = 60 and cfg_processing_capacity_mt_per_yr = 20000 group by all order by 1")
for metric in ("msp_usd_per_kg", "gwp_kg_co2e_per_kg"):
    show(f"Sobol total-order indices, {SYSTEM[0]} in {SYSTEM[1]}, {metric}",
         f"select factor, ST, ST_low, ST_high, S1, range_low, range_high, n_used from {SOBOL} where polymer = '{SYSTEM[0]}' "
         f"and solvent = '{SYSTEM[1]}' and metric = '{metric}' order by ST desc", 6)
    show(f"Sobol total-order indices averaged over the systems, {metric}",
         f"select factor, avg(ST) mean_ST, min(ST), max(ST), count(*) systems from {SOBOL} where metric = '{metric}' "
         f"group by all order by 2 desc", 5)
show(f"One-at-a-time, {SYSTEM[0]} in {SYSTEM[1]}, 60% share, 20,000 t/yr, C1: solvent and transport parameters",
     f"select parameter, metric, base_value, low_value, high_value, metric_base, metric_at_low, metric_at_high from {TORNADO} "
     f"where polymer = '{SYSTEM[0]}' and solvent = '{SYSTEM[1]}' and energy_case = 'C1' and target_mass_percent = 60 "
     f"and capacity_t_per_yr = 20000 and metric in ('msp_usd_per_kg', 'gwp_kg_co2e_per_kg') and parameter in "
     f"('solvent_loss_pct', 'solvent_price_usd_per_kg', 'dissolution_capacity', 'feedstock_distance_km') order by 2, 1")
show("Solvent make-up at the base and the high solvent-loss setting (the setting is divided by 100 twice: 0.01 -> 99.9999% "
     "recovery per pass)",
     f"select cfg_solvent_loss_pct loss_setting, median(solvent_makeup_kg_per_yr) makeup_kg_per_yr, count(*) runs "
     f"from {OAT_RUNS} where ok and {WASH} and cfg_energy_case = 'C1' and cfg_target_mass_percent = 60 "
     f"and cfg_processing_capacity_mt_per_yr = 20000 and cfg_dissolution_capacity = 3 group by all order by 1")

# ---- 3. Which route is best, over the feed grid
con.execute(f"""create temp table best as
    select scenario_id, location,
        max(profit_usd_per_yr) p, arg_max(n_washes, profit_usd_per_yr) p_washes,
        arg_max(downstream_key, profit_usd_per_yr) p_dest, arg_max(emissions_t_co2e_per_yr, profit_usd_per_yr) p_em,
        max(circularity_index) c, arg_max(n_washes, circularity_index) c_washes,
        arg_max(downstream_key, circularity_index) c_dest, arg_max(profit_usd_per_yr, circularity_index) c_p,
        arg_max(emissions_t_co2e_per_yr, circularity_index) c_em,
        arg_min(n_washes, emissions_t_co2e_per_yr) e_washes, arg_min(downstream_key, emissions_t_co2e_per_yr) e_dest,
        max(profit_usd_per_yr) filter (downstream_key = 'lf') lf_p,
        arg_max(n_washes, profit_usd_per_yr) filter (downstream_key = 'lf') lf_washes,
        arg_max(emissions_t_co2e_per_yr, profit_usd_per_yr) filter (downstream_key = 'lf') lf_em,
        max(profit_usd_per_yr) filter (ldpe) ldpe_p,
        max(profit_usd_per_yr) filter (ldpe and downstream_key = 'lf') ldpe_lf_p,
        arg_max(ldpe, profit_usd_per_yr) already_ldpe
    from (select *, (coalesce(wash_1, '') like 'LDPE/%' or coalesce(wash_2, '') like 'LDPE/%'
                     or coalesce(wash_3, '') like 'LDPE/%') ldpe from {FC} where {KEEP})
    group by all""")
J = f"best join {FS} f using (scenario_id, location)"
show("Profit-optimal route: number of washes (% of scenario-locations)",
     "select p_washes, round(100.0 * count(*) / sum(count(*)) over (), 1) pct from best group by all order by 1")
show("Profit-optimal route: destination of the residual",
     "select p_dest, count(*) n, round(100.0 * count(*) / sum(count(*)) over (), 1) pct from best group by all order by 2 desc")
show("Lowest-emissions route", "select e_washes, e_dest, count(*) n from best group by all order by 3 desc")
show("Highest-circularity route",
     "select c_washes, c_dest, round(100.0 * count(*) / sum(count(*)) over (), 1) pct from best group by all order by 3 desc", 6)
show("Medians per tonne of feed: best profit, its emissions; highest-circularity route's profit, emissions, share of best profit",
     f"select median(p {PER_T}) best_profit, median(p_em {PER_T}) its_emissions, median(c_p {PER_T}) circ_profit, "
     f"median(c_em {PER_T}) circ_emissions, median(c_p / p) circ_share_of_profit, avg((p > 0)::int) profitable from {J}")
show("Feeds with and without EVOH: median best profit per tonne",
     f"select frac_EVOH > 0 has_evoh, count(*) n, median(p {PER_T}) best_profit from {J} group by all order by 1")
show("By plant: average washes in the profit-optimal route, median best profit per tonne",
     f"select plant, avg(p_washes) washes, median(p {PER_T}) best_profit from {J} group by all order by 1")

# ---- 4. Two constrained cases
show("Residual restricted to landfill (no energy recovery)",
     f"select median(p {PER_T}) unrestricted, median(lf_p {PER_T}) landfill_only, median((p - lf_p) {PER_T}) penalty, "
     f"median(lf_p / p) share_of_profit, avg((lf_p > 0)::int) profitable, avg(lf_washes) washes, "
     f"median(p_em {PER_T}) unrestricted_emissions, median(lf_em {PER_T}) landfill_emissions from {J}")
show("Landfill only, by EVOH", f"select frac_EVOH > 0 has_evoh, median(lf_p {PER_T}) profit, avg((lf_p > 0)::int) profitable "
     f"from {J} group by all order by 1")
show("Landfill only, by plant", f"select plant, median(lf_p {PER_T}) profit, avg((lf_p > 0)::int) profitable from {J} "
     f"group by all order by 1")
L = f"{J} where frac_LDPE > 0"
show("LDPE recovery required (feeds that contain LDPE)",
     f"select count(*) n, avg(already_ldpe::int) already_in_optimum, median(p {PER_T}) unrestricted, "
     f"median(ldpe_p {PER_T}) ldpe_required, avg((ldpe_p > 0)::int) profitable, median((p - ldpe_p) {PER_T}) median_penalty, "
     f"avg((p - ldpe_p) {PER_T}) mean_penalty, median(ldpe_lf_p {PER_T}) with_landfill_too, "
     f"avg((ldpe_lf_p > 0)::int) profitable_with_landfill_too from {L}")
show("LDPE required, where the optimum did not already recover it",
     f"select count(*) n, median((p - ldpe_p) {PER_T}) median_penalty, median(frac_LDPE) median_ldpe_share from {L} "
     f"and not already_ldpe")
show("LDPE required, by plant", f"select plant, avg(already_ldpe::int) already_in_optimum, median(ldpe_p {PER_T}) profit, "
     f"avg((ldpe_p > 0)::int) profitable, median((p - ldpe_p) {PER_T}) median_penalty from {L} group by all order by 1")
show("LDPE required, by EVOH", f"select frac_EVOH > 0 has_evoh, avg((ldpe_p > 0)::int) profitable from {L} group by all order by 1")

# ---- 5. The example film: trade-off and uncertainty
con.execute(f"""create temp table draws as
    select scenario_id, location,
        arg_max(label, profit_usd_per_yr) route, max(profit_usd_per_yr) p,
        max(profit_usd_per_yr) filter (n_washes <= 1) p_simple, max(profit_usd_per_yr) filter (n_washes >= 2) p_multiple
    from {MC} where {KEEP} group by all""")
D = f"draws join {MS} m using (scenario_id, location) where feed = '{FEED}' and location = 'A'"
rows = con.execute(
    f"select label, profit_usd_per_yr {PER_T}, emissions_t_co2e_per_yr {PER_T}, profit_usd_per_yr / 1e6, "
    f"emissions_t_co2e_per_yr / 1e3, circularity_index from {MC} c join {MS} m using (scenario_id, location) "
    f"where feed = '{FEED}' and location = 'A' and draw is null and plant = 'dedicated' and {KEEP}").fetchall()
front = [r for r in rows if not any(o[1] >= r[1] and o[2] <= r[2] and (o[1] > r[1] or o[2] < r[2]) for o in rows)]
print(f"\n## Example film, base case, dedicated plant, location A: front of profit against emissions "
      f"({len(rows)} candidates)\nroute | profit_per_t | emissions_per_t | profit_musd_per_yr | emissions_kt_per_yr | circularity")
for r in sorted(front, key=lambda r: r[2]):
    print(" | ".join(f"{v:.4g}" if isinstance(v, float) else str(v) for v in r))
show("Example film, base case: best profit per tonne by plant", f"select plant, route, p {PER_T} profit_per_t from {D} "
     f"and draw is null order by 1")
show("Profit-optimal route in the 300 draws, by plant",
     f"select plant, route, count(*) draws from {D} and draw is not null group by all order by 1, 3 desc")
show("Extra profit from additional washes: best of 2-3 washes minus best of 0-1, per tonne of feed, 300 draws",
     f"select plant, count(*) draws, median((p_multiple - p_simple) {PER_T}) median_extra, "
     f"quantile_cont((p_multiple - p_simple) {PER_T}, 0.05) p5, quantile_cont((p_multiple - p_simple) {PER_T}, 0.95) p95, "
     f"sum((p_multiple > p_simple)::int) draws_favouring_more_washes from {D} and draw is not null group by all order by 1")
