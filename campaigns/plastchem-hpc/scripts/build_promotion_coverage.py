"""A-13 releases, promotion-v4 onward (charter A-13 item 7a): the previous release unchanged, plus every structure the
coverage lane has completed since, in the same schema and manifest. Generalises build_promotion_v3.py, which stays as
it is. A structure is complete when its surface is accepted (D-IDENT) and all its rows are in: 32 solvents x 10 polymer
ensembles x 2 conventions of partition (640) and 32 solvents x 2 regimes of binary LLE (64).

What a release adds to its parent:
- the parent's halogen status rows of the A-11 freeze (thermodynamics_pending, running) whose structure is now complete,
  keeping their PlastChem identity (as promotion-v3 did for PFTriDA), and those that ended in a final failure, as failed
  status rows;
- coverage-tier structures (tier "coverage", group "coverage-A13") that are complete, and those that ended in a final
  failure (preparation failures, D-IDENT rejections and the like) as failed status rows; a structure still in ORCA,
  waiting for a retry, or without all its rows waits for a later release;
- parent aliases (item 7b): each PlastChem entry served through its computed neutral parent (a salt, an ion, a hydrate)
  gets its name and CAS as aliases of the parent's row in parent-aliases.csv, checked here against the refusals
  promote_opencosmo_release applies (an unknown InChIKey; an alias that would also name another contaminant: such an
  alias is listed with offered "no"). The 200 entries whose parent promotion-v3 already served come in with the first
  release, later salts with their parent. The parent's plastchem_id lists the entry too (item 7a: a row lists every
  PlastChem entry it serves, direct and as parent), so the coverage triage and the families find it by its ID.

Names (item 7b, the A-11 rule): a coverage row takes its own name (PlastChem's, or PubChem's for a neutral parent with no
PlastChem entry; from the chunk manifest); a name that is only a registry number (CAS, EINECS, "CID n") takes the
workbook's IUPAC name, as the archives do. Rows the parent holds keep their identity.

    python3 scripts/build_promotion_coverage.py build N [--dry-run]      (writes /mnt/r/plastchem-euler/promotion-vN)
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import io
import json
import re
import shutil
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_promotion_v2 import _identity_fields, lle_row, partition_row, sha  # noqa: E402

R = Path(__file__).resolve().parents[1]
B = Path("/mnt/r/plastchem-euler")
THERMO_ROOTS = (B / "newcontam-thermo-v1", B / "coverage-thermo-v1")
#: batches whose rows never enter a release: g00 recomputed v1's anchors; p00 and p01 hold the A-12 anions
NOT_RELEASED_BATCHES = {"g00", "p00", "p01"}
P = R / "state/coverage-v1"
HALOGEN = R / "state/halogen-v1"
WORKLIST = R / "inputs/plastchem_coverage_a13_worklist.csv"
WORKLIST_SHA256 = "40b4ca4ef3237e7947dfd77beab339a0acca5beb4fb732b135b0f8ce228683e2"
PARENT_ALIASES = R / "inputs/plastchem_coverage_a13_parent_aliases.csv"
PARENT_ALIASES_SHA256 = "5466119e66c53f997912d23e4e562b0c8547b948f159a7cc15c25dda724a543c"
CENSUS = R / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"
CENSUS_SHA256 = "9b1b68e40504011798241502b19c74d2889aa9af0eb611d5f81e41d94c4a51a1"
COVERAGE_TSV = R / "reports/plastchem-coverage-2026-10-08/PLASTCHEM_COVERAGE.tsv"
#: failure modes that are not final: the structure is retried (A-13 5d) and waits for a later release
NOT_FINAL = {"slurm_timeout", "walltime_censored", "scheduler_signal_10", "scheduler_signal_15", "slurm_preempted"}
PLACEHOLDER = re.compile(r"^(\d{2,7}-\d{2}-\d|CID \d+|EINECS \d{3}-\d{3}-\d)$", re.IGNORECASE)
ALIAS_FIELDS = ["plastchem_id", "alias", "kind", "form", "input_inchikey", "offered", "note"]


def key_of(value):
    """DISSOLVE's alias key (dissolve.contaminants._key): case-folded, whitespace collapsed."""
    return " ".join(str(value or "").strip().casefold().split())


