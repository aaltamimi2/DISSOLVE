#!/usr/bin/env python3
"""Generate the admitted single-panel Results S1 dataset-landscape drafts."""

from __future__ import annotations

import ast
from collections import defaultdict
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
import xml.etree.ElementTree as ET

import duckdb
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10.0,
        "text.color": "#000000",
        "axes.labelcolor": "#000000",
        "xtick.color": "#000000",
        "ytick.color": "#000000",
        "svg.fonttype": "none",
        "svg.hashsalt": "dissolve-v12-results-s1-dataset-landscape",
    }
)

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.artist import Artist  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.text import Text  # noqa: E402
import numpy as np  # noqa: E402

try:
    from .standards_checker import (
        DataLayerRecord,
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_figure,
        check_output_pair,
    )
except ImportError:  # pragma: no cover
    from standards_checker import (
        DataLayerRecord,
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_figure,
        check_output_pair,
    )


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DB_PATH = ROOT / "src/dissolve/data/thermodynamics.duckdb"
PROPERTY_PATH = ROOT / "src/dissolve/data/Solvent_Data.csv"
READER_PATH = ROOT / "src/dissolve/thermodynamics.py"
TOOLS_PATH = ROOT / "src/dissolve/tools.py"
SEPARATION_PATH = ROOT / "src/dissolve/separation.py"
PROPOSAL_PATH = HERE / "RESULTS_S1_DATASET_LANDSCAPE_REPLACEMENT_PROPOSAL.md"
CHARTER_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/V4_VISUALIZATION_CHARTER.md")
INBOX_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/v4/STEER_INBOX.md")

EXPECTED_SOURCE_DIGESTS = {
    DB_PATH: "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc",
    PROPERTY_PATH: "c9dfd6556ec3f755157b951408a852df44b7cbdc18dea02c36266000d0c9bd49",
    READER_PATH: "2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204",
    TOOLS_PATH: "a9ac03d1412d9be6ebef7155683a0bd8a628fbd59d2e77ad9fd547ff001e5d50",
    SEPARATION_PATH: "f1b05ca8684c6ea6d47b60793f60a5607220f8b7366ccab457931758b9e8f985",
    PROPOSAL_PATH: "14d091c760a72e20444c1210f03f5cca3364a9a03a2f85c9090c625db8a82661",
    CHARTER_PATH: "f6f0a7e0d3d948e66a27202b285d924590ea0bafff39f2475462ca33f45a1a40",
}

COHORT = (
    "PS",
    "PVC",
    "PP",
    "LDPE",
    "PC",
    "HDPE",
    "NYLON6",
    "EVOH",
    "PET",
    "NYLON66",
    "PES",
)
COHORT_SET = frozenset(COHORT)
TEMPERATURES = tuple(range(25, 161, 5))
T5_CATEGORIES: tuple[int | None, ...] = (*TEMPERATURES, None)
THRESHOLD = 5.0
RAW_LOG_EDGES = tuple(float(value) for value in np.linspace(-8.0, 2.0, 51))
EXPECTED_PAYLOAD_DIGESTS = {
    "heat": "cb8fe5502436a47196498e513a716a0eb7e5e60fe749072c8e3b5636eec1c8f5",
    "onset": "50d7efe904d2891216717a514dfd52e7122e3bed7852c82d7db41b5f854a6e10",
    "density": "41c31f2d0471b0fe6dd5424a0c16692ef711360450450491f1b023bb19831eea",
    "density_denominators": "4230900abb5e4dd455510ee3404efa10754744e71fc967bb68cda7a075a8649b",
    "property": "999f5e7d4a48a34eb9710e7d3d3f0616e19165d3d67d12c9a0d43f093a02b9b7",
}

SCALE_STEM = "results_s1_dataset_scale"
COVERAGE_STEM = "results_s1_polymer_pair_coverage"
PROPERTY_STEM = "results_s1_solvent_property_domain"
HEATMAP_STEM = "results_s1_fraction_above_threshold_heatmap"
ONSET_STEM = "results_s1_dissolution_onset_distribution"
DENSITY_STEMS = {polymer: f"results_s1_density_{polymer.lower()}" for polymer in COHORT}
ALL_STEMS = (
    SCALE_STEM,
    COVERAGE_STEM,
    PROPERTY_STEM,
    HEATMAP_STEM,
    ONSET_STEM,
    *DENSITY_STEMS.values(),
)
REPRODUCTION_COMMAND = "PYTHONPATH=src:. python figures/results_s1_dataset_landscape.py"

FONT_SIZE = 10.0
FIGURE_WIDTH = 7.25
RENDER_DPI = 150
PNG_DPI = 300
BLACK = "#000000"
BLUE = "#0072B2"
ORANGE = "#E69F00"
NEUTRAL = "#B8BEC5"
GRID_EDGE = "#FFFFFF"
CMAP = matplotlib.colormaps["viridis"]

SCALAR_SQL = """
SELECT
  COUNT(DISTINCT polymer) FILTER (WHERE polymer <> 'PU') AS cohort_polymers,
  COUNT(DISTINCT solvent) FILTER (WHERE polymer <> 'PU') AS grid_solvents,
  COUNT(DISTINCT (polymer, solvent)) FILTER (WHERE polymer <> 'PU') AS stored_pairs,
  COUNT(*) FILTER (WHERE polymer <> 'PU') AS stored_cells,
  COUNT(*) FILTER (WHERE polymer <> 'PU' AND (is_valid OR invalid_reason = 'exact_100_artifact')) AS evaluable_cells,
  COUNT(*) FILTER (WHERE polymer <> 'PU' AND is_valid) AS raw_valid_cells,
  COUNT(*) FILTER (WHERE polymer <> 'PU' AND invalid_reason = 'exact_100_artifact') AS ceiling_cells,
  COUNT(*) FILTER (WHERE polymer <> 'PU' AND invalid_reason = 'nonpositive') AS refused_cells
FROM solubility_grid
""".strip()

