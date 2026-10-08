"""A-12: publish the paper's publication sets as plain files on the DISSOLVE branch, as the runs come in (owner,
2026-10-07: "push them as they come for these 26 orca/opencosmo"). Each call collects and verifies the publication
tier, copies every newly verified surface and its ORCA files into campaigns/plastchem-hpc/publication-sets/, rewrites
the folder's README and status table, and commits and pushes the branch when anything changed.

The compounds the release already holds (8 phthalates, BDE-28, gamma-HBCD, and the neutral acids of 10 of the PFAS)
come from the verified v2 archives (SHA-256 checked against their MANIFEST); the publication tier's from Euler.

    ~/.venvs/cosmo-logp/bin/python scripts/publish_publication_sets.py [--no-push]"""
import csv
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "export-v2"))
import collect_publication  # noqa: E402
from classify_contaminants import (PUBLICATION_SETS, publication_matches, publication_tier, release_rows,  # noqa: E402
                                   tier_name)
from contaminant_folder_names import folder  # noqa: E402
from euler_transport import run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state/publication-v1"
BRANCH = Path.home() / "dissolve-contaminant-always-on"
DEST = BRANCH / "campaigns/plastchem-hpc/publication-sets"
ARCHIVES = Path("/tmp/claude-1000/-home-aaltamimi2-dissolve-v12-work/a0a3a84b-39cd-4524-9b45-595823a43aef/scratchpad/"
                "export-v3/download")  # the verified 6a455e20 archives
RELEASE_SIDE = STATE / "release-side"
RELEASE_DIR = Path("/mnt/r/plastchem-euler/promotion-v2")
THERMO = Path("/mnt/r/plastchem-euler/newcontam-thermo-v1")
BATCHES = ("p00", "p01", "p02")  # A-12 thermodynamics: DECA in p00, TBBPA-dbP in p01, the neutral PFAS acids in p02
NAMES = {"optimized.xyz", "result.json", "cosmo.json", "cosmo_out.json", "cosmo.out"}
MARKERS = (b"password", b"passwd", b"secret", b"api_key", b"apikey", b"token", b"begin rsa", b"begin openssh",
           b"@wisc.edu", b"@gmail")
RELEASE = {  # label -> (folder in the 6a455e20 archives' publication-sets/, file name there)
    "P1 BBP": ("Phthalates", "Benzyl_butyl_phthalate"), "P2 DBP": ("Phthalates", "Dibutyl_Phthalate"),
    "P3 DEHP": ("Phthalates", "Bis(2-ethylhexyl)_phthalate"), "P4 DEP": ("Phthalates", "Diethyl_Phthalate"),
    "P5 DiDP": ("Phthalates", "Diisodecyl_phthalate"), "P6 DiNP": ("Phthalates", "Diisononyl_phthalate"),
    "P7 DnHP": ("Phthalates", "Dihexyl_phthalate"), "P8 DnOP": ("Phthalates", "Dioctyl_phthalate"),
    "Tri-PBDE": ("BFR", "2,4-Dibromo-1-(4-bromophenoxy)benzene"), "HBCD": ("BFR", "gamma-Hexabromocyclododecane"),
    "PFBA": ("PFAS", "Heptafluorobutyric_acid"), "PFPA": ("PFAS", "Perfluorovaleric_acid"),
    "PFHxA": ("PFAS", "Perfluorohexanoic_acid"), "PFHA": ("PFAS", "Perfluoroheptanoic_acid"),
    "PFOA": ("PFAS", "Perfluorooctanoic_acid"), "PFNA": ("PFAS", "Perfluorononanoic_acid"),
    "PFDA": ("PFAS", "Perfluorodecanoic_acid"),
    "TFPA": ("PFAS", "2,3,3,3-Tetrafluoro-2-(heptafluoropropoxy)propanoic_acid"),
    "PFUnDA": ("PFAS", "Perfluoroundecanoic_acid"), "PFDoDA": ("PFAS", "Perfluorododecanoic_acid"),
}
ALIASES = {"NH4TetraFPt": "TFPA"}  # the paper's NH4TetraFPt is its TFPA: the same surface under its own label


def sha(data):
    return hashlib.sha256(data).hexdigest()


