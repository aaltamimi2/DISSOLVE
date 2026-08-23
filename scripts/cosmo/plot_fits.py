#!/usr/bin/env python3
"""Two-panel figure from regress_all_solvents.py output.

    PYTHONPATH=src python3 scripts/cosmo/plot_fits.py --fits fits.json --out fits.png

Left: every solvent with each fit line. Right: one point per fit with its 95%
interval against slope 1. Panels use DIFFERENT palettes on purpose -- an
earlier version of this figure reused one palette for two different meanings
and a reader took a span bucket for a strategy.
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

TXT = 13
COLOURS = {"all": "#1b3a6b", "drop_known_failures": "#c2571a",
           "drop_known_failures_and_water": "#2e7d32"}
LABELS = {"all": "all solvents", "drop_known_failures": "drop chloroform",
          "drop_known_failures_and_water": "drop chloroform\n+ water"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fits", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    payload = json.loads(Path(args.fits).expanduser().read_text())
    rows, fits = payload["rows"], payload["fits"]
    names = [r["solvent"] for r in rows]
    ours = np.array([r["ours"] for r in rows])
    pred = np.array([r["neg_log10_gamma"] for r in rows])

    fig, (axl, axr) = plt.subplots(1, 2, figsize=(15.0, 6.6), gridspec_kw={"width_ratios": [1.75, 1]})
    xs = np.linspace(pred.min() - 0.4, pred.max() + 0.4, 50)
    special = set(cl.KNOWN_METHOD_FAILURES) | {"water"}
    plain = [i for i, n in enumerate(names) if n.lower() not in special]
    axl.scatter(pred[plain], ours[plain], s=62, facecolor="#8fa8c8",
                edgecolor="black", lw=0.8, zorder=3, label=f"{len(plain)} solvents")
    for marker, size, name in (("s", 110, "water"), ("D", 150, "chloroform")):
        idx = [i for i, n in enumerate(names) if n.lower() == name]
        if idx:
            axl.scatter(pred[idx], ours[idx], s=size, marker=marker,
                        facecolor="#7bbf8e" if name == "water" else "#e07b39",
                        edgecolor="black", lw=1.3, zorder=5, label=name)
    for key, fit in fits.items():
        axl.plot(xs, fit["slope"] * xs + fit["intercept"], "-", lw=2.4,
                 color=COLOURS.get(key, "#555"), zorder=2,
                 label=f"{LABELS.get(key, key).replace(chr(10), ' ')}: slope {fit['slope']:.3f}")
    ref = next(iter(fits.values()))
    axl.plot(xs, xs + ref["intercept"], "--", color="black", lw=1.5, zorder=1,
             label="slope = 1 (constant reference)")
    axl.set_xlabel(r"COSMO-RS prediction,  $-\log_{10}\gamma^{\infty}$", fontsize=TXT, color="black")
    axl.set_ylabel("our table,  logD", fontsize=TXT, color="black")
    axl.set_title(f"{payload['contaminant']} in {payload['n_solvents']} solvents from our table",
                  fontsize=TXT + 1, color="black")
    axl.legend(loc="upper left", fontsize=TXT - 2, framealpha=0.95)
    axl.grid(alpha=0.3); axl.tick_params(labelsize=TXT - 1, colors="black")

    keys = list(fits)
    ypos = list(range(len(keys)))[::-1]
    for key, y in zip(keys, ypos):
        fit = fits[key]
        low, high = fit["slope_ci95"]
        axr.errorbar(fit["slope"], y, xerr=[[fit["slope"] - low], [high - fit["slope"]]],
                     fmt="o", ms=15, color=COLOURS.get(key, "#555"),
                     ecolor=COLOURS.get(key, "#555"), elinewidth=2.6, capsize=8, capthick=2.6, zorder=3)
        axr.text(fit["slope"], y + 0.22, f"{fit['slope']:.3f}", ha="center", fontsize=TXT, color="black")
        axr.text(0.625, y - 0.26, f"n={fit['n']}    R²={fit['r_squared']:.3f}    "
                 f"resid sd={fit['residual_sd']:.3f}", ha="left", va="center", fontsize=TXT - 1, color="black")
    axr.axvline(1.0, color="black", ls="--", lw=1.8, zorder=1)
    axr.text(1.015, -0.34, "slope = 1", ha="left", va="center", fontsize=TXT, color="black")
    axr.set_yticks(ypos); axr.set_yticklabels([LABELS.get(k, k) for k in keys], fontsize=TXT, color="black")
    axr.set_xlabel("fitted slope   (bars = 95% CI)", fontsize=TXT, color="black")
    axr.set_title("One point per fit", fontsize=TXT + 1, color="black")
    axr.set_xlim(0.60, 1.16); axr.set_ylim(-0.52, len(keys) - 0.42)
    axr.grid(axis="x", alpha=0.3); axr.tick_params(labelsize=TXT - 1, colors="black")
    for side in ("top", "right"):
        axr.spines[side].set_visible(False)

    fig.tight_layout()
    fig.savefig(Path(args.out).expanduser(), dpi=180, facecolor="white")
    print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
