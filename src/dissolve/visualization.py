"""Validated plots, exact-data terminal previews, and isolated visualization scope."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import textwrap
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from . import tea_contracts
from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success
from .figure_quality import measure_figure_layout
from .session import (
    candidate_evidence, current_tool_session,
    resolve_candidate_argument,
)
from .separation import plan_multistage_separation
from .tools import _solvent_resolution_errors, screen_polymer_separation

_COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9")
_MAX_T = thermo.SENSITIVITY_EXTRAPOLATION_MAX_C
_FONT_SIZE = 11
_RENDER_DPI = 300
_TEXT_COLOR = "#000000"


def _items(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, str):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
    # Tool schemas use arrays. This fallback preserves the common N,N-DMF name
    # if a provider nevertheless emits one comma-delimited string.
    parts = [item.strip() for item in value.split(",") if item.strip()]
    rebuilt: list[str] = []
    index = 0
    while index < len(parts):
        if parts[index].casefold() == "n" and index + 1 < len(parts) and parts[index + 1].casefold().startswith("n-"):
            rebuilt.append(f"N,{parts[index + 1]}")
            index += 2
        else:
            rebuilt.append(parts[index])
            index += 1
    return list(dict.fromkeys(rebuilt))


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _resolve_polymers(tool: str, values: str | Sequence[str]) -> tuple[list[str], str | None]:
    requested = _items(values)
    resolved, unsupported = [], []
    for item in requested:
        name = thermo.resolve_polymer(item)
        (resolved if name else unsupported).append(name or item)
    if unsupported:
        return [], tool_error(
            tool, "Unsupported polymer(s): " + ", ".join(unsupported),
            error_code="unknown_polymer", unsupported_polymers=unsupported,
            available_polymers=sorted(thermo.get_available_polymers()),
        )
    if not resolved:
        return [], tool_error(tool, "At least one polymer is required.", error_code="missing_polymers")
    return list(dict.fromkeys(resolved)), None


def _resolve_solvents(tool: str, values: str | Sequence[str]) -> tuple[list[str], str | None]:
    requested = _items(values)
    resolved, unsupported = [], []
    for item in requested:
        name = thermo.resolve_solvent(item)
        (resolved if name else unsupported).append(name or item)
    if unsupported:
        return [], _solvent_resolution_errors(tool, unsupported)
    if not resolved:
        return [], tool_error(tool, "At least one solvent is required.", error_code="missing_solvents")
    return list(dict.fromkeys(resolved)), None


def _range(
    tool: str, minimum: Any, maximum: Any, step: Any, strict: bool,
) -> tuple[tuple[float, float, float], str | None]:
    start, bound, increment = _finite(minimum), _finite(maximum), _finite(step)
    if None in {start, bound, increment}:
        return (0.0, 0.0, 0.0), tool_error(
            tool, "Temperature bounds and step must be finite numbers.",
            error_code="invalid_temperature_range",
        )
    assert start is not None and bound is not None and increment is not None
    if increment <= 0 or bound < start or start <= -273.15:
        return (start, bound, increment), tool_error(
            tool, "Use an increasing physical temperature range and a positive step.",
            error_code="invalid_temperature_range",
        )
    if bound > _MAX_T:
        return (start, bound, increment), tool_error(
            tool, f"The visualization sensitivity limit is {_MAX_T:g} C.",
            error_code="temperature_above_supported_extrapolation",
            requested_temperature_max_c=bound, supported_temperature_max_c=_MAX_T,
        )
    intervals = math.floor((bound - start - (1e-9 if strict else 0.0)) / increment)
    end = start + max(0, intervals) * increment
    if strict and end >= bound:
        end -= increment
    if end < start:
        return (start, end, increment), tool_error(
            tool, "No temperatures remain below the exclusive upper bound.",
            error_code="empty_temperature_range",
        )
    if not strict and end < bound - 1e-9:
        end = bound
    return (start, end, increment), None


def _linux_path(value: str) -> str:
    text = str(value).strip()
    match = re.match(r"^\\\\wsl\.localhost\\[^\\]+\\(.*)$", text, re.I)
    return "/" + match.group(1).replace("\\", "/") if match else text


def _slug(values: Sequence[str]) -> str:
    full = "_".join(
        filter(None, (re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_") for value in values))
    ) or "plot"
    if len(full) <= 150:
        return full
    digest = hashlib.sha256(full.encode()).hexdigest()[:12]
    return f"{full[:137].rstrip('_')}_{digest}"


def _plot_path(stem: str, output_dir: Optional[str], output_path: Optional[str]) -> Path:
    if output_path:
        requested = Path(_linux_path(output_path)).expanduser()
        path = requested / f"{stem}.png" if requested.suffix == "" else requested
    else:
        root = output_dir or os.getenv("DISSOLVE_OUTPUT_DIR") or str(Path.cwd() / "plots")
        path = Path(_linux_path(root)).expanduser() / f"{stem}.png"
    if path.suffix.casefold() != ".png":
        raise ValueError("Visualization output_path must end in .png or name a directory.")
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "figure.dpi": 150, "savefig.dpi": _RENDER_DPI,
        "font.size": _FONT_SIZE, "axes.titlesize": _FONT_SIZE,
        "axes.labelsize": _FONT_SIZE, "xtick.labelsize": _FONT_SIZE,
        "ytick.labelsize": _FONT_SIZE, "legend.fontsize": _FONT_SIZE,
        "figure.titlesize": _FONT_SIZE, "text.color": _TEXT_COLOR,
        "axes.labelcolor": _TEXT_COLOR, "axes.titlecolor": _TEXT_COLOR,
        "xtick.color": _TEXT_COLOR, "ytick.color": _TEXT_COLOR,
        "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.alpha": 0.2, "legend.frameon": False,
    })
    return plt


def _save(fig: Any, path: Path, sidecar: dict[str, Any]) -> tuple[str, str]:
    if not str(sidecar.get("caption") or "").strip():
        raise ValueError("Every plot sidecar requires a non-empty caption.")
    sidecar.setdefault("subtitle", "")
    sidecar["render_dpi"] = _RENDER_DPI
    sidecar.update(filename_stem=path.stem, layout_inspection=measure_figure_layout(fig))
    fig.savefig(path, dpi=_RENDER_DPI, bbox_inches="tight", facecolor="white")
    sidecar_path = path.with_suffix(".terminal.json")
    sidecar_path.write_text(
        json.dumps(sidecar, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return str(path), str(sidecar_path)


def _artifact(path: str, sidecar: str, title: str) -> dict[str, str]:
    return {
        "kind": "plot", "format": "png", "title": title,
        "path": path, "sidecar_path": sidecar,
    }


def _nice_max(value: float) -> float:
    maximum = max(float(value), 0.0)
    if maximum >= 80:
        return 105.0
    if maximum >= 20:
        return min(105.0, math.ceil(maximum / 5.0) * 5.0 + 5.0)
    if maximum >= 5:
        return math.ceil(maximum / 2.0) * 2.0 + 2.0
    return max(0.1, math.ceil(max(maximum, 0.01) * 10.0) / 10.0 + 0.1)


# Display-only floor shared by every card/label formatter: numeric noise
# below this magnitude (e.g. cooling -7.6939e-17 MJ/kg) renders as "0".
# Stored sidecar payloads always keep the raw value.
_DISPLAY_FLOOR = 1e-9


def _format_value(value: Any) -> str:
    number = _finite(value)
    if number is None:
        return "—"
    if abs(number) < _DISPLAY_FLOOR:
        return "0"
    return f"{number:.5g}"


def _label_bars(axis: Any, bars: Any) -> None:
    axis.bar_label(bars, labels=[_format_value(item.get_height()) for item in bars], padding=3)


def _label_horizontal_bars(axis: Any, bars: Any, values: Sequence[Any]) -> None:
    axis.bar_label(bars, labels=[_format_value(value) for value in values], padding=3)


def _set_categorical_x_labels(
    fig: Any, axis: Any, positions: Sequence[Any], labels: Sequence[Any],
) -> None:
    """Render complete categorical labels with a measured, lossless layout."""
    rendered = [str(label) for label in labels]
    axis.set_xticks(list(positions), rendered)
    if len(rendered) < 2:
        return

    def _boxes() -> tuple[Any, list[Any], float, bool]:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        axis_box = axis.get_window_extent(renderer)
        label_boxes = [label.get_window_extent(renderer) for label in axis.get_xticklabels()]
        slot_width = axis_box.width / len(rendered)
        overlaps = any(
            first.x1 > second.x0 - 2
            for first, second in zip(label_boxes, label_boxes[1:])
        )
        return renderer, label_boxes, slot_width, overlaps

    _, label_boxes, slot_width, overlaps = _boxes()
    if max((box.width for box in label_boxes), default=0.0) <= slot_width * .92 and not overlaps:
        return

    # Preserve every character while limiting the horizontal extent before
    # rotation.  Long source identifiers and chemical display names therefore
    # remain inspectable rather than being truncated to an ambiguous prefix.
    wrapped = [
        "\n".join(textwrap.wrap(
            label, width=18, break_long_words=True, break_on_hyphens=True,
        )) or label
        for label in rendered
    ]
    axis.set_xticks(list(positions), wrapped)
    _, label_boxes, slot_width, overlaps = _boxes()
    if max((box.width for box in label_boxes), default=0.0) <= slot_width * .92 and not overlaps:
        return

    for label in axis.get_xticklabels():
        label.set_rotation(45)
        label.set_horizontalalignment("right")
        label.set_rotation_mode("anchor")
    renderer, _, slot_width, _ = _boxes()
    rotated_width = max(
        (label.get_window_extent(renderer).width for label in axis.get_xticklabels()),
        default=slot_width,
    )
    required_axis_width = rotated_width * len(rendered) / .92
    current_axis_width = max(axis.get_window_extent(renderer).width, 1.0)
    if required_axis_width > current_axis_width:
        width, height = fig.get_size_inches()
        fig.set_size_inches(width * required_axis_width / current_axis_width, height, forward=True)
        fig.canvas.draw()


def _diverging_cmap():
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list(
        "dissolve_cvd_diverging", ("#E8B35B", "#FFFFFF", "#78B7DE"),
    )


def _route_conditions(route: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the immutable condition identity used to compare route artifacts."""
    return [{
        "dissolved_polymer": str(step.get("dissolved_polymer") or ""),
        "solvent": str(step.get("solvent") or ""),
        "temperature_c": _finite(step.get("temperature_c")),
    } for step in route.get("steps") or []]


