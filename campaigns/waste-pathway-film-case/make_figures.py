"""Figure for the waste-pathway case study, drawn from results.json (run run_case_study.py first).

    python3 campaigns/waste-pathway-film-case/make_figures.py

Needs matplotlib (the agent's .venv does not include it; any Python with matplotlib will do). Writes
figures/waste_pathway_case.png and .pdf next to this file. Nothing here is specific to one feed: the labels, the price sweep and the
numbered routes all come from results.json.
"""
from __future__ import annotations

import json
import textwrap
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
PLANT_STYLE = {"dedicated": ("#2a78d6", "o", "-"), "shared_20kt": ("#eb6834", "s", "--")}
RAMP = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab",
        "#184f95", "#104281", "#0d366b"]
TECH = {"lf": "landfill", "we": "incineration", "gas_er": "gasification (energy)", "gas_h2": "gasification (H2)",
        "gas_h2cc": "gasification (H2+CC)", "py": "pyrolysis", "resale": "sold as resin"}
SOLVENT = {"Dimethyl sulfoxide": "DMSO", "Ethylene glycol": "EG", "Propylene glycol": "PG", "p-Xylene": "xylene",
           "Diphenyl ether": "DPE", "Toluene": "toluene", "Dodecane": "dodecane", "Acetic Acid": "acetic acid",
           "Diethylene Glycol": "DEG", "Dipentene (Dl-Limonene)": "limonene"}


def short(row: dict) -> str:
    washes = " › ".join(f"{w['polymer']} ({SOLVENT.get(w['solvent'], w['solvent'].lower())})" for w in row["washes"])
    destination = TECH.get(row["downstream"]["key"], row["downstream"]["label"])
    return f"{washes} → {destination}" if washes else f"no wash → {destination}"


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


def plant_labels(results: dict) -> dict[str, str]:
    tonnes = results["feed"]["tonnes_per_year"] / 1000.0
    return {"dedicated": f"Plant for this feed only ({tonnes:g} kt/yr)", "shared_20kt": "Shared 20 kt/yr plant"}


def front(ax, results, key, xkey, ykey, xscale=1.0, yscale=1e6):
    """Draw both plants' fronts; return {plant: [(number, row)]} with the numbers merged where points coincide."""
    fig = ax.figure
    fig.canvas.draw()
    numbered: dict[str, list] = {}
    for plant, (color, marker, ls) in PLANT_STYLE.items():
        pts = results["plants"][plant]["locations"][LOCATION]["pareto"][key]
        xs = [p[xkey] * xscale for p in pts]
        ys = [p[ykey] / yscale for p in pts]
        ax.plot(xs, ys, color=color, linestyle=ls, linewidth=1.6, marker=marker, markersize=6, markerfacecolor=color,
                markeredgecolor=SURFACE, markeredgewidth=1.2, zorder=3)
        numbered[plant] = [(i, p) for i, p in enumerate(pts, 1)]
    return numbered


ROUTE_IDS: dict[str, str] = {}  # pathway label -> R1, R2, ... in order of first appearance, shared by both front panels
ROUTE_ROWS: dict[str, dict] = {}


def route_id(row: dict) -> str:
    label = row["pathway"]
    if label not in ROUTE_IDS:
        ROUTE_IDS[label] = f"R{len(ROUTE_IDS) + 1}"
        ROUTE_ROWS[label] = row
    return ROUTE_IDS[label]


def number_points(ax, numbered, results, xkey, ykey, xscale=1.0, yscale=1e6):
    """Label each front point with the ID of its route (the same route has the same ID in every panel and plant), merging points
    that sit on top of each other."""
    ax.figure.canvas.draw()
    placed: list[tuple[float, float, list[str]]] = []
    for plant, items in numbered.items():
        for _, p in items:
            name = route_id(p)
            pixel = ax.transData.transform((p[xkey] * xscale, p[ykey] / yscale))
            for entry in placed:
                if abs(entry[0] - pixel[0]) < 38 and abs(entry[1] - pixel[1]) < 16:
                    if name not in entry[2]:
                        entry[2].append(name)
                    break
            else:
                placed.append((pixel[0], pixel[1], [name]))
    boxes: list[tuple[float, float, float, float]] = []  # label rectangles in pixels, so labels can step aside from each other
    dpi_scale = ax.figure.dpi / 72.0
    for px, py, names in placed:
        text = ", ".join(names)
        width, height = 4.6 * len(text) * dpi_scale, 9.0 * dpi_scale
        for dy in (7, -12, 20, -25, 33, -38, 46):  # points above or below the marker, the first that is free
            left, bottom = px + 6 * dpi_scale, py + dy * dpi_scale
            box = (left, bottom, left + width, bottom + height)
            if not any(box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in boxes):
                break
        boxes.append(box)
        x, y = ax.transData.inverted().transform((px, py))
        ax.annotate(text, (x, y), xytext=(6, dy), textcoords="offset points", fontsize=7, color=INK, zorder=6)


