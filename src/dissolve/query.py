"""One cell-oriented query over the measured solubility grid.

Every returned row has the same shape.  Omitting an axis selects its complete
stored domain; constraints are applied independently to each measured cell;
and pagination happens only after qualification and deterministic ordering.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from typing import Any, Callable

from . import thermo
from . import thermodynamics as solvent_properties
from .contracts import tool_error, tool_success


_TOOL = "solubility_query"
_ORDER_FIELDS = {
    "solubility": "solubility_pct",
    "boiling_point_margin": "boiling_point_margin_c",
    "temperature": "temperature_c",
}


class _InputError(ValueError):
    """A caller-visible validation failure with structured detail."""

    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    if not rows:
        return ""
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def _finite_optional(value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise _InputError(
            f"invalid_{field}", f"{field} must be a finite number or null.",
            field=field, supplied_value=value,
        )
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise _InputError(
            f"invalid_{field}", f"{field} must be a finite number or null.",
            field=field, supplied_value=value,
        ) from error
    if not math.isfinite(number):
        raise _InputError(
            f"non_finite_{field}", f"{field} must be finite.",
            field=field, supplied_value=value,
        )
    return number


def _page_integer(value: object, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise _InputError(
            f"invalid_{field}", f"{field} must be a {qualifier} integer.",
            field=field, supplied_value=value,
        )
    return value


def _name_axis(
    values: list[str] | None,
    *,
    axis: str,
    universe: Sequence[str],
    resolver: Callable[[str], str | None],
) -> tuple[list[str], bool, int]:
    """Resolve and deduplicate one name axis, or select its whole domain."""
    if values is None:
        return list(universe), True, 0
    if not isinstance(values, list):
        raise _InputError(
            f"invalid_{axis}", f"{axis} must be a list of names or null.",
            field=axis,
        )
    if not values:
        raise _InputError(
            f"empty_{axis}",
            f"{axis} cannot be empty; use null to select every {axis[:-1]}.",
            field=axis,
        )

    resolved: list[str] = []
    unsupported: list[str] = []
    duplicate_count = 0
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            unsupported.append(str(value))
            continue
        canonical = resolver(value)
        if canonical is None:
            unsupported.append(value)
            continue
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        resolved.append(canonical)
    if unsupported:
        raise _InputError(
            f"unknown_{axis}",
            f"Unsupported {axis}: " + ", ".join(repr(item) for item in unsupported),
            field=axis,
            unsupported=unsupported,
            available_count=len(universe),
        )
    return resolved, False, duplicate_count


def _nearest_nodes(temperature: float, nodes: Sequence[float]) -> list[float]:
    below = [node for node in nodes if node <= temperature]
    above = [node for node in nodes if node >= temperature]
    nearest = [
        node for node in (
            below[-1] if below else None,
            above[0] if above else None,
        )
        if node is not None
    ]
    return list(dict.fromkeys(nearest))


def _temperature_axis(
    values: list[float] | None,
    nodes: Sequence[float],
) -> tuple[list[float], bool, int]:
    if values is None:
        return list(nodes), True, 0
    if not isinstance(values, list):
        raise _InputError(
            "invalid_temperatures",
            "temperatures must be a list of stored grid nodes or null.",
            field="temperatures",
        )
    if not values:
        raise _InputError(
            "empty_temperatures",
            "temperatures cannot be empty; use null to select every grid node.",
            field="temperatures",
        )

    parsed: list[float] = []
    invalid: list[object] = []
    for value in values:
        if isinstance(value, bool):
            invalid.append(value)
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            invalid.append(value)
            continue
        if not math.isfinite(number):
            invalid.append(value)
            continue
        parsed.append(number)
    if invalid:
        raise _InputError(
            "invalid_temperatures",
            "Every requested temperature must be a finite number.",
            field="temperatures", invalid_temperatures=invalid,
        )

    node_set = set(nodes)
    off_grid = [temperature for temperature in parsed if temperature not in node_set]
    if off_grid:
        raise _InputError(
            "temperature_off_grid",
            "Requested temperature(s) are not stored grid nodes: "
            + ", ".join(f"{temperature:g} C" for temperature in off_grid) + ".",
            field="temperatures",
            off_grid_temperatures_c=list(dict.fromkeys(off_grid)),
            off_grid_temperature_count=len(set(off_grid)),
            nearest_grid_temperatures_c=[
                {
                    "temperature_c": temperature,
                    "nearest_nodes_c": _nearest_nodes(temperature, nodes),
                }
                for temperature in dict.fromkeys(off_grid)
            ],
            available_grid_temperatures_c=list(nodes),
        )

    deduplicated = list(dict.fromkeys(parsed))
    return deduplicated, False, len(parsed) - len(deduplicated)


def _fetch_grid_rows(
    polymers: Sequence[str],
    solvents: Sequence[str],
    temperatures: Sequence[float],
) -> list[tuple[Any, ...]]:
    filters: list[str] = []
    parameters: list[object] = []
    for column, values in (
        ("polymer", polymers),
        ("solvent", solvents),
        ("temperature_c", temperatures),
    ):
        placeholders = ", ".join("?" for _ in values)
        filters.append(f"{column} IN ({placeholders})")
        parameters.extend(values)
    return thermo.connection().execute(
        "SELECT polymer, solvent, temperature_c, solubility_pct, is_valid, "
        "invalid_reason, source_table FROM solubility_grid WHERE "
        + " AND ".join(filters),
        parameters,
    ).fetchall()


def _sort_rows(rows: list[dict[str, Any]], field: str, descending: bool) -> None:
    """Sort null primary values last and use cell identity as a stable tie-break."""
    def key(row: dict[str, Any]) -> tuple[object, ...]:
        primary = row[field]
        missing = primary is None
        numeric = 0.0 if missing else float(primary)
        directed = -numeric if descending else numeric
        return (
            missing,
            directed,
            row["polymer"],
            row["solvent"],
            float(row["temperature_c"]),
        )

    rows.sort(key=key)


def _display(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "No solubility cells qualified."

    def value(item: object) -> str:
        return "—" if item is None else f"{float(item):.6g}"

    return _table(
        ("Polymer", "Solvent", "T (C)", "Solubility (wt%)", "BP (C)",
         "BP margin (C)", "GHS", "Clipped"),
        tuple(
            (
                row["polymer"], row["solvent_name"], value(row["temperature_c"]),
                value(row["solubility_pct"]), value(row["boiling_point_c"]),
                value(row["boiling_point_margin_c"]), row["ghs_signal_word"],
                "yes" if row["is_clipped"] else "no",
            )
            for row in rows
        ),
    )


def solubility_query(
    polymers: list[str] | None = None,
    solvents: list[str] | None = None,
    temperatures: list[float] | None = None,
    min_solubility_pct: float | None = None,
    max_solubility_pct: float | None = None,
    require_atmospheric: bool = False,
    min_boiling_point_margin_c: float | None = None,
    order_by: str = "solubility",
    descending: bool = True,
    top_k: int = 50,
    offset: int = 0,
) -> str:
    """Query measured solubility cells across any combination of grid axes."""
    try:
        minimum = _finite_optional(min_solubility_pct, "min_solubility_pct")
        maximum = _finite_optional(max_solubility_pct, "max_solubility_pct")
        minimum_margin = _finite_optional(
            min_boiling_point_margin_c, "min_boiling_point_margin_c",
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _InputError(
                "invalid_solubility_range",
                "min_solubility_pct cannot exceed max_solubility_pct.",
                min_solubility_pct=minimum, max_solubility_pct=maximum,
            )
        if not isinstance(require_atmospheric, bool):
            raise _InputError(
                "invalid_require_atmospheric",
                "require_atmospheric must be true or false.",
                supplied_value=require_atmospheric,
            )
        if not isinstance(descending, bool):
            raise _InputError(
                "invalid_descending", "descending must be true or false.",
                supplied_value=descending,
            )
        if not isinstance(order_by, str) or order_by.strip() not in _ORDER_FIELDS:
            raise _InputError(
                "invalid_order_by",
                "order_by must be solubility, boiling_point_margin, or temperature.",
                supplied_value=order_by, available_orderings=sorted(_ORDER_FIELDS),
            )
        ordering = order_by.strip()
        limit = _page_integer(top_k, "top_k", minimum=1)
        start = _page_integer(offset, "offset", minimum=0)

        connection = thermo.connection()
        polymer_universe = list(thermo.available_polymers())
        solvent_universe = [
            str(row[0]) for row in connection.execute(
                "SELECT DISTINCT solvent FROM solubility_grid ORDER BY solvent"
            ).fetchall()
        ]
        temperature_universe = [
            float(row[0]) for row in connection.execute(
                "SELECT DISTINCT temperature_c FROM solubility_grid ORDER BY temperature_c"
            ).fetchall()
        ]
        selected_polymers, all_polymers, duplicate_polymers = _name_axis(
            polymers,
            axis="polymers",
            universe=polymer_universe,
            resolver=thermo.resolve_polymer,
        )
        selected_solvents, all_solvents, duplicate_solvents = _name_axis(
            solvents,
            axis="solvents",
            universe=solvent_universe,
            resolver=thermo.resolve_solvent,
        )
        selected_temperatures, all_temperatures, duplicate_temperatures = (
            _temperature_axis(temperatures, temperature_universe)
        )
    except _InputError as error:
        return tool_error(
            _TOOL,
            str(error),
            error_code=error.code,
            total=0,
            offset=offset,
            returned=0,
            **error.detail,
        )

    grid_rows = _fetch_grid_rows(
        selected_polymers, selected_solvents, selected_temperatures,
    )
    selected_cell_count = (
        len(selected_polymers) * len(selected_solvents) * len(selected_temperatures)
    )
    stored_cell_count = len(grid_rows)
    missing_cell_count = selected_cell_count - stored_cell_count
    assert missing_cell_count >= 0

    rejected_measurements: Counter[str] = Counter()
    evaluable_rows: list[dict[str, Any]] = []
    solvent_context: dict[str, tuple[str, float | None, str]] = {}
    for (
        polymer, solvent, temperature_c, solubility_pct, is_valid,
        invalid_reason, source_table,
    ) in grid_rows:
        if not is_valid:
            rejected_measurements[str(invalid_reason or "invalid")] += 1
            continue
        polymer_key = str(polymer)
        solvent_key = str(solvent)
        if solvent_key not in solvent_context:
            boiling_point = solvent_properties.get_boiling_point(solvent_key)
            hazard = solvent_properties.get_solvent_hazard_framing(solvent_key)
            solvent_context[solvent_key] = (
                solvent_properties.canonical_solvent_name(solvent_key),
                None if boiling_point is None else float(boiling_point),
                str(hazard.get("ghs_signal_word") or "unknown"),
            )
        solvent_name, boiling_point, signal_word = solvent_context[solvent_key]
        temperature = float(temperature_c)
        solubility = float(solubility_pct)
        margin = None if boiling_point is None else boiling_point - temperature
        evaluable_rows.append({
            "polymer": polymer_key,
            "solvent": solvent_key,
            "solvent_name": solvent_name,
            "temperature_c": temperature,
            "solubility_pct": solubility,
            "boiling_point_c": boiling_point,
            "boiling_point_margin_c": margin,
            "atmospheric_operation": None if margin is None else margin > 0.0,
            "ghs_signal_word": signal_word,
            "is_clipped": solubility >= 100.0,
            "clip_limit_wt_percent": 100.0,
            "source_table": str(source_table),
        })

    exclusion_counts = {
        "below_min_solubility_pct": 0,
        "above_max_solubility_pct": 0,
        "failed_atmospheric_requirement": 0,
        "unknown_boiling_point_for_atmospheric_requirement": 0,
        "below_min_boiling_point_margin_c": 0,
        "unknown_boiling_point_for_margin_requirement": 0,
        "failed_any_constraint": 0,
    }
    qualified: list[dict[str, Any]] = []
    for row in evaluable_rows:
        failed = False
        if minimum is not None and row["solubility_pct"] < minimum:
            exclusion_counts["below_min_solubility_pct"] += 1
            failed = True
        if maximum is not None and row["solubility_pct"] > maximum:
            exclusion_counts["above_max_solubility_pct"] += 1
            failed = True
        if require_atmospheric and row["atmospheric_operation"] is not True:
            exclusion_counts["failed_atmospheric_requirement"] += 1
            if row["boiling_point_c"] is None:
                exclusion_counts[
                    "unknown_boiling_point_for_atmospheric_requirement"
                ] += 1
            failed = True
        if minimum_margin is not None and (
            row["boiling_point_margin_c"] is None
            or row["boiling_point_margin_c"] < minimum_margin
        ):
            exclusion_counts["below_min_boiling_point_margin_c"] += 1
            if row["boiling_point_margin_c"] is None:
                exclusion_counts[
                    "unknown_boiling_point_for_margin_requirement"
                ] += 1
            failed = True
        if failed:
            exclusion_counts["failed_any_constraint"] += 1
        else:
            qualified.append(row)

    total = len(qualified)
    assert total + exclusion_counts["failed_any_constraint"] == len(evaluable_rows)
    _sort_rows(qualified, _ORDER_FIELDS[ordering], descending)
    page = qualified[start:start + limit]
    returned = len(page)
    assert returned <= limit
    assert total >= returned
    has_more = start + returned < total

    measurement_rejected_count = sum(rejected_measurements.values())
    unavailable_cell_count = missing_cell_count + measurement_rejected_count
    assert selected_cell_count == len(evaluable_rows) + unavailable_cell_count

    return tool_success(
        _TOOL,
        display=_display(page),
        results=page,
        total=total,
        offset=start,
        returned=returned,
        top_k=limit,
        has_more=has_more,
        next_offset=(start + returned if has_more else None),
        order_by=ordering,
        descending=descending,
        constraints={
            "min_solubility_pct": minimum,
            "max_solubility_pct": maximum,
            "require_atmospheric": require_atmospheric,
            "min_boiling_point_margin_c": minimum_margin,
        },
        selection={
            "polymers": None if all_polymers else selected_polymers,
            "solvents": None if all_solvents else selected_solvents,
            "temperatures_c": None if all_temperatures else selected_temperatures,
            "polymer_count": len(selected_polymers),
            "solvent_count": len(selected_solvents),
            "temperature_count": len(selected_temperatures),
            "duplicate_polymers_removed": duplicate_polymers,
            "duplicate_solvents_removed": duplicate_solvents,
            "duplicate_temperatures_removed": duplicate_temperatures,
        },
        selected_cell_count=selected_cell_count,
        stored_cell_count=stored_cell_count,
        evaluable_cell_count=len(evaluable_rows),
        unavailable_cell_count=unavailable_cell_count,
        unavailable_counts={
            "off_grid_temperature": 0,
            "not_measured": missing_cell_count,
            "measurement_rejected": measurement_rejected_count,
            "measurement_rejected_by_reason": dict(sorted(rejected_measurements.items())),
        },
        exclusion_counts=exclusion_counts,
        exclusion_counts_are_independent_predicate_failures=True,
        solubility_unit="wt_pct_solution_concentration",
    )