def ids_of(value):
    return [i.strip() for i in re.split(r"[;,]", value or "") if i.strip()]


def joined(ids):
    return ";".join(sorted(set(ids), key=lambda i: (len(i), i)))


def final(record):
    """A record that will not change: converged, or failed with a final failure mode. A run the scheduler killed for
    memory or preempted is resubmitted (memory raised per row only after an out-of-memory kill, A-13), whatever ORCA's
    own exit says, so it is not final; a second out-of-memory kill, in the 8G retry of the first, is (one such retry)."""
    status = record.get("status")
    if status == "converged":
        return True
    state = str((record.get("slurm_accounting") or {}).get("state", ""))
    earlier = str((((record.get("input") or {}).get("retry_of") or {}).get("slurm_accounting") or {}).get("state", ""))
    if state.startswith("PREEMPTED") or (state.startswith("OUT_OF_MEMORY") and not earlier.startswith("OUT_OF_MEMORY")):
        return False
    return status == "failed" and record.get("failure_mode") not in NOT_FINAL and not record.get("retry_required")


def batch_of_keys():
    """InChIKey -> (thermodynamics root, batch) for every structure a collected batch staged."""
    out = {}
    for root in THERMO_ROOTS:
        for meta in sorted(root.glob("*/batch.json")):
            batch = meta.parent.name
            if batch in NOT_RELEASED_BATCHES or not (meta.parent / "collection.json").exists():
                continue
            for key in json.loads(meta.read_text())["keys"]:
                assert key not in out, f"{key} staged twice"
                out[key] = (root, batch)
    return out


