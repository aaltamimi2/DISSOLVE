#!/usr/bin/env python3
"""Reproduce the landscape drafts and prove every admitted must-fire control.

Run from the repository root with::

    PYTHONPATH=src:. python figures/check_results_s1_landscape.py

Exit zero means the current figures passed and every deliberately defective
case was rejected for the named reason.  Any accept-all injected checker makes
at least one defect pass and therefore makes this harness return nonzero.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
import xml.etree.ElementTree as ET

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np

try:  # Package import in tests; direct import for documented CLI.
    from . import results_s1_landscape as landscape
    from .standards_checker import (
        FigureStandards,
        StandardsViolation,
        check_byte_identical,
        check_data_layer_bounds,
        check_data_role_closed_world,
        check_figure,
        check_output_pair,
    )
except ImportError:  # pragma: no cover
    import results_s1_landscape as landscape
    from standards_checker import (
        FigureStandards,
        StandardsViolation,
        check_byte_identical,
        check_data_layer_bounds,
        check_data_role_closed_world,
        check_figure,
        check_output_pair,
    )


Checker = Callable[..., dict[str, object]]


def _expect(
    name: str,
    expected: str,
    action: Callable[[], object],
    failures: list[str],
) -> None:
    try:
        action()
    except (landscape.SourceDigestError, landscape.LandscapeValidationError, StandardsViolation) as error:
        lines = str(error).splitlines()
        matching = [line for line in lines if expected in line]
        if matching:
            print(f"MUST-FIRE {name}: FAIL (expected) — {matching[0]}")
        else:
            failures.append(
                f"{name}: rejected, but not for expected token {expected!r}: {error}"
            )
            print(f"MUST-FIRE {name}: WRONG FAILURE")
    else:
        failures.append(f"{name}: defective case was accepted")
        print(f"MUST-FIRE {name}: UNEXPECTED PASS")


def _save_svg(
    drawing: landscape.LandscapeDrawing,
    path: Path,
    stem: str,
    *,
    cloud_image_limit: int | None = None,
) -> None:
    drawing.fig.savefig(
        path,
        format="svg",
        facecolor="white",
        edgecolor="none",
        metadata={
            "Creator": "figures/check_results_s1_landscape.py",
            "Date": "2026-08-26",
            "Title": stem,
        },
    )
    landscape._postprocess_svg(
        path, stem, cloud_image_limit=cloud_image_limit
    )


def _mutated_threshold_source(source: Path, destination: Path) -> None:
    text = source.read_text(encoding="utf-8")
    old = "solubility_threshold_pct: float = 5.0"
    if text.count(old) != 1:
        raise AssertionError("threshold must-fire fixture could not find exact default")
    destination.write_text(text.replace(old, "solubility_threshold_pct: float = 4.0"), encoding="utf-8")


def _validation_controls(
    data: landscape.LandscapeData,
    failures: list[str],
) -> None:
    for source in (
        landscape.DB_PATH,
        landscape.READER_PATH,
        landscape.TOOLS_PATH,
        landscape.SEPARATION_PATH,
    ):
        wrong = dict(landscape.EXPECTED_SOURCE_DIGESTS)
        wrong[source] = "0" * 64
        _expect(
            f"source digest {source.name}",
            "source digest mismatch",
            lambda wrong=wrong: landscape.measure(expected_digests=wrong),
            failures,
        )

    with TemporaryDirectory() as temp_dir:
        changed_tools = Path(temp_dir) / "tools.py"
        _mutated_threshold_source(landscape.TOOLS_PATH, changed_tools)
        _expect(
            "parsed threshold default",
            "threshold source default moved from 5.0",
            lambda: landscape.inspect_threshold_defaults(tools_path=changed_tools),
            failures,
        )

    without_ceiling = tuple(cell for cell in data.cells if not cell.is_served_ceiling)
    _expect(
        "exact-100 omission",
        "evaluable cell count changed",
        lambda: landscape.validate(replace(data, cells=without_ceiling)),
        failures,
    )

    admitted_nonpositive = data.cells + (
        replace(data.cells[0], solubility_pct=0.0, is_served_ceiling=False),
    )
    _expect(
        "nonpositive admitted",
        "nonpositive or nonfinite value entered",
        lambda: landscape.validate(
            replace(data, cells=admitted_nonpositive, raw_valid_cells=data.raw_valid_cells + 1)
        ),
        failures,
    )

    first_fraction = data.fractions[0]
    wrong_denominator = replace(
        first_fraction,
        denominator=first_fraction.denominator - 1,
        fraction=first_fraction.numerator / (first_fraction.denominator - 1),
    )
    _expect(
        "fixed denominator",
        "fixed denominator changed",
        lambda: landscape.validate(
            replace(data, fractions=(wrong_denominator, *data.fractions[1:]))
        ),
        failures,
    )

    wrong_numerator = replace(
        first_fraction,
        numerator=first_fraction.numerator + 1,
        fraction=(first_fraction.numerator + 1) / first_fraction.denominator,
    )
    _expect(
        "fraction numerator",
        "fraction numerators do not close",
        lambda: landscape.validate(
            replace(data, fractions=(wrong_numerator, *data.fractions[1:]))
        ),
        failures,
    )

    pu_points = [point for point in data.fractions if point.polymer == "PU"]
    flat = pu_points[0]
    flattened = tuple(
        replace(
            point,
            numerator=flat.numerator,
            fraction=flat.numerator / point.denominator,
        )
        if point.polymer == "PU" else point
        for point in data.fractions
    )
    _expect(
        "flat fraction series",
        "fraction series is constant for PU",
        lambda: landscape.validate(replace(data, fractions=flattened)),
        failures,
    )

    filtered = tuple(cell for cell in data.cells if cell.polymer == "EVOH")
    _expect(
        "named polymer filter",
        "scatter polymer roster changed",
        lambda: landscape.validate(replace(data, cells=filtered)),
        failures,
    )


def _geometry_controls(
    data: landscape.LandscapeData,
    *,
    figure_checker: Checker,
    bounds_checker: Checker,
    role_checker: Checker,
    failures: list[str],
) -> None:
    scatter = landscape.draw_solubility_scatter(data)
    try:
        ceiling = next(
            record for record in scatter.registry.data_layers
            if record.series_id.endswith(":ceiling")
        )
        original_id = ceiling.series_id
        ceiling.series_id = original_id.replace(":ceiling", ":raw")
        _expect(
            "served ceiling in filled layer",
            "wrong visual layer",
            lambda: landscape.check_scatter_layer_contract(data, scatter.registry),
            failures,
        )
        ceiling.series_id = original_id

        outside = scatter.registry.data_layers[0]
        old_x = outside.x_values
        changed_x = np.asarray(old_x).copy()
        changed_x[0] = 1_000.0
        outside.x_values = changed_x
        scatter.fig.canvas.draw()
        _expect(
            "clipped out-of-bounds data",
            "source geometry leaves its data region",
            lambda: bounds_checker(
                outside, scatter.fig.canvas.get_renderer(), tolerance=1e-6
            ),
            failures,
        )
        outside.x_values = old_x

        small = scatter.registry.data_layers[0]
        old_sizes = small.artist.get_sizes().copy()
        small.artist.set_sizes([6.0**2])
        _expect(
            "undersized data marker",
            "data layer marker is too small",
            lambda: bounds_checker(
                small, scatter.fig.canvas.get_renderer(), tolerance=1e-6
            ),
            failures,
        )
        small.artist.set_sizes(old_sizes)

        removed = scatter.registry.data_layers.pop(0)
        scatter.registry.structural_artists.append(removed.artist)
        _expect(
            "data artist structural escape",
            "registered only as structural",
            lambda: role_checker(scatter.fig, scatter.registry),
            failures,
        )
        scatter.registry.structural_artists.remove(removed.artist)
        scatter.registry.data_layers.insert(0, removed)

        legend_text = next(
            record.artist for record in scatter.registry.texts
            if record.artist.get_text() == "EVOH"
        )
        old_position = legend_text.get_position()
        legend_text.set_position((0.35, 0.55))
        _expect(
            "legend text in data region",
            "text enters the scientific data region",
            lambda: figure_checker(
                scatter.fig,
                scatter.registry,
                scatter.title,
                standards=FigureStandards(font_size_pt=landscape.FONT_SIZE_PT),
                content_artists=[scatter.fig.axes[0].patch],
            ),
            failures,
        )
        legend_text.set_position(old_position)

        extra = scatter.fig.add_axes((0.80, 0.02, 0.05, 0.05))
        extra.axis("off")
        _expect(
            "single axes",
            "exactly one axes",
            lambda: figure_checker(
                scatter.fig,
                scatter.registry,
                scatter.title,
                standards=FigureStandards(font_size_pt=landscape.FONT_SIZE_PT),
                content_artists=[scatter.fig.axes[0].patch],
            ),
            failures,
        )
        extra.remove()
    finally:
        plt.close(scatter.fig)


def _style_and_displacement_controls(
    data: landscape.LandscapeData,
    *,
    style_checker: Checker,
    displacement_checker: Checker,
    failures: list[str],
) -> None:
    scatter = landscape.draw_solubility_scatter(data)
    fraction = landscape.draw_dissolving_fraction(data)
    try:
        pp = next(
            record for record in fraction.registry.data_layers
            if record.series_id == "fraction:PP"
        )
        old_marker = pp.artist.get_marker()
        pp.artist.set_marker("o")
        _expect(
            "cross-figure polymer style",
            "polymer marker mismatch",
            lambda: style_checker(
                scatter.registry, fraction.registry, landscape.POLYMER_STYLE_MAP
            ),
            failures,
        )
        pp.artist.set_marker(old_marker)

        wrong_x = list(scatter.rendered_x)
        wrong_x[0] += 0.01
        wrong_manifest = landscape.displacement_manifest(data.cells, wrong_x)
        _expect(
            "wrong deterministic displacement",
            "display displacement differs",
            lambda: displacement_checker(data.cells, wrong_x, wrong_manifest),
            failures,
        )
    finally:
        plt.close(scatter.fig)
        plt.close(fraction.fig)


def _output_controls(
    *, output_checker: Checker, failures: list[str]
) -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        png = root / "control.png"
        svg = root / "control.svg"
        png.write_bytes(b"png")
        svg.write_bytes(b"svg")
        png.unlink()
        _expect(
            "required PNG",
            "missing PNG output",
            lambda: output_checker(png, svg),
            failures,
        )
        png.write_bytes(b"png")
        svg.unlink()
        _expect(
            "required SVG",
            "missing SVG output",
            lambda: output_checker(png, svg),
            failures,
        )


def _svg_controls(
    data: landscape.LandscapeData,
    *, svg_checker: Checker, failures: list[str]
) -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        valid_fraction = root / "valid_fraction.svg"
        fraction = landscape.draw_dissolving_fraction(data)
        try:
            _save_svg(fraction, valid_fraction, landscape.FRACTION_STEM)
        finally:
            plt.close(fraction.fig)

        vector_scatter = landscape.draw_solubility_scatter(
            data, cloud_rasterized=False
        )
        vector_svg = root / "vector_scatter.svg"
        try:
            for record in vector_scatter.registry.data_layers:
                offsets = record.artist.get_offsets()
                record.artist.set_offsets(offsets[:2])
            _save_svg(vector_scatter, vector_svg, landscape.SCATTER_STEM)
        finally:
            plt.close(vector_scatter.fig)
        _expect(
            "Figure A vector cloud",
            "solubility-cloud has no embedded image",
            lambda: svg_checker(vector_svg, valid_fraction),
            failures,
        )

        raster_nondata = landscape.draw_solubility_scatter(
            data, nondata_rasterized=True
        )
        raster_nondata_svg = root / "raster_nondata.svg"
        try:
            _save_svg(
                raster_nondata,
                raster_nondata_svg,
                landscape.SCATTER_STEM,
                cloud_image_limit=1,
            )
        finally:
            plt.close(raster_nondata.fig)
        _expect(
            "Figure A rasterized nondata",
            "image outside solubility-cloud",
            lambda: svg_checker(raster_nondata_svg, valid_fraction),
            failures,
        )

        valid_scatter = root / "valid_scatter.svg"
        scatter = landscape.draw_solubility_scatter(data)
        try:
            _save_svg(scatter, valid_scatter, landscape.SCATTER_STEM)
        finally:
            plt.close(scatter.fig)
        raster_fraction = landscape.draw_dissolving_fraction(
            data, series_rasterized=True
        )
        raster_fraction_svg = root / "raster_fraction.svg"
        try:
            _save_svg(raster_fraction, raster_fraction_svg, landscape.FRACTION_STEM)
        finally:
            plt.close(raster_fraction.fig)
        _expect(
            "Figure B rasterized series",
            "Figure B contains an image",
            lambda: svg_checker(valid_scatter, raster_fraction_svg),
            failures,
        )


def main(
    *,
    figure_checker: Checker | None = None,
    output_checker: Checker | None = None,
    svg_checker: Checker | None = None,
    style_checker: Checker | None = None,
    bounds_checker: Checker | None = None,
    role_checker: Checker | None = None,
    displacement_checker: Checker | None = None,
) -> int:
    figure_checker = figure_checker or check_figure
    output_checker = output_checker or check_output_pair
    svg_checker = svg_checker or landscape.check_svg_raster_contract
    style_checker = style_checker or landscape.check_polymer_style_contract
    bounds_checker = bounds_checker or check_data_layer_bounds
    role_checker = role_checker or check_data_role_closed_world
    displacement_checker = displacement_checker or landscape.check_display_displacement
    failures: list[str] = []

    data = landscape.measure()
    scatter = landscape.draw_solubility_scatter(data)
    fraction = landscape.draw_dissolving_fraction(data)
    try:
        for stem, drawing in (
            (landscape.SCATTER_STEM, scatter),
            (landscape.FRACTION_STEM, fraction),
        ):
            try:
                report = figure_checker(
                    drawing.fig,
                    drawing.registry,
                    drawing.title,
                    standards=FigureStandards(font_size_pt=landscape.FONT_SIZE_PT),
                    content_artists=[drawing.fig.axes[0].patch],
                )
            except StandardsViolation as error:
                failures.append(f"current {stem} geometry failed: {error}")
                print(f"CURRENT {stem} geometry: FAIL")
            else:
                print(f"CURRENT {stem} geometry: PASS {report}")
        landscape.check_scatter_layer_contract(data, scatter.registry)
        style_checker(scatter.registry, fraction.registry, landscape.POLYMER_STYLE_MAP)
        renderer = scatter.fig.canvas.get_renderer()
        for record in scatter.registry.data_layers:
            bounds_checker(record, renderer, tolerance=1e-6)
        role_checker(scatter.fig, scatter.registry)
        manifest = landscape.displacement_manifest(data.cells, scatter.rendered_x)
        displacement_checker(data.cells, scatter.rendered_x, manifest)
        print("CURRENT semantic contracts: PASS")
    except StandardsViolation as error:
        failures.append(f"current semantic contract failed: {error}")
        print(f"CURRENT semantic contracts: FAIL — {error}")
    finally:
        plt.close(scatter.fig)
        plt.close(fraction.fig)

    for stem in (landscape.SCATTER_STEM, landscape.FRACTION_STEM):
        try:
            output_checker(
                landscape.HERE / f"{stem}.png",
                landscape.HERE / f"{stem}.svg",
            )
        except StandardsViolation as error:
            failures.append(f"current {stem} output pair failed: {error}")
            print(f"CURRENT {stem} output pair: FAIL")
        else:
            print(f"CURRENT {stem} output pair: PASS")
    try:
        svg_checker(
            landscape.HERE / f"{landscape.SCATTER_STEM}.svg",
            landscape.HERE / f"{landscape.FRACTION_STEM}.svg",
        )
    except (OSError, ET.ParseError, StandardsViolation) as error:  # type: ignore[name-defined]
        failures.append(f"current SVG contract failed: {error}")
        print(f"CURRENT SVG contract: FAIL — {error}")
    else:
        print("CURRENT SVG contract: PASS")

    _validation_controls(data, failures)
    _geometry_controls(
        data,
        figure_checker=figure_checker,
        bounds_checker=bounds_checker,
        role_checker=role_checker,
        failures=failures,
    )
    _style_and_displacement_controls(
        data,
        style_checker=style_checker,
        displacement_checker=displacement_checker,
        failures=failures,
    )
    _output_controls(output_checker=output_checker, failures=failures)
    _svg_controls(data, svg_checker=svg_checker, failures=failures)

    with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
        first_root = Path(first_dir)
        second_root = Path(second_dir)
        landscape.generate(first_root)
        landscape.generate(second_root)
        first = tuple(first_root / name for name in landscape.generated_names())
        second = tuple(second_root / name for name in landscape.generated_names())
        try:
            check_byte_identical(first, second)
        except StandardsViolation as error:
            failures.append(f"clean generation is not deterministic: {error}")
            print("CURRENT byte determinism: FAIL")
        else:
            print("CURRENT byte determinism: PASS")
        altered = second_root / f"{landscape.SCATTER_STEM}.png"
        altered.write_bytes(altered.read_bytes() + b"must-fire")
        _expect(
            "altered output byte",
            "determinism mismatch",
            lambda: check_byte_identical(first, second),
            failures,
        )

    if failures:
        print("LANDSCAPE CHECKER CONTROL FAILURE:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
