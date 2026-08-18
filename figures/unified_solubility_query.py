#!/usr/bin/env python3
"""Draw the v12 unified-solubility-query paper schematic.

Run from the repository root with::

    PYTHONPATH=src python figures/unified_solubility_query.py

The script deliberately measures the live tool through ``dissolve.call``.  It
also reads the immutable DuckDB asset directly where the figure discusses raw
asset stamps rather than engine disposition.  Both data and rendered geometry
are asserted before either output is saved.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from importlib.resources import files
import json
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.artist import Artist
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle
from matplotlib.transforms import Bbox

from dissolve import BY_NAME, call


HERE = Path(__file__).resolve().parent
PDF_PATH = HERE / "unified_solubility_query.pdf"
PNG_PATH = HERE / "unified_solubility_query.png"

FIG_WIDTH_IN = 7.25
FIG_HEIGHT_IN = 6.60
FONT_SIZE_PT = 10.0
RENDER_DPI = 150
PNG_DPI = 300

BLACK = "#000000"
PANEL_FACE = "#FBFCFD"
PANEL_EDGE = "#AEB6BF"
CARD_FACE = "#FFFFFF"
TRACK = "#E1E5E9"
RAW_VALID = "#4E79A7"
SATURATED = "#59A14F"
REJECTED = "#E15759"
PAGE = "#F28E2B"
REFUSAL_FACE = "#FDE9E7"
REFUSAL_EDGE = "#C94C4C"
RED_JOIN_FACE = "#ECF5EA"
ARROW = "#67717C"


EXPECTED_ROW_KEYS = {
    "atmospheric_operation",
    "boiling_point_c",
    "boiling_point_margin_c",
    "clip_limit_wt_percent",
    "ghs_signal_word",
    "is_clipped",
    "polymer",
    "solubility_pct",
    "solvent",
    "solvent_name",
    "source_table",
    "temperature_c",
}
EXPECTED_RED_ROW_KEYS = EXPECTED_ROW_KEYS | {
    "hsp_polymer_identity_route",
    "hsp_polymer_name",
    "hsp_polymer_row_id",
    "hsp_solvent_identity_route",
    "hsp_solvent_name",
    "hsp_solvent_row_id",
    "hsp_source_id",
    "r0",
    "ra",
    "red",
}


def registry_query(**kwargs: Any) -> dict[str, Any]:
    """Call the stable registry name and validate the shared envelope."""
    assert "solubility_query" in BY_NAME
    assert BY_NAME["solubility_query"].engine == "thermodynamics"
    envelope = json.loads(call("solubility_query", **kwargs))
    assert set(envelope) == {"display", "data"}
    assert envelope["data"]["tool_name"] == "solubility_query"
    return envelope["data"]


def measure() -> dict[str, Any]:
    """Measure every displayed value and the hidden facts it depends on."""
    asset_path = Path(
        str(files("dissolve").joinpath("data/thermodynamics.duckdb"))
    )
    raw = duckdb.connect(str(asset_path), read_only=True)
    try:
        asset_row = raw.execute(
            """
            SELECT
                count(*) AS stored_cells,
                count(*) FILTER (WHERE is_valid) AS raw_valid,
                count(*) FILTER (WHERE NOT is_valid) AS raw_flagged,
                count(*) FILTER (
                    WHERE invalid_reason = 'exact_100_artifact'
                ) AS saturated_stamp,
                count(*) FILTER (
                    WHERE invalid_reason = 'nonpositive'
                ) AS nonpositive_stamp,
                count(DISTINCT polymer) AS polymers,
                count(DISTINCT solvent) AS solvents,
                count(DISTINCT temperature_c) AS temperatures,
                min(temperature_c) AS temperature_min,
                max(temperature_c) AS temperature_max
            FROM solubility_grid
            """
        ).fetchone()
        assert asset_row is not None
        asset = dict(
            zip(
                (
                    "stored_cells",
                    "raw_valid",
                    "raw_flagged",
                    "saturated_stamp",
                    "nonpositive_stamp",
                    "polymers",
                    "solvents",
                    "temperatures",
                    "temperature_min",
                    "temperature_max",
                ),
                map(int, asset_row),
            )
        )

        polymer_names = [
            str(row[0])
            for row in raw.execute(
                "SELECT DISTINCT polymer FROM solubility_grid ORDER BY polymer"
            ).fetchall()
        ]

        ldpe_asset_row = raw.execute(
            """
            SELECT
                count(*) AS stored,
                count(*) FILTER (WHERE is_valid) AS raw_valid,
                count(*) FILTER (
                    WHERE invalid_reason = 'exact_100_artifact'
                ) AS saturated_stamp,
                count(*) FILTER (
                    WHERE invalid_reason = 'nonpositive'
                ) AS nonpositive_stamp
            FROM solubility_grid
            WHERE polymer = 'LDPE' AND temperature_c = 140
            """
        ).fetchone()
        assert ldpe_asset_row is not None
        ldpe_asset = dict(
            zip(
                ("stored", "raw_valid", "saturated_stamp", "nonpositive_stamp"),
                map(int, ldpe_asset_row),
            )
        )
        ldpe_saturated_solvents = {
            str(row[0])
            for row in raw.execute(
                """
                SELECT solvent
                FROM solubility_grid
                WHERE polymer = 'LDPE'
                  AND temperature_c = 140
                  AND invalid_reason = 'exact_100_artifact'
                """
            ).fetchall()
        }
    finally:
        raw.close()

    global_query = registry_query(top_k=1)
    assert global_query["success"] is True
    selection = global_query["selection"]

    # Coverage is measured through the registry, not copied from the asset SQL.
    coverage: dict[str, int] = {}
    for polymer in polymer_names:
        result = registry_query(polymers=[polymer], top_k=1)
        assert result["success"] is True
        temperature_count = int(result["selection"]["temperature_count"])
        assert temperature_count == int(selection["temperature_count"])
        assert result["stored_cell_count"] % temperature_count == 0
        coverage[polymer] = result["stored_cell_count"] // temperature_count

    off_grid = registry_query(temperatures=[42], top_k=5)
    page_five = registry_query(
        polymers=["LDPE"], temperatures=[140], top_k=5, offset=0
    )
    red_global = registry_query(min_red=0, top_k=1)
    red_page_five = registry_query(
        polymers=["LDPE"], temperatures=[140], min_red=0, top_k=5, offset=0
    )
    pe_family = registry_query(polymers=["PE"], temperatures=[140], top_k=1)
    nylon_family = registry_query(
        polymers=["NYLON"], temperatures=[140], top_k=1
    )

    # Materialize the entire 990-row result through real pages.  ``total`` is
    # asserted below, but it is not treated as evidence that those rows exist.
    all_rows: list[dict[str, Any]] = []
    page_sizes: list[int] = []
    offset = 0
    while True:
        page = registry_query(
            polymers=["LDPE"], temperatures=[140], top_k=50, offset=offset
        )
        assert page["success"] is True
        assert page["offset"] == offset
        assert page["returned"] == len(page["results"])
        all_rows.extend(page["results"])
        page_sizes.append(int(page["returned"]))
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + page["returned"]
        offset = int(page["next_offset"])

    # Materialize every joined source-grain row as well.  A thermodynamic cell
    # may publish more than one row because mapped HSP grades are not collapsed.
    red_rows: list[dict[str, Any]] = []
    red_page_sizes: list[int] = []
    offset = 0
    while True:
        page = registry_query(
            polymers=["LDPE"],
            temperatures=[140],
            min_red=0,
            top_k=50,
            offset=offset,
        )
        assert page["success"] is True
        assert page["offset"] == offset
        assert page["returned"] == len(page["results"])
        red_rows.extend(page["results"])
        red_page_sizes.append(int(page["returned"]))
        if not page["has_more"]:
            assert page["next_offset"] is None
            break
        assert page["next_offset"] == offset + page["returned"]
        offset = int(page["next_offset"])

    identities = [
        (row["polymer"], row["solvent"], row["temperature_c"])
        for row in all_rows
    ]
    clipped_solvents = {
        str(row["solvent"]) for row in all_rows if row["is_clipped"] is True
    }
    atmospheric_counts = Counter(
        row["atmospheric_operation"] for row in all_rows
    )
    red_thermodynamic_identities = {
        (row["polymer"], row["solvent"], row["temperature_c"])
        for row in red_rows
    }
    red_joined_identities = {
        (
            row["polymer"],
            row["solvent"],
            row["temperature_c"],
            row["hsp_polymer_row_id"],
            row["hsp_solvent_row_id"],
        )
        for row in red_rows
    }

    # Raw asset invariants.
    assert asset == {
        "stored_cells": 253_456,
        "raw_valid": 248_378,
        "raw_flagged": 5_078,
        "saturated_stamp": 4_096,
        "nonpositive_stamp": 982,
        "polymers": 12,
        "solvents": 990,
        "temperatures": 28,
        "temperature_min": 25,
        "temperature_max": 160,
    }
    assert asset["stored_cells"] == asset["raw_valid"] + asset["raw_flagged"]
    assert asset["raw_flagged"] == (
        asset["saturated_stamp"] + asset["nonpositive_stamp"]
    )

    # Registry-level global accounting and the asset/engine policy boundary.
    assert selection == {
        "polymers": None,
        "solvents": None,
        "temperatures_c": None,
        "polymer_count": 12,
        "solvent_count": 990,
        "temperature_count": 28,
        "duplicate_polymers_removed": 0,
        "duplicate_solvents_removed": 0,
        "duplicate_temperatures_removed": 0,
    }
    cartesian = (
        selection["polymer_count"]
        * selection["solvent_count"]
        * selection["temperature_count"]
    )
    assert cartesian == 332_640 == global_query["selected_cell_count"]
    assert global_query["stored_cell_count"] == asset["stored_cells"]
    assert global_query["evaluable_cell_count"] == 252_474
    assert global_query["evaluable_cell_count"] == (
        asset["raw_valid"] + asset["saturated_stamp"]
    )
    assert global_query["unavailable_cell_count"] == 80_166
    assert global_query["unavailable_counts"] == {
        "off_grid_temperature": 0,
        "not_measured": 79_184,
        "measurement_rejected": 982,
        "measurement_rejected_by_reason": {"nonpositive": 982},
    }
    assert global_query["total"] == 252_474
    assert global_query["exclusion_counts"]["failed_any_constraint"] == 0
    assert cartesian == (
        global_query["evaluable_cell_count"]
        + global_query["unavailable_cell_count"]
    )

    # Activating either RED bound changes the result grain and publishes its
    # join coverage without changing the underlying thermodynamic accounting.
    assert red_global["success"] is True
    assert red_global["selection"] == selection
    assert red_global["selected_cell_count"] == global_query["selected_cell_count"]
    assert red_global["stored_cell_count"] == global_query["stored_cell_count"]
    assert red_global["evaluable_cell_count"] == global_query["evaluable_cell_count"]
    assert red_global["unavailable_counts"] == global_query["unavailable_counts"]
    assert red_global["constraints"] == {
        "min_solubility_pct": None,
        "max_solubility_pct": None,
        "require_atmospheric": False,
        "min_boiling_point_margin_c": None,
        "min_red": 0.0,
        "max_red": None,
    }
    assert red_global["result_grain"] == (
        "thermodynamic_cell_x_hsp_source_record_pair"
    )
    assert red_global["qualified_thermodynamic_cell_count"] == 86_592
    assert red_global["qualifying_joined_row_count"] == 141_321
    assert red_global["total"] == 141_321
    assert red_global["distinct_qualifying_solvent_count"] == 380
    assert red_global["red_join_coverage"] == {
        "requested_solvent_count": 990,
        "solvents_with_red_count": 380,
        "solvents_with_solubility_count": 990,
        "solvents_with_both_count": 380,
        "solvents_with_qualifying_red_and_solubility_count": 380,
        "requested_polymer_count": 12,
        "polymers_with_red_count": 11,
        "requested_polymer_solvent_pair_count": 11_880,
        "pairs_with_red_count": 4_180,
        "pairs_with_solubility_count": 9_047,
        "pairs_with_both_count": 3_111,
    }
    assert red_global["hsp_catalog_coverage"] == {
        "matched_hsp_polymer_source_row_count": 23,
        "total_hsp_polymer_source_row_count": 466,
        "thermodynamic_polymer_with_hsp_count": 11,
        "matched_hsp_solvent_source_row_count": 393,
        "total_hsp_solvent_source_row_count": 1_180,
        "matched_hsp_solvent_name_count": 380,
        "total_unique_hsp_solvent_name_count": 1_149,
        "grid_solvent_with_hsp_count": 380,
        "thermodynamic_polymer_count": 12,
        "grid_solvent_count": 990,
    }
    assert red_global["exclusion_counts"] == {
        "below_min_solubility_pct": 0,
        "above_max_solubility_pct": 0,
        "failed_atmospheric_requirement": 0,
        "unknown_boiling_point_for_atmospheric_requirement": 0,
        "below_min_boiling_point_margin_c": 0,
        "unknown_boiling_point_for_margin_requirement": 0,
        "failed_any_constraint": 165_882,
        "thermodynamic_cells_without_red": 165_882,
        "thermodynamic_cells_without_qualifying_red_source_record": 0,
        "red_source_record_pairs_below_min_red": 0,
        "red_source_record_pairs_above_max_red": 0,
    }
    assert red_global["red_unmatched_row_policy"] == (
        "excluded_with_explicit_coverage_and_reason_counts"
    )
    assert red_global["red_unmatched_reason"] == (
        "no_reviewed_hsp_identity_match"
    )
    assert red_global["red_temperature_dependent"] is False
    assert len(red_global["results"]) == 1
    assert set(red_global["results"][0]) == EXPECTED_RED_ROW_KEYS

    # Collection-valued polymer inputs expand known families consistently.
    assert pe_family["success"] is True
    assert pe_family["selection"]["polymers"] == ["LDPE", "HDPE"]
    assert pe_family["selection"]["polymer_count"] == 2
    assert nylon_family["success"] is True
    assert nylon_family["selection"]["polymers"] == ["NYLON6", "NYLON66"]
    assert nylon_family["selection"]["polymer_count"] == 2

    # Ragged pair coverage, all re-measured through one-axis registry calls.
    assert Counter(coverage.values()) == Counter({990: 9, 78: 1, 32: 2})
    assert coverage["PU"] == 78
    assert coverage["NYLON6"] == 32
    assert coverage["PES"] == 32
    assert sum(coverage.values()) * selection["temperature_count"] == 253_456

    # Request-level refusal remains separate from cell unavailability.
    assert off_grid["success"] is False
    assert off_grid["error_code"] == "temperature_off_grid"
    assert off_grid["off_grid_temperatures_c"] == [42.0]
    assert off_grid["nearest_grid_temperatures_c"] == [
        {"temperature_c": 42.0, "nearest_nodes_c": [40.0, 45.0]}
    ]
    assert off_grid["available_grid_temperatures_c"] == [
        float(value) for value in range(25, 161, 5)
    ]

    # Exact example: asset stamp, engine summary, and all materialized rows.
    assert ldpe_asset == {
        "stored": 990,
        "raw_valid": 972,
        "saturated_stamp": 18,
        "nonpositive_stamp": 0,
    }
    assert page_five["success"] is True
    assert page_five["selection"]["polymers"] == ["LDPE"]
    assert page_five["selection"]["solvents"] is None
    assert page_five["selection"]["temperatures_c"] == [140.0]
    assert page_five["selected_cell_count"] == 990
    assert page_five["stored_cell_count"] == 990
    assert page_five["evaluable_cell_count"] == 990
    assert page_five["unavailable_cell_count"] == 0
    assert page_five["unavailable_counts"] == {
        "off_grid_temperature": 0,
        "not_measured": 0,
        "measurement_rejected": 0,
        "measurement_rejected_by_reason": {},
    }
    assert page_five["exclusion_counts"]["failed_any_constraint"] == 0
    assert page_five["total"] == 990
    assert page_five["returned"] == len(page_five["results"]) == 5
    assert page_five["offset"] == 0
    assert page_five["has_more"] is True
    assert page_five["next_offset"] == 5
    assert all(set(row) == EXPECTED_ROW_KEYS for row in page_five["results"])

    assert page_sizes == ([50] * 19 + [40])
    assert len(all_rows) == page_five["total"] == 990
    assert len(set(identities)) == 990
    assert len({row["solvent"] for row in all_rows}) == 990
    assert all(
        row["polymer"] == "LDPE" and row["temperature_c"] == 140.0
        for row in all_rows
    )
    assert all(set(row) == EXPECTED_ROW_KEYS for row in all_rows)
    assert clipped_solvents == ldpe_saturated_solvents
    assert len(clipped_solvents) == ldpe_asset["saturated_stamp"] == 18
    assert atmospheric_counts == Counter({True: 463, False: 412, None: 115})
    assert all_rows and any(row["ghs_signal_word"] for row in all_rows)

    # The same exact selection with RED activated: observe all 786 joined rows,
    # rather than trusting the reported total or the five-row first page.
    assert red_page_five["success"] is True
    assert red_page_five["selection"] == page_five["selection"]
    assert red_page_five["selected_cell_count"] == 990
    assert red_page_five["stored_cell_count"] == 990
    assert red_page_five["evaluable_cell_count"] == 990
    assert red_page_five["unavailable_cell_count"] == 0
    assert red_page_five["constraints"] == {
        "min_solubility_pct": None,
        "max_solubility_pct": None,
        "require_atmospheric": False,
        "min_boiling_point_margin_c": None,
        "min_red": 0.0,
        "max_red": None,
    }
    assert red_page_five["result_grain"] == (
        "thermodynamic_cell_x_hsp_source_record_pair"
    )
    assert red_page_five["qualified_thermodynamic_cell_count"] == 380
    assert red_page_five["qualifying_joined_row_count"] == 786
    assert red_page_five["distinct_qualifying_solvent_count"] == 380
    assert red_page_five["total"] == 786
    assert red_page_five["returned"] == len(red_page_five["results"]) == 5
    assert red_page_five["offset"] == 0
    assert red_page_five["has_more"] is True
    assert red_page_five["next_offset"] == 5
    assert red_page_five["red_join_coverage"] == {
        "requested_solvent_count": 990,
        "solvents_with_red_count": 380,
        "solvents_with_solubility_count": 990,
        "solvents_with_both_count": 380,
        "solvents_with_qualifying_red_and_solubility_count": 380,
        "requested_polymer_count": 1,
        "polymers_with_red_count": 1,
        "requested_polymer_solvent_pair_count": 990,
        "pairs_with_red_count": 380,
        "pairs_with_solubility_count": 990,
        "pairs_with_both_count": 380,
    }
    assert red_page_five["hsp_catalog_coverage"] == red_global[
        "hsp_catalog_coverage"
    ]
    assert red_page_five["exclusion_counts"] == {
        "below_min_solubility_pct": 0,
        "above_max_solubility_pct": 0,
        "failed_atmospheric_requirement": 0,
        "unknown_boiling_point_for_atmospheric_requirement": 0,
        "below_min_boiling_point_margin_c": 0,
        "unknown_boiling_point_for_margin_requirement": 0,
        "failed_any_constraint": 610,
        "thermodynamic_cells_without_red": 610,
        "thermodynamic_cells_without_qualifying_red_source_record": 0,
        "red_source_record_pairs_below_min_red": 0,
        "red_source_record_pairs_above_max_red": 0,
    }
    assert all(
        set(row) == EXPECTED_RED_ROW_KEYS for row in red_page_five["results"]
    )

    assert red_page_sizes == ([50] * 15 + [36])
    assert len(red_rows) == red_page_five["total"] == 786
    assert len(red_joined_identities) == len(red_rows) == 786
    assert len(red_thermodynamic_identities) == 380
    assert len({row["solvent"] for row in red_rows}) == 380
    assert all(
        row["polymer"] == "LDPE"
        and row["temperature_c"] == 140.0
        and row["red"] >= 0.0
        for row in red_rows
    )
    assert all(set(row) == EXPECTED_RED_ROW_KEYS for row in red_rows)
    assert all(
        row["hsp_source_id"]
        == {
            "polymer_row_id": row["hsp_polymer_row_id"],
            "solvent_row_id": row["hsp_solvent_row_id"],
        }
        for row in red_rows
    )

    return {
        "asset": asset,
        "global_query": global_query,
        "red_global": red_global,
        "coverage": coverage,
        "off_grid": off_grid,
        "ldpe_asset": ldpe_asset,
        "page_five": page_five,
        "red_page_five": red_page_five,
        "materialized_row_count": len(all_rows),
        "materialized_unique_count": len(set(identities)),
        "materialized_page_sizes": page_sizes,
        "red_materialized_row_count": len(red_rows),
        "red_materialized_unique_count": len(red_joined_identities),
        "red_thermodynamic_cell_count": len(red_thermodynamic_identities),
        "red_materialized_page_sizes": red_page_sizes,
        "atmospheric_counts": dict(atmospheric_counts),
    }


@dataclass
class TextRecord:
    artist: Artist
    container: Artist | None
    allowed_patches: set[int] = field(default_factory=set)


@dataclass
class DrawingRegistry:
    texts: list[TextRecord] = field(default_factory=list)
    panels: dict[str, FancyBboxPatch] = field(default_factory=dict)
    collision_patches: list[Artist] = field(default_factory=list)
    connectors: list[Artist] = field(default_factory=list)
    data_marks: list[Artist] = field(default_factory=list)


def padded_bbox(box: Bbox, pixels: float) -> Bbox:
    return Bbox.from_extents(
        box.x0 - pixels,
        box.y0 - pixels,
        box.x1 + pixels,
        box.y1 + pixels,
    )


def inset_bbox(box: Bbox, pixels: float) -> Bbox:
    return Bbox.from_extents(
        box.x0 + pixels,
        box.y0 + pixels,
        box.x1 - pixels,
        box.y1 - pixels,
    )


def positive_overlap(first: Bbox, second: Bbox) -> bool:
    return (
        min(first.x1, second.x1) > max(first.x0, second.x0)
        and min(first.y1, second.y1) > max(first.y0, second.y0)
    )


def draw(data: dict[str, Any]) -> tuple[plt.Figure, DrawingRegistry, Artist]:
    fig = plt.figure(
        figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), dpi=RENDER_DPI, facecolor="white"
    )
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    registry = DrawingRegistry()

    def add_text(
        x: float,
        y: float,
        text: str,
        *,
        ha: str = "left",
        va: str = "center",
        weight: str = "normal",
        container: Artist | None = None,
        allowed: tuple[Artist, ...] = (),
        linespacing: float = 1.18,
    ) -> Artist:
        artist = ax.text(
            x,
            y,
            text,
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
        allowed_ids = {id(item) for item in allowed}
        if container is not None:
            allowed_ids.add(id(container))
        registry.texts.append(TextRecord(artist, container, allowed_ids))
        return artist

    def rounded_box(
        x: float,
        y: float,
        width: float,
        height: float,
        *,
        facecolor: str = CARD_FACE,
        edgecolor: str = PANEL_EDGE,
        linewidth: float = 0.9,
        radius: float = 0.008,
        collision: bool = True,
        zorder: float = 2,
    ) -> FancyBboxPatch:
        patch = FancyBboxPatch(
            (x, y),
            width,
            height,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            transform=ax.transAxes,
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            zorder=zorder,
        )
        ax.add_patch(patch)
        if collision:
            registry.collision_patches.append(patch)
        return patch

    def panel(name: str, x: float, y: float, width: float, height: float) -> Artist:
        patch = rounded_box(
            x,
            y,
            width,
            height,
            facecolor=PANEL_FACE,
            edgecolor=PANEL_EDGE,
            linewidth=0.9,
            radius=0.010,
            collision=False,
            zorder=1,
        )
        registry.panels[name] = patch
        return patch

    def arrow(
        start: tuple[float, float],
        end: tuple[float, float],
        *,
        color: str = ARROW,
        linewidth: float = 0.9,
        scale: float = 8.0,
    ) -> Artist:
        patch = FancyArrowPatch(
            start,
            end,
            transform=ax.transAxes,
            arrowstyle="-|>",
            mutation_scale=scale,
            linewidth=linewidth,
            color=color,
            shrinkA=0,
            shrinkB=0,
            zorder=5,
        )
        ax.add_patch(patch)
        registry.connectors.append(patch)
        return patch

    title = add_text(
        0.5,
        0.969,
        "One query over a ragged measured grid",
        ha="center",
        va="top",
        weight="semibold",
    )

    axis_panel = panel("axis", 0.035, 0.665, 0.930, 0.255)
    coverage_panel = panel("coverage", 0.035, 0.355, 0.360, 0.290)
    accounting_panel = panel("accounting", 0.415, 0.355, 0.550, 0.290)
    page_panel = panel("page", 0.035, 0.035, 0.930, 0.300)

    # 1. Independent selection axes, the opt-in RED join, and a separate
    # request-refusal branch.
    add_text(
        0.055,
        0.894,
        "1  Three fixed/free selection axes give 8 query shapes",
        weight="semibold",
        container=axis_panel,
    )
    card_specs = (
        (0.055, "Polymer", "named set  /  all 12"),
        (0.3625, "Solvent", "named set  /  all 990"),
        (0.670, "Temperature", "stored node(s)  /  all 28"),
    )
    for x, heading, choice in card_specs:
        card = rounded_box(x, 0.775, 0.275, 0.086, collision=True)
        add_text(
            x + 0.1375,
            0.839,
            heading,
            ha="center",
            weight="semibold",
            container=card,
            allowed=(axis_panel,),
        )
        add_text(
            x + 0.1375,
            0.800,
            choice,
            ha="center",
            container=card,
            allowed=(axis_panel,),
        )
    red_join_box = rounded_box(
        0.055,
        0.721,
        0.890,
        0.044,
        facecolor=RED_JOIN_FACE,
        edgecolor=SATURATED,
        collision=True,
    )
    add_text(
        0.500,
        0.743,
        "Optional min_red / max_red → source-grain Hansen join + coverage",
        ha="center",
        container=red_join_box,
        allowed=(axis_panel,),
    )
    refusal_box = rounded_box(
        0.055,
        0.673,
        0.890,
        0.039,
        facecolor=REFUSAL_FACE,
        edgecolor=REFUSAL_EDGE,
        collision=True,
    )
    add_text(
        0.500,
        0.6925,
        "42 °C → request refusal: temperature_off_grid · nearest 40, 45 °C · success=false",
        ha="center",
        container=refusal_box,
        allowed=(axis_panel,),
    )

    # 2. Ragged pair coverage.  The endpoint marker keeps small linear values
    # legible without changing their proportional bar lengths.
    add_text(
        0.055,
        0.617,
        "2  Coverage is ragged",
        weight="semibold",
        container=coverage_panel,
    )
    add_text(
        0.055,
        0.582,
        "stored solubility pairs / 990",
        container=coverage_panel,
    )
    red_coverage_mark = Rectangle(
        (0.055, 0.540),
        0.015,
        0.015,
        transform=ax.transAxes,
        facecolor=SATURATED,
        edgecolor="none",
        zorder=4,
    )
    ax.add_patch(red_coverage_mark)
    registry.collision_patches.append(red_coverage_mark)
    registry.data_marks.append(red_coverage_mark)
    red_coverage = data["red_global"]["red_join_coverage"]
    add_text(
        0.078,
        0.548,
        (
            "Hansen RED solvents: "
            f"{red_coverage['solvents_with_red_count']} / "
            f"{red_coverage['requested_solvent_count']}"
        ),
        container=coverage_panel,
    )
    coverage_rows = (
        ("9 polymers", 990),
        ("PU", data["coverage"]["PU"]),
        ("NYLON6", data["coverage"]["NYLON6"]),
        ("PES", data["coverage"]["PES"]),
    )
    track_x = 0.235
    track_width = 0.135
    bar_height = 0.019
    for (label, value), y in zip(coverage_rows, (0.505, 0.460, 0.415, 0.375)):
        add_text(0.055, y, label, container=coverage_panel)
        add_text(
            0.210,
            y,
            f"{value}",
            ha="right",
            container=coverage_panel,
        )
        track = Rectangle(
            (track_x, y - bar_height / 2),
            track_width,
            bar_height,
            transform=ax.transAxes,
            facecolor=TRACK,
            edgecolor="none",
            zorder=3,
        )
        fill_width = track_width * value / 990
        fill = Rectangle(
            (track_x, y - bar_height / 2),
            fill_width,
            bar_height,
            transform=ax.transAxes,
            facecolor=RAW_VALID,
            edgecolor="none",
            zorder=4,
        )
        ax.add_patch(track)
        ax.add_patch(fill)
        registry.collision_patches.extend((track, fill))
        registry.data_marks.extend((track, fill))
        endpoint = ax.plot(
            [track_x + fill_width],
            [y],
            marker="o",
            markersize=7.2,
            markerfacecolor=RAW_VALID,
            markeredgecolor=RAW_VALID,
            linestyle="none",
            transform=ax.transAxes,
            zorder=5,
        )[0]
        registry.data_marks.append(endpoint)

    # 3. Raw asset stamps and their deliberately different engine outcomes.
    add_text(
        0.435,
        0.617,
        "3  Asset stamp ≠ engine disposition",
        weight="semibold",
        container=accounting_panel,
    )
    add_text(
        0.435,
        0.580,
        "332,640-cell Cartesian envelope",
        container=accounting_panel,
    )
    stored_mark = Rectangle(
        (0.438, 0.539),
        0.015,
        0.015,
        transform=ax.transAxes,
        facecolor=RAW_VALID,
        edgecolor="none",
        zorder=4,
    )
    missing_mark = Rectangle(
        (0.690, 0.539),
        0.015,
        0.015,
        transform=ax.transAxes,
        facecolor=TRACK,
        edgecolor="none",
        zorder=4,
    )
    ax.add_patch(stored_mark)
    ax.add_patch(missing_mark)
    registry.collision_patches.extend((stored_mark, missing_mark))
    registry.data_marks.extend((stored_mark, missing_mark))
    add_text(0.462, 0.547, "253,456 stored", container=accounting_panel)
    add_text(0.714, 0.547, "79,184 not measured", container=accounting_panel)
    add_text(
        0.435,
        0.513,
        "Engine: 252,474 evaluable  ·  982 unavailable",
        container=accounting_panel,
    )
    outcome_rows = (
        (0.477, "248,378 raw-valid  →  served", RAW_VALID),
        (
            0.410,
            "4,096 exact_100_artifact  →  served: saturated",
            SATURATED,
        ),
        (
            0.376,
            "982 nonpositive  →  measurement-rejected",
            REJECTED,
        ),
    )
    for y, label, color in outcome_rows:
        marker = Rectangle(
            (0.480, y - 0.008),
            0.014,
            0.016,
            transform=ax.transAxes,
            facecolor=color,
            edgecolor="none",
            zorder=4,
        )
        ax.add_patch(marker)
        registry.collision_patches.append(marker)
        registry.data_marks.append(marker)
        add_text(0.505, y, label, container=accounting_panel)

    add_text(
        0.480,
        0.444,
        "5,078 asset-flagged split:",
        weight="semibold",
        container=accounting_panel,
    )

    # 4. One exact slice exposes qualification before pagination.
    add_text(
        0.055,
        0.307,
        "4  Qualify first; page second",
        weight="semibold",
        container=page_panel,
    )
    add_text(
        0.055,
        0.275,
        "Base selection: LDPE × all 990 solvents × 140 °C · no filters (RED inactive)",
        container=page_panel,
    )
    add_text(
        0.055,
        0.243,
        "Asset: 990 stored = 972 raw-valid + 18 exact_100_artifact",
        container=page_panel,
    )

    evaluable_box = rounded_box(0.055, 0.167, 0.122, 0.062, collision=True)
    predicate_box = rounded_box(0.230, 0.167, 0.160, 0.062, collision=True)
    qualified_box = rounded_box(0.445, 0.167, 0.120, 0.062, collision=True)
    add_text(
        0.116,
        0.198,
        "990\nevaluable",
        ha="center",
        weight="semibold",
        container=evaluable_box,
        allowed=(page_panel,),
    )
    add_text(
        0.310,
        0.198,
        "cell predicates\n0 exclusions",
        ha="center",
        container=predicate_box,
        allowed=(page_panel,),
    )
    add_text(
        0.505,
        0.198,
        "990\nqualified",
        ha="center",
        weight="semibold",
        container=qualified_box,
        allowed=(page_panel,),
    )
    arrow((0.183, 0.198), (0.222, 0.198))
    arrow((0.398, 0.198), (0.437, 0.198))
    arrow((0.573, 0.198), (0.620, 0.198))

    page_boxes: list[Artist] = []
    page_x0 = 0.630
    page_width = 0.034
    page_gap = 0.006
    for number in range(1, 6):
        x = page_x0 + (number - 1) * (page_width + page_gap)
        box = rounded_box(
            x,
            0.174,
            page_width,
            0.048,
            facecolor=PAGE,
            edgecolor=PAGE,
            linewidth=0.8,
            radius=0.004,
            collision=True,
        )
        page_boxes.append(box)
        add_text(
            x + page_width / 2,
            0.198,
            str(number),
            ha="center",
            weight="semibold",
            container=box,
            allowed=(page_panel,),
        )
    add_text(0.836, 0.198, "…", ha="center", container=page_panel)
    add_text(0.865, 0.198, "985 later", container=page_panel)
    add_text(
        0.670,
        0.145,
        "offset=0  ·  returned=5  ·  has_more=true  ·  next_offset=5",
        ha="center",
        container=page_panel,
    )
    add_text(
        0.500,
        0.115,
        "Row: identity · T · exact wt% · BP/margin · atmosphere {true/false/unknown} · GHS signal word",
        ha="center",
        container=page_panel,
    )
    add_text(
        0.500,
        0.085,
        (
            "RED active (min_red=0): "
            f"{data['red_page_five']['qualified_thermodynamic_cell_count']} cells joined · "
            f"{data['red_page_five']['exclusion_counts']['thermodynamic_cells_without_red']} unmatched · "
            f"{data['red_page_five']['qualifying_joined_row_count']} source-record rows"
        ),
        ha="center",
        container=page_panel,
    )
    add_text(
        0.500,
        0.055,
        (
            "Joined rows add RED/Ra/R0 + HSP source IDs; coverage reports "
            f"{data['red_page_five']['red_join_coverage']['solvents_with_red_count']} of "
            f"{data['red_page_five']['red_join_coverage']['requested_solvent_count']} solvents"
        ),
        ha="center",
        container=page_panel,
    )

    return fig, registry, title


def verify_geometry(
    fig: plt.Figure,
    registry: DrawingRegistry,
    title: Artist,
) -> dict[str, float | int]:
    """Assert the paper's typography and collision standards on rendered extents."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    pixels_per_point = fig.dpi / 72.0
    gap = 2.0 * pixels_per_point
    border_inset = 4.0 * pixels_per_point

    figure_box = fig.bbox
    text_boxes: list[Bbox] = []
    for record in registry.texts:
        artist = record.artist
        assert float(artist.get_fontsize()) == FONT_SIZE_PT
        assert to_rgba(artist.get_color()) == to_rgba(BLACK)
        box = artist.get_window_extent(renderer)
        assert box.width > 0 and box.height > 0
        assert box.height / pixels_per_point >= 7.0
        assert (
            box.x0 >= figure_box.x0
            and box.y0 >= figure_box.y0
            and box.x1 <= figure_box.x1
            and box.y1 <= figure_box.y1
        )
        if record.container is not None:
            container_box = inset_bbox(
                record.container.get_window_extent(renderer), border_inset
            )
            assert (
                box.x0 >= container_box.x0
                and box.y0 >= container_box.y0
                and box.x1 <= container_box.x1
                and box.y1 <= container_box.y1
            ), f"text escapes/touches container: {artist.get_text()!r}"
        text_boxes.append(box)

    # No text-text collision, with a real two-point clear space.
    for index, first in enumerate(text_boxes):
        first_padded = padded_bbox(first, gap / 2)
        for other_index in range(index + 1, len(text_boxes)):
            second_padded = padded_bbox(text_boxes[other_index], gap / 2)
            assert not positive_overlap(first_padded, second_padded), (
                "text collision: "
                f"{registry.texts[index].artist.get_text()!r} and "
                f"{registry.texts[other_index].artist.get_text()!r}"
            )

    # No text may meet an unrelated card, bar, or data mark.  Text inside its
    # explicitly declared card is checked by the inset assertion above.
    for record, text_box in zip(registry.texts, text_boxes):
        expanded_text = padded_bbox(text_box, gap / 2)
        for patch in registry.collision_patches:
            if id(patch) in record.allowed_patches:
                continue
            patch_box = patch.get_window_extent(renderer)
            assert not positive_overlap(expanded_text, patch_box), (
                f"text/shape collision: {record.artist.get_text()!r}"
            )

    # Connectors must also clear all text; their intentional contacts are only
    # with box edges and are therefore not whitelisted against words.
    for connector in registry.connectors:
        connector_box = connector.get_window_extent(renderer)
        for record, text_box in zip(registry.texts, text_boxes):
            assert not positive_overlap(
                padded_bbox(text_box, gap / 2), connector_box
            ), f"text/connector collision: {record.artist.get_text()!r}"

    # The title is no wider than, and horizontally contained by, the content.
    title_box = title.get_window_extent(renderer)
    content_boxes = [
        patch.get_window_extent(renderer) for patch in registry.panels.values()
    ]
    content_left = min(box.x0 for box in content_boxes)
    content_right = max(box.x1 for box in content_boxes)
    assert title_box.width <= content_right - content_left
    assert title_box.x0 >= content_left and title_box.x1 <= content_right

    # Data marks are intentionally substantial at final print size.  Linear
    # coverage fills may be narrow, but their height and endpoint marker remain
    # at least seven points; exact values are printed beside them.
    for mark in registry.data_marks:
        box = mark.get_window_extent(renderer)
        assert max(box.width, box.height) / pixels_per_point >= 7.0

    return {
        "text_count": len(registry.texts),
        "minimum_text_height_pt": min(
            box.height / pixels_per_point for box in text_boxes
        ),
        "title_width_pt": title_box.width / pixels_per_point,
        "content_width_pt": (content_right - content_left) / pixels_per_point,
    }


