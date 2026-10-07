"""A-11 release promotion-v2: promotion-v1 unchanged, plus every contaminant computed since its freeze (tier-2 CHNO
structures that converged later, and the halogen tier), in the same schema. promotion-v1 is never modified.

    python3 scripts/build_promotion_v2.py check-mapping      rebuild the four g00 anchors' rows; they must equal v1's
    python3 scripts/build_promotion_v2.py build [--partial]  write /mnt/r/plastchem-euler/promotion-v2 (or -partial)

A complete release needs every halogen task disposed and every converged structure's thermodynamics collected;
--partial writes promotion-v2-partial with manifest status "partial" (DISSOLVE refuses it unless allow_preview)."""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import io
import json
import math
import shutil
import sys
from pathlib import Path

import duckdb

R = Path(__file__).resolve().parents[1]
B = Path("/mnt/r/plastchem-euler")
V1 = B / "promotion-v1"
THERMO = B / "newcontam-thermo-v1"
LLE_UNITS = ("mole fractions: mol/mol; weight percentages: g per 100 g solution; grid differences: percentage points; "
             "chemical potentials and tangent distances: RT")
LLE_SIGN = "Compositions are nonnegative; solubility is contaminant content in the solvent-rich phase. No signed transfer coefficient."
PART_SIGN = "log10 P(solvent/polymer); positive favors solvent"
PART_UNITS = "dimensionless log10 ratio; x=mole-fraction, concentration=mol/L"
SOLVER_SHA256 = hashlib.sha256((B / "phase8-v1/phase8_lle.py").read_bytes()).hexdigest()
ENSEMBLE_MANIFEST_SHA256 = hashlib.sha256((B / "phase8-v1/manifest.json").read_bytes()).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def columns(name):
    rows = duckdb.connect().execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(V1 / name)]).fetchall()
    return [(r[0], r[1]) for r in rows]


def compact_rows(batch):
    """(header, plan sha256, row) for every compacted plan of a batch."""
    meta = json.loads((THERMO / batch / "batch.json").read_text())
    for path in sorted((THERMO / batch / "results-compact").glob("*.jsonl.gz")):
        plan_sha = sha(THERMO / batch / "plans" / path.name.replace(".jsonl.gz", ".json"))
        with gzip.open(path, "rt") as handle:
            header = json.loads(handle.readline())
            for line in handle:
                yield meta, header, plan_sha, json.loads(line)


def partition_row(r, header, plan_sha):
    assert all(math.isfinite(r[k]) for k in ("logP_x", "logP_concentration")) and r["status"] == "predicted"
    return dict(input_inchikey=r["input_inchikey"], product_solvent_key=r["product_solvent_key"],
                campaign_polymer=r["polymer"], temperature_K=r["temperature_K"], convention=r["convention"],
                logP_x=r["logP_x"], logP_concentration=r["logP_concentration"], status=r["status"], failure_mode=None,
                solute_surface_sha256=r["solute_surface_sha256"], solvent_surface_sha256=r["solvent_surface_sha256"],
                polymer_ensemble_manifest_sha256=ENSEMBLE_MANIFEST_SHA256, parameterization="openCOSMO-RS 24a",
                sign_convention=PART_SIGN, units=PART_UNITS, solute_mole_fraction=0.0, reference_state="pure_component",
                cpu_models_json=json.dumps(header["cpu_models"]), activity_job_ids_json=json.dumps(header["job_ids"]),
                chunk_plan_sha256=plan_sha)


