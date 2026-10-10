"""Figure for the waste-pathway case study, drawn from results.json (run run_case_study.py first).

    python3 campaigns/waste-pathway-film-case/make_figures.py

Needs matplotlib (the agent's .venv does not include it; any Python with matplotlib will do). Writes
figures/waste_pathway_case.png and .pdf next to this file.
"""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

HERE = Path(__file__).resolve().parent
LOCATION = "A"

# Validated categorical slots 1 and 2 (light surface): blue for the plant dedicated to the feed, orange for the shared
# plant. Color follows the plant in every panel that compares plants; the heat map uses the blue sequential ramp only.
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"
PLANT = {"dedicated": ("#2a78d6", "o", "-", "Dedicated 8 kt/yr plant"),
         "shared_20kt": ("#eb6834", "s", "--", "Shared 20 kt/yr plant")}
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
        "#184f95", "#104281", "#0d366b"]
TECH = {"lf": "landfill", "we": "incineration", "gas_er": "gasification (energy)", "gas_h2": "gasification (H2)",
        "gas_h2cc": "gasification (H2+CC)", "py": "pyrolysis"}
SOLVENT = {"Dimethyl sulfoxide": "DMSO", "Ethylene glycol": "EG", "Propylene glycol": "PG", "p-Xylene": "xylene",
           "Diphenyl ether": "DPE", "Toluene": "toluene", "Dodecane": "dodecane"}


