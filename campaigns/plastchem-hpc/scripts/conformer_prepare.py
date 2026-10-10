"""Exploratory conformer study (owner request 2026-10-10; not a release input): up to 10 distinct conformers per selected
contaminant from the campaign's own minimization. The frozen preparation (prepare_campaign.prepare: ETKDGv3 seed 12345,
300 embeddings, pruneRmsThresh 0.5, MMFF94 maxIters 2000) ranks the MMFF-converged conformers by energy and sends only
the lowest to DFT. Here the same ranked ensemble is walked in energy order and a conformer is kept when its skeleton
RMSD (heavy atoms bonded to at least two heavy atoms, so terminal F, Cl, Br, I, O and methyl carbons, whose positions
follow the skeleton; symmetry-aware, after optimal alignment) to every conformer already kept, and to its mirror image,
is at least 0.5 A (the recipe's own pruning threshold), until 10 are kept or the ensemble is exhausted. A mirror-image
conformer (a perfluoro helix of the other hand, a biaryl twisted the other way) has the same energy and the same
COSMO surface, so it is the same conformer for COSMO-RS and is not run twice. Conformer k01 is therefore the recipe's conformer;
its geometry is compared with the one the campaign prepared (same SHA-256 when the RDKit build is the same). Each kept
conformer then goes through the unchanged ORCA recipe (BP86/def2-TZVP(-f) optimization, COSMORS(Water)) on Euler.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_prepare.py [--workers 3] [--max 10] [--rmsd 0.5]"""
import os

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
import argparse  # noqa: E402
import concurrent.futures  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import rdkit  # noqa: E402
from rdkit import Chem  # noqa: E402
from rdkit.Chem import AllChem, rdMolAlign  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state/conformers-v1"
TIERS = ["coverage-v1", "halogen-v1", "tier2-v1", "campaign-v1", "publication-v1"]


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def campaign_geometry(key):
    """The tier that prepared this structure for the campaign and the SHA-256 of the geometry it sent to DFT."""
    for tier in TIERS:
        p = ROOT / "state" / tier / "prepared" / key / "preparation.json"
        if p.exists():
            prep = json.loads(p.read_text())
            if prep.get("status") == "prepared":
                return tier, prep.get("xyz_sha256")
    return None, None


