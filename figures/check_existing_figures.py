#!/usr/bin/env python3
"""Reproduce standards checks and their must-fire controls.

Run from the repository root with::

    PYTHONPATH=src:. python figures/check_existing_figures.py

Exit 0 means every current figure passed and every deliberately defective
control was rejected for the named reason.  An unexpectedly accepted control
is a checker failure and returns exit 1.
"""

from __future__ import annotations

import json
from typing import Callable

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.patches import FancyArrowPatch, Rectangle

try:  # Package import in tests; direct import for the documented CLI.
    from . import unified_solubility_query
    from .standards_checker import (
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_figure,
    )
except ImportError:  # pragma: no cover - exercised by the documented CLI
    import unified_solubility_query
    from standards_checker import (
        DrawingRegistry,
        FigureStandards,
        StandardsViolation,
        TextRecord,
        check_figure,
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
    ("title width", "title is wider", _wide_title),
    ("registration coverage", "visible text is not registered", _unregistered),
)


def main() -> int:
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

    for name, expected, mutate in MUST_FIRE:
        control_fig, control_registry, control_title = _control_figure()
        try:
            standards = mutate(control_fig, control_registry, control_title)
            try:
                check_figure(
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

    if failures:
        print("CHECKER CONTROL FAILURE:")
        for item in failures:
            print(f"  {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