def short(row: dict) -> str:
    washes = " + ".join(f"{w['polymer']} ({SOLVENT.get(w['solvent'], w['solvent'])})" for w in row["washes"])
    return f"{washes + ' → ' if washes else ''}{TECH[row['downstream']['key']]}"


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=INK2, labelsize=7.5, length=3, width=0.8)
    ax.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def front(ax, results, key, xkey, ykey, xscale=1.0, yscale=1e6, label_points=()):
    for plant, (color, marker, ls, _) in PLANT.items():
        pts = results["plants"][plant]["locations"][LOCATION]["pareto"][key]
        xs = [p[xkey] * xscale for p in pts]
        ys = [p[ykey] / yscale for p in pts]
        ax.plot(xs, ys, color=color, linestyle=ls, linewidth=1.6, marker=marker, markersize=6, markerfacecolor=color,
                markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
    return ax


def main() -> None:
    results = json.loads((HERE / "results.json").read_text())
    fig, axes = plt.subplots(2, 2, figsize=(7.4, 6.6), facecolor=SURFACE)
    (axA, axB), (axC, axD) = axes
    for ax in axes.flat:
        style(ax)

    # A: profit against emissions
    front(axA, results, "emissions_vs_profit", "emissions_t_co2e_per_yr", "profit_usd_per_yr")
    axA.set_xscale("log")
    axA.set_xlabel("Emissions (t CO$_2$e per year, log scale)", fontsize=8, color=INK2)
    axA.set_ylabel("Profit ($ million per year)", fontsize=8, color=INK2)
    ded = results["plants"]["dedicated"]["locations"][LOCATION]["pareto"]["emissions_vs_profit"]
    sh = results["plants"]["shared_20kt"]["locations"][LOCATION]["pareto"]["emissions_vs_profit"]
    def tag(ax, row, text_xy, ha="left"):
        ax.annotate(short(row), (row["emissions_t_co2e_per_yr"], row["profit_usd_per_yr"] / 1e6), xytext=text_xy,
                    textcoords="axes fraction", fontsize=7, color=INK, ha=ha, va="center",
                    arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7, shrinkA=2, shrinkB=4))

    tag(axA, ded[0], (0.14, 0.10))                 # landfill, where both fronts start
    tag(axA, ded[1], (0.33, 0.40))                 # EVOH wash, dedicated plant
    tag(axA, sh[2], (0.05, 0.90))                  # LDPE and EVOH washes, shared plant
    tag(axA, ded[-1], (0.97, 0.53), ha="right")    # the most profitable pathway, dedicated plant
    axA.set_ylim(-0.5, 6.0)
    axA.set_xlim(560, 26000)
    axA.set_title("A  Profit against emissions", loc="left", fontsize=9, color=INK, fontweight="bold")

    # B: profit against circularity
    front(axB, results, "circularity_vs_profit", "circularity_index", "profit_usd_per_yr")
    axB.set_xlabel("MICRON circularity index (0–1)", fontsize=8, color=INK2)
    axB.set_ylabel("Profit ($ million per year)", fontsize=8, color=INK2)
    axB.set_title("B  Profit against circularity", loc="left", fontsize=9, color=INK, fontweight="bold")
    handles = [Line2D([0], [0], color=c, linestyle=ls, marker=m, markersize=6, linewidth=1.6, label=lab,
                      markeredgecolor=SURFACE) for (c, m, ls, lab) in PLANT.values()]
    axB.legend(handles=handles, loc="lower left", fontsize=7, frameon=False, labelcolor=INK2)

    # C: LDPE resin price sweep
    prices = sorted(float(p) for p in results["plants"]["dedicated"]["locations"][LOCATION]["ldpe_price_sweep_max_profit"])
    for plant, (color, marker, ls, label) in PLANT.items():
        sweep = results["plants"][plant]["locations"][LOCATION]["ldpe_price_sweep_max_profit"]
        ys = [sweep[str(p)]["profit_usd_yr"] / 1e6 for p in prices]
        axC.plot(prices, ys, color=color, linestyle=ls, linewidth=1.6, marker=marker, markersize=6,
                 markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
        washes_ldpe = [sweep[str(p)]["pathway"].startswith("LDPE") for p in prices]
        first = next((i for i, flag in enumerate(washes_ldpe) if flag), None)
        if first is not None:
            axC.annotate("LDPE wash enters", (prices[first], ys[first]),
                         xytext=(-30, 34) if plant == "dedicated" else (34, -30), textcoords="offset points",
                         ha="center", fontsize=7, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7))
    axC.set_xlabel("LDPE resin price ($ per tonne)", fontsize=8, color=INK2)
    axC.set_ylabel("Profit, best pathway ($ million per year)", fontsize=8, color=INK2)
    axC.set_title("C  When the LDPE wash pays", loc="left", fontsize=9, color=INK, fontweight="bold")

    # D: circularity category scores of the three optima (shared plant, one sequential ramp, values printed)
    optima = results["plants"]["shared_20kt"]["locations"][LOCATION]["optima"]
    cols = [("max_profit", "Max\nprofit"), ("max_circularity", "Max\ncircularity"), ("min_emissions", "Min\nemissions")]
    rows = [("energy", "Energy"), ("ghg", "GHG"), ("water", "Water"), ("waste", "Waste"),
            ("substitutability", "Substitutability"), ("overall", "Overall index")]
    axD.set_facecolor(SURFACE)
    axD.grid(False)
    for side in axD.spines.values():
        side.set_visible(False)
    for j, (okey, _) in enumerate(cols):
        for i, (rkey, _) in enumerate(rows):
            value = optima[okey]["circularity_index"] if rkey == "overall" else optima[okey]["circularity_scores"][rkey]
            step = min(len(RAMP) - 1, int(max(0.0, min(1.0, value)) * (len(RAMP) - 1)))
            axD.add_patch(plt.Rectangle((j + 0.04, i + 0.04), 0.92, 0.92, facecolor=RAMP[step], edgecolor="none"))
            axD.text(j + 0.5, i + 0.5, f"{value:.2f}", ha="center", va="center", fontsize=8,
                     color="#ffffff" if step >= 6 else INK, fontweight="bold" if rkey == "overall" else "normal")
    axD.set_xlim(0, len(cols))
    axD.set_ylim(len(rows), 0)
    axD.set_xticks([j + 0.5 for j in range(len(cols))])
    axD.set_xticklabels([c[1] for c in cols], fontsize=7.5, color=INK2)
    axD.set_yticks([i + 0.5 for i in range(len(rows))])
    axD.set_yticklabels([r[1] for r in rows], fontsize=7.5, color=INK2)
    axD.xaxis.tick_top()
    axD.tick_params(length=0)
    axD.set_title("D  MICRON scores (shared plant)", loc="left", fontsize=9, color=INK, fontweight="bold", pad=30)

    fig.tight_layout(h_pad=2.2, w_pad=2.0)
    out = HERE / "figures"
    out.mkdir(exist_ok=True)
    fig.savefig(out / "waste_pathway_case.png", dpi=300, facecolor=SURFACE)
    fig.savefig(out / "waste_pathway_case.pdf", facecolor=SURFACE)
    print("wrote", out / "waste_pathway_case.png")


if __name__ == "__main__":
    main()
