"""rank_landscape source=process_rows: usable projection, landscape AND frontier.

safety_standing is a carried axis: copy a legal status from the source row.
Do not filter usable on it. Default not_requested when nothing was bound.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from . import campaign_consume

X_METRIC = "msp_usd_per_kg"
Y_METRIC = "gwp_kg_co2e_per_kg"
X_UNITS = "USD/kg"
Y_UNITS = "kg CO2e/kg"
X_DIRECTION = "min"
Y_DIRECTION = "min"
_SAFETY_STANDING_STATUSES = frozenset({
    "evaluated", "not_requested", "unavailable",
})


def _finite_number(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def hyndman_fan_type7(sample: Sequence[float], p: float) -> float:
    """Quantile at probability p, Hyndman–Fan type 7 (numpy / R default)."""
    ordered = sorted(float(item) for item in sample)
    n = len(ordered)
    if n == 0:
        raise ValueError("quantile of an empty sample")
    if n == 1:
        return ordered[0]
    h = (n - 1) * float(p)
    lo = int(math.floor(h))
    hi = int(math.ceil(h))
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def axis_span(values: Sequence[float]) -> dict[str, float]:
    ordered = [float(item) for item in values]
    return {
        "min": min(ordered),
        "p05": hyndman_fan_type7(ordered, 0.05),
        "p95": hyndman_fan_type7(ordered, 0.95),
        "max": max(ordered),
    }


def _dominates(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> bool:
    cx, cy = float(candidate[X_METRIC]), float(candidate[Y_METRIC])
    bx, by = float(baseline[X_METRIC]), float(baseline[Y_METRIC])
    return (cx <= bx and cy <= by) and (cx < bx or cy < by)


def pareto_frontier(landscape: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    frontier = [
        point for point in landscape
        if not any(
            other is not point and _dominates(other, point)
            for other in landscape
        )
    ]
    frontier.sort(key=lambda point: float(point[X_METRIC]))
    out: list[dict[str, Any]] = []
    for index, point in enumerate(frontier, 1):
        copied = dict(point)
        copied.update({
            "point_id": index,
            "point_status": "frontier",
            "is_frontier": True,
        })
        out.append(copied)
    return out


def _lca_standing(comparison: Mapping[str, Any]) -> dict[str, Any] | None:
    coverage = comparison.get("lca_coverage")
    if isinstance(coverage, dict) and coverage:
        if any(
            key in coverage
            for key in ("status", "lca_metrics_status", "metric_coverage_status")
        ):
            standing = {
                "status": coverage.get("status"),
                "lca_metrics_status": coverage.get("lca_metrics_status"),
                "metric_coverage_status": coverage.get("metric_coverage_status"),
            }
            metric_status = coverage.get("lca_metric_status")
            if isinstance(metric_status, dict) and metric_status:
                standing["lca_metric_status"] = dict(metric_status)
            return standing
        return dict(coverage)
    metric_status = comparison.get("lca_metric_status")
    if isinstance(metric_status, dict) and metric_status:
        return {"lca_metric_status": dict(metric_status)}
    return None


def _public_twelve_from_process_row(
    row: Mapping[str, Any], comparison: Mapping[str, Any],
) -> dict[str, Any]:
    """Executed D-8 twelve on a compact landscape point. Public solvent."""
    from . import tea

    normalized = dict(row.get("config_normalized_twelve") or {})
    public: dict[str, Any] = {}
    for internal, public_name in tea._DESIGN_POINT_PUBLIC_FIELDS:
        if public_name in comparison and comparison.get(public_name) is not None:
            value = comparison[public_name]
        elif internal in normalized:
            value = normalized[internal]
        elif public_name in normalized:
            value = normalized[public_name]
        else:
            value = None
        if public_name == "target_polymer":
            value = (
                row.get("polymer") or comparison.get("polymer") or value
            )
        if public_name == "solvent":
            value = (
                row.get("solvent_public_identity")
                or comparison.get("solvent")
                or value
            )
        if value is not None and value != "":
            public[public_name] = value
    if public.get("target_polymer"):
        public["polymer"] = public["target_polymer"]
    return public


def _carried_safety_standing(row: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a legal safety_standing object. Not a GSK-fail predicate."""
    nested = row.get("comparison_row")
    sources = (row.get("safety_standing"),)
    if isinstance(nested, dict):
        sources = (row.get("safety_standing"), nested.get("safety_standing"))
    for source in sources:
        if not isinstance(source, dict):
            continue
        status = str(source.get("status") or "").strip()
        if status in _SAFETY_STANDING_STATUSES:
            return dict(source)
    return {"status": "not_requested"}


