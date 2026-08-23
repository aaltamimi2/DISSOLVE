#!/usr/bin/env python3
"""Generate an RDKit conformer ensemble and verify every member is the target.

    PYTHONPATH=src python3 scripts/cosmo/make_conformers.py \
        --smiles "CCOC(=O)c1ccccc1C(=O)OCC" --tag dep --outdir /tmp/conf

Every conformer is identity-checked. Embedding and force-field optimisation can
in principle produce a structure that is no longer the input molecule, and a
silently wrong geometry would propagate into every downstream number.
"""
from __future__ import annotations

import argparse, json, sys
from pathlib import Path

# Load cosmo_logp WITHOUT importing the dissolve package.
# `from dissolve import cosmo_logp` executes dissolve/__init__.py, which pulls in
# the registry, tools and langchain_core. The COSMO work runs in an isolated venv
# that deliberately does NOT have those -- its numpy/RDKit pins conflict with the
# main environment. Loading the single module by path keeps both true: the module
# is a normal member of the package, and these scripts run without the package.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "dissolve_cosmo_logp",
    str(Path(__file__).resolve().parents[2] / "src" / "dissolve" / "cosmo_logp.py"),
)
cl = _ilu.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves cls.__module__ through sys.modules,
# and an unregistered module makes that lookup return None.
import sys as _sys
_sys.modules["dissolve_cosmo_logp"] = cl
_spec.loader.exec_module(cl)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", default=cl.DEP_SMILES)
    ap.add_argument("--tag", default="dep")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--n-embed", type=int, default=300)
    ap.add_argument("--seed", type=int, default=12345)
    ap.add_argument("--max-keep", type=int, default=8)
    ap.add_argument("--energy-window", type=float, default=3.0, help="kcal/mol")
    ap.add_argument("--expect-inchikey", default=cl.DEP_INCHIKEY)
    args = ap.parse_args()

    out = Path(args.outdir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    confs = cl.generate_conformers(args.smiles, n_embed=args.n_embed, seed=args.seed)
    kept = [c for c in confs if c[1] <= args.energy_window][: args.max_keep]
    print(f"  {len(confs)} converged, keeping {len(kept)} within {args.energy_window} kcal/mol")

    manifest = []
    for index, (atoms, energy) in enumerate(kept):
        path = cl.write_xyz(atoms, out / f"{args.tag}_c{index}.xyz",
                            comment=f"{args.tag} conformer {index} MMFF rel {energy:.2f} kcal/mol")
        if args.expect_inchikey:
            cl.verify_identity(path, args.expect_inchikey)
        manifest.append({"index": index, "file": path.name, "mmff_relative_kcal": energy})
        print(f"    {path.name}  MMFF rel {energy:6.2f} kcal/mol  identity ok")

    (out / f"{args.tag}_conformers.json").write_text(json.dumps({
        "smiles": args.smiles, "seed": args.seed, "n_embed": args.n_embed,
        "energy_window_kcal": args.energy_window,
        "inchikey": args.expect_inchikey, "conformers": manifest,
    }, indent=2))
    print(f"  MMFF energies ORDER the conformers; DFT energies weight them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
