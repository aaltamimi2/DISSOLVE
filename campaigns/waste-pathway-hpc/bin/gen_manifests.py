"""Write the campaign's manifests and shards.

    gen_manifests.py SYSTEMS.json OUT_DIR

Blocks (see CAMPAIGN.json written beside them for every count and range):
  feeds    feed scenarios for the pathway tool: polymer families x composition grid x plant x energy case
  mc       the two named feeds under common random draws of the process and economic parameters and resin prices
  surface  each wash system over target share x plant capacity x energy case
  oat      every tornado parameter moved low and high, one at a time, at a grid of base plants
  sobol    Saltelli samples over all tornado parameters for the most frequent wash systems
"""
import itertools, json, math, sys
from pathlib import Path

import numpy as np
from scipy.stats import qmc

from dissolve import tea_tornado, waste_pathway_data as d

systems = json.load(open(sys.argv[1]))
out = Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
ONLY = set(sys.argv[sys.argv.index("--only") + 1].split(",")) if "--only" in sys.argv else None
SUMMARY_NAME = sys.argv[sys.argv.index("--summary") + 1] if "--summary" in sys.argv else "CAMPAIGN.json"
#: The engine accepts at most 366 operating days; the tornado tool's default range (base 350.4, +-10%) reaches 385.4.
ENGINE_BOUNDS = {"operating_days": (0.0, 366.0)}
POLYMERS = ["LDPE", "HDPE", "PET", "NYLON6", "EVOH"]
ENERGY = ["C1", "C2", "C3"]
SEED = 20261011
summary = {"seed": SEED, "blocks": {}}


def write(name, rows, per_shard):
    if ONLY is not None and name not in ONLY:
        summary["blocks"].setdefault(name, {}).update(rows=len(rows), written=False)
        print(name, len(rows), "rows (not written)", flush=True)
        return
    folder = out / name; folder.mkdir(exist_ok=True)
    shards = math.ceil(len(rows) / per_shard)
    for index in range(shards):
        with open(folder / f"{name}-{index:04d}.jsonl", "w") as sink:
            for row in rows[index * per_shard:(index + 1) * per_shard]:
                sink.write(json.dumps(row) + "\n")
    summary["blocks"].setdefault(name, {}).update(rows=len(rows), shards=shards, per_shard=per_shard)
    print(name, len(rows), "rows in", shards, "shards", flush=True)


# ---- feeds: composition grids -------------------------------------------------------------------------------------
def simplex(parts, step=10):
    units = 100 // step
    for cut in itertools.combinations(range(1, units), parts - 1):
        edges = (0, *cut, units)
        yield [(edges[i + 1] - edges[i]) * step / 100.0 for i in range(parts)]


PLANTS = [("ded5k", 5000.0, None), ("ded20k", 20000.0, None), ("ded50k", 50000.0, None), ("shared20k", 8000.0, 20000.0)]
feeds = []
for size in (3, 4, 5):
    for family in itertools.combinations(POLYMERS, size):
        for fractions in simplex(size):
            comp = dict(zip(family, fractions))
            tag = "-".join(f"{p}{round(x * 100)}" for p, x in comp.items())
            for plant, tonnes, shared in PLANTS:
                for energy in ENERGY:
                    feeds.append({"id": f"feed|{tag}|{plant}|{energy}", "family": list(family), "fractions": comp,
                                  "tonnes": tonnes, "shared_plant": shared, "plant": plant, "energy_case": energy,
                                  "max_washes": 3, "max_solvents": 6, "locations": ["A", "B"]})
rng = np.random.default_rng(SEED)
order = rng.permutation(len(feeds))  # shards of equal expected cost: the five-polymer feeds are the slowest
write("feeds", [feeds[i] for i in order], 24)
summary["blocks"]["feeds"].update(plants=[p[0] for p in PLANTS], energy_cases=ENERGY, composition_step_pct=10,
                                  families=sum(1 for s in (3, 4, 5) for _ in itertools.combinations(POLYMERS, s)),
                                  max_washes=3, max_solvents_per_stage=6, locations=["A", "B"])

