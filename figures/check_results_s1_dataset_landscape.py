#!/usr/bin/env python3
"""Reproduce the admitted dataset-landscape drafts and fire every control.

Run from the repository root with::

    PYTHONPATH=src:. python figures/check_results_s1_dataset_landscape.py

The current outputs must pass.  Each deliberately defective fixture must be
rejected for the named reason.  Therefore an injected accept-all checker makes
this harness fail rather than giving a false green result.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

try:  # Package import in tests; direct import for the documented CLI.
    from . import results_s1_dataset_landscape as landscape
    from .standards_checker import (
        StandardsViolation,
        check_byte_identical,
        check_output_pair,
    )
except ImportError:  # pragma: no cover
    import results_s1_dataset_landscape as landscape
    from standards_checker import (
        StandardsViolation,
        check_byte_identical,
        check_output_pair,
    )


Checker = Callable[..., object]
EXPECTED_EXCEPTIONS = (
    landscape.SourceDigestError,
    landscape.DatasetLandscapeValidationError,
    StandardsViolation,
)


def _expect(
    name: str,
    token: str,
    action: Callable[[], object],
    failures: list[str],
) -> None:
    try:
        action()
    except EXPECTED_EXCEPTIONS as error:
        match = next((line for line in str(error).splitlines() if token in line), None)
        if match is None:
            failures.append(
                f"{name}: rejected for the wrong reason; wanted {token!r}: {error}"
            )
            print(f"MUST-FIRE {name}: WRONG FAILURE")
        else:
            print(f"MUST-FIRE {name}: FAIL (expected) — {match}")
    else:
        failures.append(f"{name}: defective case was accepted")
        print(f"MUST-FIRE {name}: UNEXPECTED PASS")


def _move_one(values: tuple[int, ...]) -> tuple[int, ...]:
    changed = list(values)
    source = next(index for index, count in enumerate(changed) if count > 0)
    destination = (source + 1) % len(changed)
    changed[source] -= 1
    changed[destination] += 1
    return tuple(changed)


def _semantic_controls(
    data: landscape.DatasetLandscapeData,
    failures: list[str],
) -> None:
    wrong_digest = dict(landscape.EXPECTED_SOURCE_DIGESTS)
    wrong_digest[landscape.DB_PATH] = "0" * 64
    _expect(
        "source digest",
        "source digest mismatch",
        lambda: landscape.measure(expected_digests=wrong_digest),
        failures,
    )

    _expect(
        "cohort/PU mutation",
        "cohort scalar counts changed",
        lambda: landscape.validate(replace(data, cohort_polymers=12)),
        failures,
    )
    _expect(
        "stored/evaluable confusion",
        "cohort scalar counts changed",
        lambda: landscape.validate(replace(data, evaluable_cells=data.stored_cells)),
        failures,
    )
    _expect(
        "refused nonpositive mutation",
        "cohort scalar counts changed",
        lambda: landscape.validate(replace(data, refused_cells=data.refused_cells - 1)),
        failures,
    )

    first_heat = data.heat_cells[0]
    changed_heat = replace(first_heat, numerator=first_heat.numerator + 1)
    _expect(
        "heat numerator",
        "heat payload digest changed",
        lambda: landscape.validate(
            replace(data, heat_cells=(changed_heat, *data.heat_cells[1:]))
        ),
        failures,
    )

    second_heat = data.heat_cells[1]
    shifted_denominators = (
        replace(first_heat, denominator=first_heat.denominator + 1),
        replace(second_heat, denominator=second_heat.denominator - 1),
        *data.heat_cells[2:],
    )
    _expect(
        "heat denominator",
        "heat payload digest changed",
        lambda: landscape.validate(replace(data, heat_cells=shifted_denominators)),
        failures,
    )

    onset = list(data.onset_counts)
    polymer, counts = onset[0]
    onset[0] = (polymer, _move_one(counts))
    _expect(
        "onset bin move with closure preserved",
        "onset payload digest changed",
        lambda: landscape.validate(replace(data, onset_counts=tuple(onset))),
        failures,
    )

    densities = list(data.density_counts)
    polymer, rows = densities[0]
    changed_rows = list(rows)
    changed_rows[0] = _move_one(changed_rows[0])
    densities[0] = (polymer, tuple(changed_rows))
    _expect(
        "density bin move with closure preserved",
        "density payload digest changed",
        lambda: landscape.validate(replace(data, density_counts=tuple(densities))),
        failures,
    )

    _expect(
        "property admission classification",
        "property payload digest changed",
        lambda: landscape.validate(
            replace(data, admission_points=data.admission_points[:-1])
        ),
        failures,
    )

    changed_edges = list(landscape.RAW_LOG_EDGES)
    changed_edges[1] += 0.01
    _expect(
        "density bin edge",
        "density raw log10 bin edges changed",
        lambda: landscape.check_density_bin_contract(changed_edges),
        failures,
    )


def _geometry_controls(
    data: landscape.DatasetLandscapeData,
    *,
    figure_checker: Checker,
    failures: list[str],
) -> None:
    heat = landscape.draw_fraction_heatmap(data)
    try:
        extra = heat.fig.add_axes((0.90, 0.01, 0.02, 0.02))
        extra.axis("off")
        _expect(
            "single axes",
            "exactly one axes",
            lambda: figure_checker(heat),
            failures,
        )
        extra.remove()

        hidden = heat.fig.text(
            0.02,
            0.02,
            "unregistered",
            fontsize=landscape.FONT_SIZE,
            color=landscape.BLACK,
        )
        _expect(
            "unregistered text",
            "visible text is not registered",
            lambda: figure_checker(heat),
            failures,
        )
        hidden.remove()

        layer = heat.registry.data_layers.pop(0)
        heat.registry.structural_artists.append(layer.artist)
        _expect(
            "data structural escape",
            "registered only as structural",
            lambda: figure_checker(heat),
            failures,
        )
        heat.registry.structural_artists.remove(layer.artist)
        heat.registry.data_layers.append(layer)

        original_x = layer.x_values
        changed_x = original_x.copy()
        changed_x[0] = 1_000.0
        layer.x_values = changed_x
        _expect(
            "clipped out-of-bounds source geometry",
            "source geometry leaves its data region",
            lambda: figure_checker(heat),
            failures,
        )
        layer.x_values = original_x
    finally:
        plt.close(heat.fig)

    property_drawing = landscape.draw_property_domain(data)
    try:
        layer = property_drawing.registry.data_layers[0]
        original_sizes = layer.artist.get_sizes().copy()
        layer.artist.set_sizes([6.0**2])
        _expect(
            "undersized marker",
            "data layer marker is too small",
            lambda: figure_checker(property_drawing),
            failures,
        )
        layer.artist.set_sizes(original_sizes)

        legend = next(
            record.artist
            for record in property_drawing.registry.texts
            if record.artist.get_text() == "Catalog: logP + BP\n(n=846)"
        )
        original_position = legend.get_position()
        legend.set_position((0.42, 0.52))
        _expect(
            "legend in data region",
            "text enters the scientific data region",
            lambda: figure_checker(property_drawing),
            failures,
        )
        legend.set_position(original_position)
    finally:
        plt.close(property_drawing.fig)


def _output_controls(
    *,
    output_checker: Checker,
    svg_checker: Checker,
    failures: list[str],
) -> None:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        png = root / "control.png"
        svg = root / "control.svg"
        png.write_bytes(b"png")
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

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
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")

        _expect(
            "same basename",
            "do not share one basename",
            lambda: output_checker(png, root / "different.svg"),
            failures,
        )

        raster_svg = root / "raster.svg"
        raster_svg.write_text(
            "<svg xmlns='http://www.w3.org/2000/svg'><image href='x.png'/></svg>",
            encoding="utf-8",
        )
        _expect(
            "vector-only SVG",
            "SVG contains raster image content",
            lambda: svg_checker([raster_svg]),
            failures,
        )


def _current_outputs(
    *,
    output_checker: Checker,
    svg_checker: Checker,
    failures: list[str],
) -> None:
    svg_paths: list[Path] = []
    for stem in landscape.ALL_STEMS:
        png = landscape.HERE / f"{stem}.png"
        svg = landscape.HERE / f"{stem}.svg"
        sidecar_path = landscape.HERE / f"{stem}.provenance.json"
        try:
            report = output_checker(png, svg)
            sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
            if sidecar["outputs"]["png"]["sha256"] != report["png_sha256"]:
                raise StandardsViolation([f"PNG provenance digest mismatch: {stem}"])
            if sidecar["outputs"]["svg"]["sha256"] != report["svg_sha256"]:
                raise StandardsViolation([f"SVG provenance digest mismatch: {stem}"])
            if sidecar["generator"]["sha256"] != landscape.sha256_file(
                Path(landscape.__file__).resolve()
            ):
                raise StandardsViolation([f"generator provenance mismatch: {stem}"])
        except (OSError, KeyError, json.JSONDecodeError, StandardsViolation) as error:
            failures.append(f"current output failed for {stem}: {error}")
            print(f"CURRENT {stem} output: FAIL")
        else:
            print(f"CURRENT {stem} output: PASS")
            svg_paths.append(svg)
    try:
        svg_checker(svg_paths)
    except StandardsViolation as error:
        failures.append(f"current SVG contract failed: {error}")
        print("CURRENT SVG contract: FAIL")
    else:
        print("CURRENT SVG contract: PASS")


def _determinism_controls(
    *,
    byte_checker: Checker,
    failures: list[str],
) -> None:
    with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
        first_root = Path(first_dir)
        second_root = Path(second_dir)
        landscape.generate(first_root)
        landscape.generate(second_root)
        first = tuple(first_root / name for name in landscape.generated_names())
        second = tuple(second_root / name for name in landscape.generated_names())
        try:
            report = byte_checker(first, second)
        except StandardsViolation as error:
            failures.append(f"clean generation is not byte-deterministic: {error}")
            print("CURRENT byte determinism: FAIL")
        else:
            print(f"CURRENT byte determinism: PASS {report}")

        altered = second_root / f"{landscape.HEATMAP_STEM}.svg"
        altered.write_bytes(altered.read_bytes() + b"must-fire")
        _expect(
            "altered output byte",
            "determinism mismatch",
            lambda: byte_checker(first, second),
            failures,
        )


def main(
    *,
    figure_checker: Checker | None = None,
    output_checker: Checker | None = None,
    svg_checker: Checker | None = None,
    byte_checker: Checker | None = None,
) -> int:
    figure_checker = figure_checker or landscape.verify_geometry
    output_checker = output_checker or check_output_pair
    svg_checker = svg_checker or landscape.check_svg_vector_contract
    byte_checker = byte_checker or check_byte_identical
    failures: list[str] = []

    data = landscape.measure()
    drawings = landscape.draw_all(data)
    try:
        for stem, drawing in drawings.items():
            try:
                report = figure_checker(drawing)
            except StandardsViolation as error:
                failures.append(f"current geometry failed for {stem}: {error}")
                print(f"CURRENT {stem} geometry: FAIL")
            else:
                print(f"CURRENT {stem} geometry: PASS {report}")
    finally:
        for drawing in drawings.values():
            plt.close(drawing.fig)

    _current_outputs(
        output_checker=output_checker,
        svg_checker=svg_checker,
        failures=failures,
    )
    _semantic_controls(data, failures)
    _geometry_controls(
        data,
        figure_checker=figure_checker,
        failures=failures,
    )
    _output_controls(
        output_checker=output_checker,
        svg_checker=svg_checker,
        failures=failures,
    )
    _determinism_controls(byte_checker=byte_checker, failures=failures)

    if failures:
        print("DATASET LANDSCAPE CHECKER CONTROL FAILURE:")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("DATASET LANDSCAPE CHECKER: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