def lle_row(r, plan_sha, mw_contaminant, mw_solvent):
    """The v1 builder's mapping (build_phase9_release.py), from a supplement worker's system value."""
    valid = r["status"] in ("single_liquid_phase", "two_liquid_phases")
    ties = r.get("tie_lines", [])
    row = dict(input_inchikey=r["input_inchikey"], product_solvent_key=r["product_solvent_key"],
               temperature_K=r["temperature_K"], temperature_regime=r["regime"], status=r["status"],
               value_validated=valid, solute_mole_fraction_solubility=r.get("solute_mole_fraction_solubility"),
               solute_wt_percent_solubility=r.get("solute_wt_percent_solubility"),
               x_contaminant_solvent_rich=None, x_solvent_solvent_rich=None, x_contaminant_solute_rich=None,
               x_solvent_solute_rich=None, wt_percent_contaminant_solvent_rich=None,
               wt_percent_contaminant_solute_rich=None,
               tie_lines_json=json.dumps(ties, separators=(",", ":")),
               grid_checks_json=json.dumps(r["grid_checks"], separators=(",", ":")),
               above_15_mol_percent=r.get("above_15_mol_percent"), above_15_wt_percent=r.get("above_15_wt_percent"),
               grid_change_mol_percentage_points=r.get("grid_change_mol_percentage_points"),
               grid_change_wt_percentage_points=r.get("grid_change_wt_percentage_points"),
               max_chemical_potential_residual_RT=None, minimum_tangent_distance_RT=None,
               failure_mode=None if valid else r.get("failure_mode", r["status"]),
               solute_surface_sha256=r["solute_surface_sha256"], solvent_surface_sha256=r["solvent_surface_sha256"],
               parameterization="openCOSMO-RS 24a", solver_sha256=SOLVER_SHA256, units=LLE_UNITS,
               sign_convention=LLE_SIGN, cpu_model=r["execution"]["cpu_model"], job_id=str(r["execution"]["job_id"]),
               chunk_plan_sha256=plan_sha,
               failure_evidence_json=None if valid else json.dumps(
                   {k: r[k] for k in ("exception_type", "exception_message", "traceback", "failure_policy_sha256",
                                      "recovery_policy") if k in r}, separators=(",", ":")))
    if ties:
        t = ties[0]
        xa, xb = t["x_solvent_rich"], t["x_solute_rich"]
        row.update(x_contaminant_solvent_rich=xa, x_solvent_solvent_rich=1 - xa, x_contaminant_solute_rich=xb,
                   x_solvent_solute_rich=1 - xb,
                   wt_percent_contaminant_solvent_rich=100 * xa * mw_contaminant / (xa * mw_contaminant + (1 - xa) * mw_solvent),
                   wt_percent_contaminant_solute_rich=100 * xb * mw_contaminant / (xb * mw_contaminant + (1 - xb) * mw_solvent),
                   max_chemical_potential_residual_RT=max(x["chemical_potential_residual"] for x in ties),
                   minimum_tangent_distance_RT=min(x["minimum_tangent_distance_RT"] for x in ties))
    return row


def batch_rows(batch):
    spec = json.loads((THERMO / batch / "inputs.json").read_text())
    part, lle = [], []
    for meta, header, plan_sha, r in compact_rows(batch):
        if header["plan"].startswith("partition-"):
            part.append(partition_row(r, header, plan_sha))
        else:
            unit = spec["later"][r["input_inchikey"]]
            lle.append(lle_row(r, plan_sha, float(unit["molecular_weight_g_mol"]),
                               float(spec["solvents"][r["product_solvent_key"]]["molecular_weight_g_mol"])))
    return part, lle