PAIR_SQL = """
SELECT polymer, COUNT(DISTINCT solvent) AS stored_pairs
FROM solubility_grid
WHERE polymer <> 'PU'
GROUP BY polymer
ORDER BY polymer
""".strip()

PROPERTY_SQL = """
SELECT source_row, logp, boiling_point_c
FROM solvent_data
ORDER BY source_row
""".strip()

ADMISSION_SQL = """
SELECT a.interp_key, s.logp,
       COALESCE(s.boiling_point_c, a.boiling_point_c) AS effective_boiling_point_c,
       a.cas_number, a.boiling_point_c, a.hazard_lookup_status
FROM solvent_admission_data a
JOIN solvent_data s ON s.source_row = a.source_property_row
ORDER BY a.interp_key
""".strip()

HEATMAP_SQL = """
SELECT polymer, temperature_c,
       COUNT(*) FILTER (WHERE is_valid OR invalid_reason = 'exact_100_artifact') AS evaluable_denominator,
       COUNT(*) FILTER (WHERE (is_valid OR invalid_reason = 'exact_100_artifact') AND solubility_pct >= 5.0) AS qualifying_numerator
FROM solubility_grid
WHERE polymer <> 'PU'
GROUP BY polymer, temperature_c
ORDER BY polymer, temperature_c
""".strip()

T5_SQL = """
SELECT polymer, solvent,
       MIN(temperature_c) FILTER (
         WHERE (is_valid OR invalid_reason = 'exact_100_artifact')
           AND solubility_pct >= 5.0
       ) AS t5_c
FROM solubility_grid
WHERE polymer <> 'PU'
GROUP BY polymer, solvent
ORDER BY polymer, solvent
""".strip()

DENSITY_SQL = """
SELECT polymer, temperature_c, solubility_pct,
       invalid_reason = 'exact_100_artifact' AS is_served_ceiling
FROM solubility_grid
WHERE polymer <> 'PU'
  AND (is_valid OR invalid_reason = 'exact_100_artifact')
ORDER BY polymer, temperature_c, solvent
""".strip()


class SourceDigestError(AssertionError):
    pass


class DatasetLandscapeValidationError(AssertionError):
    pass


@dataclass(frozen=True)
class HeatCell:
    polymer: str
    temperature_c: int
    denominator: int
    numerator: int

    @property
    def fraction(self) -> float:
        return self.numerator / self.denominator


@dataclass(frozen=True)
class DatasetLandscapeData:
    cohort_polymers: int
    grid_solvents: int
    stored_pairs: int
    stored_cells: int
    evaluable_cells: int
    raw_valid_cells: int
    ceiling_cells: int
    refused_cells: int
    pair_counts: tuple[tuple[str, int], ...]
    property_points: tuple[tuple[float, float], ...]
    admission_points: tuple[tuple[float, float], ...]
    property_record_count: int
    original_bp_count: int
    admission_record_count: int
    heat_cells: tuple[HeatCell, ...]
    onset_counts: tuple[tuple[str, tuple[int, ...]], ...]
    onset_order: tuple[str, ...]
    density_counts: tuple[tuple[str, tuple[tuple[int, ...], ...]], ...]
    density_denominators: tuple[tuple[str, tuple[int, ...]], ...]
    threshold_locations: tuple[dict[str, Any], ...]


@dataclass
class Drawing:
    fig: plt.Figure
    registry: DrawingRegistry
    title: Artist


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_source_digests(
    expected: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS,
) -> dict[str, str]:
    actual: dict[str, str] = {}
    errors: list[str] = []
    for path, wanted in expected.items():
        if not path.is_file():
            errors.append(f"missing source: {path}")
            continue
        observed = sha256_file(path)
        actual[str(path)] = observed
        if observed != wanted:
            errors.append(f"source digest mismatch for {path}: {observed} != {wanted}")
    if errors:
        raise SourceDigestError("\n".join(errors))
    return actual


def _default(path: Path, function: str, parameter: str) -> dict[str, Any]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function
    ]
    if len(nodes) != 1:
        raise DatasetLandscapeValidationError(f"expected one {function} definition")
    node = nodes[0]
    args = [*node.args.posonlyargs, *node.args.args]
    defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
    mapping = dict(zip((arg.arg for arg in args), defaults))
    mapping.update(
        zip((arg.arg for arg in node.args.kwonlyargs), node.args.kw_defaults)
    )
    default_node = mapping.get(parameter)
    if default_node is None:
        raise DatasetLandscapeValidationError(f"missing default {function}.{parameter}")
    value = float(ast.literal_eval(default_node))
    if value != THRESHOLD:
        raise DatasetLandscapeValidationError(
            f"threshold default moved: {function}.{parameter}={value}"
        )
    return {
        "path": str(path),
        "function": function,
        "parameter": parameter,
        "line": default_node.lineno,
        "value": value,
    }


