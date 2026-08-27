#!/usr/bin/env python3
"""Generate the admitted Results section 1 stored-solubility landscape drafts.

The module performs read-only SQL against the digest-locked stored grid and
AST-only inspection of the two source defaults that establish the 5 wt%
threshold.  It never imports or calls a screen, never interpolates, and never
selects a representative polymer--solvent pair.

Reproduce from the repository root with::

    PYTHONPATH=src:. python figures/results_s1_landscape.py
"""

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
matplotlib.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10.0,
    "text.color": "#000000",
    "axes.labelcolor": "#000000",
    "xtick.color": "#000000",
    "ytick.color": "#000000",
    "svg.fonttype": "none",
    "svg.hashsalt": "dissolve-v12-results-s1-landscape",
})

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.artist import Artist  # noqa: E402
from matplotlib.backends.backend_agg import FigureCanvasAgg  # noqa: E402
from matplotlib.colors import to_rgba  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import NullLocator  # noqa: E402
from matplotlib.markers import MarkerStyle  # noqa: E402
from matplotlib.text import Text  # noqa: E402
import numpy as np  # noqa: E402

try:  # Package import in tests; direct import for the documented CLI.
    from .standards_checker import (
        DataLayerRecord,
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_figure,
        check_output_pair,
    )
except ImportError:  # pragma: no cover - exercised by documented CLI
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
READER_PATH = ROOT / "src/dissolve/thermodynamics.py"
TOOLS_PATH = ROOT / "src/dissolve/tools.py"
SEPARATION_PATH = ROOT / "src/dissolve/separation.py"
PROPOSAL_PATH = HERE / "RESULTS_S1_LANDSCAPE_PROPOSAL.md"
CHARTER_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/V4_VISUALIZATION_CHARTER.md")
INBOX_PATH = Path("/home/aaltamimi2/dissolve-v12-audit/v4/STEER_INBOX.md")
STEER_PATH = Path(
    "/home/aaltamimi2/dissolve-v12-audit/RESULTS_S1_LANDSCAPE_FIGURE_STEER.md"
)

SCATTER_STEM = "results_s1_solubility_scatter"
FRACTION_STEM = "results_s1_dissolving_fraction"
REPRODUCTION_COMMAND = "PYTHONPATH=src:. python figures/results_s1_landscape.py"

EXPECTED_SOURCE_DIGESTS = {
    DB_PATH: "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc",
    READER_PATH: "2a22603c52f5c17d7aa65bec9c43e18f64dcb315404c0b30279704cda8c6b204",
    TOOLS_PATH: "a9ac03d1412d9be6ebef7155683a0bd8a628fbd59d2e77ad9fd547ff001e5d50",
    SEPARATION_PATH: "f1b05ca8684c6ea6d47b60793f60a5607220f8b7366ccab457931758b9e8f985",
    PROPOSAL_PATH: "aca89dc437cef8dab8f6f693ffc10f00736a569960421aab57d6594d50927f1f",
    CHARTER_PATH: "f6f0a7e0d3d948e66a27202b285d924590ea0bafff39f2475462ca33f45a1a40",
    STEER_PATH: "150faa3d3219fc710afbc3164bfec30a6d8f4f499a3ff0aa696681c446f8d3c0",
}

SCATTER_SQL = """
SELECT polymer, solvent, temperature_c, solubility_pct,
       invalid_reason = 'exact_100_artifact' AS is_served_ceiling
FROM solubility_grid
WHERE is_valid OR invalid_reason = 'exact_100_artifact'
ORDER BY polymer, solvent, temperature_c
""".strip()

FRACTION_SQL = """
WITH fractions AS (
    SELECT polymer, temperature_c,
           COUNT(DISTINCT solvent) AS stored_solvent_denominator,
           COUNT(*) FILTER (
               WHERE (is_valid OR invalid_reason = 'exact_100_artifact')
                 AND solubility_pct >= 5.0
           ) AS dissolving_numerator
    FROM solubility_grid
    GROUP BY polymer, temperature_c
)
SELECT polymer, temperature_c, stored_solvent_denominator,
       dissolving_numerator,
       dissolving_numerator::DOUBLE / stored_solvent_denominator
           AS dissolving_fraction
FROM fractions
ORDER BY polymer, temperature_c
""".strip()

DISPOSITION_SQL = """
SELECT is_valid, COALESCE(invalid_reason, '<NULL>') AS invalid_reason,
       COUNT(*) AS cells
FROM solubility_grid
GROUP BY is_valid, invalid_reason
ORDER BY is_valid DESC, invalid_reason
""".strip()

POLYMER_ORDER = (
    "EVOH", "HDPE", "LDPE", "NYLON66", "PC", "PET",
    "PP", "PS", "PVC", "PU", "NYLON6", "PES",
)

# Frozen visual identities.  Colour is never the sole identity channel.
POLYMER_STYLE_MAP: dict[str, dict[str, str]] = {
    "EVOH": {"color": "#0072B2", "marker": "o"},
    "HDPE": {"color": "#E69F00", "marker": "s"},
    "LDPE": {"color": "#009E73", "marker": "^"},
    "NYLON66": {"color": "#D55E00", "marker": "D"},
    "PC": {"color": "#CC79A7", "marker": "P"},
    "PET": {"color": "#56B4E9", "marker": "X"},
    "PP": {"color": "#000000", "marker": "v"},
    "PS": {"color": "#F0E442", "marker": ">"},
    "PVC": {"color": "#8C564B", "marker": "<"},
    "PU": {"color": "#6F4E7C", "marker": "h"},
    "NYLON6": {"color": "#17BECF", "marker": "*"},
    "PES": {"color": "#7F7F7F", "marker": "p"},
}

EXPECTED_DENOMINATORS = {
    **{polymer: 990 for polymer in POLYMER_ORDER[:9]},
    "PU": 78,
    "NYLON6": 32,
    "PES": 32,
}
TEMPERATURE_NODES = tuple(range(25, 161, 5))
THRESHOLD_PCT = 5.0

