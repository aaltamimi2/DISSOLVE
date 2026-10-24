"""Exploratory conformer studies (not a release input): one figure with a convergence curve for every contaminant whose
conformer ensemble is complete, from both studies: the 31-compound study on Euler (exploratory/conformer-ablation) and
the flexible amides and perfluoroalkyl compounds on CHTC (exploratory/conformer-htc). Each line is one compound:
|logP(first k conformers) - logP(all its conformers)|, median over the 320 solvent/polymer pairs, read from the two
ablation_by_k.csv tables (conformer_ablation.py). A compound with a single kept conformer has no curve and is counted
in the caption; a compound in both studies is drawn once, from the CHTC study.

Writes exploratory/conformer-htc/convergence_all_compounds.png (rewritten on every run).

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_convergence_all.py"""
import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exploratory/conformer-htc/convergence_all_compounds.png"
COLORS = {"flexible amide": "#1f77b4", "perfluoroalkyl, no ester": "#d62728", "perfluoroalkyl ester": "#2ca02c",
          "31-compound study (high molecular weight)": "#7f7f7f"}


def curves(folder, role_of):
    roles = {r["parent_inchikey"]: role_of(r) for r in csv.DictReader((ROOT / "exploratory" / folder / "summary_by_compound.csv").open())}
    by = defaultdict(dict)
    for r in csv.DictReader((ROOT / "exploratory" / folder / "ablation_by_k.csv").open()):
        by[r["parent_inchikey"]][int(r["k"])] = float(r["median_abs_dlogP"])
    return {p: (roles[p], [c[k] for k in sorted(c)]) for p, c in by.items()}


def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    OUT.unlink(missing_ok=True)
    data = curves("conformer-ablation", lambda r: "31-compound study (high molecular weight)")
    data.update(curves("conformer-htc", lambda r: r["role"]))
    single = sum(1 for _, c in data.values() if len(c) < 2)
    fig, ax = plt.subplots(figsize=(9, 5.6), layout="constrained")
    for name, color in COLORS.items():
        group = [c for role, c in data.values() if role == name and len(c) >= 2]
        for c in group:
            ax.plot(range(1, len(c)), c[:-1], color=color, lw=0.7, alpha=0.35)  # the last point is 0 by definition
        full = np.array([c for c in group if len(c) == 10])
        if len(full):
            ax.plot(range(1, 10), np.median(full, axis=0)[:-1], color=color, lw=2.6, marker="o", ms=4,
                    label=f"{name}: {len(group)} compounds (thick = median)")
    ax.set_yscale("symlog", linthresh=0.001)
    ax.set_ylim(bottom=0)
    ax.set_xticks(range(1, 10))
    ax.set_xlabel("conformers in the ensemble (MMFF order; 1 = the release)")
    ax.set_ylabel("|logP(k conformers) - logP(all conformers)|\nmedian over the 320 solvent/polymer pairs")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, frameon=False, loc="upper right")
    drawn = sum(1 for _, c in data.values() if len(c) >= 2)
    ax.set_title(f"Convergence with the number of conformers: one line per contaminant ({drawn} with a complete ensemble)\n"
                 f"reference = the compound's own full ensemble (10 conformers, fewer where fewer exist)\n"
                 f"{single} compounds with a single conformer have no curve", fontsize=9.5)
    fig.savefig(OUT, dpi=160)
    print(dict(drawn=drawn, single_conformer=single, total=len(data)))


if __name__ == "__main__":
    main()