def conformers(compound, keep, threshold):
    start = time.monotonic()
    key = compound["inchikey"]
    mol = Chem.MolFromSmiles(compound["smiles"])
    assert mol is not None and Chem.MolToInchiKey(mol) == key, key
    h = Chem.AddHs(mol)
    assert AllChem.MMFFHasAllMoleculeParams(h), key
    # the frozen recipe, statement for statement (prepare_campaign.prepare)
    params = AllChem.ETKDGv3()
    params.randomSeed = 12345
    params.pruneRmsThresh = 0.5
    params.numThreads = 1
    ids = list(AllChem.EmbedMultipleConfs(h, numConfs=300, params=params))
    scores = AllChem.MMFFOptimizeMoleculeConfs(h, numThreads=1, maxIters=2000)
    ranked = sorted((energy, cid) for cid, (status, energy) in zip(ids, scores) if status == 0)
    assert ranked, key
    # RMSD on the skeleton: a perfluoro chain's terminal F atoms alone give 2^n symmetry-equivalent mappings
    heavy = Chem.RemoveHs(h)
    skeleton = Chem.RWMol(heavy)
    for idx in sorted((a.GetIdx() for a in heavy.GetAtoms() if a.GetDegree() <= 1), reverse=True):
        skeleton.RemoveAtom(idx)
    skeleton = skeleton.GetMol()
    skeleton.UpdatePropertyCache(strict=False)
    Chem.FastFindRings(skeleton)
    mirror = Chem.Mol(skeleton)
    for conformer in mirror.GetConformers():
        for i in range(mirror.GetNumAtoms()):
            x, y, z = conformer.GetAtomPosition(i)
            conformer.SetAtomPosition(i, (-x, y, z))
    kept = []
    rmsd = {}
    for energy, cid in ranked:  # GetBestRMS moves the probe conformer of the copy it is given only
        distances = [min(rdMolAlign.GetBestRMS(skeleton, skeleton, prbId=cid, refId=other, maxMatches=10000),
                         rdMolAlign.GetBestRMS(mirror, skeleton, prbId=cid, refId=other, maxMatches=10000)) for _, other in kept]
        if all(d >= threshold for d in distances):
            for (_, other), d in zip(kept, distances):
                rmsd[(cid, other)] = rmsd[(other, cid)] = d
            kept.append((energy, cid))
            if len(kept) == keep:
                break
    tier, campaign_sha = campaign_geometry(key)
    e0 = kept[0][0]
    out = []
    for rank, (energy, cid) in enumerate(kept, 1):
        xyz = Chem.MolToXYZBlock(h, confId=cid)
        ckey = f"{key}-k{rank:02d}"
        folder = STATE / "prepared" / ckey
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "input.xyz").write_text(xyz)
        entry = dict(inchikey=ckey, parent_inchikey=key, conformer_rank=rank, name=compound["name"],
                     smiles=compound["smiles"], atoms=h.GetNumAtoms(), molecular_weight_g_mol=compound["molecular_weight_g_mol"],
                     rotatable_bonds=compound["rotatable_bonds"], role=compound["role"], mw_bin=compound["mw_bin"])
        prep = dict(input=entry, status="prepared", xyz_sha256=sha(xyz), selected_conformer_id=cid,
                    selected_mmff_energy_kcal=energy, mmff_delta_kcal=energy - e0,
                    skeleton_rmsd_to_k01=0.0 if rank == 1 else rmsd[(cid, kept[0][1])],
                    min_skeleton_rmsd_to_lower_ranks=None if rank == 1 else min(rmsd[(cid, o)] for _, o in kept[:rank - 1]),
                    recipe=dict(n_embed=300, seed=12345, prune_rms=0.5, max_mmff_iters=2000,
                                conformer_selection=f"energy order, kept if skeleton RMSD >= {threshold} A to every kept one and to its mirror image",
                                skeleton_atoms=skeleton.GetNumAtoms(),
                                max_conformers=keep, rdkit=rdkit.__version__))
        (folder / "preparation.json").write_text(json.dumps(prep, indent=2) + "\n")
        out.append(entry)
    summary = dict(inchikey=key, name=compound["name"], role=compound["role"], rotatable_bonds=compound["rotatable_bonds"],
                   atoms=h.GetNumAtoms(), embedded=len(ids), mmff_converged=len(ranked), kept=len(kept),
                   mmff_window_kcal=kept[-1][0] - e0,
                   campaign_tier=tier, k01_matches_campaign_geometry=(campaign_sha == sha(Chem.MolToXYZBlock(h, confId=kept[0][1]))) if campaign_sha else None,
                   skeleton_rmsd_matrix=[[0.0 if a == b else round(rmsd[(a, b)], 3) for _, b in kept] for _, a in kept],
                   mmff_energies_kcal=[e for e, _ in kept], wall_seconds=round(time.monotonic() - start, 1))
    (STATE / "ensembles").mkdir(parents=True, exist_ok=True)
    (STATE / "ensembles" / f"{key}.json").write_text(json.dumps(summary, indent=1) + "\n")
    return summary, out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max", type=int, default=10)
    parser.add_argument("--rmsd", type=float, default=0.5)
    args = parser.parse_args()
    compounds = json.loads((STATE / "selection.json").read_text())["compounds"]
    todo = [c for c in compounds if not (STATE / "ensembles" / f"{c['inchikey']}.json").exists()]
    with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as pool:
        jobs = {pool.submit(conformers, c, args.max, args.rmsd): c for c in todo}
        for future in concurrent.futures.as_completed(jobs):
            s, _ = future.result()
            print(json.dumps({k: s[k] for k in ("name", "role", "rotatable_bonds", "atoms", "embedded", "mmff_converged", "kept",
                                                "mmff_window_kcal", "campaign_tier", "k01_matches_campaign_geometry", "wall_seconds")})[:300], flush=True)


if __name__ == "__main__":
    main()
