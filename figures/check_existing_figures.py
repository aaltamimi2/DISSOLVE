#!/usr/bin/env python3
"""Reproduce standards checks and their must-fire controls.

Run from the repository root with::

    PYTHONPATH=src:. python figures/check_existing_figures.py

Exit 0 means every current figure passed and every deliberately defective
control was rejected for the named reason.  An unexpectedly accepted control
is a checker failure and returns exit 1.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import FancyArrowPatch, Rectangle
import numpy as np

try:  # Package import in tests; direct import for the documented CLI.
    from . import results_s1_screening_coverage, unified_solubility_query
    from .standards_checker import (
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_byte_identical,
        check_figure,
        check_output_pair,
    )
except ImportError:  # pragma: no cover - exercised by the documented CLI
    import results_s1_screening_coverage
    import unified_solubility_query
    from standards_checker import (
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_byte_identical,
        check_figure,
        check_output_pair,
    )


Mutation = Callable[[plt.Figure, DrawingRegistry, object], FigureStandards]


def _control_figure() -> tuple[plt.Figure, DrawingRegistry, object]:
    fig = plt.figure(figsize=(4.0, 3.0), dpi=100, facecolor="white")
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    content = Rectangle(
        (0.10, 0.10), 0.80, 0.70, transform=ax.transAxes,
        facecolor="white", edgecolor="#999999",
    )
    ax.add_patch(content)
    title = ax.text(
        0.5, 0.90, "Short title", transform=ax.transAxes,
        ha="center", va="center", fontsize=10, color="#000000",
    )
    body = ax.text(
        0.25, 0.55, "Body", transform=ax.transAxes,
        ha="left", va="center", fontsize=10, color="#000000",
    )
    mark = Rectangle(
        (0.70, 0.45), 0.05, 0.05, transform=ax.transAxes,
        facecolor="#4E79A7", edgecolor="none",
    )
    ax.add_patch(mark)
    return fig, DrawingRegistry(
        texts=[TextRecord(title, None), TextRecord(body, content)],
        panels={"content": content},
        collision_patches=[mark],
        data_marks=[mark],
    ), title


def _nonuniform(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.texts[1].artist.set_fontsize(9)
    return FigureStandards(font_size_pt=10)


def _colored_text(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.texts[1].artist.set_color("#4E79A7")
    return FigureStandards(font_size_pt=10)


def _overlap(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    body = registry.texts[1]
    other = fig.axes[0].text(
        0.25, 0.55, "Collision", transform=fig.axes[0].transAxes,
        ha="left", va="center", fontsize=10, color="#000000",
    )
    registry.texts.append(TextRecord(other, body.container))
    return FigureStandards(font_size_pt=10)


def _text_shape_overlap(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.collision_patches[0].set_xy((0.25, 0.53))
    return FigureStandards(font_size_pt=10)


def _shape_overlap(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    other = Rectangle(
        (0.72, 0.47), 0.05, 0.05, transform=fig.axes[0].transAxes,
        facecolor="#E69F00", edgecolor="none",
    )
    fig.axes[0].add_patch(other)
    registry.collision_patches.append(other)
    return FigureStandards(font_size_pt=10)


def _connector_overlap(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    connector = FancyArrowPatch(
        (0.20, 0.55), (0.40, 0.55), transform=fig.axes[0].transAxes,
        arrowstyle="-|>", color="#555555",
    )
    fig.axes[0].add_patch(connector)
    registry.connectors.append(connector)
    return FigureStandards(font_size_pt=10)


def _container_escape(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.texts[1].artist.set_position((0.02, 0.55))
    return FigureStandards(font_size_pt=10)


def _small_text(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    for record in registry.texts:
        record.artist.set_fontsize(6)
    return FigureStandards(font_size_pt=6)


def _small_mark(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.data_marks[0].set_width(0.005)
    registry.data_marks[0].set_height(0.005)
    return FigureStandards(font_size_pt=10)


def _unregistered_mark(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    registry.collision_patches.clear()
    return FigureStandards(font_size_pt=10)


def _wholly_unregistered_artist(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    mark = Rectangle(
        (0.25, 0.53), 0.10, 0.08, transform=fig.axes[0].transAxes,
        facecolor="#E69F00", edgecolor="none",
    )
    fig.axes[0].add_patch(mark)
    return FigureStandards(font_size_pt=10)


def _wholly_unregistered_figure_image(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    pixels = np.ones((50, 120, 4), dtype=float)
    pixels[:, :, :3] = (0.90, 0.62, 0.00)
    fig.figimage(pixels, xo=80, yo=140, origin="lower")
    return FigureStandards(font_size_pt=10)


def _wide_title(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    title.set_text("A title " * 30)
    return FigureStandards(font_size_pt=10)


def _unregistered(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    fig.axes[0].text(
        0.50, 0.25, "Unregistered", transform=fig.axes[0].transAxes,
        fontsize=10, color="#000000",
    )
    return FigureStandards(font_size_pt=10)


def _second_axes(
    fig: plt.Figure, registry: DrawingRegistry, title: object,
) -> FigureStandards:
    extra = fig.add_axes((0.82, 0.82, 0.10, 0.10))
    extra.axis("off")
    return FigureStandards(font_size_pt=10)


MUST_FIRE: tuple[tuple[str, str, Mutation], ...] = (
    ("uniform text", "nonuniform text size", _nonuniform),
    ("black text", "non-black text", _colored_text),
    ("no overlap", "text collision", _overlap),
    ("text/shape clearance", "text/shape collision", _text_shape_overlap),
    ("shape/shape clearance", "shape collision", _shape_overlap),
    ("connector clearance", "text/connector collision", _connector_overlap),
    ("container bounds", "text escapes or touches", _container_escape),
    ("minimum text size", "text is too small", _small_text),
    ("minimum mark size", "data mark is too small", _small_mark),
    (
        "data-mark collision registration",
        "data mark is not registered as collision-relevant",
        _unregistered_mark,
    ),
    (
        "closed graphical-artist inventory",
        "visible graphical artist is not registered",
        _wholly_unregistered_artist,
    ),
    (
        "closed image-artist inventory",
        "visible graphical artist is not registered",
        _wholly_unregistered_figure_image,
    ),
    ("title width", "title is wider", _wide_title),
    ("registration coverage", "visible text is not registered", _unregistered),
    ("single axes", "exactly one axes", _second_axes),
)


def _expect_output_failure(
    *,
    name: str,
    expected: str,
    output_checker: Callable[..., dict[str, object]],
    png_path: Path,
    svg_path: Path,
    failures: list[str],
) -> None:
    try:
        output_checker(png_path, svg_path)
    except StandardsViolation as error:
        matching = [item for item in error.violations if expected in item]
        if matching:
            print(f"MUST-FIRE {name}: FAIL (expected) — {matching[0]}")
        else:
            failures.append(
                f"{name}: rejected, but not for expected token {expected!r}"
            )
            print(f"MUST-FIRE {name}: WRONG FAILURE")
    else:
        failures.append(f"{name}: defective output pair was accepted")
        print(f"MUST-FIRE {name}: UNEXPECTED PASS")


def _coverage_controls(
    *,
    figure_checker: Callable[..., dict[str, object]],
    failures: list[str],
) -> None:
    coverage = results_s1_screening_coverage
    data = coverage.measure()

    wrong_digests = dict(coverage.EXPECTED_SOURCE_DIGESTS)
    wrong_digests[coverage.DB_PATH] = "0" * 64
    try:
        coverage.measure(expected_digests=wrong_digests)
    except coverage.SourceDigestError as error:
        print(f"MUST-FIRE coverage source digest: FAIL (expected) — {str(error).splitlines()[0]}")
    else:
        failures.append("coverage source digest: wrong DuckDB digest was accepted")
        print("MUST-FIRE coverage source digest: UNEXPECTED PASS")

    changed_pairs = tuple(
        (polymer, value + 1 if polymer == "PU" else value)
        for polymer, value in data.pair_counts
    )
    try:
        coverage.validate(replace(data, pair_counts=changed_pairs))
    except coverage.CoverageValidationError as error:
        print(f"MUST-FIRE sparse-pair arithmetic: FAIL (expected) — {str(error).splitlines()[0]}")
    else:
        failures.append("sparse-pair arithmetic: changed PU count was accepted")
        print("MUST-FIRE sparse-pair arithmetic: UNEXPECTED PASS")

    wrong_disposition = replace(
        data,
        evaluable_cells=data.raw_valid_cells,
        refused_cells=data.nonpositive_cells + data.exact_100_artifact_cells,
    )
    try:
        coverage.validate(wrong_disposition)
    except coverage.CoverageValidationError as error:
        print(f"MUST-FIRE runtime disposition: FAIL (expected) — {str(error).splitlines()[0]}")
    else:
        failures.append("runtime disposition: exact-100 cells were accepted as refused")
        print("MUST-FIRE runtime disposition: UNEXPECTED PASS")

    fig, registry, title = coverage.draw_cell_fates(data)
    try:
        first, second = registry.data_marks[:2]
        second.center = first.center
        try:
            figure_checker(
                fig,
                registry,
                title,
                standards=FigureStandards(font_size_pt=coverage.FONT_SIZE_PT),
            )
        except StandardsViolation as error:
            matching = [item for item in error.violations if "shape collision" in item]
            if matching:
                print(f"MUST-FIRE fate-circle overlap: FAIL (expected) — {matching[0]}")
            else:
                failures.append("fate-circle overlap: wrong rejection reason")
                print("MUST-FIRE fate-circle overlap: WRONG FAILURE")
        else:
            failures.append("fate-circle overlap: overlapping circles were accepted")
            print("MUST-FIRE fate-circle overlap: UNEXPECTED PASS")
    finally:
        plt.close(fig)

    fig, registry, title = coverage.draw_pair_catalog(data)
    try:
        number = next(
            record.artist
            for record in registry.texts
            if record.artist.get_text() == "990"
        )
        bar = registry.data_marks[0]
        number.set_position((bar.get_x() + bar.get_width() / 2, bar.get_y() + bar.get_height() / 2))
        number.set_ha("center")
        try:
            figure_checker(
                fig,
                registry,
                title,
                standards=FigureStandards(font_size_pt=coverage.FONT_SIZE_PT),
            )
        except StandardsViolation as error:
            matching = [item for item in error.violations if "text/shape collision" in item]
            if matching:
                print(f"MUST-FIRE pair-label overlap: FAIL (expected) — {matching[0]}")
            else:
                failures.append("pair-label overlap: wrong rejection reason")
                print("MUST-FIRE pair-label overlap: WRONG FAILURE")
        else:
            failures.append("pair-label overlap: label on bar was accepted")
            print("MUST-FIRE pair-label overlap: UNEXPECTED PASS")
    finally:
        plt.close(fig)

    with TemporaryDirectory() as first_dir, TemporaryDirectory() as second_dir:
        first_root = Path(first_dir)
        second_root = Path(second_dir)
        coverage.generate(first_root)
        coverage.generate(second_root)
        names = tuple(
            f"{stem}.{suffix}"
            for stem in (coverage.PAIR_STEM, coverage.FATE_STEM)
            for suffix in ("png", "svg", "provenance.json")
        )
        first_paths = tuple(first_root / name for name in names)
        second_paths = tuple(second_root / name for name in names)
        try:
            check_byte_identical(first_paths, second_paths)
        except StandardsViolation as error:
            failures.append(f"deterministic clean generation failed: {error}")
            print("CURRENT coverage byte determinism: FAIL")
        else:
            print("CURRENT coverage byte determinism: PASS")

        altered = second_root / f"{coverage.FATE_STEM}.svg"
        altered.write_bytes(altered.read_bytes() + b"\n<!-- must-fire mutation -->\n")
        try:
            check_byte_identical(first_paths, second_paths)
        except StandardsViolation as error:
            matching = [item for item in error.violations if "determinism mismatch" in item]
            if matching:
                print(f"MUST-FIRE byte determinism: FAIL (expected) — {matching[0]}")
            else:
                failures.append("byte determinism: wrong rejection reason")
                print("MUST-FIRE byte determinism: WRONG FAILURE")
        else:
            failures.append("byte determinism: altered SVG was accepted")
            print("MUST-FIRE byte determinism: UNEXPECTED PASS")


@contextmanager
def _historical_coverage_authorities():
    """Do not re-lock an accepted draft to a later append-only inbox state.

    The coverage generator and committed sidecars preserve the inbox snapshot
    under which those earlier drafts were admitted.  This checker reproduces
    their numeric authorities while temporarily excluding that mutable,
    explicitly non-numeric instruction log from fresh-generation controls.
    """

    expected = results_s1_screening_coverage.EXPECTED_SOURCE_DIGESTS
    path = results_s1_screening_coverage.INBOX_PATH
    historical = expected.pop(path, None)
    try:
        yield
    finally:
        if historical is not None:
            expected[path] = historical


def _main(
    *,
    figure_checker: Callable[..., dict[str, object]] | None = None,
    output_checker: Callable[..., dict[str, object]] | None = None,
) -> int:
    figure_checker = figure_checker or check_figure
    output_checker = output_checker or check_output_pair
    failures: list[str] = []

    data = unified_solubility_query.measure()
    fig, registry, title = unified_solubility_query.draw(data)
    try:
        result = unified_solubility_query.verify_geometry(fig, registry, title)
    except StandardsViolation as error:
        failures.append(f"unified_solubility_query: {error}")
        print("CURRENT unified_solubility_query: FAIL")
        for item in error.violations:
            print(f"  {item}")
    else:
        print(
            "CURRENT unified_solubility_query: PASS ",
            json.dumps(result, sort_keys=True),
            sep="",
        )
    finally:
        plt.close(fig)

    coverage_data = results_s1_screening_coverage.measure()
    for stem, (fig, registry, title) in results_s1_screening_coverage.draw(
        coverage_data
    ).items():
        try:
            result = figure_checker(
                fig,
                registry,
                title,
                standards=FigureStandards(
                    font_size_pt=results_s1_screening_coverage.FONT_SIZE_PT
                ),
            )
        except StandardsViolation as error:
            failures.append(f"{stem}: {error}")
            print(f"CURRENT {stem}: FAIL")
            for item in error.violations:
                print(f"  {item}")
        else:
            print(f"CURRENT {stem}: PASS {json.dumps(result, sort_keys=True)}")
        finally:
            plt.close(fig)

        png_path = Path(results_s1_screening_coverage.HERE) / f"{stem}.png"
        svg_path = Path(results_s1_screening_coverage.HERE) / f"{stem}.svg"
        try:
            output_result = output_checker(png_path, svg_path)
        except StandardsViolation as error:
            failures.append(f"{stem} output pair: {error}")
            print(f"CURRENT {stem} output pair: FAIL")
        else:
            print(
                f"CURRENT {stem} output pair: PASS "
                f"{json.dumps(output_result, sort_keys=True)}"
            )

    for name, expected, mutate in MUST_FIRE:
        control_fig, control_registry, control_title = _control_figure()
        try:
            standards = mutate(control_fig, control_registry, control_title)
            try:
                figure_checker(
                    control_fig,
                    control_registry,
                    control_title,
                    standards=standards,
                )
            except StandardsViolation as error:
                matching = [item for item in error.violations if expected in item]
                if matching:
                    print(f"MUST-FIRE {name}: FAIL (expected) — {matching[0]}")
                else:
                    failures.append(
                        f"{name}: rejected, but not for expected token {expected!r}"
                    )
                    print(f"MUST-FIRE {name}: WRONG FAILURE")
            else:
                failures.append(f"{name}: defective figure was accepted")
                print(f"MUST-FIRE {name}: UNEXPECTED PASS")
        finally:
            plt.close(control_fig)

    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        png = root / "control.png"
        svg = root / "control.svg"
        png.write_bytes(b"png")
        svg.write_bytes(b"svg")
        try:
            output_checker(png, svg)
        except StandardsViolation as error:
            failures.append(f"valid output pair failed: {error}")
            print("CURRENT output pair: FAIL")
        else:
            print("CURRENT output pair: PASS")

        png.unlink()
        _expect_output_failure(
            name="required PNG",
            expected="missing PNG output",
            output_checker=output_checker,
            png_path=png,
            svg_path=svg,
            failures=failures,
        )
        png.write_bytes(b"png")
        svg.unlink()
        _expect_output_failure(
            name="required SVG",
            expected="missing SVG output",
            output_checker=output_checker,
            png_path=png,
            svg_path=svg,
            failures=failures,
        )
        svg = root / "different.svg"
        svg.write_bytes(b"svg")
        _expect_output_failure(
            name="same output basename",
            expected="do not share one basename",
            output_checker=output_checker,
            png_path=png,
            svg_path=svg,
            failures=failures,
        )

    _coverage_controls(figure_checker=figure_checker, failures=failures)

    if failures:
        print("CHECKER CONTROL FAILURE:")
        for item in failures:
            print(f"  {item}")
        return 1
    return 0


def main(
    *,
    figure_checker: Callable[..., dict[str, object]] | None = None,
    output_checker: Callable[..., dict[str, object]] | None = None,
) -> int:
    with _historical_coverage_authorities():
        return _main(
            figure_checker=figure_checker,
            output_checker=output_checker,
        )


if __name__ == "__main__":
    raise SystemExit(main())
