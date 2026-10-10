"""A-12 release promotion-v3: promotion-v2 unchanged, plus the publication tier's neutral structures, in the same schema.

The tier computed the compounds of Zhou et al., Green Chem. 2026 that promotion-v2 lacks: 14 neutral PFAS acids
(sulfonic acids hold sulfur and four structures are above 700 g/mol, both outside the release), DECA and TBBPA-dbP.
Each is in only with all its rows: 32 solvents x 10 polymers x 2 conventions of partition, 32 solvents x 2 regimes of
LLE. The 24 PFAS anions the tier also computed are dropped without a trace (owner, 2026-10-08: "just fully drop those
charged PFAS"): the paper's values follow the neutral acids (r 0.95-0.97), not the anions (|r| < 0.1), so no row of
any kind names them.

publication-labels.csv maps the paper's labels to the structures served, so DISSOLVE can resolve "PFOS" or "NaDoDFNt";
a salt the paper lists is its parent acid, as the paper modelled it. Labels that name a class or a mixture (Tri-PBDE,
HBCD) are listed but not offered as aliases.

    python3 scripts/build_promotion_v3.py build      write /mnt/r/plastchem-euler/promotion-v3 (never overwritten)
"""
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
sys.path.insert(0, str(Path(__file__).resolve().parent / "export-v2"))
from build_promotion_v2 import THERMO, B, compact_rows, lle_row, partition_row, sha  # noqa: E402
from classify_contaminants import tier_name  # noqa: E402

R = Path(__file__).resolve().parents[1]
V2 = B / "promotion-v2"
TARGET = B / "promotion-v3"
TIER = R / "state/publication-v1"
BATCHES = ("p00", "p01", "p02")
#: The published table of the paper's compounds (label -> surface SHA-256), in the DISSOLVE repository.
PUBLICATION_SETS = Path.home() / "dissolve-main-cleanup/campaigns/plastchem-hpc/publication-sets/PUBLICATION_SETS.tsv"
NOT_ALIASES = {
    "Tri-PBDE": "names a class (tribromodiphenyl ethers); the paper's compound is BDE-28",
    "HBCD": "names a mixture of stereoisomers; the paper names none and this set holds gamma-HBCD",
}


def tier_molecules():
    """InChIKey -> manifest molecule, for the tier's neutral structures only; the anions are left out here."""
    out, dropped = {}, 0
    for group in ("neutral", "publication"):
        for m in json.loads((TIER / group / "manifest.json").read_text())["molecules"]:
            if m["charge"] != 0:
                dropped += 1
                continue
            record = json.loads((TIER / "records" / f"{m['inchikey']}.json").read_text())
            assert record["status"] == "verified" and record["charge"] == 0, m["inchikey"]
            out[m["inchikey"]] = dict(m, record=record, group_manifest=TIER / group / "manifest.json")
    return out, dropped


def served_name(m):
    """The computed species' name: a salt or an anion the paper lists becomes its parent acid."""
    named = tier_name(m["name"], m["labels"][0], 0)
    plain = re.sub(r" \([^()]*\)$", "", named) if named != m["name"] else named
    return plain, named != m["name"]


def tier_rows(keys):
    """Partition and LLE rows of the given InChIKeys from the tier's batches, in the v1 schema."""
    part, lle = [], []
    for batch in BATCHES:
        spec = json.loads((THERMO / batch / "inputs.json").read_text())
        for _meta, header, plan_sha, r in compact_rows(batch):
            if r["input_inchikey"] not in keys:
                continue
            if header["plan"].startswith("partition-"):
                part.append(partition_row(r, header, plan_sha))
            else:
                unit = spec["later"][r["input_inchikey"]]
                lle.append(lle_row(r, plan_sha, float(unit["molecular_weight_g_mol"]),
                                   float(spec["solvents"][r["product_solvent_key"]]["molecular_weight_g_mol"])))
    return part, lle


