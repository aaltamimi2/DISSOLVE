"""Exploratory conformer study (owner request 2026-10-10; not a release input): how the openCOSMO-RS results change with
the number of conformers and with conformer shape (radius of gyration, SASA).

Inputs: state/conformers-v1/thermo/<conformer>.json (conformer_thermo.py) and
exploratory/conformer-sampling/conformer_descriptors.csv (conformer_descriptors.py).

Conformer ensemble (standard COSMO-RS conformer treatment, built on single-conformer openCOSMO-RS, which refuses more
than one conformer): in each phase X the free energy of conformer c is G_c^X/RT = E_c/RT + ln g_c^X(conductor), with
E_c the COSMO (conductor) energy and ln g against the ideal conductor; the compound's pseudo-chemical potential is
mu^X/RT = -ln sum_c exp(-G_c^X/RT). A polymer's value per conformer is the campaign's repeat-unit ensemble, shifted to
the conductor reference by the conformer's offset (the ensemble passes a constant shift through). Then
logP_x(S/P) = (mu^P - mu^S)/(RT ln 10) and logP_concentration = logP_x + log10(V_P/V_S), as the release computes them;
for one conformer this is exactly the release's formula.

"k conformers" means the first k in the order the recipe would add them (MMFF energy rank, k01 = the conformer the
release uses). A compound enters the convergence and release-error results only when every kept conformer converged
(its ensemble is complete); until then only its single-conformer shifts are reported, against the lowest-ranked
converged conformer, and nothing stands in for a missing one.

Writes exploratory/conformer-ablation/: ablation_by_k.csv, single_conformer.csv, summary_by_compound.csv and
convergence.png, single_conformer_vs_shape.png, release_vs_ensemble_by_mw.png.

    ~/.venvs/cosmo-logp/bin/python scripts/conformer_ablation.py"""
import csv
import json
import math
import os
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STUDY = os.environ.get("CONFORMER_STUDY", "conformers-v1")  # conformers-htc-v1: the CHTC study of 236 compounds
P = ROOT / "state" / STUDY
OUT = ROOT / "exploratory" / ("conformer-ablation" if STUDY == "conformers-v1" else "conformer-htc")
SHAPE = ROOT / "exploratory" / ("conformer-sampling" if STUDY == "conformers-v1" else "conformer-htc") / "conformer_descriptors.csv"
SPEC = Path("/mnt/r/plastchem-euler/coverage-thermo-v1/t12/inputs.json")
RT_KCAL = 0.0019872041 * 298.15
HARTREE_KCAL = 627.5094740631
CONVENTION = "normalized"


def lse(a):
    a = np.asarray(a, float)
    m = a.max()
    return m + math.log(np.exp(a - m).sum())


