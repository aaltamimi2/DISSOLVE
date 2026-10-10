"""Exploratory conformer study (owner request 2026-10-10; not a release input): pick the contaminants. From the computed
contaminants of a release, in 50 g/mol bins down from the heaviest one served, take per bin the two with the most
rotatable bonds (ties: more atoms) and the heaviest rigid one (at most 4 rotatable bonds; if the bin has none, the one
with the fewest), each compound once, until at least --target compounds are chosen (a bin is never split).

    python3 scripts/conformer_select.py [--release promotion-v11] [--target 30]   (anaconda RDKit 2023.09.2)"""
import argparse
import csv
import gzip
import json
from pathlib import Path

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state/conformers-v1"
OUT = ROOT / "exploratory/conformer-sampling"
RIGID_MAX_ROTATABLE = 4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", default="promotion-v11")
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--bin-width", type=float, default=50.0)
    args = parser.parse_args()
    with gzip.open(f"/mnt/r/plastchem-euler/{args.release}/contaminants.csv.gz", "rt") as handle:
        rows = [r for r in csv.DictReader(handle) if r["campaign_status_at_snapshot"] == "converged"]
    for r in rows:
        mol = Chem.MolFromSmiles(r["smiles"])
        r["mw"] = float(r["molecular_weight_g_mol"])
        r["atoms"] = int(float(r["atom_count"]))
        r["rotatable_bonds"] = rdMolDescriptors.CalcNumRotatableBonds(mol)
        r["heavy_atoms"] = mol.GetNumHeavyAtoms()
    top = max(r["mw"] for r in rows)
    bins = {}
    for r in rows:
        bins.setdefault(int((top - r["mw"]) // args.bin_width), []).append(r)
    chosen = []
    for b in sorted(bins):
        members = bins[b]
        picks = []
        for r in sorted(members, key=lambda r: (-r["rotatable_bonds"], -r["atoms"], r["name"]))[:2]:
            picks.append((r, "flexible"))
        rest = [r for r in members if all(r is not p for p, _ in picks)]
        rigid = [r for r in rest if r["rotatable_bonds"] <= RIGID_MAX_ROTATABLE]
        pool = sorted(rigid, key=lambda r: (-r["mw"], r["name"])) or sorted(rest, key=lambda r: (r["rotatable_bonds"], -r["mw"]))
        if pool:
            picks.append((pool[0], "rigid"))
        for r, role in picks:
            chosen.append(dict(inchikey=r["input_inchikey"], name=r["name"], smiles=r["smiles"], tier=r["tier"],
                               molecular_weight_g_mol=r["mw"], atoms=r["atoms"], heavy_atoms=r["heavy_atoms"],
                               rotatable_bonds=r["rotatable_bonds"], role=role, mw_bin=b,
                               bin_g_mol=[round(top - args.bin_width * (b + 1), 1), round(top - args.bin_width * b, 1)],
                               bin_size=len(members)))
        if len(chosen) >= args.target:
            break
    STATE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    selection = dict(release=args.release, max_molecular_weight_g_mol=top, bin_width_g_mol=args.bin_width,
                     rule=__doc__.split("\n\n")[0], rigid_max_rotatable_bonds=RIGID_MAX_ROTATABLE,
                     rotatable_bonds="RDKit rdMolDescriptors.CalcNumRotatableBonds (default strictness)",
                     compounds=chosen)
    (STATE / "selection.json").write_text(json.dumps(selection, indent=1) + "\n")
    fields = ["mw_bin", "bin_g_mol", "role", "name", "inchikey", "molecular_weight_g_mol", "atoms", "heavy_atoms",
              "rotatable_bonds", "tier", "bin_size", "smiles"]
    with (OUT / "selection.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for c in chosen:
            writer.writerow(dict(c, bin_g_mol=f"{c['bin_g_mol'][0]}-{c['bin_g_mol'][1]}"))
    print(json.dumps(dict(compounds=len(chosen), bins=sorted({c["mw_bin"] for c in chosen}),
                          roles={k: sum(c["role"] == k for c in chosen) for k in ("flexible", "rigid")},
                          mw_range=[min(c["molecular_weight_g_mol"] for c in chosen), top])))


if __name__ == "__main__":
    main()
