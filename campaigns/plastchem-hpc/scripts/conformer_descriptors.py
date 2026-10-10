"""Exploratory conformer study (owner request 2026-10-10; not a release input): shape descriptors of every converged,
identity-verified conformer and the summary figure of radius of gyration against solvent-accessible surface area.

Per conformer, on the ORCA-optimized geometry (BP86/def2-TZVP(-f), the geometry the COSMO surface is built on):
  - radius of gyration, mass-weighted over all atoms (A);
  - solvent-accessible surface area, Shrake-Rupley via FreeSASA (RDKit rdFreeSASA), van der Waals radii of the RDKit
    periodic table, probe 1.4 A (A^2);
  - energies relative to the compound's lowest conformer (kcal/mol): the optimization's final energy and the COSMO
    (conductor) energy the COSMO-RS conformer weighting uses.

Writes exploratory/conformer-sampling/conformer_descriptors.csv and rg_sasa_summary.png (one panel per compound,
heaviest first; colour = COSMO energy above the compound's lowest conformer; ring = k01, the conformer the release uses).

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_descriptors.py     (RDKit with FreeSASA, matplotlib)"""
import csv
import json
import math
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdFreeSASA

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "state/conformers-v1"
OUT = ROOT / "exploratory/conformer-sampling"
HARTREE_KCAL = 627.5094740631
PT = Chem.GetPeriodicTable()


def shape(xyz_path):
    mol = Chem.MolFromXYZFile(str(xyz_path))
    pos = mol.GetConformer().GetPositions()
    mass = np.array([a.GetMass() for a in mol.GetAtoms()])
    centre = mass @ pos / mass.sum()
    rg = math.sqrt(float(mass @ ((pos - centre) ** 2).sum(axis=1)) / mass.sum())
    radii = [PT.GetRvdw(a.GetAtomicNum()) for a in mol.GetAtoms()]
    opts = rdFreeSASA.SASAOpts()
    opts.probeRadius = 1.4
    sasa = rdFreeSASA.CalcSASA(mol, radii, confIdx=-1, opts=opts)
    return rg, sasa


def short(name, n=30):
    return name if len(name) <= n else name[: n - 1] + "…"


def main():
    selection = json.loads((P / "selection.json").read_text())["compounds"]
    order = {c["inchikey"]: i for i, c in enumerate(selection)}
    rows = []
    for path in sorted((P / "records").glob("*.json")):
        r = json.loads(path.read_text())
        if r.get("status") != "converged":
            continue
        rg, sasa = shape(Path(r["archive_path"]) / "optimized.xyz")
        m = r["input"]
        prep = json.loads((P / "prepared" / m["inchikey"] / "preparation.json").read_text())
        rows.append(dict(parent_inchikey=m["parent_inchikey"], conformer=m["inchikey"], rank=m["conformer_rank"],
                         name=m["name"], role=m["role"], molecular_weight_g_mol=m["molecular_weight_g_mol"],
                         rotatable_bonds=m["rotatable_bonds"], atoms=m["atoms"], radius_of_gyration_A=round(rg, 4),
                         sasa_A2=round(sasa, 2), mmff_delta_kcal=round(prep["mmff_delta_kcal"], 3),
                         opt_energy_hartree=float(r["stages"]["opt"]["final_energies_hartree"][-1]),
                         cosmo_energy_hartree=r["cosmo_solute_energy_hartree"], node=r.get("node"), cpu_model=r.get("cpu_model")))
    by = {}
    for row in rows:
        by.setdefault(row["parent_inchikey"], []).append(row)
    for group in by.values():
        e_opt = min(x["opt_energy_hartree"] for x in group)
        e_cos = min(x["cosmo_energy_hartree"] for x in group)
        for x in group:
            x["opt_delta_kcal"] = round((x["opt_energy_hartree"] - e_opt) * HARTREE_KCAL, 3)
            x["cosmo_delta_kcal"] = round((x["cosmo_energy_hartree"] - e_cos) * HARTREE_KCAL, 3)
    rows.sort(key=lambda x: (order[x["parent_inchikey"]], x["rank"]))
    OUT.mkdir(parents=True, exist_ok=True)
    if rows:
        with (OUT / "conformer_descriptors.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    figure(selection, by)
    print(json.dumps(dict(conformers=len(rows), compounds=len(by))))


def figure(selection, by):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors

    cols = 6
    nrows = math.ceil(len(selection) / cols)
    fig, axes = plt.subplots(nrows, cols, figsize=(cols * 3.1, nrows * 3.0), squeeze=False, layout="constrained")
    norm = colors.Normalize(vmin=0, vmax=6)
    cmap = matplotlib.colormaps["viridis"]
    for ax in axes.flat:
        ax.set_visible(False)
    for ax, compound in zip(axes.flat, selection):
        ax.set_visible(True)
        group = sorted(by.get(compound["inchikey"], []), key=lambda x: x["rank"])
        title = f"{short(compound['name'])}\n{compound['molecular_weight_g_mol']:.0f} g/mol, {compound['rotatable_bonds']} rot. bonds"
        ax.set_title(title, fontsize=7.5, color="#1f1f1f" if compound["role"] == "flexible" else "#7a3b00")
        ax.tick_params(labelsize=6.5)
        if not group:
            ax.text(0.5, 0.5, "no converged conformer yet", ha="center", va="center", fontsize=7, transform=ax.transAxes, color="#999999")
            ax.set_xticks([])
            ax.set_yticks([])
            for side in ax.spines.values():
                side.set_color("#cccccc")
            continue
        x = [g["radius_of_gyration_A"] for g in group]
        y = [g["sasa_A2"] for g in group]
        c = [min(g["cosmo_delta_kcal"], 6) for g in group]
        ax.scatter(x, y, c=c, cmap=cmap, norm=norm, s=26, edgecolors="none", zorder=2)
        k01 = [g for g in group if g["rank"] == 1]
        if k01:
            ax.scatter([k01[0]["radius_of_gyration_A"]], [k01[0]["sasa_A2"]], s=70, facecolors="none", edgecolors="#d62728", linewidths=1.2, zorder=3)
        dx = max(max(x) - min(x), 0.05 * max(x))
        dy = max(max(y) - min(y), 0.05 * max(y))
        ax.set_xlim(min(x) - 0.25 * dx, max(x) + 0.25 * dx)
        ax.set_ylim(min(y) - 0.25 * dy, max(y) + 0.25 * dy)
        ax.text(0.97, 0.04, f"n={len(group)}" + (" rigid" if compound["role"] == "rigid" else ""), ha="right", va="bottom",
                fontsize=6.5, transform=ax.transAxes, color="#555555")
    fig.supxlabel("radius of gyration (Å)", fontsize=9)
    fig.supylabel("solvent-accessible surface area (Å²)", fontsize=9)
    fig.suptitle("Conformers of 31 high-MW contaminants: radius of gyration vs solvent-accessible surface area\n"
                 "ORCA BP86/def2-TZVP(-f) geometries; red ring = k01, the conformer the release uses; brown title = rigid pick",
                 fontsize=10)
    cbar = fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=axes, shrink=0.3, pad=0.01, aspect=30)
    cbar.set_label("COSMO energy above the lowest conformer (kcal/mol, capped at 6)", fontsize=8)
    fig.savefig(OUT / "rg_sasa_summary.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