def inspect_thresholds() -> tuple[dict[str, Any], ...]:
    return (
        _default(TOOLS_PATH, "_screen_direction", "solubility_threshold_pct"),
        _default(
            SEPARATION_PATH,
            "screen_precipitation_order",
            "min_dissolution_solubility_wt_pct",
        ),
    )


def measure(
    *, expected_digests: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS
) -> DatasetLandscapeData:
    assert_source_digests(expected_digests)
    threshold_locations = inspect_thresholds()
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        scalar = connection.execute(SCALAR_SQL).fetchone()
        pair_rows = connection.execute(PAIR_SQL).fetchall()
        property_rows = connection.execute(PROPERTY_SQL).fetchall()
        admission_rows = connection.execute(ADMISSION_SQL).fetchall()
        heat_rows = connection.execute(HEATMAP_SQL).fetchall()
        t5_rows = connection.execute(T5_SQL).fetchall()
        density_rows = connection.execute(DENSITY_SQL).fetchall()
    finally:
        connection.close()

    property_points = tuple(
        (float(row[1]), float(row[2]))
        for row in property_rows
        if row[1] is not None and row[2] is not None
    )
    admission_points = tuple((float(row[1]), float(row[2])) for row in admission_rows)
    if any(
        row[3] is None or float(row[4]) < 25.0 or not str(row[5]).startswith("resolved")
        for row in admission_rows
    ):
        raise DatasetLandscapeValidationError(
            "an admission record fails its governed identity/BP/status rule"
        )

    onset: dict[str, list[int]] = {
        polymer: [0] * len(T5_CATEGORIES) for polymer in COHORT_SET
    }
    onset_values: dict[str, list[int]] = defaultdict(list)
    for polymer, _solvent, t5 in t5_rows:
        index = len(TEMPERATURES) if t5 is None else TEMPERATURES.index(int(t5))
        onset[str(polymer)][index] += 1
        onset_values[str(polymer)].append(165 if t5 is None else int(t5))
    order = tuple(
        sorted(
            COHORT_SET,
            key=lambda polymer: (float(np.median(onset_values[polymer])), polymer),
        )
    )

    density: dict[str, list[list[int]]] = {
        polymer: [[0] * 51 for _ in TEMPERATURES] for polymer in COHORT_SET
    }
    density_denominators: dict[str, list[int]] = {
        polymer: [0] * len(TEMPERATURES) for polymer in COHORT_SET
    }
    for polymer, temperature, value, is_ceiling in density_rows:
        p = str(polymer)
        t_index = TEMPERATURES.index(int(temperature))
        density_denominators[p][t_index] += 1
        if bool(is_ceiling):
            bin_index = 50
        else:
            log_value = math.log10(float(value))
            bin_index = min(49, max(0, int(math.floor((log_value + 8.0) / 0.2))))
        density[p][t_index][bin_index] += 1

    data = DatasetLandscapeData(
        cohort_polymers=int(scalar[0]),
        grid_solvents=int(scalar[1]),
        stored_pairs=int(scalar[2]),
        stored_cells=int(scalar[3]),
        evaluable_cells=int(scalar[4]),
        raw_valid_cells=int(scalar[5]),
        ceiling_cells=int(scalar[6]),
        refused_cells=int(scalar[7]),
        pair_counts=tuple((str(row[0]), int(row[1])) for row in pair_rows),
        property_points=property_points,
        admission_points=admission_points,
        property_record_count=len(property_rows),
        original_bp_count=len(property_points),
        admission_record_count=len(admission_rows),
        heat_cells=tuple(
            HeatCell(str(row[0]), int(row[1]), int(row[2]), int(row[3]))
            for row in heat_rows
        ),
        onset_counts=tuple((polymer, tuple(onset[polymer])) for polymer in COHORT),
        onset_order=order,
        density_counts=tuple(
            (polymer, tuple(tuple(row) for row in density[polymer]))
            for polymer in COHORT
        ),
        density_denominators=tuple(
            (polymer, tuple(density_denominators[polymer])) for polymer in COHORT
        ),
        threshold_locations=threshold_locations,
    )
    validate(data)
    return data


def check_density_bin_contract(
    edges: Sequence[float] = RAW_LOG_EDGES,
) -> dict[str, float | int]:
    """Require the admitted 50 raw log bins plus a separate ceiling band."""

    observed = tuple(float(value) for value in edges)
    if observed != RAW_LOG_EDGES:
        raise DatasetLandscapeValidationError(
            "density raw log10 bin edges changed from the admitted -8..2 grid"
        )
    return {
        "raw_bin_count": len(observed) - 1,
        "lower_log10": observed[0],
        "upper_log10": observed[-1],
        "served_ceiling_band_count": 1,
    }


def check_t5_category_contract(
    categories: Sequence[int | None] = T5_CATEGORIES,
) -> dict[str, int]:
    """Reject interpolated crossings and require the explicit never category."""

    observed = tuple(categories)
    if observed != T5_CATEGORIES:
        raise DatasetLandscapeValidationError(
            "T5 categories changed from exact stored nodes plus never by 160"
        )
    return {
        "stored_node_count": len(TEMPERATURES),
        "never_category_count": 1,
    }