def release_side():
    """Extract the release's publication-set surfaces and ORCA folders from the verified v2 archives once."""
    if (RELEASE_SIDE / "done").exists():
        return
    manifest = {row["path"]: row["sha256"] for row in csv.DictReader(
        (ARCHIVES / "surfaces/MANIFEST.tsv").open(), delimiter="\t", quoting=csv.QUOTE_NONE)}
    wanted = {f"contaminants/publication-sets/{s}/{n}" for s, n in RELEASE.values()}
    for archive, root in ((ARCHIVES / "surfaces/opencosmo-outputs.tar.xz", "opencosmo-outputs/"),
                          (ARCHIVES / "orca/orca-calculation-files.tar.xz", "orca-calculation-files/")):
        xz = subprocess.Popen(["xz", "-T2", "-dc", str(archive)], stdout=subprocess.PIPE)
        with tarfile.open(fileobj=xz.stdout, mode="r|") as tar:
            for member in tar:
                path = member.name[len(root):]
                stem = path.removesuffix(".orcacosmo")
                hit = stem if stem in wanted else next((w for w in wanted if path.startswith(w + "/")), None)
                if not hit or not member.isfile():
                    continue
                data = tar.extractfile(member).read()
                if path.endswith(".orcacosmo"):
                    assert sha(data) == manifest[path], path
                out = RELEASE_SIDE / root.rstrip("/") / path
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
        xz.wait()
    (RELEASE_SIDE / "done").write_text("ok\n")


def fetch_runs(keys):
    """The ORCA files of verified publication-tier runs (inputs, optimised geometry, records, COSMO-step log)."""
    keys = [k for k in keys if not (STATE / "runs" / k / "result.json").exists()]
    if not keys:
        return
    command = ("cd ~/plastchem-euler/publication-v1/runs && tar -cf - --ignore-failed-read "
               + " ".join(f"{k}/*.inp {k}/optimized.xyz {k}/result.json {k}/cosmo.out" for k in keys) + " 2>/dev/null")
    result = run("ssh", ["euler", command], capture_output=True)
    with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as tar:
        for member in tar:
            if member.isfile():
                out = STATE / "runs" / member.name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(tar.extractfile(member).read())


def put(path, data):
    """Write a file under DEST unless it already holds these bytes; refuse anything carrying a secret marker."""
    low = data.lower()
    assert not any(m in low for m in MARKERS), path
    if path.exists() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def thermo_step(all_final):
    """Collect the publication tier's thermodynamics batches once they are sealed on Euler; once every ORCA run is
    final, stage and submit p01 for the verified structures p00 lacks."""
    import newcontam_thermo as nt
    for batch in BATCHES:
        if (THERMO / batch / "submission.json").exists() and not (THERMO / batch / "collection.json").exists():
            nt.collect(batch)
    if all_final and not (THERMO / "p02" / "batch.json").exists() and nt.publication_units():
        nt.stage("p02", "publication", None)
        nt.submit("p02")


def thermo_tables(entries):
    """Partition (32 solvents x 10 polymers, both conventions) and binary miscibility rows for every file here, from
    the release (promotion-v2) and the collected A-12 batches. entries: dicts with set, label, compound, file,
    species, inchikey. Returns (partition lines, miscibility lines, keys with no partition rows yet)."""
    import duckdb
    keys = sorted({e["inchikey"] for e in entries})
    con = duckdb.connect()
    part, lle = {}, {}
    for key, *row in con.execute(
            "SELECT input_inchikey, campaign_polymer, product_solvent_key, convention, logP_concentration, logP_x, "
            "status FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?))",
            [str(RELEASE_DIR / "partition.parquet"), keys]).fetchall():
        part.setdefault(key, []).append((*row, "release promotion-v2"))
    for key, *row in con.execute(
            "SELECT input_inchikey, product_solvent_key, temperature_regime, temperature_K, status, above_15_wt_percent, "
            "solute_wt_percent_solubility FROM read_parquet(?) WHERE input_inchikey IN (SELECT unnest(?))",
            [str(RELEASE_DIR / "binary-lle.parquet"), keys]).fetchall():
        lle.setdefault(key, []).append((*row, "release promotion-v2"))
    for batch in BATCHES:
        if not (THERMO / batch / "collection.json").exists():
            continue
        for f in sorted((THERMO / batch / "results-compact").glob("*.jsonl.gz")):
            with gzip.open(f, "rt") as handle:
                next(handle)  # the plan's header
                for line in handle:
                    r = json.loads(line)
                    if r["input_inchikey"] not in keys:
                        continue
                    if f.name.startswith("partition"):
                        part.setdefault(r["input_inchikey"], []).append(
                            (r["polymer"], r["product_solvent_key"], r["convention"], r["logP_concentration"],
                             r["logP_x"], r["status"], f"A-12 batch {batch}"))
                    else:
                        lle.setdefault(r["input_inchikey"], []).append(
                            (r["product_solvent_key"], r["regime"], r["temperature_K"], r["status"],
                             r.get("above_15_wt_percent"), r.get("solute_wt_percent_solubility"), f"A-12 batch {batch}"))
    head = ["set", "label", "compound", "file", "species", "inchikey"]
    part_lines, lle_lines, missing = [], [], []
    for e in entries:
        rows = sorted(part.get(e["inchikey"], []))
        if not rows:
            missing.append(e["label"])
        part_lines += [[e[h] for h in head] + list(r) for r in rows]
        if e["species"] == "neutral":
            lle_lines += [[e[h] for h in head] + list(r) for r in sorted(lle.get(e["inchikey"], []), key=str)]
    return part_lines, lle_lines, missing


