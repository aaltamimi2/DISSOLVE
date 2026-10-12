"""Collect the campaign's raw gzip records into Parquet tables and compute the first-pass analysis tables (no figures).

    collect.py RESULTS_DIR MANIFESTS_DIR OUT_DIR

Writes OUT_DIR/washes_<block>.parquet, feed_scenarios / feed_candidates, mc_scenarios / mc_candidates, oat_tornado,
sobol_indices and DATA_INDEX.json (what each file holds and its row count).
"""
import glob, json, math, os, sys
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_common as common

results, manifests, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
index = {"files": {}}
METRICS = ["msp_usd_per_kg", "gwp_kg_co2e_per_kg", "tci_usd", "aoc_usd_per_yr", "total_energy_mj_per_kg"]
POLYMERS = ["LDPE", "HDPE", "PET", "NYLON6", "EVOH"]


def scalars(mapping):
    return {k: v for k, v in (mapping or {}).items() if isinstance(v, (int, float, str, bool)) or v is None}


def wash_record(block, shard, ident, ok, config, row, error, wall):
    record = {"block": block, "shard": shard, "id": ident, "ok": bool(ok), "wall_s": wall,
              "error": None if ok else json.dumps(error)[:400]}
    record.update({f"cfg_{k}": v for k, v in scalars(config).items()})
    if ok and row:
        record.update(scalars(row))
        record["lca_status"] = (row.get("lca_coverage") or {}).get("status")
        record["worker_sha256"] = (row.get("live_provenance") or {}).get("worker_source_sha256")
    return record


class Table:
    """Streams rows into one Parquet file with a schema fixed by the first batch (later unknown columns are dropped)."""

    def __init__(self, name, description, batch=50000):
        self.name, self.rows, self.batch, self.count, self.writer, self.schema = name, [], batch, 0, None, None
        index["files"][name] = {"description": description}

    def add(self, row):
        self.rows.append(row)
        if len(self.rows) >= self.batch:
            self.flush()

    def flush(self):
        if not self.rows:
            return
        frame = pd.DataFrame(self.rows)
        for column in frame.columns:
            if frame[column].dtype == object:
                kinds = {type(v) for v in frame[column].dropna()}
                if kinds - {str}:
                    frame[column] = frame[column].map(lambda v: None if v is None else str(v) if not isinstance(v, (int, float)) else v)
                    if {type(v) for v in frame[column].dropna()} - {str}:
                        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        if self.writer is None:
            table = pa.Table.from_pandas(frame, preserve_index=False)
            fields = [pa.field(f.name, pa.float64() if pa.types.is_integer(f.type) or pa.types.is_null(f.type) else f.type) for f in table.schema]
            self.schema = pa.schema(fields)
            self.writer = pq.ParquetWriter(out / self.name, self.schema, compression="zstd")
        for field in self.schema:
            if field.name not in frame.columns:
                frame[field.name] = None
        arrays = []
        for field in self.schema:
            series = frame[field.name]
            if pa.types.is_floating(field.type):
                series = pd.to_numeric(series, errors="coerce")
            elif pa.types.is_string(field.type):
                series = series.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v))
            elif pa.types.is_boolean(field.type):
                series = series.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else bool(v))
            arrays.append(pa.array(series, type=field.type, from_pandas=True))
        self.writer.write_table(pa.Table.from_arrays(arrays, schema=self.schema))
        self.count += len(self.rows)
        self.rows = []

    def close(self):
        self.flush()
        if self.writer is not None:
            self.writer.close()
        index["files"][self.name]["rows"] = self.count
        print(self.name, self.count, flush=True)


def manifest_rows(block):
    for path in sorted(glob.glob(str(manifests / block / "*.jsonl"))):
        with open(path) as handle:
            for line in handle:
                yield json.loads(line)


# ---- washes of the configuration blocks ---------------------------------------------------------------------------
values = {}  # id -> metric values, for the tornado and Sobol tables
for block in ("surface", "oat", "oatfix", "sobol"):
    planned = {row["id"]: row for row in manifest_rows(block)}
    table = Table(f"washes_{block}.parquet", f"every BioSTEAM run of block {block}: cfg_* is the process configuration sent, "
                  "the other columns are the engine's result row")
    seen = set()
    for path in sorted(glob.glob(str(results / block / "*.runs.jsonl.gz"))):
        shard = Path(path).name.split(".")[0]
        for record in common.read_gz(path):
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            plan = planned.get(record["id"], {})
            row = wash_record(block, shard, record["id"], record["ok"], plan.get("config"), record.get("row"), record.get("error"), record.get("wall_s"))
            for extra in ("node", "parameter", "side", "value", "base_value", "system", "matrix", "row"):
                if extra in plan:
                    row[f"plan_{extra}"] = plan[extra]
            table.add(row)
            if record["ok"]:
                values[record["id"]] = [record["row"].get(m) for m in METRICS]
    table.close()
    index["files"][table.name].update(planned=len(planned), missing=len(planned) - len(seen))