def _route_signature(route: dict[str, Any]) -> str:
    canonical = json.dumps(route, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _stored_route_matches_feed(route: dict[str, Any], polymers: Sequence[str]) -> bool:
    represented = {
        str(step.get("dissolved_polymer"))
        for step in route.get("steps") or [] if step.get("dissolved_polymer")
    }
    if route.get("final_residue"):
        represented.add(str(route["final_residue"]))
    represented.update(str(item) for item in route.get("unresolved_polymers") or [])
    return represented == set(polymers)


def _enrich_stored_route(route: dict[str, Any], polymers: Sequence[str]) -> dict[str, Any]:
    """Add plot-only thermodynamic values without changing stored route conditions."""
    enriched = copy.deepcopy(route)
    remaining = list(polymers)
    steps: list[dict[str, Any]] = []
    for raw_step in enriched.get("steps") or []:
        step = dict(raw_step)
        target = str(step.get("dissolved_polymer") or "")
        solvent = str(step.get("solvent") or "")
        temperature = _finite(step.get("temperature_c"))
        retained = [polymer for polymer in remaining if polymer != target]
        target_result = (
            thermo.get_solubility_result(target, solvent, temperature)
            if temperature is not None else {}
        )
        retained_results = {
            polymer: thermo.get_solubility_result(polymer, solvent, temperature)
            for polymer in retained
        } if temperature is not None else {}
        target_value = target_result.get("solubility_pct")
        retained_values = [
            result.get("solubility_pct") for result in retained_results.values()
        ]
        if target_value is not None and retained_values and all(
            value is not None for value in retained_values
        ):
            step["target_solubility_pct"] = float(target_value)
            step["max_off_target_solubility_pct"] = max(float(value) for value in retained_values)
            step["selectivity_pct"] = (
                float(target_value) - step["max_off_target_solubility_pct"]
            )
            step["solubility_method_by_polymer"] = {
                target: target_result["method"],
                **{
                    polymer: result["method"]
                    for polymer, result in retained_results.items()
                },
            }
        step["retained_polymers"] = retained
        steps.append(step)
        if target in remaining:
            remaining.remove(target)
    enriched["steps"] = steps
    return enriched


def _metric_axis(metric: str) -> tuple[float, str]:
    """Return canvas-only scaling and an explicit engineering axis label."""
    return {
        "total_cost": (1_000_000.0, "Total annual cost ($M/yr)"),
        "profit": (1_000_000.0, "Annual profit ($M/yr)"),
        "emissions": (1.0, "Annual emissions (t CO₂e/yr)"),
        "circularity": (1.0, "Circularity (fraction of feed)"),
        "selectivity": (1.0, "Bottleneck selectivity (percentage points)"),
    }.get(metric, (1.0, metric.replace("_", " ").title()))


def _stored_pareto_evidence(tool: str, state: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Load and validate the route-bound optimization payload once."""
    current = getattr(state, "last_optimization", None) if state else None
    path_value = (current or {}).get("pareto_payload_path")
    if not path_value:
        return None, tool_error(
            tool, "No Pareto optimization has been run for the current route. "
            "Compute the frontier before requesting a plot.",
            error_code="missing_optimization_result",
        )
    try:
        payload = json.loads(Path(str(path_value)).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None, tool_error(
            tool, "The stored Pareto payload cannot be read.",
            error_code="optimization_payload_unavailable",
        )
    if payload.get("schema") != "dissolve.optimization-payload.v1":
        return None, tool_error(
            tool, "The stored Pareto payload schema is unsupported.",
            error_code="optimization_payload_invalid",
        )
    if payload.get("source_route_signature") != current.get("source_route_signature"):
        return None, tool_error(
            tool, "The stored Pareto payload does not match the current route.",
            error_code="stale_optimization_result",
        )
    slices = list(payload.get("slices") or [])
    if not slices:
        return None, tool_error(
            tool, "The stored Pareto payload has no solved slice.",
            error_code="optimization_payload_empty",
        )
    selected = slices[0]
    landscape = list(selected.get("landscape_points") or [])
    frontier = list(selected.get("points") or [])
    x_metric, y_metric = str(payload.get("x_metric")), str(payload.get("y_metric"))
    if not landscape or not frontier or any(
        point.get(x_metric) is None or point.get(y_metric) is None
        for point in [*landscape, *frontier]
    ):
        return None, tool_error(
            tool, "The stored Pareto payload lacks plottable axis values.",
            error_code="optimization_payload_empty",
        )
    return {
        "current": current, "payload_path": str(Path(str(path_value)).resolve()),
        "x_metric": x_metric, "y_metric": y_metric,
        "landscape": landscape, "frontier": frontier,
        "knee_point": selected.get("knee_point"),
        "knee_status": selected.get("knee_status"),
        "cheapest_point": selected.get("cheapest_point"),
        "feed_mass_fractions": selected.get("feed_mass_fractions"),
        "frontier_tradeoff": current.get("frontier_tradeoff"),
        "source_route_signature": current.get("source_route_signature"),
    }, None


def _draw_scale_evidence(axis: Any, rows: Sequence[dict[str, Any]], *, annotate: bool) -> tuple[Any, list[Any]]:
    """Draw scale evidence with provenance encoded independently of metric color."""
    capacities = [float(item["processing_capacity_mt_per_yr"]) for item in rows]
    msp = [float(item["msp_usd_per_kg"]) for item in rows]
    gwp = [float(item["gwp_t_co2e_per_mt"]) for item in rows]
    other = axis.twinx()
    first = axis.plot(capacities, msp, "-", color=_COLORS[0], label="MSP")
    second = other.plot(capacities, gwp, "--", color=_COLORS[1], label="GWP intensity")
    from matplotlib.lines import Line2D
    marker_handles = []
    for provenance, marker, label in (
        ("exact_cached_simulation", "o", "Exact cached simulation"),
        ("cache_derived_screening_estimate", "s", "Screening estimate"),
    ):
        indexes = [
            index for index, row in enumerate(rows)
            if row.get("provenance_class") == provenance
        ]
        if not indexes:
            continue
        axis.scatter(
            [capacities[index] for index in indexes], [msp[index] for index in indexes],
            marker=marker, s=64, facecolor=_COLORS[0], edgecolor="#000000", zorder=4,
        )
        other.scatter(
            [capacities[index] for index in indexes], [gwp[index] for index in indexes],
            marker=marker, s=64, facecolor=_COLORS[1], edgecolor="#000000", zorder=4,
        )
        marker_handles.append(Line2D(
            [0], [0], marker=marker, linestyle="", color="#777777",
            markeredgecolor="#000000", label=label,
        ))
    if annotate:
        for index, (capacity, msp_value, gwp_value) in enumerate(zip(capacities, msp, gwp)):
            at_left = index == 0
            horizontal = 6 if at_left else -6
            alignment = "left" if at_left else "right"
            vertical = (-16, -34) if at_left else (10, 28)
            axis.annotate(
                f"MSP {_format_value(msp_value)}", (capacity, msp_value),
                xytext=(horizontal, vertical[0]), textcoords="offset points", ha=alignment,
            )
            other.annotate(
                f"GWP {_format_value(gwp_value)}", (capacity, gwp_value),
                xytext=(horizontal, vertical[1]), textcoords="offset points", ha=alignment,
            )
    axis.set_xlabel("Processing capacity (metric tonnes/year)")
    axis.set_ylabel("MSP (USD/kg product)")
    other.set_ylabel("GWP (t CO2e/t product)")
    return other, first + second + marker_handles


def _draw_pareto_evidence(axis: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    """Draw a stored feasible landscape without inventing a knee."""
    landscape, frontier = evidence["landscape"], evidence["frontier"]
    x_metric, y_metric = evidence["x_metric"], evidence["y_metric"]
    x_scale, x_axis_label = _metric_axis(x_metric)
    y_scale, y_axis_label = _metric_axis(y_metric)
    axis.scatter(
        [float(point[x_metric]) / x_scale for point in landscape],
        [float(point[y_metric]) / y_scale for point in landscape],
        color="#AAAAAA", alpha=.65, label="Feasible landscape",
    )
    ordered = sorted(frontier, key=lambda point: float(point[x_metric]))
    axis.plot(
        [float(point[x_metric]) / x_scale for point in ordered],
        [float(point[y_metric]) / y_scale for point in ordered],
        "o-", color=_COLORS[0], linewidth=2, label="Non-dominated frontier",
    )
    marked = [("Cheapest", "D", _COLORS[2], evidence.get("cheapest_point"))]
    if evidence.get("knee_status") == "interior_tradeoff":
        marked.insert(0, ("Knee", "*", _COLORS[1], evidence.get("knee_point")))
    for label, marker, color, point in marked:
        if point:
            axis.scatter(
                [float(point[x_metric]) / x_scale],
                [float(point[y_metric]) / y_scale], marker=marker, s=130,
                color=color, label=label, zorder=4,
            )
    axis.set_xlabel(x_axis_label)
    axis.set_ylabel(y_axis_label)
    axis.legend()
    return {
        "x_scale": x_scale, "y_scale": y_scale,
        "x_axis_label": x_axis_label, "y_axis_label": y_axis_label,
    }


def plot_solubility_curves(
    polymers: list[str],
    solvents: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    temperature_step_c: float = 5.0,
    strict_maximum: bool = False,
    y_axis_max: Optional[float] = None, reference_temperature_c: Optional[float] = None,
    reference_temperature_label: Optional[str] = None, reference_solubility_wt_pct: Optional[float] = None,
    reference_solubility_label: Optional[str] = None, highlight_solvent: Optional[str] = None, show_markers: bool = True,
    output_dir: Optional[str] = None, output_path: Optional[str] = None,
) -> str:
    """Plot ordinary thermo curves, not stored precipitation/dropout analysis."""
    tool = "plot_solubility_curves"
    polymer_names, error = _resolve_polymers(tool, polymers)
    if error:
        return error
    solvent_names, error = _resolve_solvents(tool, solvents)
    if error:
        return error
    (start, end, step), error = _range(
        tool,
        # A domain referent is never invented by a literal default: an
        # absent bound resolves to the engine's typed fitted window.
        thermo.FITTED_TEMP_MIN_C
        if temperature_min_c is None else temperature_min_c,
        thermo.FITTED_TEMP_MAX_C
        if temperature_max_c is None else temperature_max_c,
        temperature_step_c,
        bool(strict_maximum),
    )
    if error:
        return error
    (
        axis_max, reference_temperature,
        reference_solubility, highlighted_solvent,
        user_reference_lines,
        option_error,
    ) = _solubility_curve_options(
        tool,
        solvent_names=solvent_names,
        start=start, end=end,
        y_axis_max=y_axis_max,
        reference_temperature_c=reference_temperature_c,
        reference_temperature_label=reference_temperature_label,
        reference_solubility_wt_pct=reference_solubility_wt_pct,
        reference_solubility_label=reference_solubility_label,
        highlight_solvent=highlight_solvent,
    )
    if option_error:
        return option_error

    series, excluded = [], []
    for polymer in polymer_names:
        for solvent in solvent_names:
            curve = thermo.get_solubility_curve(polymer, solvent, start, end, step)
            if not curve:
                excluded.append(f"{polymer}/{thermo.canonical_solvent_name(solvent)}")
                continue
            entry = thermo.get_entry(polymer, solvent) or {}
            points = [[float(item["temperature"]), float(item["solubility"])] for item in curve]
            series.append({
                "polymer": polymer,
                "solvent": thermo.canonical_solvent_name(solvent),
                "solvent_data_key": solvent,
                "label": f"{polymer} in {thermo.canonical_solvent_name(solvent)}",
                "points": points,
                "solubility_methods": [str(item["method"]) for item in curve],
                "temperature_use_regimes": [
                    str(item["temperature_use_regime"]) for item in curve
                ],
                "fitted_temperature_range_c": next((
                    item.get("fitted_temperature_range_c")
                    for item in curve
                    if item.get("fitted_temperature_range_c") is not None
                ), None),
                "source_grid_temperature_range_c": next((
                    item.get("source_grid_temperature_range_c")
                    for item in curve
                    if item.get("source_grid_temperature_range_c") is not None
                ), None),
                "category": entry.get("category"),
            })
    if not series:
        return tool_error(
            tool, "No supported polymer-solvent curves were found.",
            error_code="no_supported_pairs", excluded_pairs=excluded,
        )

    screening_reference = 5.0
    screening_reference_first_setpoint = {
        item["label"]: next((
            temperature for temperature, solubility in item["points"]
            if solubility >= screening_reference
        ), None)
        for item in series
    }
    temperatures = sorted({point[0] for item in series for point in item["points"]})
    regimes_by_temperature: dict[float, list[str]] = {}
    for item in series:
        for (temperature, _solubility), regime in zip(
            item["points"], item["temperature_use_regimes"],
        ):
            regimes_by_temperature.setdefault(float(temperature), []).append(
                str(regime),
            )
    regimes = [
        thermo.aggregate_temperature_use_regimes(
            value, regimes_by_temperature.get(value, ()),
        )
        for value in temperatures
    ]
    temperature_regimes = [
        {"temperature_c": value, "regime": regime} for value, regime in zip(temperatures, regimes)
    ]
    regime_counts = {regime: regimes.count(regime) for regime in dict.fromkeys(regimes)}
    fitted_ranges_by_series = {
        item["label"]: item["fitted_temperature_range_c"]
        for item in series
        if item.get("fitted_temperature_range_c") is not None
    }
    source_grid_ranges_by_series = {
        item["label"]: item["source_grid_temperature_range_c"]
        for item in series
        if item.get("source_grid_temperature_range_c") is not None
    }
    regime_boundaries = {
        "coverage_scope": "per_series",
        "fitted_temperature_range_c_by_series": fitted_ranges_by_series,
        "source_grid_temperature_range_c_by_series": (
            source_grid_ranges_by_series
        ),
        "recommended_extrapolation_max_c": thermo.RECOMMENDED_EXTRAPOLATION_MAX_C,
        "supported_sensitivity_max_c": thermo.SENSITIVITY_EXTRAPOLATION_MAX_C,
    }
    unique_fitted_ranges = {
        tuple(value) for value in fitted_ranges_by_series.values()
    }
    if len(unique_fitted_ranges) == 1:
        fitted_min_c, fitted_max_c = next(iter(unique_fitted_ranges))
        regime_boundaries.update({
            "fitted_min_c": fitted_min_c,
            "fitted_max_c": fitted_max_c,
        })
    extrapolation_regions = []
    for label, fitted_range in fitted_ranges_by_series.items():
        fitted_min_c, fitted_max_c = fitted_range
        if start < fitted_min_c:
            extrapolation_regions.append({
                "series_label": label,
                "regime": "below_fit_extrapolation",
                "temperature_min_inclusive_c": start,
                "temperature_max_exclusive_c": min(end, fitted_min_c),
            })
        if end > fitted_max_c:
            extrapolation_regions.append({
                "series_label": label,
                "regime": "exploratory_extrapolation",
                "temperature_min_exclusive_c": fitted_max_c,
                "temperature_max_inclusive_c": min(
                    end, thermo.RECOMMENDED_EXTRAPOLATION_MAX_C,
                ),
            })
        if end > thermo.RECOMMENDED_EXTRAPOLATION_MAX_C:
            extrapolation_regions.append({
                "series_label": label,
                "regime": "sensitivity_extrapolation",
                "temperature_min_exclusive_c": (
                    thermo.RECOMMENDED_EXTRAPOLATION_MAX_C
                ),
                "temperature_max_inclusive_c": end,
            })
    reference_lines = []
    fitted_starts = sorted({
        float(value[0]) for value in fitted_ranges_by_series.values()
        if start < float(value[0]) <= end
    })
    fitted_ends = sorted({
        float(value[1]) for value in fitted_ranges_by_series.values()
        if start <= float(value[1]) < end
    })
    reference_lines.extend({
        "axis": "x", "value": value,
        "label": f"Pair fitted start ({value:g} °C)", "color": "#56B4E9",
    } for value in fitted_starts)
    reference_lines.extend({
        "axis": "x", "value": value,
        "label": f"Pair fitted ceiling ({value:g} °C)", "color": "#E69F00",
    } for value in fitted_ends)
    if end > thermo.RECOMMENDED_EXTRAPOLATION_MAX_C:
        reference_lines.append({
            "axis": "x", "value": thermo.RECOMMENDED_EXTRAPOLATION_MAX_C,
            "label": "Exploratory limit (180 °C)", "color": "#D55E00"})
    reference_lines.extend(user_reference_lines)

    (
        fig,
        maximum,
        effective_axis_max,
        curve_presentation,
    ) = _render_solubility_curves(
        _pyplot(),
        series,
        start=start,
        end=end,
        axis_max=axis_max,
        reference_lines=reference_lines,
        extrapolation_regions=extrapolation_regions,
        highlighted_solvent=highlighted_solvent,
        show_markers=bool(show_markers),
    )
    range_text = (
        f"{float(temperature_min_c):g} ≤ T < {float(temperature_max_c):g} °C"
        if strict_maximum else
        f"{float(temperature_min_c):g}–{float(temperature_max_c):g} °C, inclusive"
    )
    title = "Temperature-dependent polymer solubility"
    clipped = maximum >= 100.0
    caption = (
        "Pair-specific source-grid and fitted solution concentration; not feed "
        "recovery or purity."
    )
    if extrapolation_regions:
        caption += (
            " Shading begins where at least one series is predictive "
            "extrapolation rather than fitted data."
        )
    if clipped:
        caption += " 100 wt% is the clipped model ceiling."
    stem = _slug([
        *polymer_names, *solvent_names,
        f"{start:g}c_to_{float(temperature_max_c):g}c",
        "exclusive" if strict_maximum else "inclusive",
        f"{step:g}c_step",
        f"ymax_{axis_max:g}" if axis_max is not None else "auto_y",
        f"reference_{reference_temperature:g}c" if reference_temperature is not None else "",
        f"target_{reference_solubility:g}wt" if reference_solubility is not None else "",
        f"highlight_{highlighted_solvent}" if highlighted_solvent else "",
        "markers_off" if not show_markers else "",
        "solubility_vs_temp",
    ])
    path = _plot_path(stem, output_dir, output_path)
    sidecar = {
        "schema": "dissolve.plot.v1", "plot_type": "solubility_temperature",
        "title": title, "subtitle": range_text, "caption": caption,
        "x_label": "Temperature (°C)",
        "y_label": "Solubility (wt %)", "x_min": start, "x_max": end,
        "y_axis_min": 0.0, "y_axis_max": effective_axis_max,
        "clip_limit_wt_percent": 100.0,
        "clipped_model_ceiling_present": clipped,
        "temperature_regime_counts": regime_counts,
        "temperature_regime_boundaries_c": regime_boundaries,
        "temperature_regimes": temperature_regimes,
        "extrapolation_regions": extrapolation_regions,
        "reference_lines": reference_lines,
        "screening_reference_wt_pct": screening_reference,
        "screening_reference_first_setpoint_c": screening_reference_first_setpoint,
        "curve_presentation": curve_presentation,
        "series": [{
            key: item[key]
            for key in (
                "label", "color", "polymer", "solvent", "points",
                "solubility_methods", "temperature_use_regimes",
                "fitted_temperature_range_c",
                "source_grid_temperature_range_c",
                "highlighted", "line_width", "line_alpha", "marker",
                "end_label_y",
            )
        } for item in series],
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    _pyplot().close(fig)
    summaries = [{
        "polymer": item["polymer"], "solvent": item["solvent"],
        "first_temperature_c": item["points"][0][0],
        "first_solubility_pct": item["points"][0][1],
        "last_temperature_c": item["points"][-1][0],
        "last_solubility_pct": item["points"][-1][1],
        "maximum_solubility_pct": max(point[1] for point in item["points"]),
        "solubility_methods": list(dict.fromkeys(item["solubility_methods"])),
        "temperature_use_regimes": list(dict.fromkeys(
            item["temperature_use_regimes"],
        )),
        "fitted_temperature_range_c": item.get(
            "fitted_temperature_range_c",
        ),
        "source_grid_temperature_range_c": item.get(
            "source_grid_temperature_range_c",
        ),
        "category": item["category"],
    } for item in series]
    warnings = [
        "Pair-specific modeled concentrations; not feed recovery or purity.",
    ]
    if clipped:
        warnings.append("A 100 wt% value is a clipped model ceiling, not a precise prediction.")
    extrapolation_regimes = {
        row["regime"] for row in extrapolation_regions
    }
    if "below_fit_extrapolation" in extrapolation_regimes:
        warnings.append(
            "Some points are lower-confidence extrapolations below their "
            "series' disclosed fitted minimum."
        )
    if "exploratory_extrapolation" in extrapolation_regimes:
        warnings.append(
            "Some points through 180 C are exploratory extrapolations above "
            "their series' disclosed fitted maximum."
        )
    if "sensitivity_extrapolation" in extrapolation_regimes:
        warnings.append("Points above 180 through 200 C are sensitivity-only extrapolations for screening, not fitted data.")
    return tool_success(
        tool, display=f"Full-resolution plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, "Temperature-dependent solubility"),
        analysis_type="solubility_plot", plot_type="solubility_temperature",
        plot_paths=[plot_path], plot_path=plot_path, terminal_plot_data_path=sidecar_path,
        polymers=polymer_names,
        solvents=[thermo.canonical_solvent_name(item) for item in solvent_names],
        temperature_min_c=float(temperature_min_c), temperature_max_c=end,
        requested_temperature_max_c=float(temperature_max_c), strict_maximum=bool(strict_maximum),
        temperature_step_c=step, y_axis_min=0.0, y_axis_max=effective_axis_max,
        is_clipped=clipped, clip_limit_wt_percent=100.0,
        temperature_regime_counts=regime_counts,
        temperature_regime_boundaries_c=regime_boundaries,
        extrapolation_regions=extrapolation_regions,
        screening_reference_wt_pct=screening_reference,
        screening_reference_first_setpoint_c=screening_reference_first_setpoint,
        reference_temperature_c=reference_temperature,
        reference_solubility_wt_pct=reference_solubility,
        highlight_solvent=(
            thermo.canonical_solvent_name(highlighted_solvent)
            if highlighted_solvent else None
        ),
        show_markers=bool(show_markers),
        n_series=len(series), n_points=sum(len(item["points"]) for item in series),
        series_summaries=summaries, excluded_pairs=excluded,
        solubility_unit="wt_pct_solution_concentration", warnings=warnings,
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
    )


def plot_fixed_temperature_separation(
    feed_polymers: list[str],
    temperature_c: float,
    solvents: Optional[list[str]] = None,
    target_polymers: Optional[list[str]] = None,
    ranking_mode: str = "target_dissolution",
    require_atmospheric: bool = True,
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Regenerate and plot target/off-target values for ranked candidates at exact T."""
    tool = "plot_fixed_temperature_separation"
    polymers, error = _resolve_polymers(tool, feed_polymers)
    if error:
        return error
    if len(polymers) < 2:
        return tool_error(tool, "At least two feed polymers are required.", error_code="insufficient_feed_polymers")
    temperature = _finite(temperature_c)
    if temperature is None or temperature <= -273.15 or temperature > _MAX_T:
        return tool_error(tool, "Temperature is outside the supported physical range.", error_code="invalid_temperature")
    targets = None
    if target_polymers:
        targets, error = _resolve_polymers(tool, target_polymers)
        if error:
            return error
    mode = str(ranking_mode or "target_dissolution").strip().casefold()
    if mode not in {"target_dissolution", "separation_gap"}:
        return tool_error(
            tool, "Fixed-temperature plots support target_dissolution or separation_gap.",
            error_code="unsupported_ranking_mode",
        )
    screen = parse_tool_result(screen_polymer_separation(
        polymers, temperature_min_c=temperature, temperature_max_c=temperature,
        target_polymers=targets,
        solvents=solvents,
        ranking_mode=mode,
        require_atmospheric=bool(require_atmospheric),
    ))["data"]
    if screen.get("success") is not True:
        return tool_error(tool, "The fixed-temperature screen could not be reproduced.", error_code="screen_failed")
    ranked = list(screen.get("ranked_candidates") or [])
    requested = _items(solvents)
    if requested:
        wanted, error = _resolve_solvents(tool, requested)
        if error:
            return error
        by_key = {}
        for item in ranked:
            key = thermo.resolve_solvent(str(item.get("solvent"))) or ""
            by_key.setdefault(key, item)
        missing = [thermo.canonical_solvent_name(item) for item in wanted if item not in by_key]
        if missing:
            return tool_error(
                tool, "Candidate(s) absent from reproduced screen: " + ", ".join(missing),
                error_code="candidate_not_in_screen", missing_solvents=missing,
            )
        rows = [dict(by_key[item]) for item in wanted]
    else:
        rows = [dict(item) for item in ranked[:3]]
    if not rows:
        return tool_error(tool, "No fixed-temperature candidates were returned.", error_code="no_candidates")

    plt = _pyplot()
    fig, (left, right) = plt.subplots(1, 2, figsize=(max(9.0, len(rows) * 1.35 + 5), 5.2), gridspec_kw={"width_ratios": [1.7, 1]})
    positions = list(range(len(rows)))
    width = 0.34
    target_values = [float(item["target_solubility_pct"]) for item in rows]
    off_key = (
        "closest_off_target_solubility_pct"
        if mode == "separation_gap" else "max_off_target_solubility_pct"
    )
    off_values = [float(item[off_key]) for item in rows]
    selection = [float(item["selectivity_pct"]) for item in rows]
    target_label = "Named target" if mode == "separation_gap" else "Dissolved target"
    off_label = "Closest off-target" if mode == "separation_gap" else "Maximum off-target"
    left.bar([item - width / 2 for item in positions], target_values, width, label=target_label, color=_COLORS[0])
    left.bar([item + width / 2 for item in positions], off_values, width, label=off_label, color=_COLORS[1])
    direction_labels = {
        "target_lower_than_all_off_targets": "lower",
        "target_higher_than_all_off_targets": "higher",
        "mixed": "mixed",
    }
    labels = [
        (
            f"{item['solvent']}\n→ {item['dissolved_polymer']} "
            f"{direction_labels.get(str(item.get('target_solubility_direction')), 'compare')}"
            if mode == "separation_gap" else
            f"{item['solvent']}\n→ dissolve {item['dissolved_polymer']}"
        )
        for item in rows
    ]
    _set_categorical_x_labels(fig, left, positions, labels)
    left.set_ylabel("Modeled solution concentration (wt%)")
    left.legend()
    right.barh(positions, selection, color=[_COLORS[2] if item >= 5 else _COLORS[4] for item in selection])
    right.set_yticks(positions, [str(item["solvent"]) for item in rows])
    right.invert_yaxis()
    right.axvline(5.0, color="#555555", linestyle="--", linewidth=0.9)
    right.set_xlabel(
        "Minimum gap (points)"
        if mode == "separation_gap" else "Selectivity (percentage points)"
    )
    title = "Polymer separation candidates"
    fig.suptitle(title)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    path = _plot_path(_slug([*polymers, *[str(item["solvent"]) for item in rows], f"{temperature:g}c_separation"]), output_dir, output_path)
    compact_rows = [{key: item.get(key) for key in (
        "overall_rank", "dissolved_polymer", "retained_polymers", "solvent",
        "temperature_c", "target_solubility_pct", "max_off_target_solubility_pct",
        "limiting_off_target_polymer", "selectivity_pct", "meets_selectivity_threshold",
        "minimum_target_off_target_gap_pct", "closest_off_target_solubility_pct",
        "signed_target_minus_limiting_off_target_pct", "target_solubility_direction",
        "solubility_method_by_polymer",
    )} for item in rows]
    sidecar = {
        "schema": "dissolve.plot.v1", "plot_type": "fixed_temperature_separation",
        "title": title,
        "subtitle": f"{' / '.join(polymers)} at {temperature:g} °C",
        "caption": (
            "Absolute gaps compare the named target with its closest off-target; "
            "they do not specify which polymer dissolves preferentially or establish recovery."
            if mode == "separation_gap" else
            "Model-screened solution concentrations; not feed recovery or validated purity."
        ),
        "temperature_c": temperature,
        "polymers": polymers, "candidates": compact_rows,
        "ranking_mode": mode, "require_atmospheric": bool(require_atmospheric),
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool, display=f"Full-resolution plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, "Fixed-temperature separation"),
        analysis_type="fixed_temperature_separation_plot",
        plot_type="fixed_temperature_separation", plot_paths=[plot_path], plot_path=plot_path,
        terminal_plot_data_path=sidecar_path, polymers=polymers,
        solvents=[str(item["solvent"]) for item in rows], temperature_c=temperature,
        candidate_conditions=compact_rows, ranking_mode=mode,
        warnings=["Modeled solution concentration does not establish feed recovery or product purity."],
        model_basis="fixed-temperature adaptive screen regenerated inside the plot tool",
    )


def plot_separation_analysis(
    feed_polymers: list[str],
    view: str = "route",
    solvent: Optional[str] = None,
    solvents: Optional[list[str]] = None,
    temperature_c: Optional[float] = None,
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: Optional[bool] = None,
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Plot stored precipitation proxies or another separation view for a requested subset."""
    tool = "plot_separation_analysis"
    polymers, error = _resolve_polymers(tool, feed_polymers)
    if error:
        return error
    if len(polymers) < 2:
        return tool_error(tool, "At least two feed polymers are required.", error_code="insufficient_feed_polymers")
    requested_mode = str(view or "route").strip().casefold().replace("-", "_").replace(" ", "_")
    mode = requested_mode
    mode = {
        "tree": "route", "pfd": "route", "process_flow_diagram": "route",
        "dp_state_map": "state_map", "heatmap": "selectivity_heatmap",
        "precipitation_curves": "precipitation",
        "feasibility": "atmospheric_feasibility",
        "route_window": "route_windows", "window_comparison": "route_windows",
        "route_landscape_comparison": "route_windows",
    }.get(mode, mode)
    supported = {
        "route", "state_map", "selectivity_heatmap", "precipitation",
        "precipitation_ladder", "atmospheric_feasibility", "route_windows",
    }
    if mode not in supported:
        return tool_error(
            tool, "Unsupported separation plot view.", error_code="unsupported_plot_view",
            supported_views=sorted(supported),
        )

    if mode == "precipitation_ladder":
        session = current_tool_session()
        stored_rows, candidate_source = candidate_evidence(
            session, {CANDIDATE_SHAPE_PRECIPITATION},
        )
        rows = [
            dict(item) for item in stored_rows
            if item.get("precipitation_proxy_order")
            and item.get("precipitation_proxy_crossings_c")
        ]
        if not rows:
            if session and session.last_candidates and candidate_source:
                return tool_error(
                    tool,
                    "The stored candidate evidence is not a precipitation screen; "
                    "rerun the precipitation screen before plotting its ladder.",
                    error_code="candidate_evidence_kind_mismatch",
                    analysis_type="candidate_evidence_basis_gap",
                    requested_analysis_type="precipitation_ladder_visualization",
                    candidate_evidence_source=candidate_source.get("source_tool"),
                    candidate_evidence_shape=candidate_source.get("shape"),
                    required_candidate_shape=CANDIDATE_SHAPE_PRECIPITATION,
                )
            return tool_error(
                tool, "No full-feed precipitation-order result is available in typed session state.",
                error_code="missing_full_feed_precipitation_result",
            )
        plt = _pyplot()
        fig, ax = plt.subplots(figsize=(9.2, max(4.5, 1.05 * len(rows) + 2.2)))
        polymer_colors = {
            polymer: _COLORS[index % len(_COLORS)] for index, polymer in enumerate(polymers)
        }
        for row_index, row in enumerate(rows):
            order = [item for item in row["precipitation_proxy_order"] if item in polymers]
            crossings = row["precipitation_proxy_crossings_c"]
            temperatures = [float(crossings[item]) for item in order if crossings.get(item) is not None]
            if temperatures:
                ax.plot(temperatures, [row_index] * len(temperatures), color="#000000", linewidth=1)
            for polymer in order:
                if crossings.get(polymer) is not None:
                    ax.scatter(
                        [float(crossings[polymer])], [row_index], s=80,
                        color=polymer_colors[polymer], label=polymer,
                    )
        handles, labels = ax.get_legend_handles_labels()
        unique = dict(zip(labels, handles))
        ax.legend(unique.values(), unique.keys(), loc="center left", bbox_to_anchor=(1.01, .5))
        ax.set_yticks(range(len(rows)), [str(row["solvent"]) for row in rows])
        ax.invert_yaxis()
        ax.set_xlabel("1 wt% proxy crossing while cooling (°C)")
        title = "Full-feed precipitation-order screen"
        ax.set_title(title)
        sidecar_rows = [{key: row.get(key) for key in (
            "solvent", "dissolution_temperature_c", "precipitation_proxy_order",
            "precipitation_proxy_crossings_c", "adjacent_proxy_windows_c",
            "minimum_adjacent_proxy_window_c", "boiling_point_margin_c",
            "dissolution_solubility_method_by_polymer",
            "crossing_method_by_polymer",
        )} for row in rows]
        path = _plot_path(_slug([*polymers, "precipitation_ladder"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "precipitation_ladder",
            "title": title, "subtitle": "1 wt% loading-dependent cooling proxy",
            "caption": "Marker order is the stored full-feed proxy sequence; it is not a measured cloud point, recovery yield, or validated precipitation train.",
            "polymers": polymers, "candidates": sidecar_rows,
        }
        plot_path, sidecar_path = _save(fig, path, sidecar)
        plt.close(fig)
        return tool_success(
            tool, display=f"Full-resolution precipitation-order ladder: {plot_path}",
            artifact=_artifact(plot_path, sidecar_path, title),
            analysis_type="separation_plot", plot_type="precipitation_ladder",
            plot_paths=[plot_path], plot_path=plot_path,
            terminal_plot_data_path=sidecar_path, polymers=polymers,
            solvents=[str(row["solvent"]) for row in rows],
            candidate_conditions=sidecar_rows,
            warnings=[sidecar["caption"]],
            model_basis="stored full-feed precipitation-screen proxy crossings",
        )

    if mode == "selectivity_heatmap":
        temperature = _finite(temperature_c)
        if temperature is None or temperature <= -273.15 or temperature > _MAX_T:
            return tool_error(
                tool, "A supported exact temperature is required for a selectivity heatmap.",
                error_code="missing_fixed_temperature",
            )
        requested_solvents = _items(solvents)
        if requested_solvents:
            solvent_names, error = _resolve_solvents(tool, requested_solvents)
            if error:
                return error
        else:
            leading: list[str] = []
            for target in polymers:
                for item in thermo.get_all_solvents_selectivity(
                    target, [other for other in polymers if other != target], temperature,
                )[:5]:
                    if item["solvent"] not in leading:
                        leading.append(item["solvent"])
            solvent_names = leading[:8]
        matrix: list[list[float | None]] = []
        method_matrix: list[list[dict[str, str] | None]] = []
        for target in polymers:
            retained = [other for other in polymers if other != target]
            row = []
            method_row = []
            for candidate in solvent_names:
                target_result = thermo.get_solubility_result(
                    target, candidate, temperature,
                )
                other_results = {
                    other: thermo.get_solubility_result(
                        other, candidate, temperature,
                    )
                    for other in retained
                }
                target_value = target_result.get("solubility_pct")
                others = [
                    result.get("solubility_pct")
                    for result in other_results.values()
                ]
                row.append(
                    None if target_value is None or any(value is None for value in others)
                    else float(target_value) - max(float(value) for value in others)
                )
                method_row.append(
                    None if row[-1] is None else {
                        target: target_result["method"],
                        **{
                            other: result["method"]
                            for other, result in other_results.items()
                        },
                    }
                )
            matrix.append(row)
            method_matrix.append(method_row)
        if not solvent_names or not any(value is not None for row in matrix for value in row):
            return tool_error(tool, "No selectivity matrix values were available.", error_code="no_data")
        numeric = [[float(value or 0.0) for value in row] for row in matrix]
        plt = _pyplot()
        title = "Solvent selectivity by target"
        if len(polymers) == 2:
            fig, ax = plt.subplots(figsize=(8.6, max(4.4, .65 * len(solvent_names) + 2.2)))
            positions = list(range(len(solvent_names)))
            height = .34
            for index, (polymer, row) in enumerate(zip(polymers, numeric)):
                offset = (index - .5) * height
                ax.barh(
                    [position + offset for position in positions], row, height,
                    label=f"Dissolve {polymer}", color=_COLORS[index],
                )
            ax.axvline(0.0, color="#000000", linewidth=1)
            ax.set_yticks(
                positions,
                [thermo.canonical_solvent_name(item) for item in solvent_names],
            )
            ax.invert_yaxis()
            ax.set_xlabel("Selectivity (percentage points)")
            ax.legend(loc="center left", bbox_to_anchor=(1.01, .5))
            render_mode = "signed_horizontal_bars"
        else:
            fig, ax = plt.subplots(figsize=(max(7.0, 1.2 * len(solvent_names) + 3), max(4.5, .7 * len(polymers) + 2)))
            absolute_limit = max(abs(value) for row in numeric for value in row) or 1.0
            image = ax.imshow(
                numeric, aspect="auto", cmap=_diverging_cmap(),
                vmin=-absolute_limit, vmax=absolute_limit,
            )
            _set_categorical_x_labels(
                fig, ax, range(len(solvent_names)),
                [thermo.canonical_solvent_name(item) for item in solvent_names],
            )
            ax.set_yticks(range(len(polymers)), [f"Dissolve {item}" for item in polymers])
            for row_index, row in enumerate(matrix):
                for column_index, value in enumerate(row):
                    ax.text(column_index, row_index, _format_value(value), ha="center", va="center")
            fig.colorbar(image, ax=ax, label="Selectivity (percentage points)")
            render_mode = "cvd_diverging_matrix"
        ax.set_title(title)
        fig.tight_layout()
        path = _plot_path(_slug([
            *polymers, *solvent_names, f"{temperature:g}c_selectivity_heatmap",
        ]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "selectivity_heatmap",
            "title": title,
            "subtitle": f"{temperature:g} °C · target minus maximum off-target",
            "caption": "Positive values favor the named dissolution target; values are model-screened percentage-point differences.",
            "render_mode": render_mode,
            "polymers": polymers, "solvents": [thermo.canonical_solvent_name(item) for item in solvent_names],
            "temperature_c": temperature, "selectivity_unit": "percentage_points",
            "matrix": matrix,
            "solubility_method_matrix": method_matrix,
        }
        plot_path, sidecar_path = _save(fig, path, sidecar)
        plt.close(fig)
        return tool_success(
            tool, display=f"Full-resolution selectivity heatmap: {plot_path}",
            artifact=_artifact(plot_path, sidecar_path, "Selectivity heatmap"),
            analysis_type="separation_plot", plot_type="selectivity_heatmap",
            plot_paths=[plot_path], plot_path=plot_path,
            terminal_plot_data_path=sidecar_path, polymers=polymers,
            solvents=sidecar["solvents"], temperature_c=temperature,
            selectivity_matrix=matrix, selectivity_unit="percentage_points",
            solubility_method_matrix=method_matrix,
            warnings=["Selectivity is target minus maximum off-target modeled solution concentration."],
            model_basis="admitted thermodynamic kernel regenerated at one exact temperature",
        )

    if mode == "precipitation":
        session = current_tool_session()
        stored_rows, candidate_source = candidate_evidence(
            session, {CANDIDATE_SHAPE_PRECIPITATION},
        )
        requested_solvent = thermo.resolve_solvent(str(solvent)) if solvent else None
        decision = next((
            item for item in stored_rows
            if item.get("first_precipitation_proxy_c") is not None
            and item.get("second_precipitation_proxy_c") is not None
            and {item.get("first_polymer"), item.get("second_polymer")} <= set(polymers)
            and (
                requested_solvent is None
                or thermo.resolve_solvent(str(item.get("solvent"))) == requested_solvent
            )
        ), None)
        selected_solvent = solvent or (decision or {}).get("solvent")
        if (
            not selected_solvent and session and session.last_candidates
            and candidate_source
        ):
            return tool_error(
                tool,
                "The stored candidate evidence is not a precipitation screen; "
                "name a solvent or rerun that screen before plotting.",
                error_code="candidate_evidence_kind_mismatch",
                analysis_type="candidate_evidence_basis_gap",
                requested_analysis_type="precipitation_curve_visualization",
                candidate_evidence_source=candidate_source.get("source_tool"),
                candidate_evidence_shape=candidate_source.get("shape"),
                required_candidate_shape=CANDIDATE_SHAPE_PRECIPITATION,
            )
        solvent_names, error = _resolve_solvents(
            tool, [selected_solvent] if selected_solvent else [],
        )
        if error:
            return error
        start = (
            (session.temperature_min_c if session and session.temperature_min_c is not None else 25.0)
            if temperature_min_c is None else temperature_min_c
        )
        bound = (
            (decision or {}).get("dissolution_temperature_c", 160.0)
            if temperature_max_c is None else temperature_max_c
        )
        (start_value, end, step), error = _range(tool, start, bound, 5.0, bool(strict_maximum))
        if error:
            return error
        threshold = float((decision or {}).get("precipitation_threshold_wt_pct") or 1.0)
        crossings = {
            str((decision or {}).get("first_polymer")): float(decision["first_precipitation_proxy_c"]),
            str((decision or {}).get("second_polymer")): float(decision["second_precipitation_proxy_c"]),
        } if decision else {}
        records = []
        for index, polymer in enumerate(polymers):
            curve = thermo.get_solubility_curve(
                polymer, solvent_names[0], start_value, end, step,
            )
            if curve:
                records.append({
                    "polymer": polymer,
                    "label": f"{polymer} in {thermo.canonical_solvent_name(solvent_names[0])}",
                    "color": _COLORS[index % len(_COLORS)],
                    "points": [[float(item["temperature"]), float(item["solubility"])] for item in curve],
                    "solubility_methods": [str(item["method"]) for item in curve],
                })
        if not records:
            return tool_error(tool, "No precipitation curves were available.", error_code="no_data")
        plt = _pyplot()
        fig, ax = plt.subplots(figsize=(10.0, 5.5))
        maximum = 1.0
        for record in records:
            xs = [item[0] for item in record["points"]]
            ys = [item[1] for item in record["points"]]
            maximum = max(maximum, max(ys))
            ax.plot(xs, ys, label=record["label"], color=record["color"], marker="o", markersize=3)
        ax.axhline(threshold, color="#555555", linestyle="--", label=f"{threshold:g} wt% screening proxy")
        crossing_colors = {record["polymer"]: record["color"] for record in records}
        for polymer, crossing in crossings.items():
            ax.axvline(
                crossing, color=crossing_colors.get(polymer, "#555555"),
                linestyle=":", label=f"{polymer} proxy crossing · {crossing:.1f} °C",
            )
        if len(crossings) == 2:
            low, high = sorted(crossings.values())
            ax.axvspan(low, high, color="#999999", alpha=0.10, label="Ordering window")
        ax.set(xlabel="Temperature (°C)", ylabel="Modeled solubility (wt%)", ylim=(0, _nice_max(maximum)))
        title = "Cooling and precipitation screen"
        ax.set_title(title)
        # The two crossing lines can sit directly beneath a long seven-entry
        # legend at low cardinality.  Keep the data canvas unobscured by making
        # the precipitation legend an external publication panel.
        ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0)
        path = _plot_path(_slug([*polymers, solvent_names[0], "precipitation_curves"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "precipitation_curves",
            "title": title,
            "subtitle": (
                f"{thermo.canonical_solvent_name(solvent_names[0])} · "
                f"{start_value:g}–{end:g} °C · {threshold:g} wt% proxy"
            ),
            "caption": "Stored crossings are loading-dependent screening proxies, not measured cloud points or validated precipitation recovery.",
            "legend_placement": "outside_right",
            "x_label": "Temperature (°C)", "y_label": "Solubility (wt%)",
            "x_min": start_value, "x_max": end, "y_axis_max": _nice_max(maximum),
            "threshold_wt_pct": threshold, "series": records,
            "reference_lines": [
                {"axis": "y", "value": threshold, "label": f"{threshold:g} wt% proxy", "color": "#555555"},
                *(
                    {"axis": "x", "value": value, "label": f"{polymer} · {value:.3f} °C", "color": crossing_colors.get(polymer, "#555555")}
                    for polymer, value in crossings.items()
                ),
            ],
            "precipitation_crossings": crossings,
            "crossing_temperatures_c": sorted(crossings.values()) if len(crossings) == 2 else None,
            "dissolution_temperature_c": (decision or {}).get("dissolution_temperature_c"),
            "precipitation_decision_source": "stored_screen_precipitation_order" if decision else "plot_default_proxy",
        }
        plot_path, sidecar_path = _save(fig, path, sidecar)
        plt.close(fig)
        return tool_success(
            tool, display=f"Full-resolution precipitation screen: {plot_path}",
            artifact=_artifact(plot_path, sidecar_path, "Precipitation curves"),
            analysis_type="separation_plot", plot_type="precipitation_curves",
            plot_paths=[plot_path], plot_path=plot_path,
            terminal_plot_data_path=sidecar_path, polymers=polymers,
            solvents=[thermo.canonical_solvent_name(solvent_names[0])],
            temperature_min_c=start_value, temperature_max_c=end,
            requested_temperature_max_c=float(bound), strict_maximum=bool(strict_maximum),
            precipitation_threshold_wt_pct=threshold,
            precipitation_crossings=crossings,
            crossing_temperatures_c=sidecar["crossing_temperatures_c"],
            dissolution_temperature_c=sidecar["dissolution_temperature_c"],
            min_dissolution_solubility_wt_pct=(decision or {}).get("min_dissolution_solubility_wt_pct"),
            precipitation_decision_source=sidecar["precipitation_decision_source"],
            series_summaries=[{
                "polymer": record["polymer"],
                "first_solubility_pct": record["points"][0][1],
                "last_solubility_pct": record["points"][-1][1],
                "solubility_methods": list(dict.fromkeys(record["solubility_methods"])),
            } for record in records],
            warnings=[f"The {threshold:g} wt% line and stored crossings are loading-dependent screening proxies, not validated precipitation recovery."],
            model_basis="admitted temperature-dependent solubility kernel with stored precipitation-screen decision metadata",
        )

    session = current_tool_session()
    if mode == "route_windows":
        windows = list(session.route_evidence_history if session else [])
        requested_feed = set(polymers)
        windows = [
            copy.deepcopy(item) for item in windows
            if set(item.get("polymers") or []) == requested_feed
        ]
        if len(windows) < 2:
            return tool_error(
                tool,
                "Two stored route screens for the same feed are required for a window comparison.",
                error_code="missing_route_window_comparison",
            )
        windows = sorted(
            windows[-2:], key=lambda item: float(item.get("temperature_max_c") or 0.0),
        )
        route_rows: list[list[dict[str, Any]]] = []
        for window in windows:
            alternatives = list(window.get("top_k_sequences") or [])[:3]
            if not alternatives:
                alternatives = [{
                    "rank": 1,
                    "complete": window.get("complete"),
                    "sequence": window.get("best_sequence") or [],
                    "bottleneck_selectivity_pct": window.get("bottleneck_selectivity_pct"),
                    "cumulative_off_target_burden_wt_pct_sum": window.get(
                        "cumulative_off_target_burden_wt_pct_sum"
                    ),
                }]
            route_rows.append(alternatives)
        if not all(route_rows):
            return tool_error(
                tool, "Stored route windows contain no plottable alternatives.",
                error_code="route_window_comparison_empty",
            )

        plt = _pyplot()
        fig, axes = plt.subplots(
            len(windows), 1, figsize=(11.2, 3.4 + 2.3 * len(windows)),
            sharex=True,
        )
        axes_list = list(axes) if hasattr(axes, "__iter__") else [axes]
        selectivity_values = [
            abs(float(route.get("bottleneck_selectivity_pct") or 0.0))
            for routes in route_rows for route in routes
        ]
        burden_values = [
            abs(float(route.get("cumulative_off_target_burden_wt_pct_sum") or 0.0))
            for routes in route_rows for route in routes
        ]
        left_limit = max(burden_values or [1.0]) * 1.35
        right_limit = max(selectivity_values or [1.0]) * 1.22
        for index, (axis, window, routes) in enumerate(zip(axes_list, windows, route_rows)):
            positions = list(range(len(routes)))
            selectivity = [
                float(route.get("bottleneck_selectivity_pct") or 0.0) for route in routes
            ]
            burden = [
                -float(route.get("cumulative_off_target_burden_wt_pct_sum") or 0.0)
                for route in routes
            ]
            positive = axis.barh(
                [position + .18 for position in positions], selectivity, .32,
                color=_COLORS[0], label="Bottleneck selectivity",
            )
            negative = axis.barh(
                [position - .18 for position in positions], burden, .32,
                color=_COLORS[1], label="Cumulative off-target burden",
            )
            axis.axvline(0.0, color="#000000", linewidth=1)
            axis.set_yticks(
                positions,
                [" → ".join(route.get("sequence") or []) for route in routes],
            )
            axis.invert_yaxis()
            bound = float(window.get("temperature_max_c") or 0.0)
            qualifier = "Below" if window.get("strict_maximum") else "Through"
            axis.set_ylabel(f"{qualifier} {bound:g} °C")
            axis.set_xlim(-left_limit, right_limit)
            _label_horizontal_bars(axis, positive, selectivity)
            _label_horizontal_bars(axis, negative, [abs(value) for value in burden])
            if index == 0:
                axis.legend(loc="lower right")
        axes_list[-1].set_xlabel(
            "Off-target burden ← percentage points / wt%-sum → selectivity"
        )
        plot_title = "Route alternatives across operating windows"
        fig.suptitle(plot_title)
        fig.tight_layout(rect=(0, 0, 1, .95))
        path = _plot_path(
            _slug([*polymers, "route_window_comparison"]), output_dir, output_path,
        )
        sidecar_windows = [{
            "temperature_min_c": window.get("temperature_min_c"),
            "temperature_max_c": window.get("temperature_max_c"),
            "strict_maximum": bool(window.get("strict_maximum")),
            "complete": bool(window.get("complete")),
            "best_sequence": list(window.get("best_sequence") or []),
            "evidence_signature": window.get("evidence_signature"),
            "routes": routes,
        } for window, routes in zip(windows, route_rows)]
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "route_window_comparison",
            "title": plot_title, "subtitle": " / ".join(polymers),
            "caption": (
                "Positive bars are bottleneck modeled selectivity; negative bars are the "
                "summed off-target solution-concentration burden. Neither establishes "
                "recovery, purity, or a validated process route."
            ),
            "polymers": polymers, "windows": sidecar_windows,
        }
        plot_path, sidecar_path = _save(fig, path, sidecar)
        plt.close(fig)
        return tool_success(
            tool, display=f"Full-resolution route-window comparison: {plot_path}",
            artifact=_artifact(plot_path, sidecar_path, plot_title),
            analysis_type="separation_plot", plot_type="route_window_comparison",
            plot_paths=[plot_path], plot_path=plot_path,
            terminal_plot_data_path=sidecar_path, polymers=polymers,
            route_windows=sidecar_windows,
            warnings=[sidecar["caption"]],
            model_basis="two preserved typed route-search results; no route screen rerun",
        )

    stored_route = copy.deepcopy(session.last_route) if session and session.last_route else None
    if mode == "state_map" and session:
        stored_alternative = next((
            copy.deepcopy(item) for item in reversed(session.route_evidence_history)
            if set(item.get("polymers") or []) == set(polymers)
        ), None)
        if stored_alternative:
            stored_route = stored_alternative
    route_artifact_requested = mode in {
        "route", "state_map",
    }
    explicit_constraints_match = True
    if stored_route and session:
        for supplied, remembered in (
            (temperature_min_c, session.temperature_min_c),
            (temperature_max_c, session.temperature_max_c),
        ):
            if supplied is not None and (
                remembered is None or not math.isclose(float(supplied), float(remembered))
            ):
                explicit_constraints_match = False
        if strict_maximum is not None and bool(strict_maximum) != bool(session.strict_maximum):
            explicit_constraints_match = False
    use_stored_route = bool(
        route_artifact_requested
        and stored_route
        and _stored_route_matches_feed(stored_route, polymers)
        and explicit_constraints_match
    )
    if use_stored_route:
        assert stored_route is not None and session is not None
        stored_route_signature = _route_signature(stored_route)
        route = _enrich_stored_route(stored_route, polymers)
        route.update({
            "success": True,
            "temperature_min_c": (
                float(temperature_min_c) if temperature_min_c is not None
                else session.temperature_min_c
            ),
            "temperature_max_c": (
                float(temperature_max_c) if temperature_max_c is not None
                else session.temperature_max_c
            ),
            "strict_maximum": (
                bool(strict_maximum) if strict_maximum is not None
                else bool(session.strict_maximum)
            ),
            "warnings": [
                "The figure consumes the preserved route conditions and does not rerun route selection."
            ],
            "model_basis": "preserved typed route; selectivity regenerated only at its fixed stage conditions",
        })
        route_source = "typed_session_state"
    else:
        resolved_strict = bool(strict_maximum) if strict_maximum is not None else False
        route = parse_tool_result(plan_multistage_separation(
            polymers, temperature_min_c=temperature_min_c,
            temperature_max_c=temperature_max_c, strict_maximum=resolved_strict,
        ))["data"]
        if route.get("success") is not True:
            return tool_error(
                tool, "The separation route could not be reproduced.", error_code="route_failed",
            )
        stored_route_signature = None
        route_source = "regenerated_screen"
    plt = _pyplot()
    if mode == "atmospheric_feasibility":
        screen = parse_tool_result(screen_polymer_separation(
            polymers, temperature_min_c=temperature_min_c,
            temperature_max_c=temperature_max_c,
            strict_maximum=bool(strict_maximum) if strict_maximum is not None else False,
        ))["data"]
        rows = list(screen.get("ranked_candidates") or [])[:6]
        if not rows:
            return tool_error(tool, "No atmospheric candidates were available.", error_code="no_data")
        fig, ax = plt.subplots(figsize=(max(7.5, 1.25 * len(rows) + 3), 5.0))
        positions = list(range(len(rows)))
        ax.bar([item - .18 for item in positions], [float(row["temperature_c"]) for row in rows], .36, label="Operating T", color=_COLORS[0])
        ax.bar([item + .18 for item in positions], [float(row["boiling_point_c"]) for row in rows], .36, label="Normal BP", color=_COLORS[1])
        _set_categorical_x_labels(fig, ax, positions, [str(row["solvent"]) for row in rows])
        ax.set_ylabel("Temperature (°C)")
        ax.set_title("Atmospheric-operability screen")
        ax.legend()
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "atmospheric_feasibility",
            "title": "Atmospheric operability",
            "subtitle": " / ".join(polymers),
            "caption": "Normal-boiling-point margin is an atmospheric operability check, not a complete safety assessment.",
            "polymers": polymers,
            "candidates": [{key: row.get(key) for key in (
                "solvent", "dissolved_polymer", "temperature_c", "boiling_point_c",
                "boiling_point_margin_c", "selectivity_pct",
            )} for row in rows],
        }
        title = "Atmospheric feasibility"
    elif mode == "state_map":
        routes = list(route.get("top_k_sequences") or [])[:5]
        fig, ax = plt.subplots(figsize=(9.0, max(4.0, .8 * len(routes) + 2)))
        positions = list(range(len(routes)))
        values = [float(item.get("bottleneck_selectivity_pct") or 0.0) for item in routes]
        ax.barh(positions, values, color=[_COLORS[index % len(_COLORS)] for index in positions])
        ax.set_yticks(positions, [" → ".join(item.get("sequence") or []) for item in routes])
        ax.invert_yaxis()
        ax.set_xlabel("Bottleneck selectivity (percentage points)")
        ax.set_title("Top recursive separation states/routes")
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "separation_state_map",
            "title": "Recursive separation alternatives",
            "subtitle": " / ".join(polymers),
            "caption": "Bottleneck selectivity is model-screened solution-concentration separation, not demonstrated feed recovery or purity.",
            "polymers": polymers, "routes": [{
                "rank": item.get("rank"), "sequence": item.get("sequence"),
                "complete": item.get("complete"),
                "bottleneck_selectivity_pct": item.get("bottleneck_selectivity_pct"),
            } for item in routes],
        }
        title = "Separation state map"
    else:
        from matplotlib.patches import Rectangle

        steps = list(route.get("steps") or [])
        node_count = len(steps) + 2
        columns = 3 if node_count == 3 or node_count >= 5 else 2
        row_count = math.ceil(node_count / columns)
        canvas_width = 10.4 if columns == 3 else 8.0
        canvas_height = {1: 3.8, 2: 6.2}.get(row_count, 2.0 + 1.65 * row_count)
        fig, ax = plt.subplots(figsize=(canvas_width, canvas_height))
        ax.set_axis_off()
        nodes: list[dict[str, Any]] = [{
            "heading": "Feed",
            "body": "\n".join(
                " / ".join(polymers[index:index + 4])
                for index in range(0, len(polymers), 4)
            ),
            "step": None,
        }]
        nodes.extend({
            "heading": f"Stage {index}",
            "body": (
                f"Dissolve {item['dissolved_polymer']}\n"
                f"{item['solvent']}\n{item['temperature_c']:g} °C"
            ),
            "step": item,
        } for index, item in enumerate(steps, 1))
        terminal = route.get("final_residue") or "Unresolved: " + ", ".join(
            route.get("unresolved_polymers") or []
        )
        nodes.append({
            "heading": "Final residue" if route.get("complete") else "Partial route",
            "body": str(terminal), "step": None,
        })
        positions: list[tuple[float, float]] = []
        x_values = (
            [.22, .78] if columns == 2 else
            [.14, .50, .86] if columns == 3 else [.5]
        )
        y_values = (
            [.5] if row_count == 1 else [.70, .30] if row_count == 2 else
            [.82 - index * (.64 / (row_count - 1)) for index in range(row_count)]
        )
        for index in range(node_count):
            row, offset = divmod(index, columns)
            column = offset if row % 2 == 0 else columns - 1 - offset
            positions.append((x_values[column], y_values[row]))

        ax.set_title("Multistage separation route")
        text_artists: list[Any] = []
        selectivities: list[float | None] = []
        for index, (node, (x, y)) in enumerate(zip(nodes, positions)):
            heading = str(node["heading"]).replace(" ", r"\ ")
            selectivity = _route_stage_score(_finite((node.get("step") or {}).get("selectivity_pct")))
            lines = [rf"$\mathbf{{{heading}}}$", str(node["body"])]
            if selectivity is not None:
                lines.append(f"Stage selectivity score\n{_format_value(selectivity)} / 100")
            artist = ax.text(
                x, y, "\n".join(lines), ha="center", va="center",
                linespacing=1.25, transform=ax.transAxes, zorder=3,
                bbox={
                    "boxstyle": "round,pad=0.58", "facecolor": "#F4F8FA",
                    "edgecolor": _COLORS[index % len(_COLORS)], "linewidth": 1.5,
                },
            )
            text_artists.append(artist)
            selectivities.append(selectivity)

        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        inverse = ax.transAxes.inverted()
        node_bounds: list[tuple[float, float, float, float]] = []
        for score_index, (artist, selectivity) in enumerate(zip(text_artists, selectivities)):
            patch = artist.get_bbox_patch()
            box = patch.get_window_extent(renderer) if patch is not None else artist.get_window_extent(renderer)
            lower = inverse.transform((box.x0, box.y0))
            upper = inverse.transform((box.x1, box.y1))
            bar_bottom = float(lower[1])
            if selectivity is not None:
                bar_width = min(.17, max(.10, float(upper[0] - lower[0]) * .78))
                bar_left = (float(lower[0]) + float(upper[0]) - bar_width) / 2
                bar_bottom = float(lower[1]) - .032
                ax.add_patch(Rectangle(
                    (bar_left, bar_bottom), bar_width, .013,
                    facecolor="#E6E6E6", edgecolor="none",
                    transform=ax.transAxes, zorder=2,
                ))
                ax.add_patch(Rectangle(
                    (bar_left, bar_bottom),
                    bar_width * min(max(selectivity, 0.0), 100.0) / 100.0,
                    .013, facecolor=_COLORS[2], edgecolor="none",
                    transform=ax.transAxes, zorder=2, label=("Stage selectivity score (0–100)" if score_index == 1 else "_nolegend_"),
                ))
            node_bounds.append((
                float(lower[0]), min(float(lower[1]), bar_bottom),
                float(upper[0]), float(upper[1]),
            ))

        ax.legend(loc="lower center", bbox_to_anchor=(.5, .015))
        for start_position, end_position, start_box, end_box in zip(
            positions, positions[1:], node_bounds, node_bounds[1:],
        ):
            if abs(start_position[1] - end_position[1]) < 1e-9:
                if end_position[0] > start_position[0]:
                    arrow_start = (start_box[2] + .012, start_position[1])
                    arrow_end = (end_box[0] - .012, end_position[1])
                else:
                    arrow_start = (start_box[0] - .012, start_position[1])
                    arrow_end = (end_box[2] + .012, end_position[1])
            else:
                arrow_start = (start_position[0], start_box[1] - .012)
                arrow_end = (end_position[0], end_box[3] + .012)
            ax.annotate(
                "", xy=arrow_end, xytext=arrow_start, xycoords=ax.transAxes,
                arrowprops={"arrowstyle": "-|>", "color": "#000000"},
                zorder=1,
            )
        route_identity = {
            "complete": bool(route.get("complete")),
            "best_sequence": list(route.get("best_sequence") or []),
            "steps": _route_conditions(route),
            "final_residue": route.get("final_residue"),
            "unresolved_polymers": list(route.get("unresolved_polymers") or []),
        }
        plotted_route_signature = stored_route_signature or _route_signature(route_identity)
        stored_route_match = (
            _route_conditions(stored_route or {}) == _route_conditions(route)
            if route_source == "typed_session_state" else None
        )
        if route_source == "typed_session_state" and stored_route_match is not True:
            plt.close(fig)
            return tool_error(
                tool, "The route figure diverged from typed session state.",
                error_code="route_artifact_state_mismatch",
            )
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "separation_route",
            "title": "Multistage separation route",
            "subtitle": " / ".join(polymers),
            "caption": "Each stage is a model-screened dissolution condition; recovery, precipitation, solvent removal, and product purity remain unvalidated.",
            "requested_view": requested_mode,
            "polymers": polymers, "complete": route.get("complete"), "steps": steps,
            "final_residue": route.get("final_residue"), "unresolved_polymers": route.get("unresolved_polymers") or [],
            "route_source": route_source, "route_signature": plotted_route_signature,
            "route_presentation": _route_presentation(),
            "stored_route_match": stored_route_match, "stage_scores": _route_stage_scores(steps),
        }
        title = "Multistage separation route"
    path = _plot_path(_slug([*polymers, requested_mode]), output_dir, output_path)
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool, display=f"Full-resolution {mode.replace('_', ' ')} plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, title),
        analysis_type="separation_plot", plot_type=sidecar["plot_type"],
        plot_paths=[plot_path], plot_path=plot_path, terminal_plot_data_path=sidecar_path,
        polymers=polymers, temperature_min_c=route.get("temperature_min_c"),
        temperature_max_c=route.get("temperature_max_c"),
        strict_maximum=bool(route.get("strict_maximum")),
        route_source=route_source,
        route_signature=(sidecar.get("route_signature") if mode == "route" else None),
        stored_route_match=(sidecar.get("stored_route_match") if mode == "route" else None),
        complete=route.get("complete"), best_sequence=route.get("best_sequence"),
        steps=route.get("steps") or [], final_residue=route.get("final_residue"),
        unresolved_polymers=route.get("unresolved_polymers") or [],
        candidate_conditions=sidecar.get("candidates"),
        warnings=route.get("warnings") or [], model_basis=route.get("model_basis"),
    )


def plot_comparison_results(
    result_kind: Literal[
        "safety", "contaminant_modes", "tea", "substitution",
        "optimization", "scale_pareto",
    ],
    solvents: Optional[list[str]] = None,
    operating_temperatures_c: Optional[list[float]] = None,
    target_polymer: Optional[str] = None,
    contaminants: Optional[list[str]] = None,
    max_temperature_c: Optional[float] = None,
    metric: Optional[str] = None,
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Redraw an already computed safety, contaminant, TEA, or optimization result."""
    tool = "plot_comparison_results"
    kind = str(result_kind or "").strip().casefold()
    labels: list[str]
    values: list[float]
    y_label: str
    evidence: dict[str, Any]
    optimization_plot: dict[str, Any] | None = None
    safety_plot: dict[str, Any] | None = None
    tea_plot: dict[str, Any] | None = None
    substitution_plot: dict[str, Any] | None = None
    composite_plot: dict[str, Any] | None = None
    if kind == "safety":
        from .safety import compare_solvent_safety_at_conditions

        names = _items(solvents)
        temperatures = list(operating_temperatures_c or [])
        if not names or len(names) != len(temperatures):
            return tool_error(tool, "Safety plots require one setpoint per solvent.", error_code="candidate_setpoint_mismatch")
        candidates = [{"solvent_name": name, "operating_temp_c": temperature} for name, temperature in zip(names, temperatures)]

        def safety_key(name: Any, temperature: Any) -> tuple[str, float | None]:
            resolved = thermo.resolve_solvent(str(name))
            return (resolved or str(name).strip().casefold(), _finite(temperature))

        session = current_tool_session()
        sources: list[list[dict[str, Any]]] = []
        if session and session.last_result:
            try:
                prior = json.loads(session.last_result)
                facts = prior.get("relevant_facts") or prior
                sources.append(list(facts.get("comparison_rows") or []))
            except (TypeError, ValueError):
                pass
        if session:
            sources.append(list(session.last_safety or []))
        expected = [safety_key(item["solvent_name"], item["operating_temp_c"]) for item in candidates]
        rows = []
        best_completeness = -1
        for source in sources:
            indexed = {
                safety_key(item.get("solvent"), item.get("operating_temp_c")): item
                for item in source
            }
            if all(key in indexed for key in expected):
                candidate_rows = [dict(indexed[key]) for key in expected]
                completeness = sum(
                    int(item.get("flash_point_c") is not None)
                    + int(bool(item.get("ghs_signal_word")))
                    + int(bool(item.get("occupational_exposure_limits")))
                    for item in candidate_rows
                )
                if completeness > best_completeness:
                    rows = candidate_rows
                    best_completeness = completeness
        if not rows:
            result = parse_tool_result(compare_solvent_safety_at_conditions(
                candidates, include_pubchem=False,
            ))["data"]
            if result.get("success") is not True:
                return tool_error(tool, "The safety comparison could not be reproduced.", error_code="comparison_failed")
            rows = list(result.get("comparison_rows") or [])
        for row in rows:
            if row.get("boiling_margin_c") is None:
                boiling, operating = _finite(row.get("boiling_point_c")), _finite(row.get("operating_temp_c"))
                row["boiling_margin_c"] = None if boiling is None or operating is None else boiling - operating
            flash, operating = _finite(row.get("flash_point_c")), _finite(row.get("operating_temp_c"))
            row["flash_margin_c"] = None if flash is None or operating is None else flash - operating
        labels = [str(item["solvent"]) for item in rows]
        values = [float(item.get("boiling_margin_c") or 0.0) for item in rows]
        y_label = "Normal-BP margin (°C)"
        safety_plot = {"rows": rows}
        evidence = {
            "comparison_mode": "safety", "comparison_rows": rows,
            "candidate_conditions": candidates, "solvents": labels,
        }
    elif kind in {"contaminant", "contaminant_modes"}:
        from .contaminants import compare_contaminant_removal_modes

        if not target_polymer or not contaminants:
            return tool_error(tool, "Contaminant plots require a target polymer and contaminant family/compound.", error_code="missing_contaminant_scope")
        result = parse_tool_result(compare_contaminant_removal_modes(
            target_polymer, contaminants, max_temperature_c=max_temperature_c,
        ))["data"]
        if result.get("success") is not True:
            return tool_error(
                tool,
                "No quantitative contaminant-mode figure was generated because the requested contaminant evidence is unavailable.",
                error_code="artifact_basis_gap",
                source_error_code=result.get("error_code"),
                target_polymer=result.get("target_polymer") or target_polymer,
                requested_contaminants=(
                    result.get("requested_contaminants") or list(contaminants)
                ),
                unsupported_contaminants=result.get("unsupported_contaminants") or [],
                supported_families=result.get("supported_families") or [],
                plot_generated=False,
                warnings=list(result.get("warnings") or []),
            )
        summaries = result.get("mode_summaries") or {}
        compact_summaries = {
            mode: {
                "passing_count": int((summary or {}).get("passing_count") or 0),
                "recommended_solvents": list((summary or {}).get("recommended_solvents") or [])[:5],
            }
            for mode, summary in summaries.items()
        }
        labels = ["Leaching", "STRAP temperature swing"]
        values = [float((summaries.get(key) or {}).get("passing_count") or 0) for key in ("leaching", "strap_contaminant_removal")]
        y_label = "Passing solvent candidates"
        evidence = {
            "comparison_mode": "contaminant", "target_polymer": result.get("target_polymer"),
            "requested_contaminants": result.get("requested_contaminants"),
            "recommended_mode": result.get("recommended_mode"), "mode_summaries": compact_summaries,
            "temperature_max_c": result.get("temperature_max_c"),
        }
    elif kind in {"tea", "tea_lca"}:
        state = current_tool_session()
        current = getattr(state, "last_tea", None) if state else None
        if not current:
            return tool_error(
                tool, "No admitted TEA/LCA result is available in typed session state.",
                error_code="missing_tea_result",
            )
        rows = list(current.get("comparison_rows") or [])
        metric_aliases = {
            "msp": "msp_usd_per_kg", "msp_usd_per_kg": "msp_usd_per_kg",
            "tci": "tci_usd", "tci_usd": "tci_usd",
            "aoc": "aoc_usd_per_yr", "aoc_usd_per_yr": "aoc_usd_per_yr",
            "gwp": "gwp_kg_co2e_per_kg", "gwp_kg_co2e_per_kg": "gwp_kg_co2e_per_kg",
            "energy": "total_energy_mj_per_kg", "total_energy_mj_per_kg": "total_energy_mj_per_kg",
        }
        requested_metric = str(metric or "msp").casefold().replace("-", "_").replace(" ", "_")
        selected = metric_aliases.get(requested_metric)
        special = requested_metric if requested_metric in {
            "stage_economics", "gwp_attribution", "scale_curves", "scale",
        } else None
        if selected is None and special is None:
            return tool_error(
                tool, "Unsupported TEA/LCA plot metric.", error_code="unsupported_tea_metric",
                supported_metrics=sorted([*metric_aliases, "stage_economics", "gwp_attribution", "scale_curves"]),
            )
        if special in {"scale", "scale_curves"} and getattr(
            state, "tea_scale_evidence", None
        ):
            if (
                state.tea_scale_evidence.get("route_signature")
                != route_evidence_signature(getattr(state, "last_route", None))
            ):
                return tool_error(
                    tool,
                    "The stored scale evidence no longer matches the current route.",
                    error_code="stale_scale_route_basis",
                )
            current = state.tea_scale_evidence
        if special == "stage_economics":
            available = [item for item in rows if item.get("msp_usd_per_kg") is not None and item.get("tci_usd") is not None]
            values = [float(item["msp_usd_per_kg"]) for item in available]
            y_label = "Stage economics"
        elif special == "gwp_attribution":
            available = [item for item in rows if item.get("modeled_stage_gwp_contribution_fraction") is not None]
            values = [100.0 * float(item["modeled_stage_gwp_contribution_fraction"]) for item in available]
            y_label = "Modeled dissolution-stage GWP contribution (%)"
        elif special in {"scale", "scale_curves"}:
            available = sorted([
                item for item in list(current.get("scale_comparison_rows") or [])
                if item.get("processing_capacity_mt_per_yr") is not None
                and item.get("msp_usd_per_kg") is not None
                and item.get("gwp_t_co2e_per_mt") is not None
            ], key=lambda item: float(item["processing_capacity_mt_per_yr"]))
            values = [float(item["msp_usd_per_kg"]) for item in available]
            y_label = "Scale comparison"
            special = "scale_curves"
        else:
            assert selected is not None
            available = [item for item in rows if item.get(selected) is not None]
            if not available and all(item.get("metric_value") is not None for item in rows):
                available = rows
                selected = "metric_value"
            values = [float(item[selected]) for item in available]
            units = {
                "msp_usd_per_kg": "MSP (USD/kg product)", "tci_usd": "TCI (USD)",
                "aoc_usd_per_yr": "AOC (USD/yr)",
                "gwp_kg_co2e_per_kg": "GWP (kg CO2e/kg product)",
                "total_energy_mj_per_kg": "Total energy (MJ/kg product)",
                "metric_value": str((available[0].get("metric") or "Sensitivity metric")),
            }
            y_label = units[selected]
        if not available or not values or (special == "scale_curves" and len(available) < 2):
            return tool_error(
                tool, f"The current TEA/LCA result has no complete {special or selected} basis.",
                error_code="tea_metric_unavailable",
            )
        labels = [
            str(item.get("label") or item.get("processing_capacity_mt_per_yr") or f"row-{index}")
            for index, item in enumerate(available, 1)
        ]
        if special:
            tea_plot = {"mode": special, "rows": available}
        evidence = {
            "comparison_mode": "tea_lca", "engine_mode": current.get("engine_mode"),
            "route_source": current.get("route_source"),
            "route_signature": current.get("route_signature"),
            "energy_case": current.get("energy_case"),
            "processing_capacity_mt_per_yr": current.get("processing_capacity_mt_per_yr"),
            "comparison_rows": available, "metric": special or selected,
        }
    elif kind in {"substitution", "solvent_substitution"}:
        session = current_tool_session()
        try:
            prior = json.loads(session.last_result or "{}") if session else {}
        except (TypeError, ValueError):
            prior = {}
        facts = prior.get("relevant_facts") or prior
        rows = list(facts.get("candidate_substitutions") or [])
        if facts.get("tool_name") != "screen_route_solvent_substitutions" or not rows:
            return tool_error(
                tool, "No quantitative route-solvent substitution result is available in typed session state.",
                error_code="missing_substitution_result",
            )
        rows = [
            item for item in rows
            if item.get("g_score_change") is not None
            and item.get("selectivity_loss_points") is not None
        ]
        if not rows:
            return tool_error(
                tool, "The current substitution result has no plottable tradeoff basis.",
                error_code="substitution_basis_gap",
            )
        labels = [str(item["solvent"]) for item in rows]
        values = [float(item["selectivity_loss_points"]) for item in rows]
        y_label = "Selectivity loss (percentage points)"
        substitution_plot = {
            "rows": rows, "recommended": facts.get("recommended_substitution"),
            "worst_stage": facts.get("worst_stage"),
        }
        evidence = {
            "comparison_mode": "solvent_substitution", "candidate_substitutions": rows,
            "recommended_substitution": facts.get("recommended_substitution"),
            "worst_stage": facts.get("worst_stage"),
        }
    elif kind in {"scale_pareto", "scale_and_pareto"}:
        state = current_tool_session()
        current_tea = getattr(state, "last_tea", None) if state else None
        scale_evidence = getattr(state, "tea_scale_evidence", None) if state else None
        if (
            scale_evidence
            and scale_evidence.get("route_signature")
            != route_evidence_signature(getattr(state, "last_route", None))
        ):
            return tool_error(
                tool,
                "The stored scale evidence no longer matches the current route.",
                error_code="stale_scale_route_basis",
            )
        scale_rows = sorted([
            item for item in list((scale_evidence or {}).get("scale_comparison_rows") or [])
            if item.get("processing_capacity_mt_per_yr") is not None
            and item.get("msp_usd_per_kg") is not None
            and item.get("gwp_t_co2e_per_mt") is not None
        ], key=lambda item: float(item["processing_capacity_mt_per_yr"]))
        if len(scale_rows) < 2:
            return tool_error(
                tool, "A stored multi-point TEA scale result is required for this composite.",
                error_code="missing_scale_result",
            )
        pareto, pareto_error = _stored_pareto_evidence(tool, state)
        if pareto_error:
            return pareto_error
        assert pareto is not None and current_tea is not None and scale_evidence is not None
        if (
            current_tea.get("route_signature") != pareto.get("source_route_signature")
            or scale_evidence.get("route_signature") != pareto.get("source_route_signature")
        ):
            return tool_error(
                tool, "The stored scale and Pareto results describe different routes.",
                error_code="cross_artifact_route_mismatch",
            )
        composite_plot = {"scale_rows": scale_rows, "pareto": pareto}
        labels = [str(item["processing_capacity_mt_per_yr"]) for item in scale_rows]
        values = [float(item["msp_usd_per_kg"]) for item in scale_rows]
        y_label = "Scale and route frontier"
        evidence = {
            "comparison_mode": "scale_pareto",
            "route_signature": scale_evidence.get("route_signature"),
            "scale_rows": scale_rows,
            "source_route_signature": pareto.get("source_route_signature"),
            "n_landscape_points": len(pareto["landscape"]),
            "n_frontier_points": len(pareto["frontier"]),
            "knee_status": pareto.get("knee_status"),
        }
    elif kind in {"optimization", "pareto"}:
        state = current_tool_session()
        pareto, pareto_error = _stored_pareto_evidence(tool, state)
        if pareto_error:
            return pareto_error
        assert pareto is not None
        landscape, frontier = pareto["landscape"], pareto["frontier"]
        x_metric, y_metric = pareto["x_metric"], pareto["y_metric"]
        labels = [str(point["design_id"]) for point in frontier]
        values = [float(point[y_metric]) for point in frontier]
        y_label = y_metric.replace("_", " ").title()

        def summary(point: Any) -> Any:
            if not isinstance(point, dict):
                return point
            return {
                key: point.get(key) for key in (
                    "design_id", "active_recovery_stages", "residual_technology",
                    x_metric, y_metric,
                ) if point.get(key) is not None
            }

        optimization_plot = pareto
        evidence = {
            "comparison_mode": "optimization",
            "x_metric": x_metric, "y_metric": y_metric,
            "source_route_signature": pareto.get("source_route_signature"),
            "n_landscape_points": len(landscape), "n_frontier_points": len(frontier),
            "knee_point": summary(pareto.get("knee_point")),
            "knee_status": pareto.get("knee_status"),
            "cheapest_point": summary(pareto.get("cheapest_point")),
            "pareto_payload_path": pareto["payload_path"],
        }
    else:
        message = (
            "result_kind must be safety, contaminant_modes, tea, "
            "substitution, optimization, or scale_pareto."
        )
        if kind == "safety_card":
            message += (
                " Use plot_analysis_results view=safety_card for a "
                "single-solvent card; use plot_comparison_results "
                "result_kind=safety for a comparison."
            )
        return tool_error(
            tool, message,
            error_code="unsupported_result_kind",
            supported_result_kinds=["safety", "contaminant_modes", "tea", "substitution", "optimization", "scale_pareto"],
        )
    plt = _pyplot()
    if composite_plot:
        scale_rows = composite_plot["scale_rows"]
        pareto = composite_plot["pareto"]
        fig, (scale_axis, pareto_axis) = plt.subplots(1, 2, figsize=(14.2, 5.6))
        _, scale_handles = _draw_scale_evidence(scale_axis, scale_rows, annotate=False)
        scale_axis.legend(
            scale_handles, [item.get_label() for item in scale_handles],
            loc="best", ncols=2,
        )
        pareto_axes = _draw_pareto_evidence(pareto_axis, pareto)
        if pareto.get("knee_status") != "interior_tradeoff":
            pareto_axis.text(
                .02, .97, "No interior knee\nEndpoint tradeoff only",
                transform=pareto_axis.transAxes, ha="left", va="top",
                bbox={"facecolor": "white", "alpha": .85, "edgecolor": "none"},
            )
        plot_title = "Scale response and route frontier"
        fig.suptitle(plot_title)
        fig.tight_layout(rect=(0, 0, 1, .95))
        path = _plot_path("route_scale_and_pareto", output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "scale_pareto_composite",
            "title": plot_title,
            "subtitle": "Common preserved route basis",
            "caption": (
                "Left: circles are exact cached simulations and squares are cache-derived "
                "screening estimates, connected only to show the modeled scale trend; no "
                "uncertainty band is available. Right: the stored feasible landscape and "
                "non-dominated frontier add no new optimization run."
            ),
            "result_kind": "scale_pareto",
            "route_signature": evidence["route_signature"],
            "scale_rows": scale_rows,
            "provenance_marker_key": {
                "circle": "exact_cached_simulation",
                "square": "cache_derived_screening_estimate",
            },
            "uncertainty_band_available": False,
            "pareto": {
                "x_metric": pareto["x_metric"], "y_metric": pareto["y_metric"],
                "x_axis_label": pareto_axes["x_axis_label"],
                "y_axis_label": pareto_axes["y_axis_label"],
                "x_canvas_scale_divisor": pareto_axes["x_scale"],
                "y_canvas_scale_divisor": pareto_axes["y_scale"],
                "landscape_points": pareto["landscape"],
                "frontier_points": pareto["frontier"],
                "knee_status": pareto.get("knee_status"),
                "knee_point": (
                    pareto.get("knee_point")
                    if pareto.get("knee_status") == "interior_tradeoff" else None
                ),
                "endpoint_reference_point": (
                    pareto.get("knee_point")
                    if pareto.get("knee_status") != "interior_tradeoff" else None
                ),
                "knee_marker_rendered": pareto.get("knee_status") == "interior_tradeoff",
                "cheapest_point": pareto.get("cheapest_point"),
                "frontier_tradeoff": pareto.get("frontier_tradeoff"),
            },
            "methodology_notes": [
                "TEA scale rows and optimization points share the exact route signature.",
                "Screening estimates are not promoted to exact cached simulations.",
                "The absence of an interior knee is shown rather than replaced by an endpoint.",
            ],
        }
        plot_type = "scale_pareto_composite"
        title = plot_title
    elif optimization_plot:
        tradeoff = optimization_plot.get("frontier_tradeoff") or {}
        if tradeoff:
            fig, (ax, summary_axis) = plt.subplots(
                1, 2, figsize=(10.8, 5.2), gridspec_kw={"width_ratios": [2.3, 1]},
            )
        else:
            fig, ax = plt.subplots(figsize=(7.2, 5.2))
            summary_axis = None
        landscape = optimization_plot["landscape"]
        frontier = optimization_plot["frontier"]
        x_metric, y_metric = optimization_plot["x_metric"], optimization_plot["y_metric"]
        pareto_axes = _draw_pareto_evidence(ax, optimization_plot)
        x_scale, x_axis_label = pareto_axes["x_scale"], pareto_axes["x_axis_label"]
        y_scale, y_axis_label = pareto_axes["y_scale"], pareto_axes["y_axis_label"]
        plot_title = "Route optimization frontier"
        if summary_axis is None:
            ax.set_title(plot_title)
        else:
            fig.suptitle(plot_title)
            summary_axis.set_axis_off()
            knee_note = (
                "\n\nNo interior knee\nEndpoint tradeoff only"
                if optimization_plot.get("knee_status") != "interior_tradeoff" else ""
            )
            summary_axis.text(
                0.0, .72,
                "Tradeoff from cheapest design\n\n"
                f"Annual cost increment\n${_format_value(float(tradeoff['incremental_annual_cost_usd']) / 1_000_000)} million/yr\n\n"
                f"Annual emissions reduction\n{float(tradeoff['annual_emissions_reduction_t_co2e']):,.1f} t CO₂e/yr\n\n"
                f"Cost per tonne avoided\n${float(tradeoff['incremental_cost_usd_per_t_co2e_avoided']):,.1f}/t CO₂e"
                + knee_note,
                ha="left", va="center", transform=summary_axis.transAxes,
            )
        fig.tight_layout(rect=(0, 0, 1, .94) if summary_axis is not None else None)
        path = _plot_path("route_optimization_frontier", output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "optimization_frontier",
            "title": plot_title,
            "subtitle": f"{x_metric.replace('_', ' ')} versus {y_metric.replace('_', ' ')}",
            "caption": "Points reproduce the stored route-constrained feasible landscape and non-dominated frontier; they add no new optimization run.",
            "result_kind": "optimization", "x_metric": x_metric, "y_metric": y_metric,
            "x_axis_label": x_axis_label, "y_axis_label": y_axis_label,
            "x_canvas_scale_divisor": x_scale, "y_canvas_scale_divisor": y_scale,
            "landscape_points": [{
                "design_id": point["design_id"], x_metric: point[x_metric], y_metric: point[y_metric],
            } for point in landscape],
            "points": [{
                "design_id": point["design_id"], x_metric: point[x_metric], y_metric: point[y_metric],
            } for point in frontier],
            "knee_point": evidence["knee_point"], "cheapest_point": evidence["cheapest_point"],
            "knee_status": evidence["knee_status"],
            "knee_marker_rendered": evidence["knee_status"] == "interior_tradeoff",
            "feed_mass_fractions": optimization_plot["feed_mass_fractions"],
            "source_route_signature": evidence["source_route_signature"],
            "frontier_tradeoff": tradeoff or None,
        }
        plot_type = "optimization_frontier"
        title = "Route-constrained optimization frontier"
    elif tea_plot:
        mode, rows = tea_plot["mode"], tea_plot["rows"]
        if mode == "stage_economics":
            fig, (left, right) = plt.subplots(1, 2, figsize=(11.0, 5.2))
            stage_labels = [str(item.get("label") or f"Stage {item.get('stage')}") for item in rows]
            msp = [float(item["msp_usd_per_kg"]) for item in rows]
            tci_millions = [float(item["tci_usd"]) / 1_000_000 for item in rows]
            left_bars = left.bar(stage_labels, msp, color=_COLORS[0])
            right_bars = right.bar(stage_labels, tci_millions, color=_COLORS[1])
            left.set_ylabel("MSP (USD/kg product)")
            right.set_ylabel("TCI (million USD)")
            _label_bars(left, left_bars)
            _label_bars(right, right_bars)
            plot_title = "Stage economics"
            fig.suptitle(plot_title)
            fig.tight_layout(rect=(0, 0, 1, .94))
            plot_type = "tea_stage_economics"
            caption = "Stage MSP and TCI reproduce the stored route-specific screening estimate; they are not an integrated-plant cash flow."
        elif mode == "gwp_attribution":
            fig, ax = plt.subplots(figsize=(8.4, max(4.5, .7 * len(rows) + 2.2)))
            stage_labels = [str(item.get("label") or f"Stage {item.get('stage')}") for item in rows]
            contributions = [100.0 * float(item["modeled_stage_gwp_contribution_fraction"]) for item in rows]
            bars = ax.barh(stage_labels, contributions, color=_COLORS[2])
            ax.invert_yaxis()
            ax.set_xlabel("Modeled dissolution-stage GWP contribution (%)")
            _label_horizontal_bars(ax, bars, contributions)
            plot_title = "Dissolution-stage GWP attribution"
            ax.set_title(plot_title)
            plot_type = "tea_gwp_attribution"
            caption = "Contributions use the stored dissolution-stage system boundary and do not represent a full product life cycle."
        else:
            fig, ax = plt.subplots(figsize=(8.8, 5.2))
            _, handles = _draw_scale_evidence(ax, rows, annotate=True)
            ax.legend(
                handles, [item.get_label() for item in handles],
                loc="upper center", ncol=2,
            )
            plot_title = "Scale-dependent economics and emissions"
            ax.set_title(plot_title)
            fig.tight_layout()
            plot_type = "tea_scale_curves"
            caption = "Curves reproduce stored screening-scale analogs; extrapolation flags and the route system boundary remain controlling limitations."
        path = _plot_path(_slug([plot_type]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": plot_type,
            "title": plot_title, "subtitle": str(current.get("energy_case") or "Stored energy case"),
            "caption": caption, "result_kind": "tea", "rows": rows,
            "route_signature": current.get("route_signature"),
            **({
                "provenance_marker_key": {
                    "circle": "exact_cached_simulation",
                    "square": "cache_derived_screening_estimate",
                },
            } if mode == "scale_curves" else {}),
        }
        title = plot_title
    elif substitution_plot:
        rows = substitution_plot["rows"]
        fig, ax = plt.subplots(figsize=(8.2, 5.2))
        x_values = [float(item["g_score_change"]) for item in rows]
        y_values = [float(item["selectivity_loss_points"]) for item in rows]
        ax.scatter(x_values, y_values, s=90, color=_COLORS[0])
        recommended = substitution_plot.get("recommended") or {}
        current = substitution_plot.get("worst_stage") or {}
        current_solvent = str(current.get("solvent") or "Current solvent")
        ax.scatter([0.0], [0.0], s=100, marker="X", color="#000000")
        ax.annotate(
            f"Current · {current_solvent}", (0.0, 0.0),
            xytext=(6, 6), textcoords="offset points", ha="left",
        )
        for item, x_value, y_value in zip(rows, x_values, y_values):
            marker = " ★" if item.get("solvent") == recommended.get("solvent") else ""
            at_right_edge = x_value == max(x_values)
            ax.annotate(
                f"{item.get('solvent')}{marker}", (x_value, y_value),
                xytext=((-5 if at_right_edge else 5), 5), textcoords="offset points",
                ha="right" if at_right_edge else "left",
            )
        ax.margins(x=.12, y=.15)
        ax.axvline(0.0, color="#000000", linewidth=1)
        ax.axhline(0.0, color="#000000", linewidth=1)
        ax.set_xlabel("G-score change (positive is greener)")
        ax.set_ylabel("Selectivity loss (percentage points)")
        plot_title = "Solvent-substitution tradeoff"
        ax.set_title(plot_title)
        fig.tight_layout()
        path = _plot_path("route_solvent_substitution_tradeoff", output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "substitution_tradeoff",
            "title": plot_title,
            "subtitle": "G-score improvement versus modeled selectivity loss",
            "caption": "The chart compares stored screening proxies only; G-score is not a process LCA and selectivity is not recovery or purity.",
            "result_kind": "substitution", "candidate_substitutions": rows,
            "current_reference": {
                "solvent": current_solvent, "g_score_change": 0.0,
                "selectivity_loss_points": 0.0,
            },
            "recommended_substitution": recommended,
            "worst_stage": substitution_plot.get("worst_stage"),
        }
        plot_type = "substitution_tradeoff"
        title = plot_title
    elif safety_plot:
        rows = safety_plot["rows"]
        positions = list(range(len(rows)))
        names = [str(row["solvent"]) for row in rows]
        boiling_values = [_finite(row.get("boiling_margin_c")) for row in rows]
        flash_values = [_finite(row.get("flash_margin_c")) for row in rows]
        signal_rank = {"Warning": 1.0, "Danger": 2.0}
        signal_values = [
            signal_rank.get(str(row.get("ghs_signal_word") or ""), 0.0) for row in rows
        ]
        available_panels = ["boiling_margin_c"]
        if any(value is not None for value in flash_values):
            available_panels.append("flash_margin_c")
        if any(row.get("ghs_signal_word") for row in rows):
            available_panels.append("ghs_signal_word")
        if any(row.get("occupational_exposure_limits") for row in rows):
            available_panels.append("occupational_exposure_limits")
        columns = 1 if len(available_panels) == 1 else 2
        rows_count = math.ceil(len(available_panels) / columns)
        fig, axes_value = plt.subplots(
            rows_count, columns,
            figsize=(7.4 if columns == 1 else 12.0, 4.8 * rows_count),
            squeeze=False,
        )
        axes = list(axes_value.flat)
        panel_axes = dict(zip(available_panels, axes))
        for unused in axes[len(available_panels):]:
            unused.set_visible(False)

        boiling_axis = panel_axes["boiling_margin_c"]
        boiling_bars = boiling_axis.barh(
            positions, [value or 0.0 for value in boiling_values], color=_COLORS[0],
        )
        boiling_axis.bar_label(
            boiling_bars, labels=[_format_value(value) for value in boiling_values], padding=3,
        )
        boiling_axis.axvline(0.0, color="#000000", linewidth=1)
        boiling_axis.set_yticks(positions, names)
        boiling_axis.invert_yaxis()
        boiling_axis.set_xlabel("Normal-BP margin (°C)")

        flash_axis = panel_axes.get("flash_margin_c")
        if flash_axis is not None:
            flash_bars = flash_axis.barh(
                positions, [value or 0.0 for value in flash_values], color=_COLORS[1],
            )
            flash_axis.bar_label(
                flash_bars,
                labels=[_format_value(value) for value in flash_values],
                label_type="center",
            )
            flash_axis.axvline(0.0, color="#000000", linewidth=1)
            flash_axis.set_yticks(positions, names)
            flash_axis.invert_yaxis()
            flash_axis.set_xlabel("Flash-point margin (°C)")

        signal_axis = panel_axes.get("ghs_signal_word")
        if signal_axis is not None:
            signal_axis.scatter(signal_values, positions, s=95, color=_COLORS[2])
            signal_axis.set_xlim(-.2, 2.2)
            signal_axis.set_xticks([0, 1, 2], ["Not reported", "Warning", "Danger"])
            signal_axis.set_yticks(positions, names)
            signal_axis.invert_yaxis()
            signal_axis.set_xlabel("GHS signal word")

        exposure_axis = panel_axes.get("occupational_exposure_limits")
        if exposure_axis is not None:
            exposure_axis.set_xlim(0, 1)
            exposure_axis.set_ylim(-.5, max(.5, len(rows) - .5))
            exposure_axis.set_yticks(positions, names)
            exposure_axis.invert_yaxis()
            exposure_axis.set_xticks([])
            exposure_axis.set_xlabel("Occupational exposure limits")
            for position, row in zip(positions, rows):
                limits = list(row.get("occupational_exposure_limits") or [])[:2]
                rendered_limits = []
                for item in limits:
                    value = (
                        f"{_format_value(item.get('ppm'))} ppm"
                        if item.get("ppm") is not None else
                        f"{_format_value(item.get('mg_m3'))} mg/m³"
                        if item.get("mg_m3") is not None else "value not parsed"
                    )
                    basis = f" · {item['basis']}" if item.get("basis") else ""
                    rendered_limits.append(
                        f"{item.get('authority') or 'Reported'}: {value}{basis}"
                    )
                exposure_axis.text(
                    .02, position, "\n".join(rendered_limits),
                    transform=exposure_axis.get_yaxis_transform(), ha="left", va="center",
                )

        plot_title = "Candidate safety at operating conditions"
        fig.suptitle(plot_title)
        fig.tight_layout(rect=(0, 0, 1, .95))
        path = _plot_path("safety_profile_comparison", output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "safety_profile_comparison",
            "title": plot_title,
            "subtitle": "Each solvent evaluated at its own stored operating temperature",
            "caption": "Margins, GHS classifications, and exposure limits are distinct safety evidence; missing fields remain explicit and normal-BP margin alone is not a complete safety assessment.",
            "result_kind": "safety", "comparison_rows": rows,
            "panels": available_panels,
            "omitted_unavailable_panels": [
                item for item in (
                    "flash_margin_c", "ghs_signal_word", "occupational_exposure_limits",
                ) if item not in available_panels
            ],
        }
        plot_type = "safety_profile_comparison"
        title = "Candidate safety comparison"
    else:
        fig, ax = plt.subplots(figsize=(max(6.5, 1.6 * len(labels) + 3), 4.8))
        bars = ax.bar(labels, values, color=[_COLORS[index % len(_COLORS)] for index in range(len(labels))])
        ax.set_ylabel(y_label)
        plot_title = {
            "safety": "Solvent safety comparison",
            "contaminant": "Contaminant-mode comparison",
            "contaminant_modes": "Contaminant-mode comparison",
            "tea": "TEA/LCA comparison",
            "tea_lca": "TEA/LCA comparison",
        }.get(kind, "Validated comparison")
        ax.set_title(plot_title)
        _label_bars(ax, bars)
        path = _plot_path(_slug([kind, "comparison"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "comparison_results",
            "title": plot_title, "subtitle": y_label,
            "caption": {
                "safety": "Candidate conditions are compared from typed safety evidence; normal-boiling-point margin alone is not a complete safety assessment.",
                "contaminant": "Bars count candidates passing each admitted screening mode; they do not establish contaminant-removal yield.",
                "contaminant_modes": "Bars count candidates passing each admitted screening mode; they do not establish contaminant-removal yield.",
                "tea": "Values reproduce the stored route-specific TEA/LCA result and its stated system boundary.",
                "tea_lca": "Values reproduce the stored route-specific TEA/LCA result and its stated system boundary.",
            }.get(kind, "Values reproduce bounded typed evidence without adding experimental validation."),
            "result_kind": kind, "labels": labels, "values": values,
            "y_label": y_label, "evidence": evidence,
        }
        plot_type = "comparison_results"
        title = "Validated result comparison"
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool, display=f"Full-resolution {kind} plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, title),
        analysis_type="results_plot", plot_type=plot_type,
        plot_paths=[plot_path], plot_path=plot_path, terminal_plot_data_path=sidecar_path,
        result_kind=kind, labels=labels, values=values, **evidence,
        warnings=["The plot regenerates admitted screening results; it does not add experimental validation."],
        model_basis="validated upstream tool regenerated inside the visualization boundary",
    )


def _stored_safety_comparison(state: Any) -> dict[str, Any] | None:
    """Read exact private safety evidence with a persisted-session fallback."""
    if state is None:
        return None
    private = getattr(state, "last_safety_comparison", None)
    if isinstance(private, dict) and private.get("comparison_rows"):
        return copy.deepcopy(private)
    facts: dict[str, Any] = {}
    try:
        observation = json.loads(str(getattr(state, "last_result", "") or ""))
        if observation.get("tool") == (
            "compare_solvent_safety_at_conditions"
        ):
            facts = dict(observation.get("relevant_facts") or {})
    except (TypeError, ValueError, json.JSONDecodeError):
        facts = {}
    rows = list(facts.get("comparison_rows") or [])
    if not rows:
        rows = list(getattr(state, "last_safety", None) or [])
    if not rows:
        return None
    return {
        "tool_name": "compare_solvent_safety_at_conditions",
        "comparison_mode": facts.get(
            "comparison_mode",
            "candidate_specific_temperature",
        ),
        "candidate_count": facts.get("candidate_count", len(rows)),
        "comparison_rows": [
            dict(item) for item in rows if isinstance(item, dict)
        ],
        "provenance": dict(facts.get("provenance") or {}),
    }


def _stored_safety_card(state: Any) -> dict[str, Any] | None:
    """Read the complete private typed safety-card payload."""
    if state is None:
        return None
    private = getattr(state, "last_safety_card", None)
    if (
        not isinstance(private, dict)
        or not isinstance(private.get("safety_profile"), dict)
    ):
        return None
    return copy.deepcopy(private)


def _plot_safety_card(
    state: Any,
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render a styled solvent card only from retained typed safety fields."""
    tool = "plot_analysis_results"
    stored = _stored_safety_card(state)
    if stored is None:
        return tool_error(
            tool,
            "No typed solvent safety card is available.",
            error_code="missing_safety_card",
        )
    profile = dict(stored["safety_profile"])
    identity = dict(profile.get("identity") or {})
    physical = dict(profile.get("physical_properties") or {})
    process = dict(profile.get("process_temperature_assessment") or {})
    gscore = dict(profile.get("gscore") or {})
    ghs = dict(profile.get("ghs") or {})
    peroxide = dict(profile.get("peroxide_risk") or {})
    provenance = dict(profile.get("provenance") or {})
    solvent = str(
        identity.get("name")
        or stored.get("solvent_name")
        or "Unknown solvent"
    )
    cas_number = str(identity.get("cas_number") or "not available")
    risk_level = str(process.get("risk_level") or "unknown").casefold()
    risk_fill = {
        "high": "#F4B8A6",
        "medium": "#F7DEA2",
        "low": "#BFE3D0",
    }.get(risk_level, "#E6E6E6")

    def value_unit(value: Any, unit: str) -> str:
        rendered = _format_value(value)
        return rendered if rendered == "—" else f"{rendered} {unit}"

    hazard_statements = [
        str(item) for item in list(
            ghs.get("hazard_statements") or [],
        )
    ]
    exposure_limits = [
        str(item) if not isinstance(item, dict)
        else " · ".join(
            f"{key}: {value}"
            for key, value in item.items()
            if value is not None
        )
        for item in list(
            profile.get("occupational_exposure_limits") or [],
        )[:3]
    ]
    gaps = [
        str(item).replace("_", " ")
        for item in list(profile.get("data_gaps") or [])
    ]
    flags = [
        str(item).replace("_", " ")
        for item in list(process.get("flags") or [])
    ]
    source_rows = [
        f"{str(key).replace('_', ' ')}: {value}"
        for key, value in provenance.items()
        if value and not isinstance(value, (list, dict))
    ][:3]

    plt = _pyplot()
    from matplotlib.patches import FancyBboxPatch, Rectangle

    # ---- content measurement --------------------------------------------
    # All vertical constants are in axis units calibrated to the original
    # 10.4-inch card (one unit = the full-canvas axis height). The canvas
    # height and the axis y-range grow together by the measured content
    # total, so one axis unit keeps the same physical size at any content
    # volume and every offset below keeps its meaning.
    line_height = 0.026
    block_gap = 0.012

    def wrapped(text: str, width: int) -> list[str]:
        return textwrap.wrap(text, width=width) or [text]

    flags_text = "Flags: " + (", ".join(flags) if flags else "none reported")
    flags_block = len(wrapped(flags_text, 52)) * line_height
    thermal_height = max(0.21, 0.145 + flags_block + 0.015)

    signal_text = (
        f"Signal word: {ghs.get('signal_word') or 'not reported'} · "
        f"Pictograms: {', '.join(ghs.get('pictograms') or []) or 'none'}"
    )
    signal_block = len(wrapped(signal_text, 76)) * line_height
    ghs_cursor = 0.060 + signal_block + block_gap
    statement_blocks: list[tuple[float, str]] = []
    for statement in hazard_statements:
        lines = wrapped(f"• {statement}", 76)
        block = len(lines) * line_height
        statement_blocks.append((ghs_cursor + block / 2.0, "\n".join(lines)))
        ghs_cursor += block + block_gap
    if not hazard_statements:
        ghs_cursor += line_height + block_gap
    ghs_height = max(ghs_cursor + 0.010, 0.165)

    exposure_text = (
        "OEL: " + " | ".join(exposure_limits)
        if exposure_limits
        else "OEL: not available in current sources"
    )
    peroxide_text = (
        "Peroxide class: "
        f"{peroxide.get('peroxide_former_class') or 'not available'}"
    )
    exposure_cursor = 0.075
    exposure_blocks: list[tuple[float, str]] = []
    for text in (exposure_text, peroxide_text):
        lines = wrapped(text, 37)
        block = len(lines) * line_height
        exposure_blocks.append(
            (exposure_cursor + block / 2.0, "\n".join(lines)),
        )
        exposure_cursor += block + 2.0 * block_gap
    exposure_height = max(exposure_cursor + 0.010, 0.165)
    ghs_row_height = max(ghs_height, exposure_height)

    gaps_text = ", ".join(gaps) if gaps else "No data gaps reported."
    gaps_block = len(wrapped(gaps_text, 112)) * line_height
    gaps_height = max(0.065 + gaps_block + 0.020, 0.12)

    sources_text = (
        " | ".join(source_rows) if source_rows
        else "No source details returned."
    )
    sources_block = len(wrapped(sources_text, 120)) * line_height
    sources_height = max(0.065 + sources_block + 0.020, 0.13)

    top_pad, row_gap, bottom_pad = 0.030, 0.025, 0.020
    header_height = 0.115
    total = (
        top_pad + header_height + row_gap + thermal_height + row_gap
        + ghs_row_height + row_gap + gaps_height + row_gap
        + sources_height + bottom_pad
    )

    # ---- reflowed canvas -------------------------------------------------
    fig = plt.figure(figsize=(12.8, 10.4 * total))
    axis = fig.add_axes((0.035, 0.035, 0.93, 0.89))
    axis.set_xlim(0.0, 1.0)
    axis.set_ylim(0.0, total)
    axis.axis("off")
    title = f"{solvent} solvent safety card"
    fig.suptitle(title, fontweight="bold", y=0.97)

    header_top = total - top_pad
    thermal_top = header_top - header_height - row_gap
    ghs_row_top = thermal_top - thermal_height - row_gap
    gaps_top = ghs_row_top - ghs_row_height - row_gap
    sources_top = gaps_top - gaps_height - row_gap

    def panel(
        x: float, y: float, width: float, height: float,
        facecolor: str = "#F6F8FA",
    ) -> None:
        axis.add_patch(FancyBboxPatch(
            (x, y), width, height,
            boxstyle="round,pad=0.008,rounding_size=0.01",
            linewidth=0.8,
            edgecolor="#8A8A8A",
            facecolor=facecolor,
        ))

    def label(
        x: float, y: float, text: str, *,
        weight: str = "normal",
        horizontalalignment: str = "left",
        box: dict[str, Any] | None = None,
    ) -> None:
        axis.text(
            x, y, text,
            color=_TEXT_COLOR,
            fontsize=_FONT_SIZE,
            fontweight=weight,
            horizontalalignment=horizontalalignment,
            verticalalignment="center",
            bbox=box,
        )

    panel(0.02, header_top - header_height, 0.96, header_height, "#EAF2F8")
    label(0.04, header_top - 0.045, f"CAS {cas_number}", weight="bold")
    label(
        0.95, header_top - 0.045,
        f"HEATING RISK: {risk_level.upper()}",
        weight="bold",
        horizontalalignment="right",
        box={
            "boxstyle": "round,pad=0.35",
            "facecolor": risk_fill,
            "edgecolor": "#606060",
        },
    )
    score = _finite(gscore.get("g_score"))
    label(
        0.36, header_top - 0.045,
        f"G-score: {_format_value(score)}"
        + (" · predicted" if gscore.get("ml_predicted") else ""),
        weight="bold",
    )
    axis.add_patch(Rectangle(
        (0.36, header_top - 0.090), 0.28, 0.018,
        facecolor="#FFFFFF", edgecolor="#606060", linewidth=0.8,
    ))
    axis.add_patch(Rectangle(
        (0.36, header_top - 0.090),
        0.28 * min(max((score or 0.0) / 10.0, 0.0), 1.0),
        0.018,
        facecolor="#56B4E9", edgecolor="none",
    ))
    label(
        0.04, header_top - 0.090,
        f"Operating condition: "
        f"{value_unit(stored.get('operating_temp_c'), '°C')}",
    )

    panel(0.02, thermal_top - thermal_height, 0.46, thermal_height)
    label(0.04, thermal_top - 0.035, "THERMAL / VOLATILITY", weight="bold")
    label(
        0.04, thermal_top - 0.080,
        f"Boiling point  {value_unit(physical.get('boiling_point_c'), '°C')}",
    )
    label(
        0.04, thermal_top - 0.120,
        f"Flash point  {value_unit(physical.get('flash_point_c'), '°C')}",
    )
    label(
        0.04, thermal_top - 0.160,
        "Autoignition  "
        f"{value_unit(physical.get('autoignition_c'), '°C')}",
    )
    label(
        0.04, thermal_top - 0.195,
        "Vapor pressure  "
        f"{value_unit(physical.get('vapor_pressure_kpa'), 'kPa')} at "
        f"{value_unit(physical.get('vapor_pressure_temp_c'), '°C')}",
    )

    panel(0.52, thermal_top - thermal_height, 0.46, thermal_height)
    label(0.54, thermal_top - 0.035, "PROCESS TEMPERATURE", weight="bold")
    label(
        0.54, thermal_top - 0.080,
        "Boiling margin  "
        f"{value_unit(process.get('boiling_margin_c'), '°C')}",
    )
    label(
        0.54, thermal_top - 0.120,
        "Autoignition margin  "
        f"{value_unit(process.get('autoignition_margin_c'), '°C')}",
    )
    label(
        0.54, thermal_top - 0.145 - flags_block / 2.0,
        "\n".join(wrapped(flags_text, 52)),
    )

    panel(0.02, ghs_row_top - ghs_row_height, 0.60, ghs_row_height)
    label(0.04, ghs_row_top - 0.035, "GHS HAZARDS", weight="bold")
    label(
        0.04, ghs_row_top - 0.060 - signal_block / 2.0,
        "\n".join(wrapped(signal_text, 76)),
    )
    for anchor_offset, statement_text in statement_blocks:
        label(0.04, ghs_row_top - anchor_offset, statement_text)
    if not hazard_statements:
        label(
            0.04,
            ghs_row_top - 0.060 - signal_block - block_gap
            - line_height / 2.0,
            "No hazard statements returned.",
        )

    panel(0.65, ghs_row_top - ghs_row_height, 0.33, ghs_row_height)
    label(0.67, ghs_row_top - 0.035, "EXPOSURE / STORAGE", weight="bold")
    for anchor_offset, exposure_block in exposure_blocks:
        label(0.67, ghs_row_top - anchor_offset, exposure_block)

    panel(0.02, gaps_top - gaps_height, 0.96, gaps_height, "#FFF7E6")
    label(0.04, gaps_top - 0.030, "DATA GAPS", weight="bold")
    label(
        0.04, gaps_top - 0.065 - gaps_block / 2.0,
        "\n".join(wrapped(gaps_text, 112)),
    )

    panel(0.02, sources_top - sources_height, 0.96, sources_height, "#F3F3F3")
    label(0.04, sources_top - 0.030, "SOURCES", weight="bold")
    label(
        0.04, sources_top - 0.065 - sources_block / 2.0,
        "\n".join(wrapped(sources_text, 120)),
    )

    path = _plot_path(
        _slug([solvent, "safety_card"]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "safety_card",
        "title": title,
        "subtitle": (
            f"Typed safety evidence at "
            f"{value_unit(stored.get('operating_temp_c'), '°C')}"
        ),
        "caption": (
            "The card reproduces the retained typed solvent-safety result. "
            "Reported gaps remain gaps; normal-boiling-point margin alone is "
            "not a complete safety assessment."
        ),
        "result_kind": "solvent_safety_card",
        "solvent_name": solvent,
        "cas_number": cas_number,
        "operating_temp_c": _finite(stored.get("operating_temp_c")),
        "risk_level": risk_level,
        "g_score": score,
        "thermal_volatility": physical,
        "process_temperature": process,
        "ghs": ghs,
        "occupational_exposure_limits": copy.deepcopy(
            profile.get("occupational_exposure_limits") or [],
        ),
        "data_gaps": list(profile.get("data_gaps") or []),
        "provenance": provenance,
        "publication_treatment": {
            "palette": list(_COLORS),
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
            "panel_background": "#F6F8FA",
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=f"Full-resolution solvent safety card: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, title),
        analysis_type="solvent_safety_card",
        plot_type="safety_card",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="solvent_safety_card",
        solvent_name=solvent,
        cas_number=cas_number,
        operating_temp_c=_finite(stored.get("operating_temp_c")),
        risk_level=risk_level,
        g_score=score,
        data_gaps=list(profile.get("data_gaps") or []),
        provenance=provenance,
        model_basis="retained typed get_solvent_safety_card result",
        warnings=list(stored.get("warnings") or []),
    )


def _plot_safety_margin_bars(
    state: Any,
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render flash-point minus operating-temperature margins from state."""
    tool = "plot_analysis_results"
    stored = _stored_safety_comparison(state)
    if stored is None:
        return tool_error(
            tool,
            "No stored solvent safety comparison is available.",
            error_code="missing_safety_comparison",
        )
    rows: list[dict[str, Any]] = []
    for source in stored.get("comparison_rows") or []:
        flash_point = _finite(source.get("flash_point_c"))
        operating = _finite(source.get("operating_temp_c"))
        if flash_point is None or operating is None:
            continue
        margin = flash_point - operating
        rows.append({
            "solvent": str(source.get("solvent") or "Unknown solvent"),
            "operating_temp_c": operating,
            "flash_point_c": flash_point,
            "flash_point_margin_c": margin,
            "margin_class": (
                "negative_above_flash_point"
                if margin < 0 else "nonnegative_at_or_below_flash_point"
            ),
            "color": "#D55E00" if margin < 0 else "#0072B2",
        })
    if not rows:
        return tool_error(
            tool,
            "The stored safety comparison has no paired flash point and "
            "operating temperature values.",
            error_code="missing_safety_margin_values",
        )

    plt = _pyplot()
    height = max(4.8, 0.54 * len(rows) + 2.5)
    fig, axis = plt.subplots(figsize=(9.4, height))
    positions = list(range(len(rows)))
    margins = [float(item["flash_point_margin_c"]) for item in rows]
    bars = axis.barh(
        positions,
        margins,
        color=[str(item["color"]) for item in rows],
        edgecolor="#000000",
        linewidth=0.6,
    )
    labels = [
        f"{item['solvent']} · {item['operating_temp_c']:g} °C"
        for item in rows
    ]
    axis.set_yticks(positions, labels)
    axis.invert_yaxis()
    axis.set_xlabel(
        "Flash-point margin = flash point − operating temperature (°C)",
    )
    zero_label = "Zero margin: operating temperature equals flash point"
    axis.axvline(
        0.0,
        color="#000000",
        linewidth=1.2,
        linestyle="--",
        label=zero_label,
    )
    span_min = min([0.0, *margins])
    span_max = max([0.0, *margins])
    span = max(span_max - span_min, 1.0)
    axis.set_xlim(span_min - 0.18 * span, span_max + 0.18 * span)
    axis.bar_label(
        bars,
        labels=[f"{value:.1f} °C" for value in margins],
        padding=4,
    )
    axis.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
    )
    plot_title = "Flash-point safety margin by stored condition"
    fig.suptitle(plot_title)
    fig.subplots_adjust(
        left=0.28,
        right=0.94,
        bottom=0.16,
        top=0.82,
    )
    path = _plot_path(
        "stored_solvent_safety_margins",
        output_dir,
        output_path,
    )
    provenance = dict(stored.get("provenance") or {})
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "safety_margin_bars",
        "title": plot_title,
        "subtitle": "Stored candidate-specific solvent conditions",
        "caption": (
            "Margin equals flash point minus operating temperature. Negative "
            "values indicate operation above the reported flash point; this "
            "single margin is not a complete process-safety assessment."
        ),
        "result_kind": "safety",
        "comparison_rows": rows,
        "zero_line": {"value_c": 0.0, "label": zero_label},
        "margin_definition": (
            "flash point minus operating temperature"
        ),
        "provenance": provenance,
        "publication_treatment": {
            "palette": ["#D55E00", "#0072B2"],
            "negative_margin_color": "#D55E00",
            "nonnegative_margin_color": "#0072B2",
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution safety-margin bars: {plot_path}. "
            "Negative margins are conditions above flash point."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Stored solvent flash-point margins",
        ),
        analysis_type="safety_margin_plot",
        plot_type="safety_margin_bars",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="safety",
        comparison_rows=rows,
        margin_definition="flash point minus operating temperature",
        zero_line_label=zero_label,
        provenance=provenance,
        warnings=[
            "A flash-point margin alone is not a complete process-safety "
            "assessment.",
        ],
        model_basis="stored compare_solvent_safety_at_conditions result",
    )


def _stored_solvent_ranking(state: Any) -> dict[str, Any] | None:
    """Read exact private solvent-ranking evidence when it is available."""
    if state is None:
        return None
    private = getattr(state, "last_solvent_ranking", None)
    if isinstance(private, dict) and private.get("results"):
        return copy.deepcopy(private)
    try:
        observation = json.loads(str(getattr(state, "last_result", "") or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if observation.get("tool") != "rank_solvents_by_solubility":
        return None
    facts = dict(observation.get("relevant_facts") or {})
    rows = [
        dict(item) for item in facts.get("results") or []
        if isinstance(item, dict)
    ]
    if not rows:
        return None
    return {
        key: facts.get(key) for key in (
            "tool_name", "ranking_axis", "polymer", "temperature_c",
            "require_atmospheric", "model_basis", "solubility_unit",
        ) if facts.get(key) is not None
    } | {"results": rows}


def _solvent_join_key(value: Any) -> str:
    resolved = thermo.resolve_solvent(str(value or ""))
    if resolved:
        return resolved
    return "".join(
        character for character in str(value or "").casefold()
        if character.isalnum()
    )


def _highlight_join_keys(
    requested: Sequence[str] | None,
    rows: Sequence[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    aliases: dict[str, str] = {}
    for row in rows:
        key = str(row["solvent_identity"])
        label = str(row["solvent"])
        normalized = re.findall(r"[a-z0-9]+", label.casefold())
        for alias in (
            key,
            "".join(normalized),
            "".join(item[0] for item in normalized if item),
        ):
            if alias:
                aliases.setdefault(alias, key)
    selected: list[str] = []
    unknown: list[str] = []
    for raw in _items(requested):
        normalized = "".join(re.findall(r"[a-z0-9]+", raw.casefold()))
        key = aliases.get(normalized)
        if key is None:
            resolved = _solvent_join_key(raw)
            key = resolved if any(
                row["solvent_identity"] == resolved for row in rows
            ) else None
        if key is None:
            unknown.append(raw)
        elif key not in selected:
            selected.append(key)
    return selected, unknown


def _plot_metric_scatter(
    state: Any,
    *,
    highlight_solvents: Sequence[str] | None,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Join stored solvent metrics by canonical identity and render them."""
    tool = "plot_analysis_results"
    ranking = _stored_solvent_ranking(state)
    safety = _stored_safety_comparison(state)
    if ranking is None or safety is None:
        return tool_error(
            tool,
            "Stored solvent ranking and safety comparison results are both "
            "required for a metric scatter.",
            error_code="missing_metric_join_sources",
        )
    safety_by_identity: dict[str, list[dict[str, Any]]] = {}
    for source in safety.get("comparison_rows") or []:
        score = _finite(source.get("g_score"))
        identity = _solvent_join_key(source.get("solvent"))
        if identity and score is not None:
            safety_by_identity.setdefault(identity, []).append(
                dict(source),
            )
    joined: list[dict[str, Any]] = []
    ranking_temperature = _finite(ranking.get("temperature_c"))
    for source in ranking.get("results") or []:
        solubility = _finite(source.get("solubility_pct"))
        identity = _solvent_join_key(source.get("solvent"))
        candidates = safety_by_identity.get(identity) or []
        if solubility is None or not identity or not candidates:
            continue
        safety_row = min(
            candidates,
            key=lambda item: abs(
                (_finite(item.get("operating_temp_c")) or 0.0)
                - (ranking_temperature or 0.0)
            ),
        )
        score = _finite(safety_row.get("g_score"))
        if score is None:
            continue
        joined.append({
            "solvent": str(source.get("solvent") or ""),
            "solvent_identity": identity,
            "rank": source.get("rank"),
            "solubility_wt_pct": solubility,
            "solubility_method": source.get("method"),
            "g_score": score,
            "safety_operating_temp_c": _finite(
                safety_row.get("operating_temp_c"),
            ),
            "g_score_is_ml_predicted": safety_row.get(
                "g_score_is_ml_predicted",
            ),
            "g_score_source_families": list(
                safety_row.get("source_families") or [],
            ),
        })
    if len(joined) < 2:
        return tool_error(
            tool,
            "Fewer than two solvents have both stored metrics.",
            error_code="insufficient_metric_join",
        )
    highlighted, unknown = _highlight_join_keys(
        highlight_solvents,
        joined,
    )
    if unknown:
        return tool_error(
            tool,
            "Highlighted solvents are absent from the stored metric join: "
            + ", ".join(unknown),
            error_code="highlight_not_in_metric_join",
            unknown_highlight_solvents=unknown,
        )

    polymer = str(
        ranking.get("polymer")
        or joined[0].get("polymer")
        or "polymer",
    )
    temperature = ranking_temperature
    methods = list(dict.fromkeys(
        str(item.get("method") or "").replace("_", " ")
        for item in ranking.get("results") or []
        if item.get("method")
    ))
    method_label = (
        ", ".join(methods)
        if methods else str(ranking.get("model_basis") or "stored model")
    )
    unit_label = (
        "wt% solution concentration"
        if ranking.get("solubility_unit")
        == "wt_pct_solution_concentration"
        else str(
            ranking.get("solubility_unit") or "modeled concentration",
        ).replace("_", " ")
    )
    x_provenance = (
        f"DISSOLVE unified solubility API using {method_label} for "
        f"{polymer} at {temperature:g} °C ({unit_label})"
        if temperature is not None else
        f"DISSOLVE unified solubility API using {method_label} for "
        f"{polymer} ({unit_label})"
    )
    estimated = [
        str(item["solvent"]) for item in joined
        if (
            item.get("g_score_is_ml_predicted") is True
            or (
                "GreenSolventDB" in item.get(
                    "g_score_source_families",
                    (),
                )
                and "GSK solvent guide" not in item.get(
                    "g_score_source_families",
                    (),
                )
            )
        )
    ]
    y_provenance = (
        "GSK solvent guide g-scores with GreenSolventDB estimates for "
        + ", ".join(estimated)
        + ", retained by the stored solvent safety comparison"
        if estimated else
        "GSK solvent guide g-scores retained by the stored solvent safety "
        "comparison"
    )

    plt = _pyplot()
    fig, axis = plt.subplots(figsize=(8.8, 6.2))
    highlight_colors = {
        identity: _COLORS[index % len(_COLORS)]
        for index, identity in enumerate(highlighted)
    }
    colors = [
        highlight_colors.get(
            str(item["solvent_identity"]),
            "#8C8C8C",
        )
        for item in joined
    ]
    sizes = [
        86 if item["solvent_identity"] in highlight_colors else 48
        for item in joined
    ]
    axis.scatter(
        [float(item["solubility_wt_pct"]) for item in joined],
        [float(item["g_score"]) for item in joined],
        c=colors,
        s=sizes,
        edgecolors="#000000",
        linewidths=0.6,
        zorder=3,
    )
    x_values = [
        float(item["solubility_wt_pct"]) for item in joined
    ]
    x_midpoint = (min(x_values) + max(x_values)) / 2.0
    direct_labels = []
    for label_index, item in enumerate(
        row for row in joined
        if row["solvent_identity"] in highlight_colors
    ):
        x_value = float(item["solubility_wt_pct"])
        horizontal = 10 if x_value <= x_midpoint else -10
        offset = (
            horizontal,
            10 if label_index % 2 == 0 else -16,
        )
        axis.annotate(
            str(item["solvent"]),
            (
                x_value,
                float(item["g_score"]),
            ),
            xytext=offset,
            textcoords="offset points",
            ha="left" if offset[0] >= 0 else "right",
            va="bottom" if offset[1] >= 0 else "top",
            color="#000000",
        )
        direct_labels.append(str(item["solvent"]))
    temperature_label = (
        f" at {temperature:g} °C" if temperature is not None else ""
    )
    axis.set_xlabel(
        f"Modeled {polymer} solubility{temperature_label} "
        f"({unit_label})",
    )
    axis.set_ylabel("G-score (higher is preferable as a screening proxy)")
    axis.set_title("Stored solvent metrics joined by solvent identity")
    axis.margins(x=0.14, y=0.16)
    fig.subplots_adjust(
        left=0.15,
        right=0.95,
        bottom=0.16,
        top=0.88,
    )
    path = _plot_path(
        _slug([polymer, "solubility_g_score_scatter"]),
        output_dir,
        output_path,
    )
    axis_provenance = {
        "x": {
            "source_tool": "rank_solvents_by_solubility",
            "model_basis": ranking.get("model_basis"),
            "polymer": polymer,
            "temperature_c": temperature,
            "unit": unit_label,
            "methods": methods,
        },
        "y": {
            "source_tool": "compare_solvent_safety_at_conditions",
            "source_families": list(
                (safety.get("provenance") or {}).get(
                    "source_families",
                ) or [],
            ),
            "green_solvent_estimate_solvents": estimated,
        },
    }
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "metric_scatter",
        "title": "Stored solvent metric comparison",
        "subtitle": (
            f"{polymer} solubility{temperature_label} versus solvent "
            "greenness score"
        ),
        "caption": (
            "The x axis reproduces the stored thermodynamic solvent ranking; "
            "the y axis reproduces stored sourced greenness scores. Points "
            "are joined only by canonical solvent identity."
        ),
        "result_kind": "stored_solvent_metric_join",
        "joined_rows": joined,
        "join_basis": "canonical solvent identity",
        "highlighted_solvents": direct_labels,
        "direct_label_mode": "highlighted solvents only",
        "x_axis_provenance": x_provenance,
        "y_axis_provenance": y_provenance,
        "axis_provenance": axis_provenance,
        "publication_treatment": {
            "palette": list(_COLORS),
            "context_color": "#8C8C8C",
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution stored-metric scatter: {plot_path}. "
            f"X-axis provenance: {x_provenance}. "
            f"Y-axis provenance: {y_provenance}."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Stored solvent metric comparison",
        ),
        analysis_type="stored_solvent_metric_join",
        plot_type="metric_scatter",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="stored_solvent_metric_join",
        polymers=[polymer],
        solvents=[str(item["solvent"]) for item in joined],
        temperature_c=temperature,
        joined_rows=joined,
        joined_pair_count=len(joined),
        highlighted_solvents=direct_labels,
        join_basis="canonical solvent identity",
        x_axis_provenance=x_provenance,
        y_axis_provenance=y_provenance,
        axis_provenance=axis_provenance,
        warnings=[
            "G-score is a sourced screening proxy rather than a complete "
            "sustainability assessment.",
        ],
        model_basis="stored thermodynamic ranking and safety comparison",
    )


_TEA_RECORD_RENDER_METRICS = {
    "msp": (
        "economics", "msp_usd_per_kg",
        "Minimum selling price", "USD/kg product",
    ),
    "tci": (
        "economics", "tci_usd",
        "Total capital investment", "USD",
    ),
    "aoc": (
        "economics", "aoc_usd_per_yr",
        "Annual operating cost", "USD/yr",
    ),
    "gwp": (
        "lca", "gwp_kg_co2e_per_kg",
        "Global warming potential", "kg CO2e/kg product",
    ),
    "etox": (
        "lca", "etox_ctue_per_kg",
        "Ecotoxicity", "CTUe/kg product",
    ),
    "htc": (
        "lca", "htc_ctuh_per_kg",
        "Human toxicity, carcinogenic", "CTUh/kg product",
    ),
    "htnc": (
        "lca", "htnc_ctuh_per_kg",
        "Human toxicity, noncarcinogenic", "CTUh/kg product",
    ),
    "electricity": (
        "operations", "electricity_consumed_mj_per_kg",
        "Electricity consumed", "MJ/kg product",
    ),
    "heating": (
        "operations", "heating_duty_mj_per_kg",
        "Heating duty", "MJ/kg product",
    ),
    "cooling": (
        "operations", "cooling_duty_mj_per_kg",
        "Cooling duty", "MJ/kg product",
    ),
    "energy": (
        "operations", "total_energy_mj_per_kg",
        "Total process energy", "MJ/kg product",
    ),
}


def _stored_tea_records(state: Any) -> dict[str, Any] | None:
    """Read the exact admitted-record snapshot retained outside model state."""
    if state is None:
        return None
    private = getattr(state, "last_tea_records", None)
    if not isinstance(private, dict):
        return None
    records = [
        copy.deepcopy(item) for item in private.get("records") or []
        if isinstance(item, dict)
    ]
    if not records:
        return None
    return {**copy.deepcopy(private), "records": records}


def _tea_record_metric(
    record: dict[str, Any],
    metric: str,
) -> float | None:
    section, field, _label, _unit = _TEA_RECORD_RENDER_METRICS[metric]
    return _finite((record.get(section) or {}).get(field))


def _tea_record_identity(record: dict[str, Any]) -> tuple[str, str]:
    config = record.get("config") or {}
    return (
        str(config.get("target_plastic") or "Unknown polymer"),
        str(config.get("solvent") or "Unknown solvent"),
    )


def _plot_tea_record_card(
    state: Any,
    *,
    target_polymer: str | None,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render up to three exact admitted process records as styled cards."""
    tool = "plot_analysis_results"
    stored = _stored_tea_records(state)
    if stored is None:
        return tool_error(
            tool,
            "No admitted process records are available.",
            error_code="missing_admitted_process_records",
        )
    rule = tea_contracts.TEA_RECORD_CARD_SELECTION_RULE
    boundary_key = rule.boundary.config_key

    def boundary_of(record: dict[str, Any]) -> str:
        return str((record.get("config") or {}).get(boundary_key) or "")

    stored_records = list(stored["records"])
    requested_polymer = str(target_polymer or "").strip().casefold()
    # The requested boundaries are the distinct boundary values the request
    # covers, in stored order — not a single string. An unscoped request
    # covers every stored boundary, which is what makes "both stages" a
    # resolvable scope rather than a phrase.
    all_boundaries = list(dict.fromkeys(
        boundary_of(record) for record in stored_records if boundary_of(record)
    ))
    requested_boundaries = [
        value for value in all_boundaries
        if not requested_polymer or value.casefold() == requested_polymer
    ]
    candidates = [
        record for record in stored_records
        if boundary_of(record) in requested_boundaries
    ]
    if not candidates:
        return tool_error(
            tool,
            "No stored admitted process record matches the requested scope.",
            error_code="missing_tea_card_records",
        )

    # Coverage first: one representative per requested boundary before any
    # boundary gets a second card, so the cap can never spend every slot on
    # whichever boundary happens to sort first in storage.
    selected: list[dict[str, Any]] = []
    for value in requested_boundaries:
        group = [
            record for record in candidates if boundary_of(record) == value
        ]
        preferred = next(
            (
                record for record in group
                if str((record.get("config") or {}).get("energy_case") or "")
                in rule.energy_case_preference
            ),
            group[0],
        )
        selected.append(preferred)
    for record in candidates:
        if len(selected) >= rule.render_cap:
            break
        if not any(record is item for item in selected):
            selected.append(record)
    records = selected[:rule.render_cap]

    omitted = [
        record for record in candidates
        if not any(record is item for item in records)
    ]
    rendered_boundaries = sorted({
        boundary_of(record) for record in records if boundary_of(record)
    })
    omitted_boundaries = sorted(
        set(requested_boundaries) - set(rendered_boundaries),
    )
    truncation = {
        "truncated": bool(omitted),
        "total_candidates": len(candidates),
        "omitted_count": len(omitted),
        "omitted_record_ids": [
            str(record.get("record_id") or "") for record in omitted
        ],
        "omitted_target_polymers": omitted_boundaries,
        "selection_rule": rule.name,
        "selection_rule_description": rule.description,
        "render_cap": rule.render_cap,
    }

    def metric(
        record: dict[str, Any],
        section: str,
        field: str,
        unit: str,
    ) -> str:
        return f"{_format_value((record.get(section) or {}).get(field))} {unit}"

    plt = _pyplot()
    from matplotlib.patches import FancyBboxPatch

    figure_width = max(6.0, 5.1 * len(records))
    fig, axes = plt.subplots(
        1,
        len(records),
        figsize=(figure_width, 8.4),
        squeeze=False,
    )
    fig.suptitle("Process records", fontweight="bold", y=0.97)
    cards: list[dict[str, Any]] = []
    for axis, record in zip(axes[0], records):
        config = dict(record.get("config") or {})
        economics = dict(record.get("economics") or {})
        lca = dict(record.get("lca") or {})
        operations = dict(record.get("operations") or {})
        polymer, solvent = _tea_record_identity(record)
        record_id = str(record.get("record_id") or "unlabeled record")
        axis.set_xlim(0.0, 1.0)
        axis.set_ylim(0.0, 1.0)
        axis.axis("off")
        axis.add_patch(FancyBboxPatch(
            (0.015, 0.01), 0.97, 0.965,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            linewidth=0.9,
            edgecolor="#777777",
            facecolor="#F8FAFC",
        ))

        def panel(y: float, height: float, color: str) -> None:
            axis.add_patch(FancyBboxPatch(
                (0.045, y), 0.91, height,
                boxstyle="round,pad=0.008,rounding_size=0.012",
                linewidth=0.6,
                edgecolor="#A0A0A0",
                facecolor=color,
            ))

        def label(
            x: float, y: float, text: str, *,
            weight: str = "normal",
            horizontalalignment: str = "left",
        ) -> None:
            axis.text(
                x, y, text,
                color=_TEXT_COLOR,
                fontsize=_FONT_SIZE,
                fontweight=weight,
                horizontalalignment=horizontalalignment,
                verticalalignment="center",
            )

        panel(0.77, 0.17, "#EAF2F8")
        label(0.07, 0.91, f"{polymer} / {solvent}", weight="bold")
        label(0.07, 0.865, record_id)
        label(
            0.07, 0.825,
            f"Dissolve {_format_value(config.get('dissolution_temperature_c'))} °C"
            f" · precipitate "
            f"{_format_value(config.get('precipitation_temperature_c'))} °C",
        )
        label(
            0.07, 0.79,
            f"{_format_value(config.get('processing_capacity'))} t/yr"
            f" · energy case {config.get('energy_case') or '—'}"
            f" · feed {_format_value(config.get('target_plastic_percent'))} wt%",
        )

        panel(0.57, 0.19, "#F3F7FB")
        label(0.07, 0.72, "ECONOMICS", weight="bold")
        label(
            0.07, 0.675,
            f"MSP  {metric(record, 'economics', 'msp_usd_per_kg', 'USD/kg')}",
        )
        label(
            0.07, 0.63,
            f"TCI  {metric(record, 'economics', 'tci_usd', 'USD')}",
        )
        label(
            0.07, 0.585,
            f"AOC  {metric(record, 'economics', 'aoc_usd_per_yr', 'USD/yr')}",
        )

        panel(0.33, 0.21, "#EEF7F2")
        label(0.07, 0.50, "LIFE-CYCLE METRICS", weight="bold")
        label(
            0.07, 0.455,
            f"GWP  {metric(record, 'lca', 'gwp_kg_co2e_per_kg', 'kg CO₂e/kg')}",
        )
        label(
            0.07, 0.41,
            f"Ecotox  {metric(record, 'lca', 'etox_ctue_per_kg', 'CTUe/kg')}",
        )
        label(
            0.07, 0.365,
            "Human tox (cancer)  "
            f"{metric(record, 'lca', 'htc_ctuh_per_kg', 'CTUh/kg')}\n"
            "Human tox (non-cancer)  "
            f"{metric(record, 'lca', 'htnc_ctuh_per_kg', 'CTUh/kg')}",
        )

        panel(0.14, 0.18, "#FFF7E6")
        label(0.07, 0.295, "OPERATIONS", weight="bold")
        label(
            0.07, 0.255,
            "Electricity  "
            f"{metric(record, 'operations', 'electricity_consumed_mj_per_kg', 'MJ/kg')}",
        )
        label(
            0.07, 0.21,
            "Heating  "
            f"{metric(record, 'operations', 'heating_duty_mj_per_kg', 'MJ/kg')}"
            " · cooling  "
            f"{metric(record, 'operations', 'cooling_duty_mj_per_kg', 'MJ/kg')}",
        )
        label(
            0.07, 0.165,
            "Total energy  "
            f"{metric(record, 'operations', 'total_energy_mj_per_kg', 'MJ/kg')}",
        )

        label(0.07, 0.095, "BASIS", weight="bold")
        label(
            0.07, 0.045,
            "\n".join(textwrap.wrap(
                "Exact admitted cache record; no interpolation. "
                f"Solvent loss {_format_value(config.get('solvent_loss_pct'))}%"
                f" · solvent price "
                f"{_format_value(config.get('solvent_price'))} USD/kg.",
                width=58,
            )),
        )
        cards.append({
            "record_id": record.get("record_id"),
            "record_group": record.get("record_group"),
            "config": copy.deepcopy(config),
            "economics": copy.deepcopy(economics),
            "lca": copy.deepcopy(lca),
            "operations": copy.deepcopy(operations),
            "energy_normalization": copy.deepcopy(
                record.get("energy_normalization") or {},
            ),
        })

    # A cap that is not visible on the figure is a silent cap: whoever reads
    # the PNG without the sidecar must still be able to see that records were
    # left out, which ones, and by what rule.
    truncation_note = ""
    if truncation["truncated"]:
        omitted_text = ", ".join(truncation["omitted_record_ids"])
        truncation_note = "\n".join(textwrap.wrap(
            f"Showing {len(records)} of {truncation['total_candidates']} "
            f"admitted records. Omitted: {omitted_text}. "
            f"Selection: {rule.description}.",
            width=int(max(80.0, 16.0 * figure_width)),
        ))
    fig.subplots_adjust(
        left=0.025,
        right=0.975,
        bottom=0.04 + (
            0.035 * len(truncation_note.split("\n")) if truncation_note else 0.0
        ),
        top=0.92,
        wspace=0.08,
    )
    if truncation_note:
        fig.text(
            0.5, 0.012, truncation_note,
            color=_TEXT_COLOR,
            fontsize=_FONT_SIZE,
            horizontalalignment="center",
            verticalalignment="bottom",
        )
    path = _plot_path(
        _slug([
            "admitted_process_record_cards",
            *(str(item["record_id"]) for item in cards),
        ]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "tea_record_card",
        "title": "Admitted process records",
        "subtitle": f"{len(cards)} exact stored configuration(s)",
        "caption": (
            "Each card reproduces one exact admitted BioSTEAM cache record "
            "with its stored configuration, product basis, economics, "
            "life-cycle metrics, and operating-energy split."
        ),
        "result_kind": "admitted_process_record_cards",
        "record_count": len(cards),
        "cards": cards,
        "requested_target_polymers": sorted(requested_boundaries),
        "rendered_target_polymers": rendered_boundaries,
        "truncation": truncation,
        "record_basis": stored.get("record_basis"),
        "record_assumptions": copy.deepcopy(
            stored.get("record_assumptions") or [],
        ),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "palette": list(_COLORS),
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
            "layout": "one-row card grid, maximum three records",
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution admitted process-record card set: {plot_path}"
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Admitted process records",
        ),
        analysis_type="admitted_process_record_cards",
        plot_type="tea_record_card",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="admitted_process_record_cards",
        record_count=len(cards),
        record_ids=[item["record_id"] for item in cards],
        cards=cards,
        # Coverage is reported as typed scope, not left to prose: what was
        # asked for, what actually rendered, and what did not.
        requested_target_polymers=sorted(requested_boundaries),
        rendered_target_polymers=rendered_boundaries,
        omitted_target_polymers=truncation["omitted_target_polymers"],
        omitted_record_ids=truncation["omitted_record_ids"],
        omitted_count=truncation["omitted_count"],
        total_candidates=truncation["total_candidates"],
        truncated=truncation["truncated"],
        selection_rule=truncation["selection_rule"],
        record_basis=stored.get("record_basis"),
        provenance=copy.deepcopy(stored.get("provenance") or {}),
        model_basis="exact admitted BioSTEAM cache records",
    )


def _plot_tea_pareto(
    state: Any,
    *,
    x_metric: str,
    y_metric: str,
    target_polymer: str | None,
    energy_case: str | None,
    include_sensitivity_variants: bool,
    show_provenance_note: bool,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render a min/min frontier over exact admitted process records."""
    tool = "plot_analysis_results"
    stored = _stored_tea_records(state)
    if stored is None:
        return tool_error(
            tool,
            "No admitted process records are available.",
            error_code="missing_admitted_process_records",
        )
    invalid_metrics = [
        item for item in (x_metric, y_metric)
        if item not in _TEA_RECORD_RENDER_METRICS
    ]
    if invalid_metrics:
        return tool_error(
            tool,
            "Unsupported admitted-record Pareto metric: "
            + ", ".join(invalid_metrics),
            error_code="unsupported_tea_render_metric",
            available_metrics=list(_TEA_RECORD_RENDER_METRICS),
        )
    if x_metric == y_metric:
        return tool_error(
            tool,
            "Select two distinct metrics for the Pareto comparison.",
            error_code="duplicate_tea_pareto_metrics",
        )
    requested_polymer = str(target_polymer or "").strip().casefold()
    requested_case = str(energy_case or "").strip().upper()
    if requested_case and requested_case not in {"C1", "C2", "C3"}:
        return tool_error(
            tool,
            f"Unsupported admitted energy case: {energy_case}.",
            error_code="unsupported_tea_energy_case",
            available_energy_cases=["C1", "C2", "C3"],
        )

    points = []
    for record in stored["records"]:
        record_group = str(record.get("record_group") or "")
        if record_group != "energy_case" and not (
            include_sensitivity_variants
            and record_group == "sensitivity"
        ):
            continue
        config = dict(record.get("config") or {})
        polymer, solvent = _tea_record_identity(record)
        if requested_polymer and polymer.casefold() != requested_polymer:
            continue
        record_case = str(config.get("energy_case") or "").upper()
        if requested_case and record_case != requested_case:
            continue
        x_value = _tea_record_metric(record, x_metric)
        y_value = _tea_record_metric(record, y_metric)
        if x_value is None or y_value is None:
            continue
        sensitivity_axis = str(
            record.get("sensitivity_axis") or "",
        )
        sensitivity_level = str(
            record.get("sensitivity_level") or "",
        )
        sensitivity_abbreviation = {
            "solvent_price": "SP",
            "plant_scale": "PS",
            "solvent_loss": "SL",
            "dissolution_temperature": "DT",
            "precipitation_temperature": "PT",
            "feedstock_distance": "FD",
            "feed_composition": "FC",
        }.get(sensitivity_axis, sensitivity_axis.replace("_", "-"))
        point_label = (
            f"{polymer}-{record_case or 'base'}"
            if record_group == "energy_case"
            else (
                f"{polymer}-{sensitivity_abbreviation}-"
                f"{sensitivity_level}"
            )
        )
        points.append({
            "record_id": record.get("record_id"),
            "record_group": record_group,
            "label": point_label,
            "polymer": polymer,
            "solvent": solvent,
            "energy_case": record_case or None,
            "sensitivity_axis": sensitivity_axis or None,
            "sensitivity_level": sensitivity_level or None,
            "x_metric": x_metric,
            "x_value": x_value,
            "y_metric": y_metric,
            "y_value": y_value,
            "config": copy.deepcopy(config),
        })
    if len(points) < 2:
        return tool_error(
            tool,
            "At least two stored admitted records with both requested metrics "
            "are required for a Pareto comparison.",
            error_code="insufficient_tea_pareto_points",
            point_count=len(points),
        )

    for point in points:
        point["pareto_efficient"] = not any(
            (
                other["x_value"] <= point["x_value"]
                and other["y_value"] <= point["y_value"]
                and (
                    other["x_value"] < point["x_value"]
                    or other["y_value"] < point["y_value"]
                )
            )
            for other in points
            if other is not point
        )
    frontier = sorted(
        (
            copy.deepcopy(point)
            for point in points if point["pareto_efficient"]
        ),
        key=lambda item: (item["x_value"], item["y_value"]),
    )
    dominated = [
        point for point in points if not point["pareto_efficient"]
    ]
    efficient = [
        point for point in points if point["pareto_efficient"]
    ]
    polymer_colors = {
        polymer: _COLORS[index % len(_COLORS)]
        for index, polymer in enumerate(dict.fromkeys(
            point["polymer"] for point in points
        ))
    }

    plt = _pyplot()
    fig, axis = plt.subplots(figsize=(10.4, 6.8))
    if dominated:
        axis.scatter(
            [point["x_value"] for point in dominated],
            [point["y_value"] for point in dominated],
            color="#B7B7B7",
            edgecolor="#555555",
            linewidth=0.7,
            s=75,
            label="Dominated admitted record",
            zorder=2,
        )
    for polymer in dict.fromkeys(
        point["polymer"] for point in efficient
    ):
        rows = [
            point for point in efficient
            if point["polymer"] == polymer
        ]
        axis.scatter(
            [point["x_value"] for point in rows],
            [point["y_value"] for point in rows],
            color=polymer_colors[polymer],
            edgecolor="#000000",
            linewidth=0.8,
            s=95,
            label=f"Pareto-efficient · {polymer}",
            zorder=4,
        )
    if len(frontier) > 1:
        axis.step(
            [point["x_value"] for point in frontier],
            [point["y_value"] for point in frontier],
            where="post",
            color="#000000",
            linewidth=1.2,
            label="Min/min Pareto frontier",
            zorder=3,
        )
    directly_labeled = [
        point for point in points
        if (
            not include_sensitivity_variants
            or point["pareto_efficient"]
        )
    ]
    placed_labels: list[dict[str, Any]] = []
    x_span = max(
        max(point["x_value"] for point in points)
        - min(point["x_value"] for point in points),
        1e-12,
    )
    y_span = max(
        max(point["y_value"] for point in points)
        - min(point["y_value"] for point in points),
        1e-12,
    )
    for index, point in enumerate(directly_labeled):
        close_count = sum(
            (
                abs(other["x_value"] - point["x_value"]) / x_span < 0.08
                and abs(other["y_value"] - point["y_value"]) / y_span < 0.08
            )
            for other in placed_labels
        )
        horizontalalignment = "left" if index % 2 == 0 else "right"
        vertical_offset = 7 + 16 * close_count
        axis.annotate(
            str(point["label"]),
            (point["x_value"], point["y_value"]),
            xytext=(
                7 if horizontalalignment == "left" else -7,
                vertical_offset,
            ),
            textcoords="offset points",
            horizontalalignment=horizontalalignment,
            verticalalignment="bottom",
            color=_TEXT_COLOR,
            fontsize=_FONT_SIZE,
            fontweight=(
                "bold" if point["pareto_efficient"] else "normal"
            ),
        )
        placed_labels.append(point)

    _x_section, _x_field, x_label, x_unit = (
        _TEA_RECORD_RENDER_METRICS[x_metric]
    )
    _y_section, _y_field, y_label, y_unit = (
        _TEA_RECORD_RENDER_METRICS[y_metric]
    )
    axis.set_xlabel(f"{x_label} ({x_unit})")
    axis.set_ylabel(f"{y_label} ({y_unit})")
    axis.margins(x=0.16, y=0.20)
    plot_title = "Admitted process-record Pareto frontier"
    fig.suptitle(plot_title, y=0.97)
    axis.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncols=2,
    )
    provenance_note = (
        "Exact admitted BioSTEAM cache records; both axes use each record's "
        "stored product basis and configuration. Min/min dominance is "
        "computed without interpolation or cross-record substitution."
    )
    if show_provenance_note:
        fig.text(
            0.5,
            0.055,
            "\n".join(textwrap.wrap(provenance_note, width=105)),
            horizontalalignment="center",
            verticalalignment="center",
            color=_TEXT_COLOR,
            fontsize=_FONT_SIZE,
        )
    fig.subplots_adjust(
        left=0.14,
        right=0.96,
        bottom=0.20 if show_provenance_note else 0.14,
        top=0.82,
    )
    path = _plot_path(
        _slug([
            "admitted_process_pareto",
            x_metric,
            y_metric,
            str(target_polymer or "all"),
            str(energy_case or "all_cases"),
        ]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "tea_pareto",
        "title": plot_title,
        "subtitle": (
            f"{x_label} versus {y_label}; lower is preferred on both axes"
        ),
        "caption": (
            "Points reproduce exact admitted process records. The frontier "
            "contains records for which no other selected record is no worse "
            "on both axes and strictly better on at least one axis."
        ),
        "result_kind": "admitted_process_record_pareto",
        "x_metric": x_metric,
        "x_metric_label": x_label,
        "x_metric_unit": x_unit,
        "y_metric": y_metric,
        "y_metric_label": y_label,
        "y_metric_unit": y_unit,
        "dominance": "minimize_x_and_minimize_y",
        "point_label_mode": (
            "energy-case records directly labeled"
            if not include_sensitivity_variants else
            "Pareto-efficient sensitivity selection directly labeled; "
            "all point labels retained in artifact data"
        ),
        "point_count": len(points),
        "points": copy.deepcopy(points),
        "frontier_record_ids": [
            point["record_id"] for point in frontier
        ],
        "frontier_points": frontier,
        "include_sensitivity_variants": include_sensitivity_variants,
        "target_polymer_selector": target_polymer,
        "energy_case_selector": energy_case,
        "provenance_note_shown": show_provenance_note,
        "provenance_note": (
            provenance_note if show_provenance_note else None
        ),
        "record_basis": stored.get("record_basis"),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "palette": list(_COLORS),
            "dominated_color": "#B7B7B7",
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
            "frontier_line": "black min/min step",
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution admitted process-record Pareto frontier: "
            f"{plot_path}. Axes: {x_label} ({x_unit}) and "
            f"{y_label} ({y_unit})."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Admitted process-record Pareto frontier",
        ),
        analysis_type="admitted_process_record_pareto",
        plot_type="tea_pareto",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="admitted_process_record_pareto",
        x_metric=x_metric,
        x_metric_unit=x_unit,
        y_metric=y_metric,
        y_metric_unit=y_unit,
        point_count=len(points),
        points=points,
        frontier_record_ids=[
            point["record_id"] for point in frontier
        ],
        frontier_points=frontier,
        record_basis=stored.get("record_basis"),
        provenance=copy.deepcopy(stored.get("provenance") or {}),
        model_basis="exact admitted BioSTEAM cache records",
    )


def _plot_tea_case_bars(
    state: Any,
    *,
    metric: str,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render one admitted metric across stored C1-C3 process records."""
    tool = "plot_analysis_results"
    stored = _stored_tea_records(state)
    if stored is None:
        return tool_error(
            tool,
            "No admitted process-record comparison is available.",
            error_code="missing_admitted_process_records",
        )
    if metric not in _TEA_RECORD_RENDER_METRICS:
        return tool_error(
            tool,
            f"Unsupported admitted-record metric: {metric}.",
            error_code="unsupported_tea_render_metric",
            available_metrics=list(_TEA_RECORD_RENDER_METRICS),
        )
    records = [
        record for record in stored["records"]
        if (
            record.get("record_group") == "energy_case"
            and str((record.get("config") or {}).get("energy_case") or "")
            in {"C1", "C2", "C3"}
            and _tea_record_metric(record, metric) is not None
        )
    ]
    if not records:
        return tool_error(
            tool,
            "The stored admitted records contain no C1-C3 values for the "
            f"requested {metric} metric.",
            error_code="missing_tea_case_values",
        )
    groups = list(dict.fromkeys(
        _tea_record_identity(record) for record in records
    ))
    energy_cases = [
        case for case in ("C1", "C2", "C3")
        if any(
            str((record.get("config") or {}).get("energy_case")) == case
            for record in records
        )
    ]
    rows = []
    by_group_case = {}
    for record in records:
        polymer, solvent = _tea_record_identity(record)
        case = str((record.get("config") or {}).get("energy_case"))
        value = _tea_record_metric(record, metric)
        if value is None:
            continue
        row = {
            "record_id": record.get("record_id"),
            "record_group": f"{polymer} / {solvent}",
            "polymer": polymer,
            "solvent": solvent,
            "energy_case": case,
            "metric": metric,
            "metric_value": value,
            "metric_unit": _TEA_RECORD_RENDER_METRICS[metric][3],
            "config": copy.deepcopy(record.get("config") or {}),
        }
        rows.append(row)
        by_group_case[((polymer, solvent), case)] = row

    _section, _field, metric_label, metric_unit = (
        _TEA_RECORD_RENDER_METRICS[metric]
    )
    plt = _pyplot()
    figure_width = max(8.6, 2.5 * len(groups) + 4.2)
    fig, axis = plt.subplots(figsize=(figure_width, 5.8))
    positions = list(range(len(groups)))
    width = 0.76 / max(len(energy_cases), 1)
    for case_index, case in enumerate(energy_cases):
        offset = (
            case_index - (len(energy_cases) - 1) / 2.0
        ) * width
        values = [
            (
                by_group_case.get((group, case), {})
                .get("metric_value", 0.0)
            )
            for group in groups
        ]
        bars = axis.bar(
            [position + offset for position in positions],
            values,
            width,
            label=case,
            color=_COLORS[case_index % len(_COLORS)],
            edgecolor="#000000",
            linewidth=0.5,
        )
        axis.bar_label(
            bars,
            labels=[
                _format_value(value)
                if (group, case) in by_group_case else ""
                for group, value in zip(groups, values)
            ],
            padding=3,
        )
    axis.set_xticks(
        positions,
        [f"{polymer}\n{solvent}" for polymer, solvent in groups],
    )
    axis.set_ylabel(f"{metric_label} ({metric_unit})")
    plot_title = "Admitted process records by energy case"
    fig.suptitle(plot_title)
    axis.legend(
        title="Energy case",
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncols=max(1, len(energy_cases)),
    )
    axis.margins(y=0.18)
    fig.subplots_adjust(
        left=0.16,
        right=0.96,
        bottom=0.17,
        top=0.82,
    )
    path = _plot_path(
        _slug(["admitted", metric, "energy_case_bars"]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "tea_case_bars",
        "title": "Admitted process-record comparison",
        "subtitle": f"{metric_label} across stored C1-C3 records",
        "caption": (
            "Grouped bars reproduce exact admitted BioSTEAM cache records. "
            "Each group is one stored target-polymer/solvent process "
            "configuration; values are not interpolated between records."
        ),
        "result_kind": "admitted_process_record_comparison",
        "metric": metric,
        "metric_label": metric_label,
        "metric_unit": metric_unit,
        "record_group_basis": "target polymer and solvent",
        "energy_cases": energy_cases,
        "comparison_rows": rows,
        "record_basis": stored.get("record_basis"),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "palette": list(_COLORS),
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution admitted-record case bars: {plot_path}. "
            f"Metric: {metric_label} ({metric_unit}); exact cache basis."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Admitted process-record comparison",
        ),
        analysis_type="admitted_process_record_comparison",
        plot_type="tea_case_bars",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="admitted_process_record_comparison",
        metric=metric,
        metric_unit=metric_unit,
        comparison_rows=rows,
        energy_cases=energy_cases,
        record_basis=stored.get("record_basis"),
        provenance=copy.deepcopy(stored.get("provenance") or {}),
        model_basis="exact admitted BioSTEAM cache records",
    )


_SENSITIVITY_LEVEL_ORDER = {
    "low": 0,
    "mid": 1,
    "high": 2,
}


def _plot_tea_sensitivity_tornado(
    state: Any,
    *,
    metric: str,
    target_polymer: str | None,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render admitted sensitivity endpoints as deltas from the C1 baseline."""
    tool = "plot_analysis_results"
    stored = _stored_tea_records(state)
    if stored is None:
        return tool_error(
            tool,
            "No admitted process-record comparison is available.",
            error_code="missing_admitted_process_records",
        )
    if metric not in _TEA_RECORD_RENDER_METRICS:
        return tool_error(
            tool,
            f"Unsupported admitted-record metric: {metric}.",
            error_code="unsupported_tea_render_metric",
            available_metrics=list(_TEA_RECORD_RENDER_METRICS),
        )
    sensitivity_records = [
        record for record in stored["records"]
        if (
            record.get("record_group") == "sensitivity"
            and record.get("sensitivity_axis")
            and _tea_record_metric(record, metric) is not None
        )
    ]
    available_polymers = list(dict.fromkeys(
        _tea_record_identity(record)[0] for record in sensitivity_records
    ))
    selected_polymer = (
        thermo.resolve_polymer(str(target_polymer))
        if target_polymer else (
            available_polymers[0]
            if len(available_polymers) == 1 else None
        )
    )
    if selected_polymer is None:
        return tool_error(
            tool,
            (
                "Select one target polymer for the admitted sensitivity "
                "tornado."
            ),
            error_code=(
                "unknown_sensitivity_target"
                if target_polymer else "ambiguous_sensitivity_target"
            ),
            available_target_polymers=available_polymers,
        )
    selected = [
        record for record in sensitivity_records
        if _tea_record_identity(record)[0] == selected_polymer
    ]
    if not selected:
        return tool_error(
            tool,
            "No admitted sensitivity records are stored for "
            f"{selected_polymer}.",
            error_code="missing_tea_sensitivity_values",
            available_target_polymers=available_polymers,
        )
    solvents = list(dict.fromkeys(
        _tea_record_identity(record)[1] for record in selected
    ))
    if len(solvents) != 1:
        return tool_error(
            tool,
            "The selected sensitivity records do not share one solvent basis.",
            error_code="ambiguous_sensitivity_solvent_basis",
            stored_solvents=solvents,
        )
    solvent = solvents[0]
    baseline = next((
        record for record in stored["records"]
        if (
            record.get("record_group") == "energy_case"
            and _tea_record_identity(record)
            == (selected_polymer, solvent)
            and str((record.get("config") or {}).get("energy_case")) == "C1"
        )
    ), None)
    baseline_value = (
        _tea_record_metric(baseline, metric)
        if isinstance(baseline, dict) else None
    )
    if baseline_value is None:
        return tool_error(
            tool,
            "The matching C1 baseline is absent for the stored sensitivity "
            "records.",
            error_code="missing_tea_sensitivity_baseline",
        )
    records_by_axis: dict[str, list[dict[str, Any]]] = {}
    for record in selected:
        records_by_axis.setdefault(
            str(record["sensitivity_axis"]),
            [],
        ).append(record)
    rows = []
    for axis, axis_records in records_by_axis.items():
        ordered = sorted(
            axis_records,
            key=lambda record: (
                _SENSITIVITY_LEVEL_ORDER.get(
                    str(record.get("sensitivity_level") or ""),
                    99,
                ),
                str(record.get("record_id") or ""),
            ),
        )
        if len(ordered) < 2:
            continue
        lower, upper = ordered[0], ordered[-1]
        lower_value = _tea_record_metric(lower, metric)
        upper_value = _tea_record_metric(upper, metric)
        if lower_value is None or upper_value is None:
            continue
        rows.append({
            "sensitivity_axis": axis,
            "baseline_record_id": baseline.get("record_id"),
            "baseline_value": baseline_value,
            "lower_record_id": lower.get("record_id"),
            "lower_level": lower.get("sensitivity_level"),
            "lower_value": lower_value,
            "lower_delta": lower_value - baseline_value,
            "upper_record_id": upper.get("record_id"),
            "upper_level": upper.get("sensitivity_level"),
            "upper_value": upper_value,
            "upper_delta": upper_value - baseline_value,
        })
    if not rows:
        return tool_error(
            tool,
            "No admitted sensitivity axis has two stored endpoint records.",
            error_code="incomplete_tea_sensitivity_axes",
        )
    rows.sort(
        key=lambda row: max(
            abs(float(row["lower_delta"])),
            abs(float(row["upper_delta"])),
        ),
        reverse=True,
    )

    _section, _field, metric_label, metric_unit = (
        _TEA_RECORD_RENDER_METRICS[metric]
    )
    plt = _pyplot()
    fig, axis = plt.subplots(
        figsize=(10.0, max(5.2, 0.62 * len(rows) + 2.4)),
    )
    positions = list(range(len(rows)))
    lower_bars = axis.barh(
        [position - 0.18 for position in positions],
        [float(row["lower_delta"]) for row in rows],
        0.34,
        color=_COLORS[0],
        edgecolor="#000000",
        linewidth=0.5,
        label="Lower admitted endpoint",
    )
    upper_bars = axis.barh(
        [position + 0.18 for position in positions],
        [float(row["upper_delta"]) for row in rows],
        0.34,
        color=_COLORS[1],
        edgecolor="#000000",
        linewidth=0.5,
        label="Upper admitted endpoint",
    )
    axis.set_yticks(
        positions,
        [
            str(row["sensitivity_axis"]).replace("_", " ")
            for row in rows
        ],
    )
    axis.invert_yaxis()
    axis.axvline(
        0.0,
        color="#000000",
        linewidth=1.2,
        linestyle="--",
        label="C1 baseline",
    )
    axis.bar_label(
        lower_bars,
        labels=[
            f"{row['lower_level']} {_format_value(row['lower_delta'])}"
            for row in rows
        ],
        padding=3,
    )
    axis.bar_label(
        upper_bars,
        labels=[
            f"{row['upper_level']} {_format_value(row['upper_delta'])}"
            for row in rows
        ],
        padding=3,
    )
    axis.set_xlabel(
        f"Change in {metric_label} from C1 baseline ({metric_unit})",
    )
    plot_title = (
        f"{selected_polymer} / {solvent} admitted sensitivity tornado"
    )
    fig.suptitle(plot_title)
    axis.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, 1.01),
        ncols=3,
    )
    delta_values = [
        float(row[key])
        for row in rows
        for key in ("lower_delta", "upper_delta")
    ]
    span_min = min([0.0, *delta_values])
    span_max = max([0.0, *delta_values])
    span = max(span_max - span_min, 1e-12)
    axis.set_xlim(
        span_min - 0.28 * span,
        span_max + 0.28 * span,
    )
    fig.subplots_adjust(
        left=0.27,
        right=0.94,
        bottom=0.16,
        top=0.82,
    )
    path = _plot_path(
        _slug([
            selected_polymer,
            solvent,
            metric,
            "sensitivity_tornado",
        ]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "tea_sensitivity_tornado",
        "title": "Admitted process-record sensitivity",
        "subtitle": (
            f"{selected_polymer} / {solvent}; {metric_label} relative to C1"
        ),
        "caption": (
            "Each bar reproduces one admitted sensitivity endpoint as a "
            "difference from the matching stored C1 baseline. The chart does "
            "not interpolate between endpoints."
        ),
        "result_kind": "admitted_process_sensitivity",
        "target_polymer": selected_polymer,
        "solvent": solvent,
        "metric": metric,
        "metric_label": metric_label,
        "metric_unit": metric_unit,
        "baseline_record_id": baseline.get("record_id"),
        "baseline_metric_value": baseline_value,
        "sensitivity_rows": rows,
        "record_basis": stored.get("record_basis"),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "palette": [_COLORS[0], _COLORS[1]],
            "baseline_color": _TEXT_COLOR,
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution admitted sensitivity tornado: {plot_path}. "
            f"Baseline: {baseline_value:.6g} {metric_unit} from "
            f"{baseline.get('record_id')}."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Admitted process-record sensitivity",
        ),
        analysis_type="admitted_process_sensitivity",
        plot_type="tea_sensitivity_tornado",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="admitted_process_sensitivity",
        target_polymer=selected_polymer,
        solvents=[solvent],
        metric=metric,
        metric_unit=metric_unit,
        baseline_record_id=baseline.get("record_id"),
        baseline_metric_value=baseline_value,
        sensitivity_rows=rows,
        record_basis=stored.get("record_basis"),
        provenance=copy.deepcopy(stored.get("provenance") or {}),
        model_basis="exact admitted BioSTEAM cache records",
    )


_CONTAMINANT_HEATMAP_CATEGORIES = (
    {
        "name": "not_evaluated",
        "code": 0,
        "label": "N/E",
        "color": "#D9D9D9",
    },
    {
        "name": "fail",
        "code": 1,
        "label": "FAIL",
        "color": "#F2A17B",
    },
    {
        "name": "pass",
        "code": 2,
        "label": "PASS",
        "color": "#7FD1B9",
    },
)
_CONTAMINANT_HEATMAP_CRITERIA = (
    ("miscibility_pass", "Hot-phase miscibility"),
    (
        "precipitation_regime_pass",
        "Cooling-regime miscibility",
    ),
    ("logd_pass", "Positive workbook logD"),
)


def _contaminant_heatmap_color_model() -> tuple[Any, Any]:
    """Return the fixed three-category map; no continuous scale is admitted."""
    from matplotlib.colors import BoundaryNorm, ListedColormap

    colormap = ListedColormap([
        str(item["color"]) for item in _CONTAMINANT_HEATMAP_CATEGORIES
    ])
    return colormap, BoundaryNorm(
        [-0.5, 0.5, 1.5, 2.5],
        colormap.N,
        clip=True,
    )


def _contaminant_heatmap_category(
    family: dict[str, Any],
    criterion: str,
) -> dict[str, Any]:
    records = [
        item for item in family.get("criteria_records") or []
        if isinstance(item, dict)
    ]
    evaluated = any(
        item.get("evidence_status") == "evaluated"
        and (
            criterion != "precipitation_regime_pass"
            or item.get(criterion) is not None
        )
        for item in records
    )
    category_name = (
        "not_evaluated"
        if not evaluated else "pass"
        if family.get(criterion) is True else "fail"
    )
    return next(
        item for item in _CONTAMINANT_HEATMAP_CATEGORIES
        if item["name"] == category_name
    )


def _plot_contaminant_criteria_heatmap(
    state: Any,
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render exact family-level criteria with solid categorical colors."""
    tool = "plot_analysis_results"
    stored = _stored_analysis_result(state)
    if (
        not isinstance(stored, dict)
        or stored.get("analysis_type") != "contaminant_screen_analysis"
    ):
        return tool_error(
            tool,
            "No admitted contaminant screen is available.",
            error_code="missing_contaminant_analysis_result",
        )
    stages = [
        copy.deepcopy(item)
        for item in stored.get("stage_records") or []
        if isinstance(item, dict) and item.get("family_records")
    ]
    if not stages:
        return tool_error(
            tool,
            "The admitted contaminant screen has no family-stage criteria.",
            error_code="contaminant_analysis_not_plottable",
        )
    families = list(dict.fromkeys(
        str(family["contaminant_family"])
        for stage in stages
        for family in stage.get("family_records") or []
        if family.get("contaminant_family")
    ))
    if not families:
        return tool_error(
            tool,
            "The admitted contaminant screen has no family identities.",
            error_code="contaminant_analysis_not_plottable",
        )
    criterion_rows = [
        {
            "contaminant_family": family,
            "criterion": criterion,
            "label": f"{family} — {criterion_label}",
        }
        for family in families
        for criterion, criterion_label in _CONTAMINANT_HEATMAP_CRITERIA
    ]
    matrix: list[list[int]] = []
    category_matrix: list[list[str]] = []
    annotation_matrix: list[list[str]] = []
    for row in criterion_rows:
        codes, categories, annotations = [], [], []
        for stage in stages:
            family = next(
                (
                    item for item in stage.get("family_records") or []
                    if item.get("contaminant_family")
                    == row["contaminant_family"]
                ),
                {},
            )
            category = _contaminant_heatmap_category(
                family,
                str(row["criterion"]),
            )
            codes.append(int(category["code"]))
            categories.append(str(category["name"]))
            annotations.append(str(category["label"]))
        matrix.append(codes)
        category_matrix.append(categories)
        annotation_matrix.append(annotations)

    from matplotlib.patches import Patch

    plt = _pyplot()
    fig, axis = plt.subplots(
        figsize=(
            max(8.8, 1.7 * len(stages) + 5.4),
            max(6.2, 0.60 * len(criterion_rows) + 2.8),
        ),
    )
    colormap, normalization = _contaminant_heatmap_color_model()
    axis.imshow(
        matrix,
        cmap=colormap,
        norm=normalization,
        aspect="auto",
        interpolation="nearest",
    )
    stage_labels = []
    stage_columns = []
    for index, stage in enumerate(stages, start=1):
        stage_number = stage.get("stage_index")
        stage_label = (
            f"Stage {stage_number}"
            if stage_number is not None else
            f"Condition {stage.get('condition_index') or index}"
        )
        solvent = str(
            stage.get("route_solvent")
            or thermo.canonical_solvent_name(str(stage.get("solvent") or ""))
        )
        operating = _finite(stage.get("operating_temperature_c"))
        route_temperature = _finite(stage.get("route_temperature_c"))
        temperature_label = (
            f"{operating:g} °C"
            if operating is not None else "no feasible window"
        )
        stage_labels.append(
            f"{stage_label}\n{solvent}\n{temperature_label}"
        )
        stage_columns.append({
            "stage_index": stage_number,
            "stage_label": stage_label,
            "stage_binding": stage.get("stage_binding"),
            "mode": stage.get("mode"),
            "solvent": stage.get("solvent"),
            "route_solvent": stage.get("route_solvent"),
            "operating_temperature_c": operating,
            "route_temperature_c": route_temperature,
            "precipitation_temperature_c": stage.get(
                "precipitation_temperature_c",
            ),
            "polymer_context": copy.deepcopy(
                stage.get("polymer_context") or {},
            ),
        })
    axis.set_xticks(range(len(stages)), stage_labels)
    axis.set_yticks(
        range(len(criterion_rows)),
        [row["label"] for row in criterion_rows],
    )
    for row_index, annotations in enumerate(annotation_matrix):
        for column_index, label in enumerate(annotations):
            axis.text(
                column_index,
                row_index,
                label,
                ha="center",
                va="center",
                color=_TEXT_COLOR,
                fontsize=_FONT_SIZE,
                fontweight="bold",
            )
    axis.set_xlabel("Stored route stage and screen condition")
    axis.set_ylabel("Contaminant family and criterion")
    axis.grid(False)
    axis.tick_params(length=0)
    plot_title = "Contaminant criteria by stored route stage"
    fig.suptitle(plot_title, y=0.98)
    axis.legend(
        handles=[
            Patch(
                facecolor=str(item["color"]),
                edgecolor="#000000",
                linewidth=0.6,
                label=(
                    "Not evaluated"
                    if item["name"] == "not_evaluated" else
                    str(item["name"]).title()
                ),
            )
            for item in _CONTAMINANT_HEATMAP_CATEGORIES
        ],
        loc="upper center",
        bbox_to_anchor=(0.5, -0.18),
        ncols=3,
    )
    fig.subplots_adjust(
        left=0.34,
        right=0.97,
        bottom=0.25,
        top=0.82,
    )
    path = _plot_path(
        _slug([
            "contaminant_criteria",
            str(stored.get("target_polymer") or "screen"),
            *[
                str(stage.get("route_solvent") or stage.get("solvent") or "")
                for stage in stages
            ],
        ]),
        output_dir,
        output_path,
    )
    category_encoding = [
        copy.deepcopy(item) for item in _CONTAMINANT_HEATMAP_CATEGORIES
    ]
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "contaminant_criteria_heatmap",
        "title": plot_title,
        "subtitle": (
            f"Target polymer: {stored.get('target_polymer')}; "
            f"mode: {str(stored.get('mode') or '').replace('_', ' ')}"
        ),
        "caption": (
            "Solid colors encode family-wide pass, fail, or not-evaluated "
            "categories without a within-category gradient. Miscibility and "
            "logD reproduce the admitted Zhou workbook screen; polymer wt% "
            "and cooling behavior are fitted-model screening context, not "
            "validated removal efficiency, recovery, or purity."
        ),
        "result_kind": "contaminant_criteria",
        "target_polymer": stored.get("target_polymer"),
        "other_polymers": copy.deepcopy(
            stored.get("other_polymers") or [],
        ),
        "requested_contaminants": copy.deepcopy(
            stored.get("requested_contaminants") or [],
        ),
        "contaminant_families": families,
        "unsupported_contaminants": copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        "criterion_rows": criterion_rows,
        "stage_columns": stage_columns,
        "category_matrix": category_matrix,
        "category_encoding": category_encoding,
        "source_stage_records": stages,
        "criterion_record_count": stored.get(
            "criterion_record_count",
        ),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "encoding": "solid_categorical_colors_no_gradients",
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    compact_provenance = {
        key: (stored.get("provenance") or {}).get(key)
        for key in (
            "source_dataset", "thermodynamic_basis", "evidence_class",
        )
        if (stored.get("provenance") or {}).get(key) is not None
    }
    return tool_success(
        tool,
        display=(
            f"Full-resolution contaminant criteria heatmap: {plot_path}. "
            f"{len(families)} families across {len(stages)} stored stages."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Contaminant criteria by stored route stage",
        ),
        analysis_type="contaminant_criteria_plot",
        plot_type="contaminant_criteria_heatmap",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="contaminant_criteria",
        target_polymer=stored.get("target_polymer"),
        other_polymers=copy.deepcopy(
            stored.get("other_polymers") or [],
        ),
        requested_contaminants=copy.deepcopy(
            stored.get("requested_contaminants") or [],
        ),
        contaminant_families=families,
        unsupported_contaminants=copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        stage_count=len(stages),
        family_record_count=stored.get("family_record_count"),
        criterion_record_count=stored.get("criterion_record_count"),
        labels=[row["label"] for row in criterion_rows],
        values=category_matrix,
        provenance=compact_provenance,
        warnings=[
            "The artifact reproduces screening evidence and does not "
            "establish contaminant removal efficiency, recovery, or purity.",
        ],
        model_basis=(
            "stored admitted contaminant screen with exact route-stage "
            "identity binding"
        ),
    )


def _plot_contaminant_screen_card(
    state: Any,
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render typed route-stage contaminant decisions as styled cards."""
    tool = "plot_analysis_results"
    stored = _stored_analysis_result(state)
    if (
        not isinstance(stored, dict)
        or stored.get("analysis_type") != "contaminant_screen_analysis"
    ):
        return tool_error(
            tool,
            "No admitted contaminant screen is available.",
            error_code="missing_contaminant_analysis_result",
        )
    stages = [
        copy.deepcopy(item)
        for item in stored.get("stage_records") or []
        if isinstance(item, dict) and item.get("family_records")
    ]
    if not stages:
        return tool_error(
            tool,
            "The admitted contaminant screen has no stage decisions.",
            error_code="contaminant_analysis_not_plottable",
        )

    def criterion_status(
        family: dict[str, Any],
        field: str,
    ) -> tuple[str, str]:
        evidence = {
            str(item.get("evidence_status") or "")
            for item in family.get("criteria_records") or []
            if isinstance(item, dict)
        }
        if "evaluated" not in evidence:
            return "N/E", "#D9D9D9"
        value = family.get(field)
        if value is None:
            return "N/A", "#D9D9D9"
        return (
            ("PASS", "#7FD1B9")
            if value is True else ("FAIL", "#F2A17B")
        )

    plt = _pyplot()
    from matplotlib.patches import FancyBboxPatch

    fig, axes = plt.subplots(
        1,
        len(stages),
        figsize=(max(6.2, 6.2 * len(stages)), 8.2),
        squeeze=False,
    )
    fig.suptitle("Contaminant screen", fontweight="bold", y=0.97)
    card_rows = []
    for position, (axis, stage) in enumerate(
        zip(axes[0], stages),
        start=1,
    ):
        axis.set_xlim(0.0, 1.0)
        axis.set_ylim(0.0, 1.0)
        axis.axis("off")
        axis.add_patch(FancyBboxPatch(
            (0.015, 0.01), 0.97, 0.965,
            boxstyle="round,pad=0.012,rounding_size=0.02",
            linewidth=0.9,
            edgecolor="#777777",
            facecolor="#F8FAFC",
        ))

        def panel(y: float, height: float, color: str) -> None:
            axis.add_patch(FancyBboxPatch(
                (0.045, y), 0.91, height,
                boxstyle="round,pad=0.008,rounding_size=0.012",
                linewidth=0.6,
                edgecolor="#A0A0A0",
                facecolor=color,
            ))

        def label(
            x: float, y: float, text: str, *,
            weight: str = "normal",
            horizontalalignment: str = "left",
            box: dict[str, Any] | None = None,
        ) -> None:
            axis.text(
                x, y, text,
                color=_TEXT_COLOR,
                fontsize=_FONT_SIZE,
                fontweight=weight,
                horizontalalignment=horizontalalignment,
                verticalalignment="center",
                bbox=box,
            )

        stage_number = stage.get("stage_index")
        stage_label = (
            f"Stage {stage_number}"
            if stage_number is not None
            else f"Condition {stage.get('condition_index') or position}"
        )
        solvent = str(
            stage.get("route_solvent")
            or thermo.canonical_solvent_name(
                str(stage.get("solvent") or ""),
            )
            or stage.get("solvent")
            or "Unknown solvent"
        )
        mode = str(stage.get("mode") or stored.get("mode") or "").replace(
            "_", " ",
        )
        operating = _finite(stage.get("operating_temperature_c"))
        precipitation = _finite(
            stage.get("precipitation_temperature_c"),
        )
        panel(0.79, 0.16, "#EAF2F8")
        label(
            0.07, 0.915,
            f"{stage_label} · {solvent}",
            weight="bold",
        )
        label(0.07, 0.87, f"Mode: {mode or 'not reported'}")
        condition = (
            f"Operate {_format_value(operating)} °C"
            if operating is not None else "No feasible process window"
        )
        if precipitation is not None:
            condition += f" · precipitate {_format_value(precipitation)} °C"
        label(0.07, 0.82, condition)

        polymer_context = dict(stage.get("polymer_context") or {})
        target = dict(polymer_context.get("target") or {})
        other_rows = [
            dict(item) for item in polymer_context.get("others") or []
            if isinstance(item, dict)
        ]
        panel(0.625, 0.135, "#F3F7FB")
        label(0.07, 0.73, "POLYMER CONTEXT", weight="bold")
        target_value = _finite(target.get("solubility_wt_pct"))
        label(
            0.07, 0.685,
            f"Target {target.get('polymer') or stored.get('target_polymer')}: "
            + (
                f"{_format_value(target_value)} wt%"
                if target_value is not None else
                str(target.get("status") or "not evaluated").replace("_", " ")
            ),
        )
        label(
            0.07, 0.645,
            "Others: " + (
                ", ".join(
                    f"{item.get('polymer')} "
                    + (
                        f"{_format_value(item.get('solubility_wt_pct'))} wt%"
                        if _finite(item.get("solubility_wt_pct")) is not None
                        else str(item.get("status") or "N/E").replace("_", " ")
                    )
                    for item in other_rows
                )
                or "none"
            ),
        )

        family_rows = [
            dict(item) for item in stage.get("family_records") or []
            if isinstance(item, dict)
        ]
        family_height = 0.19
        family_gap = 0.015
        top = 0.615
        rendered_families = []
        for family_index, family in enumerate(family_rows):
            y = top - (family_index + 1) * family_height - (
                family_index * family_gap
            )
            panel(y, family_height - 0.01, "#F6F8FA")
            family_name = str(
                family.get("contaminant_family") or "Unknown family",
            )
            label(
                0.07, y + family_height - 0.045,
                f"{family_name} · "
                f"{family.get('contaminant_count') or 0} compounds",
                weight="bold",
            )
            logd_min = _finite(family.get("logd_min"))
            logd_max = _finite(family.get("logd_max"))
            label(
                0.07, y + family_height - 0.09,
                "logD range: "
                + (
                    f"{_format_value(logd_min)} to {_format_value(logd_max)}"
                    if logd_min is not None and logd_max is not None
                    else "not evaluated"
                ),
            )
            criteria = []
            for chip_index, (field, chip_name) in enumerate((
                ("miscibility_pass", "MISC"),
                ("precipitation_regime_pass", "PRECIP"),
                ("logd_pass", "logD"),
            )):
                status, color = criterion_status(family, field)
                label(
                    0.16 + chip_index * 0.32,
                    y + 0.035,
                    f"{chip_name} {status}",
                    weight="bold",
                    horizontalalignment="center",
                    box={
                        "boxstyle": "round,pad=0.3",
                        "facecolor": color,
                        "edgecolor": "#606060",
                    },
                )
                criteria.append({
                    "criterion": field,
                    "status": status,
                    "color": color,
                })
            rendered_families.append({
                "contaminant_family": family_name,
                "contaminant_count": family.get("contaminant_count"),
                "logd_min": logd_min,
                "logd_max": logd_max,
                "criteria": criteria,
                "criteria_records": copy.deepcopy(
                    family.get("criteria_records") or [],
                ),
            })

        panel(0.02, 0.175, "#FFF7E6")
        unsupported = [
            str(item)
            for item in stored.get("unsupported_contaminants") or []
        ]
        provenance = dict(stored.get("provenance") or {})
        label(
            0.07, 0.17,
            "UNSUPPORTED: "
            + (", ".join(unsupported) if unsupported else "none reported"),
            weight="bold",
        )
        label(
            0.07, 0.115,
            "\n".join(textwrap.wrap(
                "Evidence: "
                f"{str(provenance.get('evidence_class') or 'not reported').replace('_', ' ')}"
                f" · {provenance.get('source_dataset') or 'source not reported'}",
                width=58,
            )),
        )
        label(
            0.07, 0.05,
            "\n".join(textwrap.wrap(
                "Screening evidence; not validated removal efficiency, "
                "recovery, or purity.",
                width=58,
            )),
        )
        card_rows.append({
            "stage_index": stage_number,
            "stage_label": stage_label,
            "stage_binding": stage.get("stage_binding"),
            "mode": stage.get("mode"),
            "solvent": stage.get("solvent"),
            "route_solvent": stage.get("route_solvent"),
            "operating_temperature_c": operating,
            "precipitation_temperature_c": precipitation,
            "polymer_context": copy.deepcopy(polymer_context),
            "families": rendered_families,
            "caveats": copy.deepcopy(stage.get("caveats") or []),
        })

    fig.subplots_adjust(
        left=0.025,
        right=0.975,
        bottom=0.04,
        top=0.92,
        wspace=0.08,
    )
    path = _plot_path(
        _slug([
            "contaminant_screen_cards",
            str(stored.get("target_polymer") or "target"),
            *(str(item["solvent"]) for item in card_rows),
        ]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "contaminant_screen_card",
        "title": "Contaminant screen by route stage",
        "subtitle": (
            f"Target polymer: {stored.get('target_polymer')}; "
            f"{len(card_rows)} stage or condition card(s)"
        ),
        "caption": (
            "Each card reproduces typed contaminant-family criteria, logD "
            "bounds, and polymer wt% context for one admitted stage or screen "
            "condition. Solid colors are categorical. Unsupported identities "
            "and non-evaluated criteria remain explicit."
        ),
        "result_kind": "contaminant_screen_cards",
        "target_polymer": stored.get("target_polymer"),
        "other_polymers": copy.deepcopy(
            stored.get("other_polymers") or [],
        ),
        "stage_count": len(card_rows),
        "cards": card_rows,
        "unsupported_contaminants": copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        "criterion_record_count": stored.get(
            "criterion_record_count",
        ),
        "provenance": copy.deepcopy(stored.get("provenance") or {}),
        "publication_treatment": {
            "encoding": "solid_categorical_colors_no_gradients",
            "pass_color": "#7FD1B9",
            "fail_color": "#F2A17B",
            "not_evaluated_color": "#D9D9D9",
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    compact_provenance = {
        key: (stored.get("provenance") or {}).get(key)
        for key in (
            "source_dataset", "thermodynamic_basis", "evidence_class",
        )
        if (stored.get("provenance") or {}).get(key) is not None
    }
    return tool_success(
        tool,
        display=(
            f"Full-resolution contaminant screen cards: {plot_path}. "
            f"{len(card_rows)} stored stage or condition cards."
        ),
        artifact=_artifact(
            plot_path,
            sidecar_path,
            "Contaminant screen by route stage",
        ),
        analysis_type="contaminant_screen_card_plot",
        plot_type="contaminant_screen_card",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="contaminant_screen_cards",
        target_polymer=stored.get("target_polymer"),
        stage_count=len(card_rows),
        family_record_count=sum(
            len(item["families"]) for item in card_rows
        ),
        criterion_record_count=stored.get(
            "criterion_record_count",
        ),
        unsupported_contaminants=copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        cards=card_rows,
        provenance=compact_provenance,
        model_basis=(
            "typed contaminant screen joined to stored route stages by "
            "canonical solvent identity"
        ),
    )


_PARTITION_FAMILY_MARKERS = ("o", "s", "^", "D", "v", "P", "X")

# The scope vocabulary is a closed registry keyed by the records' typed
# binding.  A map whose facets are screened conditions must never present
# them as route stages: the title, the count key, and the scope summary
# the answer binds all derive from this registry, not from row order.
_CONTAMINANT_PARTITION_SCOPES = {
    "route_stages": {
        "scope_kind": "route_stages",
        "count_key": "stage_count",
        "singular": "route stage",
        "plural": "route stages",
        "title": "Contaminant partitioning by route stage",
    },
    "screened_conditions": {
        "scope_kind": "screened_conditions",
        "count_key": "condition_count",
        "singular": "screened condition",
        "plural": "screened conditions",
        "title": "Contaminant partitioning by screened condition",
    },
}


def _contaminant_partition_scope(
    records: Sequence[dict[str, Any]],
) -> dict[str, str]:
    """Classify the plotted records from their typed binding structure."""
    route_bound = bool(records) and all(
        record.get("stage_binding") == "stored_route"
        and type(record.get("stage_index")) is int
        for record in records
    )
    key = "route_stages" if route_bound else "screened_conditions"
    return dict(_CONTAMINANT_PARTITION_SCOPES[key])


def _partition_stage_label(stage: dict[str, Any], position: int) -> str:
    """Name a facet from typed state, never from row order alone."""
    number = stage.get("stage_index")
    if stage.get("stage_binding") == "stored_route" and number is not None:
        return f"Stage {number}"
    return f"Condition {stage.get('condition_index') or position}"


def _partition_polymer_line(stage: dict[str, Any]) -> str:
    """One line of polymer-solubility context for a facet."""
    context = stage.get("polymer_context") or {}
    target = context.get("target") or {}
    parts = []
    if target.get("polymer"):
        parts.append(
            f"{target['polymer']} "
            f"{_format_value(target.get('solubility_wt_pct'))} wt% "
            f"({str(target.get('status') or 'unknown').replace('_', ' ')})"
        )
    for other in context.get("others") or []:
        if other.get("polymer"):
            parts.append(
                f"{other['polymer']} "
                f"{_format_value(other.get('solubility_wt_pct'))} wt%"
            )
    return " · ".join(parts) or "No polymer context retained."


def _plot_contaminant_partitioning_map(
    state: Any,
    *,
    output_dir: Optional[str],
    output_path: Optional[str],
) -> str:
    """Render per-compound logD against the STRAP pass boundary, by stage.

    The family heatmap reports one verdict per family, collapsing 26 PFAS
    into a single cell, so a family that fails as a whole hides which
    compounds sat just under the boundary and which were nowhere near it.
    This view is the per-compound counterpart and reads the same stored
    records the heatmap reads — ``criteria_records`` — so there is exactly
    one source of truth for a compound's logD and verdict.
    """
    tool = "plot_analysis_results"
    stored = _stored_analysis_result(state)
    if (
        not isinstance(stored, dict)
        or stored.get("analysis_type") != "contaminant_screen_analysis"
    ):
        return tool_error(
            tool,
            "No admitted contaminant screen is available.",
            error_code="missing_contaminant_analysis_result",
        )
    stages = [
        copy.deepcopy(item)
        for item in stored.get("stage_records") or []
        if isinstance(item, dict) and item.get("family_records")
    ]
    if not stages:
        return tool_error(
            tool,
            "The admitted contaminant screen has no stage decisions.",
            error_code="contaminant_analysis_not_plottable",
        )
    scope = _contaminant_partition_scope(stages)
    scope_count = len(stages)
    scope_noun = scope["singular"] if scope_count == 1 else scope["plural"]
    scope_summary = (
        f"Target polymer: {stored.get('target_polymer')}; "
        f"{scope['singular']} count: {scope_count}."
    )

    from . import contaminants as contaminant_module

    criterion = contaminant_module.CONTAMINANT_LOGD_CRITERION
    families = [
        str(item) for item in stored.get("contaminant_families") or []
    ]
    marker_by_family = {
        family: _PARTITION_FAMILY_MARKERS[
            index % len(_PARTITION_FAMILY_MARKERS)
        ]
        for index, family in enumerate(families)
    }
    pass_color, fail_color, absent_color = "#7FD1B9", "#F2A17B", "#D9D9D9"

    # ---- typed facet assembly (no prose, no reordering by chance) --------
    facets: list[dict[str, Any]] = []
    for position, stage in enumerate(stages, start=1):
        points: list[dict[str, Any]] = []
        band_centers: list[tuple[str, float]] = []
        cursor = 0.0
        for family_record in stage.get("family_records") or []:
            family = str(family_record.get("contaminant_family") or "")
            records = list(family_record.get("criteria_records") or [])
            # Vertical position is family grouping and within-family logD
            # rank, so it carries information; random jitter would not.
            ordered = sorted(
                records,
                key=lambda item: (
                    item.get("logd") is None,
                    item.get("logd") if item.get("logd") is not None else 0.0,
                    str(item.get("contaminant") or ""),
                ),
            )
            band_start = cursor
            for record in ordered:
                evaluated = record.get("evidence_status") == "evaluated"
                points.append({
                    "contaminant": str(record.get("contaminant") or ""),
                    "contaminant_family": family,
                    "logd": record.get("logd"),
                    "miscibility_pass": record.get("miscibility_pass"),
                    "precipitation_regime_pass": record.get(
                        "precipitation_regime_pass",
                    ),
                    "logd_pass": record.get("logd_pass"),
                    "passes": bool(record.get("passes")),
                    "evidence_status": str(
                        record.get("evidence_status") or "",
                    ),
                    "marker": marker_by_family.get(family, "o"),
                    "color": (
                        absent_color if not evaluated
                        else pass_color if record.get("passes")
                        else fail_color
                    ),
                    "y": cursor,
                })
                cursor += 1.0
            if ordered:
                band_centers.append(
                    (family, (band_start + cursor - 1.0) / 2.0),
                )
            cursor += 1.0
        evaluated_points = [
            item for item in points
            if item["evidence_status"] == "evaluated"
            and isinstance(item["logd"], (int, float))
        ]
        # An empty facet must state the reason the STATE gives, not a
        # generic "no data": the distinction between "screened and nothing
        # passed" and "never screened" is the whole point.
        statuses = {
            item["evidence_status"] for item in points if item["evidence_status"]
        }
        if evaluated_points:
            evidence_status = "evaluated"
            evidence_reason = ""
        else:
            evidence_status = (
                sorted(statuses)[0] if statuses else "not_evaluated"
            )
            evidence_reason = (
                "Not evaluated: no process window at this condition."
                if evidence_status == "not_evaluated_no_process_window"
                else f"Not evaluated: {evidence_status.replace('_', ' ')}."
            )
        facets.append({
            "stage_label": _partition_stage_label(stage, position),
            "stage_binding": stage.get("stage_binding"),
            "solvent": stage.get("solvent"),
            "operating_temperature_c": stage.get("operating_temperature_c"),
            "precipitation_temperature_c": stage.get(
                "precipitation_temperature_c",
            ),
            "passes": bool(stage.get("passes")),
            "polymer_context": copy.deepcopy(
                stage.get("polymer_context") or {},
            ),
            "polymer_context_line": _partition_polymer_line(stage),
            "evidence_status": evidence_status,
            "evidence_reason": evidence_reason,
            "band_centers": [
                {"contaminant_family": name, "y": value}
                for name, value in band_centers
            ],
            "points": points,
        })

    finite = [
        float(item["logd"])
        for facet in facets for item in facet["points"]
        if isinstance(item["logd"], (int, float))
    ]
    span = max(finite) - min(finite) if finite else 1.0
    pad = max(0.2, span * 0.15) if finite else 1.0
    low = min([*finite, criterion.threshold]) - pad if finite else -1.0
    high = max([*finite, criterion.threshold]) + pad if finite else 1.0

    plt = _pyplot()
    from matplotlib.lines import Line2D

    rows = max(1, max(len(facet["points"]) for facet in facets))
    # Reserved bands: the title had been colliding with the per-facet
    # polymer context and the legend had been overprinting the axis labels,
    # so each gets vertical space of its own rather than sharing the plot
    # area with whatever happens to be tall that render.
    figure_height = max(8.2, 3.4 + 0.135 * rows)
    figure_width = max(7.6, 6.6 * len(facets))
    context_wrap = int(max(28.0, 10.0 * figure_width / len(facets)))
    context_rows = max(
        len(textwrap.wrap(
            f"{facet['stage_label']} — x · {facet['polymer_context_line']}",
            width=context_wrap,
        ))
        for facet in facets
    )
    # The title band is sized for the title alone; per-facet context sits
    # below it, in the gap above each axes, and never shares the band.
    title_band = ((_FONT_SIZE * 1.9) / (72.0 * figure_height)) * (
        context_rows + 2.2
    )
    fig, axes = plt.subplots(
        1, len(facets),
        figsize=(figure_width, figure_height),
        squeeze=False,
    )
    fig.suptitle(
        scope["title"],
        fontweight="bold",
        y=0.995 - (_FONT_SIZE * 1.9) / (72.0 * figure_height),
        verticalalignment="top",
    )

    for axis, facet in zip(axes[0], facets):
        axis.set_xlim(low, high)
        axis.set_ylim(-1.0, max(1.0, rows))
        axis.axvspan(
            criterion.threshold, high,
            color=pass_color, alpha=0.18, linewidth=0.0, zorder=0,
        )
        axis.axvline(
            criterion.threshold,
            color=_TEXT_COLOR, linewidth=2.0, zorder=2,
        )
        for point in facet["points"]:
            if not isinstance(point["logd"], (int, float)):
                continue
            axis.scatter(
                [float(point["logd"])], [point["y"]],
                marker=point["marker"],
                c=point["color"],
                edgecolors=_TEXT_COLOR,
                linewidths=0.4,
                s=34,
                zorder=3,
            )
        axis.set_yticks([
            item["y"] for item in facet["band_centers"]
        ])
        axis.set_yticklabels([
            item["contaminant_family"] for item in facet["band_centers"]
        ])
        axis.set_ylabel(
            "Contaminant family (ordered by logD within family)",
        )
        axis.set_xlabel(f"{criterion.axis} · {criterion.pass_meaning}")
        conditions = " · ".join(part for part in (
            str(facet["solvent"] or "unknown solvent"),
            f"dissolve {_format_value(facet['operating_temperature_c'])} °C",
            f"precipitate "
            f"{_format_value(facet['precipitation_temperature_c'])} °C",
        ) if part)
        # Deliberately NOT set_title: the inspector counts every axes title
        # alongside the suptitle, and the whole point of the title band is
        # that it belongs to the figure title and nothing else. The facet
        # context is ordinary text in the gap above its own axes.
        axis.text(
            0.5, 1.02,
            "\n".join(textwrap.wrap(
                f"{facet['stage_label']} — {conditions} · "
                f"{facet['polymer_context_line']}",
                width=context_wrap,
            )),
            transform=axis.transAxes,
            color=_TEXT_COLOR,
            fontsize=_FONT_SIZE,
            horizontalalignment="center",
            verticalalignment="bottom",
        )
        if facet["evidence_status"] != "evaluated":
            axis.text(
                (low + high) / 2.0, max(1.0, rows) / 2.0,
                facet["evidence_reason"],
                color=_TEXT_COLOR,
                fontsize=_FONT_SIZE,
                horizontalalignment="center",
                verticalalignment="center",
            )

    handles = [
        Line2D(
            [], [], linestyle="none",
            marker=marker_by_family.get(family, "o"),
            markerfacecolor="#FFFFFF",
            markeredgecolor=_TEXT_COLOR,
            label=family,
        )
        for family in families
    ] + [
        Line2D(
            [], [], linestyle="none", marker="o",
            markerfacecolor=color, markeredgecolor=_TEXT_COLOR, label=label,
        )
        for color, label in (
            (pass_color, "meets all STRAP criteria"),
            (fail_color, "fails at least one criterion"),
            (absent_color, "not evaluated"),
        )
    ]
    provenance = copy.deepcopy(stored.get("provenance") or {})
    caveat_lines = textwrap.wrap(
        f"{provenance.get('source_dataset') or 'Workbook'} screening "
        "evidence: not validated partition coefficients, removal "
        "efficiency, recovery, or purity.",
        width=int(max(60.0, 9.0 * figure_width)),
    )
    # One text row is _FONT_SIZE points; converting to a figure fraction
    # keeps the reserved bands the same physical size at any figure height,
    # so the legend cannot grow into the axis labels and the caveat cannot
    # grow into the legend.
    row = (_FONT_SIZE * 1.9) / (72.0 * figure_height)
    columns = min(len(handles), 5)
    legend_rows = 1 + (len(handles) - 1) // max(1, columns)
    caveat_top = 0.012 + row * len(caveat_lines)
    legend_top = caveat_top + 0.012 + row * legend_rows
    fig.legend(
        handles=handles,
        loc="lower center",
        ncol=columns,
        bbox_to_anchor=(0.5, caveat_top + 0.012),
    )
    # The x tick labels and the axis label are drawn BELOW the axes box, so
    # the axes floor has to clear the legend row by their height too — that
    # gap is what the legend was previously printing on top of.
    fig.subplots_adjust(
        left=0.16, right=0.98,
        bottom=min(0.62, legend_top + 0.02 + 2.8 * row),
        top=0.995 - title_band,
        wspace=0.30,
    )
    fig.text(
        0.5, 0.012, "\n".join(caveat_lines),
        color=_TEXT_COLOR,
        fontsize=_FONT_SIZE,
        horizontalalignment="center",
        verticalalignment="bottom",
    )

    path = _plot_path(
        _slug([
            "contaminant_partitioning_map",
            str(stored.get("target_polymer") or ""),
            *(str(facet["solvent"] or "") for facet in facets),
        ]),
        output_dir,
        output_path,
    )
    sidecar = {
        "schema": "dissolve.plot.v1",
        "plot_type": "contaminant_partitioning_map",
        "title": scope["title"],
        "subtitle": (
            f"Target polymer: {stored.get('target_polymer') or 'unknown'}; "
            f"{scope_count} {scope_noun}"
        ),
        "caption": (
            "Each marker is one contaminant at its screened logD. Positive "
            "logD partitions into the solvent and is removable; negative "
            "logD stays with the polymer. Marker shape is the contaminant "
            "family and fill is the combined STRAP verdict. "
            + " ".join(caveat_lines)
        ),
        "result_kind": "contaminant_partitioning_map",
        "target_polymer": stored.get("target_polymer"),
        "other_polymers": copy.deepcopy(stored.get("other_polymers") or []),
        "contaminant_families": families,
        "pass_criterion": {
            "name": criterion.name,
            "axis": criterion.axis,
            "threshold": criterion.threshold,
            "pass_direction": criterion.pass_direction,
            "pass_meaning": criterion.pass_meaning,
            "fail_meaning": criterion.fail_meaning,
        },
        "logd_range": [low, high],
        "scope_kind": scope["scope_kind"],
        "scope_summary": scope_summary,
        scope["count_key"]: scope_count,
        "criterion_record_count": sum(
            len(facet["points"]) for facet in facets
        ),
        "facets": facets,
        "unsupported_contaminants": copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        "provenance": provenance,
        "publication_treatment": {
            "encoding": "solid_categorical_colors_no_gradients",
            "pass_color": pass_color,
            "fail_color": fail_color,
            "not_evaluated_color": absent_color,
            "text_color": _TEXT_COLOR,
            "font_size_pt": _FONT_SIZE,
        },
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool,
        display=(
            f"Full-resolution contaminant partitioning map: {plot_path}. "
            f"{sidecar['criterion_record_count']} contaminant records across "
            f"{scope_count} stored {scope_noun}."
        ),
        # Without the artifact the turn is refused despite a correct PNG:
        # presence evaluation has nothing inspector-validated to bind to.
        artifact=_artifact(plot_path, sidecar_path, scope["title"]),
        analysis_type="contaminant_partitioning_map_plot",
        plot_type="contaminant_partitioning_map",
        plot_paths=[plot_path],
        plot_path=plot_path,
        terminal_plot_data_path=sidecar_path,
        result_kind="contaminant_partitioning_map",
        target_polymer=stored.get("target_polymer"),
        scope_kind=scope["scope_kind"],
        scope_summary=scope_summary,
        criterion_record_count=sidecar["criterion_record_count"],
        **{scope["count_key"]: scope_count},
        contaminant_families=families,
        unsupported_contaminants=copy.deepcopy(
            stored.get("unsupported_contaminants") or [],
        ),
        provenance=provenance,
        warnings=copy.deepcopy(stored.get("warnings") or []),
        model_basis=(
            "per-contaminant screened logD joined to stored route stages by "
            "canonical solvent identity"
        ),
    )


def _stored_analysis_result(state: Any) -> dict[str, Any] | None:
    """Select the exact private evidence for the matching admitted subtype."""
    current = getattr(state, "last_analysis", None) if state else None
    private_analysis = (
        getattr(state, "last_analysis_plot_data", None) if state else None
    )
    current_type = (
        str(current.get("analysis_type") or "")
        if isinstance(current, dict) else ""
    )
    private_type = (
        str(private_analysis.get("analysis_type") or "")
        if isinstance(private_analysis, dict) else ""
    )
    if private_analysis and (
        not current
        or current_type == private_type
        or (
            current_type.startswith("hsp_")
            and private_type.startswith("hsp_")
        )
        or getattr(state, "last_tool", None) in {
            "lookup_hansen_parameters", "screen_hansen_compatibility",
            "screen_contaminant_leaching",
            "screen_contaminant_strap_removal",
            "compare_contaminant_removal_modes",
        }
    ):
        return private_analysis
    return current


def plot_analysis_results(
    view: Literal[
        "auto", "heatmap", "radar", "sphere", "gauge", "summary",
        "pair_gap", "family", "method_comparison", "red_vs_solubility",
        "safety_margin_bars", "safety_card", "metric_scatter", "tea_case_bars",
        "tea_record_card", "tea_pareto", "tea_sensitivity_tornado",
        "contaminant_criteria_heatmap", "contaminant_screen_card",
        "partitioning_map",
    ] = "auto",
    hsp_heatmap_orientation: Literal["materials_rows", "materials_columns"] = "materials_rows",
    highlight_pairs: Optional[Sequence[dict[str, str]]] = None,
    highlight_solvents: Optional[Sequence[str]] = None,
    metric: Literal[
        "msp", "tci", "aoc", "gwp", "etox", "htc", "htnc",
        "electricity", "heating", "cooling", "energy",
    ] = "gwp",
    x_metric: Literal[
        "msp", "tci", "aoc", "gwp", "etox", "htc", "htnc",
        "electricity", "heating", "cooling", "energy",
    ] = "gwp",
    y_metric: Literal[
        "msp", "tci", "aoc", "gwp", "etox", "htc", "htnc",
        "electricity", "heating", "cooling", "energy",
    ] = "msp",
    target_polymer: Optional[str] = None,
    energy_case: Optional[Literal["C1", "C2", "C3"]] = None,
    include_sensitivity_variants: bool = False,
    show_provenance_note: bool = True,
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Plot the current typed HSP, statistical, Tg, or thermal result."""
    from .session import current_tool_session

    tool = "plot_analysis_results"
    state = current_tool_session()
    requested = str(view or "auto").casefold()
    if requested == "safety_margin_bars":
        return _plot_safety_margin_bars(
            state,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "safety_card":
        return _plot_safety_card(
            state,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "metric_scatter":
        return _plot_metric_scatter(
            state,
            highlight_solvents=highlight_solvents,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "tea_case_bars":
        return _plot_tea_case_bars(
            state,
            metric=str(metric),
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "tea_record_card":
        return _plot_tea_record_card(
            state,
            target_polymer=target_polymer,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "tea_pareto":
        return _plot_tea_pareto(
            state,
            x_metric=str(x_metric),
            y_metric=str(y_metric),
            target_polymer=target_polymer,
            energy_case=energy_case,
            include_sensitivity_variants=bool(
                include_sensitivity_variants,
            ),
            show_provenance_note=bool(show_provenance_note),
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "tea_sensitivity_tornado":
        return _plot_tea_sensitivity_tornado(
            state,
            metric=str(metric),
            target_polymer=target_polymer,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "contaminant_criteria_heatmap":
        return _plot_contaminant_criteria_heatmap(
            state,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "contaminant_screen_card":
        return _plot_contaminant_screen_card(
            state,
            output_dir=output_dir,
            output_path=output_path,
        )
    if requested == "partitioning_map":
        return _plot_contaminant_partitioning_map(
            state,
            output_dir=output_dir,
            output_path=output_path,
        )
    # H8: this stored-result boundary replaces the pre-bridge 20-series cap at
    # visualization.py:201 and the old _resolve_polymers/_resolve_solvents
    # display-name re-resolution path. Plotting cannot silently change scope.
    current = _stored_analysis_result(state)
    if not current:
        return tool_error(tool, "No admitted analysis result is available in typed session state.", error_code="missing_analysis_result")
    analysis_type = str(current.get("analysis_type") or "")
    plt = _pyplot()
    evidence: dict[str, Any]
    if analysis_type == "pairwise_thermodynamic_separability":
        from .tools import screen_pairwise_solubility_overlap

        if requested not in {"auto", "summary", "pair_gap"}:
            return tool_error(
                tool, "Pairwise thermodynamic state supports the pair_gap view.",
                error_code="unsupported_analysis_view",
            )
        reproduced = parse_tool_result(screen_pairwise_solubility_overlap(
            list(current.get("polymers") or []),
            temperature_max_c=current.get("temperature_max_c"),
            strict_maximum=bool(current.get("strict_maximum")),
        ))["data"]
        rows = list(reproduced.get("ranked_pairs") or [])
        if reproduced.get("success") is not True or not rows:
            return tool_error(
                tool, "The typed pairwise screen could not be reproduced.",
                error_code="analysis_reproduction_failed",
            )
        labels = [" / ".join(row["polymers"]) for row in rows]
        values = [float(row["maximum_absolute_gap_pct"]) for row in rows]
        fig, ax = plt.subplots(figsize=(9.4, max(5.0, .42 * len(rows) + 2.0)))
        positions = list(range(len(rows)))
        bars = ax.barh(
            positions, values,
            color=[_COLORS[1] if index < 3 else _COLORS[0] for index in positions],
        )
        ax.set_yticks(positions, labels)
        ax.invert_yaxis()
        ax.set_xlabel("Best-achievable absolute gap (percentage points)")
        plot_title = "Pairwise thermodynamic gap ranking"
        ax.set_title(plot_title)
        _label_horizontal_bars(ax, bars, values)
        path = _plot_path(_slug([*current.get("polymers", []), "pair_gap_ranking"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "pairwise_gap_ranking",
            "title": plot_title,
            "subtitle": (
                f"Below {current.get('temperature_max_c'):g} °C"
                if current.get("strict_maximum") else
                f"Through {current.get('temperature_max_c'):g} °C"
            ),
            "caption": "Smaller best-achievable gaps indicate greater relative overlap; they do not prove inseparability.",
            "result_kind": "pairwise_thermodynamic_separability",
            "polymers": list(current.get("polymers") or []), "ranked_pairs": rows,
        }
        evidence = {
            "polymers": list(current.get("polymers") or []),
            "pair_count": len(rows), "candidate_conditions": rows[:3],
            "evidence_class": "temperature_dependent_thermodynamic_pair_screen",
        }
        title = "Pairwise thermodynamic gap ranking"
    elif analysis_type == "hsp_family_lookup":
        rows = list(current.get("rows") or [])
        families = list(current.get("family_summaries") or [])
        if not rows or not families:
            return tool_error(
                tool, "The stored family result has no plottable source records.",
                error_code="analysis_result_not_plottable",
            )
        if requested not in {"auto", "family", "summary"}:
            return tool_error(
                tool, "A family HSP lookup supports the family view.",
                error_code="unsupported_analysis_view",
            )
        family_label = str(families[0].get("family_label") or "Polymer family")
        relationships = list(dict.fromkeys(str(row.get("relationship") or "unclassified") for row in rows))
        colors = {name: _COLORS[index % len(_COLORS)] for index, name in enumerate(relationships)}
        fig, axes = plt.subplots(
            1, 3, sharey=True,
            figsize=(14.5, max(5.2, 0.36 * len(rows) + 2.4)),
        )
        positions = list(range(len(rows)))
        labels = [f"{row['source_label']} · {row['source_record_id']}" for row in rows]
        for axis, key, axis_label in zip(
            axes,
            ("dispersion", "polar", "hydrogen_bonding"),
            ("dD (MPa$^{0.5}$)", "dP (MPa$^{0.5}$)", "dH (MPa$^{0.5}$)"),
        ):
            values = [float(row[key]) for row in rows]
            headline_range = (families[0].get("headline_parameter_ranges") or {}).get(key)
            if headline_range:
                axis.axvspan(
                    float(headline_range["minimum"]), float(headline_range["maximum"]),
                    color="#d9d9d9", alpha=0.45, zorder=0,
                )
            axis.scatter(
                values, positions, s=38,
                c=[colors[str(row.get("relationship") or "unclassified")] for row in rows],
            )
            axis.set_xlabel(axis_label)
            axis.grid(axis="x", alpha=0.25)
        axes[0].set_yticks(positions, labels)
        axes[0].invert_yaxis()
        plot_title = f"{family_label}: HSP spread"
        fig.suptitle(plot_title)
        from matplotlib.lines import Line2D
        from matplotlib.patches import Patch
        handles = [
            Line2D([0], [0], marker="o", linestyle="", color=color, label=name.replace("_", " "))
            for name, color in colors.items()
        ]
        if families[0].get("headline_parameter_ranges"):
            handles.append(Patch(facecolor="#d9d9d9", label="headline parameter range"))
        fig.legend(
            handles=handles, loc="center left", bbox_to_anchor=(0.80, 0.5),
            ncol=1, frameon=False,
        )
        fig.subplots_adjust(left=0.27, right=0.78, bottom=0.09, top=0.93, wspace=0.18)
        path = _plot_path(_slug([families[0]["family_id"], "hsp_family_spread"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "hsp_family_dot_range",
            "title": plot_title,
            "subtitle": f"{family_label} · source-record-resolved HSP evidence",
            "caption": (
                "Core controls the concise headline only. All displayed evidence tiers remain accessible; "
                "parameter spread is descriptive and is not experimental agreement."
            ),
            "result_kind": "hsp_family", "family_summaries": families,
            "rows": rows, "resolution_issues": list(current.get("resolution_issues") or []),
            "temperature_dependent": False,
            "evidence_class": "qualitative_hansen_family_parameters",
        }
        evidence = {
            "families": [family["family_id"] for family in families],
            "source_record_count": len(rows), "temperature_dependent": False,
            "evidence_class": "qualitative_hansen_family_parameters",
            "family_summaries": families,
            "resolution_issues": list(current.get("resolution_issues") or []),
        }
        title = f"{family_label} HSP family spread"
    elif analysis_type == "hsp_thermodynamic_comparison":
        all_rows = [dict(row) for row in list(current.get("joined_rows") or [])]
        rows = [
            row for row in all_rows
            if _finite(row.get("red")) is not None
            and _finite(row.get("fitted_solubility_wt_pct")) is not None
        ]
        if not rows:
            return tool_error(
                tool, "The stored joined-method result has no plottable fitted pairs.",
                error_code="analysis_result_not_plottable",
            )
        if requested not in {"auto", "method_comparison", "red_vs_solubility", "summary"}:
            return tool_error(
                tool,
                "A joined HSP/thermodynamic result supports the method_comparison view.",
                error_code="unsupported_analysis_view",
            )
        polymers = list(dict.fromkeys(
            str(row.get("thermodynamic_polymer") or row["polymer"]) for row in rows
        ))
        solvents = list(dict.fromkeys(str(row["solvent"]) for row in all_rows))
        polymer_colors = {
            name: _COLORS[index % len(_COLORS)] for index, name in enumerate(polymers)
        }
        markers = ("o", "s", "^", "D", "P", "X")
        solvent_markers = {
            name: markers[index % len(markers)] for index, name in enumerate(solvents)
        }
        fig, (ax, key_axis) = plt.subplots(
            1, 2, figsize=(15.2, max(6.2, .43 * len(all_rows) + 2.2)),
            gridspec_kw={"width_ratios": [1.35, 1]},
        )
        for row in rows:
            ax.scatter(
                [float(row["red"])], [float(row["fitted_solubility_wt_pct"])],
                color=polymer_colors[str(row.get("thermodynamic_polymer") or row["polymer"])],
                marker=solvent_markers[str(row["solvent"])],
                s=78, alpha=.82, edgecolor="#000000", linewidth=.45,
            )
        ax.axvline(1.0, color="#000000", linestyle="--", linewidth=1.2)
        positive_values = [float(row["fitted_solubility_wt_pct"]) for row in rows]
        if all(value > 0 for value in positive_values):
            ax.set_yscale("log")
            y_scale = "log"
        else:
            ax.set_yscale("symlog", linthresh=1e-5)
            y_scale = "symlog"
        ax.set_xlabel("Hansen relative energy difference (RED)")
        ax.set_ylabel("Thermodynamic solubility (wt% solution concentration)")
        unavailable_rows = [row for row in all_rows if row not in rows]
        plot_title = "Hansen RED versus temperature-dependent solubility"
        fig.suptitle(plot_title)
        from matplotlib.lines import Line2D
        handles = []
        for row in all_rows:
            polymer = str(row.get("thermodynamic_polymer") or row["polymer"])
            available = row in rows
            handles.append(Line2D(
                [0], [0], marker=(solvent_markers[str(row["solvent"])] if available else "x"),
                linestyle="", color=(polymer_colors.get(polymer, "#777777") if available else "#777777"),
                markeredgecolor="#000000",
                label=(
                    f"{polymer} / {row['solvent']} · "
                    f"P{row.get('polymer_source_record_id')} / "
                    f"S{row.get('solvent_source_record_id')}"
                    + (" · no thermodynamic evidence" if not available else "")
                ),
            ))
        key_axis.set_axis_off()
        key_axis.legend(handles=handles, loc="center left", labelspacing=.55)
        fig.tight_layout(rect=(0, 0, 1, .95))
        path = _plot_path(
            _slug(["hsp", "red", "fitted_solubility", *polymers, *solvents]),
            output_dir, output_path,
        )
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "hsp_red_vs_fitted_solubility",
            "title": plot_title,
            "subtitle": f"Exactly {float(current.get('fitted_temperature_c')):g} °C",
            "caption": (
                "RED is a temperature-independent qualitative compatibility descriptor; "
                "thermodynamic solubility is a separate temperature-dependent result whose "
                "method is carried on each row. The RED=1 line is the Hansen-sphere convention "
                "and is not a solubility decision boundary. Requested rows without thermodynamic evidence remain explicit "
                "in the sidecar and are not plotted as numerical points."
            ),
            "result_kind": "hsp_thermodynamic_comparison",
            "temperature_c": current.get("fitted_temperature_c"),
            "temperature_use_regime": current.get("fitted_temperature_use_regime"),
            "x_reference": {"value": 1.0, "meaning": "Hansen-sphere convention"},
            "y_scale": y_scale, "rows": all_rows,
            "plotted_rows": rows,
            "unavailable_fitted_rows": unavailable_rows,
            "resolution_issues": list(current.get("resolution_issues") or []),
            "evidence_class": "joined_distinct_hsp_and_fitted_thermodynamic_evidence",
        }
        evidence = {
            "polymers": polymers, "solvents": solvents, "n_series": len(rows),
            "temperature_c": current.get("fitted_temperature_c"),
            "joined_pair_count": len(all_rows), "plotted_pair_count": len(rows),
            "unavailable_fitted_pair_count": len(all_rows) - len(rows),
            "evidence_class": sidecar["evidence_class"],
            "resolution_issues": sidecar["resolution_issues"],
        }
        title = plot_title
    elif analysis_type == "hsp_red_matrix":
        rows = list(current.get("rows") or [])
        polymers, solvents = list(current.get("polymers") or []), list(current.get("solvents") or [])
        if not rows or not polymers or not solvents:
            return tool_error(
                tool, "The stored HSP matrix has no plottable source-record values.",
                error_code="analysis_result_not_plottable",
            )
        selected_view = ("gauge" if len(rows) == 1 else "heatmap") if requested == "auto" else requested
        if selected_view not in {"heatmap", "radar", "sphere", "gauge"}:
            return tool_error(tool, "HSP view must be heatmap, radar, sphere, or gauge.", error_code="unsupported_analysis_view")
        if selected_view == "heatmap":
            (
                fig, plot_title,
                sidecar_extra,
                heatmap_error,
            ) = _plot_hsp_red_heatmap(
                plt,
                rows=rows,
                polymers=polymers,
                solvents=solvents,
                orientation=hsp_heatmap_orientation,
                highlight_pairs=highlight_pairs or (),
            )
            if heatmap_error:
                return tool_error(
                    tool,
                    heatmap_error,
                    error_code="invalid_hsp_heatmap_options",
                )
        elif selected_view == "radar":
            selected_polymer = _first_requested_hsp_polymer(rows, polymers)
            selected_source_record = next(
                row["polymer_source_record_id"]
                for row in rows
                if row["polymer"] == selected_polymer
            )
            selected_rows = [
                row for row in rows
                if row["polymer_source_record_id"] == selected_source_record
            ]
            maximum_solvent_series = 6

            fig, plot_title, sidecar_extra = _plot_hsp_normalized_radar(
                plt,
                selected_polymer,
                selected_rows,
                maximum_solvent_series,
            )
        elif selected_view == "sphere":
            selected_polymer = _first_requested_hsp_polymer(rows, polymers)
            selected_source_record = next(
                row["polymer_source_record_id"]
                for row in rows
                if row["polymer"] == selected_polymer
            )
            selected_rows = [
                row for row in rows
                if row["polymer_source_record_id"] == selected_source_record
            ]
            fig, plot_title, sidecar_extra = _plot_hsp_interaction_sphere(
                plt,
                selected_polymer,
                selected_rows,
            )
        else:
            row = rows[0]
            fig, ax = plt.subplots(figsize=(7, 2.8))
            ax.barh([f"{row['polymer']} / {row['solvent']}"], [row["red"]], color=_COLORS[0])
            ax.axvline(1.0, color=_COLORS[1], linestyle="--", label="Hansen-sphere boundary")
            ax.set_xlabel("RED")
            plot_title = "Hansen compatibility gauge"
            ax.set_title(plot_title)
            ax.legend()
            sidecar_extra = {}
        path = _plot_path(_slug(["hsp", selected_view, *polymers, *solvents]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": f"hsp_{selected_view}",
            "title": plot_title, "subtitle": "Temperature-independent qualitative screen",
            "caption": "Hansen RED is a qualitative compatibility proxy, not temperature-dependent solubility or demonstrated process selectivity.",
            "result_kind": "hsp", "polymers": polymers, "solvents": solvents,
            "rows": rows, "temperature_dependent": False,
            "evidence_class": "qualitative_hansen_red_screen",
            "family_summaries": list(current.get("family_summaries") or []),
            "family_red_summaries": list(current.get("family_red_summaries") or []),
            "resolution_issues": list(current.get("resolution_issues") or []),
            **sidecar_extra,
        }
        evidence = {
            "polymers": polymers, "solvents": solvents, **_hsp_plot_count_evidence(selected_view, rows, sidecar_extra),
            "temperature_dependent": False,
            "evidence_class": "qualitative_hansen_red_screen",
            "family_summaries": list(current.get("family_summaries") or []),
            "family_red_summaries": list(current.get("family_red_summaries") or []),
            "resolution_issues": list(current.get("resolution_issues") or []),
        }
        title = f"Hansen {selected_view}"
    else:
        result = current.get("result") or {}
        summaries = list(result.get("summaries") or [])
        matches = list(current.get("matches") or [])
        thermal = result if analysis_type == "thermal_group_contribution" else {}
        if summaries:
            labels = [row["name"] for row in summaries]
            values = [float(row["mean"]) for row in summaries]
            errors = [[value - float(row["mean_confidence_interval"][0]) for value, row in zip(values, summaries)], [float(row["mean_confidence_interval"][1]) - value for value, row in zip(values, summaries)]]
            y_label, evidence_class = "Mean with confidence interval", "explicit_numeric_samples"
        elif matches:
            labels = ["; ".join(row.get("names") or []) or row["psmiles"] for row in matches]
            values = [float(row["tg_c"]) for row in matches]
            errors = None
            y_label, evidence_class = "Tg (°C)", "tg_snapshot"
        elif thermal:
            labels = ["Tm (K)", "ΔHf (kJ/mol)", "ΔCp (J/mol K)"]
            values = [float(thermal.get("tm_k") or 0), float(thermal.get("delta_hf_j_per_mol") or 0) / 1000, float(thermal.get("delta_cp_j_per_mol_k") or 0)]
            errors = None
            y_label, evidence_class = "Group-contribution estimate", "predictive_extension_not_fitted_record"
        else:
            return tool_error(tool, "The current analysis has no plottable bounded values.", error_code="analysis_result_not_plottable")
        fig, ax = plt.subplots(figsize=(max(6, 1.3 * len(labels) + 3), 4.6))
        bars = ax.bar(labels, values, yerr=errors, capsize=5, color=[_COLORS[index % len(_COLORS)] for index in range(len(labels))])
        ax.set_ylabel(y_label)
        plot_title = "Analysis summary"
        ax.set_title(plot_title)
        _label_bars(ax, bars)
        path = _plot_path(_slug([analysis_type, "summary"]), output_dir, output_path)
        sidecar = {
            "schema": "dissolve.plot.v1", "plot_type": "comparison_results",
            "title": plot_title, "subtitle": y_label,
            "caption": "Values reproduce the current bounded analysis result; the evidence class in this sidecar governs interpretation.",
            "result_kind": "analysis", "labels": labels, "values": values,
            "y_label": y_label, "evidence_class": evidence_class,
        }
        evidence = {"labels": labels, "values": values, "evidence_class": evidence_class}
        title = "Analysis summary"
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool, display=f"Full-resolution analysis plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, title),
        analysis_type="analysis_plot", plot_type=sidecar["plot_type"],
        plot_paths=[plot_path], plot_path=plot_path,
        terminal_plot_data_path=sidecar_path, result_kind=sidecar["result_kind"],
        **evidence,
        warnings=["The artifact reproduces bounded admitted evidence and adds no experimental validation."],
        model_basis=(
            "stored typed analysis evidence; HSP rows are plotted without re-resolution"
            if str(analysis_type).startswith("hsp_") else
            "typed analysis state regenerated inside the visualization boundary"
        ),
    )


def plot_research_results(
    output_dir: Optional[str] = None,
    output_path: Optional[str] = None,
) -> str:
    """Plot bounded retrieval-component scores from typed literature state."""
    from .session import current_tool_session

    tool = "plot_research_results"
    state = current_tool_session()
    current = getattr(state, "last_research", None) if state else None
    if not current:
        return tool_error(tool, "No admitted literature result is available in typed session state.", error_code="missing_research_result")
    rows = [
        item for item in list(current.get("results") or [])
        if item.get("final_score") is not None
    ][:10]
    if not rows:
        return tool_error(
            tool, "The current research result has no retrieval scores to plot.",
            error_code="research_result_not_plottable",
        )
    labels = [str(item.get("citation_id") or item.get("chunk_id") or index + 1) for index, item in enumerate(rows)]
    sparse = [float(item.get("sparse_score") or 0.0) for item in rows]
    dense = [float(item.get("dense_score") or 0.0) for item in rows]
    final = [float(item.get("final_score") or 0.0) for item in rows]
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=(max(7.0, len(rows) * 1.05 + 3), 4.8))
    positions = list(range(len(rows)))
    width = 0.24
    ax.bar([value - width for value in positions], sparse, width, label="BM25", color=_COLORS[0])
    if current.get("dense_index_available"):
        ax.bar(positions, dense, width, label="Dense", color=_COLORS[2])
    ax.bar([value + width for value in positions], final, width, label="Final", color=_COLORS[1])
    _set_categorical_x_labels(fig, ax, positions, labels)
    ax.set_ylabel("Normalized retrieval score")
    plot_title = "Literature retrieval scores"
    ax.set_title(plot_title)
    ax.legend()
    path = _plot_path(_slug(["literature", "retrieval", current.get("knowledgebase") or "corpus"]), output_dir, output_path)
    sidecar = {
        "schema": "dissolve.plot.v1", "plot_type": "research_retrieval_scores",
        "title": plot_title,
        "subtitle": str(current.get("knowledgebase") or "corpus"),
        "caption": "Scores describe corpus retrieval rank, not scientific effect size, evidence quality, or experimental confidence.",
        "result_kind": "research", "query": current.get("query"),
        "knowledgebase": current.get("knowledgebase"),
        "retrieval_mode": current.get("retrieval_mode"),
        "dense_index_available": bool(current.get("dense_index_available")),
        "categorical_label_policy": "complete_lossless_wrap_then_measured_rotation",
        "results": [{
            key: item.get(key) for key in (
                "citation_id", "chunk_id", "title", "source", "sparse_score",
                "dense_score", "section_boost", "final_score",
            )
        } for item in rows],
    }
    plot_path, sidecar_path = _save(fig, path, sidecar)
    plt.close(fig)
    return tool_success(
        tool, display=f"Full-resolution retrieval-score plot: {plot_path}",
        artifact=_artifact(plot_path, sidecar_path, "Literature retrieval scores"),
        analysis_type="research_plot", plot_type=sidecar["plot_type"],
        plot_paths=[plot_path], plot_path=plot_path,
        terminal_plot_data_path=sidecar_path, result_kind="research",
        query=current.get("query"), knowledgebase=current.get("knowledgebase"),
        retrieval_mode=current.get("retrieval_mode"),
        dense_index_available=bool(current.get("dense_index_available")),
        result_count=len(rows),
        warnings=["The artifact visualizes retrieval scores, not scientific effect size or experimental confidence."],
        model_basis="retrieval-score artifact regenerated from the admitted corpus result",
    )


_DOTS = ((0, 0, 1), (0, 1, 2), (0, 2, 4), (1, 0, 8), (1, 1, 16), (1, 2, 32), (0, 3, 64), (1, 3, 128))


def _series_preview(payload: dict[str, Any], width: int, rows: int):
    from rich.text import Text

    raw = list(payload.get("series") or [])
    if not raw:
        return None
    all_points = [point for item in raw for point in item.get("points") or []]
    if not all_points:
        return None
    x_min, x_max = float(payload["x_min"]), float(payload["x_max"])
    y_max = float(payload.get("y_axis_max") or _nice_max(max(float(point[1]) for point in all_points)))
    chart_columns, chart_rows = max(28, width - 9), max(8, rows - 5)
    pixels_x, pixels_y = chart_columns * 2, chart_rows * 4
    ink: dict[tuple[int, int], tuple[int, int, int]] = {}

    def rgb(hex_color: str) -> tuple[int, int, int]:
        text = hex_color.lstrip("#")
        return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4))

    def project(point: Sequence[float]) -> tuple[int, int]:
        x, y = float(point[0]), min(max(float(point[1]), 0.0), y_max)
        return (
            round((x - x_min) / max(1e-12, x_max - x_min) * (pixels_x - 1)),
            round((1 - y / max(1e-12, y_max)) * (pixels_y - 1)),
        )

    for item in raw:
        color = rgb(str(item.get("color") or _COLORS[0]))
        projected = [project(point) for point in item.get("points") or []]
        for first, second in zip(projected, projected[1:]):
            steps = max(abs(second[0] - first[0]), abs(second[1] - first[1]), 1)
            for step in range(steps + 1):
                fraction = step / steps
                ink[(round(first[0] + (second[0] - first[0]) * fraction), round(first[1] + (second[1] - first[1]) * fraction))] = color
    references = list(payload.get("reference_lines") or [])
    for reference in references:
        color = rgb(str(reference.get("color") or "#555555"))
        value = float(reference.get("value") or 0.0)
        points = (
            [project((x_min, value)), project((x_max, value))]
            if reference.get("axis") == "y"
            else [project((value, 0.0)), project((value, y_max))]
        )
        first, second = points
        steps = max(abs(second[0] - first[0]), abs(second[1] - first[1]), 1)
        for step in range(0, steps + 1, 2):
            fraction = step / steps
            ink[(round(first[0] + (second[0] - first[0]) * fraction), round(first[1] + (second[1] - first[1]) * fraction))] = color
    result = Text(no_wrap=True, overflow="crop")
    result.append(str(payload.get("y_label") or "Value"), style="bold")
    for item in raw[:4]:
        result.append("   ● ", style=f"#{str(item.get('color') or _COLORS[0]).lstrip('#')}")
        result.append(str(item.get("label") or "Series"))
    if references:
        result.append("\nReferences", style="bold")
        for reference in references[:3]:
            result.append("   ┊ ", style=f"#{str(reference.get('color') or '#555555').lstrip('#')}")
            result.append(str(reference.get("label") or "Reference"))
    result.append("\n")
    ticks = {round((chart_rows - 1) * part): y_max * (1 - part) for part in (0, .25, .5, .75, 1)}
    for row in range(chart_rows):
        result.append(f"{ticks[row]:6.1f} ┤" if row in ticks else "       │", style="dim")
        for column in range(chart_columns):
            bits, colors = 0, []
            for dx, dy, bit in _DOTS:
                color = ink.get((column * 2 + dx, row * 4 + dy))
                if color:
                    bits |= bit
                    colors.append(color)
            if bits:
                average = tuple(round(sum(color[channel] for color in colors) / len(colors)) for channel in range(3))
                result.append(chr(0x2800 + bits), style=f"rgb({average[0]},{average[1]},{average[2]})")
            else:
                result.append("╌" if row in ticks else " ", style="dim")
        result.append("\n")
    result.append("       └" + "─" * chart_columns + "\n", style="dim")
    footer = f"{x_min:g}°C".ljust(chart_columns)
    right = f"{x_max:g}°C"
    footer = footer[:max(0, chart_columns - len(right))] + right
    result.append("        " + footer, style="dim")
    return result


def _structured_preview(payload: dict[str, Any]):
    from rich.text import Text

    result = Text()
    plot_type = payload.get("plot_type")
    if plot_type == "fixed_temperature_separation":
        result.append(f"Candidates at {payload.get('temperature_c'):g} °C\n", style="bold")
        for item in payload.get("candidates") or []:
            result.append(f"{item.get('solvent')}: ")
            result.append(f"{float(item.get('selectivity_pct') or 0):.2f} points", style="cyan")
            result.append(f" · dissolve {item.get('dissolved_polymer')}\n")
    elif plot_type == "separation_route":
        result.append(" → ".join(str(item) for item in payload.get("polymers") or []), style="bold")
        result.append("\n")
        for item in payload.get("steps") or []:
            result.append(f"{item.get('step')}. {item.get('dissolved_polymer')} · {item.get('solvent')} @ {item.get('temperature_c'):g} °C\n")
        result.append(f"Final residue: {payload.get('final_residue') or 'partial/unresolved'}")
    elif plot_type == "route_window_comparison":
        result.append("Stored route windows\n", style="bold")
        for window in payload.get("windows") or []:
            qualifier = "Below" if window.get("strict_maximum") else "Through"
            result.append(
                f"{qualifier} {_format_value(window.get('temperature_max_c'))} °C: "
                f"{' → '.join(window.get('best_sequence') or [])}\n"
            )
    elif plot_type == "comparison_results":
        maximum = max([float(item) for item in payload.get("values") or []] or [1.0])
        for label, value in zip(payload.get("labels") or [], payload.get("values") or []):
            count = round(30 * float(value) / maximum) if maximum else 0
            result.append(f"{label:<26} {'█' * count} {float(value):g}\n")
    elif plot_type == "optimization_frontier":
        x_metric = str(payload.get("x_metric") or "x").replace("_", " ")
        y_metric = str(payload.get("y_metric") or "y").replace("_", " ")
        result.append(f"{x_metric} ↔ {y_metric}\n", style="bold")
        for point in payload.get("points") or []:
            result.append(
                f"{point.get('design_id')}: {float(point[payload['x_metric']]):.4g} · "
                f"{float(point[payload['y_metric']]):.4g}\n"
            )
        knee = payload.get("knee_point") or {}
        cheapest = payload.get("cheapest_point") or {}
        result.append(
            f"Knee: {knee.get('design_id') or '—'} · Cheapest: "
            f"{cheapest.get('design_id') or '—'}"
        )
        if payload.get("knee_status") == "endpoint_only_no_interior_knee":
            result.append(" · no interior knee", style="yellow")
    elif plot_type == "scale_pareto_composite":
        result.append("Scale evidence and route frontier\n", style="bold")
        for row in payload.get("scale_rows") or []:
            provenance = (
                "exact cache" if row.get("provenance_class") == "exact_cached_simulation"
                else "screening estimate"
            )
            result.append(
                f"{_format_value(row.get('processing_capacity_mt_per_yr'))} t/yr: "
                f"MSP {_format_value(row.get('msp_usd_per_kg'))} USD/kg · "
                f"GWP {_format_value(row.get('gwp_t_co2e_per_mt'))} t CO2e/t · "
                f"{provenance}\n"
            )
        pareto = payload.get("pareto") or {}
        result.append(
            f"Frontier: {len(pareto.get('frontier_points') or [])} of "
            f"{len(pareto.get('landscape_points') or [])} feasible designs"
        )
        if pareto.get("knee_status") == "endpoint_only_no_interior_knee":
            result.append(" · no interior knee", style="yellow")
    elif plot_type == "selectivity_heatmap":
        solvents = list(payload.get("solvents") or [])
        result.append(f"Selectivity at {payload.get('temperature_c'):g} °C\n", style="bold")
        result.append("Target".ljust(14) + " ".join(str(item)[:12].rjust(12) for item in solvents) + "\n")
        for polymer, row in zip(payload.get("polymers") or [], payload.get("matrix") or []):
            values = " ".join(("—" if value is None else f"{float(value):.1f}").rjust(12) for value in row)
            result.append(str(polymer).ljust(14) + values + "\n")
    elif plot_type == "atmospheric_feasibility":
        result.append("Atmospheric-operability candidates\n", style="bold")
        for item in payload.get("candidates") or []:
            result.append(
                f"{item.get('solvent')}: {item.get('temperature_c'):g} °C / "
                f"BP {item.get('boiling_point_c'):g} °C · margin "
                f"{item.get('boiling_point_margin_c'):g} °C\n"
            )
    elif plot_type == "safety_profile_comparison":
        result.append("Candidate safety at stored operating conditions\n", style="bold")
        for row in payload.get("comparison_rows") or []:
            result.append(f"{row.get('solvent')}: ", style="bold")
            result.append(
                f"BP margin {_format_value(row.get('boiling_margin_c'))} °C · "
                f"flash margin {_format_value(row.get('flash_margin_c'))} °C · "
                f"GHS {row.get('ghs_signal_word') or 'not reported'}"
            )
            limits = list(row.get("occupational_exposure_limits") or [])
            result.append(f" · OEL {'reported' if limits else 'not available'}\n")
    elif plot_type == "safety_margin_bars":
        result.append("Flash-point safety margins\n", style="bold")
        for row in payload.get("comparison_rows") or []:
            result.append(
                f"{row.get('solvent')} at "
                f"{_format_value(row.get('operating_temp_c'))} °C: "
                f"{_format_value(row.get('flash_point_margin_c'))} °C\n"
            )
        result.append(
            "Negative margin means operating above the reported flash point.",
            style="dim",
        )
    elif plot_type == "safety_card":
        result.append(
            f"{payload.get('solvent_name')} safety card\n",
            style="bold",
        )
        result.append(
            f"CAS {payload.get('cas_number')} · "
            f"heating risk {payload.get('risk_level')} · "
            f"G-score {_format_value(payload.get('g_score'))}\n",
        )
        gaps = list(payload.get("data_gaps") or [])
        result.append(
            "Data gaps: "
            + (", ".join(str(item) for item in gaps) if gaps else "none"),
            style="dim",
        )
    elif plot_type == "tea_case_bars":
        result.append("Admitted process records by energy case\n", style="bold")
        for row in payload.get("comparison_rows") or []:
            result.append(
                f"{row.get('record_group')} · {row.get('energy_case')}: "
                f"{_format_value(row.get('metric_value'))} "
                f"{row.get('metric_unit')}\n"
            )
    elif plot_type == "tea_record_card":
        result.append("Admitted process record cards\n", style="bold")
        for card in payload.get("cards") or []:
            config = card.get("config") or {}
            economics = card.get("economics") or {}
            lca = card.get("lca") or {}
            result.append(
                f"{card.get('record_id')} · "
                f"{config.get('target_plastic')} / {config.get('solvent')} · "
                f"MSP {_format_value(economics.get('msp_usd_per_kg'))} USD/kg"
                f" · GWP {_format_value(lca.get('gwp_kg_co2e_per_kg'))} "
                "kg CO2e/kg\n",
            )
    elif plot_type == "tea_pareto":
        result.append("Admitted process-record Pareto frontier\n", style="bold")
        result.append(
            f"{payload.get('x_metric')} ({payload.get('x_metric_unit')}) ↔ "
            f"{payload.get('y_metric')} ({payload.get('y_metric_unit')})\n",
        )
        frontier_ids = set(payload.get("frontier_record_ids") or [])
        for point in payload.get("points") or []:
            result.append(
                f"{point.get('label')}: "
                f"{_format_value(point.get('x_value'))}, "
                f"{_format_value(point.get('y_value'))}"
                + (
                    " · efficient\n"
                    if point.get("record_id") in frontier_ids
                    else " · dominated\n"
                ),
            )
    elif plot_type == "tea_sensitivity_tornado":
        result.append("Admitted process sensitivity\n", style="bold")
        result.append(
            f"C1 baseline: "
            f"{_format_value(payload.get('baseline_metric_value'))} "
            f"{payload.get('metric_unit')}\n"
        )
        for row in payload.get("sensitivity_rows") or []:
            result.append(
                f"{str(row.get('sensitivity_axis') or '').replace('_', ' ')}: "
                f"{row.get('lower_level')} "
                f"{_format_value(row.get('lower_delta'))} · "
                f"{row.get('upper_level')} "
                f"{_format_value(row.get('upper_delta'))}\n"
            )
    elif plot_type == "contaminant_screen_card":
        result.append("Contaminant screen cards\n", style="bold")
        for card in payload.get("cards") or []:
            result.append(
                f"{card.get('stage_label')} · "
                f"{card.get('route_solvent') or card.get('solvent')}\n",
                style="bold",
            )
            for family in card.get("families") or []:
                statuses = ", ".join(
                    f"{item.get('criterion')}: {item.get('status')}"
                    for item in family.get("criteria") or []
                )
                result.append(
                    f"{family.get('contaminant_family')}: {statuses}\n",
                )
    elif plot_type == "contaminant_partitioning_map":
        result.append("Contaminant partitioning map\n", style="bold")
        criterion = payload.get("pass_criterion") or {}
        if criterion.get("pass_meaning"):
            result.append(f"{criterion['pass_meaning']}\n")
        for facet in payload.get("facets") or []:
            result.append(
                f"{facet.get('stage_label')} · {facet.get('solvent')}\n",
                style="bold",
            )
            if facet.get("evidence_status") != "evaluated":
                result.append(f"{facet.get('evidence_reason')}\n")
                continue
            points = [
                item for item in facet.get("points") or []
                if isinstance(item.get("logd"), (int, float))
            ]
            removable = [item for item in points if item.get("logd_pass")]
            result.append(
                f"{len(removable)} of {len(points)} screened contaminants "
                f"partition into the solvent\n",
            )
    elif plot_type == "precipitation_ladder":
        result.append("Full-feed precipitation proxy order\n", style="bold")
        for row in payload.get("candidates") or []:
            crossings = row.get("precipitation_proxy_crossings_c") or {}
            order = row.get("precipitation_proxy_order") or []
            result.append(f"{row.get('solvent')}: ")
            result.append(" → ".join(
                f"{polymer} {_format_value(crossings.get(polymer))} °C" for polymer in order
            ) + "\n")
    elif plot_type == "pairwise_gap_ranking":
        result.append("Pairwise best-gap ranking\n", style="bold")
        for row in list(payload.get("ranked_pairs") or [])[:8]:
            result.append(
                f"{' / '.join(row.get('polymers') or [])}: "
                f"{_format_value(row.get('maximum_absolute_gap_pct'))} points\n"
            )
    elif plot_type in {"tea_stage_economics", "tea_gwp_attribution", "tea_scale_curves"}:
        result.append(f"{payload.get('title')}\n", style="bold")
        for row in payload.get("rows") or []:
            label = row.get("label") or row.get("processing_capacity_mt_per_yr") or "row"
            result.append(f"{label}: ")
            for key, unit in (
                ("msp_usd_per_kg", "USD/kg"), ("tci_usd", "USD TCI"),
                ("modeled_stage_gwp_contribution_fraction", "GWP fraction"),
                ("gwp_t_co2e_per_mt", "t CO2e/t"),
            ):
                if row.get(key) is not None:
                    result.append(f"{_format_value(row[key])} {unit} · ")
            result.append("\n")
    elif plot_type == "substitution_tradeoff":
        result.append("Solvent-substitution tradeoff\n", style="bold")
        current = payload.get("current_reference") or {}
        if current:
            result.append(f"Current · {current.get('solvent')}: reference origin\n")
        for row in payload.get("candidate_substitutions") or []:
            result.append(
                f"{row.get('solvent')}: G-score Δ {_format_value(row.get('g_score_change'))} · "
                f"selectivity loss {_format_value(row.get('selectivity_loss_points'))} points\n"
            )
    elif plot_type == "hsp_red_vs_fitted_solubility":
        result.append(
            f"Hansen RED and thermodynamic solubility at "
            f"{_format_value(payload.get('temperature_c'))} °C\n",
            style="bold",
        )
        for row in payload.get("rows") or []:
            fitted = row.get("fitted_solubility_wt_pct")
            result.append(
                f"{row.get('polymer')} / {row.get('solvent')} "
                f"[{row.get('polymer_source_record_id')}]: RED "
                f"{_format_value(row.get('red'))} · {row.get('solubility_method') or 'unavailable'} "
                f"{_format_value(fitted)} wt%\n"
            )
    elif plot_type == "hsp_family_dot_range":
        result.append("Temperature-independent Hansen family evidence\n", style="bold")
        family_rows = list(payload.get("rows") or [])
        for row in family_rows[:18]:
            result.append(
                f"{row.get('source_label')} [{row.get('source_record_id')}]: "
                f"dD {_format_value(row.get('dispersion'))} · "
                f"dP {_format_value(row.get('polar'))} · "
                f"dH {_format_value(row.get('hydrogen_bonding'))} · "
                f"{str(row.get('relationship') or 'unclassified').replace('_', ' ')}\n"
            )
        if len(family_rows) > 18:
            result.append(
                f"{len(family_rows) - 18} additional source records are retained "
                "in the full figure sidecar.\n"
            )
        for issue in payload.get("resolution_issues") or []:
            result.append(
                f"Not resolved: {issue.get('query')} — {issue.get('reason')}\n"
            )
    elif str(plot_type).startswith("hsp_"):
        result.append("Temperature-independent qualitative Hansen screen\n", style="bold")
        for row in payload.get("rows") or []:
            result.append(
                f"{row.get('polymer')} / {row.get('solvent')}: RED "
                f"{float(row.get('red')):.3f} · "
                f"{'inside' if row.get('inside_hansen_sphere') else 'outside'} sphere\n"
            )
        for issue in payload.get("resolution_issues") or []:
            result.append(
                f"Not resolved: {issue.get('query')} — {issue.get('reason')}\n"
            )
    elif plot_type == "research_retrieval_scores":
        result.append("Literature retrieval scores\n", style="bold")
        for row in payload.get("results") or []:
            result.append(
                f"{row.get('citation_id') or row.get('chunk_id')}: "
                f"BM25 {float(row.get('sparse_score') or 0):.3f} · "
                f"dense {float(row.get('dense_score') or 0):.3f} · "
                f"final {float(row.get('final_score') or 0):.3f}\n"
            )
    elif plot_type == "separation_state_map":
        result.append("Recursive route alternatives\n", style="bold")
        for item in payload.get("routes") or []:
            result.append(
                f"{item.get('rank')}. {' → '.join(item.get('sequence') or [])} · "
                f"bottleneck {float(item.get('bottleneck_selectivity_pct') or 0):.2f} points\n"
            )
    else:
        return None
    return result


def build_terminal_plot_panel(path: str | Path, *, console_width: int = 100):
    """Render exact sidecar data in-terminal and retain the full-resolution path."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.text import Text

    resolved = Path(path).resolve()
    try:
        payload = json.loads(resolved.with_suffix(".terminal.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    preview = _series_preview(payload, max(48, min(96, console_width - 8)), 24)
    preview = preview or _structured_preview(payload)
    if preview is None:
        return None
    description = Text(str(payload.get("caption") or ""))
    path_line = Text.assemble(("Full-resolution PNG  ", "dim"), (str(resolved), "cyan"))
    return Panel(
        Group(preview, Text(), description, Text(), path_line),
        title="[bold blue]DISSOLVE PLOT[/]", border_style="blue",
        expand=False, padding=(0, 1),
    )


def _validate_visualization_plan(
    calls: Sequence[dict[str, Any]], original_query: str,
    typed_context: dict[str, Any],
) -> tuple[str, ...]:
    """Keep destinations and precipitation-view semantics harness-owned."""
    violations = []
    precipitation_rows, _ = candidate_evidence(
        typed_context, {CANDIDATE_SHAPE_PRECIPITATION},
    )
    pair_evidence = _precipitation_crossing_cardinality(precipitation_rows) == 2
    # Stored evidence, not prose, identifies pair-only precipitation data.
    full_feed_evidence = (
        _precipitation_crossing_cardinality(precipitation_rows) >= 3
    )
    # Three-or-more typed crossings support the ladder; wording cannot
    # override the declared view or the stored evidence shape.
    explicit_polymers = list(dict.fromkeys(
        identity for label, _ in thermo.find_polymer_mentions(original_query)
        for identity in thermo.expand_polymer_identity(label)
    ))
    pair_request = bool(
        pair_evidence
        and not full_feed_evidence
        and (
            len(explicit_polymers) == 2
            or re.search(r"\b(?:versus|vs\.?|between)\b", original_query, re.I)
        )
    )
    violations.extend(_declared_visualization_plan_violations(
        calls, typed_context,
    ))
    for call in calls:
        name = str(call.get("name") or "")
        arguments = call.get("args") or {}
        if not isinstance(arguments, dict):
            return ("Visualization arguments must be one JSON object.",)
        forbidden = sorted({"output_dir", "output_path"} & set(arguments))
        if forbidden:
            violations.append(
                "Omit model-supplied filesystem destinations "
                + ", ".join(forbidden)
                + "; the harness owns the validated artifact directory and filename."
            )
        if name == "plot_separation_analysis" and pair_evidence and arguments.get("view") == "precipitation_ladder":
            violations.append(
                f"The {'requested pair/subset' if pair_request else 'stored evidence'} "
                "contains two-polymer crossings; use "
                "view='precipitation' rather than precipitation_ladder."
            )
        if name == "plot_separation_analysis" and full_feed_evidence and arguments.get("view") == "precipitation":
            violations.append(
                "The stored evidence contains three-or-more-polymer crossings; "
                "use view='precipitation_ladder' rather than precipitation."
            )
    return tuple(violations)


def _precipitation_crossing_cardinality(
    rows: Sequence[dict[str, Any]],
) -> int:
    """Return the largest typed crossing set without inferring plot intent."""
    cardinalities = []
    for row in rows:
        crossings = row.get("precipitation_proxy_crossings_c")
        if isinstance(crossings, dict):
            cardinalities.append(sum(value is not None for value in crossings.values()))
            continue
        pair = {
            str(row.get(polymer_key))
            for polymer_key, crossing_key in (
                ("first_polymer", "first_precipitation_proxy_c"),
                ("second_polymer", "second_precipitation_proxy_c"),
            )
            if row.get(polymer_key) and row.get(crossing_key) is not None
        }
        cardinalities.append(len(pair))
    return max(cardinalities, default=0)


def _project_visualization_context(
    typed_context: dict[str, Any],
    _original_query: str,
) -> dict[str, Any]:
    """Drop known wrong-surface options while retaining an audit annotation."""
    projected = copy.deepcopy(typed_context)
    _drop_inapplicable_visualization_options(projected)
    return projected


VISUALIZATION_PROMPT = """You are DISSOLVE's visualization specialist. Call
exactly one plotting tool. The current query and resolved objective—not the
broader session polymer list—own the plotted material subset. Pass exactly the
materials named there unless the current user explicitly adds another.
When declared_deliverable provides visualization_view, copy that exact value
to the matching tool's view argument when that tool exposes one. The shared
typed registry resolves planner aliases. Every visualization_options entry
that maps to a tool argument must be present in the call; renderer-invariant
entries are satisfied by their registered view.
For temperature-dependent curves, preserve every
polymer, solvent, temperature bound, strict/inclusive bound, and axis request;
use plot_solubility_curves. Pass reference_temperature_c only when the user asks
to mark an operating limit inside that plotted range; pass
reference_solubility_wt_pct only for a named concentration target. Use
highlight_solvent for one requested full-colour series against grey context, and
show_markers=false when the user requests a line-only publication view. The current user's explicit bounds override older
typed bounds: "from A to B" is inclusive unless the current request itself says
below, under, or less than B. Never inherit strict_maximum=true into a new
inclusive range. For ranked candidates at one exact temperature, use
plot_fixed_temperature_separation; never transcribe values because the tool
regenerates them. For a multistage tree/PFD, state map, selectivity heatmap,
precipitation curve, or atmospheric-feasibility plot, use
plot_separation_analysis with the matching view. Use route_windows only when two
stored route screens for the same feed are being compared; it consumes both
private typed results and must not rerun either screen. For a current stored
precipitation-order result, pass the requested polymers and precipitation view;
the tool consumes the stored solvent, proxy, dissolution bound, and crossings,
so do not copy those values into tool arguments. A request mentioning
precipitation behavior, dropout, or cooling from the dissolution temperature is
this stored precipitation view even if the parent objective also says
“solubility curves”; never use plot_solubility_curves for it. For an
already established single-solvent safety card, use plot_analysis_results with
view=safety_card. Use plot_comparison_results with result_kind=safety only for a
multi-solvent safety comparison. For an already established contaminant, TEA/LCA,
or optimization comparison, use plot_comparison_results with exact typed entities
and setpoints. Never invent data or accept arbitrary numeric series. This
specialist cannot compute the
first optimization frontier; a frontier, knee, or trade-off request without an
existing optimization result belongs to optimization-engineer. For direct
admitted process records, use plot_analysis_results with tea_case_bars for a
chosen metric across stored energy cases, or tea_sensitivity_tornado for stored
sensitivity endpoints against their C1 baseline; copy the requested metric and
target polymer without supplying numeric values. Use TEA metrics stage_economics,
gwp_attribution, or scale_curves only when that exact typed basis exists; use
result_kind scale_pareto only when both the stored multi-point scale evidence and
the already-computed frontier share one route signature. Use result_kind
substitution for a stored route-substitution tradeoff. For a stored
full-feed cooling order, request precipitation_ladder. For a current pairwise
thermodynamic screen, use plot_analysis_results with pair_gap; for HSP,
statistical, Tg, or thermal results use its corresponding view. For a joined HSP
and fitted-solubility result, use method_comparison and preserve their distinct
evidence meanings; RED=1 is never a fitted-solubility threshold. These tools
consume typed state and never accept model-supplied values. For an HSP heatmap
with a long solvent list, use hsp_heatmap_orientation="materials_columns";
highlight_pairs contains only material/solvent cells explicitly named for
emphasis, and supported solvent classes are grouped automatically. For current local-corpus retrieval scores, use
plot_research_results; it consumes typed citation/score state and never image
bytes or model-supplied values. When the current query or resolved objective
names a subset of materials, pass exactly that subset; do not add other materials
from the broader typed feed. Ignore scores copied into the objective because
typed state is authoritative. Return no prose; the parent model
explains the validated artifact. Never pass output_dir or output_path; the
harness owns the artifact destination and validates the materialized file."""



def _first_requested_hsp_polymer(
    rows: Sequence[dict[str, Any]],
    requested_polymers: Sequence[str],
) -> str:
    """Choose the first requested polymer represented in an HSP result."""
    available = {str(row.get("polymer") or "") for row in rows}
    for polymer in requested_polymers:
        if polymer in available:
            return polymer
    if rows:
        return str(rows[0]["polymer"])
    raise ValueError("An HSP material selection requires at least one result row.")


def _hsp_interaction_surface(
    center: dict[str, Any],
    interaction_radius: float,
    *,
    azimuth_samples: int = 49,
    polar_samples: int = 25,
) -> tuple[Any, Any, Any]:
    """Return the RED=1 boundary in raw Hansen-coordinate units."""
    import numpy as np

    azimuth = np.linspace(0.0, 2.0 * np.pi, azimuth_samples)
    polar = np.linspace(0.0, np.pi, polar_samples)
    radius = float(interaction_radius)
    sin_polar = np.sin(polar)
    dispersion = (
        float(center["dispersion"])
        + radius / 2.0 * np.outer(np.cos(azimuth), sin_polar)
    )
    polarity = (
        float(center["polar"])
        + radius * np.outer(np.sin(azimuth), sin_polar)
    )
    hydrogen_bonding = (
        float(center["hydrogen_bonding"])
        + radius * np.outer(np.ones_like(azimuth), np.cos(polar))
    )
    return dispersion, polarity, hydrogen_bonding


def _plot_hsp_interaction_sphere(
    plt: Any,
    selected_polymer: str,
    selected_rows: Sequence[dict[str, Any]],
) -> tuple[Any, str, dict[str, Any]]:
    """Render one explicit polymer record and its true Hansen interaction boundary."""
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    from matplotlib.ticker import MaxNLocator

    first = selected_rows[0]
    center = first["polymer_hsp"]
    radius = float(first["interaction_radius"])
    dispersion, polarity, hydrogen_bonding = _hsp_interaction_surface(
        center, radius,
    )
    inside_color, outside_color = "#D55E00", "#0072B2"
    surface_color = "#56B4E9"

    fig = plt.figure(figsize=(9.2, 7.0))
    ax = fig.add_subplot(111, projection="3d")
    ax.set_proj_type("ortho")
    ax.plot_surface(
        dispersion, polarity, hydrogen_bonding,
        color=surface_color, alpha=.10, linewidth=0, shade=False,
    )
    ax.plot_wireframe(
        dispersion, polarity, hydrogen_bonding,
        color=surface_color, alpha=.38, linewidth=.55,
        rstride=4, cstride=4,
    )
    ax.scatter(
        [float(center["dispersion"])],
        [float(center["polar"])],
        [float(center["hydrogen_bonding"])],
        color="#000000", marker="*", s=150, edgecolor="#000000", linewidth=.7,
        depthshade=False,
    )
    inside_rows, outside_rows = [], []
    for row in selected_rows:
        solvent = row["solvent_hsp"]
        inside = float(row["red"]) < 1.0
        (inside_rows if inside else outside_rows).append(row)
        ax.scatter(
            [float(solvent["dispersion"])],
            [float(solvent["polar"])],
            [float(solvent["hydrogen_bonding"])],
            color=inside_color if inside else outside_color,
            marker="o" if inside else "^",
            s=62 if inside else 48,
            edgecolor="#000000", linewidth=.5, depthshade=False,
        )
    plot_title = (
        f"{selected_polymer} Hansen interaction sphere "
        f"(R₀ = {radius:g} MPa½)"
    )
    ax.set(
        xlabel="",
        ylabel="",
        zlabel="",
        title=plot_title,
    )
    ax.view_init(elev=23, azim=-45)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.pane.set_alpha(.04)
        axis.set_major_locator(MaxNLocator(nbins=3))
    ax.xaxis.set_major_locator(MaxNLocator(nbins=2))
    ax.zaxis.set_major_locator(MaxNLocator(nbins=4))
    ax.tick_params(pad=0)
    x_limits, y_limits, z_limits = ax.get_xlim3d(), ax.get_ylim3d(), ax.get_zlim3d()
    ax.set_box_aspect((
        2.0 * abs(x_limits[1] - x_limits[0]),
        abs(y_limits[1] - y_limits[0]),
        abs(z_limits[1] - z_limits[0]),
    ), zoom=.73)
    ax.text2D(.25, .055, "δD", transform=ax.transAxes)
    ax.text2D(.76, .11, "δP", transform=ax.transAxes)
    ax.text2D(.87, .52, "δH", transform=ax.transAxes)
    ax.text2D(
        .5, .91, "All axes in MPa½",
        transform=ax.transAxes, ha="center",
    )
    handles = [
        Line2D(
            [0], [0], linestyle="", marker="*", markersize=12,
            markerfacecolor="#000000", markeredgecolor="#000000",
            label=f"{selected_polymer} center",
        ),
        Line2D(
            [0], [0], linestyle="", marker="o", markersize=8,
            markerfacecolor=inside_color, markeredgecolor="#000000",
            label="Inside sphere (RED < 1)",
        ),
        Line2D(
            [0], [0], linestyle="", marker="^", markersize=8,
            markerfacecolor=outside_color, markeredgecolor="#000000",
            label="At/outside sphere (RED ≥ 1)",
        ),
        Patch(
            facecolor=surface_color, edgecolor=surface_color, alpha=.35,
            label="RED = 1 boundary",
        ),
    ]
    fig.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(.5, .015),
        ncol=2, columnspacing=2.5,
    )
    fig.subplots_adjust(left=.02, right=.98, bottom=.19, top=.88)
    return fig, plot_title, {
        "selected_polymer": selected_polymer,
        "selected_polymer_source_record_id": first["polymer_source_record_id"],
        "interaction_radius": radius,
        "interaction_surface": {
            "equation": (
                "4*(dD-dD_polymer)^2 + (dP-dP_polymer)^2 "
                "+ (dH-dH_polymer)^2 = R0^2"
            ),
            "red_boundary": 1.0,
            "dispersion_semiaxis": radius / 2.0,
            "polar_semiaxis": radius,
            "hydrogen_bonding_semiaxis": radius,
            "coordinate_units": "MPa^0.5",
            "display_aspect": (
                "Raw HSP ticks retained; the dD display scale is doubled so "
                "the Hansen-metric boundary is visually spherical."
            ),
        },
        "plotted_rows": list(selected_rows),
        "inside_sphere_solvents": [str(row["solvent"]) for row in inside_rows],
        "outside_sphere_solvents": [str(row["solvent"]) for row in outside_rows],
        "sphere_encoding": {
            "polymer": {"color": "#000000", "marker": "*"},
            "inside": {
                "color": inside_color, "marker": "o",
                "rule": "RED < 1", "direct_labels": False,
            },
            "at_or_outside": {
                "color": outside_color, "marker": "^", "rule": "RED >= 1",
            },
            "legend_location": "outside_axes_below",
        },
    }


def _plot_hsp_normalized_radar(
    plt: Any,
    selected_polymer: str,
    selected_rows: Sequence[dict[str, Any]],
    maximum_solvent_series: int,
) -> tuple[Any, str, dict[str, Any]]:
    """Render an axis-normalized HSP radar with an explicit bounded selection."""
    from math import pi

    keys = ("dispersion", "polar", "hydrogen_bonding")
    labels = ("δD", "δP", "δH")
    first = selected_rows[0]
    center = first["polymer_hsp"]
    indexed_rows = list(enumerate(selected_rows))
    ranked_rows = sorted(
        indexed_rows,
        key=lambda item: (
            float(item[1]["red"]) >= 1.0,
            float(item[1]["red"]),
            item[0],
        ),
    )
    plotted_rows = [
        row for _, row in ranked_rows[:maximum_solvent_series]
    ]
    omitted_rows = [
        row for _, row in ranked_rows[maximum_solvent_series:]
    ]
    all_values = [
        center,
        *(row["solvent_hsp"] for row in plotted_rows),
    ]
    axis_maxima = {
        key: max(float(values[key]) for values in all_values) or 1.0
        for key in keys
    }

    def normalized(values: dict[str, Any]) -> list[float]:
        return [
            float(values[key]) / axis_maxima[key]
            for key in keys
        ]

    angles = [index / len(keys) * 2.0 * pi for index in range(len(keys))]
    closed_angles = [*angles, angles[0]]
    fig = plt.figure(figsize=(10.4, 6.2))
    axis = fig.add_axes([.05, .13, .60, .73], polar=True)
    key_axis = fig.add_axes([.68, .12, .30, .74])
    key_axis.set_axis_off()
    polymer_points = normalized(center)
    polymer_line, = axis.plot(
        closed_angles, [*polymer_points, polymer_points[0]],
        color="#000000", linewidth=2.7, marker="*", markersize=8,
        label=f"{selected_polymer} polymer", zorder=5,
    )
    axis.fill(
        closed_angles, [*polymer_points, polymer_points[0]],
        color="#000000", alpha=.08, zorder=1,
    )
    solvent_lines = []
    radar_series = [{
        "name": selected_polymer,
        "kind": "polymer",
        "source_record_id": first["polymer_source_record_id"],
        "raw_hsp": dict(center),
        "normalized_hsp": dict(zip(keys, polymer_points)),
        "color": "#000000",
    }]
    for index, row in enumerate(plotted_rows):
        points = normalized(row["solvent_hsp"])
        line, = axis.plot(
            closed_angles, [*points, points[0]],
            color=_COLORS[index], linewidth=1.5, marker="o", markersize=3.5,
            alpha=.88,
            label=f"{row['solvent']} (RED {float(row['red']):.2f})",
        )
        solvent_lines.append(line)
        radar_series.append({
            "name": row["solvent"],
            "kind": "solvent",
            "source_record_id": row["solvent_source_record_id"],
            "red": float(row["red"]),
            "inside_hansen_sphere": float(row["red"]) < 1.0,
            "raw_hsp": dict(row["solvent_hsp"]),
            "normalized_hsp": dict(zip(keys, points)),
            "color": _COLORS[index],
        })
    axis.set_xticks(angles, labels)
    axis.set_ylim(0.0, 1.0)
    axis.set_yticks(
        [.25, .50, .75, 1.0],
        ["25%", "50%", "75%", "100%"],
    )
    axis.set_rlabel_position(18)
    plot_title = f"{selected_polymer} normalized Hansen parameter radar"
    fig.suptitle(plot_title)
    key_axis.legend(
        handles=[polymer_line, *solvent_lines],
        loc="upper left", bbox_to_anchor=(0, 1),
        borderaxespad=0, labelspacing=.65,
    )
    selection_note = (
        f"Showing {len(plotted_rows)} of {len(selected_rows)} solvents.\n"
        "Selection: inside sphere first, then lowest RED.\n"
        "Each axis is a fraction of its displayed-series maximum."
    )
    key_axis.text(0, .12, selection_note, ha="left", va="bottom")
    return fig, plot_title, {
        "selected_polymer": selected_polymer,
        "selected_polymer_source_record_id": first["polymer_source_record_id"],
        "plotted_rows": list(plotted_rows),
        "radar_series": radar_series,
        "radar_normalization": {
            "method": "fraction_of_axis_maximum",
            "axis_keys": list(keys),
            "axis_maxima": axis_maxima,
            "basis": "polymer and every displayed solvent series",
            "radial_scale": "0-100%",
        },
        "radar_selection": {
            "rule": "inside_sphere_first_then_lowest_red",
            "maximum_solvent_series": maximum_solvent_series,
            "requested_solvent_count": len(selected_rows),
            "shown_solvents": [str(row["solvent"]) for row in plotted_rows],
            "omitted_solvents": [str(row["solvent"]) for row in omitted_rows],
            "disclosed_on_plot": True,
        },
        "radar_encoding": {
            "polymer": {
                "color": "#000000", "marker": "*", "linewidth": 2.7,
                "filled": True,
            },
            "solvent_colors": [_COLORS[index] for index in range(len(plotted_rows))],
            "solvent_marker": "o",
            "color_cycle_wrapped": False,
        },
    }


_HSP_SOLVENT_CLASS_ORDER = (
    "Alkanes",
    "Aromatics",
    "Chlorinated",
    "Ketones / esters",
    "Alcohols",
    "Polar aprotic",
)
_HSP_SOLVENT_CLASSES = {
    "dodecane": "Alkanes",
    "n-hexane": "Alkanes",
    "hexane": "Alkanes",
    "heptane": "Alkanes",
    "n-heptane": "Alkanes",
    "cyclohexane": "Alkanes",
    "benzene": "Aromatics",
    "toluene": "Aromatics",
    "o-xylene": "Aromatics",
    "p-xylene": "Aromatics",
    "diphenyl ether": "Aromatics",
    "chloroform": "Chlorinated",
    "dichloromethane": "Chlorinated",
    "acetone": "Ketones / esters",
    "mek": "Ketones / esters",
    "ethyl acetate": "Ketones / esters",
    "methyl acetate": "Ketones / esters",
    "gvl": "Ketones / esters",
    "cyclohexanol": "Alcohols",
    "ethylene glycol": "Alcohols",
    "ethanol": "Alcohols",
    "methanol": "Alcohols",
    "isopropanol": "Alcohols",
    "1-propanol": "Alcohols",
    "propylene glycol": "Alcohols",
    "tert-butanol": "Alcohols",
    "thf": "Polar aprotic",
    "dmf": "Polar aprotic",
    "dmso": "Polar aprotic",
    "nmp": "Polar aprotic",
}


def _hsp_red_color_scale(
    maximum_red: float,
) -> tuple[Any, Any, list[float], dict[str, Any]]:
    """Build a hard categorical break below RED=1 and a blue ramp above it."""
    import numpy as np
    from matplotlib.colors import (
        BoundaryNorm, LinearSegmentedColormap, ListedColormap, to_hex,
    )

    scale_maximum = max(1.01, float(maximum_red))
    inside_color = "#D73027"
    light_blue, dark_blue = "#DEEBF7", "#08519C"
    blue_ramp = LinearSegmentedColormap.from_list(
        "hsp_red_outside_blue", [light_blue, dark_blue], N=256,
    )
    blue_colors = blue_ramp(np.linspace(0.0, 1.0, 256))
    colormap = ListedColormap(
        [inside_color, *blue_colors],
        name="hsp_red_categorical_inside_blue_outside",
    )
    blue_boundaries = np.linspace(1.0, scale_maximum, 257)
    boundaries = [0.0, *(float(value) for value in blue_boundaries)]
    normalization = BoundaryNorm(boundaries, colormap.N, clip=True)
    return colormap, normalization, boundaries, {
        "inside_rule": "RED < 1",
        "inside_color": inside_color,
        "inside_color_mode": "solid_category_no_gradient",
        "at_or_outside_rule": "RED >= 1",
        "at_threshold_color": to_hex(blue_colors[0], keep_alpha=False).upper(),
        "outside_maximum_color": to_hex(
            blue_colors[-1], keep_alpha=False,
        ).upper(),
        "outside_color_mode": "sequential_blue_light_to_dark",
        "hard_break_value": 1.0,
        "scale_minimum": 0.0,
        "scale_maximum": scale_maximum,
        "colorbar": "solid red block below 1 followed by blue ramp",
        "text_color": "#000000",
    }


def _group_hsp_solvents(
    solvents: Sequence[str],
) -> tuple[list[str], list[dict[str, Any]], dict[str, str]]:
    classified = {
        solvent: _HSP_SOLVENT_CLASSES.get(solvent.casefold())
        for solvent in solvents
    }
    if not any(classified.values()):
        return list(solvents), [], {}
    buckets = {
        group: [
            solvent for solvent in solvents if classified[solvent] == group
        ]
        for group in _HSP_SOLVENT_CLASS_ORDER
    }
    unclassified = [
        solvent for solvent in solvents if classified[solvent] is None
    ]
    ordered_groups = [
        (group, buckets[group])
        for group in _HSP_SOLVENT_CLASS_ORDER
        if buckets[group]
    ]
    if unclassified:
        ordered_groups.append(("Other / unclassified", unclassified))
    ordered_solvents, groups = [], []
    group_by_solvent: dict[str, str] = {}
    for group, members in ordered_groups:
        start = len(ordered_solvents)
        ordered_solvents.extend(members)
        group_by_solvent.update({member: group for member in members})
        groups.append({
            "class": group,
            "start_index": start,
            "end_index": len(ordered_solvents) - 1,
            "solvents": list(members),
        })
    return ordered_solvents, groups, group_by_solvent


def _select_hsp_heatmap_rows(
    rows: Sequence[dict[str, Any]],
    polymers: Sequence[str],
    solvents: Sequence[str],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    selected_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    selections, plotted_rows = [], []
    for polymer in polymers:
        candidates = [row for row in rows if row.get("polymer") == polymer]
        if not candidates:
            return {}, [], [], f"No HSP source record represents {polymer}."
        source_ids = list(dict.fromkeys(
            row["polymer_source_record_id"] for row in candidates
        ))
        chosen_source_id = next(
            (
                source_id
                for source_id in source_ids
                if any(
                    row["polymer_source_record_id"] == source_id
                    and row.get("polymer_headline_eligible") is True
                    for row in candidates
                )
            ),
            source_ids[0],
        )
        chosen_rows = [
            row for row in candidates
            if row["polymer_source_record_id"] == chosen_source_id
        ]
        for solvent in solvents:
            matching = next(
                (
                    row for row in chosen_rows
                    if row.get("solvent") == solvent
                ),
                None,
            )
            if matching is None:
                return (
                    {}, [], [],
                    f"Selected HSP record {chosen_source_id} for {polymer} "
                    f"has no {solvent} row.",
                )
            selected_by_pair[(polymer, solvent)] = matching
            plotted_rows.append(matching)
        first = chosen_rows[0]
        selections.append({
            "polymer": polymer,
            "selected_polymer_source_record_id": chosen_source_id,
            "polymer_source_label": first.get("polymer_source_label"),
            "selection_rule": (
                "first headline-eligible source record in typed result order"
            ),
            "alternative_polymer_source_record_ids": [
                source_id for source_id in source_ids
                if source_id != chosen_source_id
            ],
        })
    return selected_by_pair, selections, plotted_rows, None


def _plot_hsp_red_heatmap(
    plt: Any,
    *,
    rows: Sequence[dict[str, Any]],
    polymers: Sequence[str],
    solvents: Sequence[str],
    orientation: str,
    highlight_pairs: Sequence[dict[str, str]],
) -> tuple[Any, str, dict[str, Any], str | None]:
    """Render HSP RED with categorical inside-sphere and sequential outside colors."""
    from matplotlib.patches import Rectangle

    if orientation not in {"materials_rows", "materials_columns"}:
        return (
            None, "", {},
            "HSP heatmap orientation must be materials_rows or materials_columns.",
        )
    (
        selected_by_pair,
        record_selections,
        plotted_rows,
        selection_error,
    ) = _select_hsp_heatmap_rows(rows, polymers, solvents)
    if selection_error:
        return None, "", {}, selection_error
    display_solvents, solvent_groups, group_by_solvent = (
        _group_hsp_solvents(solvents)
    )
    matrix = [
        [
            float(selected_by_pair[(polymer, solvent)]["red"])
            for solvent in solvents
        ]
        for polymer in polymers
    ]
    display_matrix = [
        [
            float(selected_by_pair[(polymer, solvent)]["red"])
            for solvent in display_solvents
        ]
        for polymer in polymers
    ]
    maximum = max(max(values) for values in matrix)
    colormap, normalization, boundaries, encoding = _hsp_red_color_scale(
        maximum,
    )
    if orientation == "materials_columns":
        image_values = [
            [display_matrix[x_index][y_index] for x_index in range(len(polymers))]
            for y_index in range(len(display_solvents))
        ]
        fig, axis = plt.subplots(figsize=(
            max(8.3, .95 * len(polymers) + 5.4),
            max(5.2, .56 * len(display_solvents) + 2.7),
        ))
        axis.set_xticks(range(len(polymers)), polymers)
        y_labels = [
            (
                f"{group_by_solvent[solvent]} · {solvent}"
                if group_by_solvent else solvent
            )
            for solvent in display_solvents
        ]
        axis.set_yticks(range(len(display_solvents)), y_labels)
        axis.set_xlabel("Materials")
        axis.set_ylabel("Solvents grouped by class")
    else:
        image_values = display_matrix
        fig, axis = plt.subplots(figsize=(
            max(7.2, .72 * len(display_solvents) + 3.6),
            max(4.4, .58 * len(polymers) + 2.5),
        ))
        x_labels = [
            (
                f"{solvent}\n{group_by_solvent[solvent]}"
                if group_by_solvent else solvent
            )
            for solvent in display_solvents
        ]
        _set_categorical_x_labels(
            fig, axis, range(len(display_solvents)), x_labels,
        )
        axis.set_yticks(range(len(polymers)), polymers)
        axis.set_xlabel("Solvents grouped by class")
        axis.set_ylabel("Materials")
    image = axis.imshow(
        image_values,
        cmap=colormap,
        norm=normalization,
        aspect="auto",
    )
    for group in solvent_groups[:-1]:
        separator = float(group["end_index"]) + .5
        if orientation == "materials_columns":
            axis.axhline(separator, color="#555555", linewidth=.8)
        else:
            axis.axvline(separator, color="#555555", linewidth=.8)
    plot_title = "Hansen relative energy difference (RED)"
    axis.set_title(plot_title)
    colorbar = fig.colorbar(
        image, ax=axis, boundaries=boundaries, spacing="proportional",
        label="RED: inside category (<1) / outside scale (≥1)",
        pad=.03,
    )
    colorbar.ax.axhline(1.0, color="#000000", linewidth=1.3)
    tick_values = [0.0, 1.0]
    if encoding["scale_maximum"] > 1.0:
        span = encoding["scale_maximum"] - 1.0
        tick_values.extend([
            1.0 + span / 3.0,
            1.0 + 2.0 * span / 3.0,
            encoding["scale_maximum"],
        ])
    colorbar.set_ticks(list(dict.fromkeys(round(value, 3) for value in tick_values)))
    if len(polymers) * len(display_solvents) <= 40:
        for y_index, values in enumerate(image_values):
            for x_index, value in enumerate(values):
                axis.text(
                    x_index, y_index, f"{value:.2f}",
                    ha="center", va="center", color="#000000",
                    bbox={
                        "facecolor": "#FFFFFF", "alpha": .74,
                        "edgecolor": "none", "pad": 1.2,
                    },
                )
    polymer_lookup = {polymer.casefold(): polymer for polymer in polymers}
    solvent_lookup = {
        solvent.casefold(): solvent for solvent in display_solvents
    }
    highlighted_cells, unmatched_highlights = [], []
    for requested_pair in highlight_pairs:
        if not isinstance(requested_pair, dict):
            unmatched_highlights.append({
                "requested": requested_pair,
                "reason": "highlight must be one material/solvent object",
            })
            continue
        requested_polymer = str(
            requested_pair.get("polymer")
            or requested_pair.get("material")
            or ""
        ).strip()
        requested_solvent = str(
            requested_pair.get("solvent") or ""
        ).strip()
        polymer = polymer_lookup.get(requested_polymer.casefold())
        solvent = solvent_lookup.get(requested_solvent.casefold())
        if polymer is None or solvent is None:
            unmatched_highlights.append({
                "requested": dict(requested_pair),
                "reason": "material or solvent is absent from the displayed matrix",
            })
            continue
        polymer_index = list(polymers).index(polymer)
        solvent_index = display_solvents.index(solvent)
        x_index, y_index = (
            (polymer_index, solvent_index)
            if orientation == "materials_columns"
            else (solvent_index, polymer_index)
        )
        axis.add_patch(Rectangle(
            (x_index - .47, y_index - .47), .94, .94,
            fill=False, edgecolor="#000000", linewidth=2.3,
        ))
        highlighted_cells.append({
            "polymer": polymer,
            "solvent": solvent,
            "x_index": x_index,
            "y_index": y_index,
            "red": float(selected_by_pair[(polymer, solvent)]["red"]),
        })
    fig.tight_layout()
    if orientation == "materials_rows":
        _set_categorical_x_labels(
            fig, axis, range(len(display_solvents)), x_labels,
        )
    return fig, plot_title, {
        "matrix": matrix,
        "matrix_polymers": list(polymers),
        "matrix_solvents": list(solvents),
        "display_matrix": image_values,
        "display_polymers": list(polymers),
        "display_solvents": display_solvents,
        "heatmap_orientation": orientation,
        "record_selections": record_selections,
        "plotted_rows": plotted_rows,
        "solvent_groups": solvent_groups,
        "solvent_group_separators": [
            float(group["end_index"]) + .5
            for group in solvent_groups[:-1]
        ],
        "heatmap_encoding": encoding,
        "highlighted_cells": highlighted_cells,
        "unmatched_highlight_pairs": unmatched_highlights,
    }, None


def _solubility_end_label_positions(
    series: Sequence[dict[str, Any]],
    y_axis_max: float,
) -> dict[str, float]:
    """Separate end labels in data coordinates while preserving series order."""
    ordered = sorted(
        (
            float(item["points"][-1][1]),
            str(item["label"]),
        )
        for item in series
    )
    lower, upper = .025 * y_axis_max, .975 * y_axis_max
    minimum_gap = max(.06 * y_axis_max, .25)
    adjusted: list[list[Any]] = []
    for value, label in ordered:
        position = max(lower, min(upper, value))
        if adjusted:
            position = max(position, float(adjusted[-1][0]) + minimum_gap)
        adjusted.append([position, label])
    if adjusted and float(adjusted[-1][0]) > upper:
        shift = float(adjusted[-1][0]) - upper
        for item in adjusted:
            item[0] = float(item[0]) - shift
    for index in range(len(adjusted) - 2, -1, -1):
        adjusted[index][0] = min(
            float(adjusted[index][0]),
            float(adjusted[index + 1][0]) - minimum_gap,
        )
    if adjusted and float(adjusted[0][0]) < lower:
        shift = lower - float(adjusted[0][0])
        for item in adjusted:
            item[0] = float(item[0]) + shift
    return {str(label): float(position) for position, label in adjusted}


def _solubility_curve_options(
    tool: str,
    *,
    solvent_names: Sequence[str],
    start: float,
    end: float,
    y_axis_max: Any,
    reference_temperature_c: Any,
    reference_temperature_label: Any,
    reference_solubility_wt_pct: Any,
    reference_solubility_label: Any,
    highlight_solvent: Any,
) -> tuple[
    float | None,
    float | None,
    float | None,
    str | None,
    list[dict[str, Any]],
    str | None,
]:
    axis_max = _finite(y_axis_max) if y_axis_max is not None else None
    if y_axis_max is not None and (axis_max is None or axis_max <= 0):
        return (
            None, None, None, None, [],
            tool_error(
                tool,
                "y_axis_max must be a positive finite number.",
                error_code="invalid_y_axis",
            ),
        )
    reference_temperature = (
        _finite(reference_temperature_c)
        if reference_temperature_c is not None else None
    )
    if reference_temperature_c is not None and (
        reference_temperature is None
        or reference_temperature < start
        or reference_temperature > end
    ):
        return (
            axis_max, None, None, None, [],
            tool_error(
                tool,
                "The reference temperature must fall inside the plotted range.",
                error_code="reference_temperature_outside_plot",
                reference_temperature_c=reference_temperature_c,
                plotted_temperature_min_c=start,
                plotted_temperature_max_c=end,
            ),
        )
    reference_solubility = (
        _finite(reference_solubility_wt_pct)
        if reference_solubility_wt_pct is not None else None
    )
    if reference_solubility_wt_pct is not None and (
        reference_solubility is None
        or reference_solubility < 0
        or axis_max is not None and reference_solubility > axis_max
    ):
        return (
            axis_max, reference_temperature, None, None, [],
            tool_error(
                tool,
                "The solubility reference must be finite, nonnegative, and "
                "inside an explicit y-axis maximum.",
                error_code="reference_solubility_outside_plot",
                reference_solubility_wt_pct=reference_solubility_wt_pct,
                y_axis_max=axis_max,
            ),
        )
    highlighted_solvent = None
    if highlight_solvent:
        highlighted, highlight_error = _resolve_solvents(
            tool, [highlight_solvent],
        )
        if highlight_error:
            return (
                axis_max, reference_temperature, reference_solubility,
                None, [], highlight_error,
            )
        highlighted_solvent = highlighted[0]
        if highlighted_solvent not in solvent_names:
            return (
                axis_max, reference_temperature, reference_solubility,
                None, [],
                tool_error(
                    tool,
                    "highlight_solvent must be one of the plotted solvents.",
                    error_code="highlight_solvent_outside_plot",
                    highlight_solvent=highlight_solvent,
                    plotted_solvents=[
                        thermo.canonical_solvent_name(item)
                        for item in solvent_names
                    ],
                ),
            )
    reference_lines = []
    if reference_temperature is not None:
        reference_lines.append({
            "axis": "x", "value": reference_temperature,
            "label": (
                str(reference_temperature_label).strip()
                if reference_temperature_label
                else f"Operating reference ({reference_temperature:g} °C)"
            ),
            "color": "#000000",
            "evidence_role": "user_supplied_operating_reference",
        })
    if reference_solubility is not None:
        reference_lines.append({
            "axis": "y", "value": reference_solubility,
            "label": (
                str(reference_solubility_label).strip()
                if reference_solubility_label
                else f"Target concentration ({reference_solubility:g} wt %)"
            ),
            "color": "#000000",
            "evidence_role": "user_supplied_concentration_reference",
        })
    return (
        axis_max,
        reference_temperature,
        reference_solubility,
        highlighted_solvent,
        reference_lines,
        None,
    )


def _render_solubility_curves(
    plt: Any,
    series: Sequence[dict[str, Any]],
    *,
    start: float,
    end: float,
    axis_max: float | None,
    reference_lines: Sequence[dict[str, Any]],
    extrapolation_regions: Sequence[dict[str, Any]],
    highlighted_solvent: str | None,
    show_markers: bool,
) -> tuple[Any, float, float, dict[str, Any]]:
    """Apply publication presentation without changing any thermodynamic point."""
    fig, axis = plt.subplots(figsize=(11.2, 5.8))
    colors = {
        "below_fit_extrapolation": "#56B4E9",
        "exploratory_extrapolation": "#E69F00",
        "sensitivity_extrapolation": "#D55E00",
    }
    shaded_regions = []
    for regime, color in colors.items():
        matching = [
            row for row in extrapolation_regions if row.get("regime") == regime
        ]
        if not matching:
            continue
        lower_key = (
            "temperature_min_inclusive_c"
            if regime == "below_fit_extrapolation"
            else "temperature_min_exclusive_c"
        )
        upper_key = (
            "temperature_max_exclusive_c"
            if regime == "below_fit_extrapolation"
            else "temperature_max_inclusive_c"
        )
        shaded_regions.append((
            min(float(row[lower_key]) for row in matching),
            max(float(row[upper_key]) for row in matching),
            color,
        ))
    for left, right, color in shaded_regions:
        if right > left:
            axis.axvspan(left, right, color=color, alpha=.10)
    for reference in reference_lines:
        plotting_method = (
            axis.axvline if reference["axis"] == "x" else axis.axhline
        )
        plotting_method(
            reference["value"],
            color=reference.get("color") or "#777777",
            linewidth=1.0 if reference.get("evidence_role") else .8,
            linestyle="--",
        )
    maximum = max(
        max(float(point[1]) for point in item["points"])
        for item in series
    )
    reference_y_values = [
        float(reference["value"])
        for reference in reference_lines
        if reference["axis"] == "y"
    ]
    effective_axis_max = axis_max or _nice_max(
        max([maximum, *reference_y_values], default=maximum)
    )
    highlighted_indices = [
        index for index, item in enumerate(series)
        if item["solvent_data_key"] == highlighted_solvent
    ]
    highlighted_color_indices = {
        series_index: color_index
        for color_index, series_index in enumerate(highlighted_indices)
    }
    for index, item in enumerate(series):
        highlighted = (
            highlighted_solvent is None
            or item["solvent_data_key"] == highlighted_solvent
        )
        if highlighted_solvent is None:
            color = _COLORS[index % len(_COLORS)]
            line_width, line_alpha, zorder = 1.8, 1.0, 2
        elif highlighted:
            color = _COLORS[
                highlighted_color_indices[index] % len(_COLORS)
            ]
            line_width, line_alpha, zorder = 2.6, 1.0, 4
        else:
            color = "#B7B7B7"
            line_width, line_alpha, zorder = 1.3, .78, 1
        xs = [float(point[0]) for point in item["points"]]
        ys = [float(point[1]) for point in item["points"]]
        marker = "o" if show_markers else None
        item.update({
            "color": color,
            "highlighted": bool(highlighted),
            "line_width": line_width,
            "line_alpha": line_alpha,
            "marker": marker,
        })
        axis.plot(
            xs, ys, color=color, linewidth=line_width, alpha=line_alpha,
            marker=marker, markersize=3,
            markevery=max(1, len(xs) // 8), zorder=zorder,
        )
    axis.set(
        xlabel="Temperature (°C)",
        ylabel="Solubility (wt %)",
        ylim=(0, effective_axis_max),
    )
    axis.set_title("Temperature-dependent polymer solubility")
    label_positions = _solubility_end_label_positions(
        series, effective_axis_max,
    )
    from matplotlib.transforms import blended_transform_factory

    x_span = max(end - start, 1.0)
    end_label_transform = blended_transform_factory(
        axis.transAxes, axis.transData,
    )
    axis.set_xlim(start, end)
    fig.subplots_adjust(right=.72)
    for item in series:
        actual_y = float(item["points"][-1][1])
        label_y = label_positions[str(item["label"])]
        item["end_label_y"] = label_y
        axis.plot(
            [1.0, 1.025, 1.035],
            [actual_y, label_y, label_y],
            transform=end_label_transform,
            color=item["color"], linewidth=.9,
            alpha=float(item["line_alpha"]), clip_on=False,
        )
        axis.text(
            1.043, label_y, str(item["label"]),
            transform=end_label_transform,
            color="#000000", ha="left", va="center", clip_on=False,
        )
    for reference in reference_lines:
        if not reference.get("evidence_role"):
            continue
        if reference["axis"] == "x":
            axis.text(
                float(reference["value"]) + .008 * x_span,
                effective_axis_max * .97,
                str(reference["label"]),
                rotation=90, ha="left", va="top", color="#000000",
                bbox={
                    "facecolor": "#FFFFFF", "alpha": .78,
                    "edgecolor": "none", "pad": 1.0,
                },
            )
        else:
            axis.text(
                start + .012 * x_span,
                float(reference["value"]) + .012 * effective_axis_max,
                str(reference["label"]),
                ha="left", va="bottom", color="#000000",
                bbox={
                    "facecolor": "#FFFFFF", "alpha": .78,
                    "edgecolor": "none", "pad": 1.0,
                },
            )
    return fig, maximum, effective_axis_max, {
        "series_label_mode": "direct_end_labels",
        "legend_present": False,
        "highlight_mode": (
            "one_solvent_full_color_with_grey_context"
            if highlighted_solvent else "all_series_full_color"
        ),
        "highlighted_solvent": (
            thermo.canonical_solvent_name(highlighted_solvent)
            if highlighted_solvent else None
        ),
        "context_color": "#B7B7B7" if highlighted_solvent else None,
        "show_markers": show_markers,
        "reference_label_mode": "direct_on_plot",
        "y_axis_label": "Solubility (wt %)",
        "declared_temperature_axis_preserved": True,
    }


def _route_stage_scores(steps: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe the bounded score printed and encoded for each route stage."""
    rows: list[dict[str, Any]] = []
    for index, step in enumerate(steps, 1):
        selectivity = _route_stage_score(_finite(step.get("selectivity_pct")))
        if selectivity is None:
            continue
        rows.append({
            "stage": index,
            "label": "Stage selectivity score",
            "value": selectivity,
            "range": [0.0, 100.0],
            "basis": "modeled target-minus-maximum-off-target solubility percentage points",
        })
    return rows


def _route_stage_score(value: float | None) -> float | None:
    return None if value is None else min(max(value, 0.0), 100.0)


def _route_presentation() -> dict[str, Any]:
    return {
        "palette": list(_COLORS),
        "font_size_pt": _FONT_SIZE,
        "typeface_source": "shared_matplotlib_publication_style",
        "stage_score_label": "Stage selectivity score",
        "stage_score_range": [0.0, 100.0],
        "progress_bar_legend": "Stage selectivity score (0–100)",
    }


from . import visualization_contracts as visual_contracts


def _drop_inapplicable_visualization_options(
    typed_context: dict[str, Any],
    *,
    tool_name: str | None = None,
) -> tuple[str, ...]:
    """Apply the registry's surface map and retain every dropped decision."""
    declared = typed_context.get("declared_deliverable")
    if (
        not isinstance(declared, dict)
        or declared.get("kind") != "visualization"
    ):
        return ()
    view = str(declared.get("visualization_view") or "").strip()
    retained, dropped = visual_contracts.partition_visualization_options(
        declared.get("visualization_options"),
        view=view,
        tool_name=tool_name,
    )
    if not dropped:
        return ()
    declared["visualization_options"] = retained
    annotations = typed_context.setdefault(
        "visualization_option_annotations", [],
    )
    if not isinstance(annotations, list):
        annotations = typed_context["visualization_option_annotations"] = []
    for option in dropped:
        annotation = {
            "code": "DROPPED_INAPPLICABLE_VISUALIZATION_OPTION",
            "option": option,
            "visualization_view": view,
            "tool_name": tool_name,
        }
        if annotation not in annotations:
            annotations.append(annotation)
    return tuple(dropped)


def _declared_visualization_plan_violations(
    calls: Sequence[dict[str, Any]],
    typed_context: dict[str, Any],
) -> tuple[str, ...]:
    """Bind declared presentation options to one specialist tool call."""
    declared = typed_context.get("declared_deliverable")
    if (
        not isinstance(declared, dict)
        or declared.get("kind") != "visualization"
    ):
        return ()
    view = str(declared.get("visualization_view") or "").strip()
    options = list(declared.get("visualization_options") or [])
    if not view and not options:
        return ()
    plotting_calls = [
        item for item in calls
        if str(item.get("name") or "").startswith("plot_")
        and isinstance(item.get("args"), dict)
    ]
    if len(plotting_calls) != 1:
        return (
            "component=declared_deliverable.visualization_view: Return "
            "exactly one plotting call for the declared visualization.",
        )
    call = plotting_calls[0]
    name = str(call.get("name"))
    arguments = call["args"]
    _drop_inapplicable_visualization_options(
        typed_context, tool_name=name,
    )
    options = list(declared.get("visualization_options") or [])
    violations: list[str] = []
    if view:
        view_contract = visual_contracts.visualization_view_contract(view)
        view_value_matches = bool(
            view_contract is not None
            and (
                view_contract.argument is None
                or visual_contracts.canonical_visualization_view(
                    arguments.get(view_contract.argument),
                ) == view
            )
        )
        if (
            view_contract is None
            or name not in view_contract.tools
            or not view_value_matches
        ):
            violations.append(
                "component=declared_deliverable.visualization_view: Preserve "
                f"visualization_view={view!r} in the matching plotting call."
            )
    if options:
        unsupported = [
            option for option in options
            if (
                (contract := (
                    visual_contracts.visualization_option_contract(option)
                )) is None
                or not visual_contracts.visualization_option_is_supported(
                    contract,
                    tool_name=name,
                    view=view,
                )
            )
        ]
        if unsupported:
            violations.append(
                "component=declared_deliverable.visualization_options: "
                f"{name} does not accept declared options {unsupported}."
            )
        missing = [
            option for option in options
            if (
                (contract := (
                    visual_contracts.visualization_option_contract(option)
                )) is not None
                and visual_contracts.visualization_option_is_supported(
                    contract,
                    tool_name=name,
                    view=view,
                )
                and contract.argument is not None
                and contract.argument not in arguments
            )
        ]
        if missing:
            violations.append(
                "component=declared_deliverable.visualization_options: "
                f"Preserve declared option fields {missing} in {name}."
            )
    return tuple(violations)


def _hsp_plot_count_evidence(
    selected_view: str,
    rows: Sequence[dict[str, Any]],
    sidecar_extra: dict[str, Any],
) -> dict[str, Any]:
    """Distinguish source-pair cardinality from bounded radar series."""
    if selected_view == "radar":
        return {
            "evaluated_pair_count": len(rows),
            "radar_selection": dict(sidecar_extra["radar_selection"]),
        }
    return {"n_series": len(rows)}
