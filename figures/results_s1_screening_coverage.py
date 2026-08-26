#!/usr/bin/env python3
"""Generate the admitted Results §1 screening-coverage first drafts.

This script draws two separately named, single-axes figures.  It never queries
solubility values and never calls an engine screen.

Figure/source map
-----------------
``results_s1_stored_pair_catalog``
    Polymer labels and bar lengths come from ``PAIR_COUNTS_SQL`` against
    ``src/dissolve/data/thermodynamics.duckdb``.  ``PAIR_LENGTH_SQL`` and
    ``TEMPERATURE_NODES_SQL`` establish that every stored pair has 28 stored
    rows on the exact 25--160 degC, 5 degC node sequence.

``results_s1_envelope_cell_fates``
    Raw-valid, ``exact_100_artifact``, and ``nonpositive`` counts come from
    ``DISPOSITIONS_SQL`` against the same DuckDB.  The runtime classification
    rule is pinned to ``src/dissolve/thermodynamics.py``: raw-valid and
    ``exact_100_artifact`` cells are evaluable; ``nonpositive`` cells are
    refused.  Not-measured cells are the asserted ragged-pair hole in the full
    12 x 990 x 28 envelope.

Reproduce from the repository root with::

    PYTHONPATH=src:. python figures/results_s1_screening_coverage.py

The outputs are first drafts, not final publication figures.  Representative
S(T) pairs remain an owner decision and no curves are drawn here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import duckdb
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10.0,
    "svg.fonttype": "none",
    "svg.hashsalt": "dissolve-v12-results-s1-screening-coverage",
})

import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import Ellipse, Rectangle

try:  # Package import in tests; direct import for the documented CLI.
    from .standards_checker import (
        DrawingRegistry,
        FigureStandards,
        TextRecord,
        check_figure,
        check_output_pair,
    )
except ImportError:  # pragma: no cover - exercised by the documented CLI
    from standards_checker import (
        DrawingRegistry,
        FigureStandards,
        TextRecord,
        check_figure,
        check_output_pair,
    )


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / "src/dissolve/data/thermodynamics.duckdb"
READER_PATH = ROOT / "src/dissolve/thermodynamics.py"
PROPOSAL_PATH = HERE / "RESULTS_S1_SCREENING_COVERAGE_PROPOSAL.md"
CHARTER_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/V4_VISUALIZATION_CHARTER.md")
INBOX_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/v4/STEER_INBOX.md")
STEER_PATH = Path(
    "/home/aaltamimi2/dissolve-v12-audit/RESULTS_S1_COVERAGE_FIGURE_STEER.md"
)

PAIR_STEM = "results_s1_stored_pair_catalog"
FATE_STEM = "results_s1_envelope_cell_fates"
REPRODUCTION_COMMAND = (
    "PYTHONPATH=src:. python figures/results_s1_screening_coverage.py"
)

EXPECTED_SOURCE_DIGESTS = {
    DB_PATH: "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc",
    READER_PATH: "2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204",
    PROPOSAL_PATH: "dc1991c3682997db46576c39c6aa0831fa20f3088a8b654de68402a525c15841",
    CHARTER_PATH: "f6f0a7e0d3d948e66a27202b285d924590ea0bafff39f2475462ca33f45a1a40",
    INBOX_PATH: "0f4107c3febd9e81e1ccac7bdfcce5815d8decb675f24f2b07851fae5c512941",
    STEER_PATH: "a0b8a7337df049b3abd9bfe2330d16cb6027cd7aabbf80d6ba767bf4b5d4b49a",
}

PAIR_COUNTS_SQL = """
SELECT polymer,
       COUNT(DISTINCT solvent) AS stored_pairs,
       COUNT(*) AS stored_cells,
       COUNT(DISTINCT temperature_c) AS temperature_nodes