def main():
    spec = json.loads(SPEC.read_text())
    solvents = list(spec["solvents"])
    vs = {s: spec["solvents"][s][CONVENTION + "_volume_cm3_mol"] for s in solvents}
    selection = {c["inchikey"]: c for c in json.loads((P / "selection.json").read_text())["compounds"]}
    shape = {r["conformer"]: r for r in csv.DictReader(SHAPE.open())}
    by = defaultdict(list)
    for path in sorted((P / "thermo").glob("*.json")):
        t = json.loads(path.read_text())
        by[t["parent_inchikey"]].append(t)
    OUT.mkdir(parents=True, exist_ok=True)
    rows_k, rows_single, rows_summary = [], [], []
    curves = {}
    for parent, confs in by.items():
        confs.sort(key=lambda t: t["rank"])
        polymers = list(confs[0]["polymer_ensembles"])
        vp = {p: confs[0]["polymer_ensembles"][p][CONVENTION + "_volume"] for p in polymers}
        e = np.array([t["cosmo_energy_hartree"] * HARTREE_KCAL / RT_KCAL for t in confs])
        e -= e.min()
        gs = {s: np.array([t["ln_gamma_conductor"]["solvent:" + s] for t in confs]) for s in solvents}
        gp = {p: np.array([t["polymer_ensembles"][p][CONVENTION + "_gamma"] + t["pure_component_offset"] for t in confs]) for p in polymers}
        pairs = [(s, p) for s in solvents for p in polymers]

        def logp(idx):
            mus = {s: -lse(-(e[idx] + gs[s][idx])) for s in solvents}
            mup = {p: -lse(-(e[idx] + gp[p][idx])) for p in polymers}
            return np.array([(mup[p] - mus[s]) / math.log(10) + math.log10(vp[p] / vs[s]) for s, p in pairs])

        n = len(confs)
        kept = json.loads((P / "ensembles" / f"{parent}.json").read_text())["kept"]
        complete = n == kept and [t["rank"] for t in confs] == list(range(1, kept + 1))
        full = logp(list(range(n)))
        prefix = 0  # conformers k01..k0m all converged: only then is "the first k" what the recipe would run
        while prefix < n and confs[prefix]["rank"] == prefix + 1:
            prefix += 1
        curve = []
        for k in range(1, prefix + 1 if complete else 0):
            d = np.abs(logp(list(range(k))) - full)
            curve.append((k, float(np.median(d)), float(d.max())))
            rows_k.append(dict(parent_inchikey=parent, name=selection[parent]["name"], k=k, ranks=" ".join(str(t["rank"]) for t in confs[:k]),
                               median_abs_dlogP=round(float(np.median(d)), 4), max_abs_dlogP=round(float(d.max()), 4)))
        curves[parent] = curve
        assert "water" in gs, "the water phase is missing from the solvent panel"
        weights_water = np.exp(-(e + gs["water"] - (e + gs["water"]).min()))
        weights_water /= weights_water.sum()
        k01 = logp([0])
        for i, t in enumerate(confs):
            single = logp([i])
            assert t["conformer"] in shape, f"no shape descriptors for {t['conformer']}: run conformer_descriptors.py first"
            sh = shape[t["conformer"]]
            rows_single.append(dict(parent_inchikey=parent, name=selection[parent]["name"], conformer=t["conformer"], rank=t["rank"],
                                    cosmo_delta_kcal=round(float(e[i] * RT_KCAL), 3), weight_in_water=round(float(weights_water[i]), 4),
                                    radius_of_gyration_A=sh.get("radius_of_gyration_A"), sasa_A2=sh.get("sasa_A2"),
                                    cosmo_area_A2=round(t["cosmo_area_A2"], 2), cosmo_volume_A3=round(t["cosmo_volume_A3"], 2),
                                    median_dlogP_vs_first=round(float(np.median(single - k01)), 4),
                                    median_abs_dlogP_vs_first=round(float(np.median(np.abs(single - k01))), 4),
                                    max_abs_dlogP_vs_first=round(float(np.abs(single - k01).max()), 4)))
        c = selection[parent]
        has_k01 = confs[0]["rank"] == 1
        # compared only when the ensemble is complete: against a partial ensemble the gap is not the release's error
        d01 = np.abs(k01 - full) if complete else None
        if not complete:
            curves.pop(parent, None)
        rows_summary.append(dict(parent_inchikey=parent, name=c["name"], role=c["role"], molecular_weight_g_mol=c["molecular_weight_g_mol"],
                                 rotatable_bonds=c["rotatable_bonds"], conformers_converged=n,
                                 conformers_kept=kept, k01_converged=has_k01, ensemble_complete=complete,
                                 release_vs_ensemble_median_abs_dlogP=round(float(np.median(d01)), 4) if complete else "",
                                 release_vs_ensemble_max_abs_dlogP=round(float(d01.max()), 4) if complete else "",
                                 single_conformer_spread_median=round(float(np.median(np.ptp(np.array([logp([i]) for i in range(n)]), axis=0))), 4)))
    # every output is rewritten on every run (an empty table keeps its header), so nothing stale survives a run
    fields = {"ablation_by_k.csv": ["parent_inchikey", "name", "k", "ranks", "median_abs_dlogP", "max_abs_dlogP"]}
    for name, rows in (("ablation_by_k.csv", rows_k), ("single_conformer.csv", rows_single), ("summary_by_compound.csv", rows_summary)):
        with (OUT / name).open("w", newline="") as handle:
            w = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else fields.get(name, ["note"]))
            w.writeheader()
            w.writerows(rows)
    for name in ("convergence.png", "single_conformer_vs_shape.png", "release_vs_ensemble_by_mw.png"):
        (OUT / name).unlink(missing_ok=True)
    if STUDY == "conformers-v1":  # per-compound figures: the 31-compound study only (conformer_htc_summary.py draws the other)
        figures(curves, rows_single, rows_summary, selection)
    print(json.dumps(dict(compounds=len(by), conformers=sum(len(v) for v in by.values()),
                          complete=[r["name"][:30] for r in rows_summary if r["conformers_converged"] == r["conformers_kept"]])))