def build():
    assert not (TARGET / "manifest.json").exists(), f"{TARGET} is sealed; never overwrite a release"
    if TARGET.exists():
        shutil.rmtree(TARGET)
    with gzip.open(V2 / "contaminants.csv.gz", "rt") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames
        cohort = {r["input_inchikey"]: r for r in reader}
    tier, dropped_anions = tier_molecules()
    served_v2 = {k for k, r in cohort.items() if r["campaign_status_at_snapshot"] == "converged"}
    assert not set(tier) & served_v2, "a tier structure repeats a contaminant promotion-v2 serves"
    # A promotion-v2 status row the tier completes (PFTriDA: halogen ORCA converged, thermodynamics pending at the
    # frozen snapshot) keeps its PlastChem identity and takes the tier's surface and rows.
    completed = sorted(set(tier) & set(cohort))
    part, lle = tier_rows(set(tier))
    computed = collections.Counter(r["input_inchikey"] for r in part)
    lle_counts = collections.defaultdict(collections.Counter)
    for r in lle:
        lle_counts[r["input_inchikey"]]["qualified" if r["value_validated"] else "unresolved"] += 1
    assert set(computed) == set(tier), f"partition rows missing for {sorted(set(tier) - set(computed))}"
    assert all(n == 640 for n in computed.values()), "every structure needs 32 x 10 x 2 partition rows"
    assert all(sum(c.values()) == 64 for c in lle_counts.values()) and set(lle_counts) == set(tier)
    for key, m in tier.items():
        identity = m["record"]["identity"]
        name, renamed = served_name(m)
        row = {f: "" for f in fields}
        row.update(input_inchikey=key, name=name, smiles=m["smiles"], cas="" if renamed else m["cas"],
                   plastchem_id=m.get("plastchem_id") or "", molecular_weight_g_mol=str(m["molecular_weight_g_mol"]),
                   atom_count=str(m["atoms"]), in_phase83_snapshot="False")
        if key in cohort:  # a completed status row: PlastChem's name, CAS, id and SMILES stay
            row.update({f: cohort[key][f] for f in ("name", "smiles", "cas", "plastchem_id", "molecular_weight_g_mol",
                                                    "atom_count", "in_phase83_snapshot") if cohort[key].get(f)})
        row.update(group="publication-A12", tier="publication", exclusion_reason="", failure_mode="", failure_detail="",
                   source_input_sha256=sha(m["group_manifest"]), campaign_status_at_snapshot="converged",
                   perceived_inchikey=identity.get("perceived_inchikey") or "",
                   identity_match_basis=identity.get("identity_match_basis") or "",
                   perception_engines_agreeing_on_perceived_key=json.dumps(
                       identity.get("perception_engines_agreeing_on_perceived_key") or []),
                   surface_sha256=m["record"]["surface_sha256"],
                   source_record_sha256=hashlib.sha256(json.dumps(m["record"], sort_keys=True).encode()).hexdigest(),
                   partition_predicted_rows=str(computed[key]), partition_failed_rows="0",
                   lle_qualified_rows=str(lle_counts[key]["qualified"]),
                   lle_unresolved_or_failed_rows=str(lle_counts[key]["unresolved"]),
                   all_requested_quantities_evaluated="True",
                   all_requested_quantities_qualified=str(lle_counts[key]["unresolved"] == 0))
        cohort[key] = {f: row.get(f, "") for f in fields}
    # the paper's labels -> the structure served, matched by the surface's SHA-256 (the files are byte-identical)
    by_surface = {r["surface_sha256"]: k for k, r in cohort.items() if r.get("surface_sha256")}
    labels = []
    for r in csv.DictReader(PUBLICATION_SETS.open(), delimiter="\t"):
        key = by_surface.get(r["sha256"])
        assert key and key in cohort and (key in computed or cohort[key]["campaign_status_at_snapshot"] == "converged"), r["label"]
        alias = r["label"].split(" ", 1)[1] if re.match(r"P\d+ ", r["label"]) else r["label"]
        labels.append(dict(label=r["label"], alias=alias, set=r["set"], input_inchikey=key,
                           offered="no" if r["label"] in NOT_ALIASES else "yes", note=NOT_ALIASES.get(r["label"], "")))
        renamed = served_name({"name": r["name"], "labels": [r["label"]]})[1]  # the paper lists a salt or an anion
        for cas in re.findall(r"\b\d{2,7}-\d\d-\d\b", r["cas"]):  # the paper lists two CAS numbers for some
            if cas != cohort[key]["cas"]:
                labels.append(dict(label=r["label"], alias=cas, set=r["set"], input_inchikey=key, offered="yes",
                                   note="the CAS of the salt the paper lists; served as its parent acid" if renamed
                                   else "a CAS number the paper gives for this compound"))
    assert len({row["label"] for row in labels}) == 38
    TARGET.mkdir(parents=True)
    work = R / "state/publication-v1/release-work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    con = duckdb.connect(str(work / "build.duckdb"))
    con.execute("SET threads=2")
    con.execute("SET memory_limit='1GB'")
    con.execute("SET temp_directory=?", [str(work / "tmp")])
    for name, rows in (("partition.parquet", part), ("binary-lle.parquet", lle)):
        cols = [(c, t) for c, t, *_ in con.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(V2 / name)]).fetchall()]
        rows_file = work / f"{name}.jsonl"
        with rows_file.open("w") as handle:
            for r in rows:
                handle.write(json.dumps({c: r[c] for c, _ in cols}) + "\n")
        types = ", ".join(f"'{c}': '{t}'" for c, t in cols)
        con.execute("DROP TABLE IF EXISTS fresh")
        con.execute("CREATE TABLE fresh AS SELECT * FROM read_parquet(?) LIMIT 0", [str(V2 / name)])
        con.execute(f"INSERT INTO fresh SELECT * FROM read_json(?, format='newline_delimited', columns={{{types}}})",
                    [str(rows_file)])
        assert con.execute("SELECT count(*) FROM fresh").fetchone()[0] == len(rows)
        local_out = work / name
        assert "'" not in str(V2 / name) + str(local_out)
        con.execute(f"COPY (SELECT * FROM read_parquet('{V2 / name}') UNION ALL SELECT * FROM fresh) "
                    f"TO '{local_out}' (FORMAT parquet, COMPRESSION zstd)")
        shutil.copyfile(local_out, TARGET / name)
    for name, key in zip(("partition.parquet", "binary-lle.parquet"),
                         ("input_inchikey, product_solvent_key, campaign_polymer, convention",
                          "input_inchikey, product_solvent_key, temperature_regime")):
        duplicates = con.execute(f"SELECT count(*) FROM (SELECT {key}, count(*) n FROM read_parquet(?) GROUP BY ALL "
                                 "HAVING n <> 1)", [str(TARGET / name)]).fetchone()[0]
        assert duplicates == 0, f"{name}: duplicate rows"
        charged = con.execute("SELECT count(*) FROM read_parquet(?) WHERE input_inchikey LIKE '%-M' OR input_inchikey LIKE '%-L'",
                              [str(TARGET / name)]).fetchone()[0]
        assert charged == 0, f"{name}: a charged structure's rows"
    con.close()
    shutil.rmtree(work)
    text = io.StringIO()
    writer = csv.DictWriter(text, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for key in sorted(cohort):
        writer.writerow(cohort[key])
    raw = text.getvalue().encode()
    with gzip.GzipFile(TARGET / "contaminants.csv.gz", "wb", mtime=0) as handle:
        handle.write(raw)
    with (TARGET / "publication-labels.csv").open("w", newline="") as handle:
        out = csv.DictWriter(handle, fieldnames=["label", "alias", "set", "input_inchikey", "offered", "note"],
                             lineterminator="\n")
        out.writeheader()
        out.writerows(labels)
    for name in ("polymer-product-map.csv", "product-mapping-evidence.json"):
        shutil.copyfile(V2 / name, TARGET / name)
    provenance = TARGET / "provenance-a12"
    provenance.mkdir()
    for batch in BATCHES:
        for name in ("batch.json", "inputs.json", "collection.json"):
            shutil.copyfile(THERMO / batch / name, provenance / f"{batch}-{name}")
    for group in ("neutral", "publication"):
        shutil.copyfile(TIER / group / "manifest.json", provenance / f"{group}-manifest.json")
    shutil.copyfile(PUBLICATION_SETS, provenance / "PUBLICATION_SETS.tsv")
    statuses = collections.Counter((r["tier"], r["campaign_status_at_snapshot"]) for r in cohort.values())
    v2_summary = json.loads((V2 / "summary.json").read_text())
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status="complete",
                   parent_release="promotion-v2", parent_manifest_sha256=sha(V2 / "manifest.json"),
                   computed_contaminants=v2_summary["computed_contaminants"] + len(computed),
                   added_since_v2=len(computed), status_rows_completed=completed,
                   charged_structures_dropped=dropped_anions,
                   identity_status_rows=len(cohort), statuses={f"{t}/{s}": n for (t, s), n in sorted(statuses.items())},
                   partition_rows=v2_summary["partition_rows"] + len(part), LLE_rows=v2_summary["LLE_rows"] + len(lle),
                   batches=list(BATCHES), publication_labels=len(labels),
                   cohort_sha256=hashlib.sha256(raw).hexdigest())
    (TARGET / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (TARGET / "README.md").write_text(README.format(**summary))
    files = {str(p.relative_to(TARGET)): dict(bytes=p.stat().st_size, sha256=sha(p))
             for p in sorted(TARGET.rglob("*")) if p.is_file()}
    manifest = dict(utc=summary["utc"], status="complete", files=files,
                    payload_bytes=sum(f["bytes"] for f in files.values()), cohort_sha256=summary["cohort_sha256"],
                    parent_release=dict(name="promotion-v2", manifest_sha256=summary["parent_manifest_sha256"]))
    (TARGET / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "statuses"}))


