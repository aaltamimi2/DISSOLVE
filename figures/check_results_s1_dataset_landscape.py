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
    *,
    emit: bool = True,
) -> None:
    try:
        action()
    except EXPECTED_EXCEPTIONS as error:
        match = next((line for line in str(error).splitlines() if token in line), None)
        if match is None:
            failures.append(
                f"{name}: rejected for the wrong reason; wanted {token!r}: {error}"
            )
            if emit:
                print(f"MUST-FIRE {name}: WRONG FAILURE")
        else:
            if emit:
                print(f"MUST-FIRE {name}: FAIL (expected) — {match}")
    else:
        failures.append(f"{name}: defective case was accepted")
        if emit:
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
    *,
    semantic_validator: Checker,
    density_bin_validator: Checker,
    t5_category_validator: Checker,
    failures: list[str],
) -> None:
    for source in (
        landscape.DB_PATH,
        landscape.PROPERTY_PATH,
        landscape.READER_PATH,
        landscape.TOOLS_PATH,
        landscape.SEPARATION_PATH,
    ):
        wrong_digest = dict(landscape.EXPECTED_SOURCE_DIGESTS)
        wrong_digest[source] = "0" * 64
        _expect(
            f"source digest {source.name}",
            "source digest mismatch",
            lambda wrong_digest=wrong_digest: landscape.measure(
                expected_digests=wrong_digest
            ),
            failures,
        )

    wrong_style_digest = dict(landscape.EXPECTED_DENSITY_STYLE_DIGESTS)
    wrong_style_digest[landscape.ZERO_WHITE_PROPOSAL_PATH] = "0" * 64
    _expect(
        "source digest density zero-white proposal",
        "source digest mismatch",
        lambda: landscape.assert_density_style_digests(wrong_style_digest),
        failures,
    )

    _expect(
        "cohort/PU mutation",
        "cohort scalar counts changed",
        lambda: semantic_validator(replace(data, cohort_polymers=12)),
        failures,
    )
    _expect(
        "stored/evaluable confusion",
        "cohort scalar counts changed",
        lambda: semantic_validator(replace(data, evaluable_cells=data.stored_cells)),
        failures,
    )
    _expect(
        "refused nonpositive mutation",
        "cohort scalar counts changed",
        lambda: semantic_validator(replace(data, refused_cells=data.refused_cells - 1)),
        failures,
    )

    first_heat = data.heat_cells[0]
    changed_heat = replace(first_heat, numerator=first_heat.numerator + 1)
    _expect(
        "heat numerator",
        "heat payload digest changed",
        lambda: semantic_validator(
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
        lambda: semantic_validator(replace(data, heat_cells=shifted_denominators)),
        failures,
    )

    onset = list(data.onset_counts)
    polymer, counts = onset[0]
    onset[0] = (polymer, _move_one(counts))
    _expect(
        "onset bin move with closure preserved",
        "onset payload digest changed",
        lambda: semantic_validator(replace(data, onset_counts=tuple(onset))),
        failures,
    )

    without_never = tuple(
        (polymer, counts[:-1]) for polymer, counts in data.onset_counts
    )
    _expect(
        "dropped never category",
        "onset bins do not close",
        lambda: semantic_validator(replace(data, onset_counts=without_never)),
        failures,
    )

    interpolated_categories = list(landscape.T5_CATEGORIES)
    interpolated_categories[1] = 27.5
    _expect(
        "interpolated T5 crossing",
        "T5 categories changed from exact stored nodes",
        lambda: t5_category_validator(interpolated_categories),
        failures,
    )

    _expect(
        "frozen onset row order",
        "onset order changed",
        lambda: semantic_validator(
            replace(data, onset_order=tuple(reversed(data.onset_order)))
        ),
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
        lambda: semantic_validator(replace(data, density_counts=tuple(densities))),
        failures,
    )

    # A refused nonpositive has no density row. Adding one without changing
    # the runtime-evaluable denominator must break per-temperature closure.
    refused_density = list(data.density_counts)
    polymer, rows = refused_density[0]
    refused_rows = [list(row) for row in rows]
    refused_rows[0][0] += 1
    refused_density[0] = (
        polymer,
        tuple(tuple(row) for row in refused_rows),
    )
    _expect(
        "refused nonpositive enters density",
        "density bins do not close",
        lambda: semantic_validator(
            replace(data, density_counts=tuple(refused_density))
        ),
        failures,
    )

    # Exercise served-ceiling omission and misplacement separately.
    ceiling_polymer_index = next(
        polymer_index
        for polymer_index, (_polymer, density_rows) in enumerate(data.density_counts)
        if any(row[50] > 0 for row in density_rows)
    )
    polymer, rows = data.density_counts[ceiling_polymer_index]
    ceiling_temperature_index = next(
        index for index, row in enumerate(rows) if row[50] > 0
    )

    omitted_rows = [list(row) for row in rows]
    omitted_rows[ceiling_temperature_index][50] -= 1
    omitted_density = list(data.density_counts)
    omitted_density[ceiling_polymer_index] = (
        polymer,
        tuple(tuple(row) for row in omitted_rows),
    )
    _expect(
        "served ceiling omitted",
        "density bins do not close",
        lambda: semantic_validator(
            replace(data, density_counts=tuple(omitted_density))
        ),
        failures,
    )

    misplaced_rows = [list(row) for row in rows]
    misplaced_rows[ceiling_temperature_index][50] -= 1
    misplaced_rows[ceiling_temperature_index][49] += 1
    misplaced_density = list(data.density_counts)
    misplaced_density[ceiling_polymer_index] = (
        polymer,
        tuple(tuple(row) for row in misplaced_rows),
    )
    _expect(
        "served ceiling outside top band",
        "density payload digest changed",
        lambda: semantic_validator(
            replace(data, density_counts=tuple(misplaced_density))
        ),
        failures,
    )
    _expect(
        "density fraction closure",
        "density bins do not close",
        lambda: semantic_validator(
            replace(data, density_counts=tuple(refused_density))
        ),
        failures,
    )

    for name, field in (
        ("property catalog classification", "property_record_count"),
        ("original-BP classification", "original_bp_count"),
        ("governed-admission count classification", "admission_record_count"),
    ):
        _expect(
            name,
            "property/admission counts changed",
            lambda field=field: semantic_validator(
                replace(data, **{field: getattr(data, field) - 1})
            ),
            failures,
        )
    _expect(
        "governed-admission identity classification",
        "property payload digest changed",
        lambda: semantic_validator(
            replace(data, admission_points=data.admission_points[:-1])
        ),
        failures,
    )

    changed_edges = list(landscape.RAW_LOG_EDGES)
    changed_edges[1] += 0.01
    _expect(
        "density bin edge",
        "density raw log10 bin edges changed",
        lambda: density_bin_validator(changed_edges),
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


def _density_color_controls(
    data: landscape.DatasetLandscapeData,
    *,
    color_validator: Checker,
    failures: list[str],
) -> None:
    for polymer, stem in landscape.DENSITY_STEMS.items():
        drawing = landscape.draw_density(data, polymer)
        sidecar = json.loads(
            (landscape.HERE / f"{stem}.provenance.json").read_text(encoding="utf-8")
        )
        try:
            report = color_validator(data, drawing, polymer, sidecar)
        except EXPECTED_EXCEPTIONS as error:
            failures.append(
                f"current density color contract failed for {polymer}: {error}"
            )
            print(f"CURRENT density color {polymer}: FAIL")
        else:
            print(f"CURRENT density color {polymer}: PASS {report}")

        if polymer != "PET":
            plt.close(drawing.fig)
            continue

        layer = next(
            record
            for record in drawing.registry.data_layers
            if record.series_id == "density:PET"
        )
        matrix = landscape._density_fraction_matrix(data, "PET").reshape(-1)
        zero_index = int(
            next(index for index, value in enumerate(matrix) if value == 0)
        )
        positive_index = int(
            next(index for index, value in enumerate(matrix) if value > 0)
        )
        original_colors = layer.artist.get_facecolors().copy()

        changed = original_colors.copy()
        changed[zero_index] = landscape.CMAP(0.0)
        layer.artist.set_facecolors(changed)
        _expect(
            "zero density cell colored",
            "zero density cell is not white",
            lambda: color_validator(data, drawing, "PET", sidecar),
            failures,
        )
        layer.artist.set_facecolors(original_colors)

        changed = original_colors.copy()
        changed[positive_index] = (1.0, 1.0, 1.0, 1.0)
        layer.artist.set_facecolors(changed)
        _expect(
            "positive density cell white",
            "positive density cell is white",
            lambda: color_validator(data, drawing, "PET", sidecar),
            failures,
        )
        layer.artist.set_facecolors(original_colors)

        zero_swatch = next(
            patch
            for patch in drawing.registry.collision_patches
            if patch.get_gid() == "quantitative-legend-0"
        )
        original_swatch = zero_swatch.get_facecolor()
        zero_swatch.set_facecolor(landscape.CMAP(0.0))
        _expect(
            "zero legend swatch colored",
            "density zero legend swatch is not white",
            lambda: color_validator(data, drawing, "PET", sidecar),
            failures,
        )
        zero_swatch.set_facecolor(original_swatch)

        missing_zero = json.loads(json.dumps(sidecar))
        missing_zero["density_color_contract"].pop("zero_fraction_fill")
        _expect(
            "density sidecar zero fill missing",
            "density sidecar color contract changed: zero_fraction_fill",
            lambda: color_validator(data, drawing, "PET", missing_zero),
            failures,
        )

        wrong_colormap = json.loads(json.dumps(sidecar))
        wrong_colormap["density_color_contract"]["positive_fraction_colormap"] = (
            "plasma"
        )
        _expect(
            "density sidecar colormap changed",
            "density sidecar color contract changed: positive_fraction_colormap",
            lambda: color_validator(data, drawing, "PET", wrong_colormap),
            failures,
        )

        wrong_normalization = json.loads(json.dumps(sidecar))
        wrong_normalization["density_color_contract"]["normalization"] = [0.0, 0.5]
        _expect(
            "density sidecar normalization changed",
            "density sidecar color contract changed: normalization",
            lambda: color_validator(data, drawing, "PET", wrong_normalization),
            failures,
        )

        missing_style_source = json.loads(json.dumps(sidecar))
        missing_style_source["sources"] = [
            row
            for row in missing_style_source["sources"]
            if row["path"] != str(landscape.ZERO_WHITE_PROPOSAL_PATH)
        ]
        _expect(
            "density sidecar style source missing",
            "density sidecar style source lock is missing",
            lambda: color_validator(data, drawing, "PET", missing_style_source),
            failures,
        )
        plt.close(drawing.fig)


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


def _accept_all_controls(
    data: landscape.DatasetLandscapeData,
    failures: list[str],
) -> None:
    """Prove the harness itself rejects injected accept-all validators."""

    def accept_all(*args, **kwargs):
        return {}

    def prove(name: str, action: Callable[[], object]) -> None:
        probe_failures: list[str] = []
        _expect(name, "unreachable", action, probe_failures, emit=False)
        wanted = [f"{name}: defective case was accepted"]
        if probe_failures == wanted:
            print(
                f"MUST-FIRE accept-all {name}: FAIL (expected) — "
                "injected validator acceptance makes the harness nonzero"
            )
        else:
            failures.append(
                f"accept-all {name}: harness probe did not detect acceptance: "
                f"{probe_failures}"
            )
            print(f"MUST-FIRE accept-all {name}: WRONG FAILURE")

    prove(
        "semantic validator",
        lambda: accept_all(replace(data, cohort_polymers=12)),
    )
    changed_edges = list(landscape.RAW_LOG_EDGES)
    changed_edges[1] += 0.01
    prove("density-bin validator", lambda: accept_all(changed_edges))
    changed_categories = list(landscape.T5_CATEGORIES)
    changed_categories[1] = 27.5
    prove("T5-category validator", lambda: accept_all(changed_categories))

    drawing = landscape.draw_fraction_heatmap(data)
    try:
        drawing.fig.add_axes((0.90, 0.01, 0.02, 0.02)).axis("off")
        prove("figure checker", lambda: accept_all(drawing))
    finally:
        plt.close(drawing.fig)

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        missing_png = root / "missing.png"
        svg = root / "missing.svg"
        svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        prove("output checker", lambda: accept_all(missing_png, svg))

    density = landscape.draw_density(data, "PET")
    try:
        prove(
            "density-color/provenance validator",
            lambda: accept_all(data, density, "PET", {}),
        )
    finally:
        plt.close(density.fig)


def main(
    *,
    semantic_validator: Checker | None = None,
    density_bin_validator: Checker | None = None,
    t5_category_validator: Checker | None = None,
    density_color_validator: Checker | None = None,
    figure_checker: Checker | None = None,
    output_checker: Checker | None = None,
    svg_checker: Checker | None = None,
    byte_checker: Checker | None = None,
) -> int:
    semantic_validator = semantic_validator or landscape.validate
    density_bin_validator = (
        density_bin_validator or landscape.check_density_bin_contract
    )
    t5_category_validator = (
        t5_category_validator or landscape.check_t5_category_contract
    )
    density_color_validator = (
        density_color_validator or landscape.check_density_color_contract
    )
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
    _semantic_controls(
        data,
        semantic_validator=semantic_validator,
        density_bin_validator=density_bin_validator,
        t5_category_validator=t5_category_validator,
        failures=failures,
    )
    _geometry_controls(
        data,
        figure_checker=figure_checker,
        failures=failures,
    )
    _density_color_controls(
        data,
        color_validator=density_color_validator,
        failures=failures,
    )
    _output_controls(
        output_checker=output_checker,
        svg_checker=svg_checker,
        failures=failures,
    )
    _accept_all_controls(data, failures)
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
