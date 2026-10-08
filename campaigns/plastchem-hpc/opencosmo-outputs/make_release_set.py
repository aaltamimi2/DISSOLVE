"""A-13 item 7d: the archive set of one release. Every contaminant a release computes that its parent did not gets its
surface and its ORCA run folder in a new set of archive parts named for the release (opencosmo-outputs-<release>.tar.xz
and orca-calculation-files-<release>.tar.xz, 95 MB parts); the existing parts are never rewritten. Same structure as the
v2 archives (A-11 item 7): a folder per contaminant named by contaminant_folder_names.py and filed by
classify_contaminants.py (publication set > agent family > PlastChem group > structure class > no-family).

Names never move an archived file: a new name that collides (ignoring case) with a name already in the archive, or with
another new one, carries "_[first InChIKey block]" (the whole key if that still collides); archived names stay. A name
that is only a registry number takes the workbook's IUPAC name, as make_contaminants_tsv.py does. Contaminants already in
the archive keep their folder even when a later family would file them elsewhere (the existing parts are never rewritten).

Writes, in OUT_DIR: set.tsv (inchikey, sha256, name, folder name, category folder, candidate paths under ~/plastchem-euler
on Euler, for build_release_set.py there) and CLASSIFICATION.rows.tsv (rows to append to CLASSIFICATION.tsv).

    DISSOLVE_SRC=~/dissolve-main-cleanup/src ~/.venvs/cosmo-logp/bin/python scripts/export-v2/make_release_set.py \\
        /mnt/r/plastchem-euler/promotion-v4 ~/dissolve-main-cleanup/campaigns/plastchem-hpc OUT_DIR"""
import csv
import gzip
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_contaminants import FIELDS, classify, release_rows  # noqa: E402
from contaminant_folder_names import folder  # noqa: E402

PLACEHOLDER = re.compile(r"^(\d{2,7}-\d{2}-\d|CID \d+|EINECS \d{3}-\d{3}-\d)$", re.IGNORECASE)


def candidates(key, digest):
    """Where a surface may be under ~/plastchem-euler on Euler: the coverage tier, its retry rounds, the halogen tier and
    its retries (A-11), then the earlier tiers (as make_contaminants_tsv.py lists them)."""
    stages = ["coverage-v1", *[f"coverage-v1/retries/r{n:02d}" for n in range(1, 10)], "halogen-v1",
              *[f"halogen-v1/retries/r{n:02d}" for n in range(1, 10)], "tier2-v1", "tier2-v1/time-limit-retry1",
              "tier2-v1/time-limit-retry2"]
    out = [f"phase83-v1/surfaces/{digest}.orcacosmo"]
    for stage in stages:
        out += [f"{stage}/returns/{key}/surface.orcacosmo", f"{stage}/runs/{key}/cosmo.solute.orcacosmo"]
    return out


def main(release, campaign, out_dir):
    release, campaign, out_dir = Path(release), Path(campaign), Path(out_dir)
    parent = json.loads((release / "summary.json").read_text())["parent_release"]
    rows, computed = release_rows(release)
    _, parent_computed = release_rows(release.parent / parent)
    archived = list(csv.DictReader((campaign / "opencosmo-outputs/MANIFEST.tsv").open(), delimiter="\t", quoting=csv.QUOTE_NONE))
    in_archive = {r["identity"] for r in archived if r["category"] == "contaminant"}
    new = [r for r in computed if r["input_inchikey"] not in {p["input_inchikey"] for p in parent_computed}
           and r["input_inchikey"] not in in_archive]
    taken = {Path(r["path"]).name.removesuffix(".orcacosmo").casefold() for r in archived if r["category"] == "contaminant"}
    workbook = {r["plastchem_ID"]: r for r in csv.DictReader(
        (Path(__file__).resolve().parents[2] / "inputs/census/plastchem_db_v1.0_full_database_subset.csv").open())}
    names, suffixed = {}, 0
    for r in sorted(new, key=lambda r: r["input_inchikey"]):
        name = r["name"]
        if PLACEHOLDER.match(name.strip()):
            ids = [i.split(".")[0] for i in r["plastchem_id"].replace(",", ";").split(";") if i.strip()]
            name = next((workbook[i]["iupac_name"] for i in ids if i in workbook and workbook[i]["iupac_name"] not in ("", "nan")), name)
        base = folder(name)
        value = base
        if value.casefold() in taken:
            value = f"{base}_[{r['input_inchikey'].split('-')[0]}]"
        if value.casefold() in taken:
            value = f"{base}_[{r['input_inchikey']}]"
        assert value.casefold() not in taken, r["input_inchikey"]
        suffixed += value != base
        taken.add(value.casefold())
        names[r["input_inchikey"]] = (name, value)
    records = classify(new, rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "set.tsv").open("w") as handle:
        for r in sorted(new, key=lambda r: names[r["input_inchikey"]][1].casefold()):
            key = r["input_inchikey"]
            handle.write("\t".join([key, r["surface_sha256"], names[key][0].replace("\t", " "), names[key][1],
                                    records[key]["path"], ",".join(candidates(key, r["surface_sha256"]))]) + "\n")
    columns = ["inchikey", "name", "surface", "folder", *[f for f in FIELDS if f not in ("inchikey", "name", "path")]]
    clean = lambda value: re.sub(r"[\t\r\n]+", " ", str(value))  # noqa: E731
    with (out_dir / "CLASSIFICATION.rows.tsv").open("w") as handle:
        for key in sorted(records, key=lambda k: names[k][1].casefold()):
            rec = dict(records[key], name=names[key][0],
                       surface=f"contaminants/{records[key]['path']}/{names[key][1]}.orcacosmo",
                       folder=f"contaminants/{records[key]['path']}")
            handle.write("\t".join(clean(rec[c]) for c in columns) + "\n")
    tops = {}
    for rec in records.values():
        tops[rec["path"].split("/")[0]] = tops.get(rec["path"].split("/")[0], 0) + 1
    print(json.dumps(dict(release=release.name, parent=parent, new_contaminants=len(new),
                          renamed_for_collision=suffixed, folders=tops)))


if __name__ == "__main__":
    main(*sys.argv[1:4])