def compact_process_row(row: Mapping[str, Any]) -> dict[str, Any]:
    comparison = dict(row.get("comparison_row") or {})
    standing = dict(row.get("standing") or {})
    payload = {
        "pair_id": row.get("pair_id"),
        "target_polymer": row.get("polymer"),
        "solvent": row.get("solvent_public_identity"),
        "campaign_fingerprint": row.get("campaign_fingerprint"),
        "outcome": row.get("outcome"),
        "error_type": row.get("error_type"),
        X_METRIC: comparison.get(X_METRIC),
        Y_METRIC: comparison.get(Y_METRIC),
        "standing": standing,
        "process_parameter_status": standing.get("process_parameter_status"),
        "can_cite_as_validated_process": standing.get(
            "can_cite_as_validated_process",
        ),
        "lca_coverage": _lca_standing(comparison),
        "safety_standing": _carried_safety_standing(row),
    }
    payload.update(_public_twelve_from_process_row(row, comparison))
    return payload


def row_is_usable(
    point: Mapping[str, Any],
    *,
    canonical: str | None,
    skip_campaign_identity: bool = False,
) -> bool:
    if not skip_campaign_identity:
        fingerprint = str(point.get("campaign_fingerprint") or "").strip().casefold()
        if fingerprint != str(canonical or "").casefold():
            return False
    if str(point.get("outcome") or "") != "success":
        return False
    if not _finite_number(point.get(X_METRIC)):
        return False
    if not _finite_number(point.get(Y_METRIC)):
        return False
    coverage = point.get("lca_coverage")
    if not isinstance(coverage, dict) or not coverage:
        return False
    if skip_campaign_identity:
        from . import tea

        return tea._public_twelve_from_row(dict(point)) is not None
    standing = point.get("standing")
    if not isinstance(standing, dict) or not standing:
        return False
    if not standing.get("process_parameter_status"):
        return False
    return True


def _exclusion_token(point: Mapping[str, Any]) -> str:
    token = str(point.get("error_type") or "").strip()
    if token:
        return token
    if str(point.get("outcome") or "") != "success":
        return "failure"
    return "not_usable"


def frontier_tradeoff(
    frontier: Sequence[Mapping[str, Any]],
    *,
    cheapest_equals_lowest_y: bool,
) -> dict[str, Any] | None:
    if len(frontier) < 2 or cheapest_equals_lowest_y:
        return None
    cheapest = min(frontier, key=lambda point: float(point[X_METRIC]))
    best_y = min(frontier, key=lambda point: float(point[Y_METRIC]))
    x_at_cheapest = float(cheapest[X_METRIC])
    y_at_cheapest = float(cheapest[Y_METRIC])
    x_at_best_y = float(best_y[X_METRIC])
    y_at_best_y = float(best_y[Y_METRIC])
    delta_y = y_at_best_y - y_at_cheapest
    payload = {
        "x_metric": X_METRIC,
        "y_metric": Y_METRIC,
        "x_direction": X_DIRECTION,
        "y_direction": Y_DIRECTION,
        "x_units": X_UNITS,
        "y_units": Y_UNITS,
        "x_at_cheapest": x_at_cheapest,
        "y_at_cheapest": y_at_cheapest,
        "x_at_best_y": x_at_best_y,
        "y_at_best_y": y_at_best_y,
        "delta_x": x_at_best_y - x_at_cheapest,
        "delta_y": delta_y,
        "delta_y_percent": (
            100.0 * delta_y / y_at_cheapest if y_at_cheapest else None
        ),
    }
    if x_at_cheapest > 0:
        payload["x_ratio"] = x_at_best_y / x_at_cheapest
    return payload


