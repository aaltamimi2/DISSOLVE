"""A-11: assemble every openCOSMO surface behind promotion-v2 into opencosmo-outputs/ (contaminants, polymers,
solvents). Contaminants are named by their human-readable folder name (contaminant_folder_names.py) and filed under
the category folder classify_contaminants.py gave them (contaminants/<category>/<name>.orcacosmo), with
CLASSIFICATION.tsv and PUBLICATION_SETS.tsv beside MANIFEST.tsv; polymers and solvents as in v1. Every contaminant surface must match the SHA-256 the release recorded. Runs on Euler from
~/opencosmo-export-v2, reading the v1 export's solvent and polymer inputs; only ORCA/openCOSMO outputs (.orcacosmo) go
in, never a licensed COSMObase/COSMOtherm file."""
import hashlib, pathlib, sys

HOME = pathlib.Path.home()
EXP = HOME / "opencosmo-export-v2"
V1 = HOME / "opencosmo-export"
OUT = EXP / "opencosmo-outputs"
LANE = HOME / "plastchem-euler"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def link(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        dst.unlink()
    dst.symlink_to(src)


rows, bad = [], []
# contaminants: inchikey, sha256, name, folder, category, candidate paths under ~/plastchem-euler (first matching one
# is used)
for line in open(EXP / "contaminants-v2.tsv"):
    inchikey, expected, name, folder, category, candidates = line.rstrip("\n").split("\t")
    src = next((LANE / rel for rel in candidates.split(",") if (LANE / rel).is_file() and sha(LANE / rel) == expected), None)
    if src is None:
        bad.append(("contaminant", inchikey, expected, candidates))
        continue
    dst = OUT / "contaminants" / category / f"{folder}.orcacosmo"
    link(src, dst)
    rows.append(("contaminant", dst.relative_to(OUT), inchikey, name, expected, str(src.relative_to(LANE))))
for line in open(V1 / "panel.tsv"):
    safe, rel, expected, name = line.rstrip("\n").split("\t")
    src = LANE / "phase8-v1" / rel
    got = sha(src)
    if got != expected:
        bad.append(("panel solvent", name, expected, got))
    dst = OUT / "solvents" / "panel-32" / f"{safe}.orcacosmo"
    link(src, dst)
    rows.append(("solvent (32-solvent panel)", dst.relative_to(OUT), name, name, got, f"phase8-v1/{rel}"))
common = {line.split("\t")[0]: line.rstrip("\n").split("\t") for line in open(V1 / "common.tsv")}
for inchikey, (_, safe, name) in sorted(common.items()):
    src = V1 / "incoming" / "phase9-solvent-library-v1" / "results" / inchikey / "surface.orcacosmo"
    dst = OUT / "solvents" / "common-69" / f"{safe}.orcacosmo"
    link(src, dst)
    rows.append(("solvent (69 common)", dst.relative_to(OUT), inchikey, name, sha(src),
                 f"phase9-solvent-library-v1/results/{inchikey}/surface.orcacosmo"))
for src in sorted((V1 / "incoming" / "polymer-v1" / "results").glob("*/surface.orcacosmo")):
    conformer = src.parent.name
    polymer = conformer.split("__")[0]
    dst = OUT / "polymers" / polymer / f"{conformer}.orcacosmo"
    link(src, dst)
    rows.append(("polymer conformer", dst.relative_to(OUT), conformer, polymer, sha(src),
                 f"polymer-v1/results/{conformer}/surface.orcacosmo"))
placed = {str(row[1]) for row in rows if row[0] == "contaminant"}
classified = [line.split("\t")[2] for line in open(EXP / "CLASSIFICATION.tsv")][1:]
if sorted(classified) != sorted(placed):  # the tables must describe exactly the files in the archive
    bad.append(("CLASSIFICATION.tsv", len(classified), len(placed), sorted(set(classified) ^ placed)[:3]))
for line in list(open(EXP / "PUBLICATION_SETS.tsv"))[1:]:
    for path in filter(None, line.split("\t")[4].split("; ")):
        if path not in placed:
            bad.append(("PUBLICATION_SETS.tsv", path))
if bad:
    print("UNRESOLVED OR MISMATCHED:", len(bad), bad[:5], file=sys.stderr)
    sys.exit(1)
for table in ("CLASSIFICATION.tsv", "PUBLICATION_SETS.tsv"):
    (OUT / table).write_bytes((EXP / table).read_bytes())
with open(OUT / "MANIFEST.tsv", "w") as f:
    f.write("category\tpath\tidentity\tname\tsha256\tcampaign_source\n")
    for row in rows:
        f.write("\t".join(map(str, row)) + "\n")
counts = {}
for row in rows:
    counts[row[0]] = counts.get(row[0], 0) + 1
print("\n".join(f"{k}: {v}" for k, v in counts.items()), "\ntotal:", len(rows), "files; hash mismatches: 0")
