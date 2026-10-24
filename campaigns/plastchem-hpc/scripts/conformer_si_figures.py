"""Exploratory conformer study on CHTC (not a release input): the SI figures that show what the conformers look like.

Representative compounds are chosen by a fixed rule, not by eye. Within each class (flexible amide, perfluoroalkyl
without ester, perfluoroalkyl ester), among the compounds whose 10-conformer ensemble is complete, sorted by the release's
worst deviation from the ensemble over the 320 solvent/polymer pairs, the compounds at the 10th, 50th and 90th
percentile (nearest rank) are taken.

  si_rg_sasa_representative.png  one panel per representative compound (3 classes x 3 percentiles): radius of gyration
                                 against solvent-accessible surface area of its 10 conformers; colour = Boltzmann
                                 weight in water (the ensemble of conformer_ablation.py), ring = k01 (the release's
                                 conformer), star = the dominant conformer in water.
  si_conformer_structures.png    optimized geometries (BP86/def2-TZVP(-f)), drawn on their own principal axes at one
                                 scale per row. Rows 1-3: the 50th-percentile compound of each class, showing k01, the
                                 dominant conformer in water, and the most compact and most extended conformer (smallest
                                 and largest radius of gyration; a conformer already shown is not repeated). Row 4: the
                                 perfluoroalkyl compound of the 30-conformer test whose shape-stratified conformers carry
                                 the most weight in water, showing k01 and its lowest-COSMO-energy stratified conformer.

Reads exploratory/conformer-htc/{summary_by_compound,single_conformer,conformer_descriptors,conf30_by_compound}.csv
(conformer_ablation.py, conformer_descriptors.py, conformer_30_analysis.py) and the returned geometries. Nothing stands
in for a missing conformer: a compound without a complete ensemble is never chosen. Every output is rewritten.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_si_figures.py"""
import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
from rdkit import Chem

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from conformer_descriptors import shape  # noqa: E402

OUT = ROOT / "exploratory/conformer-htc"
RETURNED = ROOT / "state/conformers-htc-v1/returned"
STUDY30, SHAPE = ROOT / "state/conformers-htc-30-v1", ROOT / "state/conformers-htc-shape-v1"
CLASSES = ("flexible amide", "perfluoroalkyl, no ester", "perfluoroalkyl ester")
PERCENTILES = (10, 50, 90)
HARTREE_KCAL = 627.5094740631
CPK = {"H": "#e8e8e8", "C": "#404040", "N": "#3050f8", "O": "#ff0d0d", "F": "#90e050", "S": "#e0c030", "P": "#ff8000",
       "Cl": "#1ff01f", "Br": "#a62929", "Si": "#daa520", "I": "#940094"}
SIZE = {"H": 22, "F": 60, "I": 110}
OUTPUTS = ("si_rg_sasa_representative.png", "si_conformer_structures.png", "si_representatives.json")


def table(name):
    with (OUT / name).open() as handle:
        return list(csv.DictReader(handle))


def short(name, n=34):
    return name if len(name) <= n else name[: n - 1] + "…"


def representatives(summary):
    picks = []
    for role in CLASSES:
        rows = sorted((r for r in summary if r["role"] == role and r["ensemble_complete"] == "True"),
                      key=lambda r: float(r["release_vs_ensemble_max_abs_dlogP"]))
        if not rows:
            raise SystemExit(f"no complete ensemble in class {role!r}")
        for q in PERCENTILES:
            r = rows[max(0, math.ceil(q / 100 * len(rows)) - 1)]
            picks.append(dict(role=role, percentile=q, of=len(rows), parent=r["parent_inchikey"], name=r["name"],
                              release_worst=float(r["release_vs_ensemble_max_abs_dlogP"]),
                              release_median=float(r["release_vs_ensemble_median_abs_dlogP"])))
    return picks


def geometry(key):
    xyz = RETURNED / key / "optimized.xyz"
    lines = xyz.read_text().splitlines()
    n = int(lines[0])
    atoms = [l.split() for l in lines[2:2 + n]]
    return [a[0].capitalize() for a in atoms], np.array([[float(x) for x in a[1:4]] for a in atoms]), xyz


def bonds(smiles):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    return [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]


def principal(pos):
    c = pos - pos.mean(axis=0)
    _, vec = np.linalg.eigh(c.T @ c)
    p = c @ vec[:, ::-1]  # largest extent along x, then y; z is depth
    return p