FIGURE_WIDTH_IN = 7.25
FIGURE_HEIGHT_IN = 5.40
RENDER_DPI = 150
PNG_DPI = 300
FONT_SIZE_PT = 10.0
MARKER_DIAMETER_PT = 7.0
BLACK = "#000000"

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class SourceDigestError(AssertionError):
    """A named source no longer matches its admitted digest."""


class LandscapeValidationError(AssertionError):
    """A measured or mutated landscape no longer satisfies the contract."""


@dataclass(frozen=True)
class SolubilityCell:
    polymer: str
    solvent: str
    temperature_c: int
    solubility_pct: float
    is_served_ceiling: bool


@dataclass(frozen=True)
class FractionPoint:
    polymer: str
    temperature_c: int
    denominator: int
    numerator: int
    fraction: float


@dataclass(frozen=True)
class ThresholdDefault:
    path: str
    function: str
    parameter: str
    line: int
    value: float


@dataclass(frozen=True)
class LandscapeData:
    cells: tuple[SolubilityCell, ...]
    fractions: tuple[FractionPoint, ...]
    raw_valid_cells: int
    exact_100_artifact_cells: int
    nonpositive_cells: int
    threshold_defaults: tuple[ThresholdDefault, ...]


@dataclass(frozen=True)
class DisplacementManifest:
    algorithm: str
    maximum_absolute_displacement_c: float
    row_count: int
    canonical_sha256: str


@dataclass
class LandscapeDrawing:
    fig: plt.Figure
    registry: DrawingRegistry
    title: Artist
    rendered_x: tuple[float, ...] = ()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_source_digests(
    expected: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS,
) -> dict[str, str]:
    """Hash all locked authorities before opening the numeric asset."""

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


def _function_default(
    path: Path,
    function_name: str,
    parameter_name: str,
) -> ThresholdDefault:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    ]
    if len(matches) != 1:
        raise LandscapeValidationError(
            f"expected one {function_name!r} definition in {path}; found {len(matches)}"
        )
    node = matches[0]
    arguments = [*node.args.posonlyargs, *node.args.args]
    positional_defaults = [None] * (len(arguments) - len(node.args.defaults)) + list(
        node.args.defaults
    )
    defaults = dict(zip((arg.arg for arg in arguments), positional_defaults))
    defaults.update(zip((arg.arg for arg in node.args.kwonlyargs), node.args.kw_defaults))
    default_node = defaults.get(parameter_name)
    if default_node is None:
        raise LandscapeValidationError(
            f"{function_name}.{parameter_name} has no literal default"
        )
    try:
        value = float(ast.literal_eval(default_node))
    except (TypeError, ValueError) as error:
        raise LandscapeValidationError(
            f"{function_name}.{parameter_name} default is not numeric"
        ) from error
    return ThresholdDefault(
        path=str(path),
        function=function_name,
        parameter=parameter_name,
        line=int(getattr(default_node, "lineno", node.lineno)),
        value=value,
    )


def inspect_threshold_defaults(
    *,
    tools_path: Path = TOOLS_PATH,
    separation_path: Path = SEPARATION_PATH,
) -> tuple[ThresholdDefault, ...]:
    defaults = (
        _function_default(
            tools_path, "_screen_direction", "solubility_threshold_pct"
        ),
        _function_default(
            separation_path,
            "screen_precipitation_order",
            "min_dissolution_solubility_wt_pct",
        ),
    )
    wrong = [item for item in defaults if item.value != THRESHOLD_PCT]
    if wrong:
        raise LandscapeValidationError(
            "threshold source default moved from 5.0: "
            + ", ".join(
                f"{item.function}.{item.parameter}={item.value:g}" for item in wrong
            )
        )
    return defaults


def measure(
    *,
    expected_digests: Mapping[Path, str] = EXPECTED_SOURCE_DIGESTS,
    tools_path: Path = TOOLS_PATH,
    separation_path: Path = SEPARATION_PATH,
) -> LandscapeData:
    """Recount every plotted value from locked sources with read-only SQL."""

    assert_source_digests(expected_digests)
    defaults = inspect_threshold_defaults(
        tools_path=tools_path, separation_path=separation_path
    )
    connection = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        cell_rows = connection.execute(SCATTER_SQL).fetchall()
        fraction_rows = connection.execute(FRACTION_SQL).fetchall()
        disposition_rows = connection.execute(DISPOSITION_SQL).fetchall()
    finally:
        connection.close()

    cells = tuple(
        SolubilityCell(
            polymer=str(row[0]),
            solvent=str(row[1]),
            temperature_c=int(row[2]),
            solubility_pct=float(row[3]),
            is_served_ceiling=bool(row[4]),
        )
        for row in cell_rows
    )
    fractions = tuple(
        FractionPoint(
            polymer=str(row[0]),
            temperature_c=int(row[1]),
            denominator=int(row[2]),
            numerator=int(row[3]),
            fraction=float(row[4]),
        )
        for row in fraction_rows
    )
    dispositions = {
        (bool(row[0]), str(row[1])): int(row[2]) for row in disposition_rows
    }
    data = LandscapeData(
        cells=cells,
        fractions=fractions,
        raw_valid_cells=dispositions.get((True, "<NULL>"), 0),
        exact_100_artifact_cells=dispositions.get(
            (False, "exact_100_artifact"), 0
        ),
        nonpositive_cells=dispositions.get((False, "nonpositive"), 0),
        threshold_defaults=defaults,
    )
    validate(data)
    return data