def main() -> None:
    results = json.loads((HERE / "results.json").read_text())
    labels = plant_labels(results)
    fig = plt.figure(figsize=(7.6, 8.1), facecolor=SURFACE)
    grid = fig.add_gridspec(3, 2, height_ratios=[1.0, 1.0, 0.36], hspace=0.62, wspace=0.34, left=0.085, right=0.985, top=0.965, bottom=0.02)
    axA, axB, axC, axD = (fig.add_subplot(grid[0, 0]), fig.add_subplot(grid[0, 1]), fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[1, 1]))
    keyax = fig.add_subplot(grid[2, :])
    keyax.axis("off")
    for ax in (axA, axB, axC):
        style(ax)

    # A: profit against emissions
    numA = front(axA, results, "emissions_vs_profit", "emissions_t_co2e_per_yr", "profit_usd_per_yr")
    axA.set_xscale("log")
    axA.set_xlabel("Emissions (t CO$_2$e per year, log scale)", fontsize=8, color=INK2)
    axA.set_ylabel("Profit ($ million per year)", fontsize=8, color=INK2)
    axA.set_title("A  Profit against emissions", loc="left", fontsize=9, color=INK, fontweight="bold")
    number_points(axA, numA, results, "emissions_t_co2e_per_yr", "profit_usd_per_yr")

    # B: profit against circularity
    numB = front(axB, results, "circularity_vs_profit", "circularity_index", "profit_usd_per_yr")
    axB.set_xlabel("MICRON circularity index (0–1)", fontsize=8, color=INK2)
    axB.set_ylabel("Profit ($ million per year)", fontsize=8, color=INK2)
    axB.set_title("B  Profit against circularity", loc="left", fontsize=9, color=INK, fontweight="bold")
    number_points(axB, numB, results, "circularity_index", "profit_usd_per_yr")
    handles = [Line2D([0], [0], color=c, linestyle=ls, marker=m, markersize=6, linewidth=1.6, label=labels[plant],
                      markeredgecolor=SURFACE) for plant, (c, m, ls) in PLANT_STYLE.items()]
    axB.legend(handles=handles, loc="lower left", fontsize=6.6, frameon=False, labelcolor=INK2)

    # C: resin price sweep
    polymer = results.get("sweep_polymer", "EVOH")
    sweep0 = results["plants"]["dedicated"]["locations"][LOCATION]["price_sweep_max_profit"]
    prices = sorted(float(p) for p in sweep0)
    for plant, (color, marker, ls) in PLANT_STYLE.items():
        sweep = results["plants"][plant]["locations"][LOCATION]["price_sweep_max_profit"]
        ys = [sweep[str(p)]["profit_usd_yr"] / 1e6 for p in prices]
        axC.plot(prices, ys, color=color, linestyle=ls, linewidth=1.6, marker=marker, markersize=6, markeredgecolor=SURFACE,
                 markeredgewidth=1.2, zorder=3)
        washes = [sweep[str(p)]["pathway"].startswith(polymer) or f"{polymer}/" in sweep[str(p)]["pathway"] for p in prices]
        first = next((i for i, flag in enumerate(washes) if flag), None)
        if first is not None and first > 0:
            axC.annotate(f"{polymer} wash enters", (prices[first], ys[first]),
                         xytext=(-30, 34) if plant == "dedicated" else (30, -16), textcoords="offset points",
                         ha="center" if plant == "dedicated" else "left",
                         fontsize=7, color=INK, arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7))
    axC.set_xlabel(f"{polymer} resin price ($ per tonne)", fontsize=8, color=INK2)
    axC.set_ylabel("Profit, best pathway ($ million per year)", fontsize=8, color=INK2)
    axC.set_title(f"C  When the {polymer} wash pays", loc="left", fontsize=9, color=INK, fontweight="bold")

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

    # Key: every route that appears on a front, once, so no point depends on a label inside a panel
    entries = [f"{rid} {short(ROUTE_ROWS[label])}" for label, rid in ROUTE_IDS.items()]
    half = (len(entries) + 1) // 2
    width = max(len(e) for e in entries) + 2
    lines = []
    for i in range(half):
        left = entries[i]
        right = entries[i + half] if i + half < len(entries) else ""
        lines.append(f"{left:<{width}}{right}")
    keyax.text(0.0, 1.0, "Routes on the fronts (blue: plant for this feed only; orange: shared plant)\n" + "\n".join(lines),
               transform=keyax.transAxes, ha="left", va="top", fontsize=6.0, color=INK2, family="monospace", linespacing=1.35)
    out = HERE / "figures"
    out.mkdir(exist_ok=True)
    fig.savefig(out / "waste_pathway_case.png", dpi=300, facecolor=SURFACE)
    fig.savefig(out / "waste_pathway_case.pdf", facecolor=SURFACE)
    print("wrote", out / "waste_pathway_case.png")


if __name__ == "__main__":
    main()