def main(push=True):
    collect_publication.main()
    release_side()
    groups = [json.loads((STATE / g / "manifest.json").read_text())["molecules"] for g in ("publication", "neutral")]
    published = {m["inchikey"] for m in groups[1]} | {m["inchikey"] for m in groups[0] if m["charge"] == 0}
    records = {p.stem: json.loads(p.read_text()) for p in (STATE / "records").glob("*.json")}
    all_final = all(records.get(k, {}).get("status") in ("verified", "failed", "identity_rejected") for k in published)
    thermo_step(all_final)
    sets = json.loads(PUBLICATION_SETS.read_text())["sets"]
    tier = publication_tier()  # the neutral PFAS acids the release lacks, DECA and TBBPA-dbP
    release_all, _ = release_rows(RELEASE_DIR)
    _, matched = publication_matches(release_all, sets, skip=set(tier))
    fetch_runs([record["inchikey"] for _, record in tier.values() if record])
    rows, expected, changed, new = [], set(), False, []

    def place(rel, data):
        expected.add(rel)
        return put(DEST / rel, data)

    for set_name in ("PFAS", "BFR", "Phthalates"):
        for label, name, cas, *_ in sets[set_name]:
            row = {"set": set_name, "label": label, "name": name, "cas": cas.replace(";", ", "), "species": "neutral"}
            if label in tier:
                m, record = tier[label]
                stem = folder(tier_name(name, label, 0))
                row.update(computed_in="publication tier (A-12)", inchikey=m["inchikey"])
                if record:
                    surface = (STATE / "incoming/returns" / m["inchikey"] / "surface.orcacosmo").read_bytes()
                    assert sha(surface) == record["surface_sha256"]
                    if place(f"{set_name}/{stem}.orcacosmo", surface):
                        changed = True
                        new.append(label)
                    for f in sorted((STATE / "runs" / m["inchikey"]).iterdir()):
                        if f.suffix == ".inp" or f.name in NAMES:
                            changed |= place(f"{set_name}/orca-calculation-files/{stem}/{f.name}", f.read_bytes())
                    changed |= place(f"{set_name}/orca-calculation-files/{stem}/identity.json",
                                     (json.dumps(record["identity"], indent=1) + "\n").encode())
                    row.update(file=f"{set_name}/{stem}.orcacosmo", sha256=record["surface_sha256"], status="in")
                else:
                    row.update(file="", sha256="", status="running on Euler")
            else:
                source = ALIASES.get(label, label)
                set_dir, stem = RELEASE[source]
                base = RELEASE_SIDE / "opencosmo-outputs/contaminants/publication-sets" / set_dir
                surface = (base / f"{stem}.orcacosmo").read_bytes()
                hits = [r["input_inchikey"] for r in matched[(set_name, label)] if r["surface_sha256"] == sha(surface)]
                assert len(hits) == 1, (label, hits)
                out_stem = folder(tier_name(name, label, 0)) if label in ALIASES else stem
                changed |= place(f"{set_name}/{out_stem}.orcacosmo", surface)
                orca = RELEASE_SIDE / "orca-calculation-files/contaminants/publication-sets" / set_dir / stem
                for f in sorted(orca.iterdir()):
                    changed |= place(f"{set_name}/orca-calculation-files/{out_stem}/{f.name}", f.read_bytes())
                row.update(computed_in="release (promotion-v2)" + (f", the surface of {source}" if label in ALIASES
                                                                    else ""),
                           inchikey=hits[0], file=f"{set_name}/{out_stem}.orcacosmo", sha256=sha(surface), status="in")
            rows.append(row)
    entries = [dict(set=r["set"], label=r["label"], compound=r["name"], file=r["file"], species="neutral",
                    inchikey=r["inchikey"]) for r in rows if r["status"] == "in"]
    part_lines, lle_lines, missing = thermo_tables(entries)
    head = ["set", "label", "compound", "file", "species", "inchikey"]
    tsv = lambda header, lines: ("\t".join(header) + "\n" + "".join(
        "\t".join("" if v is None else str(v) for v in line) + "\n" for line in lines)).encode()
    changed |= place("THERMODYNAMICS_partition.tsv", tsv(
        head + ["polymer", "solvent", "convention", "logP_concentration", "logP_x", "status", "computed_in"], part_lines))
    changed |= place("THERMODYNAMICS_miscibility.tsv", tsv(
        head + ["solvent", "regime", "temperature_K", "status", "miscible_at_15_wt_percent", "solubility_wt_percent",
                "computed_in"], lle_lines))
    fields = ["set", "label", "name", "cas", "species", "computed_in", "status", "file", "sha256"]
    table = "".join("\t".join(str(r.get(f, "")) for f in fields) + "\n" for r in rows)
    changed |= place("PUBLICATION_SETS.tsv", ("\t".join(fields) + "\n" + table).encode())
    changed |= place("README.md", readme(rows, len(entries) - len(missing), len(entries), missing).encode())
    for path in sorted(DEST.rglob("*"), reverse=True):  # nothing here that is not one of the paper's compounds
        if path.is_file() and str(path.relative_to(DEST)) not in expected:
            path.unlink()
            changed = True
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    done = sum(r["status"] == "in" for r in rows)
    print(f"{done} of {len(rows)} compounds in; new this call: {new or 'none'}; partition rows for "
          f"{len(entries) - len(missing)} of {len(entries)} files (missing: {missing or 'none'})")
    complete = all_final and done == len(rows) and not missing and all(
        (THERMO / b / "collection.json").exists() for b in BATCHES if (THERMO / b / "batch.json").exists())
    (STATE / "publish-complete.json").write_text(json.dumps({"complete": complete}) + "\n")
    if not changed or not push:
        return changed
    commit(rows, new, len(entries) - len(missing), len(entries))
    return changed