def validate(data: LandscapeData) -> None:
    """Assert roster, dispositions, denominators, and exact stored ratios."""

    errors: list[str] = []
    cells = data.cells
    fractions = data.fractions
    polymers = {cell.polymer for cell in cells}
    solvents = {cell.solvent for cell in cells}
    if tuple(sorted(polymers)) != tuple(sorted(POLYMER_ORDER)):
        errors.append(f"scatter polymer roster changed: {sorted(polymers)}")
    if len(solvents) != 990:
        errors.append(f"scatter solvent roster changed: {len(solvents)} != 990")
    if len(cells) != 252_474:
        errors.append(f"evaluable cell count changed: {len(cells)} != 252474")
    ceiling_count = sum(cell.is_served_ceiling for cell in cells)
    if data.raw_valid_cells != 248_378:
        errors.append(f"raw-valid count changed: {data.raw_valid_cells} != 248378")
    if data.exact_100_artifact_cells != 4_096 or ceiling_count != 4_096:
        errors.append(
            "served-ceiling count changed: "
            f"asset={data.exact_100_artifact_cells}, rows={ceiling_count}"
        )
    if data.nonpositive_cells != 982:
        errors.append(f"nonpositive count changed: {data.nonpositive_cells} != 982")
    if any(cell.solubility_pct <= 0 or not math.isfinite(cell.solubility_pct) for cell in cells):
        errors.append("a nonpositive or nonfinite value entered evaluable cells")
    values = [cell.solubility_pct for cell in cells]
    if not values or min(values) != 1e-8 or max(values) != 100.0:
        errors.append("evaluable range changed from 1e-8 through 100.0")
    if any((cell.solubility_pct == 100.0) != cell.is_served_ceiling for cell in cells):
        errors.append("100 wt% rows no longer exactly match the served-ceiling layer")
    threshold_count = sum(cell.solubility_pct >= THRESHOLD_PCT for cell in cells)
    if threshold_count != 96_325:
        errors.append(f"threshold-qualified cell count changed: {threshold_count} != 96325")
    if any(default.value != THRESHOLD_PCT for default in data.threshold_defaults):
        errors.append("a parsed threshold default differs from 5.0")

    if len(fractions) != 336:
        errors.append(f"fraction row count changed: {len(fractions)} != 336")
    fraction_roster = {point.polymer for point in fractions}
    if fraction_roster != set(POLYMER_ORDER):
        errors.append(f"fraction polymer roster changed: {sorted(fraction_roster)}")
    grouped: dict[str, list[FractionPoint]] = defaultdict(list)
    for point in fractions:
        grouped[point.polymer].append(point)
        expected_denominator = EXPECTED_DENOMINATORS.get(point.polymer)
        if point.denominator != expected_denominator:
            errors.append(
                f"fixed denominator changed for {point.polymer} at "
                f"{point.temperature_c}: {point.denominator} != {expected_denominator}"
            )
        expected_fraction = point.numerator / point.denominator
        if not math.isclose(point.fraction, expected_fraction, rel_tol=0.0, abs_tol=1e-15):
            errors.append(
                f"fraction is not exact numerator/denominator for "
                f"{point.polymer} at {point.temperature_c}"
            )
    for polymer in POLYMER_ORDER:
        points = grouped.get(polymer, [])
        nodes = tuple(point.temperature_c for point in points)
        if nodes != TEMPERATURE_NODES:
            errors.append(f"stored temperature nodes changed for {polymer}")
        if len({point.fraction for point in points}) < 2:
            errors.append(f"fraction series is constant for {polymer}")
    numerator_sum = sum(point.numerator for point in fractions)
    if numerator_sum != threshold_count:
        errors.append(
            f"fraction numerators do not close to scatter threshold count: "
            f"{numerator_sum} != {threshold_count}"
        )
    if errors:
        raise LandscapeValidationError("\n".join(errors))


def _solvent_jitter(solvent: str) -> float:
    integer = int.from_bytes(
        hashlib.sha256(solvent.encode("utf-8")).digest()[:8], "big"
    )
    unit = integer / float(2**64 - 1)
    return (unit - 0.5) * 0.20


def display_x(cell: SolubilityCell) -> float:
    polymer_index = POLYMER_ORDER.index(cell.polymer)
    polymer_dodge = (polymer_index - (len(POLYMER_ORDER) - 1) / 2.0) * 0.32
    return float(cell.temperature_c) + polymer_dodge + _solvent_jitter(cell.solvent)


def _displacement_lines(
    cells: Sequence[SolubilityCell], rendered_x: Sequence[float]
) -> Iterable[str]:
    for cell, shown in zip(cells, rendered_x):
        yield (
            f"{cell.polymer}\t{cell.solvent}\t{cell.temperature_c}\t"
            f"{cell.temperature_c:.17g}\t{shown:.17g}\n"
        )


def displacement_manifest(
    cells: Sequence[SolubilityCell], rendered_x: Sequence[float]
) -> DisplacementManifest:
    digest = hashlib.sha256()
    maximum = 0.0
    for cell, shown, line in zip(cells, rendered_x, _displacement_lines(cells, rendered_x)):
        maximum = max(maximum, abs(float(shown) - cell.temperature_c))
        digest.update(line.encode("utf-8"))
    return DisplacementManifest(
        algorithm=(
            "stored_temperature + 0.32*(polymer_index-5.5) + "
            "0.20*(uint64_be(sha256(solvent)[:8])/(2^64-1)-0.5)"
        ),
        maximum_absolute_displacement_c=maximum,
        row_count=len(cells),
        canonical_sha256=digest.hexdigest(),
    )