# ---- one-at-a-time tornado table ----------------------------------------------------------------------------------
tornado = Table("oat_tornado.parquet", "one row per base plant (node), parameter and metric: the metric at the base, at the "
                "parameter's low and high value, and the swing; operating_days high is the engine's bound of 366 days")
nodes = {}
for row in manifest_rows("oat"):
    nodes.setdefault(row["node"], {"base": None, "sides": {}})
    if row["side"] == "base":
        nodes[row["node"]]["base"] = row
    else:
        nodes[row["node"]]["sides"][(row["parameter"], row["side"])] = row
for row in manifest_rows("oatfix"):
    nodes[row["node"]]["sides"][(row["parameter"], "high")] = row
for node, entry in nodes.items():
    base = entry["base"]
    polymer, solvent, energy, share, capacity = node.split("|")
    base_values = values.get(base["id"])
    for parameter in sorted({p for p, _ in entry["sides"]}):
        low, high = entry["sides"].get((parameter, "low")), entry["sides"].get((parameter, "high"))
        low_values, high_values = (values.get(low["id"]) if low else None), (values.get(high["id"]) if high else None)
        for position, metric in enumerate(METRICS):
            b = base_values[position] if base_values else None
            lo = low_values[position] if low_values else None
            hi = high_values[position] if high_values else None
            reach = [v for v in (lo, hi, b) if v is not None]
            swing = (max(reach) - min(reach)) if b is not None and len(reach) > 1 else None
            tornado.add({"polymer": polymer, "solvent": solvent, "energy_case": energy, "target_mass_percent": float(share),
                         "capacity_t_per_yr": float(capacity), "parameter": parameter, "metric": metric,
                         "base_value": float(base["config"][parameter]), "low_value": low["value"] if low else None,
                         "high_value": high["value"] if high else None, "metric_base": b, "metric_at_low": lo,
                         "metric_at_high": hi, "swing": swing,
                         "swing_pct_of_base": (swing / abs(b) * 100.0) if swing is not None and b else None})
tornado.close()

# ---- Sobol indices ------------------------------------------------------------------------------------------------
campaign = json.load(open(manifests / "CAMPAIGN.v2.json"))
sobol = Table("sobol_indices.parquet", "first-order (Saltelli 2010) and total (Jansen) indices per wash system, metric and "
              "factor, with 95% bootstrap intervals over the N base rows; n_used counts rows where A, B and AB_i all ran")
rng = np.random.default_rng(campaign["seed"])
for system, spec in campaign["blocks"]["sobol"]["systems"].items():
    names, N = spec["factors"], spec["N"]

    def column(label):
        data = np.full((N, len(METRICS)), np.nan)
        for r in range(N):
            got = values.get(f"sobol|{system}|{label}|{r:04d}")
            if got is not None:
                data[r] = [np.nan if v is None else v for v in got]
        return data

    A, B = column("A"), column("B")
    for i, factor in enumerate(names):
        AB = column(f"AB{i}")
        for m, metric in enumerate(METRICS):
            a, b, ab = A[:, m], B[:, m], AB[:, m]
            keep = ~(np.isnan(a) | np.isnan(b) | np.isnan(ab))
            a, b, ab = a[keep], b[keep], ab[keep]
            if len(a) < 50:
                continue

            def estimate(a, b, ab):
                variance = np.var(np.concatenate([a, b]), ddof=1)
                if variance <= 0:
                    return np.nan, np.nan
                return float(np.mean(b * (ab - a)) / variance), float(np.mean((a - ab) ** 2) / (2 * variance))

            s1, st = estimate(a, b, ab)
            boots = np.array([estimate(a[pick], b[pick], ab[pick]) for pick in (rng.integers(0, len(a), len(a)) for _ in range(200))])
            polymer, solvent = system.split("|")
            sobol.add({"polymer": polymer, "solvent": solvent, "metric": metric, "factor": factor,
                       "range_low": spec["ranges"][factor][0], "range_high": spec["ranges"][factor][1],
                       "S1": s1, "S1_low": float(np.nanpercentile(boots[:, 0], 2.5)), "S1_high": float(np.nanpercentile(boots[:, 0], 97.5)),
                       "ST": st, "ST_low": float(np.nanpercentile(boots[:, 1], 2.5)), "ST_high": float(np.nanpercentile(boots[:, 1], 97.5)),
                       "n_used": int(len(a)), "N": N, "output_mean": float(np.mean(np.concatenate([a, b]))),
                       "output_sd": float(np.std(np.concatenate([a, b]), ddof=1))})
sobol.close()
del values

