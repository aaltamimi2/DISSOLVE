"""A-11/A-12: contaminants-v2.tsv for build_export_v2.py: every computed contaminant of a promotion-v2 release, and
every surface the publication tier computed for the paper's compounds (A-12), with its surface SHA-256, name,
human-readable folder name (over the whole cohort, so names never depend on which subset is computed), the category
folder it goes in (classify_contaminants.py) and the paths under ~/plastchem-euler on Euler where its surface may be.
Also writes, beside it, CLASSIFICATION.tsv (every membership of every file) and PUBLICATION_SETS.tsv (each
publication-set compound with its file, species and where it was computed, or why it has none).

    python3 scripts/export-v2/make_contaminants_tsv.py /mnt/r/plastchem-euler/promotion-v2 OUT.tsv"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_contaminants import (FIELDS, PUBLICATION_SETS, classify, describe_structure,  # noqa: E402
                                   publication_aliases, publication_table, publication_tier, release_rows, tier_name,
                                   tier_rows)
import json  # noqa: E402
from contaminant_folder_names import folders  # noqa: E402

release, out = Path(sys.argv[1]), Path(sys.argv[2])
rows, _ = release_rows(release)
# A name that is only a registry number (CAS, EINECS) or a PubChem "CID ..." placeholder is not a chemical name: use
# the workbook's IUPAC name for those (owner, 2026-10-07: "rename all contaminants to actual chemical names").
PLACEHOLDER = re.compile(r"^(\d{2,7}-\d{2}-\d|CID \d+|EINECS \d{3}-\d{3}-\d)$", re.IGNORECASE)
workbook = {r["plastchem_ID"]: r for r in csv.DictReader(
    (Path(__file__).resolve().parents[2] / "inputs/census/plastchem_db_v1.0_full_database_subset.csv").open())}
for r in rows:
    if PLACEHOLDER.match(r["name"].strip()):
        ids = [i.split(".")[0] for i in r["plastchem_id"].replace(",", ";").split(";") if i.strip()]
        iupac = next((workbook[i]["iupac_name"] for i in ids if i in workbook and workbook[i]["iupac_name"] not in ("", "nan")), None)
        if iupac:
            r["name"] = iupac
def release_candidates(key, digest):
    """Where a release surface may be under ~/plastchem-euler on Euler."""
    out = [f"phase83-v1/surfaces/{digest}.orcacosmo"]
    for stage in ("tier2-v1", "tier2-v1/time-limit-retry1", "tier2-v1/time-limit-retry2", "halogen-v1",
                  *[f"halogen-v1/retries/r{n:02d}" for n in range(1, 10)]):
        out += [f"{stage}/returns/{key}/surface.orcacosmo", f"{stage}/runs/{key}/cosmo.solute.orcacosmo"]
    return out


tier = {f"{t['inchikey']}#{t['label']}": t for t in tier_rows()}  # the two ADONA salts share one acid
# a paper label whose surface is a release row filed under another label (NH4TetraFPt is TFPA's acid)
for set_name, label, name, cas, r in publication_aliases(
        rows, json.loads(PUBLICATION_SETS.read_text())["sets"], skip=set(publication_tier())):
    tier[f"{r['input_inchikey']}#{label}"] = {
        "label": label, "set": set_name, "name": tier_name(name, label, 0), "inchikey": r["input_inchikey"], "cas": cas,
        "sha256": r["surface_sha256"], "smiles": r["smiles"], "charge": 0, "species": "neutral",
        "candidates": release_candidates(r["input_inchikey"], r["surface_sha256"])}
names = folders([(r["input_inchikey"], r["name"]) for r in rows] + [(key, t["name"]) for key, t in tier.items()])
computed = [r for r in rows if r["campaign_status_at_snapshot"] == "converged" and r["partition_predicted_rows"] not in ("", "0")]
records = classify(computed, rows)
surface = {key: f"contaminants/{rec['path']}/{names[key]}.orcacosmo" for key, rec in records.items()}
tier_surface = {t["label"]: f"contaminants/publication-sets/{t['set']}/{names[key]}.orcacosmo" for key, t in tier.items()}
with out.open("w") as handle:
    for r in sorted(computed, key=lambda r: names[r["input_inchikey"]].casefold()):
        key, digest = r["input_inchikey"], r["surface_sha256"]
        handle.write("\t".join([key, digest, r["name"].replace("\t", " "), names[key], records[key]["path"],
                                ",".join(release_candidates(key, digest))]) + "\n")
    for key, t in sorted(tier.items(), key=lambda item: names[item[0]].casefold()):
        handle.write("\t".join([t["inchikey"], t["sha256"], t["name"], names[key], f"publication-sets/{t['set']}",
                                ",".join(t["candidates"])]) + "\n")
columns = ["inchikey", "name", "surface", "folder", *[f for f in FIELDS if f not in ("inchikey", "name", "path")]]


def tsv(path, header, records):
    """Plain TSV (no quoting): tabs and line breaks inside a value become spaces."""
    clean = lambda value: re.sub(r"[\t\r\n]+", " ", str(value))
    path.write_text("".join("\t".join(map(clean, line)) + "\n" for line in [header, *records]))


lines = [[{**records[key], "surface": surface[key], "folder": f"contaminants/{records[key]['path']}"}[c] for c in columns]
         for key in records]
for key, t in tier.items():
    halogens, groups = describe_structure(t["smiles"])
    row = {"inchikey": t["inchikey"], "name": t["name"], "surface": tier_surface[t["label"]],
           "folder": f"contaminants/publication-sets/{t['set']}",
           "assigned_by": ("publication set (Zhou et al. 2026), computed in amendment A-12"
                           if t["candidates"][0].startswith("publication-v1") else
                           "publication set (Zhou et al. 2026), the release surface of another of its labels"),
           "publication_set": f"{t['set']}: {t['label']} (CAS {t['cas']})", "agent_families": "",
           "plastchem_groups": "", "plastchem_functions": "", "halogens": halogens, "functional_groups": groups,
           "plastchem_ids": "", "cas": t["cas"]}
    lines.append([row[c] for c in columns])
lines.sort(key=lambda line: line[2].casefold())
tsv(out.parent / "CLASSIFICATION.tsv", columns, lines)
table = publication_table(rows, surface, tier_surface)
tsv(out.parent / "PUBLICATION_SETS.tsv", list(table[0]), [list(item.values()) for item in table])
print(out, len(computed), "release contaminants +", len(tier), "publication-tier files;",
      sum(bool(t["surfaces"]) for t in table), "of", len(table), "publication-set compounds have surfaces")