def quality_block(
    landscape: Sequence[dict[str, Any]],
    *,
    grouping: dict[str, Any],
) -> dict[str, Any]:
    frontier = pareto_frontier(landscape)
    cheapest = min(frontier, key=lambda point: float(point[X_METRIC]))
    lowest_y = min(frontier, key=lambda point: float(point[Y_METRIC]))
    cheapest_equals_lowest_y = cheapest is lowest_y or (
        float(cheapest[X_METRIC]) == float(lowest_y[X_METRIC])
        and float(cheapest[Y_METRIC]) == float(lowest_y[Y_METRIC])
    )
    n_landscape = len(landscape)
    n_frontier = len(frontier)
    sparse = n_frontier == 1 or cheapest_equals_lowest_y
    if n_frontier == 1:
        knee_status = "endpoint_only_no_interior_knee"
    elif cheapest_equals_lowest_y:
        knee_status = "endpoint_only_no_interior_knee"
    elif n_frontier > 2:
        knee_status = "interior_tradeoff"
    else:
        knee_status = "endpoint_only_no_interior_knee"
    marked = []
    frontier_pairs = {point.get("pair_id") for point in frontier}
    for point in landscape:
        copied = dict(point)
        copied["is_frontier"] = copied.get("pair_id") in frontier_pairs
        marked.append(copied)
    return {
        "landscape_points": marked,
        "frontier_points": frontier,
        "n_landscape_points": n_landscape,
        "n_frontier_points": n_frontier,
        "frontier_fraction": (
            n_frontier / n_landscape if n_landscape else None
        ),
        "axis_spans": {
            X_METRIC: axis_span([float(p[X_METRIC]) for p in landscape]),
            Y_METRIC: axis_span([float(p[Y_METRIC]) for p in landscape]),
        },
        "cheapest_point": dict(cheapest),
        "frontier_tradeoff": frontier_tradeoff(
            frontier, cheapest_equals_lowest_y=cheapest_equals_lowest_y,
        ),
        "knee_status": knee_status,
        "cheapest_equals_lowest_y": cheapest_equals_lowest_y,
        "sparse_frontier": sparse,
        "grouping": grouping,
        "metric_units": {X_METRIC: X_UNITS, Y_METRIC: Y_UNITS},
        "x_metric": X_METRIC,
        "y_metric": Y_METRIC,
    }