def draw(ax, elements, pos, bond_list, hx, hy):
    import matplotlib.patches as mp
    p = principal(pos)
    for i, j in sorted(bond_list, key=lambda b: (p[b[0], 2] + p[b[1], 2])):
        ax.plot(p[[i, j], 0], p[[i, j], 1], color="#777777", lw=1.0, zorder=1 + (p[i, 2] + p[j, 2]) / 100)
    for i in np.argsort(p[:, 2]):
        e = elements[i]
        ax.scatter(p[i, 0], p[i, 1], s=SIZE.get(e, 50), color=CPK.get(e, "#ff69b4"), edgecolors="#222222",
                   linewidths=0.3, zorder=2 + p[i, 2] / 100)
    ax.set_xlim(-hx, hx)
    ax.set_ylim(-hy, hy)
    ax.set_aspect("equal")
    ax.axis("off")


def main():
    for name in OUTPUTS:
        (OUT / name).unlink(missing_ok=True)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    summary = table("summary_by_compound.csv")
    single = {r["conformer"]: r for r in table("single_conformer.csv")}
    desc = table("conformer_descriptors.csv")
    picks = representatives(summary)

    # Figure 1: Rg against SASA for the nine representatives
    fig, axes = plt.subplots(3, 3, figsize=(13, 10.5), layout="constrained")
    scatter = None
    for ax, pick in zip(axes.T.ravel(), picks):
        rows = [r for r in desc if r["parent_inchikey"] == pick["parent"]]
        assert len(rows) == 10, (pick["parent"], len(rows))
        w = np.array([float(single[r["conformer"]]["weight_in_water"]) for r in rows])
        rg = np.array([float(r["radius_of_gyration_A"]) for r in rows])
        sa = np.array([float(r["sasa_A2"]) for r in rows])
        rank = [int(r["rank"]) for r in rows]
        scatter = ax.scatter(sa, rg, c=w, cmap="viridis", vmin=0, vmax=1, s=46, edgecolors="#333333", linewidths=0.4, zorder=3)
        k1 = rank.index(1)
        ax.scatter(sa[k1], rg[k1], s=170, facecolors="none", edgecolors="#d62728", linewidths=1.4, zorder=4)
        d = int(np.argmax(w))
        ax.scatter(sa[d], rg[d], marker="s", s=150, facecolors="none", edgecolors="#ff7f0e", linewidths=1.6, zorder=5)
        ax.annotate(f"w={w[d]:.2f}", (sa[d], rg[d]), xytext=(5, -10), textcoords="offset points", fontsize=7, color="#b35900")
        for x, y, k in zip(sa, rg, rank):
            ax.annotate(str(k), (x, y), xytext=(4, 3), textcoords="offset points", fontsize=6.5, color="#444444")
        ax.set_title(f"{short(pick['name'], 30)}\nP{pick['percentile']} of {pick['of']}; release worst |ΔlogP| "
                     f"{pick['release_worst']:.2f}", fontsize=8)
        ax.set_xlabel("SASA (Å²)", fontsize=8)
        ax.set_ylabel("radius of gyration (Å)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.3)
    for ax, role in zip(axes[0], CLASSES):
        ax.annotate(role, (0.5, 1.2), xycoords="axes fraction", ha="center", fontsize=10, fontweight="bold")
    fig.colorbar(scatter, ax=axes, shrink=0.6, label="Boltzmann weight in water (10-conformer ensemble)")
    fig.suptitle("Conformer shape for representative compounds (10th, 50th and 90th percentile of the release error in "
                 "each class)\nring = k01, the conformer the release uses; orange square = dominant conformer in water (w = its weight); numbers = "
                 "MMFF energy rank", fontsize=9)
    fig.savefig(OUT / "si_rg_sasa_representative.png", dpi=200)
    plt.close(fig)

    # Figure 2: structures
    rows_out = []
    for pick in (p for p in picks if p["percentile"] == 50):
        rows = [r for r in desc if r["parent_inchikey"] == pick["parent"]]
        by_key = {r["conformer"]: r for r in rows}
        w = {k: float(single[k]["weight_in_water"]) for k in by_key}
        k01 = next(k for k, r in by_key.items() if r["rank"] == "1")
        chosen = [("k01 (release)", k01), ("dominant in water", max(w, key=w.get)),
                  ("most compact", min(by_key, key=lambda k: float(by_key[k]["radius_of_gyration_A"]))),
                  ("most extended", max(by_key, key=lambda k: float(by_key[k]["radius_of_gyration_A"])))]
        seen, panels = set(), []
        for label, key in chosen:
            if key in seen:
                panels = [(l + (" = " + label if k == key else ""), k, t) for l, k, t in panels]
                continue
            seen.add(key)
            r = by_key[key]
            panels.append((label, key, f"k{int(r['rank']):02d}: Rg {float(r['radius_of_gyration_A']):.2f} Å, "
                                       f"SASA {float(r['sasa_A2']):.0f} Å²\nΔE_COSMO {float(r['cosmo_delta_kcal']):.1f} "
                                       f"kcal/mol, weight {w[key]:.2f}"))
        smiles = json.loads((STUDY30.parent / "conformers-htc-v1/prepared" / k01 / "preparation.json").read_text())["input"]["smiles"]
        rows_out.append((f"{short(pick['name'], 50)} ({pick['role']}, median release error {pick['release_worst']:.2f})",
                         smiles, panels))

    conf30 = [r for r in table("conf30_by_compound.csv") if r["role"].startswith("perfluoro") and r.get("weight_in_water_of_shape_conformers")]
    if not conf30:
        raise SystemExit("no perfluoroalkyl compound of the 30-conformer test has its shape conformers")
    c = max(conf30, key=lambda r: float(r["weight_in_water_of_shape_conformers"]))
    parent = c["parent_inchikey"]
    energies = {}
    for folder in (STUDY30, SHAPE):
        for path in (folder / "records").glob(parent + "-k*.json"):
            r = json.loads(path.read_text())
            if r.get("status") == "converged":
                energies[r["inchikey"]] = r["cosmo_solute_energy_hartree"]
    shape_keys = [k for k in energies if int(k[-2:]) >= 41]
    best_shape = min(shape_keys, key=energies.get)
    e0 = min(energies.values())
    k01 = parent + "-k01"
    panels = []
    for label, key in (("k01 (release)", k01), ("lowest-COSMO-energy stratified", best_shape)):
        rg, sasa = shape(RETURNED / key / "optimized.xyz")
        panels.append((label, key, f"k{key[-2:]}: Rg {rg:.2f} Å, SASA {sasa:.0f} Å²\n"
                                   f"ΔE_COSMO {(energies[key] - e0) * HARTREE_KCAL:.1f} kcal/mol (of 30 + stratified)"))
    smiles = json.loads((STUDY30 / "prepared" / k01 / "preparation.json").read_text())["input"]["smiles"]
    rows_out.append((f"{short(c['name'], 50)} (30-conformer test; stratified conformers carry "
                     f"{float(c['weight_in_water_of_shape_conformers']):.0%} of the weight in water)", smiles, panels))

    ncol, nrow = max(len(p) for _, _, p in rows_out), len(rows_out)
    width, row_h = 4.3 * ncol, 3.6
    fig = plt.figure(figsize=(width, row_h * nrow + 0.5))
    band = 1.0 / (nrow + 0.5 / row_h)  # figure-fraction height of one row
    for r, (title, smiles, panels) in enumerate(rows_out):
        top = 1 - r * band
        fig.text(0.01, top - 0.06 * band, title, fontsize=11, fontweight="bold", va="top")
        bl = bonds(smiles)
        geoms = [geometry(key) for _, key, _ in panels]
        proj = [principal(pos) for _, pos, _ in geoms]
        hx = max(np.abs(q[:, 0]).max() for q in proj) + 1.0
        hy = max(np.abs(q[:, 1]).max() for q in proj) + 1.0
        for col, ((label, key, text), (elements, pos, _)) in enumerate(zip(panels, geoms)):
            ax = fig.add_axes([0.01 + col / ncol, top - band * 0.97, 1 / ncol - 0.02, band * 0.62])
            draw(ax, elements, pos, bl, hx, hy)
            ax.set_title(f"{label}\n{text}", fontsize=8.5)
    present = {e for _, _, p in rows_out for _, k, _ in p for e in geometry(k)[0]}
    handles = [plt.Line2D([], [], marker="o", ls="", markersize=7, markerfacecolor=CPK[e], markeredgecolor="#222222", label=e)
               for e in CPK if e in present]
    fig.legend(handles=handles, loc="lower right", fontsize=9, frameon=False, ncol=len(handles))
    fig.savefig(OUT / "si_conformer_structures.png", dpi=200)
    plt.close(fig)

    (OUT / "si_representatives.json").write_text(json.dumps(dict(
        rule="per class, complete 10-conformer ensembles sorted by the release's worst |dlogP| over 320 pairs; nearest-rank 10th/50th/90th percentile",
        picks=picks, structures=[dict(title=t, conformers=[dict(label=l, key=k) for l, k, _ in p]) for t, _, p in rows_out],
        thirty_conformer_example=dict(parent_inchikey=parent, name=c["name"], rule="perfluoroalkyl compound of the 30-conformer test with the largest weight in water on its shape-stratified conformers")),
        indent=1) + "\n")
    print(json.dumps(dict(picks=[(p["role"], p["percentile"], p["name"][:40], p["release_worst"]) for p in picks],
                          thirty=c["name"]), indent=1))


if __name__ == "__main__":
    main()
