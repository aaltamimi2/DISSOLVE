"""Exploratory conformer study on CHTC (owner request 2026-10-13; not a release input): does going from 10 to 30
conformers, or adding shape-stratified conformers, change the partition coefficients of the 18 compounds that were not
settled at 10?

Reads the thermodynamics of state/conformers-htc-30-v1 (conformers 1-30, MMFF order) and of
state/conformers-htc-shape-v1 (conformers 41-50: the lowest-MMFF-energy conformer of each SASA bin of the pool that
conformers 1-30 did not already hold). The ensemble is the Boltzmann ensemble of conformer_ablation.py (COSMO energy
plus the conductor-reference chemical potential in each phase), logP over the 320 solvent/polymer pairs, "normalized"
convention. A compound is analysed only when all 30 conformers are converged, identity-verified and have
thermodynamics; the shape columns are filled only when all of its stratified conformers are too. Anything else is
listed as not yet, and nothing stands in for a missing conformer.

Writes exploratory/conformer-htc/: conf30_by_compound.csv, conf30_by_k.csv, conf30_summary.json, convergence_30.png.
Every output is rewritten on every run.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_30_analysis.py"""
import csv
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
P30 = ROOT / "state/conformers-htc-30-v1"
PSH = ROOT / "state/conformers-htc-shape-v1"
OUT = ROOT / "exploratory/conformer-htc"
SPEC = Path("/mnt/r/plastchem-euler/coverage-thermo-v1/t12/inputs.json")
RT_KCAL = 0.0019872041 * 298.15
HARTREE_KCAL = 627.5094740631
CONVENTION = "normalized"
OUTPUTS = ("conf30_by_compound.csv", "conf30_by_k.csv", "conf30_summary.json", "convergence_30.png")


def lse(a):
    a = np.asarray(a, float)
    m = a.max()
    return m + math.log(np.exp(a - m).sum())


def thermo(folder, parent):
    return {t["rank"]: t for t in (json.loads(p.read_text()) for p in sorted((folder / "thermo").glob(parent + "-k*.json")))}


