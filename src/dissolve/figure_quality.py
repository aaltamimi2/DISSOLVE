"""Measured figure-layout evidence shared by rendering and artifact inspection."""

from __future__ import annotations

import math
from typing import Any

from matplotlib import colors
from matplotlib.text import Text


def _overlap(first: Any, second: Any) -> bool:
    return (
        min(first.x1, second.x1) - max(first.x0, second.x0) > 1.0
        and min(first.y1, second.y1) - max(first.y0, second.y0) > 1.0
    )


def _hidden_tick_labels(fig: Any) -> set[int]:
    hidden: set[int] = set()
    for axes in fig.axes:
        dimensions = [
            axes.xaxis, axes.yaxis,
            *([axes.zaxis] if hasattr(axes, "zaxis") else []),
        ]
        for axis in dimensions:
            ticks = [*axis.get_major_ticks(), *axis.get_minor_ticks()]
            if not axes.axison or not axis.get_visible():
                for tick in ticks:
                    hidden.update((id(tick.label1), id(tick.label2)))
                continue
            lower, upper = sorted(float(value) for value in axis.get_view_interval())
            tolerance = max(1.0, abs(lower), abs(upper)) * 1e-12
            for tick in ticks:
                if not lower - tolerance <= float(tick.get_loc()) <= upper + tolerance:
                    hidden.update((id(tick.label1), id(tick.label2)))
    return hidden


def _three_dimensional_text_roles(fig: Any) -> dict[int, str]:
    roles: dict[int, str] = {}
    for axes in fig.axes:
        if not hasattr(axes, "zaxis"):
            continue
        roles[id(axes.title)] = "title"
        for dimension, axis in (
            ("x", axes.xaxis), ("y", axes.yaxis), ("z", axes.zaxis),
        ):
            roles[id(axis.label)] = f"{dimension}_axis_label"
            for tick in [*axis.get_major_ticks(), *axis.get_minor_ticks()]:
                roles[id(tick.label1)] = f"{dimension}_tick_label"
                roles[id(tick.label2)] = f"{dimension}_tick_label"
        for text in axes.texts:
            roles[id(text)] = "axes_text"
    return roles


def measure_figure_layout(
    fig: Any,
    *,
    expected_font_size: float = 11.0,
) -> dict[str, Any]:
    """Measure text bounds, including every text artist owned by a 3D axes."""
    violations: list[str] = []
    # A second draw stabilizes the projected transforms used by 3D axes.
    fig.canvas.draw()
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    output_box = fig.get_tightbbox(renderer).transformed(fig.dpi_scale_trans)
    hidden_ticks = _hidden_tick_labels(fig)
    three_dimensional_roles = _three_dimensional_text_roles(fig)
    texts: list[tuple[Text, Any]] = []
    three_dimensional_extents: list[dict[str, Any]] = []
    measured_text_count = 0
    for item in fig.findobj(match=Text):
        if (
            not item.get_visible()
            or not item.get_text().strip()
            or id(item) in hidden_ticks
        ):
            continue
        if float(item.get_fontsize()) != expected_font_size:
            violations.append(
                f"text {item.get_text()!r} is {item.get_fontsize():g} pt "
                f"(expected {expected_font_size:g} pt)"
            )
        if colors.to_rgba(item.get_color()) != colors.to_rgba("black"):
            violations.append(f"text {item.get_text()!r} is not black")
        box = item.get_window_extent(renderer)
        bounds = tuple(float(value) for value in box.bounds)
        valid_bounds = (
            all(math.isfinite(value) for value in bounds)
            and bounds[2] > 0.0
            and bounds[3] > 0.0
        )
        if not valid_bounds:
            violations.append(
                f"text {item.get_text()!r} has no measurable display extent"
            )
        else:
            measured_text_count += 1
            if (
                box.x0 < output_box.x0 - 1 or box.y0 < output_box.y0 - 1
                or box.x1 > output_box.x1 + 1 or box.y1 > output_box.y1 + 1
            ):
                violations.append(
                    f"text {item.get_text()!r} escapes the saved canvas"
                )
            texts.append((item, box))
        role = three_dimensional_roles.get(id(item))
        if role is not None:
            three_dimensional_extents.append({
                "text": item.get_text(),
                "role": role,
                "bounds_px": [round(value, 3) for value in bounds],
                "measured": valid_bounds,
            })
    for index, (first, first_box) in enumerate(texts):
        for second, second_box in texts[index + 1:]:
            if _overlap(first_box, second_box):
                violations.append(
                    f"text overlap {first.get_text()!r} / {second.get_text()!r}"
                )
    titles = [
        item
        for item in [
            getattr(fig, "_suptitle", None),
            *(axis.title for axis in fig.axes),
        ]
        if item is not None and item.get_text().strip()
    ]
    if len(titles) != 1:
        violations.append(f"expected one title, found {len(titles)}")
    elif fig.axes:
        axes_width = max(
            axis.get_window_extent(renderer).width for axis in fig.axes
        )
        if titles[0].get_window_extent(renderer).width > axes_width + 1:
            violations.append("title is wider than its axes")
    axes_3d_count = sum(hasattr(axes, "zaxis") for axes in fig.axes)
    measured_3d_count = sum(
        bool(item["measured"]) for item in three_dimensional_extents
    )
    if axes_3d_count and not three_dimensional_extents:
        violations.append("3D axes has no measured text artists")
    elif measured_3d_count != len(three_dimensional_extents):
        violations.append("not every 3D text artist has a measurable display extent")
    return {
        "schema": "dissolve.figure-layout.v1",
        "status": "pass" if not violations else "fail",
        "canvas_bounds_px": [
            round(float(value), 3) for value in output_box.bounds
        ],
        "text_count": len(texts),
        "measured_text_count": measured_text_count,
        "axes_3d_count": axes_3d_count,
        "three_dimensional_text_count": len(three_dimensional_extents),
        "measured_three_dimensional_text_count": measured_3d_count,
        "three_dimensional_text_extents": three_dimensional_extents,
        "violations": violations,
    }


def inspect_figure(
    fig: Any,
    name: str,
    *,
    expected_font_size: float = 11.0,
) -> list[str]:
    """Return measured publication-contract violations before a figure is saved."""
    report = measure_figure_layout(
        fig, expected_font_size=expected_font_size,
    )
    return [f"{name}: {violation}" for violation in report["violations"]]