def rows_for(keys):
    """Partition and LLE rows (the v1 schema) of the given InChIKeys, from the batches that staged them."""
    wanted = collections.defaultdict(set)
    where = batch_of_keys()
    for key in keys:
        wanted[where[key]].add(key)
    part, lle = [], []
    for (root, batch), batch_keys in sorted(wanted.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        spec = json.loads((root / batch / "inputs.json").read_text())
        for path in sorted((root / batch / "results-compact").glob("*.jsonl.gz")):
            plan = json.loads((root / batch / "plans" / path.name.replace(".jsonl.gz", ".json")).read_text())
            plan_keys = set(plan["keys"]) if "keys" in plan else {s["inchikey"] for s in plan["systems"]}
            if not plan_keys & batch_keys:
                continue
            plan_sha = sha(root / batch / "plans" / path.name.replace(".jsonl.gz", ".json"))
            with gzip.open(path, "rt") as handle:
                header = json.loads(handle.readline())
                for line in handle:
                    r = json.loads(line)
                    if r["input_inchikey"] not in batch_keys:
                        continue
                    if header["plan"].startswith("partition-"):
                        part.append(partition_row(r, header, plan_sha))
                    else:
                        unit = spec["later"][r["input_inchikey"]]
                        lle.append(lle_row(r, plan_sha, float(unit["molecular_weight_g_mol"]),
                                           float(spec["solvents"][r["product_solvent_key"]]["molecular_weight_g_mol"])))
    return part, lle, {where[k][1] for k in keys}


def census():
    assert sha(CENSUS) == CENSUS_SHA256
    return {r["plastchem_ID"]: r for r in csv.DictReader(CENSUS.open())}


def entry_name(entry):
    return next((entry[k].strip() for k in ("pubchem_name", "iupac_name", "cas") if (entry[k] or "").strip() not in ("", "nan")), "")


def entry_cas(entry):
    return (entry["cas_fixed"] or entry["cas"] or "").strip("'").strip()


def readable(name, ids, workbook):
    """A registry-number name becomes the workbook's IUPAC name (make_contaminants_tsv.py does the same)."""
    if not PLACEHOLDER.match(name.strip()):
        return name
    return next((workbook[i]["iupac_name"] for i in ids if i in workbook and workbook[i]["iupac_name"] not in ("", "nan")), name)


def coverage_molecules(records=None):
    """InChIKey -> chunk manifest molecule, over every chunk. A structure two chunks list (c00 holds the owner's requests
    ahead of the work-list order) takes the chunk that ran it, from its record, else the first that lists it."""
    out = {}
    for path in sorted(P.glob("c[0-9][0-9]/manifest.json")):
        for m in json.loads(path.read_text())["molecules"]:
            if m["inchikey"] in out and (records or {}).get(m["inchikey"], {}).get("group") != path.parent.name:
                continue
            out[m["inchikey"]] = dict(m, chunk_manifest=path)
    return out


def as_parent_entries(keys):
    """PlastChem entries served through each coverage structure as its neutral parent (salt, ion, hydrate), from the
    coverage triage the work list was built from: {inchikey: [(plastchem_id, name, cas, form)]}."""
    out = collections.defaultdict(list)
    for r in csv.DictReader(COVERAGE_TSV.open(), delimiter="\t"):
        if r["bucket"] == "to compute" and r["species_inchikey"] in keys and r["detail"].startswith("parent of"):
            form = r["detail"].split(";")[0].removeprefix("parent of a ").removeprefix("parent of an ").strip()
            out[r["species_inchikey"]].append((r["plastchem_id"], r["name"], r["cas"], form))
    return out


def build(number, dry_run=False):
    parent_dir, target = B / f"promotion-v{number - 1}", B / f"promotion-v{number}"
    assert (parent_dir / "manifest.json").exists(), f"{parent_dir} is not a sealed release"
    assert json.loads((parent_dir / "manifest.json").read_text())["status"] == "complete"
    assert not (target / "manifest.json").exists(), f"{target} is sealed; never overwrite a release"
    assert sha(WORKLIST) == WORKLIST_SHA256 and sha(PARENT_ALIASES) == PARENT_ALIASES_SHA256
    workbook = census()
    with gzip.open(parent_dir / "contaminants.csv.gz", "rt") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        cohort = {r["input_inchikey"]: r for r in reader}
    served_parent = {k for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged"}
    # 1. what is new: halogen status rows of the parent and coverage records, each at its final outcome
    halogen = {p.stem: json.loads(p.read_text()) for p in (HALOGEN / "records").glob("*.json")}
    coverage = {p.stem: json.loads(p.read_text()) for p in (P / "records").glob("*.json")}
    molecules = coverage_molecules(coverage)
    status_rows = {}  # parent status rows that now have a final outcome
    for k, r in cohort.items():
        if r["campaign_status_at_snapshot"] == "converged":
            continue
        if coverage.get(k, {}).get("status") == "converged":  # an earlier tier's failure retried by this lane (A-13 5d)
            status_rows[k] = coverage[k]
        elif r["tier"] == "halogen" and r["campaign_status_at_snapshot"] in ("thermodynamics_pending", "running") \
                and final(halogen.get(k, {})):
            status_rows[k] = halogen[k]
    new_rows = {k: r for k, r in coverage.items() if k not in cohort and k in molecules and final(r)}
    assert not set(status_rows) & set(new_rows)
    converged = sorted(k for k, r in {**status_rows, **new_rows}.items() if r["status"] == "converged")
    staged = batch_of_keys()
    part, lle, batches = rows_for([k for k in converged if k in staged])
    computed = collections.Counter(r["input_inchikey"] for r in part)
    lle_counts = collections.defaultdict(collections.Counter)
    for r in lle:
        lle_counts[r["input_inchikey"]]["qualified" if r["value_validated"] else "unresolved"] += 1
    whole = {k for k in computed if computed[k] == 640 and sum(lle_counts[k].values()) == 64}
    waiting = sorted(set(converged) - whole)  # converged, rows not all in yet: a later release
    part = [r for r in part if r["input_inchikey"] in whole]
    lle = [r for r in lle if r["input_inchikey"] in whole]
    assert not whole & served_parent, "a new row repeats a contaminant the parent serves"
    surfaces = {k: (status_rows.get(k) or new_rows.get(k))["surface_sha256"] for k in whole}
    assert all(r["solute_surface_sha256"] == surfaces[r["input_inchikey"]] for r in part + lle), \
        "a row was computed from another surface than the accepted one"
    # 2. the identity rows
    completed, newly_failed, added, added_failed = [], [], [], []
    for key, record in status_rows.items():
        if record["status"] == "converged" and key not in whole:
            continue
        row = cohort[key]
        status = "converged" if key in whole else "failed"
        row.update(_identity_fields(record, status), exclusion_reason="")
        (completed if key in whole else newly_failed).append(key)
    for key, record in new_rows.items():
        if record["status"] == "converged" and key not in whole:
            continue
        m = molecules[key]
        status = "converged" if key in whole else "failed"
        row = {f: "" for f in fields}
        row.update(input_inchikey=key, name=readable(m["name"], ids_of(m["plastchem_id"]), workbook), smiles=m["smiles"],
                   cas=m["cas"], plastchem_id=m["plastchem_id"], molecular_weight_g_mol=m["molecular_weight_g_mol"],
                   atom_count=str(m["atoms"]), group="coverage-A13", tier="coverage", in_phase83_snapshot="False",
                   source_input_sha256=WORKLIST_SHA256)
        row.update(_identity_fields(record, status))
        cohort[key] = row
        (added if key in whole else added_failed).append(key)
    for key in whole:
        cohort[key].update(partition_predicted_rows=str(computed[key]), partition_failed_rows="0",
                           lle_qualified_rows=str(lle_counts[key]["qualified"]),
                           lle_unresolved_or_failed_rows=str(lle_counts[key]["unresolved"]),
                           all_requested_quantities_evaluated="True",
                           all_requested_quantities_qualified=str(lle_counts[key]["unresolved"] == 0))
    # 3. parent aliases: the parent release's, the 200 pinned ones (first release), and the salts of new structures
    served = {k for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged"}
    previous = list(csv.DictReader((parent_dir / "parent-aliases.csv").open())) if (parent_dir / "parent-aliases.csv").exists() else []
    entries = {}  # plastchem_id -> (parent inchikey, name, cas, form)
    for r in previous:
        entries.setdefault(r["plastchem_id"], (r["input_inchikey"], None, None, r["form"]))
    for r in csv.DictReader(PARENT_ALIASES.open()):
        entries.setdefault(r["plastchem_id"], (r["parent_inchikey"], r["name"], r["cas"], r["form"]))
    for key, items in as_parent_entries(served).items():  # every served parent, whichever release computed it
        for pid, name, cas, form in items:
            entries.setdefault(pid, (key, name, cas, form))
    for pid, (key, *_rest) in entries.items():
        assert key in served, f"PlastChem entry {pid}: its parent {key} is not served"
        ids = ids_of(cohort[key]["plastchem_id"])
        if pid not in ids:
            cohort[key]["plastchem_id"] = joined(ids + [pid])
    # the aliases every row already answers to, as promote_opencosmo_release builds them
    taken = collections.defaultdict(set)
    for key, r in cohort.items():
        for value in (r["name"], r["cas"], key, r["perceived_inchikey"], *(f"plastchem {i}" for i in ids_of(r["plastchem_id"]))):
            if value:
                taken[key_of(value)].add(key)
    if (parent_dir / "publication-labels.csv").exists():
        for r in csv.DictReader((parent_dir / "publication-labels.csv").open()):
            if r["offered"] == "yes":
                taken[key_of(r["alias"])].add(r["input_inchikey"])
    alias_rows = [r for r in previous]
    have = {(r["plastchem_id"], r["kind"]) for r in previous}
    for pid, (key, name, cas, form) in sorted(entries.items(), key=lambda item: int(item[0])):
        for kind, value in (("name", name), ("cas", cas)):
            if (pid, kind) in have or not value or value == "nan":
                continue
            others = taken.get(key_of(value), set()) - {key}
            note = f"also names {sorted(others)[0]}; not offered" if others else ""
            alias_rows.append(dict(plastchem_id=pid, alias=value, kind=kind, form=form, input_inchikey=key,
                                   offered="no" if others else "yes", note=note))
            if not others:
                taken[key_of(value)].add(key)
    statuses = collections.Counter((r["tier"], r["campaign_status_at_snapshot"]) for r in cohort.values())
    report = dict(release=target.name, parent=parent_dir.name, completed_status_rows=len(completed),
                  newly_failed_status_rows=len(newly_failed), added_coverage=len(added), added_coverage_failed=len(added_failed),
                  converged_waiting_for_rows=len(waiting), new_partition_rows=len(part), new_lle_rows=len(lle),
                  batches=sorted(batches), parent_aliases=len(alias_rows),
                  parent_aliases_offered=sum(r["offered"] == "yes" for r in alias_rows),
                  computed_contaminants=len(served), identity_status_rows=len(cohort))
    if dry_run:
        print(json.dumps(dict(report, statuses={f"{t}/{s}": n for (t, s), n in sorted(statuses.items())},
                              waiting=waiting[:10]), indent=1))
        return report
    assert whole, "nothing new to release"
    # 4. the tables: parent rows unchanged, new rows appended (built on local disk; R: makes DuckDB crawl)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    work = P / "release-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    con = duckdb.connect(str(work / "build.duckdb"))
    con.execute("SET threads=2")
    con.execute("SET memory_limit='1GB'")
    con.execute("SET temp_directory=?", [str(work / "tmp")])
    for name, rows in (("partition.parquet", part), ("binary-lle.parquet", lle)):
        cols = [(c, t) for c, t, *_ in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(parent_dir / name)]).fetchall()]
        rows_file = work / f"{name}.jsonl"
        with rows_file.open("w") as handle:
            for r in rows:
                handle.write(json.dumps({c: r[c] for c, _ in cols}) + "\n")
        types = ", ".join(f"'{c}': '{t}'" for c, t in cols)
        con.execute("DROP TABLE IF EXISTS fresh")
        con.execute("CREATE TABLE fresh AS SELECT * FROM read_parquet(?) LIMIT 0", [str(parent_dir / name)])
        con.execute(f"INSERT INTO fresh SELECT * FROM read_json(?, format='newline_delimited', columns={{{types}}})",
                    [str(rows_file)])
        assert con.execute("SELECT count(*) FROM fresh").fetchone()[0] == len(rows)
        local_out = work / name
        assert "'" not in str(parent_dir / name) + str(local_out)
        con.execute(f"COPY (SELECT * FROM read_parquet('{parent_dir / name}') UNION ALL SELECT * FROM fresh) "
                    f"TO '{local_out}' (FORMAT parquet, COMPRESSION zstd)")
        shutil.copyfile(local_out, target / name)
    for name, key in zip(("partition.parquet", "binary-lle.parquet"),
                         ("input_inchikey, product_solvent_key, campaign_polymer, convention",
                          "input_inchikey, product_solvent_key, temperature_regime")):
        duplicates = con.execute(f"SELECT count(*) FROM (SELECT {key}, count(*) n FROM read_parquet(?) GROUP BY ALL "
                                 "HAVING n <> 1)", [str(target / name)]).fetchone()[0]
        assert duplicates == 0, f"{name}: duplicate rows"
        charged = con.execute("SELECT count(*) FROM read_parquet(?) WHERE input_inchikey LIKE '%-M' OR input_inchikey LIKE '%-L'",
                              [str(target / name)]).fetchone()[0]
        assert charged == 0, f"{name}: a charged structure's rows"
        parent_rows = con.execute("SELECT count(*) FROM read_parquet(?)", [str(parent_dir / name)]).fetchone()[0]
        new_count = con.execute("SELECT count(*) FROM read_parquet(?)", [str(target / name)]).fetchone()[0]
        assert new_count == parent_rows + (len(part) if name == "partition.parquet" else len(lle))
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
    with (target / "parent-aliases.csv").open("w", newline="") as handle:
        out = csv.DictWriter(handle, fieldnames=ALIAS_FIELDS, lineterminator="\n")
        out.writeheader()
        out.writerows(sorted(alias_rows, key=lambda r: (int(r["plastchem_id"]), r["kind"])))
    for name in ("polymer-product-map.csv", "product-mapping-evidence.json", "publication-labels.csv"):
        shutil.copyfile(parent_dir / name, target / name)
    provenance = target / "provenance-a13"
    provenance.mkdir()
    for root, batch in sorted({staged[k] for k in whole}, key=lambda item: item[1]):
        for name in ("batch.json", "inputs.json", "collection.json"):
            shutil.copyfile(root / batch / name, provenance / f"{batch}-{name}")
    for path in sorted(P.glob("c[0-9][0-9]/manifest.json")):
        if any(molecules[k]["chunk_manifest"] == path for k in added + added_failed):
            shutil.copyfile(path, provenance / f"{path.parent.name}-manifest.json")
    for path in sorted(P.glob("r[0-9][0-9]/manifest.json")):  # a retry round that ran an added structure
        if any(coverage.get(k, {}).get("group") == path.parent.name for k in added + added_failed):
            shutil.copyfile(path, provenance / f"{path.parent.name}-manifest.json")
    for path in (WORKLIST, PARENT_ALIASES):
        shutil.copyfile(path, provenance / path.name)
    parent_summary = json.loads((parent_dir / "summary.json").read_text())
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status="complete",
                   parent_release=parent_dir.name, parent_manifest_sha256=sha(parent_dir / "manifest.json"),
                   computed_contaminants=len(served), added_since_parent=len(whole),
                   added_halogen_status_rows_completed=len(completed), added_coverage=len(added),
                   status_rows_now_failed=len(newly_failed), coverage_failed_status_rows=len(added_failed),
                   converged_waiting_for_rows=len(waiting), identity_status_rows=len(cohort),
                   statuses={f"{t}/{s}": n for (t, s), n in sorted(statuses.items())},
                   partition_rows=parent_summary["partition_rows"] + len(part), LLE_rows=parent_summary["LLE_rows"] + len(lle),
                   batches=sorted(batches), parent_aliases=len(alias_rows),
                   parent_aliases_offered=sum(r["offered"] == "yes" for r in alias_rows),
                   publication_labels=parent_summary.get("publication_labels"),
                   cohort_sha256=hashlib.sha256(raw).hexdigest())
    assert summary["computed_contaminants"] == parent_summary["computed_contaminants"] + len(whole)
    (target / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (target / "README.md").write_text(README.format(name=target.name, **summary))
    files = {str(p.relative_to(target)): dict(bytes=p.stat().st_size, sha256=sha(p))
             for p in sorted(target.rglob("*")) if p.is_file()}
    manifest = dict(utc=summary["utc"], status="complete", files=files,
                    payload_bytes=sum(f["bytes"] for f in files.values()), cohort_sha256=summary["cohort_sha256"],
                    parent_release=dict(name=parent_dir.name, manifest_sha256=summary["parent_manifest_sha256"]))
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "statuses"}))
    return summary