def commit(rows, new, thermo_done, thermo_total):
    git = ["git", "-C", str(BRANCH)]
    subprocess.run(git + ["add", "-A", str(DEST)], check=True)
    staged = subprocess.run(git + ["diff", "--cached", "--name-only"], capture_output=True, text=True, check=True).stdout
    assert all(line.startswith("campaigns/plastchem-hpc/publication-sets/") for line in staged.split()), staged
    done = sum(r["status"] == "in" for r in rows)
    message = (f"The paper's publication sets hold {done} of their 38 compounds as plain files, all neutral as the "
               f"paper modelled them\n\nAmendment A-12: " + (f"new: {', '.join(new)}. " if new else "")
               + "Each surface's SHA-256 is the one the collector verified; identity by D-IDENT "
               "(identity.json beside each run's ORCA files). Compounds still running are listed in "
               f"PUBLICATION_SETS.tsv. Partition rows (openCOSMO-RS 24a, 32 solvents x 10 polymers) are in for "
               f"{thermo_done} of {thermo_total} files.\n")
    subprocess.run(git + ["commit", "-q", "-F", "-"], input=message, text=True, check=True)
    subprocess.run(git + ["push", "-q", "dissolve", "contaminant-always-on"], check=True)
    print(subprocess.run(git + ["log", "-1", "--format=%h %s"], capture_output=True, text=True).stdout.strip())


