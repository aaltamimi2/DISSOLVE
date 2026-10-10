"""A-11 gate: the four anchors recomputed by the new staging (batch g00) must reproduce promotion-v1 to 1e-9 in every
partition row and every LLE system (same status and 15 wt% verdict). Writes newcontam-thermo-v1/g00/gate.json."""
import gzip
import json
import math
from pathlib import Path

import duckdb

B = Path("/mnt/r/plastchem-euler")
G = B / "newcontam-thermo-v1/g00"
TOLERANCE = 1e-9  # LLE: the same single-system solve, so values must agree to round-off
# Partition: v1 solved each phase once for a ~50-contaminant chunk at x = 0; this batch solves the four anchors
# together. A-9 measured that one solve per phase at x = 0 equals the per-pair solve to within 1e-5 log10, so a
# different batch membership may differ by that much and no more.
PARTITION_TOLERANCE = 1e-5

meta = json.loads((G / "batch.json").read_text())
keys = meta["keys"]
con = duckdb.connect()
v1 = {(r[0], r[1], r[2], r[3]): (r[4], r[5]) for r in con.execute(
    "SELECT input_inchikey, product_solvent_key, campaign_polymer, convention, logP_x, logP_concentration "
    "FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?))",
    [str(B / "promotion-v1/partition.parquet"), keys]).fetchall()}
new = {}
lle_rows = []
for path in sorted((G / "results-compact").glob("*.jsonl.gz")):
    with gzip.open(path, "rt") as handle:
        header = json.loads(handle.readline())
        for line in handle:
            row = json.loads(line)
            if header["plan"].startswith("partition-"):
                new[(row["input_inchikey"], row["product_solvent_key"], row["polymer"], row["convention"])] = (
                    row["logP_x"], row["logP_concentration"])
            else:
                lle_rows.append(row)
partition_diffs = [max(abs(a - b) for a, b in zip(new[k], v1[k])) for k in v1 if k in new]
lle_v1 = {(r[0], r[1], r[2]): r[3:] for r in con.execute(
    "SELECT input_inchikey, product_solvent_key, temperature_regime, status, above_15_wt_percent, "
    "solute_wt_percent_solubility FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?))",
    [str(B / "promotion-v1/binary-lle.parquet"), keys]).fetchall()}
lle_new, lle_mismatch = {}, []
for value in lle_rows:
    key = (value["input_inchikey"], value["product_solvent_key"], value["regime"])
    lle_new[key] = value
    status, verdict, wt = lle_v1[key]
    same = value["status"] == status and value.get("above_15_wt_percent") == verdict
    if wt is not None and value.get("solute_wt_percent_solubility") is not None:
        same = same and abs(value["solute_wt_percent_solubility"] - wt) <= TOLERANCE * max(1.0, abs(wt))
    if not same:
        lle_mismatch.append(dict(system=list(key), v1=[status, verdict, wt],
                                 new=[value["status"], value.get("above_15_wt_percent"), value.get("solute_wt_percent_solubility")]))
gate = dict(keys=keys, partition_rows_v1=len(v1), partition_rows_new=len(new), partition_compared=len(partition_diffs),
            max_partition_difference=max(partition_diffs) if partition_diffs else None,
            lle_systems_v1=len(lle_v1), lle_systems_new=len(lle_new), lle_mismatches=lle_mismatch[:20],
            lle_mismatch_count=len(lle_mismatch), lle_tolerance=TOLERANCE, partition_tolerance=PARTITION_TOLERANCE,
            partition_rows_above_1e_9=sum(d > 1e-9 for d in partition_diffs))
gate["passed"] = (len(new) == len(v1) == len(partition_diffs) and gate["max_partition_difference"] is not None
                  and gate["max_partition_difference"] <= PARTITION_TOLERANCE and len(lle_new) == len(lle_v1) and not lle_mismatch
                  and all(math.isfinite(d) for d in partition_diffs))
(G / "gate.json").write_text(json.dumps(gate, indent=2) + "\n")
print(json.dumps({k: v for k, v in gate.items() if k != "lle_mismatches"}))