def save_outputs(fig: plt.Figure) -> None:
    fig.savefig(
        PDF_PATH,
        format="pdf",
        dpi=PNG_DPI,
        facecolor="white",
        edgecolor="none",
        metadata={
            "Title": "One query over a ragged measured grid",
            "Creator": "figures/unified_solubility_query.py",
            "CreationDate": None,
            "ModDate": None,
        },
    )
    fig.savefig(
        PNG_PATH,
        format="png",
        dpi=PNG_DPI,
        facecolor="white",
        edgecolor="none",
        metadata={"Software": "figures/unified_solubility_query.py"},
    )
    assert PDF_PATH.is_file() and PDF_PATH.stat().st_size > 10_000
    assert PNG_PATH.is_file() and PNG_PATH.stat().st_size > 10_000
    image = plt.imread(PNG_PATH)
    assert image.shape[1] == round(FIG_WIDTH_IN * PNG_DPI)
    assert image.shape[0] == round(FIG_HEIGHT_IN * PNG_DPI)


def main() -> None:
    data = measure()
    fig, drawing_registry, title = draw(data)
    geometry = verify_geometry(fig, drawing_registry, title)
    save_outputs(fig)
    plt.close(fig)
    print(
        json.dumps(
            {
                "commit_contract": "rebased measurements asserted in script",
                "asset": data["asset"],
                "coverage": data["coverage"],
                "engine_evaluable": data["global_query"]["evaluable_cell_count"],
                "engine_rejected": data["global_query"]["unavailable_counts"][
                    "measurement_rejected"
                ],
                "ldpe_140_materialized_rows": data["materialized_row_count"],
                "ldpe_140_unique_rows": data["materialized_unique_count"],
                "hansen_red_solvent_coverage": data["red_global"][
                    "red_join_coverage"
                ]["solvents_with_red_count"],
                "hansen_red_grid_solvent_count": data["red_global"][
                    "red_join_coverage"
                ]["requested_solvent_count"],
                "ldpe_140_red_materialized_rows": data[
                    "red_materialized_row_count"
                ],
                "ldpe_140_red_unique_rows": data[
                    "red_materialized_unique_count"
                ],
                "ldpe_140_red_thermodynamic_cells": data[
                    "red_thermodynamic_cell_count"
                ],
                "geometry": geometry,
                "outputs": [str(PDF_PATH), str(PNG_PATH)],
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
