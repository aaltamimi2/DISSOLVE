#!/usr/bin/env python3
"""Stage 1: geometry + COSMO surface for one molecule. Emits .orcacosmo.

    PYTHONPATH=src python3 scripts/cosmo/run_orca_stage.py \
        --xyz dep.xyz --tag dep --outdir ~/cosmo-artifacts/stage1

Runs SERIAL by default. The shared_openmpi418 ORCA build wants libmpi.so.40,
and a machine whose system MPI is MPICH does not have it -- %pal then dies in
Startup with an error that does not mention MPI. Parallelise across molecules
instead of within one job; these are small.
"""
from __future__ import annotations

import argparse, os, subprocess, sys, time
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
    str(Path(__file__).resolve().parents[1] / "cosmo_logp.py"),
)
cl = _ilu.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves cls.__module__ through sys.modules,
# and an unregistered module makes that lookup return None.
import sys as _sys
_sys.modules["dissolve_cosmo_logp"] = cl
_spec.loader.exec_module(cl)


def read_xyz(path: Path) -> list[cl.Atom]:
    lines = path.read_text().splitlines()
    return [
        cl.Atom(p[0].capitalize(), float(p[1]), float(p[2]), float(p[3]))
        for p in (l.split() for l in lines[2:]) if len(p) == 4
    ]


def run_orca(orca: str, inp: Path, out: Path) -> int:
    started = time.time()
    with out.open("w") as handle:
        code = subprocess.run([orca, inp.name], cwd=inp.parent, stdout=handle,
                              stderr=subprocess.STDOUT).returncode
    print(f"    {inp.name}: exit={code} wall={time.time()-started:.0f}s")
    return code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xyz", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--orca", default=os.environ.get("ORCA_BIN", "orca"))
    ap.add_argument("--solvent", default="Water")
    ap.add_argument("--maxcore", type=int, default=1500,
                    help="MB PER PROCESS, not per job")
    ap.add_argument("--expect-inchikey", default=None,
                    help="verify identity before AND after optimisation")
    args = ap.parse_args()

    work = Path(args.outdir).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    atoms = read_xyz(Path(args.xyz).expanduser())
    print(f"  {args.tag}: {len(atoms)} atoms")

    if args.expect_inchikey:
        cl.verify_identity(Path(args.xyz).expanduser(), args.expect_inchikey)
        print(f"    identity before opt: {args.expect_inchikey}")

    opt_in = work / f"{args.tag}.inp"
    opt_in.write_text(cl.orca_opt_input(atoms, maxcore_mb=args.maxcore))
    opt_out = work / f"{args.tag}.opt.out"
    if run_orca(args.orca, opt_in, opt_out) != 0:
        print("    ORCA optimisation failed", file=sys.stderr)
        return 1

    text = opt_out.read_text(errors="ignore")
    if not cl.orca_converged(text):
        print("    WARNING: optimisation did not report convergence", file=sys.stderr)
    optimised = cl.parse_orca_optimised_geometry(text)
    xyz_out = cl.write_xyz(optimised, work / f"{args.tag}.opt.xyz", comment=f"{args.tag} optimised")
    print(f"    energy {cl.parse_orca_final_energy(text):.9f} Eh")

    # An optimiser that closes a ring or transfers a proton has changed the
    # molecule. Checking only the input would not catch it.
    if args.expect_inchikey:
        cl.verify_identity(xyz_out, args.expect_inchikey)
        print(f"    identity after opt:  {args.expect_inchikey}")

    cos_in = work / f"{args.tag}_cosmo.inp"
    cos_in.write_text(cl.orca_cosmors_input(optimised, solvent=args.solvent, maxcore_mb=args.maxcore))
    if run_orca(args.orca, cos_in, work / f"{args.tag}_cosmo.out") != 0:
        return 1

    produced = work / f"{args.tag}_cosmo.solute.orcacosmo"
    if not produced.is_file():
        print(f"    no .orcacosmo produced -- is ORCA >= {cl.ORCA_MIN_VERSION}?", file=sys.stderr)
        return 1
    print(f"    wrote {produced.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