def figures(curves, rows_single, rows_summary, selection):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cmap = matplotlib.colormaps["viridis"]
    mws = [selection[p]["molecular_weight_g_mol"] for p in curves] or [0, 1]
    norm = matplotlib.colors.Normalize(min(mws), max(mws))
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), layout="constrained")
    for parent, curve in curves.items():
        if len(curve) < 2:
            continue
        c = selection[parent]
        k = [x[0] for x in curve]
        for ax, j in zip(axes, (1, 2)):
            ax.plot(k, [x[j] for x in curve], marker="o", ms=3, lw=1.2, color=cmap(norm(c["molecular_weight_g_mol"])),
                    ls="-" if c["role"] == "flexible" else "--", label=c["name"][:28] if j == 1 else None)
    for ax, title in zip(axes, ("median over 320 solvent/polymer pairs", "worst solvent/polymer pair")):
        ax.set_xlabel("conformers in the ensemble (MMFF order; 1 = the release)")
        ax.set_ylabel("|logP(k) - logP(all converged)|")
        ax.set_title(title, fontsize=10)
        ax.grid(alpha=0.3)
    if curves:
        axes[0].legend(fontsize=6.5, frameon=False)
    else:
        for ax in axes:
            ax.text(0.5, 0.5, "no compound has all its conformers yet", ha="center", va="center", transform=ax.transAxes, color="#777777")
    fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap), ax=axes, label="molecular weight (g/mol)", shrink=0.8)
    fig.suptitle("Convergence of the openCOSMO-RS partition coefficient with the number of conformers", fontsize=11)
    fig.savefig(OUT / "convergence.png", dpi=150)
    plt.close(fig)

    parents = sorted({r["parent_inchikey"] for r in rows_single
                      if sum(x["parent_inchikey"] == r["parent_inchikey"] for x in rows_single) >= 2})
    if parents:
        cols = min(4, len(parents))
        nrows = math.ceil(len(parents) / cols)
        fig, axes = plt.subplots(nrows, cols, figsize=(cols * 3.4, nrows * 3.1), squeeze=False, layout="constrained")
        for ax in axes.flat:
            ax.set_visible(False)
        lim = max([abs(r["median_dlogP_vs_first"]) for r in rows_single] + [0.05])
        dnorm = matplotlib.colors.TwoSlopeNorm(vmin=-lim, vcenter=0, vmax=lim)
        for ax, parent in zip(axes.flat, parents):
            ax.set_visible(True)
            rs = [r for r in rows_single if r["parent_inchikey"] == parent and r["radius_of_gyration_A"]]
            sc = ax.scatter([float(r["radius_of_gyration_A"]) for r in rs], [float(r["sasa_A2"]) for r in rs],
                            c=[r["median_dlogP_vs_first"] for r in rs], cmap="coolwarm", norm=dnorm, s=40, edgecolors="#333333", linewidths=0.4)
            for r in rs:
                if r["rank"] == 1:
                    ax.scatter([float(r["radius_of_gyration_A"])], [float(r["sasa_A2"])], s=110, facecolors="none", edgecolors="#d62728", linewidths=1.3)
            ax.set_title(f"{selection[parent]['name'][:30]}\n{selection[parent]['molecular_weight_g_mol']:.0f} g/mol", fontsize=8)
            ax.tick_params(labelsize=7)
        fig.supxlabel("radius of gyration (Å)")
        fig.supylabel("SASA (Å²)")
        fig.colorbar(sc, ax=axes, label="median logP shift of this single conformer vs the lowest-ranked converged one (k01 when present)", shrink=0.6)
        fig.suptitle("Single-conformer openCOSMO-RS results across the conformers' shapes (red ring = k01, the release)", fontsize=10)
        fig.savefig(OUT / "single_conformer_vs_shape.png", dpi=150)
        plt.close(fig)

    if any(r["ensemble_complete"] for r in rows_summary):
        fig, ax = plt.subplots(figsize=(6.5, 4.2), layout="constrained")
        for r in rows_summary:
            if r["release_vs_ensemble_max_abs_dlogP"] == "":
                continue
            ax.scatter(r["molecular_weight_g_mol"], r["release_vs_ensemble_max_abs_dlogP"], s=20 + 3 * r["rotatable_bonds"],
                       marker="o" if r["role"] == "flexible" else "s", color="#1f77b4" if r["role"] == "flexible" else "#a0522d",
                       alpha=0.8)
        ax.set_xlabel("molecular weight (g/mol)")
        ax.set_ylabel("|logP(release, k01) - logP(ensemble)|, worst pair")
        ax.set_title("Error of the single-conformer release against the full conformer ensemble\n(complete ensembles only; "
                     "circles flexible, squares rigid; size = rotatable bonds)", fontsize=9)
        ax.grid(alpha=0.3)
        fig.savefig(OUT / "release_vs_ensemble_by_mw.png", dpi=150)
        plt.close(fig)


if __name__ == "__main__":
    main()
