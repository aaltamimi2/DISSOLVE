"""Exploratory conformer study on CHTC (owner request 2026-10-12; not a release input): what the 10-conformer ensembles
of the flexible amides and perfluoroalkyl compounds say about the single-conformer release, by class.

Reads the tables conformer_ablation.py wrote for this study (exploratory/conformer-htc: summary_by_compound.csv,
ablation_by_k.csv, single_conformer.csv). Only compounds whose 10 conformers all converged and passed the identity
check are counted; the others are listed as incomplete and nothing stands in for a missing conformer. The reference is
the 10-conformer ensemble itself, which is not shown to be converged: the change from 9 to 10 conformers is reported
next to every error so that this can be judged.

Writes exploratory/conformer-htc/: class_summary.csv, class_summary.json, release_error_by_class.png,
convergence_by_class.png, dominant_conformer_rank.png. Every output is rewritten on every run.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_htc_summary.py"""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "exploratory/conformer-htc"
SELECTED = 236
COLORS = {"flexible amide": "#1f77b4", "perfluoroalkyl, no ester": "#d62728", "perfluoroalkyl ester": "#2ca02c"}
OUTPUTS = ("class_summary.csv", "class_summary.json", "release_error_by_class.png", "convergence_by_class.png", "dominant_conformer_rank.png")


def table(name):
    return list(csv.DictReader((OUT / name).open()))


def stats(values):
    v = np.asarray(values, float)
    return dict(median=round(float(np.median(v)), 3), p90=round(float(np.percentile(v, 90)), 3), max=round(float(v.max()), 3),
                over_0_3=int((v > 0.3).sum()), over_1=int((v > 1.0).sum()))