def check_mapping():
    """Every v1 column of the four anchors, rebuilt from g00: identifiers, statuses, units and LLE values exactly
    (round-off), partition values within the A-9 batch bound; only per-run provenance may differ."""
    part, lle = batch_rows("g00")
    keys = sorted({r["input_inchikey"] for r in part})
    con = duckdb.connect()
    provenance = {"cpu_models_json", "activity_job_ids_json", "chunk_plan_sha256", "cpu_model", "job_id"}
    report = {}
    for name, rows, key_fields in (("partition.parquet", part, ("input_inchikey", "product_solvent_key", "campaign_polymer", "convention")),
                                   ("binary-lle.parquet", lle, ("input_inchikey", "product_solvent_key", "temperature_regime"))):
        cols = [c for c, _ in columns(name)]
        assert all(set(r) == set(cols) for r in rows), f"{name}: column set differs from v1"
        rel = con.execute(f"SELECT * FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?))", [str(V1 / name), keys])
        v1 = {tuple(r[cols.index(f)] for f in key_fields): dict(zip(cols, r)) for r in rel.fetchall()}
        worst, mismatched = 0.0, collections.Counter()
        for r in rows:
            old = v1[tuple(r[f] for f in key_fields)]
            for c in cols:
                if c in provenance:
                    continue
                a, b = r[c], old[c]
                if isinstance(a, float) and isinstance(b, float):
                    d = abs(a - b)
                    bound = 1e-5 if name == "partition.parquet" and c in ("logP_x", "logP_concentration") else 1e-9 * max(1.0, abs(b))
                    worst = max(worst, d) if c.startswith("logP") else worst
                    if d > bound:
                        mismatched[c] += 1
                elif c.endswith("_json") and a and b:
                    if json.loads(a) != json.loads(b):
                        diff = _json_close(json.loads(a), json.loads(b))
                        if not diff:
                            mismatched[c] += 1
                elif (a in ("", None)) != (b in ("", None)) or (a not in ("", None) and a != b):
                    mismatched[c] += 1
        report[name] = dict(rows=len(rows), v1_rows=len(v1), matched=len(rows) == len(v1), mismatched=dict(mismatched),
                            max_logP_difference=worst)
    report["passed"] = all(v["matched"] and not v["mismatched"] for v in report.values() if isinstance(v, dict))
    (THERMO / "g00/mapping-check.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))
    return report["passed"]


def _json_close(a, b, tolerance=1e-9):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_json_close(a[k], b[k], tolerance) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_json_close(x, y, tolerance) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(float(a) - float(b)) <= tolerance * max(1.0, abs(float(b)))
    return a == b


def _records(tier):
    folder = R / f"state/{tier}/records"
    return {path.stem: json.loads(path.read_text()) for path in folder.glob("*.json")}


def _identity_fields(record, status):
    """The v1 identity columns from a campaign record."""
    return dict(campaign_status_at_snapshot=status, perceived_inchikey=record.get("perceived_inchikey") or "",
                identity_match_basis=record.get("identity_match_basis") or "",
                perception_engines_agreeing_on_perceived_key=json.dumps(record.get("perception_engines_agreeing_on_perceived_key") or []),
                surface_sha256=record.get("surface_sha256") or "",
                source_record_sha256=hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest(),
                failure_mode=(record.get("failure_mode") or "") if status != "converged" else "",
                failure_detail=(record.get("error") or "")[:500] if status != "converged" else "")


def build(partial, frozen=False):
    """frozen (owner, 2026-10-07: "commit without those additional ones still running"): a complete release of the
    cohort as it stands, like promotion-v1: every structure whose 640 partition and 64 LLE rows are all in; the rest
    are status rows (still running, or thermodynamics pending) for a later release."""
    target = B / ("promotion-v2-partial" if partial else "promotion-v2")
    assert not (target / "manifest.json").exists() or partial, f"{target} is sealed; never overwrite a release"
    if target.exists():
        shutil.rmtree(target)
    batches = sorted({p.parent.name for pattern in ("*/collection.json", "*/collection-partial.json")
                      for p in THERMO.glob(pattern) if p.parent.name != "g00"})
    v1_manifest = json.loads((V1 / "manifest.json").read_text())
    with gzip.open(V1 / "contaminants.csv.gz", "rt") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        cohort = {r["input_inchikey"]: r for r in reader}
    served_v1 = {k for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged"}
    part, lle = [], []
    for batch in batches:
        p_rows, l_rows = batch_rows(batch)
        part += p_rows
        lle += l_rows
    computed = collections.Counter(r["input_inchikey"] for r in part)
    lle_counts = collections.defaultdict(collections.Counter)
    for r in lle:
        lle_counts[r["input_inchikey"]]["qualified" if r["value_validated"] else "unresolved"] += 1
    # a structure is in only with every row: 32 solvents x 10 polymers x 2 conventions, and 32 solvents x 2 regimes
    whole = {k for k, n in computed.items() if n == 640 and sum(lle_counts[k].values()) == 64}
    dropped = sorted(set(computed) | set(lle_counts)) if not whole else sorted((set(computed) | set(lle_counts)) - whole)
    part = [r for r in part if r["input_inchikey"] in whole]
    lle = [r for r in lle if r["input_inchikey"] in whole]
    computed = collections.Counter(r["input_inchikey"] for r in part)
    lle_counts = {k: v for k, v in lle_counts.items() if k in whole}
    assert not set(computed) & served_v1, "a new row repeats a v1 contaminant"
    assert all(n == 640 for n in computed.values()), "every new contaminant needs 32 x 10 x 2 partition rows"
    assert set(lle_counts) == set(computed) and all(sum(c.values()) == 64 for c in lle_counts.values())
    # tier 2: every row's status from the campaign records as they stand now
    tier2 = _records("tier2-v1")
    for key, row in cohort.items():
        if row["tier"] != "tier2" or key in served_v1:
            continue
        record = tier2.get(key, {})
        status = "converged" if key in computed else ("failed" if record.get("status") == "failed" else row["campaign_status_at_snapshot"])
        if record:
            row.update(_identity_fields(record, status))
    # halogen tier: one identity row per pinned structure
    manifest = json.loads((R / "state/halogen-v1/halogen/manifest.json").read_text())
    halogen = _records("halogen-v1")
    for m in manifest["molecules"]:
        record = halogen.get(m["inchikey"], {})
        status = "converged" if m["inchikey"] in computed else record.get("status") or "not_yet_run"
        if status == "converged_identity_pending":
            status = "awaiting_verification"
        row = {f: "" for f in fields}
        row.update(input_inchikey=m["inchikey"], name=m["name"], smiles=m["smiles"], cas=m["cas"],
                   plastchem_id=m["plastchem_id"], molecular_weight_g_mol=m["molecular_weight_g_mol"],
                   atom_count=str(m["atoms"]), group="CHNO+halogen", tier="halogen", in_phase83_snapshot="False",
                   source_input_sha256=manifest["input"]["sha256"])
        row.update(_identity_fields(record, status) if record else dict(campaign_status_at_snapshot=status))
        cohort[m["inchikey"]] = row
    for key, row in cohort.items():
        if key in computed:
            row.update(partition_predicted_rows=str(computed[key]), partition_failed_rows="0",
                       lle_qualified_rows=str(lle_counts[key]["qualified"]),
                       lle_unresolved_or_failed_rows=str(lle_counts[key]["unresolved"]),
                       all_requested_quantities_evaluated="True",
                       all_requested_quantities_qualified=str(lle_counts[key]["unresolved"] == 0))
    pending = [k for k, r in cohort.items() if r["tier"] == "halogen" and r["campaign_status_at_snapshot"] not in ("converged", "failed")]
    unthermo = [k for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged" and k not in computed and k not in served_v1]
    if not partial and not frozen:
        assert not pending, f"{len(pending)} halogen structures not yet disposed"
        assert not unthermo, f"{len(unthermo)} converged structures without thermodynamics"
    for key, row in cohort.items():  # say what a status row is waiting for
        if row["tier"] != "halogen" or key in computed:
            continue
        status = row["campaign_status_at_snapshot"]
        if status == "converged":
            row.update(campaign_status_at_snapshot="thermodynamics_pending",
                       exclusion_reason="ORCA converged; partition and LLE not finished at the frozen snapshot")
        elif status not in ("failed",):
            row.update(campaign_status_at_snapshot="running",
                       exclusion_reason="ORCA still running at the frozen snapshot")
        elif row.get("failure_mode") in ("scheduler_signal_15", "slurm_preempted"):
            row.update(campaign_status_at_snapshot="running",
                       exclusion_reason="preempted by the scheduler; rerun still running at the frozen snapshot")
    target.mkdir(parents=True)
    # Work on local disk: a DuckDB file and its WAL on the network drive turn every write into a round trip.
    work = R / "state/halogen-v1/release-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    con = duckdb.connect(str(work / "build.duckdb"))
    con.execute("SET threads=2")
    con.execute("SET memory_limit='1GB'")
    con.execute("SET temp_directory=?", [str(work / "tmp")])
    for name, rows in (("partition.parquet", part), ("binary-lle.parquet", lle)):
        cols = columns(name)
        rows_file = work / f"{name}.jsonl"
        with rows_file.open("w") as handle:
            for r in rows:
                handle.write(json.dumps({c: r[c] for c, _ in cols}) + "\n")
        types = ", ".join(f"'{c}': '{t}'" for c, t in cols)
        con.execute("DROP TABLE IF EXISTS fresh")
        con.execute(f"CREATE TABLE fresh AS SELECT * FROM read_parquet(?) LIMIT 0", [str(V1 / name)])
        if rows:
            con.execute(f"INSERT INTO fresh SELECT * FROM read_json(?, format='newline_delimited', columns={{{types}}})",
                        [str(rows_file)])
        assert con.execute("SELECT count(*) FROM fresh").fetchone()[0] == len(rows)
        local_out = work / name
        # COPY ... TO takes no bound parameter; both paths are this script's own, with no quotes in them
        assert "'" not in str(V1 / name) + str(local_out)
        con.execute(f"COPY (SELECT * FROM read_parquet('{V1 / name}') UNION ALL SELECT * FROM fresh) "
                    f"TO '{local_out}' (FORMAT parquet, COMPRESSION zstd)")
        shutil.copyfile(local_out, target / name)
    keys = ("input_inchikey, product_solvent_key, campaign_polymer, convention", "input_inchikey, product_solvent_key, temperature_regime")
    for name, key in zip(("partition.parquet", "binary-lle.parquet"), keys):
        duplicates = con.execute(f"SELECT count(*) FROM (SELECT {key}, count(*) n FROM read_parquet(?) GROUP BY ALL HAVING n <> 1)",
                                 [str(target / name)]).fetchone()[0]
        assert duplicates == 0, f"{name}: duplicate rows"
    con.close()
    shutil.rmtree(work)
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for key in sorted(cohort):
        writer.writerow(cohort[key])
    raw = text.getvalue().encode()
    with gzip.GzipFile(target / "contaminants.csv.gz", "wb", mtime=0) as handle:
        handle.write(raw)
    for name in ("polymer-product-map.csv", "product-mapping-evidence.json"):
        shutil.copyfile(V1 / name, target / name)
    for folder in ("provenance", "validation"):  # file by file: R: refuses copied permissions and timestamps
        for source in sorted((V1 / folder).rglob("*")):
            if source.is_file():
                destination = target / "v1" / source.relative_to(V1)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    provenance = target / "provenance-a11"
    provenance.mkdir()
    for batch in batches:
        for name in ("batch.json", "inputs.json", "collection.json", "collection-partial.json"):
            if (THERMO / batch / name).exists():
                shutil.copyfile(THERMO / batch / name, provenance / f"{batch}-{name}")
    for name in ("gate.json", "mapping-check.json"):
        shutil.copyfile(THERMO / "g00" / name, provenance / f"g00-{name}")
    statuses = collections.Counter((r["tier"], r["campaign_status_at_snapshot"]) for r in cohort.values())
    served = served_v1 | set(computed)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status="partial" if partial else "complete",
                   frozen_snapshot=frozen, structures_with_incomplete_rows_left_out=len(dropped),
                   parent_release="promotion-v1", parent_manifest_sha256=sha(V1 / "manifest.json"),
                   computed_contaminants=len(served), added_since_v1=len(computed),
                   added_tier2=sum(cohort[k]["tier"] == "tier2" for k in computed),
                   added_halogen=sum(cohort[k]["tier"] == "halogen" for k in computed),
                   identity_status_rows=len(cohort), statuses={f"{t}/{s}": n for (t, s), n in sorted(statuses.items())},
                   partition_rows=3731200 + len(part), LLE_rows=373120 + len(lle), batches=batches,
                   pending_halogen=len(pending), converged_without_thermodynamics=len(unthermo),
                   cohort_sha256=hashlib.sha256(raw).hexdigest())
    (target / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    readme = README.format(**summary)
    if frozen:
        readme += (f"\nFrozen snapshot (owner, 2026-10-07: commit without the structures still running): every structure "
                   f"whose rows were all in is served; {summary['pending_halogen']} halogen runs were still in ORCA and "
                   f"{summary['converged_without_thermodynamics'] + summary['structures_with_incomplete_rows_left_out']} "
                   "had converged without finished partition and LLE rows. They are status rows here "
                   "(running, thermodynamics_pending) and go into a later release.\n")
    (target / "README.md").write_text(readme)
    files = {str(p.relative_to(target)): dict(bytes=p.stat().st_size, sha256=sha(p))
             for p in sorted(target.rglob("*")) if p.is_file()}
    manifest_out = dict(utc=summary["utc"], status=summary["status"], files=files,
                        payload_bytes=sum(f["bytes"] for f in files.values()), cohort_sha256=summary["cohort_sha256"],
                        parent_release=dict(name="promotion-v1", manifest_sha256=summary["parent_manifest_sha256"]))
    (target / "manifest.json").write_text(json.dumps(manifest_out, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "statuses"}))


README = """# Contaminant thermodynamic promotion release v2

Snapshot: {utc}. Status: {status}. This is promotion-v1, unchanged, plus every contaminant computed since its freeze
(amendment A-11, 2026-10-06): tier-2 CHNO structures (500-700 g/mol) that converged after the v1 snapshot, and the
halogen tier, PlastChem structures of C, H, N and O with F, Cl, Br or I up to 700 g/mol. {computed_contaminants}
contaminants are computed, {added_since_v1} of them new ({added_tier2} tier-2, {added_halogen} halogen).

The new rows use the same frozen recipe and the same schema: ORCA 6.1.1 BP86/def2-TZVP(-f) optimisation and
BP86/def2-TZVPD COSMORS(Water) surfaces, one conformer, identity by D-IDENT; openCOSMO-RS 24a at 298.15 K and solute
x = 0 against the same 32 panel solvents and the same 10 polymer conformer ensembles, both conventions; binary LLE in
the RT and high regimes with the same solver (phase8_lle.py). They were computed by the supplement workers on Euler
(AMD EPYC 7763), batch by batch (newcontam-thermo-v1). Gate g00 recomputed DEP, DBP, BBP and DEHP: all 256 LLE systems
reproduce v1 exactly, and all 2,560 partition rows reproduce it within the A-9 bound for a different batch membership
(max 5.9e-6 log10, bound 1e-5); every release column was rebuilt from those results and matched v1's.

Iodine: def2 basis sets put an ECP on iodine, and the installed openCOSMORS24a class has no tau_53, which enters only
dG_solv, not the ln gamma used here. Failures are status rows; nothing is interpolated for a failed or unrun structure.
`contaminants.csv.gz` records every pinned structure with its status. v1's provenance and validation are under `v1/`.
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check-mapping", "build"])
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--frozen", action="store_true", help="a complete release of the cohort as it stands now")
    args = parser.parse_args()
    if args.action == "check-mapping":
        sys.exit(0 if check_mapping() else 1)
    build(args.partial, frozen=args.frozen)