def check_display_displacement(
    source_rows: Sequence[SolubilityCell],
    rendered_offsets: Sequence[float],
    manifest: DisplacementManifest,
) -> dict[str, int | float | str]:
    violations: list[str] = []
    if len(source_rows) != len(rendered_offsets):
        violations.append(
            f"display displacement row count changed: "
            f"{len(rendered_offsets)} != {len(source_rows)}"
        )
    nodes = set(TEMPERATURE_NODES)
    if any(row.temperature_c not in nodes for row in source_rows):
        violations.append("a source row is not on an exact stored temperature node")
    expected = tuple(display_x(row) for row in source_rows)
    if len(expected) == len(rendered_offsets):
        for index, (wanted, observed) in enumerate(zip(expected, rendered_offsets)):
            if float(observed) != wanted:
                violations.append(
                    f"display displacement differs at source row {index}: "
                    f"{observed:.17g} != {wanted:.17g}"
                )
                break
    actual_manifest = displacement_manifest(source_rows, rendered_offsets)
    if actual_manifest.maximum_absolute_displacement_c >= 2.5:
        violations.append(
            "display displacement leaves its 5 degC node interval: "
            f"{actual_manifest.maximum_absolute_displacement_c:.17g}"
        )
    if manifest.row_count != actual_manifest.row_count:
        violations.append("displacement manifest row count does not match")
    if manifest.canonical_sha256 != actual_manifest.canonical_sha256:
        violations.append("displacement manifest digest does not match rendered x")
    if not math.isclose(
        manifest.maximum_absolute_displacement_c,
        actual_manifest.maximum_absolute_displacement_c,
        rel_tol=0.0,
        abs_tol=1e-15,
    ):
        violations.append("displacement manifest maximum does not match rendered x")
    if violations:
        raise StandardsViolation(violations)
    return {
        "row_count": actual_manifest.row_count,
        "maximum_absolute_displacement_c": actual_manifest.maximum_absolute_displacement_c,
        "canonical_sha256": actual_manifest.canonical_sha256,
    }


def _new_figure() -> tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(
        figsize=(FIGURE_WIDTH_IN, FIGURE_HEIGHT_IN),
        dpi=RENDER_DPI,
        facecolor="white",
    )
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0.105, 0.145, 0.615, 0.725))
    ax.set_facecolor("white")
    for spine in ax.spines.values():
        spine.set_color(BLACK)
        spine.set_linewidth(0.8)
    ax.tick_params(axis="both", which="both", labelsize=FONT_SIZE_PT, colors=BLACK)
    ax.xaxis.set_gid("axes-scaffold-x")
    ax.yaxis.set_gid("axes-scaffold-y")
    return fig, ax


def _register_all_text(fig: plt.Figure, registry: DrawingRegistry) -> None:
    fig.canvas.draw()
    registry.texts = [
        TextRecord(artist, None)
        for artist in fig.findobj(match=Text)
        if artist.get_visible() and bool(artist.get_text())
    ]


def _figure_text(
    fig: plt.Figure,
    x: float,
    y: float,
    text: str,
    *,
    ha: str = "left",
    va: str = "center",
    weight: str = "normal",
    gid: str | None = None,
) -> Text:
    artist = fig.text(
        x, y, text,
        ha=ha, va=va,
        fontsize=FONT_SIZE_PT,
        fontfamily="DejaVu Sans",
        fontweight=weight,
        color=BLACK,
    )
    if gid:
        artist.set_gid(gid)
    return artist


def _legend_mark(
    fig: plt.Figure,
    registry: DrawingRegistry,
    *,
    x: float,
    y: float,
    color: str,
    marker: str,
    gid: str,
    open_marker: bool = False,
) -> Line2D:
    mark = Line2D(
        [x], [y],
        transform=fig.transFigure,
        linestyle="none",
        marker=marker,
        markersize=MARKER_DIAMETER_PT,
        markerfacecolor="white" if open_marker else color,
        markeredgecolor=color,
        markeredgewidth=1.0,
        clip_on=False,
        color=color,
        zorder=20,
    )
    mark.set_gid(gid)
    mark.set_label(gid)
    fig.add_artist(mark)
    registry.collision_patches.append(mark)
    registry.data_marks.append(mark)
    return mark


def _polymer_legend(
    fig: plt.Figure,
    registry: DrawingRegistry,
    *,
    fraction_labels: bool,
    rasterized: bool = False,
) -> None:
    start_y = 0.825
    step = 0.0515
    for index, polymer in enumerate(POLYMER_ORDER):
        y = start_y - index * step
        style = POLYMER_STYLE_MAP[polymer]
        mark = _legend_mark(
            fig,
            registry,
            x=0.765,
            y=y,
            color=style["color"],
            marker=style["marker"],
            gid=f"polymer-legend-mark-{polymer.lower()}",
        )
        mark.set_rasterized(rasterized)
        if fraction_labels and polymer in {"PU", "NYLON6", "PES"}:
            label = f"{polymer} (n={EXPECTED_DENOMINATORS[polymer]})"
        else:
            label = polymer
        text = _figure_text(
            fig, 0.785, y, label,
            gid=f"polymer-legend-text-{polymer.lower()}",
        )
        text.set_rasterized(rasterized)
    if fraction_labels:
        _figure_text(
            fig, 0.765, 0.185,
            "Dense: n=990 each",
            gid="polymer-legend-dense-note",
        ).set_rasterized(rasterized)