def validate(data: DatasetLandscapeData) -> None:
    errors: list[str] = []
    check_density_bin_contract()
    check_t5_category_contract()
    expected_scalars = (11, 990, 8974, 251272, 250527, 246525, 4002, 745)
    observed = (
        data.cohort_polymers,
        data.grid_solvents,
        data.stored_pairs,
        data.stored_cells,
        data.evaluable_cells,
        data.raw_valid_cells,
        data.ceiling_cells,
        data.refused_cells,
    )
    if observed != expected_scalars:
        errors.append(f"cohort scalar counts changed: {observed} != {expected_scalars}")
    if data.stored_cells != data.stored_pairs * len(TEMPERATURES):
        errors.append("stored cells do not close to pairs x nodes")
    if data.evaluable_cells != data.raw_valid_cells + data.ceiling_cells:
        errors.append("evaluable cells do not close to raw plus ceiling")
    if data.stored_cells != data.evaluable_cells + data.refused_cells:
        errors.append("stored cells do not close to evaluable plus refused")
    if dict(data.pair_counts) != {
        **{p: 990 for p in COHORT if p not in {"NYLON6", "PES"}},
        "NYLON6": 32,
        "PES": 32,
    }:
        errors.append("pair coverage changed or PU entered the cohort")
    if (
        data.property_record_count,
        data.original_bp_count,
        data.admission_record_count,
    ) != (1006, 846, 786):
        errors.append("property/admission counts changed")
    if len(data.heat_cells) != 308:
        errors.append(f"heatmap cell count changed: {len(data.heat_cells)} != 308")
    if {cell.polymer for cell in data.heat_cells} != COHORT_SET:
        errors.append("heatmap cohort changed or PU entered")
    if any(
        cell.denominator <= 0 or cell.numerator < 0 or cell.numerator > cell.denominator
        for cell in data.heat_cells
    ):
        errors.append("invalid heatmap numerator/denominator")
    if sum(cell.denominator for cell in data.heat_cells) != data.evaluable_cells:
        errors.append("heatmap evaluable denominators do not close")
    if data.onset_order != COHORT:
        errors.append(f"onset order changed: {data.onset_order} != {COHORT}")
    pair_counts = dict(data.pair_counts)
    for polymer, counts in data.onset_counts:
        if len(counts) != 29 or sum(counts) != pair_counts[polymer]:
            errors.append(f"onset bins do not close for {polymer}")
    denominators = dict(data.density_denominators)
    for polymer, rows in data.density_counts:
        if len(rows) != 28 or any(len(row) != 51 for row in rows):
            errors.append(f"density shape changed for {polymer}")
            continue
        if any(
            sum(row) != denominator
            for row, denominator in zip(rows, denominators[polymer])
        ):
            errors.append(f"density bins do not close for {polymer}")
    if tuple(item["value"] for item in data.threshold_locations) != (5.0, 5.0):
        errors.append("threshold defaults differ from 5.0")
    payloads = {
        "heat": [
            (cell.polymer, cell.temperature_c, cell.denominator, cell.numerator)
            for cell in data.heat_cells
        ],
        "onset": list(data.onset_counts),
        "density": list(data.density_counts),
        "density_denominators": list(data.density_denominators),
        "property": [data.property_points, data.admission_points],
    }
    for name, payload in payloads.items():
        digest = hashlib.sha256(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest()
        if digest != EXPECTED_PAYLOAD_DIGESTS[name]:
            errors.append(f"{name} payload digest changed: {digest}")
    if errors:
        raise DatasetLandscapeValidationError("\n".join(errors))


def _new_figure(
    height: float,
    *,
    axes_rect: tuple[float, float, float, float] = (0.12, 0.14, 0.64, 0.74),
) -> tuple[plt.Figure, plt.Axes, DrawingRegistry]:
    fig = plt.figure(figsize=(FIGURE_WIDTH, height), dpi=RENDER_DPI, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes(axes_rect)
    ax.xaxis.set_gid("axes-scaffold-x")
    ax.yaxis.set_gid("axes-scaffold-y")
    ax.tick_params(labelsize=FONT_SIZE, colors=BLACK)
    for spine in ax.spines.values():
        spine.set_color(BLACK)
        spine.set_linewidth(0.8)
    return fig, ax, DrawingRegistry()


def _title(fig: plt.Figure, ax: plt.Axes, value: str) -> Text:
    box = ax.get_position()
    text = fig.text(
        box.x0 + box.width / 2,
        0.955,
        value,
        ha="center",
        va="top",
        fontsize=FONT_SIZE,
        fontweight="semibold",
        color=BLACK,
    )
    text.set_gid("figure-title")
    return text


def _register_texts(fig: plt.Figure, registry: DrawingRegistry) -> None:
    fig.canvas.draw()
    registry.texts = [
        TextRecord(text, None)
        for text in fig.findobj(match=Text)
        if text.get_visible() and bool(text.get_text())
    ]


def _legend_swatch(
    fig: plt.Figure,
    registry: DrawingRegistry,
    x: float,
    y: float,
    color: Any,
    label: str,
    gid: str,
) -> None:
    patch = Rectangle(
        (x, y - 0.012),
        0.020,
        0.024,
        transform=fig.transFigure,
        facecolor=color,
        edgecolor=BLACK,
        linewidth=0.5,
        clip_on=False,
    )
    patch.set_gid(gid)
    fig.add_artist(patch)
    registry.collision_patches.append(patch)
    registry.data_marks.append(patch)
    text = fig.text(
        x + 0.030, y, label, ha="left", va="center", fontsize=FONT_SIZE, color=BLACK
    )
    text.set_gid(f"{gid}-text")


def draw_dataset_scale(data: DatasetLandscapeData) -> Drawing:
    validate(data)
    fig, ax, registry = _new_figure(4.6, axes_rect=(0.06, 0.08, 0.88, 0.80))
    ax.axis("off")
    title = _title(
        fig, ax, "Dataset scale: property catalog and stored thermodynamic grid"
    )
    entries = (
        (0.18, 0.74, "PROPERTY CATALOG", "1,006\nproperty records"),
        (0.50, 0.74, "ORIGINAL BP", "846\nrecords"),
        (0.82, 0.74, "GOVERNED ADMISSION", "786\nrecords"),
        (0.10, 0.34, "POLYMERS", "11\nPU excluded"),
        (0.30, 0.34, "GRID SOLVENTS", "990\nfitted labels"),
        (0.50, 0.34, "STORED PAIRS", "8,974\npairs"),
        (0.70, 0.34, "TEMPERATURE", "28 nodes\n25–160 °C"),
        (0.90, 0.34, "STORED CELLS", "251,272\n250,527 evaluable"),
    )
    for x, y, heading, body in entries:
        ax.text(
            x,
            y + 0.075,
            heading,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=FONT_SIZE,
            fontweight="semibold",
            color=BLACK,
        )
        ax.text(
            x,
            y - 0.005,
            body,
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=FONT_SIZE,
            color=BLACK,
            linespacing=1.35,
        )
    for x in (0.34, 0.66):
        ax.text(
            x,
            0.74,
            "→",
            transform=ax.transAxes,
            ha="center",
            va="center",
            fontsize=FONT_SIZE,
            color=BLACK,
        )
    ax.text(
        0.90,
        0.17,
        "745 refused nonpositive cells",
        transform=ax.transAxes,
        ha="center",
        va="center",
        fontsize=FONT_SIZE,
        color=BLACK,
    )
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def draw_pair_coverage(data: DatasetLandscapeData) -> Drawing:
    validate(data)
    fig, ax, registry = _new_figure(5.0, axes_rect=(0.17, 0.14, 0.59, 0.74))
    title = _title(fig, ax, "Stored polymer–solvent pair coverage")
    counts = dict(data.pair_counts)
    y = np.arange(len(COHORT))
    bars = ax.barh(
        y, [counts[p] for p in COHORT], color=BLUE, edgecolor="none", height=0.58
    )
    ax.set_yticks(y, COHORT)
    ax.invert_yaxis()
    ax.set_xlim(0, 1120)
    ax.set_xlabel("Stored solubility pairs / 990", fontsize=FONT_SIZE)
    ax.set_xticks((0, 250, 500, 750, 990))
    for polymer, bar in zip(COHORT, bars):
        bar.set_gid(f"pair-coverage-{polymer.lower()}")
        registry.collision_patches.append(bar)
        registry.data_marks.append(bar)
        text = ax.text(
            bar.get_width() + 18,
            bar.get_y() + bar.get_height() / 2,
            f"{counts[polymer]:,}",
            ha="left",
            va="center",
            fontsize=FONT_SIZE,
            color=BLACK,
        )
        text.set_gid(f"pair-count-{polymer.lower()}")
    fig.text(
        0.17,
        0.025,
        "Cohort excludes PU; NYLON6 and PES retain their stored 32-solvent catalogs.",
        fontsize=FONT_SIZE,
        color=BLACK,
    )
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def draw_property_domain(data: DatasetLandscapeData) -> Drawing:
    validate(data)
    fig, ax, registry = _new_figure(4.8)
    title = _title(fig, ax, "Physical domain of the solvent property catalog")
    full = np.asarray(data.property_points)
    admitted = np.asarray(data.admission_points)
    ax.set_xlim(
        min(full[:, 0].min(), admitted[:, 0].min()) - 0.6,
        max(full[:, 0].max(), admitted[:, 0].max()) + 0.6,
    )
    ax.set_ylim(
        min(full[:, 1].min(), admitted[:, 1].min()) - 20,
        max(full[:, 1].max(), admitted[:, 1].max()) + 20,
    )
    ax.set_xlabel("logP", fontsize=FONT_SIZE)
    ax.set_ylabel("Normal boiling point (°C)", fontsize=FONT_SIZE)
    ax.set_xticks((-2, 0, 5, 10, 15, 20))
    ax.set_yticks((-150, 0, 100, 200, 300, 400))
    layers = (
        ("property-catalog", full, NEUTRAL, 0.50, "o"),
        ("governed-admission", admitted, BLUE, 0.72, "s"),
    )
    for name, points, color, alpha, marker in layers:
        artist = ax.scatter(
            points[:, 0],
            points[:, 1],
            s=49,
            marker=marker,
            facecolor=color,
            edgecolor="none",
            alpha=alpha,
            clip_on=True,
        )
        artist.set_gid(f"data-layer-{name}")
        artist.set_label(name)
        registry.data_layers.append(
            DataLayerRecord(
                artist, ax.patch, points[:, 0], points[:, 1], name, marker_size_pt=7.0
            )
        )
    _legend_swatch(
        fig,
        registry,
        0.790,
        0.76,
        NEUTRAL,
        "Catalog: logP + BP\n(n=846)",
        "legend-property",
    )
    _legend_swatch(
        fig,
        registry,
        0.790,
        0.66,
        BLUE,
        "Governed\nadmission\n(n=786)",
        "legend-admission",
    )
    fig.text(0.790, 0.54, "Admission ≠ safety", fontsize=FONT_SIZE, color=BLACK)
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def _matrix_collection(
    ax: plt.Axes,
    matrix: np.ndarray,
    *,
    x_centers: Sequence[float],
    y_centers: Sequence[float],
    gid: str,
    cmap=CMAP,
    norm=Normalize(0, 1),
) -> tuple[PolyCollection, np.ndarray, np.ndarray]:
    verts: list[list[tuple[float, float]]] = []
    colors: list[Any] = []
    xs: list[float] = []
    ys: list[float] = []
    for yi, y in enumerate(y_centers):
        for xi, x in enumerate(x_centers):
            verts.append(
                [
                    (x - 0.5, y - 0.5),
                    (x + 0.5, y - 0.5),
                    (x + 0.5, y + 0.5),
                    (x - 0.5, y + 0.5),
                ]
            )
            colors.append(cmap(norm(float(matrix[yi, xi]))))
            xs.append(float(x))
            ys.append(float(y))
    collection = PolyCollection(
        verts,
        facecolors=colors,
        edgecolors=GRID_EDGE,
        linewidths=0.35,
        closed=True,
        clip_on=True,
    )
    collection.set_gid(gid)
    collection.set_label(gid)
    ax.add_collection(collection)
    return collection, np.asarray(xs), np.asarray(ys)


def _quantitative_legend(
    fig: plt.Figure,
    registry: DrawingRegistry,
    values: Sequence[float],
    *,
    y_start: float = 0.74,
    labels: Sequence[str] | None = None,
) -> None:
    fig.text(
        0.790,
        y_start + 0.07,
        "Fraction",
        fontsize=FONT_SIZE,
        fontweight="semibold",
        color=BLACK,
    )
    shown = labels or tuple(f"{value:.2g}" for value in values)
    for index, (value, label) in enumerate(zip(values, shown)):
        _legend_swatch(
            fig,
            registry,
            0.790,
            y_start - index * 0.065,
            CMAP(value),
            label,
            f"quantitative-legend-{index}",
        )


def draw_fraction_heatmap(data: DatasetLandscapeData) -> Drawing:
    validate(data)
    fig, ax, registry = _new_figure(4.8, axes_rect=(0.16, 0.15, 0.60, 0.72))
    title = _title(fig, ax, "Evaluable solvent fraction ≥5 wt%")
    lookup = {
        (cell.polymer, cell.temperature_c): cell.fraction for cell in data.heat_cells
    }
    matrix = np.asarray(
        [
            [lookup[(polymer, temperature)] for temperature in TEMPERATURES]
            for polymer in COHORT
        ]
    )
    artist, xs, ys = _matrix_collection(
        ax,
        matrix,
        x_centers=np.arange(28),
        y_centers=np.arange(11),
        gid="data-layer-threshold-heatmap",
    )
    registry.data_layers.append(
        DataLayerRecord(artist, ax.patch, xs, ys, "threshold-heatmap")
    )
    ax.set_xlim(-0.5, 27.5)
    ax.set_ylim(10.5, -0.5)
    ax.set_yticks(np.arange(11), COHORT)
    tick_nodes = (25, 50, 75, 100, 125, 150, 160)
    ax.set_xticks(
        [TEMPERATURES.index(value) for value in tick_nodes],
        [str(value) for value in tick_nodes],
    )
    ax.set_xlabel("Stored temperature node (°C)", fontsize=FONT_SIZE)
    _quantitative_legend(fig, registry, (0.0, 0.25, 0.50, 0.75, 1.0))
    fig.text(
        0.790,
        0.35,
        "Runtime-\nevaluable\ndenominator",
        fontsize=FONT_SIZE,
        color=BLACK,
        linespacing=1.3,
    )
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def _onset_color(category: int | None) -> Any:
    return "#7F7F7F" if category is None else CMAP((category - 25) / 135.0)


def draw_onset_distribution(data: DatasetLandscapeData) -> Drawing:
    validate(data)
    fig, ax, registry = _new_figure(5.0, axes_rect=(0.16, 0.14, 0.60, 0.74))
    title = _title(fig, ax, "First stored temperature reaching 5 wt% solubility")
    count_map = dict(data.onset_counts)
    pair_map = dict(data.pair_counts)
    verts: list[list[tuple[float, float]]] = []
    colors: list[Any] = []
    centers_x: list[float] = []
    centers_y: list[float] = []
    for y, polymer in enumerate(COHORT):
        left = 0.0
        for category, count in zip(T5_CATEGORIES, count_map[polymer]):
            width = 100.0 * count / pair_map[polymer]
            if width <= 0:
                continue
            verts.append(
                [
                    (left, y - 0.30),
                    (left + width, y - 0.30),
                    (left + width, y + 0.30),
                    (left, y + 0.30),
                ]
            )
            colors.append(_onset_color(category))
            centers_x.append(left + width / 2)
            centers_y.append(float(y))
            left += width
    collection = PolyCollection(
        verts,
        facecolors=colors,
        edgecolors="white",
        linewidths=0.25,
        closed=True,
        clip_on=True,
    )
    collection.set_gid("data-layer-onset-distribution")
    collection.set_label("onset-distribution")
    ax.add_collection(collection)
    registry.data_layers.append(
        DataLayerRecord(
            collection,
            ax.patch,
            np.asarray(centers_x),
            np.asarray(centers_y),
            "onset-distribution",
        )
    )
    ax.set_xlim(0, 100)
    ax.set_ylim(10.5, -0.5)
    ax.set_yticks(np.arange(11), COHORT)
    ax.set_xlabel("Fraction of stored polymer–solvent pairs (%)", fontsize=FONT_SIZE)
    values = (25, 50, 75, 100, 125, 160)
    fig.text(
        0.790,
        0.83,
        "T5 stored node",
        fontsize=FONT_SIZE,
        fontweight="semibold",
        color=BLACK,
    )
    for index, value in enumerate(values):
        _legend_swatch(
            fig,
            registry,
            0.790,
            0.76 - index * 0.060,
            _onset_color(value),
            f"{value} °C",
            f"onset-legend-{value}",
        )
    _legend_swatch(
        fig,
        registry,
        0.790,
        0.36,
        _onset_color(None),
        "Never by 160 °C",
        "onset-legend-never",
    )
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def draw_density(data: DatasetLandscapeData, polymer: str) -> Drawing:
    validate(data)
    if polymer not in COHORT_SET:
        raise ValueError(polymer)
    fig, ax, registry = _new_figure(4.8, axes_rect=(0.14, 0.15, 0.62, 0.72))
    title = _title(fig, ax, f"Stored solubility density — {polymer}")
    count_rows = np.asarray(dict(data.density_counts)[polymer], dtype=float)
    denominators = np.asarray(dict(data.density_denominators)[polymer], dtype=float)
    matrix = (count_rows / denominators[:, None]).T
    artist, xs, ys = _matrix_collection(
        ax,
        matrix,
        x_centers=np.arange(28),
        y_centers=np.arange(51),
        gid=f"data-layer-density-{polymer.lower()}",
    )
    registry.data_layers.append(
        DataLayerRecord(artist, ax.patch, xs, ys, f"density:{polymer}")
    )
    ax.set_xlim(-0.5, 27.5)
    ax.set_ylim(-0.5, 50.5)
    tick_nodes = (25, 50, 75, 100, 125, 150, 160)
    ax.set_xticks(
        [TEMPERATURES.index(value) for value in tick_nodes],
        [str(value) for value in tick_nodes],
    )
    ax.set_xlabel("Stored temperature node (°C)", fontsize=FONT_SIZE)
    y_logs = (-8, -6, -4, -2, 0)
    y_positions = [int(round((value + 8.0) / 0.2)) for value in y_logs]
    ax.set_yticks(
        [*y_positions, 50], ["10⁻⁸", "10⁻⁶", "10⁻⁴", "10⁻²", "1", "served\nceiling"]
    )
    ax.set_ylabel("Stored solubility (wt%)", fontsize=FONT_SIZE)
    threshold_y = (math.log10(THRESHOLD) + 8.0) / 0.2
    line = ax.axhline(threshold_y, color=BLACK, linewidth=0.8, linestyle="--")
    line.set_gid("threshold-guide")
    line.set_label("threshold-guide")
    registry.connectors.append(line)
    _quantitative_legend(fig, registry, (0.0, 0.25, 0.50, 0.75, 1.0))
    fig.text(
        0.790,
        0.35,
        "Fraction of evaluable\nsolvents per node",
        fontsize=FONT_SIZE,
        color=BLACK,
        linespacing=1.3,
    )
    fig.text(0.790, 0.25, "Dashed: 5 wt%", fontsize=FONT_SIZE, color=BLACK)
    _register_texts(fig, registry)
    return Drawing(fig, registry, title)


def draw_all(data: DatasetLandscapeData) -> dict[str, Drawing]:
    drawings = {
        SCALE_STEM: draw_dataset_scale(data),
        COVERAGE_STEM: draw_pair_coverage(data),
        PROPERTY_STEM: draw_property_domain(data),
        HEATMAP_STEM: draw_fraction_heatmap(data),
        ONSET_STEM: draw_onset_distribution(data),
    }
    drawings.update(
        {DENSITY_STEMS[polymer]: draw_density(data, polymer) for polymer in COHORT}
    )
    return drawings


def verify_geometry(drawing: Drawing) -> dict[str, float | int]:
    return check_figure(
        drawing.fig,
        drawing.registry,
        drawing.title,
        standards=FigureStandards(font_size_pt=FONT_SIZE),
        content_artists=[drawing.fig.axes[0].patch],
    )


def _postprocess_svg(path: Path) -> None:
    namespace = "http://www.w3.org/2000/svg"
    ET.register_namespace("", namespace)
    ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")
    tree = ET.parse(path)
    root = tree.getroot()
    if list(root.iter(f"{{{namespace}}}image")):
        raise StandardsViolation(
            [f"vector landscape SVG contains an image: {path.name}"]
        )
    tree.write(path, encoding="utf-8", xml_declaration=True)


def check_svg_vector_contract(paths: Iterable[str | Path]) -> dict[str, int]:
    violations: list[str] = []
    count = 0
    for raw in paths:
        path = Path(raw)
        count += 1
        root = ET.parse(path).getroot()
        if any(element.tag.endswith("image") for element in root.iter()):
            violations.append(f"SVG contains raster image content: {path.name}")
    if violations:
        raise StandardsViolation(violations)
    return {"svg_count": count}


def _save(
    drawing: Drawing, destination: Path, stem: str
) -> tuple[dict[str, Any], Path, Path]:
    png = destination / f"{stem}.png"
    svg = destination / f"{stem}.svg"
    drawing.fig.savefig(
        png,
        format="png",
        dpi=PNG_DPI,
        facecolor="white",
        edgecolor="none",
        metadata={"Software": "figures/results_s1_dataset_landscape.py"},
    )
    drawing.fig.savefig(
        svg,
        format="svg",
        facecolor="white",
        edgecolor="none",
        metadata={
            "Creator": "figures/results_s1_dataset_landscape.py",
            "Date": "2026-08-26",
            "Title": stem,
        },
    )
    _postprocess_svg(svg)
    return check_output_pair(png, svg), png, svg


def _sources(actual: Mapping[str, str]) -> list[dict[str, Any]]:
    roles = {
        DB_PATH: "numeric grid/property/admission authority",
        PROPERTY_PATH: "property source lineage",
        READER_PATH: "runtime evaluable rule",
        TOOLS_PATH: "5 wt% threshold default",
        SEPARATION_PATH: "5 wt% threshold default",
        PROPOSAL_PATH: "admitted analysis/figure contract",
        CHARTER_PATH: "figure standards and owner stops",
    }
    rows = [
        {
            "path": str(path),
            "role": roles[path],
            "digest_lock": True,
            "expected_sha256": wanted,
            "actual_sha256": actual[str(path)],
        }
        for path, wanted in EXPECTED_SOURCE_DIGESTS.items()
    ]
    rows.append(
        {
            "path": str(INBOX_PATH),
            "role": "append-only steering; not a number source",
            "digest_lock": False,
            "observed_sha256": sha256_file(INBOX_PATH),
        }
    )
    return rows


def _sidecar(
    stem: str,
    data: DatasetLandscapeData,
    actual: Mapping[str, str],
    geometry: Mapping[str, Any],
    output: Mapping[str, Any],
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "artifact_status": "first draft; not a final publication figure",
        "figure": stem,
        "analysis_cohort": {
            "included_polymers": list(COHORT),
            "excluded_polymers": ["PU"],
        },
        "reproduction_command": REPRODUCTION_COMMAND,
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "sources": _sources(actual),
        "queries": {
            "scalars": SCALAR_SQL,
            "pair_coverage": PAIR_SQL,
            "properties": PROPERTY_SQL,
            "admissions": ADMISSION_SQL,
            "heatmap": HEATMAP_SQL,
            "t5": T5_SQL,
            "density": DENSITY_SQL,
        },
        "threshold_locations": list(data.threshold_locations),
        "standards_report": dict(geometry),
        "outputs": {
            "png": {
                "path": f"{stem}.png",
                "bytes": output["png_bytes"],
                "sha256": output["png_sha256"],
            },
            "svg": {
                "path": f"{stem}.svg",
                "bytes": output["svg_bytes"],
                "sha256": output["svg_sha256"],
            },
        },
    }
    if stem == SCALE_STEM:
        common["drawn_values"] = {
            key: getattr(data, key)
            for key in (
                "cohort_polymers",
                "grid_solvents",
                "stored_pairs",
                "stored_cells",
                "evaluable_cells",
                "raw_valid_cells",
                "ceiling_cells",
                "refused_cells",
                "property_record_count",
                "original_bp_count",
                "admission_record_count",
            )
        }
    elif stem == COVERAGE_STEM:
        common["pair_counts"] = dict(data.pair_counts)
    elif stem == PROPERTY_STEM:
        common["point_counts"] = {
            "property_logp_original_bp": len(data.property_points),
            "governed_admission_effective_bp": len(data.admission_points),
        }
    elif stem == HEATMAP_STEM:
        common["cells"] = [
            {
                "polymer": cell.polymer,
                "temperature_c": cell.temperature_c,
                "evaluable_denominator": cell.denominator,
                "qualifying_numerator": cell.numerator,
                "fraction": cell.fraction,
            }
            for cell in data.heat_cells
        ]
        common["polymer_order"] = list(COHORT)
    elif stem == ONSET_STEM:
        common["categories_c"] = [*TEMPERATURES, "never_by_160"]
        common["counts"] = {
            polymer: list(counts) for polymer, counts in data.onset_counts
        }
        common["polymer_order"] = list(COHORT)
    else:
        polymer = next(
            polymer
            for polymer, density_stem in DENSITY_STEMS.items()
            if density_stem == stem
        )
        common["polymer"] = polymer
        common["raw_log10_bin_edges"] = list(RAW_LOG_EDGES)
        common["served_ceiling_band_index"] = 50
        common["bin_counts_by_temperature"] = {
            str(t): list(row)
            for t, row in zip(TEMPERATURES, dict(data.density_counts)[polymer])
        }
        common["evaluable_denominator_by_temperature"] = {
            str(t): value
            for t, value in zip(TEMPERATURES, dict(data.density_denominators)[polymer])
        }
    return common


def generate(output_dir: str | Path = HERE) -> dict[str, Any]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    actual = assert_source_digests()
    data = measure()
    drawings = draw_all(data)
    reports: dict[str, Any] = {}
    svg_paths: list[Path] = []
    try:
        for stem, drawing in drawings.items():
            geometry = verify_geometry(drawing)
            output, png, svg = _save(drawing, destination, stem)
            svg_paths.append(svg)
            sidecar = _sidecar(stem, data, actual, geometry, output)
            sidecar_path = destination / f"{stem}.provenance.json"
            sidecar_path.write_text(
                json.dumps(sidecar, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            reports[stem] = {
                "geometry": geometry,
                "output": output,
                "paths": [str(png), str(svg), str(sidecar_path)],
            }
        reports["svg_contract"] = check_svg_vector_contract(svg_paths)
    finally:
        for drawing in drawings.values():
            plt.close(drawing.fig)
    return reports


def generated_names() -> tuple[str, ...]:
    return tuple(
        f"{stem}.{suffix}"
        for stem in ALL_STEMS
        for suffix in ("png", "svg", "provenance.json")
    )


def main() -> int:
    print(json.dumps(generate(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
