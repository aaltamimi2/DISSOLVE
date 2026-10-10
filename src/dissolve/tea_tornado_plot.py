"""Draw the tornado plot of a tea_tornado result: one panel per TEA/LCA metric, one pair of bars per parameter.

tea_tornado saves every result it computes (``latest_tornado.json`` in ~/.dissolve/pathway_results, or the folder named by
DISSOLVE_PATHWAY_RESULTS_DIR) and, with ``draw_plot=true``, draws and opens this figure. The same code runs from a terminal:

    python3 scripts/plot_tornado.py                       # the latest tornado
    python3 scripts/plot_tornado.py path/to/result.json   # a saved one
    python3 scripts/plot_tornado.py --out tornado.pdf     # PNG or PDF, by extension

This file imports nothing from dissolve and needs matplotlib only to draw. Next to the figure it writes ``<name>.md``, the same
numbers as tables, so nothing in the figure depends on color or on reading a bar length.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

SCHEMA = "dissolve.tea-tornado-data.v1"
# Validated categorical slots 1 and 2 of the dataviz palette: blue for a parameter at its low value, orange at its high value.
INK, INK2, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#8a8985", "#e4e3df", "#fcfcfb"
LOW, HIGH = "#2a78d6", "#eb6834"


def default_path() -> Path:
    return Path(os.environ.get("DISSOLVE_PATHWAY_RESULTS_DIR") or Path.home() / ".dissolve" / "pathway_results") / "latest_tornado.json"


def load(path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text())
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"{path} is not a saved tornado (schema {data.get('schema')!r})")
    return data


def fmt(value: float | None, digits: int = 3) -> str:
    """A number with a few significant digits and no scientific notation for ordinary sizes."""
    if value is None:
        return "n/a"
    if value != 0 and (abs(value) >= 1e6 or abs(value) < 1e-3):
        return f"{value:.{digits - 1}e}"
    return f"{value:,.{digits}g}" if abs(value) < 1000 else f"{value:,.0f}"


def ordered_rows(data: dict[str, Any], metric: str) -> list[dict[str, Any]]:
    """The parameters of one panel, the one moving the metric most first; parameters with no result sort last."""
    return sorted(data["rows"], key=lambda r: -(r["metrics"][metric]["swing"] or 0.0))


def range_label(row: dict[str, Any]) -> str:
    unit = f" {row['unit']}" if row["unit"] else ""
    return f"{row['label']}\n{fmt(row['low_value'])} – {fmt(row['high_value'])}{unit}"


def tornado_markdown(data: dict[str, Any]) -> str:
    base = data["base"]
    lines = [f"# Tornado: {data['polymer']} washed in {data['solvent']}", "",
             f"Base plant: {base['target_mass_percent']:g} wt% target, {base['processing_capacity_mt_per_yr']:,.0f} t/yr, dissolution at "
             f"{base['dissolution_temperature_c']:g} °C, solvent at ${base['solvent_price_usd_per_kg']:g}/kg; basis of the runs: "
             f"{data['basis']}.", ""]
    if base.get("assumed_by_default"):
        lines += [f"Assumed by default (not given): {', '.join(base['assumed_by_default'])}.", ""]
    for meta in data["metrics"]:
        m = meta["metric"]
        lines += [f"## {meta['label']} ({meta['unit']}); base {fmt(base['metrics'][m])}", "",
                  "| Parameter | Low value | High value | Result at low | Result at high | Swing | Swing (% of base) |",
                  "|---|---|---|---|---|---|---|"]
        for r in ordered_rows(data, m):
            cell = r["metrics"][m]
            pct = "n/a" if cell["swing_pct_of_base"] is None else f"{cell['swing_pct_of_base']:.1f}%"
            lines.append(f"| {r['label']} ({r['unit']}) | {fmt(r['low_value'])} | {fmt(r['high_value'])} | {fmt(cell['at_low'])} | "
                         f"{fmt(cell['at_high'])} | {fmt(cell['swing'])} | {pct} |")
        lines.append("")
    lines.append("One parameter is moved at a time; interactions are not shown, and the ranking depends on the ranges. Results describe "
                 "one simulated wash plant, gate to gate, not a pathway's profit or a plant measurement.")
    return "\n".join(lines) + "\n"


def draw(data: dict[str, Any], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    metrics = data["metrics"]
    count = len(metrics)
    columns = min(count, 2)
    grid_rows = (count + columns - 1) // columns
    rows_per_panel = len(data["rows"])
    panel_h = 0.62 * rows_per_panel + 1.5
    fig, axes = plt.subplots(grid_rows, columns, figsize=(6.6 * columns, panel_h * grid_rows + 1.5), facecolor=SURFACE, squeeze=False)
    base = data["base"]
    for ax, meta in zip(axes.flat, metrics):
        m = meta["metric"]
        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(colors=INK2, labelsize=8, length=3)
        ax.grid(True, axis="x", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        rows = ordered_rows(data, m)
        base_value = base["metrics"][m]
        span = [base_value]
        for i, r in enumerate(rows):
            cell = r["metrics"][m]
            for value, color in ((cell["at_low"], LOW), (cell["at_high"], HIGH)):
                if value is None:
                    continue
                span.append(value)
                ax.barh(i, value - base_value, left=base_value, height=0.62, color=color, edgecolor="none", zorder=3)
            if any(v is None for v in (cell["at_low"], cell["at_high"])):
                ax.text(base_value, i + 0.36, "one side failed", ha="center", va="top", fontsize=6.5, color=MUTED)
        ax.axvline(base_value, color=INK, linewidth=1.0, zorder=5)
        pad = (max(span) - min(span)) * 0.22 or abs(base_value) * 0.05 or 1.0
        ax.set_xlim(min(span) - pad, max(span) + pad)
        ax.set_ylim(len(rows) - 0.5, -0.5)
        tiny = 0.04 * (ax.get_xlim()[1] - ax.get_xlim()[0])
        for i, r in enumerate(rows):  # value labels at the bar ends; bars too short to label apart get one label
            cell = r["metrics"][m]
            found = [v for v in (cell["at_low"], cell["at_high"]) if v is not None]
            if not found:
                continue
            if (cell["swing_pct_of_base"] or 0.0) < 0.05 and len(found) == 2:
                ax.text(max(found + [base_value]), i, "  no measurable change", ha="left", va="center", fontsize=7.2, color=MUTED, zorder=4)
            elif len(found) == 2 and abs(found[1] - found[0]) < tiny:
                ax.text(max(found + [base_value]), i, f"  {fmt(cell['at_low'], 4)} / {fmt(cell['at_high'], 4)}", ha="left", va="center",
                        fontsize=7.2, color=INK2, zorder=4)
            else:
                for value in found:
                    ax.text(value, i, f" {fmt(value)} " if value >= base_value else f" {fmt(value)} ", ha="left" if value >= base_value else "right",
                            va="center", fontsize=7.2, color=INK2, zorder=4)
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([range_label(r) for r in rows], fontsize=7.6, color=INK, linespacing=1.15)
        ax.set_xlabel(f"{meta['label']} ({meta['unit']})", fontsize=8.5, color=INK2)
        ax.set_title(f"{meta['label']}  ·  base {fmt(base_value)}", loc="left", fontsize=9.5, color=INK, fontweight="bold")
    for ax in list(axes.flat)[count:]:
        ax.axis("off")
    fig.suptitle(f"Sensitivity of {data['polymer']} washed in {data['solvent']} (BioSTEAM TEA/LCA)", x=0.01, ha="left", fontsize=11,
                 color=INK, fontweight="bold")
    fig.legend(handles=[Patch(color=LOW, label="Parameter at its low value"), Patch(color=HIGH, label="Parameter at its high value")],
               loc="lower left", bbox_to_anchor=(0.01, 0.0), ncol=2, frameon=False, fontsize=8.5, labelcolor=INK2)
    fig.text(0.99, 0.012, f"Base: {base['target_mass_percent']:g} wt% target, {base['processing_capacity_mt_per_yr']:,.0f} t/yr, "
             f"{base['dissolution_temperature_c']:g} °C. One parameter moved at a time; not a pathway's profit.", fontsize=7,
             color=MUTED, ha="right", va="bottom")
    fig.tight_layout(rect=(0, 0.045, 1, 0.95), h_pad=2.0, w_pad=2.5)
    fig.savefig(out, dpi=300, facecolor=SURFACE)
    plt.close(fig)


def write(data_file: Path, out: Path | None = None) -> tuple[Path, Path]:
    """Draw one saved tornado and write its table; returns (figure, table)."""
    data = load(data_file)
    figure = out or data_file.with_suffix(".png")
    draw(data, figure)
    table = figure.with_suffix(".md")
    table.write_text(tornado_markdown(data))
    return figure, table


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result", nargs="?", type=Path, help="saved tornado (default: the latest one)")
    parser.add_argument("--out", type=Path, help="figure file, .png or .pdf (default: next to the saved tornado)")
    args = parser.parse_args(argv)
    path = args.result or default_path()
    if not path.exists():
        print(f"no saved tornado at {path}: run tea_tornado first", file=sys.stderr)
        return 1
    figure, table = write(path, args.out)
    print(f"wrote {figure}\nwrote {table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