def draw_solubility_scatter(
    data: LandscapeData,
    *,
    cloud_rasterized: bool = True,
    nondata_rasterized: bool = False,
) -> LandscapeDrawing:
    validate(data)
    fig, ax = _new_figure()
    registry = DrawingRegistry()
    title = _figure_text(
        fig, 0.4125, 0.955,
        "Stored solubility landscape across all polymers",
        ha="center", weight="semibold", gid="figure-title",
    )
    ax.set_xlim(21.5, 163.5)
    ax.set_xticks(tuple(range(25, 161, 15)))
    ax.set_xlabel("Stored temperature node (°C)", fontsize=FONT_SIZE_PT)
    ax.set_yscale("log")
    ax.set_ylim(5e-9, 180.0)
    ax.set_yticks((1e-8, 1e-6, 1e-4, 1e-2, 1.0, 100.0))
    ax.yaxis.set_minor_locator(NullLocator())
    ax.set_ylabel("Stored solubility (wt%)", fontsize=FONT_SIZE_PT)
    ax.grid(False)

    threshold = ax.axhline(
        THRESHOLD_PCT, color=BLACK, linewidth=0.9, linestyle="--", zorder=5,
    )
    threshold.set_gid("threshold-guide")
    threshold.set_label("threshold-guide")
    threshold.set_rasterized(nondata_rasterized)
    registry.connectors.append(threshold)
    _figure_text(
        fig, 0.750, 0.085,
        "5 wt% dashed threshold",
        gid="threshold-guide-label",
    ).set_rasterized(nondata_rasterized)
    _figure_text(
        fig, 0.105, 0.025,
        "Horizontal displacement is display-only; no intermediate temperature was evaluated.",
        gid="stored-node-note",
    )

    rendered_x = tuple(display_x(cell) for cell in data.cells)
    cell_indices: dict[tuple[str, bool], list[int]] = defaultdict(list)
    for index, cell in enumerate(data.cells):
        cell_indices[(cell.polymer, cell.is_served_ceiling)].append(index)
    for polymer in POLYMER_ORDER:
        style = POLYMER_STYLE_MAP[polymer]
        for is_ceiling in (False, True):
            indices = cell_indices[(polymer, is_ceiling)]
            if not indices:
                continue
            x_values = np.fromiter((rendered_x[index] for index in indices), float)
            y_values = np.fromiter(
                (data.cells[index].solubility_pct for index in indices), float
            )
            layer_role = "ceiling" if is_ceiling else "raw"
            kwargs: dict[str, Any] = {
                "s": MARKER_DIAMETER_PT**2,
                "marker": style["marker"],
                "linewidths": 0.65 if is_ceiling else 0.0,
                "rasterized": cloud_rasterized,
                "clip_on": True,
                "zorder": 2.2 if is_ceiling else 2.0,
            }
            if is_ceiling:
                kwargs.update(
                    facecolors="none", edgecolors=style["color"], alpha=0.72
                )
            else:
                kwargs.update(
                    facecolors=style["color"], edgecolors="none", alpha=0.075
                )
            artist = ax.scatter(x_values, y_values, **kwargs)
            artist.set_gid(f"scatter-layer-{polymer.lower()}-{layer_role}")
            artist.set_label(f"scatter:{polymer}:{layer_role}")
            registry.data_layers.append(DataLayerRecord(
                artist=artist,
                data_region=ax.patch,
                x_values=x_values,
                y_values=y_values,
                series_id=f"scatter:{polymer}:{layer_role}",
                marker_size_pt=MARKER_DIAMETER_PT,
                style_color=style["color"],
                style_marker=style["marker"],
                source_indices=tuple(indices),
            ))

    _polymer_legend(
        fig, registry, fraction_labels=False, rasterized=nondata_rasterized
    )
    raw_key = _legend_mark(
        fig, registry,
        x=0.765, y=0.175, color=BLACK, marker="o",
        gid="polymer-legend-raw-key",
    )
    raw_key.set_rasterized(nondata_rasterized)
    _figure_text(
        fig, 0.785, 0.175, "raw-valid cell", gid="polymer-legend-raw-text"
    ).set_rasterized(nondata_rasterized)
    ceiling_key = _legend_mark(
        fig, registry,
        x=0.765, y=0.130, color=BLACK, marker="o",
        gid="polymer-legend-ceiling-key", open_marker=True,
    )
    ceiling_key.set_rasterized(nondata_rasterized)
    _figure_text(
        fig, 0.785, 0.130, "served ceiling", gid="polymer-legend-ceiling-text"
    ).set_rasterized(nondata_rasterized)

    if nondata_rasterized:
        ax.xaxis.set_rasterized(True)
        ax.yaxis.set_rasterized(True)
    _register_all_text(fig, registry)
    return LandscapeDrawing(fig, registry, title, rendered_x)


def draw_dissolving_fraction(
    data: LandscapeData,
    *,
    series_rasterized: bool = False,
) -> LandscapeDrawing:
    validate(data)
    fig, ax = _new_figure()
    registry = DrawingRegistry()
    title = _figure_text(
        fig, 0.4125, 0.955,
        "Stored solvent catalogs meeting the 5 wt% threshold",
        ha="center", weight="semibold", gid="figure-title",
    )
    ax.set_xlim(22.5, 162.5)
    ax.set_xticks(tuple(range(25, 161, 15)))
    ax.set_xlabel("Stored temperature node (°C)", fontsize=FONT_SIZE_PT)
    ax.set_ylim(-0.025, 1.025)
    ax.set_yticks(np.linspace(0.0, 1.0, 6))
    ax.set_ylabel("Fraction of stored solvent catalog", fontsize=FONT_SIZE_PT)
    ax.grid(False)

    grouped: dict[str, list[FractionPoint]] = defaultdict(list)
    for point in data.fractions:
        grouped[point.polymer].append(point)
    for polymer in POLYMER_ORDER:
        points = grouped[polymer]
        x_values = np.asarray([point.temperature_c for point in points], dtype=float)
        y_values = np.asarray([point.fraction for point in points], dtype=float)
        style = POLYMER_STYLE_MAP[polymer]
        line, = ax.plot(
            x_values,
            y_values,
            color=style["color"],
            marker=style["marker"],
            markersize=MARKER_DIAMETER_PT,
            markerfacecolor=style["color"],
            markeredgecolor=style["color"],
            markeredgewidth=0.65,
            linewidth=0.9,
            clip_on=True,
            rasterized=series_rasterized,
            zorder=2,
        )
        line.set_gid(f"fraction-series-{polymer.lower()}")
        line.set_label(f"fraction:{polymer}")
        registry.data_layers.append(DataLayerRecord(
            artist=line,
            data_region=ax.patch,
            x_values=x_values,
            y_values=y_values,
            series_id=f"fraction:{polymer}",
            marker_size_pt=MARKER_DIAMETER_PT,
            style_color=style["color"],
            style_marker=style["marker"],
        ))

    _polymer_legend(fig, registry, fraction_labels=True)
    _figure_text(
        fig, 0.105, 0.025,
        "Points are stored nodes; lines are visual guides only. No missing pair is interpolated.",
        gid="stored-node-note",
    )
    _register_all_text(fig, registry)
    return LandscapeDrawing(fig, registry, title)


def verify_geometry(drawing: LandscapeDrawing) -> dict[str, float | int]:
    return check_figure(
        drawing.fig,
        drawing.registry,
        drawing.title,
        standards=FigureStandards(font_size_pt=FONT_SIZE_PT),
        content_artists=[drawing.fig.axes[0].patch],
    )


