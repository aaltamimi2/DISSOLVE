#!/usr/bin/env python3
"""Delta logD from .orcacosmo files, scored against the anchor pairs.

    PYTHONPATH=src python3 scripts/cosmo/compute_delta_logd.py \
        --artifacts ~/cosmo-artifacts/stage1 --solute dep

With --conformer-dir, the solute is treated as a Boltzmann ensemble: each
conformer is evaluated separately and combined explicitly, because
COSMORS.add_molecule accepts a list but raises NotImplementedError on more
than one conformer.
"""
from __future__ import annotations

import argparse, json, re, sys
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

SOLVENT_TAGS = {"dichloromethane": "dcm", "water": "water", "methanol": "methanol",
                "hexane": "hexane", "cyclohexanol": "cyclohexanol"}


def solvent_file(root: Path, name: str) -> Path:
    return root / f"{SOLVENT_TAGS.get(name, name)}_cosmo.solute.orcacosmo"


def conformer_energies(conf_dir: Path) -> list[tuple[Path, float]]:
    """(.orcacosmo, relative DFT energy kcal/mol) for each conformer."""
    rows = []
    for cosmo in sorted(conf_dir.rglob("*_cosmo.solute.orcacosmo")):
        tag = re.sub(r"_cosmo\.solute\.orcacosmo$", "", cosmo.name)
        out = cosmo.parent / f"{tag}.opt.out"
        if not out.is_file():
            raise cl.CosmoError(f"no optimisation output beside {cosmo.name}")
        rows.append((cosmo, cl.parse_orca_final_energy(out.read_text(errors="ignore"))))
    if not rows:
        raise cl.CosmoError(f"no .orcacosmo under {conf_dir}")
    lowest = min(e for _, e in rows)
    return [(p, (e - lowest) * cl.HARTREE_TO_KCAL) for p, e in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifacts", required=True, help="dir holding the solvent .orcacosmo files")
    ap.add_argument("--solute", default="dep")
    ap.add_argument("--conformer-dir", default=None, help="enables the Boltzmann ensemble")
    ap.add_argument("--basis", choices=["mole_fraction", "concentration"], default="concentration")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    root = Path(args.artifacts).expanduser()
    solvents = sorted({s for pair in cl.ANCHOR_PAIRS for s in pair[:2]})

    if args.conformer_dir:
        ensemble = conformer_energies(Path(args.conformer_dir).expanduser())
        print(f"  ensemble of {len(ensemble)} conformers")
        for path, dE in ensemble:
            print(f"    {path.parent.name}/{path.name}  dE {dE:5.2f} kcal/mol")
    else:
        ensemble = [(root / f"{args.solute}_cosmo.solute.orcacosmo", 0.0)]

    ln_gamma: dict[str, float] = {}
    for solvent in solvents:
        sf = solvent_file(root, solvent)
        if not sf.is_file():
            print(f"  {solvent}: MISSING {sf.name}", file=sys.stderr)
            continue
        per_conf = [cl.ln_gamma_infinite_dilution(p, sf) for p, _ in ensemble]
        ln_gamma[solvent] = (
            cl.boltzmann_combine(per_conf, [d for _, d in ensemble])
            if len(per_conf) > 1 else per_conf[0]
        )
        print(f"  ln_gamma_inf({args.solute} in {solvent:<16}) = {ln_gamma[solvent]:9.3f}")

    use_volume = args.basis == "concentration"
    predicted = {}
    for a, b, _ in cl.ANCHOR_PAIRS:
        if a in ln_gamma and b in ln_gamma:
            predicted[f"{a}-{b}"] = cl.delta_log_d(
                ln_gamma[a], ln_gamma[b],
                volume_a=cl.MOLAR_VOLUMES_CM3[a] if use_volume else None,
                volume_b=cl.MOLAR_VOLUMES_CM3[b] if use_volume else None,
            )

    result = cl.evaluate_anchor_pairs(predicted)
    print(f"\n  {'pair':<32}{'predicted':>10}{'ours':>8}{'resid':>8}")
    for row in result["rows"]:
        if row["predicted"] is None:
            print(f"  {row['pair']:<32}{'--':>10}{row['ours']:>8.2f}")
            continue
        flag = "" if row["passes"] else "   <-- outside tolerance"
        print(f"  {row['pair']:<32}{row['predicted']:>10.2f}{row['ours']:>8.2f}{row['residual']:>+8.2f}{flag}")
    print(f"\n  tolerance {result['tolerance']} on >= {cl.MIN_PAIRS_PASSING} of 4, water-free pair required")
    print(f"  passing {result['n_passing']}/4   water-free passes: {result['water_free_passes']}")
    print(f"  ACCEPT: {result['accept']}")

    if args.out:
        payload = {"solute": args.solute, "ln_gamma_inf": ln_gamma, "basis": args.basis,
                   "n_conformers": len(ensemble), "evaluation": result}
        Path(args.out).expanduser().write_text(json.dumps(payload, indent=2))
        print(f"  wrote {args.out}")
    return 0 if result["accept"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