# ---- tornado parameters and their ranges --------------------------------------------------------------------------
PARAMS = list(tea_tornado.PARAMETERS)
WIDE = {"target_mass_percent": (10.0, 90.0), "processing_capacity_mt_per_yr": (2000.0, 100000.0)}  # global ranges
LOG = {"processing_capacity_mt_per_yr"}


def base_config(system, share, capacity, energy, temperature=None):
    spec = d.StageSpec(frozenset(), system["polymer"], system["solvent"],
                       float(temperature if temperature is not None else system["temperatures_c"][0]), float(capacity), float(share))
    return d.process_config(spec, energy_case=energy)


def ranges_for(config):
    result = {}
    for name in PARAMS:
        if name not in config or config[name] is None:
            continue
        span = tea_tornado.default_range(name, float(config[name]), None)
        if span is not None and span[0] != span[1]:
            low, high = float(span[0]), float(span[1])
            if name in ENGINE_BOUNDS:
                low, high = max(low, ENGINE_BOUNDS[name][0]), min(high, ENGINE_BOUNDS[name][1])
            result[name] = (low, high)
    return result


# ---- mc: named feeds under common draws ---------------------------------------------------------------------------
NAMED = {"published": ({"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}, 8000.0),
         "second": ({"HDPE": 0.4, "LDPE": 0.25, "PET": 0.2, "EVOH": 0.15}, 12000.0)}
reference = base_config(systems[0], 50.0, 20000.0, "C1")
mc_params = {k: v for k, v in ranges_for(reference).items()
             if k not in ("target_mass_percent", "processing_capacity_mt_per_yr", "dissolution_temperature_c", "solvent_price_usd_per_kg")}
DRAWS = 300
unit = qmc.LatinHypercube(d=len(mc_params) + 1 + len(POLYMERS), seed=SEED).random(DRAWS)
mc = []
for name, (fractions, tonnes) in NAMED.items():
    for plant, shared in (("dedicated", None), ("shared20k", 20000.0)):
        mc.append({"id": f"mc|{name}|{plant}|base", "feed": name, "fractions": fractions, "tonnes": tonnes, "shared_plant": shared,
                   "plant": plant, "energy_case": "C1", "max_washes": 3, "max_solvents": 6, "locations": ["A", "B"], "draw": None})
        for k in range(DRAWS):
            u = unit[k]
            overrides = {p: lo + u[i] * (hi - lo) for i, (p, (lo, hi)) in enumerate(mc_params.items())}
            factor = 0.7 + 0.6 * u[len(mc_params)]
            prices = {p: d.DEFAULT_POLYMER_PRICES_USD_PER_T[p] * (0.8 + 0.4 * u[len(mc_params) + 1 + j]) for j, p in enumerate(POLYMERS)}
            mc.append({"id": f"mc|{name}|{plant}|{k:04d}", "feed": name, "fractions": fractions, "tonnes": tonnes,
                       "shared_plant": shared, "plant": plant, "energy_case": "C1", "max_washes": 3, "max_solvents": 6,
                       "locations": ["A", "B"], "draw": k, "overrides": overrides, "solvent_price_factor": factor,
                       "prices": {p: v for p, v in prices.items() if p in fractions}})
write("mc", mc, 16)
summary["blocks"]["mc"].update(draws=DRAWS, sampler="Latin hypercube, the same draws for every feed and plant",
                               parameter_ranges=mc_params, solvent_price_factor=[0.7, 1.3], resin_price_factor=[0.8, 1.2],
                               feeds={k: {"fractions": v[0], "tonnes": v[1]} for k, v in NAMED.items()})

# ---- surface ------------------------------------------------------------------------------------------------------
SHARES = list(range(5, 100, 5))
CAPACITIES = [1000.0, 2000.0, 5000.0, 10000.0, 20000.0, 50000.0, 100000.0, 200000.0]
surface, skipped = [], []
for system in systems:
    for temperature in system["temperatures_c"]:
        for energy in ENERGY:
            for share in SHARES:
                for capacity in CAPACITIES:
                    try:
                        config = base_config(system, share, capacity, energy, temperature)
                    except d.StageUnavailable as error:
                        skipped.append([system["polymer"], system["solvent"], str(error.args[0])]); break
                    surface.append({"id": f"surface|{system['polymer']}|{system['solvent']}|{temperature:g}|{energy}|{share}|{capacity:g}",
                                    "polymer": system["polymer"], "solvent": system["solvent"], "config": config})
write("surface", surface, 2500)
summary["blocks"]["surface"].update(shares_pct=SHARES, capacities_t_per_yr=CAPACITIES, energy_cases=ENERGY,
                                    systems=sum(len(s["temperatures_c"]) for s in systems))

# ---- oat ----------------------------------------------------------------------------------------------------------
oat = []
for system in systems:
    for energy in ENERGY:
        for share in (20.0, 40.0, 60.0, 80.0):
            for capacity in (5000.0, 20000.0, 80000.0):
                base = base_config(system, share, capacity, energy)
                node = f"{system['polymer']}|{system['solvent']}|{energy}|{share:g}|{capacity:g}"
                oat.append({"id": f"oat|{node}|base", "node": node, "parameter": None, "side": "base", "config": base})
                for name, (low, high) in ranges_for(base).items():
                    for side, value in (("low", low), ("high", high)):
                        oat.append({"id": f"oat|{node}|{name}|{side}", "node": node, "parameter": name, "side": side,
                                    "value": value, "base_value": base[name], "config": {**base, name: value}})
write("oat", oat, 2500)
write("oatfix", [dict(row, id=row["id"] + "|bounded") for row in oat if row["parameter"] == "operating_days" and row["side"] == "high"], 2500)
summary["blocks"]["oat"].update(parameters=PARAMS, node_shares_pct=[20, 40, 60, 80], node_capacities=[5000, 20000, 80000],
                                energy_cases=ENERGY, ranges="tea_tornado.default_range at each node's base value")

# ---- sobol --------------------------------------------------------------------------------------------------------
N = 1024
chosen = [system for system in systems if system["count"] >= 7]  # admissible in at least 7 family screens
sobol = []
for system in chosen:
    base = base_config(system, 50.0, 20000.0, "C1")
    spans = {**ranges_for(base), **WIDE}
    names = [n for n in PARAMS if n in spans]
    k = len(names)
    points = qmc.Sobol(d=2 * k, scramble=True, seed=SEED).random(N)
    A, B = points[:, :k], points[:, k:]
    blocks = [("A", A), ("B", B)] + [(f"AB{i}", np.column_stack([B[:, j] if j == i else A[:, j] for j in range(k)])) for i in range(k)]

    def scale(name, u):
        low, high = spans[name]
        return float(math.exp(math.log(low) + u * (math.log(high) - math.log(low))) if name in LOG else low + u * (high - low))

    tag = f"{system['polymer']}|{system['solvent']}"
    for label, matrix in blocks:
        for row in range(N):
            values = {name: scale(name, matrix[row, j]) for j, name in enumerate(names)}
            sobol.append({"id": f"sobol|{tag}|{label}|{row:04d}", "system": tag, "matrix": label, "row": row,
                          "factors": values, "config": {**base, **values}})
    summary["blocks"].setdefault("sobol", {}).setdefault("systems", {})[tag] = {"factors": names, "ranges": spans, "N": N}
write("sobol", sobol, 2500)
summary["blocks"]["sobol"].update(design="Saltelli: A, B and one AB_i per factor, scrambled Sobol points", energy_case="C1",
                                  log_scaled=sorted(LOG))
summary["surface_skipped"] = skipped[:50]
summary["engine_bounds_applied"] = ENGINE_BOUNDS
json.dump(summary, open(out / SUMMARY_NAME, "w"), indent=1, default=str)
print("total config rows", len(surface) + len(oat) + len(sobol), "| scenarios", len(feeds) + len(mc))