def main():
    for name in OUTPUTS:
        (OUT / name).unlink(missing_ok=True)
    spec = json.loads(SPEC.read_text())
    solvents = list(spec["solvents"])
    vs = {s: spec["solvents"][s][CONVENTION + "_volume_cm3_mol"] for s in solvents}
    rows, rows_k, curves, not_yet = [], [], {}, []
    for c in json.loads((P30 / "selection.json").read_text())["compounds"]:
        parent = c["inchikey"]
        t30 = thermo(P30, parent)
        if sorted(t30) != list(range(1, 31)):
            not_yet.append(dict(parent_inchikey=parent, name=c["name"], conformers_with_thermodynamics=len(t30)))
            continue
        bins = json.loads((PSH / "ensembles" / f"{parent}.json").read_text())["bins"]
        new = sorted(int(b["conformer"][-2:]) for b in bins if b.get("conformer"))
        tsh = thermo(PSH, parent)
        shape_ready = sorted(tsh) == new
        confs = [t30[k] for k in range(1, 31)] + ([tsh[k] for k in new] if shape_ready else [])
        index = {t["rank"]: i for i, t in enumerate(confs)}
        polymers = list(confs[0]["polymer_ensembles"])
        vp = {p: confs[0]["polymer_ensembles"][p][CONVENTION + "_volume"] for p in polymers}
        e = np.array([t["cosmo_energy_hartree"] * HARTREE_KCAL / RT_KCAL for t in confs])
        e -= e.min()
        gs = {s: np.array([t["ln_gamma_conductor"]["solvent:" + s] for t in confs]) for s in solvents}
        gp = {p: np.array([t["polymer_ensembles"][p][CONVENTION + "_gamma"] + t["pure_component_offset"] for t in confs]) for p in polymers}
        pairs = [(s, p) for s in solvents for p in polymers]

        def logp(ranks):
            idx = [index[r] for r in ranks]
            mus = {s: -lse(-(e[idx] + gs[s][idx])) for s in solvents}
            mup = {p: -lse(-(e[idx] + gp[p][idx])) for p in polymers}
            return np.array([(mup[p] - mus[s]) / math.log(10) + math.log10(vp[p] / vs[s]) for s, p in pairs])

        def weights(ranks):
            idx = [index[r] for r in ranks]
            g = e[idx] + gs["water"][idx]
            w = np.exp(-(g - g.min()))
            return dict(zip(ranks, w / w.sum()))

        first = lambda k: list(range(1, k + 1))
        ref30 = logp(first(30))
        curve = []
        for k in range(1, 31):
            d = np.abs(logp(first(k)) - ref30)
            curve.append((float(np.median(d)), float(d.max())))
            rows_k.append(dict(parent_inchikey=parent, name=c["name"], k=k, median_abs_dlogP_vs_30=round(curve[-1][0], 4),
                               max_abs_dlogP_vs_30=round(curve[-1][1], 4)))
        curves[parent] = (c, curve)
        w30 = weights(first(30))
        step = [float(np.abs(logp(first(k)) - logp(first(k - 1))).max()) for k in range(2, 31)]  # step[k-2]: k-1 -> k
        row = dict(parent_inchikey=parent, name=c["name"], role=c["role"], atoms=c.get("atoms"),
                   release_vs_30_median=round(curve[0][0], 3), release_vs_30_worst=round(curve[0][1], 3),
                   ten_vs_30_median=round(curve[9][0], 3), ten_vs_30_worst=round(curve[9][1], 3),
                   twenty_vs_30_worst=round(curve[19][1], 3), change_29_to_30_worst=round(step[-1], 3),
                   largest_step_after_20_worst=round(max(step[19:]), 3),
                   conformers_needed_within_0_1=next(k for k in range(1, 31) if all(curve[j - 1][1] <= 0.1 for j in range(k, 31))),
                   conformers_needed_within_0_3=next(k for k in range(1, 31) if all(curve[j - 1][1] <= 0.3 for j in range(k, 31))),
                   weight_in_water_of_11_to_30=round(float(sum(w30[k] for k in range(11, 31))), 3),
                   dominant_rank_in_30=max(w30, key=w30.get), dominant_weight_in_30=round(float(max(w30.values())), 3),
                   shape_conformers_added=len(new), shape_ready=shape_ready)
        if shape_ready and new:
            everything = first(30) + new
            full = logp(everything)
            wall = weights(everything)
            picks = sorted(int(b["covered_by_energy_ordered"][1:]) if b.get("covered_by_energy_ordered") else int(b["conformer"][-2:])
                           for b in bins if b.get("covered_by_energy_ordered") or b.get("conformer"))
            picks = sorted(set(picks))
            row.update(thirty_vs_30_plus_shape_worst=round(float(np.abs(ref30 - full).max()), 3),
                       weight_in_water_of_shape_conformers=round(float(sum(wall[k] for k in new)), 4),
                       lowest_shape_conformer_cosmo_kcal=round(float(min(e[index[k]] for k in new) * RT_KCAL), 2),
                       stratified_set_size=len(picks),
                       stratified_set_vs_30_plus_shape_worst=round(float(np.abs(logp(picks) - full).max()), 3),
                       first_10_vs_30_plus_shape_worst=round(float(np.abs(logp(first(10)) - full).max()), 3))
        rows.append(row)
    if not rows:
        raise SystemExit("no compound has all 30 conformers with thermodynamics yet")
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with (OUT / "conf30_by_compound.csv").open("w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)
    with (OUT / "conf30_by_k.csv").open("w", newline="") as handle:
        w = csv.DictWriter(handle, fieldnames=list(rows_k[0]))
        w.writeheader()
        w.writerows(rows_k)
    col = lambda name: np.array([r[name] for r in rows if r.get(name, "") != ""], float)
    stat = lambda v: dict(n=int(len(v)), median=round(float(np.median(v)), 3), max=round(float(v.max()), 3),
                          over_0_1=int((v > 0.1).sum()), over_0_3=int((v > 0.3).sum())) if len(v) else dict(n=0)
    summary = dict(selected=18, analysed=len(rows), not_yet=not_yet,
                   shape_analysed=sum(1 for r in rows if "thirty_vs_30_plus_shape_worst" in r),
                   shape_not_yet=[r["parent_inchikey"] for r in rows if r["shape_conformers_added"] and not r["shape_ready"]],
                   **{name: stat(col(name)) for name in ("release_vs_30_worst", "ten_vs_30_worst", "twenty_vs_30_worst", "change_29_to_30_worst",
                                                         "largest_step_after_20_worst", "weight_in_water_of_11_to_30",
                                                         "thirty_vs_30_plus_shape_worst", "weight_in_water_of_shape_conformers",
                                                         "stratified_set_vs_30_plus_shape_worst", "first_10_vs_30_plus_shape_worst")},
                   dominant_rank_in_30=sorted(int(r["dominant_rank_in_30"]) for r in rows))
    (OUT / "conf30_summary.json").write_text(json.dumps(summary, indent=1) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"flexible amide": "#1f77b4", "perfluoroalkyl, no ester": "#d62728", "perfluoroalkyl ester": "#2ca02c"}
    fig, ax = plt.subplots(figsize=(8, 4.8), layout="constrained")
    k = np.arange(1, 30)
    for c, curve in curves.values():
        ax.plot(k, [v[1] for v in curve[:29]], color=colors.get(c["role"], "#555555"), lw=1.0, alpha=0.75)
    for role, color in colors.items():
        if any(c["role"] == role for c, _ in curves.values()):
            ax.plot([], [], color=color, label=role)
    ax.axvline(10, color="#999999", lw=0.8, ls=":")
    for limit in (0.1, 0.3):
        ax.axhline(limit, color="#999999", lw=0.8, ls=":")
    ax.set_yscale("symlog", linthresh=0.01)
    ax.set_ylim(bottom=0)
    ax.set_xlabel("conformers in the ensemble (MMFF order; 1 = the release)")
    ax.set_ylabel("|logP(k conformers) - logP(30 conformers)|, worst of 320 pairs")
    ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=0.3)
    ax.set_title(f"30-conformer test: {len(rows)} of 18 compounds not settled at 10 (one line per compound)\n"
                 "reference = the 30-conformer ensemble, so the last point (k=30, zero by definition) is not drawn", fontsize=9)
    fig.savefig(OUT / "convergence_30.png", dpi=150)
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
