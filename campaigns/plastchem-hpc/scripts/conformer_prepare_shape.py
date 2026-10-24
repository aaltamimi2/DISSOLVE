"""Exploratory conformer study on CHTC (owner request 2026-10-13; not a release input): a shape-stratified conformer
set for the compounds of the 30-conformer test. The energy-ordered selection (conformer_prepare.py) stays within a few
kcal/mol of the MMFF minimum and covers about 70% of the SASA span of the embedded pool with 30 conformers, missing the
most exposed shapes. Here the same pool (the frozen embedding: ETKDGv3 seed 12345, 300 embeds, prune 0.5, MMFF94 2000
iterations) is cut into 10 bins of equal width in solvent-accessible surface area, from the pool's smallest to its
largest, and the conformer of lowest MMFF energy in each bin is taken, whatever its energy. A bin whose pick is already
one of the 30 energy-ordered conformers is not run twice (recorded as covered); an empty bin stays empty.

Conformer keys are <InChIKey>-k41 ... -k50 (bin 1 = most compact surface ... bin 10 = most exposed), so that they
cannot collide with the energy-ordered k01-k30. The re-embedded pool is checked against the prepared k01-k30 geometries
by SHA-256 before anything is written.

Writes state/conformers-htc-shape-v1/prepared/<key>/, ensembles/<InChIKey>.json and selection.json.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_prepare_shape.py [--workers N]"""
import argparse
import concurrent.futures
import hashlib
import json
import os

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import rdkit  # noqa: E402
from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem, rdFreeSASA  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "state/conformers-htc-30-v1"
STATE = ROOT / "state/conformers-htc-shape-v1"
BINS = 10
PT = Chem.GetPeriodicTable()


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def sasa(h, cid):
    opts = rdFreeSASA.SASAOpts()
    opts.probeRadius = 1.4
    return rdFreeSASA.CalcSASA(h, [PT.GetRvdw(a.GetAtomicNum()) for a in h.GetAtoms()], confIdx=cid, opts=opts)


def stratify(compound):
    key = compound["inchikey"]
    mol = Chem.MolFromSmiles(compound["smiles"])
    assert mol is not None and Chem.MolToInchiKey(mol) == key, key
    h = Chem.AddHs(mol)
    params = AllChem.ETKDGv3()
    params.randomSeed = 12345
    params.pruneRmsThresh = 0.5
    params.numThreads = 1
    ids = list(AllChem.EmbedMultipleConfs(h, numConfs=300, params=params))
    scores = AllChem.MMFFOptimizeMoleculeConfs(h, numThreads=1, maxIters=2000)
    pool = sorted((energy, cid) for cid, (status, energy) in zip(ids, scores) if status == 0)
    used = {}
    for rank in range(1, 31):  # the pool is the one the energy-ordered conformers came from
        prep = json.loads((SOURCE / "prepared" / f"{key}-k{rank:02d}" / "preparation.json").read_text())
        assert sha(Chem.MolToXYZBlock(h, confId=prep["selected_conformer_id"])) == prep["xyz_sha256"], f"{key} k{rank:02d}: the pool is not reproduced"
        used[prep["selected_conformer_id"]] = rank
    e0 = pool[0][0]
    area = {cid: sasa(h, cid) for _, cid in pool}
    low, high = min(area.values()), max(area.values())
    edges = np.linspace(low, high, BINS + 1)
    bins, out = [], []
    for b in range(BINS):
        inside = [(e, cid) for e, cid in pool if edges[b] <= area[cid] < edges[b + 1] or (b == BINS - 1 and area[cid] == high)]
        row = dict(bin=b + 1, sasa_from_A2=round(float(edges[b]), 2), sasa_to_A2=round(float(edges[b + 1]), 2), pool_conformers=len(inside))
        if inside:
            energy, cid = inside[0]
            row.update(selected_conformer_id=cid, sasa_A2=round(area[cid], 2), mmff_delta_kcal=round(energy - e0, 3))
            if cid in used:
                row["covered_by_energy_ordered"] = f"k{used[cid]:02d}"
            else:
                ckey = f"{key}-k{40 + b + 1:02d}"
                xyz = Chem.MolToXYZBlock(h, confId=cid)
                folder = STATE / "prepared" / ckey
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "input.xyz").write_text(xyz)
                entry = dict(inchikey=ckey, parent_inchikey=key, conformer_rank=40 + b + 1, name=compound["name"], smiles=compound["smiles"],
                             atoms=h.GetNumAtoms(), molecular_weight_g_mol=compound["molecular_weight_g_mol"],
                             rotatable_bonds=compound["rotatable_bonds"], role=compound["role"], mw_bin=compound["mw_bin"])
                prep = dict(input=entry, status="prepared", xyz_sha256=sha(xyz), selected_conformer_id=cid,
                            selected_mmff_energy_kcal=energy, mmff_delta_kcal=energy - e0, sasa_bin=b + 1, mmff_sasa_A2=area[cid],
                            recipe=dict(n_embed=300, seed=12345, prune_rms=0.5, max_mmff_iters=2000,
                                        conformer_selection=f"lowest MMFF energy in bin {b + 1} of {BINS} equal-width SASA bins over the pool "
                                                            f"({low:.1f}-{high:.1f} A2; probe 1.4 A, RDKit van der Waals radii)",
                                        rdkit=rdkit.__version__))
                (folder / "preparation.json").write_text(json.dumps(prep, indent=2) + "\n")
                row["conformer"] = ckey
                out.append(ckey)
        bins.append(row)
    summary = dict(inchikey=key, name=compound["name"], role=compound["role"], atoms=h.GetNumAtoms(), pool=len(pool),
                   pool_sasa_A2=[round(low, 2), round(high, 2)], kept=len(out), bins=bins)
    (STATE / "ensembles").mkdir(parents=True, exist_ok=True)
    (STATE / "ensembles" / f"{key}.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    source = json.loads((SOURCE / "selection.json").read_text())
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "selection.json").write_text(json.dumps(dict(
        scope="exploratory, not a release input (owner 2026-10-13: queue the shape-stratified set)",
        rule="the compounds of the 30-conformer test; per compound the lowest-MMFF-energy conformer in each of 10 equal-width SASA bins of the embedded pool",
        compounds=source["compounds"]), indent=1) + "\n")
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        for s in pool.map(stratify, source["compounds"]):
            print(json.dumps(dict(name=s["name"][:40], pool=s["pool"], new=s["kept"],
                                  covered=sum(1 for b in s["bins"] if "covered_by_energy_ordered" in b),
                                  empty=sum(1 for b in s["bins"] if not b["pool_conformers"]),
                                  mmff_delta_kcal=[b.get("mmff_delta_kcal") for b in s["bins"]])), flush=True)


if __name__ == "__main__":
    main()