README = """# Contaminant thermodynamic promotion release v3

promotion-v2 unchanged, plus the publication tier of amendment A-12: the compounds of Zhou et al., Green Chem. 2026
(d5gc06059a) that promotion-v2 lacks, computed with the same recipe and served in the same schema.

- Computed contaminants: {computed_contaminants} ({added_since_v2} added since promotion-v2: 14 neutral PFAS acids, DECA
  and TBBPA-dbP). One of them, PFTriDA, was a promotion-v2 status row (ORCA converged in the halogen tier,
  thermodynamics pending at its frozen snapshot); it keeps its PlastChem identity and takes the tier's surface and
  rows: {status_rows_completed}.
- The {charged_structures_dropped} PFAS anions the tier also computed are not in this release in any form: the paper's
  values follow the neutral acids (r 0.95-0.97 on its 32-solvent PFAS sheet), not the anions (|r| below 0.1).
- Partition rows: {partition_rows}; LLE rows: {LLE_rows}.
- publication-labels.csv: each of the paper's {publication_labels} labels and CAS numbers with the structure served;
  a salt is its parent acid, as the paper modelled it. Tri-PBDE and HBCD name a class and a mixture, so they are
  listed but not offered as aliases.
- Parent: promotion-v2, manifest {parent_manifest_sha256}.
"""


if __name__ == "__main__":
    if sys.argv[1:] != ["build"]:
        raise SystemExit(__doc__)
    build()
