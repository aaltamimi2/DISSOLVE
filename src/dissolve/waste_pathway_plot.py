"""Draw the three Pareto fronts of a waste-pathway request: emissions against profit, circularity against profit, and
emissions against circularity, with the numbered routes listed under each panel.

optimize_waste_pathway saves every request it answers (all candidate pathways and the three exact fronts) and reports the file
as ``figure_data_file``; with ``draw_plot=true`` it also draws and opens this figure. The same code runs from a terminal:

    python3 scripts/plot_pareto.py                      # the latest request, ~/.dissolve/pathway_results/latest.json
    python3 scripts/plot_pareto.py path/to/result.json  # a saved request
    python3 scripts/plot_pareto.py --out fronts.pdf     # PNG or PDF, by extension
    python3 scripts/plot_pareto.py --summary            # the work behind the request: pool, screen, simulations, candidates

This file imports nothing from dissolve and needs matplotlib only to draw (the agent's .venv may lack it; any Python that has it
can run this file). Next to the figure it writes ``<name>_fronts.md``, the same fronts as a table.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

SCHEMA = "dissolve.waste-pathway-figure-data.v1"
PAIRS = (("emissions_vs_profit", "emissions", "profit"), ("circularity_vs_profit", "circularity", "profit"),
         ("emissions_vs_circularity", "emissions", "circularity"))
KEY = {"emissions": "emissions_t_co2e_per_yr", "profit": "profit_usd_per_yr", "circularity": "circularity_index"}
SOLVENT = {"dimethyl sulfoxide": "DMSO", "ethylene glycol": "EG", "propylene glycol": "PG", "diethylene glycol": "DEG",
           "diphenyl ether": "DPE", "dipentene (dl-limonene)": "limonene", "acetic acid": "acetic acid", "p-xylene": "xylene"}


def short_solvent(name: str) -> str:
    """A short, unambiguous name for a route list: a known abbreviation, else the full name when it is short (so propylene carbonate is not read as propylene glycol), else its first word."""
    known = SOLVENT.get(name.strip().lower())
    if known:
        return known
    return name.lower() if len(name) <= 20 else name.split()[0].lower()
TECH = {"lf": "landfill", "we": "incineration", "py": "pyrolysis", "gas_er": "gasification (energy)",
        "gas_h2": "gasification (H2)", "gas_h2cc": "gasification (H2+CC)", "resale": "sold as resin"}
# Validated categorical slots 1 and 2 of the dataviz palette; the dominated candidates are a recessive neutral.
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"
FRONT, ANSWER, DOMINATED = "#2a78d6", "#eb6834", "#c9c8c3"
LIST_WIDTH = 50  # characters per line of the route list under a panel


def default_path() -> Path:
    return Path(os.environ.get("DISSOLVE_PATHWAY_RESULTS_DIR") or Path.home() / ".dissolve" / "pathway_results") / "latest.json"


def load(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"{path} is not a saved pathway request (schema {data.get('schema')!r})")
    return data


def short_label(point: dict[str, Any]) -> str:
    washes = " › ".join(f"{w['polymer']} ({short_solvent(w['solvent'])})" for w in point["washes"])
    destination = TECH.get(point["downstream_key"], point["downstream"])
    return f"{washes} → {destination}" if washes else f"no wash → {destination}"


def front_points(data: dict[str, Any], name: str) -> list[dict[str, Any]]:
    """The points of one front, in the order the tool saved them (ascending in the first metric)."""
    by_label = {c["label"]: c for c in data["candidates"]}
    return [by_label[label] for label in data["fronts"][name]]


def route_list(data: dict[str, Any], name: str) -> list[str]:
    """The numbered routes of one front as wrapped lines for the list under its panel."""
    lines: list[str] = []
    for i, point in enumerate(front_points(data, name), 1):
        wrapped = textwrap.wrap(short_label(point), LIST_WIDTH - 4) or [""]
        lines.append(f"{i:>2}  {wrapped[0]}")
        lines.extend(f"      {rest}" for rest in wrapped[1:])
    return lines


def fronts_markdown(data: dict[str, Any]) -> str:
    feed = ", ".join(f"{v:.0%} {k}" for k, v in data["feed"]["mass_fractions"].items())
    lines = [f"# Pareto fronts: {data['feed']['tonnes_per_year']:,.0f} t/yr ({feed})", "",
             f"{len(data['candidates'])} candidate pathways; {data['plant'].get('cost_basis')}; downstream sites: "
             f"{data['downstream_sites']}; basis of the washes: {data['basis']}.", ""]
    for name, x, y in PAIRS:
        lines += [f"## {y} against {x}", "", "| # | Pathway | Profit ($/yr) | Emissions (t CO2e/yr) | Circularity |", "|---|---|---|---|---|"]
        for i, p in enumerate(front_points(data, name), 1):
            lines.append(f"| {i} | {short_label(p)} | {p[KEY['profit']]:,.0f} | {p[KEY['emissions']]:,.0f} | {p[KEY['circularity']]:.3f} |")
        lines.append("")
    lines.append("Circularity is the MICRON screening index against upper bounds fixed for this request; predicted pathways "
                 "do not establish experimental purity, recovery or economics.")
    return "\n".join(lines) + "\n"


def draw(data: dict[str, Any], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    labels = {"emissions": "Emissions (t CO$_2$e per year)", "profit": "Profit ($ million per year)", "circularity": "MICRON circularity index"}
    better = {"emissions": "lower is better", "profit": "higher is better", "circularity": "higher is better"}
    scale = {"emissions": 1.0, "profit": 1e-6, "circularity": 1.0}
    answer = data.get("selected")
    lists = [route_list(data, name) for name, _, _ in PAIRS]
    list_lines = max(len(lines) for lines in lists)
    plot_h, gap_h, list_h = 4.1, 0.75, 0.145 * list_lines + 0.1  # inches: panels, room for the x labels, route lists
    top_h, bottom_h = 1.1, 0.45
    total = top_h + plot_h + gap_h + list_h + bottom_h
    fig = plt.figure(figsize=(12.6, total), facecolor=SURFACE)
    grid = fig.add_gridspec(2, 3, height_ratios=[plot_h, gap_h + list_h], hspace=0.0, wspace=0.28, left=0.055, right=0.99,
                            top=1 - top_h / total, bottom=bottom_h / total)
    for col, (name, x, y) in enumerate(PAIRS):
        ax = fig.add_subplot(grid[0, col])
        ax.set_facecolor(SURFACE)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(MUTED)
        ax.tick_params(colors=INK2, labelsize=8, length=3)
        ax.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        cx = [c[KEY[x]] * scale[x] for c in data["candidates"]]
        cy = [c[KEY[y]] * scale[y] for c in data["candidates"]]
        ax.scatter(cx, cy, s=16, color=DOMINATED, edgecolor="none", zorder=2)
        front = front_points(data, name)
        fx, fy = [p[KEY[x]] * scale[x] for p in front], [p[KEY[y]] * scale[y] for p in front]
        ax.plot(fx, fy, color=FRONT, linewidth=1.6, zorder=3)
        ax.scatter(fx, fy, s=46, color=FRONT, edgecolor=SURFACE, linewidth=1.2, zorder=4)
        positive = [v for v in cx if v > 0]
        if x == "emissions" and positive and max(positive) / min(positive) > 30:
            ax.set_xscale("log")
        fig.canvas.draw()  # pixel positions are needed to merge the numbers of points that sit on top of each other
        placed: list[tuple[float, float, list[int]]] = []
        for i, (px, py) in enumerate(zip(fx, fy), 1):
            pixel = ax.transData.transform((px, py))
            for entry in placed:
                if abs(entry[0] - pixel[0]) < 9 and abs(entry[1] - pixel[1]) < 9:
                    entry[2].append(i)
                    break
            else:
                placed.append((pixel[0], pixel[1], [i]))
        for pixel_x, pixel_y, numbers in placed:
            px, py = ax.transData.inverted().transform((pixel_x, pixel_y))
            ax.annotate(", ".join(map(str, numbers)), (px, py), xytext=(5, 5), textcoords="offset points", fontsize=7.5,
                        color=INK, zorder=6)
        point = next((c for c in data["candidates"] if c["label"] == answer), None) if answer else None
        if point:  # the request's own answer, on this front or not
            ax.scatter([point[KEY[x]] * scale[x]], [point[KEY[y]] * scale[y]], s=110, facecolor="none", edgecolor=ANSWER,
                       linewidth=2.2, zorder=5)
        ax.set_xlabel(f"{labels[x]}: {better[x]}", fontsize=8.5, color=INK2)
        ax.set_ylabel(f"{labels[y]}: {better[y]}", fontsize=8.5, color=INK2)
        ax.set_title(f"{y.capitalize()} vs. {x}", loc="left", fontsize=9.5, color=INK, fontweight="bold")
        ax.text(1.0, 1.02, f"{len(front)} non-dominated", transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=MUTED)
        key = fig.add_subplot(grid[1, col])  # the numbered routes of this front
        key.axis("off")
        key.text(0.0, list_h / (gap_h + list_h), "\n".join(lists[col]), transform=key.transAxes, ha="left", va="top",
                 fontsize=7.4, color=INK2, family="monospace", linespacing=1.35)
    handles = [Line2D([0], [0], marker="o", color="none", markerfacecolor=DOMINATED, markersize=6, label="Other candidate pathways"),
               Line2D([0], [0], marker="o", color=FRONT, markerfacecolor=FRONT, markersize=7, linewidth=1.6,
                      label="Non-dominated front (numbers are listed under each panel)")]
    if answer:
        handles.append(Line2D([0], [0], marker="o", color="none", markerfacecolor="none", markeredgecolor=ANSWER,
                              markeredgewidth=2.2, markersize=10, label="Pathway chosen for this request"))
    feed = ", ".join(f"{v:.0%} {k}" for k, v in data["feed"]["mass_fractions"].items())
    fig.text(0.01, 1 - 0.15 / total, f"{data['feed']['tonnes_per_year']:,.0f} t/yr ({feed}); {data['plant'].get('cost_basis')}; "
             f"{len(data['candidates'])} candidate pathways", ha="left", va="top", fontsize=10, color=INK)
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, 1 - 0.38 / total), ncol=3, frameon=False, fontsize=8.5,
               labelcolor=INK2)
    fig.text(0.01, 0.12 / total, "Circularity is a screening index against bounds fixed for this request. Predicted pathways do not "
             "establish experimental purity, recovery or economics.", fontsize=7, color=MUTED, ha="left", va="bottom")
    fig.savefig(out, dpi=300, facecolor=SURFACE)
    plt.close(fig)


def write(data_file: Path, out: Path | None = None) -> tuple[Path, Path]:
    """Draw one saved request and write its table; returns (figure, table)."""
    data = load(data_file)
    figure = out or data_file.with_name(f"{data_file.stem}_fronts.png")
    draw(data, figure)
    table = figure.with_name(f"{figure.stem}.md" if figure.stem.endswith("_fronts") else f"{figure.stem}_fronts.md")
    table.write_text(fronts_markdown(data))
    return figure, table


def _summary(data: dict[str, Any]) -> dict[str, Any]:
    summary = data.get("work_summary")
    if not summary:
        raise SystemExit("this saved request has no work summary (it was saved by an older version): ask the question again")
    return summary


def stream_state(removed_before: list[str]) -> str:
    return "whole stream" if not removed_before else f"after {', '.join(removed_before)} removed"


def summary_rows(data: dict[str, Any]) -> list[tuple[str, int, str, str]]:
    """The stages of the work, in order: (label, bar size, shown value, note). The bar size is a count; the shown value is
    that count, or the three front sizes for the last stage."""
    w = _summary(data)
    pool, sims, routes, cands = w["solvent_pool"], w["simulations"], w["routes"], w["candidates"]
    fronts = list(w["fronts"].values())
    screens = w["screens"]
    by_washes = ", ".join(f"{n} with {k} wash{'es' if k != '1' else ''}" for k, n in routes["by_number_of_washes"].items())
    return [
        ("Solvents in scope", pool["costable_in_scope"], str(pool["costable_in_scope"]),
         f"{pool['costable_in_scope'] + pool['costable_outside_scope']} solvents have a price and life-cycle record; "
         f"{pool['costable_outside_scope']} lie outside the \u201c{pool['scope']}\u201d scope"),
        ("Clear the thermodynamic screen", sum(x["passed"] for x in screens), str(sum(x["passed"] for x in screens)),
         "solvent options for a polymer in a stream, over every wash the feed allows"),
        ("Costed (best 3 per polymer and stream)", sum(x["costed"] for x in screens), str(sum(x["costed"] for x in screens)),
         "each one needs its own process simulation"),
        ("BioSTEAM process runs", sims["washes_costed"], str(sims["washes_costed"]),
         f"{sims['live_simulations']} live simulations ({sims['simulated_in_this_call']} run in this call, "
         f"{sims['reused_from_earlier_call']} reused from an earlier call), {sims['stored_records']} from stored records, "
         f"{sims['failed']} failed"),
        ("Wash routes", routes["total"], str(routes["total"]), by_washes),
        ("Candidate pathways", cands["total"], str(cands["total"]), "every route paired with each downstream destination it allows"),
        ("Non-dominated pathways", max(fronts or [0]), " \u00b7 ".join(str(n) for n in fronts),
         "the three fronts: emissions vs profit \u00b7 circularity vs profit \u00b7 emissions vs circularity"),
    ]


def screen_lines(data: dict[str, Any]) -> list[str]:
    """One line per screen: the polymer, the stream it is washed from, and how the solvent pool narrowed."""
    lines = [f"{'wash':<28}{'pool':>5}{'dissolves':>10}{'+cools':>8}{'costed':>8}"]
    for x in _summary(data)["screens"]:
        name = f"{x['polymer']} ({stream_state(x['removed_before'])})"
        lines.append(f"{name:<28}{x['pool']:>5}{x['passed_solubility']:>10}{x['passed']:>8}{x['costed']:>8}"
                     + ("  none" if x["passed"] == 0 else ""))
    return lines


SCREEN_KEY = ("dissolves: target at least 10 wt%, other polymers at most 3 wt%,",
              "  at least 10 \u00b0C below boiling, at some temperature",
              "+cools: and the target falls out of solution on cooling,",
              "  the solvent stays liquid, and (if asked) is not Danger-rated",
              "costed: the best 3 by selectivity, each simulated")


def effort_lines(data: dict[str, Any]) -> list[str]:
    w = _summary(data)
    sims, cands = w["simulations"], w["candidates"]
    fronts = w["fronts"]
    seconds = sims.get("simulation_seconds") or 0.0
    return [f"{sims['live_simulations']} live BioSTEAM simulations"
            + (f", {seconds:.0f} s of simulation in all" if seconds else "")
            + f" ({sims['reused_from_earlier_call']} reused from an earlier call), {sims['stored_records']} from stored records",
            f"{cands['total']} candidate pathways scored on profit, emissions and circularity",
            "selection by an exact MILP, checked by enumeration, for 3 objectives",
            f"exact fronts: emissions\u2013profit {fronts['emissions_vs_profit']}, circularity\u2013profit "
            f"{fronts['circularity_vs_profit']}, emissions\u2013circularity {fronts['emissions_vs_circularity']}",
            f"this call took {w['seconds']:.0f} s"]


def draw_summary(data: dict[str, Any], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    rows = summary_rows(data)
    fig = plt.figure(figsize=(12.6, 7.0), facecolor=SURFACE)
    ax = fig.add_axes((0.02, 0.06, 0.60, 0.80))
    ax.set_facecolor(SURFACE)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(len(rows), 0)
    top = max(r[1] for r in rows) or 1
    for i, (label, size, shown, note) in enumerate(rows):
        width = max(0.10, (size / top) ** 0.5 * 0.9) if size else 0.10
        ax.text(0.0, i + 0.0, label, fontsize=9, color=INK, fontweight="bold", va="top")
        ax.barh(i + 0.48, width, left=(1 - width) / 2, height=0.28, color=FRONT, edgecolor="none")
        ax.text(0.5, i + 0.48, shown, ha="center", va="center", fontsize=10.5, color="#ffffff", fontweight="bold")
        ax.text(0.0, i + 0.80, note, fontsize=7.6, color=INK2, va="center")
    feed = ", ".join(f"{v:.0%} {k}" for k, v in data["feed"]["mass_fractions"].items())
    fig.text(0.015, 0.965, f"What was done for {data['feed']['tonnes_per_year']:,.0f} t/yr ({feed}); "
             f"{data['plant'].get('cost_basis')}", fontsize=11, color=INK, fontweight="bold", va="top")
    side = fig.add_axes((0.645, 0.06, 0.345, 0.80))
    side.axis("off")
    side.set_facecolor(SURFACE)
    axes_height_in = 7.0 * 0.80

    def block(y: float, lines: list[str], size: float, spacing: float, **style: Any) -> float:
        """Draw lines of text with their top at axes fraction ``y``; return the fraction just below the block."""
        side.text(0, y, "\n".join(lines), fontsize=size, va="top", transform=side.transAxes, linespacing=spacing, **style)
        return y - len(lines) * size * spacing / 72.0 / axes_height_in

    y = 1.0
    side.text(0, y, "Screen, per polymer and stream", fontsize=9, color=INK, fontweight="bold", va="top", transform=side.transAxes)
    y = block(y - 0.04, screen_lines(data), 7.2, 1.5, color=INK2, family="monospace")
    y = block(y - 0.02, list(SCREEN_KEY), 6.6, 1.4, color=MUTED, family="monospace")
    side.text(0, y - 0.04, "Effort", fontsize=9, color=INK, fontweight="bold", va="top", transform=side.transAxes)
    wrapped = [row for line in effort_lines(data) for row in textwrap.wrap(f"\u2022 {line}", 62, subsequent_indent="   ")]
    block(y - 0.09, wrapped, 7.8, 1.55, color=INK2)
    fig.text(0.015, 0.02, "Bar widths scale with the square root of each count. Counts describe the work done; they are not results. "
             "Predicted pathways do not establish experimental purity, recovery or economics.", fontsize=7, color=MUTED)
    fig.savefig(out, dpi=300, facecolor=SURFACE)
    plt.close(fig)


def write_summary(data_file: Path, out: Path | None = None) -> Path:
    data = load(data_file)
    figure = out or data_file.with_name(f"{data_file.stem}_work.png")
    draw_summary(data, figure)
    return figure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result", nargs="?", type=Path, help="saved request (default: the latest one)")
    parser.add_argument("--out", type=Path, help="figure file, .png or .pdf (default: next to the request)")
    parser.add_argument("--summary", action="store_true", help="draw the work behind the request instead of the fronts")
    args = parser.parse_args(argv)
    path = args.result or default_path()
    if not path.exists():
        print(f"no saved request at {path}: ask optimize_waste_pathway a question first", file=sys.stderr)
        return 1
    if args.summary:
        print(f"wrote {write_summary(path, args.out)}")
        return 0
    figure, table = write(path, args.out)
    print(f"wrote {figure}\nwrote {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
