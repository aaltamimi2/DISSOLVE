"""A-11: contaminants-v2.tsv for build_export_v2.py, from a promotion-v2 release: every computed contaminant with its
surface SHA-256, name, human-readable folder name (over the whole cohort, so names never depend on which subset is
computed), the category folder it goes in (classify_contaminants.py: publication set, agent family, PlastChem group,
structure class or no-family) and the paths under ~/plastchem-euler on Euler where its surface may be. Also writes,
beside it, CLASSIFICATION.tsv (every membership of every contaminant) and PUBLICATION_SETS.tsv (each publication-set
item with its surfaces, or why it has none).

    python3 scripts/export-v2/make_contaminants_tsv.py /mnt/r/plastchem-euler/promotion-v2 OUT.tsv"""
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_contaminants import FIELDS, classify, publication_table, release_rows  # noqa: E402
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
names = folders([(r["input_inchikey"], r["name"]) for r in rows])
computed = [r for r in rows if r["campaign_status_at_snapshot"] == "converged" and r["partition_predicted_rows"] not in ("", "0")]
records = classify(computed, rows)
surface = {key: f"contaminants/{rec['path']}/{names[key]}.orcacosmo" for key, rec in records.items()}
with out.open("w") as handle:
    for r in sorted(computed, key=lambda r: names[r["input_inchikey"]].casefold()):
        key, digest = r["input_inchikey"], r["surface_sha256"]
        candidates = [f"phase83-v1/surfaces/{digest}.orcacosmo"]
        for stage in ("tier2-v1", "tier2-v1/time-limit-retry1", "tier2-v1/time-limit-retry2", "halogen-v1",
                      *[f"halogen-v1/retries/r{n:02d}" for n in range(1, 10)]):
            candidates += [f"{stage}/returns/{key}/surface.orcacosmo", f"{stage}/runs/{key}/cosmo.solute.orcacosmo"]
        handle.write("\t".join([key, digest, r["name"].replace("\t", " "), names[key], records[key]["path"],
                                ",".join(candidates)]) + "\n")
columns = ["inchikey", "name", "surface", "folder", *[f for f in FIELDS if f not in ("inchikey", "name", "path")]]


def tsv(path, header, records):
    """Plain TSV (no quoting): tabs and line breaks inside a value become spaces."""
    clean = lambda value: re.sub(r"[\t\r\n]+", " ", str(value))
    path.write_text("".join("\t".join(map(clean, line)) + "\n" for line in [header, *records]))


tsv(out.parent / "CLASSIFICATION.tsv", columns, [
    [{**records[key], "surface": surface[key], "folder": f"contaminants/{records[key]['path']}"}[c] for c in columns]
    for key in sorted(records, key=lambda key: surface[key].casefold())])
table = publication_table(rows, surface)
tsv(out.parent / "PUBLICATION_SETS.tsv", list(table[0]), [list(item.values()) for item in table])
print(out, len(computed), "contaminants;", sum(bool(t["surfaces"]) for t in table), "of", len(table),
      "publication-set items have surfaces")