def main():
    for name in OUTPUTS:
        (OUT / name).unlink(missing_ok=True)
    summary = table("summary_by_compound.csv")
    complete = [r for r in summary if r["ensemble_complete"] == "True"]
    if not complete:
        raise SystemExit("no compound has all 10 conformers yet")
    role = {r["parent_inchikey"]: r["role"] for r in summary}
    by_k = defaultdict(dict)
    for r in table("ablation_by_k.csv"):
        by_k[r["parent_inchikey"]][int(r["k"])] = (float(r["median_abs_dlogP"]), float(r["max_abs_dlogP"]))
    dominant = {}
    for r in table("single_conformer.csv"):
        p = r["parent_inchikey"]
        if p in by_k and (p not in dominant or float(r["weight_in_water"]) > dominant[p][1]):
            dominant[p] = (int(r["rank"]), float(r["weight_in_water"]))
    rows, report = [], dict(selected=SELECTED, complete=len(complete), incomplete_or_not_returned=SELECTED - len(complete), classes={})
    for name in [*COLORS, "all"]:
        group = [r for r in complete if name == "all" or r["role"] == name]
        if not group:
            continue
        keys = [r["parent_inchikey"] for r in group]
        worst = stats([r["release_vs_ensemble_max_abs_dlogP"] for r in group])
        typical = stats([r["release_vs_ensemble_median_abs_dlogP"] for r in group])
        last = stats([by_k[k][9][1] for k in keys])
        first_within = {}
        for limit in (0.1, 0.3):
            needed = [next(k for k in range(1, 11) if all(by_k[key][j][1] <= limit for j in range(k, 11))) for key in keys]
            first_within[limit] = Counter(needed)
        ranks = Counter(dominant[k][0] for k in keys)
        entry = dict(compounds=len(group), release_error_worst_pair=worst, release_error_median_pair=typical,
                     change_from_9_to_10_worst_pair=last, dominant_conformer_is_release=ranks.get(1, 0),
                     dominant_rank_counts={str(k): ranks[k] for k in sorted(ranks)},
                     conformers_needed_within_0_1={str(k): v for k, v in sorted(first_within[0.1].items())},
                     conformers_needed_within_0_3={str(k): v for k, v in sorted(first_within[0.3].items())})
        report["classes"][name] = entry
        rows.append(dict(role=name, compounds=len(group), worst_pair_median=worst["median"], worst_pair_p90=worst["p90"],
                         worst_pair_max=worst["max"], worst_pair_over_0_3=worst["over_0_3"], worst_pair_over_1=worst["over_1"],
                         median_pair_median=typical["median"], median_pair_p90=typical["p90"], median_pair_max=typical["max"],
                         change_9_to_10_median=last["median"], change_9_to_10_p90=last["p90"], change_9_to_10_max=last["max"],
                         dominant_is_release=ranks.get(1, 0)))
    with (OUT / "class_summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (OUT / "class_summary.json").write_text(json.dumps(report, indent=1) + "\n")
    figures(complete, by_k, dominant, role)
    print(json.dumps(report, indent=1))


def figures(complete, by_k, dominant, role):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    present = [name for name in COLORS if any(r["role"] == name for r in complete)]
    note = f"{len(complete)} of {SELECTED} selected compounds (those with all 10 conformers); reference = the 10-conformer ensemble"

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), layout="constrained", sharey=True)
    for ax, column, title in zip(axes, ("release_vs_ensemble_median_abs_dlogP", "release_vs_ensemble_max_abs_dlogP"),
                                 ("median over the 320 solvent/polymer pairs", "worst solvent/polymer pair")):
        for name in present:
            v = np.sort([float(r[column]) for r in complete if r["role"] == name])
            ax.step(v, 1 - np.arange(len(v)) / len(v), where="post", color=COLORS[name], lw=1.6, label=f"{name} (n={len(v)})")
        for limit in (0.3, 1.0):
            ax.axvline(limit, color="#999999", lw=0.8, ls=":")
        ax.set_xscale("symlog", linthresh=0.01)
        ax.set_xlim(left=0)
        ax.set_xlabel("|logP(release, 1 conformer) - logP(10 conformers)|")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("fraction of compounds with at least this error")
    axes[0].legend(fontsize=8, frameon=False)
    fig.suptitle("Error of the single-conformer release against the 10-conformer ensemble\n" + note, fontsize=10)
    fig.savefig(OUT / "release_error_by_class.png", dpi=150)
    plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), layout="constrained", sharey=True)
    k = np.arange(1, 10)
    for ax, j, title in zip(axes, (0, 1), ("median over the 320 solvent/polymer pairs", "worst solvent/polymer pair")):
        for name in present:
            keys = [r["parent_inchikey"] for r in complete if r["role"] == name]
            curves = np.array([[by_k[key][i][j] for i in k] for key in keys])
            ax.plot(k, np.median(curves, axis=0), color=COLORS[name], lw=1.8, marker="o", ms=3.5, label=f"{name}: median compound")
            ax.plot(k, np.percentile(curves, 90, axis=0), color=COLORS[name], lw=1.1, ls="--", label=f"{name}: 90th percentile")
        ax.set_yscale("symlog", linthresh=0.01)
        ax.set_xlabel("conformers in the ensemble (MMFF order; 1 = the release)")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3)
    axes[0].set_ylabel("|logP(k conformers) - logP(10 conformers)|")
    axes[0].legend(fontsize=7, frameon=False)
    fig.suptitle("How the partition coefficient approaches the 10-conformer value as conformers are added\n" + note, fontsize=10)
    fig.savefig(OUT / "convergence_by_class.png", dpi=150)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(6.8, 4.2), layout="constrained")
    width = 0.8 / len(present)
    for i, name in enumerate(present):
        ranks = Counter(dominant[r["parent_inchikey"]][0] for r in complete if r["role"] == name)
        total = sum(ranks.values())
        ax.bar(np.arange(1, 11) + (i - (len(present) - 1) / 2) * width, [ranks.get(n, 0) / total for n in range(1, 11)], width,
               color=COLORS[name], label=f"{name} (n={total})")
    ax.set_xticks(range(1, 11))
    ax.set_xlabel("MMFF rank of the conformer with the largest weight in water (1 = the release)")
    ax.set_ylabel("fraction of compounds")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.3, axis="y")
    ax.set_title("Which conformer dominates after DFT and COSMO-RS\n" + note, fontsize=9)
    fig.savefig(OUT / "dominant_conformer_rank.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    main()