FROM solubility_grid
GROUP BY polymer
ORDER BY stored_pairs DESC, polymer
""".strip()

PAIR_LENGTH_SQL = """
SELECT point_count, COUNT(*) AS stored_pairs
FROM (
    SELECT polymer, solvent, COUNT(*) AS point_count
    FROM solubility_grid
    GROUP BY polymer, solvent
)
GROUP BY point_count
ORDER BY point_count
""".strip()

TEMPERATURE_NODES_SQL = """
SELECT DISTINCT temperature_c
FROM solubility_grid
ORDER BY temperature_c
""".strip()

DISPOSITIONS_SQL = """
SELECT is_valid,
       COALESCE(invalid_reason, '<NULL>') AS invalid_reason,
       COUNT(*) AS cells
FROM solubility_grid
GROUP BY is_valid, invalid_reason
ORDER BY is_valid DESC, invalid_reason
""".strip()

EXPECTED_PAIR_COUNTS = {
    "EVOH": 990,
    "HDPE": 990,
    "LDPE": 990,
    "NYLON66": 990,
    "PC": 990,
    "PET": 990,
    "PP": 990,
    "PS": 990,
    "PVC": 990,
    "PU": 78,
    "NYLON6": 32,
    "PES": 32,
}
EXPECTED_NODES = tuple(range(25, 161, 5))

FIGURE_WIDTH_IN = 7.25
PAIR_HEIGHT_IN = 5.40
FATE_HEIGHT_IN = 4.10
RENDER_DPI = 150
PNG_DPI = 300
FONT_SIZE_PT = 10.0

BLACK = "#000000"
BLUE = "#0072B2"
GREEN = "#009E73"
VERMILLION = "#D55E00"
NEUTRAL = "#B8BEC5"
PANEL_EDGE = "#8E969E"


class SourceDigestError(AssertionError):
    """A named source no longer matches the admitted digest."""


class CoverageValidationError(AssertionError):
    """Stored coverage no longer satisfies the admitted arithmetic."""


@dataclass(frozen=True)
class CoverageData:
    """Only stored counts and runtime dispositions; never S(T) values."""

    pair_counts: tuple[tuple[str, int], ...]
    polymer_count: int
    solvent_count: int
    temperature_nodes: tuple[int, ...]
    pair_length_distribution: tuple[tuple[int, int], ...]
    stored_pair_count: int
    stored_cell_count: int
    raw_valid_cells: int
    exact_100_artifact_cells: int
    nonpositive_cells: int
    evaluable_cells: int
    refused_cells: int
    not_measured_cells: int
    envelope_cells: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_source_digests(
    expected: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS,
) -> dict[str, str]:
    """Hash every authority before opening the numeric asset."""

    actual: dict[str, str] = {}
    errors: list[str] = []
    for path, expected_digest in expected.items():
        if not path.is_file():
            errors.append(f"missing source: {path}")
            continue
        observed = sha256_file(path)
        actual[str(path)] = observed
        if observed != expected_digest:
            errors.append(
                f"source digest mismatch for {path}: "
                f"{observed} != {expected_digest}"
            )
    if errors:
        raise SourceDigestError("\n".join(errors))
    return actual


def measure(
    *,
    expected_digests: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS,
) -> CoverageData:
    """Read coverage counts with direct, read-only SQL after digest checks."""

    assert_source_digests(expected_digests)
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        pair_rows = connection.execute(PAIR_COUNTS_SQL).fetchall()
        pair_lengths = connection.execute(PAIR_LENGTH_SQL).fetchall()
        node_rows = connection.execute(TEMPERATURE_NODES_SQL).fetchall()
        disposition_rows = connection.execute(DISPOSITIONS_SQL).fetchall()
        polymer_count, solvent_count, stored_cells = connection.execute(
            "SELECT COUNT(DISTINCT polymer), COUNT(DISTINCT solvent), COUNT(*) "
            "FROM solubility_grid"
        ).fetchone()
    finally:
        connection.close()

    pair_counts = tuple((str(row[0]), int(row[1])) for row in pair_rows)
    stored_pair_count = sum(value for _, value in pair_counts)
    nodes = tuple(int(row[0]) for row in node_rows)
    dispositions = {
        (bool(row[0]), str(row[1])): int(row[2])
        for row in disposition_rows
    }
    raw_valid = dispositions.get((True, "<NULL>"), 0)
    exact_100 = dispositions.get((False, "exact_100_artifact"), 0)
    nonpositive = dispositions.get((False, "nonpositive"), 0)
    envelope = int(polymer_count) * int(solvent_count) * len(nodes)
    data = CoverageData(
        pair_counts=pair_counts,
        polymer_count=int(polymer_count),
        solvent_count=int(solvent_count),
        temperature_nodes=nodes,
        pair_length_distribution=tuple(
            (int(point_count), int(count))
            for point_count, count in pair_lengths
        ),
        stored_pair_count=stored_pair_count,
        stored_cell_count=int(stored_cells),
        raw_valid_cells=raw_valid,
        exact_100_artifact_cells=exact_100,
        nonpositive_cells=nonpositive,
        evaluable_cells=raw_valid + exact_100,
        refused_cells=nonpositive,
        not_measured_cells=envelope - int(stored_cells),
        envelope_cells=envelope,
    )
    validate(data)
    return data


def validate(data: CoverageData) -> None:
    """Assert every admitted count and the sparse-pair identity."""

    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(message)

    observed_pairs = dict(data.pair_counts)
    require(
        observed_pairs == EXPECTED_PAIR_COUNTS,
        f"stored pair counts changed: {observed_pairs!r}",
    )
    require(data.polymer_count == 12, f"polymer count changed: {data.polymer_count}")
    require(data.solvent_count == 990, f"solvent count changed: {data.solvent_count}")
    require(
        data.temperature_nodes == EXPECTED_NODES,
        f"temperature nodes changed: {data.temperature_nodes!r}",
    )
    require(
        data.pair_length_distribution == ((28, 9052),),
        f"stored pair lengths changed: {data.pair_length_distribution!r}",
    )
    require(data.stored_pair_count == 9052, "stored pair total is not 9,052")
    require(data.stored_cell_count == 253456, "stored cell total is not 253,456")
    require(data.raw_valid_cells == 248378, "raw-valid total is not 248,378")
    require(
        data.exact_100_artifact_cells == 4096,
        "exact_100_artifact total is not 4,096",
    )
    require(data.nonpositive_cells == 982, "nonpositive total is not 982")
    require(data.evaluable_cells == 252474, "evaluable total is not 252,474")
    require(data.refused_cells == 982, "measurement-refused total is not 982")
    require(data.not_measured_cells == 79184, "not-measured total is not 79,184")
    require(data.envelope_cells == 332640, "Cartesian envelope is not 332,640")
    require(
        data.evaluable_cells + data.refused_cells + data.not_measured_cells
        == data.envelope_cells,
        "three cell fates do not sum to the envelope",
    )
    sparse_hole = (
        (data.solvent_count - observed_pairs.get("PU", -1))
        + (data.solvent_count - observed_pairs.get("NYLON6", -1))
        + (data.solvent_count - observed_pairs.get("PES", -1))
    ) * len(data.temperature_nodes)
    require(
        sparse_hole == data.not_measured_cells == 79184,
        "79,184 sparse-pair hole identity failed",
    )
    require(
        data.raw_valid_cells + data.exact_100_artifact_cells
        == data.evaluable_cells,
        "runtime evaluable classification changed",
    )
    require(
        data.refused_cells == data.nonpositive_cells,
        "runtime refused classification changed",
    )
    if errors:
        raise CoverageValidationError("\n".join(errors))


def _new_canvas(height_in: float) -> tuple[plt.Figure, plt.Axes, DrawingRegistry, Artist]:
    fig = plt.figure(
        figsize=(FIGURE_WIDTH_IN, height_in),
        dpi=RENDER_DPI,
        facecolor="white",
    )
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    registry = DrawingRegistry()
    panel = Rectangle(
        (0.035, 0.025),
        0.930,
        0.875,
        transform=ax.transAxes,
        facecolor="white",
        edgecolor=PANEL_EDGE,
        linewidth=0.8,
        zorder=0,
    )
    ax.add_patch(panel)
    registry.panels["content"] = panel
    return fig, ax, registry, panel


def _add_text(
    ax: plt.Axes,
    registry: DrawingRegistry,
    x: float,
    y: float,
    value: str,
    *,
    container: Artist | None,
    ha: str = "left",
    va: str = "center",
    weight: str = "normal",
    linespacing: float = 1.22,
) -> Artist:
    artist = ax.text(
        x,
        y,
        value,
        transform=ax.transAxes,
        ha=ha,
        va=va,
        fontsize=FONT_SIZE_PT,
        fontfamily="DejaVu Sans",
        fontweight=weight,
        color=BLACK,
        linespacing=linespacing,
        zorder=10,
    )
    registry.texts.append(TextRecord(artist, container))
    return artist


def draw_pair_catalog(
    data: CoverageData,
) -> tuple[plt.Figure, DrawingRegistry, Artist]:
    """Draw the separately named stored-pair catalog figure."""

    validate(data)
    fig, ax, registry, panel = _new_canvas(PAIR_HEIGHT_IN)
    title = _add_text(
        ax,
        registry,
        0.5,
        0.965,
        "Stored solubility-pair catalog by polymer",
        container=None,
        ha="center",
        va="top",
        weight="semibold",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.862,
        "Nine polymers share 990 stored solvents",
        container=panel,
        ha="center",
        weight="semibold",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.820,
        "Every stored pair contains 28 nodes: 25–160 °C in 5 °C increments",
        container=panel,
        ha="center",
    )

    x_start = 0.235
    max_width = 0.585
    bar_height = 0.023
    y_start = 0.765
    y_step = 0.0475
    for index, (polymer, stored_pairs) in enumerate(data.pair_counts):
        y = y_start - index * y_step
        _add_text(
            ax, registry, 0.195, y, polymer,
            container=panel, ha="right", weight="semibold",
        )
        width = max_width * stored_pairs / data.solvent_count
        bar = Rectangle(
            (x_start, y - bar_height / 2),
            width,
            bar_height,
            transform=ax.transAxes,
            facecolor=BLUE,
            edgecolor="none",
            zorder=3,
        )
        bar.set_gid(f"stored-pairs-{polymer.lower()}")
        ax.add_patch(bar)
        registry.collision_patches.append(bar)
        registry.data_marks.append(bar)
        _add_text(
            ax,
            registry,
            x_start + width + 0.018,
            y,
            f"{stored_pairs:,}",
            container=panel,
        )

    _add_text(
        ax,
        registry,
        0.5275,
        0.160,
        "stored solubility pairs / 990",
        container=panel,
        ha="center",
        weight="semibold",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.108,
        "PU, NYLON6 and PES have ragged solvent catalogs; stored pairs still span all 28 nodes",
        container=panel,
        ha="center",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.055,
        "Sources: thermodynamics.duckdb 4aa3adc7…  ·  thermodynamics.py 2a22603c…",
        container=panel,
        ha="center",
    )
    return fig, registry, title


def _circle_dimensions(diameter_pt: float, height_in: float) -> tuple[float, float]:
    return (
        diameter_pt / (FIGURE_WIDTH_IN * 72.0),
        diameter_pt / (height_in * 72.0),
    )


def draw_cell_fates(
    data: CoverageData,
) -> tuple[plt.Figure, DrawingRegistry, Artist]:
    """Draw three runtime cell fates with marker area proportional to count."""

    validate(data)
    fig, ax, registry, panel = _new_canvas(FATE_HEIGHT_IN)
    title = _add_text(
        ax,
        registry,
        0.5,
        0.965,
        "Stored-grid envelope: three cell fates",
        container=None,
        ha="center",
        va="top",
        weight="semibold",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.858,
        "Circle area is proportional to cell count within 12 × 990 × 28 stored-grid positions",
        container=panel,
        ha="center",
    )
    _add_text(
        ax,
        registry,
        0.5,
        0.803,
        "79,184 = missing PU, NYLON6 and PES solvent pairs × all 28 temperature nodes",
        container=panel,
        ha="center",
        weight="semibold",
    )

    largest_diameter_pt = 116.0
    fate_specs = (
        (
            "evaluable",
            0.20,
            data.evaluable_cells,
            GREEN,
            "Evaluable",
            f"{data.evaluable_cells:,}",
            f"{data.raw_valid_cells:,} raw-valid\n"
            f"+ {data.exact_100_artifact_cells:,} exact-100 served",
        ),
        (
            "measurement-refused",
            0.50,
            data.refused_cells,
            VERMILLION,
            "Measurement-refused",
            f"{data.refused_cells:,}",
            "nonpositive only",
        ),
        (
            "not-measured",
            0.80,
            data.not_measured_cells,
            NEUTRAL,
            "Not measured",
            f"{data.not_measured_cells:,}",
            "whole missing pairs\n× 28 nodes",
        ),
    )
    for gid, x, count, color, label, count_label, detail in fate_specs:
        diameter_pt = largest_diameter_pt * math.sqrt(
            count / data.evaluable_cells
        )
        width, height = _circle_dimensions(diameter_pt, FATE_HEIGHT_IN)
        circle = Ellipse(
            (x, 0.548),
            width,
            height,
            transform=ax.transAxes,
            facecolor=color,
            edgecolor="none",
            zorder=3,
        )
        circle.set_gid(f"cell-fate-{gid}")
        ax.add_patch(circle)
        registry.collision_patches.append(circle)
        registry.data_marks.append(circle)
        _add_text(
            ax, registry, x, 0.295, label,
            container=panel, ha="center", weight="semibold",
        )
        _add_text(
            ax, registry, x, 0.238, count_label,
            container=panel, ha="center", weight="semibold",
        )
        _add_text(
            ax, registry, x, 0.157, detail,
            container=panel, ha="center", linespacing=1.30,
        )

    _add_text(
        ax,
        registry,
        0.5,
        0.055,
        "Sources: thermodynamics.duckdb 4aa3adc7…  ·  thermodynamics.py 2a22603c…",
        container=panel,
        ha="center",
    )
    return fig, registry, title


def draw(
    data: CoverageData,
) -> dict[str, tuple[plt.Figure, DrawingRegistry, Artist]]:
    """Draw both single-panel figures without composing them."""

    return {
        PAIR_STEM: draw_pair_catalog(data),
        FATE_STEM: draw_cell_fates(data),
    }


def verify_geometry(
    fig: plt.Figure,
    registry: DrawingRegistry,
    title: Artist,
) -> dict[str, float | int]:
    return check_figure(
        fig,
        registry,
        title,
        standards=FigureStandards(font_size_pt=FONT_SIZE_PT),
    )


def _save_pair(
    fig: plt.Figure,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path, dict[str, int | str]]:
    png_path = output_dir / f"{stem}.png"
    svg_path = output_dir / f"{stem}.svg"
    fig.savefig(
        png_path,
        format="png",
        dpi=PNG_DPI,
        facecolor="white",
        edgecolor="none",
        metadata={"Software": "figures/results_s1_screening_coverage.py"},
    )
    fig.savefig(
        svg_path,
        format="svg",
        facecolor="white",
        edgecolor="none",
        metadata={
            "Creator": "figures/results_s1_screening_coverage.py",
            "Date": "2026-08-26",
            "Title": stem,
        },
    )
    return png_path, svg_path, check_output_pair(png_path, svg_path)


def _source_manifest(actual: Mapping[str, str]) -> list[dict[str, str]]:
    roles = {
        DB_PATH: "numeric stored-grid authority",
        READER_PATH: "runtime disposition authority",
        PROPOSAL_PATH: "admitted figure/source proposal",
        CHARTER_PATH: "figure standards and owner stops",
        INBOX_PATH: "durable current steering",
        STEER_PATH: "non-numeric coverage framing",
    }
    return [
        {
            "path": str(path),
            "role": roles[path],
            "expected_sha256": expected,
            "actual_sha256": actual[str(path)],
        }
        for path, expected in EXPECTED_SOURCE_DIGESTS.items()
    ]


def _sidecar_payload(
    *,
    stem: str,
    data: CoverageData,
    source_digests: Mapping[str, str],
    standards_report: Mapping[str, float | int],
    output_report: Mapping[str, int | str],
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "artifact_status": "first draft; not a final publication figure",
        "figure": stem,
        "representative_curves": "not drawn; owner pair selection pending",
        "reproduction_command": REPRODUCTION_COMMAND,
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "sources": _source_manifest(source_digests),
        "standards_report": dict(standards_report),
        "outputs": {
            "png": {
                "path": f"{stem}.png",
                "bytes": output_report["png_bytes"],
                "sha256": output_report["png_sha256"],
            },
            "svg": {
                "path": f"{stem}.svg",
                "bytes": output_report["svg_bytes"],
                "sha256": output_report["svg_sha256"],
            },
        },
    }
    if stem == PAIR_STEM:
        common.update({
            "queries": {
                "pair_counts": PAIR_COUNTS_SQL,
                "pair_lengths": PAIR_LENGTH_SQL,
                "temperature_nodes": TEMPERATURE_NODES_SQL,
            },
            "field_map": {
                "polymer labels and bar lengths": "pair_counts.polymer, pair_counts.stored_pairs",
                "990 denominator": "COUNT(DISTINCT solvent) from solubility_grid",
                "28 nodes and 25–160 degC range": "temperature_nodes plus pair_lengths",
            },
            "drawn_values": {
                "pair_counts": dict(data.pair_counts),
                "stored_pair_total": data.stored_pair_count,
                "temperature_nodes": list(data.temperature_nodes),
            },
        })
    elif stem == FATE_STEM:
        common.update({
            "queries": {
                "pair_counts": PAIR_COUNTS_SQL,
                "temperature_nodes": TEMPERATURE_NODES_SQL,
                "raw_dispositions": DISPOSITIONS_SQL,
            },
            "field_map": {
                "evaluable": "raw valid + exact_100_artifact under thermodynamics.py:get_connection",
                "measurement-refused": "nonpositive only",
                "not measured": "12 x 990 x 28 envelope minus stored cells; sparse-pair identity",
            },
            "drawn_values": {
                "polymer_count": data.polymer_count,
                "solvent_count": data.solvent_count,
                "temperature_node_count": len(data.temperature_nodes),
                "raw_valid_cells": data.raw_valid_cells,
                "exact_100_artifact_served_cells": data.exact_100_artifact_cells,
                "evaluable_cells": data.evaluable_cells,
                "measurement_refused_nonpositive_cells": data.refused_cells,
                "not_measured_cells": data.not_measured_cells,
                "envelope_cells": data.envelope_cells,
                "sparse_hole_identity": "((990-78)+(990-32)+(990-32))*28=79184",
            },
        })
    else:  # pragma: no cover - internal call contract
        raise ValueError(f"unknown figure stem: {stem}")
    return common


def generate(output_dir: str | Path = HERE) -> dict[str, dict[str, Any]]:
    """Measure, validate, draw, check, and save both admitted draft figures."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    source_digests = assert_source_digests()
    data = measure()
    drawings = draw(data)
    reports: dict[str, dict[str, Any]] = {}
    for stem, (fig, registry, title) in drawings.items():
        try:
            standards_report = verify_geometry(fig, registry, title)
            png_path, svg_path, output_report = _save_pair(fig, destination, stem)
            sidecar = _sidecar_payload(
                stem=stem,
                data=data,
                source_digests=source_digests,
                standards_report=standards_report,
                output_report=output_report,
            )
            sidecar_path = destination / f"{stem}.provenance.json"
            sidecar_path.write_text(
                json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            reports[stem] = {
                "geometry": standards_report,
                "output_pair": output_report,
                "paths": [str(png_path), str(svg_path), str(sidecar_path)],
            }
        finally:
            plt.close(fig)
    return reports


def main() -> int:
    reports = generate()
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
