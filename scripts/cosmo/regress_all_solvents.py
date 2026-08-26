#!/usr/bin/env python3
"""Regress our whole logD row for one contaminant against COSMO-RS.

    PYTHONPATH=src python3 scripts/cosmo/regress_all_solvents.py \
        --contaminant "diethyl phthalate" --cosmo-dir <COSMObase> --out fits.json

THE TEST. If our logD is log10(C_S / C_ref) against ONE constant reference
phase, then logD(S) = -log10(gamma_S) + K and the regression has SLOPE 1.
A slope away from 1 means the two are not the same physical quantity -- or
that one solvent is levering the fit, which is why the exclusions below are
reported as separate fits rather than folded in silently.

Uses COSMObase Turbomole .cosmo files with the default_turbomole
parameterisation: 31 solvents already exist there, whereas the ORCA route
needs one DFT run per solvent. Accuracy is lower; coverage is the point.
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


def load_our_logd(db: Path, contaminant: str) -> list[tuple[str, float]]:
    import duckdb
    con = duckdb.connect(str(db), read_only=True)
    return con.execute(
        "SELECT solvent_raw, logd FROM logd WHERE lower(contaminant_key) LIKE ? ORDER BY logd DESC",
        [f"%{contaminant.lower()}%"],
    ).fetchall()


def resolve(cosmo_dir: Path, name: str) -> Path | None:
    stem = cl.solvent_file_stem(name)
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    index = {norm(p.name[: -len("_c0.cosmo")]): p for p in cosmo_dir.glob("*_c0.cosmo")}
    return index.get(norm(stem)) or index.get(norm(name))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--contaminant", default="diethyl phthalate")
    ap.add_argument("--solute-file", default="diethylphthalate_c0.cosmo")
    ap.add_argument("--cosmo-dir", required=True)
    ap.add_argument("--db", default="src/dissolve/data/contaminants.duckdb")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    cosmo_dir = Path(args.cosmo_dir).expanduser()
    solute = cosmo_dir / args.solute_file
    if not solute.is_file():
        print(f"  no solute file {solute}", file=sys.stderr)
        return 1

    rows = load_our_logd(Path(args.db), args.contaminant)
    print(f"  our table lists {len(rows)} solvents for {args.contaminant}")

    try:
        import numpy as np
        from opencosmorspy import COSMORS, Parameterization
    except ImportError as exc:
        raise cl.CosmoDependencyError(f"opencosmorspy required: {exc}") from exc

    data = []
    for name, ours in rows:
        path = resolve(cosmo_dir, name)
        if path is None:
            print(f"    skip {name}: no .cosmo file")
            continue
        engine = COSMORS(Parameterization("default_turbomole"))
        engine.add_molecule([str(solute)])
        engine.add_molecule([str(path)])
        engine.add_job(x=np.array([1e-5, 1 - 1e-5]), T=cl.STANDARD_T, refst="pure_component")
        lng = float(engine.calculate()["tot"]["lng"][0][0])
        data.append({"solvent": name, "ours": float(ours),
                     "neg_log10_gamma": float(-lng / np.log(10))})

    print(f"  computed {len(data)} of {len(rows)}\n")
    fits = {}
    variants = [("all", set()), ("drop_known_failures", set(cl.KNOWN_METHOD_FAILURES))]
    variants.append(("drop_known_failures_and_water", set(cl.KNOWN_METHOD_FAILURES) | {"water"}))
    for label, excluded in variants:
        subset = [d for d in data if d["solvent"].lower() not in excluded]
        if len(subset) < 3:
            continue
        fit = cl.linear_fit([d["neg_log10_gamma"] for d in subset], [d["ours"] for d in subset])
        low, high = fit.slope_ci95
        fits[label] = {
            "n": int(fit.n), "slope": float(fit.slope), "intercept": float(fit.intercept),
            "r_squared": float(fit.r_squared), "residual_sd": float(fit.residual_sd),
            "slope_ci95": [float(low), float(high)],
            "contains_unit_slope": bool(fit.contains_unit_slope()),
            "excluded": sorted(excluded),
        }
        mark = "includes 1" if fit.contains_unit_slope() else "EXCLUDES 1"
        print(f"  {label:<30} n={fit.n:<3} slope {fit.slope:.3f} [{low:.3f}, {high:.3f}]  "
              f"R2 {fit.r_squared:.3f}  sd {fit.residual_sd:.3f}   {mark}")

    print("\n  Slope 1 is what a single constant reference phase predicts.")
    for name, why in cl.KNOWN_METHOD_FAILURES.items():
        print(f"  excluded {name}: {why}")

    if args.out:
        Path(args.out).expanduser().write_text(json.dumps(
            {"contaminant": args.contaminant, "n_solvents": len(data),
             "rows": data, "fits": fits}, indent=2))
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
