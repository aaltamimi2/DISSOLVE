"""Exploratory conformer studies (not a release input): one figure with every conformer of every contaminant studied,
from both studies (exploratory/conformer-ablation and exploratory/conformer-htc, single_conformer.csv of
conformer_ablation.py). Absolute radius of gyration and SASA mostly sort the compounds by size, so each conformer is
placed relative to its own compound's release conformer (k01): the change in radius of gyration and in
solvent-accessible surface area, in percent. The colour is the shift of that single conformer's partition coefficient
against k01 (median over the 320 solvent/polymer pairs), the marker is the class. A compound whose k01 has not
returned is left out and counted; k01 itself sits at the origin and is not drawn.

Writes exploratory/conformer-htc/shape_all_conformers.png (rewritten on every run).

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_shape_all.py"""
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exploratory/conformer-htc/shape_all_conformers.png"
MARKERS = {"flexible amide": "o", "perfluoroalkyl, no ester": "s", "perfluoroalkyl ester": "^", "31-compound study (high molecular weight)": "D"}
CLIP = 1.0


def load(folder, role_of):
    roles = {r["parent_inchikey"]: role_of(r) for r in csv.DictReader((ROOT / "exploratory" / folder / "summary_by_compound.csv").open())}
    by = defaultdict(list)
    for r in csv.DictReader((ROOT / "exploratory" / folder / "single_conformer.csv").open()):
        by[r["parent_inchikey"]].append(r)
    return {p: (roles[p], rows) for p, rows in by.items()}


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.unlink(missing_ok=True)
    data = load("conformer-ablation", lambda r: "31-compound study (high molecular weight)")
    data.update(load("conformer-htc", lambda r: r["role"]))
    points, no_k01, compounds = defaultdict(list), 0, 0
    for role, rows in data.values():
        first = [r for r in rows if r["rank"] == "1"]
        if not first:
            no_k01 += 1
            continue
        others = [r for r in rows if r["rank"] != "1"]
        compounds += bool(others)
        rg0, sasa0 = float(first[0]["radius_of_gyration_A"]), float(first[0]["sasa_A2"])
        for r in others:
            points[role].append((100 * (float(r["radius_of_gyration_A"]) / rg0 - 1), 100 * (float(r["sasa_A2"]) / sasa0 - 1),
                                 float(r["median_dlogP_vs_first"])))
    fig, ax = plt.subplots(figsize=(9, 6.6), layout="constrained")
    norm = matplotlib.colors.TwoSlopeNorm(vmin=-CLIP, vcenter=0, vmax=CLIP)
    total = 0
    for role, marker in MARKERS.items():
        p = np.array(points.get(role, []))
        if not len(p):
            continue
        order = np.argsort(np.abs(p[:, 2]))  # the large shifts on top
        sc = ax.scatter(p[order, 0], p[order, 1], c=np.clip(p[order, 2], -CLIP, CLIP), cmap="coolwarm", norm=norm, marker=marker, s=22,
                        edgecolors="#444444", linewidths=0.25, alpha=0.85, label=f"{role} ({len(p)})")
        total += len(p)
    ax.axhline(0, color="#888888", lw=0.7)
    ax.axvline(0, color="#888888", lw=0.7)
    ax.set_xlabel("radius of gyration relative to the release conformer (%)   ← more compact | more extended →")
    ax.set_ylabel("solvent-accessible surface area relative to the release conformer (%)")
    ax.grid(alpha=0.3)
    legend = ax.legend(fontsize=8, frameon=False, loc="upper left")
    for handle in legend.legend_handles:
        handle.set_color("#888888")
    fig.colorbar(sc, ax=ax, shrink=0.8, label=f"logP shift of this conformer alone vs the release conformer\n(median over 320 pairs, clipped at ±{CLIP:g})")
    ax.set_title(f"Shape of every conformer against its compound's release conformer: {total} conformers of {compounds} compounds\n"
                 f"release conformer (k01) = the origin" + (f"; {no_k01} compounds without a returned k01 left out" if no_k01 else ""), fontsize=9.5)
    fig.savefig(OUT, dpi=160)
    print(dict(conformers=total, compounds=compounds, without_k01=no_k01))


if __name__ == "__main__":
    main()
