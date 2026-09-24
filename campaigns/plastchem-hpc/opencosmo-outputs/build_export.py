"""Assemble every openCOSMO surface of the PlastChem campaign into opencosmo-outputs/ (contaminants, polymers,
solvents), check each against the campaign's recorded SHA-256 where one exists, and write MANIFEST.tsv and README.md.
Runs on Euler from ~/opencosmo-export. Only ORCA/openCOSMO outputs (.orcacosmo) go in: the licensed COSMObase/COSMOtherm
route-A .cosmo files are never touched."""
import hashlib, os, pathlib, sys

HOME = pathlib.Path.home()
EXP = HOME / "opencosmo-export"
OUT = EXP / "opencosmo-outputs"
LANE = HOME / "plastchem-euler"
INCOMING = EXP / "incoming"


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
# contaminants: the frozen cohort's staged surfaces, named by InChIKey; expected hash from the cohort
for line in open(EXP / "contaminants.tsv"):
    inchikey, rel, expected, name = line.rstrip("\n").split("\t")
    src = LANE / "phase83-v1" / rel
    got = sha(src)
    if got != expected:
        bad.append(("contaminant", inchikey, expected, got))
    dst = OUT / "contaminants" / f"{inchikey}.orcacosmo"
    link(src, dst)
    rows.append(("contaminant", dst.relative_to(OUT), inchikey, name, got, f"phase83-v1/{rel}"))
# panel solvents: the 32 production solvent surfaces; the file name is the expected hash
for line in open(EXP / "panel.tsv"):
    safe, rel, expected, name = line.rstrip("\n").split("\t")
    src = LANE / "phase8-v1" / rel
    got = sha(src)
    if got != expected:
        bad.append(("panel solvent", name, expected, got))
    dst = OUT / "solvents" / "panel-32" / f"{safe}.orcacosmo"
    link(src, dst)
    rows.append(("solvent (32-solvent panel)", dst.relative_to(OUT), name, name, got, f"phase8-v1/{rel}"))
# common solvents: the 69-solvent library (DFT from COSMObase starting geometries; the output surfaces are ours)
common = {line.split("\t")[0]: line.rstrip("\n").split("\t") for line in open(EXP / "common.tsv")}
for inchikey, (_, safe, name) in sorted(common.items()):
    src = INCOMING / "phase9-solvent-library-v1" / "results" / inchikey / "surface.orcacosmo"
    dst = OUT / "solvents" / "common-69" / f"{safe}.orcacosmo"
    link(src, dst)
    rows.append(("solvent (69 common)", dst.relative_to(OUT), inchikey, name, sha(src),
                 f"phase9-solvent-library-v1/results/{inchikey}/surface.orcacosmo"))
# polymers: every computed conformer surface, grouped by polymer
for src in sorted((INCOMING / "polymer-v1" / "results").glob("*/surface.orcacosmo")):
    conformer = src.parent.name
    polymer = conformer.split("__")[0]
    dst = OUT / "polymers" / polymer / f"{conformer}.orcacosmo"
    link(src, dst)
    rows.append(("polymer conformer", dst.relative_to(OUT), conformer, polymer, sha(src),
                 f"polymer-v1/results/{conformer}/surface.orcacosmo"))

if bad:
    print("HASH MISMATCH:", bad[:5], file=sys.stderr)
    sys.exit(1)
with open(OUT / "MANIFEST.tsv", "w") as f:
    f.write("category\tpath\tidentity\tname\tsha256\tcampaign_source\n")
    for row in rows:
        f.write("\t".join(map(str, row)) + "\n")
counts = {}
for row in rows:
    counts[row[0]] = counts.get(row[0], 0) + 1
print("\n".join(f"{k}: {v}" for k, v in counts.items()), "\ntotal:", len(rows), "files; hash mismatches: 0")