def project_usable(
    rows: Iterable[Mapping[str, Any]],
    *,
    canonical: str | None,
    skip_campaign_identity: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    usable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    by_type: Counter[str] = Counter()
    for row in rows:
        point = compact_process_row(row)
        if row_is_usable(
            point,
            canonical=canonical,
            skip_campaign_identity=skip_campaign_identity,
        ):
            usable.append(point)
            continue
        token = _exclusion_token(point)
        by_type[token] += 1
        excluded.append({
            "pair_id": point.get("pair_id"),
            "target_polymer": point.get("target_polymer"),
            "solvent": point.get("solvent"),
            "error_type": token,
            "outcome": point.get("outcome"),
        })
    return usable, excluded, dict(by_type)


def load_filtered_rows(
    path: Path,
    canonical: str,
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> list[dict[str, Any]]:
    wanted_polymers = {
        campaign_consume._key(item) for item in (polymers or []) if item
    }
    wanted_solvent = str(solvent or "").strip() or None
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            row_fp = str(row.get("campaign_fingerprint") or "").strip().casefold()
            if row_fp != canonical:
                raise campaign_consume.CampaignConsumeError(
                    "process_rows.jsonl campaign_fingerprint disagrees",
                    error_code="campaign_fingerprint_mismatch",
                    supplied=canonical,
                    computed=row_fp or None,
                    pair_id=row.get("pair_id"),
                )
            polymer = str(row.get("polymer") or "").strip()
            if wanted_polymers and campaign_consume._key(polymer) not in wanted_polymers:
                continue
            public_solvent = str(row.get("solvent_public_identity") or "")
            if wanted_solvent is not None and not campaign_consume._solvents_match(
                wanted_solvent, public_solvent,
            ):
                continue
            rows.append(row)
    return rows


def _filter_process_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
) -> list[Mapping[str, Any]]:
    wanted_polymers = {
        campaign_consume._key(item) for item in (polymers or []) if item
    }
    wanted_solvent = str(solvent or "").strip() or None
    filtered: list[Mapping[str, Any]] = []
    for row in rows:
        polymer = str(
            row.get("polymer") or row.get("target_polymer") or "",
        ).strip()
        if wanted_polymers and campaign_consume._key(polymer) not in wanted_polymers:
            continue
        public_solvent = str(
            row.get("solvent_public_identity") or row.get("solvent") or "",
        )
        if wanted_solvent is not None and not campaign_consume._solvents_match(
            wanted_solvent, public_solvent,
        ):
            continue
        filtered.append(row)
    return filtered


def _pair_id_for_economics_row(row: Mapping[str, Any], index: int) -> str:
    for key in ("pair_id", "label", "record_id"):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    polymer = str(row.get("target_polymer") or row.get("polymer") or "").strip()
    solvent = str(row.get("solvent") or "").strip()
    case = str(row.get("energy_case") or "").strip()
    token = "|".join(part for part in (polymer, solvent, case) if part)
    return token or f"handle-row-{index + 1}"


def _outcome_for_economics_row(row: Mapping[str, Any]) -> str:
    if row.get("success") is True:
        return "success"
    if row.get("success") is False:
        return "failure"
    outcome = str(row.get("outcome") or "").strip()
    if outcome:
        return outcome
    if _finite_number(row.get(X_METRIC)):
        return "success"
    return "failure"


def economics_row_as_process_row(
    row: Mapping[str, Any],
    *,
    index: int = 0,
) -> dict[str, Any]:
    """JSONL-shaped process row from an evaluate or lookup comparison row."""
    from . import tea

    if isinstance(row.get("comparison_row"), dict) and (
        row.get("polymer") is not None
        or row.get("solvent_public_identity") is not None
    ):
        copied = dict(row)
        if not copied.get("pair_id"):
            copied["pair_id"] = _pair_id_for_economics_row(row, index)
        return copied
    public = tea._public_twelve_from_row(dict(row)) or {}
    worker = {
        internal: public[public_name]
        for internal, public_name in tea._DESIGN_POINT_PUBLIC_FIELDS
        if public_name in public
    }
    standing = dict(row.get("standing") or {})
    process_status = row.get("process_parameter_status")
    if isinstance(process_status, dict) and process_status:
        standing.setdefault("process_parameter_status", process_status)
    if "can_cite_as_validated_process" in row:
        standing.setdefault(
            "can_cite_as_validated_process",
            row["can_cite_as_validated_process"],
        )
    comparison = {
        X_METRIC: row.get(X_METRIC),
        Y_METRIC: row.get(Y_METRIC),
        **public,
    }
    coverage = row.get("lca_coverage")
    if isinstance(coverage, dict) and coverage:
        comparison["lca_coverage"] = coverage
    metric_status = row.get("lca_metric_status")
    if isinstance(metric_status, dict) and metric_status:
        comparison["lca_metric_status"] = dict(metric_status)
    payload = {
        "pair_id": _pair_id_for_economics_row(row, index),
        "polymer": public.get("target_polymer") or row.get("polymer"),
        "solvent_public_identity": public.get("solvent") or row.get("solvent"),
        "campaign_fingerprint": row.get("campaign_fingerprint"),
        "outcome": _outcome_for_economics_row(row),
        "error_type": row.get("error_type"),
        "standing": standing,
        "comparison_row": comparison,
        "config_normalized_twelve": worker,
    }
    safety = row.get("safety_standing")
    if isinstance(safety, dict):
        payload["safety_standing"] = dict(safety)
    if row.get("engine_mode"):
        payload["engine_mode"] = row["engine_mode"]
    return payload


def _rank_usable_population(
    usable: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
    by_type: dict[str, int],
    *,
    n_rows_read: int,
    polymer_grouping: str,
    operation: str,
    extra_census: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    census: dict[str, Any] = {
        "n_rows_read": n_rows_read,
        "excluded_count": len(excluded),
        "excluded_by_error_type": by_type,
        "excluded_records": excluded,
        "n_usable": len(usable),
        "ingested_into_admitted_cache": False,
        "safety_standing_policy": "carried_not_filtered",
        "view_family": "two_of_five",
        **dict(extra_census or {}),
    }
    if len(usable) < 2:
        raise campaign_consume.CampaignConsumeError(
            "fewer than two usable rows after the projection",
            error_code="landscape_too_small",
            **census,
        )
    grouping_token = str(polymer_grouping or "per_target_polymer").strip()
    polymers_present = list(dict.fromkeys(
        str(point.get("target_polymer") or "") for point in usable
    ))
    if operation == "sort":
        ranked = sorted(usable, key=lambda point: float(point[X_METRIC]))
        return {
            **census,
            "operation": "sort",
            "landscape_points": ranked,
            "n_landscape_points": len(ranked),
            "n_returned": len(ranked),
            "grouping": {
                "polymer_grouping": grouping_token,
                "target_polymers": polymers_present,
            },
            "metric_units": {X_METRIC: X_UNITS},
            "x_metric": X_METRIC,
        }
    if grouping_token == "per_target_polymer" and len(polymers_present) > 1:
        grouped = []
        for polymer in polymers_present:
            subset = [
                point for point in usable
                if point.get("target_polymer") == polymer
            ]
            if len(subset) < 2:
                continue
            grouped.append({
                "target_polymer": polymer,
                **quality_block(
                    subset,
                    grouping={
                        "polymer_grouping": "per_target_polymer",
                        "target_polymer": polymer,
                    },
                ),
            })
        if len(grouped) < 1:
            raise campaign_consume.CampaignConsumeError(
                "fewer than two usable rows after the projection",
                error_code="landscape_too_small",
                **census,
            )
        return {
            **census,
            "operation": "pareto_dominance",
            "polymer_grouping": "per_target_polymer",
            "grouped_fronts": grouped,
            "n_groups": len(grouped),
        }
    grouping = {
        "polymer_grouping": grouping_token,
        "target_polymers": polymers_present,
    }
    block = quality_block(usable, grouping=grouping)
    n_land, n_front = block["n_landscape_points"], block["n_frontier_points"]
    if n_land != len(block["landscape_points"]) or n_front != len(
        block["frontier_points"]
    ):
        raise RuntimeError("landscape counts disagree with arrays")
    return {
        **census,
        "operation": "pareto_dominance",
        **block,
    }


def rank_process_rows(
    bound: Mapping[str, Any],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    polymer_grouping: str = "per_target_polymer",
    operation: str = "pareto_dominance",
) -> dict[str, Any]:
    canonical = str(bound["canonical"])
    rows = load_filtered_rows(
        bound["process_rows_path"],
        canonical,
        polymers=polymers,
        solvent=solvent,
    )
    usable, excluded, by_type = project_usable(rows, canonical=canonical)
    return _rank_usable_population(
        usable,
        excluded,
        by_type,
        n_rows_read=len(rows),
        polymer_grouping=polymer_grouping,
        operation=operation,
        extra_census={
            "campaign_fingerprint": canonical,
            "append_log_fingerprint": bound.get("append_log_fingerprint"),
            **dict(bound.get("projected") or {}),
        },
    )


def rank_handle_process_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    polymers: list[str] | None = None,
    solvent: str | None = None,
    polymer_grouping: str = "per_target_polymer",
    operation: str = "pareto_dominance",
    skip_campaign_identity: bool = True,
    canonical: str | None = None,
    extra_census: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Rank tool-1 rows already in a handle. No JSONL, no BioSTEAM."""
    converted = [
        economics_row_as_process_row(row, index=index)
        for index, row in enumerate(rows)
    ]
    fingerprints = {
        str(row.get("campaign_fingerprint") or "").strip().casefold()
        for row in converted
        if str(row.get("campaign_fingerprint") or "").strip()
    }
    if len(fingerprints) > 1:
        raise campaign_consume.CampaignConsumeError(
            "handle mixes more than one campaign fingerprint",
            error_code="mixed_campaign_basis",
            fingerprints=sorted(fingerprints),
            n_rows_read=len(converted),
            ingested_into_admitted_cache=False,
        )
    filtered = _filter_process_rows(
        converted, polymers=polymers, solvent=solvent,
    )
    usable, excluded, by_type = project_usable(
        filtered,
        canonical=canonical,
        skip_campaign_identity=skip_campaign_identity,
    )
    extra: dict[str, Any] = dict(extra_census or {})
    if "campaign_fingerprint" not in extra and len(fingerprints) == 1:
        extra["campaign_fingerprint"] = next(iter(fingerprints))
    return _rank_usable_population(
        usable,
        excluded,
        by_type,
        n_rows_read=len(filtered),
        polymer_grouping=polymer_grouping,
        operation=operation,
        extra_census=extra,
    )