def _marker_vertices(marker: str) -> np.ndarray:
    style = MarkerStyle(marker)
    return style.get_path().transformed(style.get_transform()).vertices


def _record_polymer(record: DataLayerRecord) -> str | None:
    parts = record.series_id.split(":")
    if len(parts) >= 2 and parts[0] in {"scatter", "fraction"}:
        return parts[1]
    return None


def _artist_rgb(record: DataLayerRecord) -> tuple[float, float, float]:
    artist = record.artist
    if isinstance(artist, Line2D):
        return tuple(float(value) for value in to_rgba(artist.get_color())[:3])
    colors = (
        artist.get_edgecolors()
        if record.series_id.endswith(":ceiling")
        else artist.get_facecolors()
    )
    if len(colors) == 0:
        raise StandardsViolation(
            [f"series has no introspectable color: {record.series_id}"]
        )
    return tuple(float(value) for value in colors[0, :3])


def _artist_marker_matches(record: DataLayerRecord, marker: str) -> bool:
    artist = record.artist
    if isinstance(artist, Line2D):
        return artist.get_marker() == marker
    paths = artist.get_paths()
    if len(paths) != 1:
        return False
    actual = paths[0].vertices
    wanted = _marker_vertices(marker)
    return actual.shape == wanted.shape and bool(np.allclose(actual, wanted, atol=1e-12))


def check_polymer_style_contract(
    scatter_registry: DrawingRegistry,
    fraction_registry: DrawingRegistry,
    manifest_style_map: Mapping[str, Mapping[str, str]],
) -> dict[str, int]:
    violations: list[str] = []
    if set(manifest_style_map) != set(POLYMER_ORDER):
        violations.append("manifest style map does not contain the exact 12-polymer roster")
    expected_rgbs = {
        polymer: tuple(float(value) for value in to_rgba(style["color"])[:3])
        for polymer, style in manifest_style_map.items()
    }
    seen: dict[str, set[str]] = {"scatter": set(), "fraction": set()}
    for figure_name, registry in (
        ("scatter", scatter_registry), ("fraction", fraction_registry)
    ):
        for record in registry.data_layers:
            polymer = _record_polymer(record)
            if polymer is None:
                continue
            seen[figure_name].add(polymer)
            expected = manifest_style_map.get(polymer)
            if expected is None:
                violations.append(f"unmapped polymer style in {figure_name}: {polymer}")
                continue
            try:
                actual_rgb = _artist_rgb(record)
            except StandardsViolation as error:
                violations.extend(error.violations)
            else:
                if any(
                    abs(actual - wanted) > 1e-12
                    for actual, wanted in zip(actual_rgb, expected_rgbs[polymer])
                ):
                    violations.append(
                        f"polymer color mismatch in {figure_name}: {polymer}"
                    )
            if not _artist_marker_matches(record, expected["marker"]):
                violations.append(
                    f"polymer marker mismatch in {figure_name}: {polymer}"
                )
            if record.style_color != expected["color"] or record.style_marker != expected["marker"]:
                violations.append(
                    f"registered polymer style mismatch in {figure_name}: {polymer}"
                )
    for figure_name, roster in seen.items():
        if roster != set(POLYMER_ORDER):
            violations.append(f"{figure_name} style roster is incomplete: {sorted(roster)}")
    if violations:
        raise StandardsViolation(violations)
    return {"polymer_count": len(POLYMER_ORDER)}


def check_scatter_layer_contract(
    data: LandscapeData,
    registry: DrawingRegistry,
) -> dict[str, int]:
    """Trace every scatter source row to exactly one admitted visual layer."""

    violations: list[str] = []
    seen: list[int] = []
    role_counts = {"raw": 0, "ceiling": 0}
    for record in registry.data_layers:
        if not record.series_id.startswith("scatter:"):
            continue
        parts = record.series_id.split(":")
        if len(parts) != 3 or parts[2] not in role_counts:
            violations.append(f"invalid scatter layer identity: {record.series_id}")
            continue
        _, polymer, role = parts
        if record.source_indices is None:
            violations.append(f"scatter layer has no source-row trace: {record.series_id}")
            continue
        indices = tuple(int(value) for value in record.source_indices)
        if len(indices) != len(np.asarray(record.x_values).reshape(-1)):
            violations.append(f"scatter layer source/offset counts differ: {record.series_id}")
        role_counts[role] += len(indices)
        seen.extend(indices)
        for index in indices:
            if index < 0 or index >= len(data.cells):
                violations.append(f"scatter layer source index is out of range: {index}")
                break
            cell = data.cells[index]
            expected_role = "ceiling" if cell.is_served_ceiling else "raw"
            if cell.polymer != polymer or expected_role != role:
                violations.append(
                    f"scatter source row {index} is in the wrong visual layer: "
                    f"{record.series_id}"
                )
                break
    if len(seen) != len(data.cells) or sorted(seen) != list(range(len(data.cells))):
        violations.append("scatter source-row trace is not an exact one-to-one roster")
    if role_counts["raw"] != data.raw_valid_cells:
        violations.append(
            f"filled raw layer count changed: {role_counts['raw']} != {data.raw_valid_cells}"
        )
    if role_counts["ceiling"] != data.exact_100_artifact_cells:
        violations.append(
            "open served-ceiling layer count changed: "
            f"{role_counts['ceiling']} != {data.exact_100_artifact_cells}"
        )
    if violations:
        raise StandardsViolation(violations)
    return {
        "source_row_count": len(seen),
        "raw_layer_count": role_counts["raw"],
        "served_ceiling_layer_count": role_counts["ceiling"],
    }