# ---- feed scenarios and their candidates --------------------------------------------------------------------------
for block, prefix in (("feeds", "feed"), ("mc", "mc")):
    washes = Table(f"washes_{block}.parquet", f"every BioSTEAM run made for the {block} scenarios (one per distinct wash per scenario)")
    scenarios = Table(f"{prefix}_scenarios.parquet", "one row per scenario and downstream location: the feed, the plant, the three "
                      "optima (pathway, profit $/yr, emissions t CO2e/yr, circularity), front sizes and counts")
    candidates = Table(f"{prefix}_candidates.parquet", "every candidate pathway of every scenario and location, with its profit, "
                       "emissions and circularity and whether it lies on each exact Pareto front", batch=200000)
    planned = sum(1 for _ in manifest_rows(block)); done = 0
    for path in sorted(glob.glob(str(results / block / "*.washes.jsonl.gz"))):
        shard = Path(path).name.split(".")[0]
        for record in common.read_gz(path):
            washes.add(wash_record(block, shard, None, record["ok"], record.get("config"), record.get("row"), record.get("error"), record.get("wall_s")))
    washes.close()
    for path in sorted(glob.glob(str(results / block / "*.scenarios.jsonl.gz"))):
        for record in common.read_gz(path):
            done += 1
            scenario = record["scenario"]
            head = {"scenario_id": record["id"], "tonnes_per_yr": scenario["tonnes"], "plant": scenario.get("plant"),
                    "shared_plant_t_per_yr": scenario.get("shared_plant"), "energy_case": scenario.get("energy_case"),
                    "family": "+".join(scenario.get("family") or sorted(scenario["fractions"])), "feed": scenario.get("feed"),
                    "draw": scenario.get("draw"), "solvent_price_factor": scenario.get("solvent_price_factor"),
                    **{f"frac_{p}": float(scenario["fractions"].get(p, 0.0)) for p in POLYMERS},
                    **{f"ovr_{k}": v for k, v in (scenario.get("overrides") or {}).items()},
                    **{f"price_{p}": v for p, v in (scenario.get("prices") or {}).items()}}
            for location, result in record["locations"].items():
                answer, figure = result.get("answer") or {}, result.get("figure_data") or {}
                row = {**head, "location": location, "ok": bool(result.get("ok")) and "selected" in answer,
                       "error": json.dumps(result.get("error") or answer.get("error_type") or answer.get("message"))[:300] if "selected" not in answer else None,
                       "scenario_wall_s": record.get("wall_s"), "candidates": answer.get("candidates_considered"),
                       "washes_costed": len(answer.get("wash_comparison") or []),
                       "washes_failed": len(answer.get("washes_that_failed") or []),
                       "polymers_without_wash": ",".join(answer.get("polymers_with_no_admissible_wash") or []),
                       "warnings": len(answer.get("warnings") or []), "milp_agrees": answer.get("milp_agrees_with_enumeration"),
                       "solver": answer.get("solver") if isinstance(answer.get("solver"), str) else json.dumps(answer.get("solver"))}
                for objective, best in (answer.get("optima_by_objective") or {}).items():
                    if isinstance(best, dict):
                        row.update({f"{objective}_pathway": best.get("pathway"), f"{objective}_profit_usd_per_yr": best.get("profit_usd_per_yr"),
                                    f"{objective}_emissions_t_per_yr": best.get("emissions_t_co2e_per_yr"),
                                    f"{objective}_circularity": best.get("circularity_index"),
                                    f"{objective}_downstream": (best.get("downstream") or {}).get("key"),
                                    f"{objective}_n_washes": len(best.get("washes") or [])})
                fronts = {name: set(members) for name, members in (figure.get("fronts") or {}).items()}
                for name, members in fronts.items():
                    row[f"front_size_{name}"] = len(members)
                bounds = figure.get("circularity_upper_bounds") or {}
                row.update({f"bound_{k}": v for k, v in bounds.items() if isinstance(v, (int, float))})
                scenarios.add(row)
                for candidate in figure.get("candidates") or []:
                    steps = [f"{w['polymer']}/{w['solvent']}" for w in candidate.get("washes") or []]
                    candidates.add({"scenario_id": record["id"], "location": location, "label": candidate["label"],
                                    "n_washes": len(steps), "wash_1": steps[0] if len(steps) > 0 else None,
                                    "wash_2": steps[1] if len(steps) > 1 else None, "wash_3": steps[2] if len(steps) > 2 else None,
                                    "downstream_key": candidate.get("downstream_key"),
                                    "profit_usd_per_yr": candidate.get("profit_usd_per_yr"),
                                    "emissions_t_co2e_per_yr": candidate.get("emissions_t_co2e_per_yr"),
                                    "circularity_index": candidate.get("circularity_index"),
                                    "on_emissions_vs_profit": candidate["label"] in fronts.get("emissions_vs_profit", ()),
                                    "on_circularity_vs_profit": candidate["label"] in fronts.get("circularity_vs_profit", ()),
                                    "on_emissions_vs_circularity": candidate["label"] in fronts.get("emissions_vs_circularity", ())})
    scenarios.close(); candidates.close()
    index["files"][scenarios.name].update(planned_scenarios=planned, scenarios_done=done)

index["metrics"] = METRICS
index["source_commit"] = open(Path(__file__).resolve().parents[1] / "code" / "SOURCE_COMMIT").read().strip() if (Path(__file__).resolve().parents[1] / "code" / "SOURCE_COMMIT").exists() else None
index["total_biosteam_runs"] = sum(v.get("rows", 0) for k, v in index["files"].items() if k.startswith("washes_"))
json.dump(index, open(out / "DATA_INDEX.json", "w"), indent=1)
print("total BioSTEAM runs", index["total_biosteam_runs"])
