"""A-11: contaminants-v2.tsv for build_export_v2.py, from a promotion-v2 release: every computed contaminant with its
surface SHA-256, name, human-readable folder name (over the whole cohort, so names never depend on which subset is
computed) and the paths under ~/plastchem-euler on Euler where its surface may be.

    python3 scripts/export-v2/make_contaminants_tsv.py /mnt/r/plastchem-euler/promotion-v2 OUT.tsv"""
import csv
import gzip
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contaminant_folder_names import folders  # noqa: E402

release, out = Path(sys.argv[1]), Path(sys.argv[2])
with gzip.open(release / "contaminants.csv.gz", "rt") as handle:
    rows = list(csv.DictReader(handle))
# A name that is only a CAS number or a PubChem "CID ..." placeholder is not a chemical name: use the workbook's IUPAC
# name for those (owner, 2026-10-07: "rename all contaminants to actual chemical names").
PLACEHOLDER = re.compile(r"^(\d{2,7}-\d{2}-\d|CID \d+)$")
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
with out.open("w") as handle:
    for r in sorted(computed, key=lambda r: names[r["input_inchikey"]].casefold()):
        key, digest = r["input_inchikey"], r["surface_sha256"]
        candidates = [f"phase83-v1/surfaces/{digest}.orcacosmo"]
        for stage in ("tier2-v1", "tier2-v1/time-limit-retry1", "tier2-v1/time-limit-retry2", "halogen-v1",
                      *[f"halogen-v1/retries/r{n:02d}" for n in range(1, 10)]):
            candidates += [f"{stage}/returns/{key}/surface.orcacosmo", f"{stage}/runs/{key}/cosmo.solute.orcacosmo"]
        handle.write("\t".join([key, digest, r["name"].replace("\t", " "), names[key], ",".join(candidates)]) + "\n")
print(out, len(computed))