def readme(rows, thermo_done=0, thermo_total=0, missing=()):
    counts = {s: (sum(r["set"] == s and r["status"] == "in" for r in rows), sum(r["set"] == s for r in rows))
              for s in ("PFAS", "BFR", "Phthalates")}
    lines = [f"| {r['label']} | {r['name']} | {r['cas']} | {r['computed_in']} | "
             f"{'`' + r['file'] + '`' if r['file'] else r['status']} |" for r in rows]
    waiting = f" (waiting: {', '.join(missing)})" if missing else ""
    return f"""# Publication sets of Zhou et al., Green Chem. 2026

The ORCA/openCOSMO surfaces (`.orcacosmo`) of every compound of Zhou et al., *Solvent-Mediated Contaminant Removal
from Plastic Waste Using Thermodynamic Modeling*, Green Chem. 2026 (d5gc06059a), one per compound, as plain files for
validation against the paper, with their openCOSMO-RS partition and miscibility results. The same surfaces, byte for
byte, are also in the campaign archives (`../opencosmo-outputs/` and `../orca-calculation-files/`, folder
`contaminants/publication-sets/`); the partition and miscibility rows are only here.

- `PFAS/`: {counts['PFAS'][0]} of the paper's {counts['PFAS'][1]} PFAS (ESI Table S7).
- `BFR/`: {counts['BFR'][0]} of its {counts['BFR'][1]} brominated flame retardants (Case Study 1).
- `Phthalates/`: {counts['Phthalates'][0]} of its {counts['Phthalates'][1]} phthalates (ESI Table S5).
- `<set>/orca-calculation-files/<name>/`: each surface's ORCA inputs, optimised geometry, run record and COSMO-step
  log; for the runs of amendment A-12 also `identity.json`, the identity check.
- `PUBLICATION_SETS.tsv`: every compound with where it was computed, its file and its SHA-256.
- `THERMODYNAMICS_partition.tsv` and `THERMODYNAMICS_miscibility.tsv`: see below.

## Species

Every compound is neutral, as the paper modelled them. For the PFAS this was checked against the paper's own values
(d5gc06059a2_suppl.xlsx, sheet PFAS_log_D, 32 solvents): for the ten PFAS acids in both forms, the paper's values
follow the neutral acids (r = 0.95-0.97, RMSE 0.6-0.9 log units) and not the anions (|r| below 0.1). Salts are their
parent acids: the paper's values for NH4TetraFPt equal TFPA's, so `PFAS/` holds TFPA's surface under both labels,
and its two ADONA salts (NaDoDFNt, NH4PFNt) are one acid. A file named for a salt or an anion is named as the acid
computed, with the paper's label, e.g. `Perfluorononanesulfonic_acid_(PFNS).orcacosmo`.

Tri-PBDE is the ESI's 2,4,4'-tribromodiphenyl ether (BDE-28). The paper reports one HBCD value without naming the
stereoisomer; this set holds gamma-HBCD, the main component of technical HBCD.

## Thermodynamics

openCOSMO-RS 24a at 298.15 K, solute at infinite dilution, with the same 32 solvents, 10 polymer conformer ensembles
and workers as the PlastChem release: release compounds carry the release's own rows, and the A-12 structures were
computed the same way on Euler (batches p00, p01, p02). Partition rows are in for {thermo_done} of the {thermo_total}
files here{waiting}.

- `THERMODYNAMICS_partition.tsv`: log10 P(solvent/polymer) per file, polymer (EVOH, nylon 6, nylon 6,6, PC, PE,
  PET, PP, PS, PVC, PVDF), solvent and convention; positive favours the solvent. `logP_concentration` is on the
  mol/L basis, `logP_x` on the mole-fraction basis. `normalized` is the convention DISSOLVE serves; `existing`
  weights the polymer's conformers as the earlier route did. The paper used PVC for its phthalates (ESI Table S6)
  and PS, PET, PE and PP for its BFRs (Tables S1-S4).
- `THERMODYNAMICS_miscibility.tsv`: binary liquid-liquid equilibrium of each compound with each solvent at room
  temperature and at the solvent's high temperature: phase status, solubility in wt%, and whether it is miscible at
  15 wt%.

## How they were made

ORCA 6.1.1: RDKit ETKDGv3 (seed 12345, 300 conformers) and MMFF94 pick one conformer; a gas-phase `OPT BP86
def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`, whose solute step writes the surface (BP86/def2-TZVPD).
The phthalates, BDE-28, gamma-HBCD and ten of the PFAS acids are the PlastChem campaign's surfaces (release
promotion-v2). The other PFAS acids, DECA and TBBPA-dbP were computed for this set under amendment A-12 with the same
recipe (structures from PubChem by CAS, checked against PlastChem; PFTriDS, PFTetraDA, DECA and TBBPA-dbP are above
the campaign's 700 g/mol limit, and the sulfonic acids contain sulfur, which the campaign otherwise excludes).
Identity: the optimised geometry's connectivity must match the input.

## Compounds

| Label | Compound (as in the paper) | CAS | Computed in | File |
|---|---|---|---|---|
{chr(10).join(lines)}
"""


if __name__ == "__main__":
    main(push="--no-push" not in sys.argv)