def _parent_map(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _wrap_elements(
    root: ET.Element,
    elements: Sequence[ET.Element],
    group_id: str,
) -> ET.Element:
    group = ET.Element(f"{{{SVG_NS}}}g", {"id": group_id})
    if not elements:
        root.append(group)
        return group
    parents = _parent_map(root)
    first = elements[0]
    first_parent = parents[first]
    insert_at = list(first_parent).index(first)
    first_parent.insert(insert_at, group)
    for element in elements:
        parent = _parent_map(root).get(element)
        if parent is not None:
            parent.remove(element)
            group.append(element)
    return group


def _elements_with_id_prefix(root: ET.Element, prefix: str) -> list[ET.Element]:
    return [
        element for element in root.iter()
        if element.get("id", "").startswith(prefix)
        and element.get("id") != prefix.rstrip("-")
    ]


def _postprocess_svg(
    path: Path,
    stem: str,
    *,
    cloud_image_limit: int | None = None,
) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    axes = _elements_with_id_prefix(root, "axes-scaffold-")
    _wrap_elements(root, axes, "axes-scaffold")
    legend = _elements_with_id_prefix(root, "polymer-legend-")
    _wrap_elements(root, legend, "polymer-legend")
    if stem == SCATTER_STEM:
        parents = _parent_map(root)
        images = [
            element for element in root.iter(f"{{{SVG_NS}}}image")
            if not any(
                ancestor.get("id") in {"axes-scaffold", "threshold-guide", "polymer-legend"}
                for ancestor in _ancestors(element, parents)
            )
        ]
        if cloud_image_limit is not None:
            images = images[:cloud_image_limit]
        cloud_parts: list[ET.Element] = images or _elements_with_id_prefix(
            root, "scatter-layer-"
        )
        _wrap_elements(root, cloud_parts, "solubility-cloud")
    elif stem == FRACTION_STEM:
        series = _elements_with_id_prefix(root, "fraction-series-")
        _wrap_elements(root, series, "fraction-series")
    else:  # pragma: no cover - internal call contract
        raise ValueError(f"unknown landscape stem: {stem}")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _ancestors(
    element: ET.Element,
    parents: Mapping[ET.Element, ET.Element],
) -> Iterable[ET.Element]:
    current = parents.get(element)
    while current is not None:
        yield current
        current = parents.get(current)


def _descendants(group: ET.Element, local_name: str) -> list[ET.Element]:
    return list(group.iter(f"{{{SVG_NS}}}{local_name}"))


def _id(root: ET.Element, value: str) -> ET.Element | None:
    return next((element for element in root.iter() if element.get("id") == value), None)


def _has_vector(group: ET.Element) -> bool:
    vector_names = {"path", "line", "polyline", "polygon", "use", "text"}
    return any(element.tag.rsplit("}", 1)[-1] in vector_names for element in group.iter())


def check_svg_raster_contract(
    scatter_svg: str | Path,
    fraction_svg: str | Path,
) -> dict[str, int]:
    violations: list[str] = []
    scatter_root = ET.parse(scatter_svg).getroot()
    fraction_root = ET.parse(fraction_svg).getroot()

    cloud = _id(scatter_root, "solubility-cloud")
    scatter_images = list(scatter_root.iter(f"{{{SVG_NS}}}image"))
    if cloud is None:
        violations.append("Figure A is missing tagged solubility-cloud group")
    else:
        cloud_images = _descendants(cloud, "image")
        if not cloud_images:
            violations.append("Figure A solubility-cloud has no embedded image")
        if _descendants(cloud, "use") or _descendants(cloud, "path"):
            violations.append("Figure A solubility-cloud contains vector point geometry")
        if len(cloud_images) != len(scatter_images):
            violations.append("Figure A has an image outside solubility-cloud")
    for group_id in ("axes-scaffold", "threshold-guide", "polymer-legend"):
        group = _id(scatter_root, group_id)
        if group is None:
            violations.append(f"Figure A is missing tagged {group_id} group")
        elif _descendants(group, "image"):
            violations.append(f"Figure A {group_id} contains raster image content")
        elif not _has_vector(group):
            violations.append(f"Figure A {group_id} has no vector content")

    fraction_images = list(fraction_root.iter(f"{{{SVG_NS}}}image"))
    if fraction_images:
        violations.append("Figure B contains an image")
    for group_id in ("fraction-series", "axes-scaffold", "polymer-legend"):
        group = _id(fraction_root, group_id)
        if group is None:
            violations.append(f"Figure B is missing tagged {group_id} group")
        elif _descendants(group, "image"):
            violations.append(f"Figure B {group_id} contains raster image content")
        elif not _has_vector(group):
            violations.append(f"Figure B {group_id} has no vector content")
    if violations:
        raise StandardsViolation(violations)
    return {
        "scatter_image_count": len(scatter_images),
        "fraction_image_count": len(fraction_images),
    }


def _save_pair(
    drawing: LandscapeDrawing,
    output_dir: Path,
    stem: str,
) -> tuple[Path, Path, dict[str, int | str]]:
    png_path = output_dir / f"{stem}.png"
    svg_path = output_dir / f"{stem}.svg"
    drawing.fig.savefig(
        png_path,
        format="png",
        dpi=PNG_DPI,
        facecolor="white",
        edgecolor="none",
        metadata={"Software": "figures/results_s1_landscape.py"},
    )
    drawing.fig.savefig(
        svg_path,
        format="svg",
        facecolor="white",
        edgecolor="none",
        metadata={
            "Creator": "figures/results_s1_landscape.py",
            "Date": "2026-08-26",
            "Title": stem,
        },
    )
    _postprocess_svg(svg_path, stem)
    return png_path, svg_path, check_output_pair(png_path, svg_path)


def _source_manifest(locked: Mapping[str, str]) -> list[dict[str, Any]]:
    roles = {
        DB_PATH: "numeric stored-grid authority",
        READER_PATH: "runtime evaluable-disposition authority",
        TOOLS_PATH: "5 wt% screen-direction default authority",
        SEPARATION_PATH: "5 wt% precipitation-screen default authority",
        PROPOSAL_PATH: "clause-by-clause admitted build contract",
        CHARTER_PATH: "figure standards and owner stops",
        STEER_PATH: "non-numeric landscape framing",
    }
    sources = [
        {
            "path": str(path),
            "role": roles[path],
            "digest_lock": True,
            "expected_sha256": expected,
            "actual_sha256": locked[str(path)],
        }
        for path, expected in EXPECTED_SOURCE_DIGESTS.items()
    ]
    sources.append({
        "path": str(INBOX_PATH),
        "role": "append-only governing instruction history; not a number source",
        "digest_lock": False,
        "observed_sha256": sha256_file(INBOX_PATH),
    })
    return sources


def _fraction_rows(data: LandscapeData) -> list[dict[str, int | float | str]]:
    return [
        {
            "polymer": point.polymer,
            "temperature_c": point.temperature_c,
            "stored_solvent_denominator": point.denominator,
            "dissolving_numerator": point.numerator,
            "dissolving_fraction": point.fraction,
        }
        for point in data.fractions
    ]


def _layer_counts(data: LandscapeData) -> dict[str, dict[str, Any]]:
    scatter: dict[str, dict[str, int]] = {}
    for polymer in POLYMER_ORDER:
        polymer_cells = [cell for cell in data.cells if cell.polymer == polymer]
        ceiling = sum(cell.is_served_ceiling for cell in polymer_cells)
        scatter[polymer] = {
            "raw_valid": len(polymer_cells) - ceiling,
            "served_ceiling": ceiling,
            "total": len(polymer_cells),
        }
    fraction: dict[str, dict[str, int]] = {}
    for polymer in POLYMER_ORDER:
        points = [point for point in data.fractions if point.polymer == polymer]
        fraction[polymer] = {"stored_node_points": len(points)}
    return {"scatter": scatter, "fraction": fraction}


def _sidecar_payload(
    *,
    stem: str,
    data: LandscapeData,
    locked_sources: Mapping[str, str],
    standards_report: Mapping[str, float | int],
    output_report: Mapping[str, int | str],
    displacement: DisplacementManifest,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "artifact_status": "first draft; not a final publication figure",
        "figure": stem,
        "reproduction_command": REPRODUCTION_COMMAND,
        "generator": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__).resolve()),
        },
        "sources": _source_manifest(locked_sources),
        "threshold_defaults": [item.__dict__ for item in data.threshold_defaults],
        "polymer_style_map": POLYMER_STYLE_MAP,
        "stored_temperature_nodes_c": list(TEMPERATURE_NODES),
        "queries": {
            "scatter_cells": SCATTER_SQL,
            "fractions": FRACTION_SQL,
            "dispositions": DISPOSITION_SQL,
        },
        "fraction_rows": _fraction_rows(data),
        "per_layer_row_counts": _layer_counts(data),
        "displacement": displacement.__dict__,
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
    if stem == SCATTER_STEM:
        payload.update({
            "field_map": {
                "x": "temperature_c plus deterministic display-only polymer dodge and solvent jitter",
                "y": "solubility_pct for runtime-evaluable rows",
                "filled_layer": "is_valid raw-valid cells",
                "open_served_ceiling_layer": "invalid_reason = exact_100_artifact",
            },
            "drawn_values": {
                "all_evaluable_cells": len(data.cells),
                "raw_valid_cells": data.raw_valid_cells,
                "exact_100_artifact_served_cells": data.exact_100_artifact_cells,
                "threshold_qualified_cells": sum(
                    cell.solubility_pct >= THRESHOLD_PCT for cell in data.cells
                ),
                "stored_min_solubility_pct": min(cell.solubility_pct for cell in data.cells),
                "stored_max_solubility_pct": max(cell.solubility_pct for cell in data.cells),
            },
            "source_row_order": "ORDER BY polymer, solvent, temperature_c",
            "unshifted_temperature_c_by_source_row": [
                cell.temperature_c for cell in data.cells
            ],
        })
    elif stem == FRACTION_STEM:
        payload.update({
            "field_map": {
                "x": "stored temperature_c",
                "numerator": "runtime-evaluable rows with solubility_pct >= 5.0",
                "denominator": "fixed COUNT(DISTINCT solvent) for the polymer catalog",
                "y": "dissolving_numerator / stored_solvent_denominator",
            },
            "fixed_denominators": EXPECTED_DENOMINATORS,
        })
    else:  # pragma: no cover - internal call contract
        raise ValueError(f"unknown landscape stem: {stem}")
    return payload