README = """# Contaminant thermodynamic promotion release {name}

{parent_release} unchanged, plus what the coverage campaign (charter amendment A-13, 2026-10-08) completed since: the
simulable PlastChem structures DISSOLVE did not serve, lightest first, with the campaign's frozen recipe, served in the
same schema.

- Computed contaminants: {computed_contaminants} ({added_since_parent} added: {added_halogen_status_rows_completed} halogen
  structures of the A-11 freeze whose partition and miscibility rows are now complete, and {added_coverage} structures of
  the coverage tier). {status_rows_now_failed} status rows of the parent ended in a final failure and say so;
  {coverage_failed_status_rows} coverage structures failed for good and are status rows.
- Recipe, unchanged: RDKit ETKDGv3 (seed 12345, up to 300 conformers), MMFF94, the lowest-energy conformer to ORCA 6.1.1
  BP86/def2-TZVP(-f) optimisation and BP86/def2-TZVPD COSMORS(Water) on AMD EPYC 7763; identity by D-IDENT;
  openCOSMO-RS 24a at 298.15 K and solute x = 0 against the 32 panel solvents and the 10 polymer ensembles, both
  conventions; binary LLE in the RT and high regimes with phase8_lle.py. Thermodynamics ran on Genoa (A-9 note).
- Each structure is in only with all its rows: 640 partition and 64 LLE. Partition rows: {partition_rows}; LLE rows:
  {LLE_rows}.
- parent-aliases.csv: {parent_aliases} names and CAS numbers of PlastChem entries served through their computed neutral
  parent (a salt, an ion or a hydrate; {parent_aliases_offered} offered as aliases, the rest would also name another
  contaminant). The parent's plastchem_id lists those entries.
- publication-labels.csv: the paper's labels, unchanged from the parent.
- Parent: {parent_release}, manifest {parent_manifest_sha256}.
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build"])
    parser.add_argument("number", type=int)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    assert args.number >= 4
    build(args.number, dry_run=args.dry_run)