def generate(output_dir: str | Path = HERE) -> dict[str, dict[str, Any]]:
    """Measure, validate, draw, check, and save both admitted draft figures."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    locked_sources = assert_source_digests()
    data = measure()
    scatter = draw_solubility_scatter(data)
    fraction = draw_dissolving_fraction(data)
    displacement = displacement_manifest(data.cells, scatter.rendered_x)
    check_display_displacement(data.cells, scatter.rendered_x, displacement)
    check_polymer_style_contract(
        scatter.registry, fraction.registry, POLYMER_STYLE_MAP
    )
    check_scatter_layer_contract(data, scatter.registry)
    drawings = {SCATTER_STEM: scatter, FRACTION_STEM: fraction}
    reports: dict[str, dict[str, Any]] = {}
    try:
        for stem, drawing in drawings.items():
            standards_report = verify_geometry(drawing)
            png_path, svg_path, output_report = _save_pair(
                drawing, destination, stem
            )
            sidecar = _sidecar_payload(
                stem=stem,
                data=data,
                locked_sources=locked_sources,
                standards_report=standards_report,
                output_report=output_report,
                displacement=displacement,
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
        reports["svg_contract"] = check_svg_raster_contract(
            destination / f"{SCATTER_STEM}.svg",
            destination / f"{FRACTION_STEM}.svg",
        )
    finally:
        plt.close(scatter.fig)
        plt.close(fraction.fig)
    return reports


def generated_names() -> tuple[str, ...]:
    return tuple(
        f"{stem}.{suffix}"
        for stem in (SCATTER_STEM, FRACTION_STEM)
        for suffix in ("png", "svg", "provenance.json")
    )


def main() -> int:
    reports = generate()
    print(json.dumps(reports, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
