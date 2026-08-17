"""Model-selectable deterministic thermodynamic tools."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal, Optional, Sequence

from langchain_core.tools import InjectedToolArg

from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success

_STRONG_OVERLAP_RATIO = 0.70
MATERIAL_UNDER_COVERAGE_RATIO = 0.90

# One product-level registry for scientific methods the current tool surface
# actually implements. Consumers may render it, but must not expand it in prose.
MODELING_CAPABILITIES = (
    {"capability": "temperature-dependent grid and fitted solubility", "tools": (
        "predict_solubility", "predict_solubility_range",
        "rank_polymers_by_solubility", "rank_solvents_by_solubility",
    )},
    {"capability": "selective-dissolution and multistage route screening", "tools": (
        "screen_polymer_separation", "plan_multistage_separation",
        "screen_pairwise_solubility_overlap",
    )},
    {"capability": "cooling and precipitation-order proxy screening", "tools": (
        "screen_precipitation_order",
    )},
    {"capability": "Hansen RED and binary Random Forest fallback", "tools": (
        "resolve_polymer_data_scope", "lookup_material_database_membership",
        "screen_hansen_compatibility",
    )},
)
UNAVAILABLE_MODELING_METHODS = (
    {"method": "PC-SAFT", "status": "planned_not_available", "aliases": ("PC-SAFT", "PC SAFT")},
    {"method": "Flory-Huggins interaction parameters", "status": "not_available", "aliases": ("Flory-Huggins", "Flory Huggins")},
    {"method": "binodal or spinodal phase diagrams", "status": "not_available", "aliases": ("binodal", "spinodal")},
    {"method": "solvent-mixture thermodynamics", "status": "not_available", "aliases": ("solvent mixture", "solvent-mixture")},
    {"method": "fractional blend-dissolution simulation", "status": "not_available", "aliases": ("fractional dissolution", "blend dissolution")},
    {"method": "COSMO or molecular-dynamics simulation", "status": "not_available", "aliases": ("COSMO", "molecular dynamics", "molecular-dynamics")},
)


def _table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    """Render a compact deterministic table artifact, never conversational prose."""
    if not rows:
        return ""
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(str(value) for value in row) + " |" for row in rows),
    ])


def _temperature_error(tool: str, temperature_c: float) -> Optional[str]:
    try:
        temperature = float(temperature_c)
    except (TypeError, ValueError):
        return tool_error(tool, "Temperature must be numeric.", error_code="invalid_temperature")
    if not math.isfinite(temperature):
        return tool_error(tool, "Temperature must be finite.", error_code="non_finite_temperature")
    if temperature <= -273.15:
        return tool_error(
            tool,
            "Temperature must be above absolute zero.",
            error_code="temperature_below_absolute_zero",
            temperature_c=temperature,
        )
    if temperature > thermo.SENSITIVITY_EXTRAPOLATION_MAX_C:
        return tool_error(
            tool,
            f"Temperature {temperature:g} C is above the supported 200 C sensitivity limit.",
            error_code="temperature_above_supported_extrapolation",
            temperature_c=temperature,
            max_temperature_c=thermo.SENSITIVITY_EXTRAPOLATION_MAX_C,
        )
    return None


def _solvent_resolution_detail(solvent_name: str) -> dict[str, Any]:
    """Describe one unresolved model identity without collapsing its cause."""
    identity = thermo.identify_known_solvent(solvent_name)
    model_status = thermo.get_fitted_solvent_status(solvent_name)
    if identity is None:
        identity_status = "not_found"
    elif model_status == "excluded_data_quality":
        identity_status = "known_with_excluded_fitted_model"
    else:
        identity_status = "known_without_fitted_model"
    return {
        "solvent_name": solvent_name,
        "solvent_identity_status": identity_status,
        "fitted_model_status": model_status,
        "known_solvent_identity": identity,
    }


def _solvent_resolution_error(tool: str, solvent_name: str) -> str:
    """Keep chemical identity separate from fitted-solubility availability."""
    detail = _solvent_resolution_detail(solvent_name)
    if detail["solvent_identity_status"] == "not_found":
        message = f"Unknown solvent: {solvent_name!r}."
    elif detail["fitted_model_status"] == "excluded_data_quality":
        message = (
            f"Known solvent with a data-quality-excluded fitted model: "
            f"{solvent_name!r}."
        )
    else:
        message = (
            f"Known solvent without an available fitted solubility model: "
            f"{solvent_name!r}."
        )
    return tool_error(
        tool,
        message,
        error_code="unknown_solvent",
        **detail,
        available_solvents=sorted(thermo.get_available_solvents()),
    )


def _solvent_resolution_errors(tool: str, solvent_names: Sequence[str]) -> str:
    """Return per-name identity evidence for a plural unresolved request."""
    unsupported = list(dict.fromkeys(str(item) for item in solvent_names))
    details = [_solvent_resolution_detail(item) for item in unsupported]
    single = details[0] if len(details) == 1 else {}
    return tool_error(
        tool,
        "Unsupported solvent(s): " + ", ".join(unsupported),
        error_code="unknown_solvent",
        unsupported_solvents=unsupported,
        unsupported_solvent_details=details,
        **single,
        available_solvents=sorted(thermo.get_available_solvents()),
    )


def _resolve_pair(tool: str, polymer_name: str, solvent_name: str) -> tuple[str, str] | str:
    polymer = thermo.resolve_polymer(polymer_name)
    if polymer is None:
        return tool_error(
            tool,
            f"Unknown polymer: {polymer_name!r}.",
            error_code="unknown_polymer",
            polymer_name=polymer_name,
            available_polymers=sorted(thermo.get_available_polymers()),
        )
    solvent = thermo.resolve_solvent(solvent_name)
    if solvent is None:
        return _solvent_resolution_error(tool, solvent_name)
    if thermo.is_solubility_pair_excluded(polymer, solvent):
        reason = thermo.get_solubility_pair_exclusion_reason(polymer, solvent)
        return tool_error(
            tool,
            f"Excluded data-quality pair: {polymer} in {solvent}.",
            error_code="excluded_data_quality_pair",
            polymer_name=polymer,
            solvent_name=solvent,
            reason=reason,
        )
    if not thermo.has_solubility_pair(polymer, solvent):
        return tool_error(
            tool,
            f"No fitted or grid data for {polymer} in {solvent}.",
            error_code="pair_not_found",
            polymer_name=polymer,
            solvent_name=solvent,
        )
    return polymer, solvent


def _pair_result(
    polymer: str,
    solvent: str,
    temperature_c: float,
    method: str = thermo.AUTO,
) -> Optional[dict]:
    evidence = thermo.get_solubility_result(
        polymer, solvent, temperature_c, method,
    )
    value = evidence.get("solubility_pct")
    if not evidence.get("available") or value is None:
        return None
    # v12: every served value comes from solubility_grid. The old label was
    # read from solubility_coefficients, so a grid value reported "fitted".
    category = "grid_only"
    return {
        "polymer": polymer,
        "solvent": thermo.canonical_solvent_name(solvent),
        "solvent_data_key": solvent,
        "temperature_c": float(temperature_c),
        "solubility_pct": float(value),
        "category": category,
        "method": evidence["method"],
        "grid_point_status": evidence.get("grid_point_status"),
        "source_temperatures_c": evidence.get("source_temperatures_c"),
        "source_grid_temperature_range_c": evidence.get(
            "source_grid_temperature_range_c",
        ),
        "extrapolation": evidence.get("extrapolation") or "none",
        "temperature_use_regime": thermo.temperature_use_regime(
            temperature_c, evidence["method"],
        ),
        "is_clipped": bool(float(value) >= 100.0),
        "clip_limit_wt_percent": 100.0,
        **thermo.get_solvent_hazard_framing(solvent),
    }


def _single_polymer_catalog_provenance(
    screened_count: int,
) -> dict[str, object]:
    """Qualify catalog size with the count evaluated for one polymer."""
    provenance = dict(thermo.get_solvent_catalog_provenance())
    count = int(screened_count)
    assert 0 <= count <= int(provenance["fitted_solvent_count"])
    provenance["fitted_solvents_for_polymer"] = count
    return provenance


def _screened_solvent_counts(
    polymers: Sequence[str], temperature_c: float,
) -> dict[str, int]:
    """Count catalog solvents accepted by the actual pair-screen kernel."""
    solvents = sorted(thermo.get_available_solvents())
    return {
        polymer: sum(
            _pair_result(polymer, solvent, temperature_c) is not None
            for solvent in solvents
        )
        for polymer in polymers
    }


def _screen_catalog_provenance(
    polymers: Sequence[str], temperature_c: float,
) -> dict[str, object]:
    """Disclose only material per-polymer gaps on a multi-polymer screen."""
    counts = _screened_solvent_counts(polymers, temperature_c)
    if len(counts) == 1:
        return _single_polymer_catalog_provenance(next(iter(counts.values())))
    provenance = dict(thermo.get_solvent_catalog_provenance())
    catalog_count = int(provenance["fitted_solvent_count"])
    under_covered = {
        polymer: count for polymer, count in counts.items()
        if count < MATERIAL_UNDER_COVERAGE_RATIO * catalog_count
    }
    if under_covered:
        provenance["under_covered_polymers"] = under_covered
    return provenance


def predict_solubility(polymer_name: str, solvent_name: str, temperature_c: float) -> str:
    """Predict one polymer/solvent solubility at exactly the requested temperature.

    Choose this for one pair at one exact temperature.
    """
    tool = "predict_solubility"
    if error := _temperature_error(tool, temperature_c):
        return error
    resolved = _resolve_pair(tool, polymer_name, solvent_name)
    if isinstance(resolved, str):
        return resolved
    polymer, solvent = resolved
    row = _pair_result(
        polymer, solvent, float(temperature_c), thermo.AUTO,
    )
    if row is None:
        evidence = thermo.get_solubility_result(
            polymer, solvent, float(temperature_c),
        )
        unavailable_reason = str(
            evidence.get("unavailable_reason") or "solubility_value_unavailable"
        )
        return tool_error(
            tool,
            f"No solubility value for {polymer} in {solvent} at {temperature_c:g} C.",
            error_code=(
                "solubility_grid_all_points_filtered"
                if unavailable_reason == "all_grid_points_filtered" else
                "apelblat_fit_unavailable_above_grid"
                if unavailable_reason == "apelblat_fit_unavailable_above_grid" else
                "solubility_grid_value_unavailable"
            ),
            polymer_name=polymer,
            solvent_name=thermo.canonical_solvent_name(solvent),
            temperature_c=float(temperature_c),
            method=evidence.get("method"),
            unavailable_reason=unavailable_reason,
            grid_valid_point_count=evidence.get("grid_valid_point_count"),
            grid_filtered_point_count=evidence.get("grid_filtered_point_count"),
            grid_filtered_reasons=evidence.get("grid_filtered_reasons"),
        )
    boiling_point = thermo.get_boiling_point(solvent)
    atmospheric = None if boiling_point is None else float(temperature_c) < boiling_point
    return tool_success(
        tool,
        display=_table(
            ("Polymer", "Solvent", "T (C)", "Solubility (wt%)"),
            ((polymer, row["solvent"], f"{float(temperature_c):g}", f"{row['solubility_pct']:.6g}"),),
        ),
        polymer_name=polymer,
        solvent_name=row["solvent"],
        solvent_data_key=solvent,
        temperature_c=float(temperature_c),
        category=row["category"],
        method=row["method"],
        grid_point_status=row.get("grid_point_status"),
        source_temperatures_c=row.get("source_temperatures_c"),
        solubility_pct=row["solubility_pct"],
        solubility_unit="wt_pct_solution_concentration",
        screening_reference_wt_pct=5.0,
        below_screening_reference=row["solubility_pct"] < 5.0,
        is_clipped=row["is_clipped"],
        clip_limit_wt_percent=100.0,
        boiling_point_c=boiling_point,
        atmospheric_operation=atmospheric,
        extrapolation=row.get("extrapolation") or "none",
        temperature_extrapolation=thermo.temperature_extrapolation_status(
            float(temperature_c), row.get("fitted_temperature_range_c"),
        ),
        temperature_use_regime=row["temperature_use_regime"],
        **thermo.get_solvent_hazard_framing(solvent),
        solvent_catalog_provenance=thermo.get_solvent_catalog_provenance(),
        fitted_temperature_range_c=row.get("fitted_temperature_range_c"),
        source_grid_temperature_range_c=row.get(
            "source_grid_temperature_range_c",
        ),
        recommended_extrapolation_max_c=thermo.RECOMMENDED_EXTRAPOLATION_MAX_C,
        sensitivity_extrapolation_max_c=thermo.SENSITIVITY_EXTRAPOLATION_MAX_C,
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
    )


def predict_solubility_range(
    polymer_name: str,
    solvent_name: str,
    t_start_c: float = 25.0,
    t_end_c: float = 160.0,
    t_step_c: float = 5.0,
) -> str:
    """Return numeric range data for one pair; visual curve requests use the plot specialist.

    Choose this for a named pair over a temperature range.
    """
    tool = "predict_solubility_range"
    if error := _temperature_error(tool, t_start_c):
        return error
    try:
        end, step = float(t_end_c), max(float(t_step_c), 1.0)
    except (TypeError, ValueError):
        return tool_error(tool, "Range bounds and step must be numeric.", error_code="invalid_range")
    if not math.isfinite(end) or not math.isfinite(step):
        return tool_error(tool, "Range bounds and step must be finite.", error_code="non_finite_range")
    requested_end = end
    capped = end > thermo.SENSITIVITY_EXTRAPOLATION_MAX_C
    end = min(end, thermo.SENSITIVITY_EXTRAPOLATION_MAX_C)
    resolved = _resolve_pair(tool, polymer_name, solvent_name)
    if isinstance(resolved, str):
        return resolved
    polymer, solvent = resolved
    # v12: every served value comes from solubility_grid. The old label was
    # read from solubility_coefficients, so a grid value reported "fitted".
    category = "grid_only"
    predictions: list[dict] = []
    unavailable_predictions: list[dict] = []
    fitted_temperature_range_c = None
    source_grid_temperature_range_c = None
    for temperature in thermo._temperature_grid(
        float(t_start_c), end, step,
    )[:200]:
        evidence = thermo.get_solubility_result(polymer, solvent, temperature)
        if evidence.get("fitted_temperature_range_c") is not None:
            fitted_temperature_range_c = evidence[
                "fitted_temperature_range_c"
            ]
        if evidence.get("source_grid_temperature_range_c") is not None:
            source_grid_temperature_range_c = evidence[
                "source_grid_temperature_range_c"
            ]
        if not evidence.get("available"):
            unavailable_predictions.append({
                "temperature_c": temperature,
                "method": evidence.get("method"),
                "unavailable_reason": evidence.get("unavailable_reason"),
            })
            continue
        value = float(evidence["solubility_pct"])
        predictions.append({
            "temperature_c": temperature,
            "solubility_pct": value,
            "method": evidence["method"],
            "grid_point_status": evidence.get("grid_point_status"),
            "source_temperatures_c": evidence.get("source_temperatures_c"),
            "extrapolation": evidence.get("extrapolation") or "none",
            "temperature_use_regime": evidence["temperature_use_regime"],
            "is_clipped": bool(value >= 100.0),
        })
    methods = list(dict.fromkeys(
        str(row["method"]) for row in predictions
    ))
    method = methods[0] if len(methods) == 1 else "mixed"
    if not predictions:
        evidence = thermo.get_solubility_result(
            polymer, solvent, float(t_start_c),
        )
        return tool_error(
            tool,
            f"No retained grid or fitted values for {polymer} in {solvent}.",
            error_code=(
                "solubility_grid_all_points_filtered"
                if evidence.get("unavailable_reason") == "all_grid_points_filtered"
                else "solubility_range_unavailable"
            ),
            polymer_name=polymer,
            solvent_name=thermo.canonical_solvent_name(solvent),
            method=evidence.get("method"),
            unavailable_reason=evidence.get("unavailable_reason"),
            grid_filtered_point_count=evidence.get("grid_filtered_point_count"),
            grid_filtered_reasons=evidence.get("grid_filtered_reasons"),
        )
    boiling_point = thermo.get_boiling_point(solvent)
    extrapolated = sum(row.get("extrapolation") not in {None, "", "none"} for row in predictions)
    return tool_success(
        tool,
        display=_table(
            ("T (C)", "Solubility (wt%)"),
            tuple((f"{row['temperature_c']:g}", f"{row['solubility_pct']:.6g}") for row in predictions),
        ),
        polymer_name=polymer,
        solvent_name=thermo.canonical_solvent_name(solvent),
        solvent_data_key=solvent,
        category=category,
        method=method,
        t_start_c=float(t_start_c),
        t_end_c=end,
        requested_t_end_c=requested_end,
        range_was_capped=capped,
        t_step_c=step,
        n_points=len(predictions),
        predictions=predictions,
        unavailable_predictions=unavailable_predictions,
        unavailable_point_count=len(unavailable_predictions),
        extrapolated_points=extrapolated,
        fitted_temperature_range_c=fitted_temperature_range_c,
        source_grid_temperature_range_c=source_grid_temperature_range_c,
        sensitivity_extrapolation_max_c=thermo.SENSITIVITY_EXTRAPOLATION_MAX_C,
        boiling_point_c=boiling_point,
        atmospheric_range_exceeds_bp=bool(boiling_point is not None and end >= boiling_point),
        solubility_unit="wt_pct_solution_concentration",
        screening_reference_wt_pct=5.0,
        below_screening_reference=bool(
            predictions and max(row["solubility_pct"] for row in predictions) < 5.0
        ),
        clip_limit_wt_percent=100.0,
        **thermo.get_solvent_hazard_framing(solvent),
        solvent_catalog_provenance=thermo.get_solvent_catalog_provenance(),
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
    )


def _top_k(value: int) -> int:
    try:
        return max(1, min(int(value), 50))
    except (TypeError, ValueError):
        return 10


def _assign_clipped_ceiling_ranks(
    rows: list[dict],
    *,
    rank_key: str = "rank",
) -> None:
    """Assign competition ranks while treating clipped ceilings as one tie."""
    clipped_rank = next((
        ordinal for ordinal, row in enumerate(rows, 1)
        if row.get("is_clipped") is True
    ), None)
    clipped_count = sum(
        row.get("is_clipped") is True for row in rows
    )
    for ordinal, row in enumerate(rows, 1):
        if row.get("is_clipped") is True and clipped_count > 1:
            row[rank_key] = clipped_rank
            row["rank_tie_basis"] = "clipped_model_ceiling"
        else:
            row[rank_key] = ordinal


def _is_extrapolated_threshold_row(row: dict) -> bool:
    """Return whether a modeled value sits outside its admitted data range."""
    regimes = [row.get("temperature_use_regime")]
    regimes.extend(
        (row.get("temperature_use_regime_by_polymer") or {}).values()
    )
    return any("extrapolation" in str(regime or "") for regime in regimes)


def _meets_unclipped_threshold(row: dict, threshold: float) -> bool:
    """Require an unclipped, fitted-domain value for threshold evidence."""
    return bool(
        row.get("is_clipped") is not True
        and not _is_extrapolated_threshold_row(row)
        and float(row["solubility_pct"]) >= threshold
    )


def _extrapolated_meets_threshold(row: dict, threshold: float) -> bool:
    """Count an above-threshold extrapolation without treating it as evidence."""
    return bool(
        row.get("is_clipped") is not True
        and _is_extrapolated_threshold_row(row)
        and float(row["solubility_pct"]) >= threshold
    )


def rank_polymers_by_solubility(
    solvent_name: str,
    temperature_c: float,
    top_k: int = 5,
    min_solubility_pct: float = 5.0,
    requested_polymers: Optional[list[str]] = None,
) -> str:
    """Rank all supported polymers in one solvent at exactly one temperature.

    Choose this for polymers in one fixed solvent at one exact temperature.
    """
    tool = "rank_polymers_by_solubility"
    if error := _temperature_error(tool, temperature_c):
        return error
    solvent = thermo.resolve_solvent(solvent_name)
    if solvent is None:
        return _solvent_resolution_error(tool, solvent_name)
    evaluated = [
        row for polymer in sorted(thermo.get_available_polymers())
        if (row := _pair_result(polymer, solvent, float(temperature_c))) is not None
    ]
    evaluated.sort(key=lambda row: (
        _is_extrapolated_threshold_row(row),
        -row["solubility_pct"], row["polymer"],
    ))
    _assign_clipped_ceiling_ranks(evaluated)
    by_polymer = {row["polymer"]: row for row in evaluated}
    requested_supported, unsupported, unavailable = [], [], []
    for label in requested_polymers or []:
        resolved = thermo.resolve_polymer(str(label))
        if resolved is None:
            unsupported.append(str(label))
        elif resolved not in by_polymer:
            unavailable.append(resolved)
        elif resolved not in requested_supported:
            requested_supported.append(resolved)
    results = (
        [row for row in evaluated if row["polymer"] in set(requested_supported)]
        if requested_polymers else evaluated[:_top_k(top_k)]
    )
    threshold = max(0.0, float(min_solubility_pct))
    comparison = results if requested_polymers else evaluated
    meeting = sum(
        _meets_unclipped_threshold(row, threshold) for row in comparison
    )
    clipped_meeting = sum(
        row.get("is_clipped") is True
        and row["solubility_pct"] >= threshold
        for row in comparison
    )
    extrapolated_meeting = sum(
        _extrapolated_meets_threshold(row, threshold) for row in comparison
    )
    boiling_point = thermo.get_boiling_point(solvent)
    return tool_success(
        tool,
        display=_table(
            ("Rank", "Polymer", "Solubility (wt%)", "Basis"),
            tuple((row["rank"], row["polymer"], f"{row['solubility_pct']:.6g}", row["method"]) for row in results),
        ),
        ranking_axis="polymers",
        solvent=thermo.canonical_solvent_name(solvent),
        solvent_data_key=solvent,
        temperature_c=float(temperature_c),
        min_solubility_pct=threshold,
        requested_polymers=[str(item) for item in requested_polymers or []],
        requested_supported_polymers=requested_supported,
        unsupported_requested_polymers=unsupported,
        unavailable_requested_polymers=unavailable,
        n_supported_candidates=len(evaluated),
        n_candidates_evaluated=len(evaluated),
        n_candidates_unavailable=len(unavailable),
        unavailable_candidates=unavailable,
        n_meeting_threshold=meeting,
        n_clipped_meeting_threshold=clipped_meeting,
        n_extrapolated_meeting_threshold=extrapolated_meeting,
        n_meeting_threshold_global=sum(
            _meets_unclipped_threshold(row, threshold) for row in evaluated
        ),
        n_clipped_meeting_threshold_global=sum(
            row.get("is_clipped") is True
            and row["solubility_pct"] >= threshold
            for row in evaluated
        ),
        threshold_count_excludes_clipped=True,
        n_extrapolated_meeting_threshold_global=sum(
            _extrapolated_meets_threshold(row, threshold) for row in evaluated
        ),
        threshold_count_excludes_extrapolated=True,
        best_result_is_weak=(
            not comparison
            or not _meets_unclipped_threshold(comparison[0], threshold)
        ),
        boiling_point_c=boiling_point,
        atmospheric_operation=None if boiling_point is None else float(temperature_c) < boiling_point,
        temperature_use_regime=thermo.aggregate_temperature_use_regimes(
            float(temperature_c),
            (row.get("temperature_use_regime") for row in evaluated),
        ),
        solvent_catalog_provenance=thermo.get_solvent_catalog_provenance(),
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
        solubility_unit="wt_pct_solution_concentration",
        results=results,
    )


def rank_solvents_by_solubility(
    polymer_name: str,
    temperature_c: float,
    top_k: int = 5,
    min_solubility_pct: float = 5.0,
    require_atmospheric: bool = True,
) -> str:
    """Rank all supported solvents for one polymer at exactly one temperature.

    Choose this for solvents for one polymer at one exact temperature.
    Catalog provenance reports the count this call actually evaluated for the
    named polymer, rather than inferring coverage from a separate accessor.
    """
    tool = "rank_solvents_by_solubility"
    if error := _temperature_error(tool, temperature_c):
        return error
    polymer = thermo.resolve_polymer(polymer_name)
    if polymer is None:
        return tool_error(
            tool,
            f"Unknown polymer: {polymer_name!r}.",
            error_code="unknown_polymer",
            available_polymers=sorted(thermo.get_available_polymers()),
        )
    evaluated, eligible, unavailable, excluded = [], [], [], []
    for solvent in sorted(thermo.get_available_solvents()):
        row = _pair_result(polymer, solvent, float(temperature_c))
        if row is None:
            unavailable.append(thermo.canonical_solvent_name(solvent))
            continue
        boiling_point = thermo.get_boiling_point(solvent)
        row.update({
            "boiling_point_c": boiling_point,
            "normal_bp_margin_c": None if boiling_point is None else boiling_point - float(temperature_c),
            "atmospheric_operation": None if boiling_point is None else float(temperature_c) < boiling_point,
        })
        evaluated.append(row)
        if require_atmospheric and row["atmospheric_operation"] is not True:
            excluded.append({
                "solvent": row["solvent"],
                "boiling_point_c": boiling_point,
                "solubility_pct": row["solubility_pct"],
                "method": row["method"],
            })
        else:
            eligible.append(row)
    eligible.sort(key=lambda row: (
        _is_extrapolated_threshold_row(row),
        -row["solubility_pct"], row["solvent"],
    ))
    _assign_clipped_ceiling_ranks(eligible)
    threshold = max(0.0, float(min_solubility_pct))
    results = eligible[:_top_k(top_k)]
    from .safety import condition_operability, merge_condition_operability

    for row in results:
        merge_condition_operability(
            row, condition_operability(row["solvent"], float(temperature_c)),
        )
    catalog_provenance = _single_polymer_catalog_provenance(len(evaluated))
    assert catalog_provenance["fitted_solvents_for_polymer"] == len(evaluated)
    return tool_success(
        tool,
        display=_table(
            ("Rank", "Solvent", "Solubility (wt%)", "BP margin (C)"),
            tuple((row["rank"], row["solvent"], f"{row['solubility_pct']:.6g}", row["normal_bp_margin_c"]) for row in results),
        ),
        ranking_axis="solvents",
        polymer=polymer,
        temperature_c=float(temperature_c),
        min_solubility_pct=threshold,
        require_atmospheric=bool(require_atmospheric),
        n_supported_candidates=len(evaluated),
        n_candidates_evaluated=len(evaluated),
        n_candidates_ranked=len(eligible),
        n_candidates_unavailable=len(unavailable),
        unavailable_candidates=unavailable,
        n_excluded_atmospheric=len(excluded),
        excluded_atmospheric=excluded[:5],
        excluded_atmospheric_was_truncated=len(excluded) > 5,
        n_meeting_threshold=sum(
            _meets_unclipped_threshold(row, threshold) for row in eligible
        ),
        n_clipped_meeting_threshold=sum(
            row.get("is_clipped") is True
            and row["solubility_pct"] >= threshold
            for row in eligible
        ),
        n_extrapolated_meeting_threshold=sum(
            _extrapolated_meets_threshold(row, threshold) for row in eligible
        ),
        threshold_count_excludes_clipped=True,
        threshold_count_excludes_extrapolated=True,
        best_result_is_weak=(
            not eligible
            or not _meets_unclipped_threshold(eligible[0], threshold)
        ),
        temperature_use_regime=thermo.aggregate_temperature_use_regimes(
            float(temperature_c),
            (row.get("temperature_use_regime") for row in evaluated),
        ),
        solvent_catalog_provenance=catalog_provenance,
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
        solubility_unit="wt_pct_solution_concentration",
        results=results,
    )


def _unique_names(values: Sequence[str]) -> list[str]:
    result, seen = [], set()
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def normalize_feed_composition(
    supplied: Optional[dict[str, float]], polymers: list[str],
) -> Optional[dict[str, float]]:
    if supplied is None:
        return None
    if not isinstance(supplied, dict) or not supplied:
        raise ValueError("feed_mass_fractions must map every feed polymer to a fraction or percent")
    resolved: dict[str, float] = {}
    for name, raw in supplied.items():
        polymer = thermo.resolve_polymer(str(name))
        value = float(raw)
        if polymer is None or not math.isfinite(value) or value <= 0:
            raise ValueError("feed_mass_fractions contains an unknown polymer or invalid value")
        resolved[polymer] = resolved.get(polymer, 0.0) + value
    if set(resolved) != set(polymers):
        raise ValueError("feed_mass_fractions must cover exactly the screened feed polymers")
    total = sum(resolved.values())
    if abs(total - 100.0) <= 0.01:
        resolved = {key: value / 100.0 for key, value in resolved.items()}
    elif abs(total - 1.0) > 0.0001:
        raise ValueError("feed_mass_fractions must sum to 1 or 100")
    return resolved


def _temperature_grid(start: float, end: float, step: float, strict: bool) -> list[float]:
    """Grid nodes inside the bounds. See thermodynamics._nodes_within.

    The single choke point for every range sweep in the tree — this tool, the
    two safety screens, and the two separation screens. Bounding it to stored
    nodes is what stops a screen from evaluating nothing and reporting that
    nothing qualified.
    """
    return thermo._nodes_within(start, end, step, strict)


def _screen_direction(
    target: str,
    retained: list[str],
    temperatures: list[float],
    require_atmospheric: bool,
    limit: int,
    candidate_solvents: Optional[set[str]] = None,
    ranking_mode: Literal[
        "target_dissolution", "separation_gap", "absolute_solubility",
    ] = "target_dissolution",
    solubility_threshold_pct: float = 5.0,
    common_temperature_counts: Optional[dict[float, dict[str, int]]] = None,
    common_temperature_candidates: Optional[dict[float, list[dict]]] = None,
    prefer_source_grid_evidence: bool = True,
) -> tuple[list[dict], int, int, list[dict]]:
    best_by_solvent, screened, excluded = {}, 0, 0
    for temperature in temperatures:
        rows = (
            [{
                "solvent": row["solvent"],
                "solvent_data_key": row["solvent_data_key"],
                "target_sol": row["solubility_pct"],
                "max_other_sol": None,
                "solubility_method_by_polymer": {
                    target: row["method"],
                },
                "temperature_use_regime_by_polymer": {
                    target: row["temperature_use_regime"],
                },
                "fitted_temperature_range_by_polymer": {
                    target: row.get("fitted_temperature_range_c"),
                },
                "source_grid_temperature_range_by_polymer": {
                    target: row.get("source_grid_temperature_range_c"),
                },
            } for solvent in sorted(thermo.get_available_solvents())
             if (row := _pair_result(target, solvent, temperature)) is not None]
            if ranking_mode == "absolute_solubility"
            else thermo.get_all_solvents_selectivity(target, retained, temperature)
        )
        for row in rows:
            display_solvent = str(row["solvent"])
            resolved_solvent = thermo.resolve_solvent(display_solvent)
            if candidate_solvents is not None and resolved_solvent not in candidate_solvents:
                continue
            screened += 1
            solvent_key = (
                row.get("solvent_data_key")
                or resolved_solvent
                or row["solvent"]
            )
            boiling_point = thermo.get_boiling_point(solvent_key)
            atmospheric = boiling_point is not None and temperature < boiling_point
            if require_atmospheric and not atmospheric:
                excluded += 1
                continue
            clipped = float(row["target_sol"]) >= 100.0
            threshold_temperature_regime = thermo.aggregate_temperature_use_regimes(
                temperature,
                (row.get("temperature_use_regime_by_polymer") or {}).values(),
            )
            if common_temperature_counts is not None:
                counts = common_temperature_counts[temperature]
                if clipped:
                    counts["clipped"] += 1
                elif (
                    "extrapolation" in threshold_temperature_regime
                    and float(row["target_sol"]) >= solubility_threshold_pct
                ):
                    counts["extrapolated"] += 1
                elif float(row["target_sol"]) >= solubility_threshold_pct:
                    counts["qualifying"] += 1
            off_target_evidence = ({
                polymer: thermo.get_solubility_result(
                    polymer, solvent_key, temperature,
                )
                for polymer in retained
            } if ranking_mode != "absolute_solubility" else {})
            off_targets = {
                polymer: evidence.get("solubility_pct")
                for polymer, evidence in off_target_evidence.items()
            }
            if any(value is None for value in off_targets.values()):
                continue
            if ranking_mode == "separation_gap":
                limiting = min(
                    off_targets,
                    key=lambda polymer: (
                        abs(float(row["target_sol"]) - float(off_targets[polymer])),
                        polymer,
                    ),
                )
                limiting_value = float(off_targets[limiting])
                signed_gap = float(row["target_sol"]) - limiting_value
                score = abs(signed_gap)
                deltas = [float(row["target_sol"]) - float(value) for value in off_targets.values()]
                direction = (
                    "target_lower_than_all_off_targets" if all(delta < 0 for delta in deltas)
                    else "target_higher_than_all_off_targets" if all(delta > 0 for delta in deltas)
                    else "mixed"
                )
            elif ranking_mode == "target_dissolution":
                limiting = max(off_targets, key=off_targets.get) if off_targets else None
                limiting_value = None if limiting is None else float(off_targets[limiting])
                signed_gap = float(row["selectivity"])
                score = signed_gap
                direction = None
            else:
                limiting = None
                limiting_value = None
                signed_gap = None
                score = float(row["target_sol"])
                direction = None
            canonical_solvent = thermo.canonical_solvent_name(solvent_key)
            solubility_method_by_polymer = {
                **dict(row.get("solubility_method_by_polymer") or {}),
                **{
                    polymer: evidence["method"]
                    for polymer, evidence in off_target_evidence.items()
                    if evidence.get("available")
                },
            }
            temperature_use_regime_by_polymer = {
                **dict(row.get("temperature_use_regime_by_polymer") or {}),
                **{
                    polymer: evidence["temperature_use_regime"]
                    for polymer, evidence in off_target_evidence.items()
                    if evidence.get("available")
                },
            }
            fitted_temperature_range_by_polymer = {
                **dict(row.get("fitted_temperature_range_by_polymer") or {}),
                **{
                    polymer: evidence.get("fitted_temperature_range_c")
                    for polymer, evidence in off_target_evidence.items()
                    if evidence.get("available")
                },
            }
            source_grid_temperature_range_by_polymer = {
                **dict(
                    row.get("source_grid_temperature_range_by_polymer") or {}
                ),
                **{
                    polymer: evidence.get("source_grid_temperature_range_c")
                    for polymer, evidence in off_target_evidence.items()
                    if evidence.get("available")
                },
            }
            candidate_temperature_regime = thermo.aggregate_temperature_use_regimes(
                temperature, temperature_use_regime_by_polymer.values(),
            )
            candidate = {
                "solvent": canonical_solvent,
                "temperature_c": temperature,
                "ranking_score": score,
                "selectivity_pct": (
                    None if ranking_mode == "absolute_solubility" else score
                ),
                "target_solubility_pct": row["target_sol"],
                "max_off_target_solubility_pct": row.get("max_other_sol"),
                "limiting_off_target_polymer": limiting,
                "off_target_solubilities_pct": off_targets,
                "solubility_method_by_polymer": solubility_method_by_polymer,
                "boiling_point_c": boiling_point,
                "boiling_point_margin_c": None if boiling_point is None else boiling_point - temperature,
                "atmospheric_feasible": atmospheric,
                "temperature_extrapolation": (
                    "mixed"
                    if len({
                        thermo.temperature_extrapolation_status(
                            temperature,
                            fitted_range,
                        )
                        for fitted_range in (
                            fitted_temperature_range_by_polymer.values()
                        )
                    }) > 1
                    else thermo.temperature_extrapolation_status(
                        temperature,
                        next((
                            fitted_range for fitted_range in (
                                fitted_temperature_range_by_polymer.values()
                            )
                        ), None),
                    )
                ),
                "temperature_use_regime": candidate_temperature_regime,
                "meets_selectivity_threshold": (
                    None if ranking_mode == "absolute_solubility" else score >= 5.0
                ),
                "meets_solubility_threshold": bool(
                    float(row["target_sol"]) >= solubility_threshold_pct
                    and not clipped
                    and (
                        common_temperature_counts is None
                        or "extrapolation" not in threshold_temperature_regime
                    )
                ),
                "is_clipped": clipped,
                "clip_limit_wt_percent": 100.0,
                **thermo.get_solvent_hazard_framing(solvent_key),
            }
            if (
                "extrapolation" in candidate_temperature_regime
                or candidate_temperature_regime == "mixed"
            ):
                candidate.update({
                    "temperature_use_regime_by_polymer": (
                        temperature_use_regime_by_polymer
                    ),
                    "fitted_temperature_range_by_polymer": (
                        fitted_temperature_range_by_polymer
                    ),
                    "source_grid_temperature_range_by_polymer": (
                        source_grid_temperature_range_by_polymer
                    ),
                })
            if ranking_mode == "separation_gap":
                candidate.update({
                    "minimum_target_off_target_gap_pct": score,
                    "signed_target_minus_limiting_off_target_pct": signed_gap,
                    "closest_off_target_solubility_pct": limiting_value,
                    "target_solubility_direction": direction,
                })
            if display_solvent.casefold() != canonical_solvent.casefold():
                candidate["source_solvent"] = display_solvent
            if common_temperature_candidates is not None:
                common_temperature_candidates[temperature].append(candidate)
            key = candidate["solvent"].casefold()
            existing = best_by_solvent.get(key)
            candidate_priority = (
                *((not _is_extrapolated_threshold_row(candidate),)
                  if prefer_source_grid_evidence else ()),
                candidate["ranking_score"], candidate["target_solubility_pct"],
            )
            existing_priority = (
                *((not _is_extrapolated_threshold_row(existing),)
                  if prefer_source_grid_evidence else ()),
                existing["ranking_score"], existing["target_solubility_pct"],
            ) if existing is not None else None
            if existing_priority is None or candidate_priority > existing_priority:
                best_by_solvent[key] = candidate
    ranked, ranked_all = _rank_screen_candidates(
        list(best_by_solvent.values()), limit, ranking_mode,
        prefer_source_grid_evidence=prefer_source_grid_evidence,
    )
    return ranked, screened, excluded, ranked_all


def _rank_screen_candidates(
    candidates: list[dict],
    limit: int,
    ranking_mode: Literal[
        "target_dissolution", "separation_gap", "absolute_solubility",
    ],
    *,
    prefer_source_grid_evidence: bool = True,
) -> tuple[list[dict], list[dict]]:
    """Sort and annotate one candidate population without changing its scope."""
    ranked_all = sorted(
        candidates,
        key=lambda item: (
            *((_is_extrapolated_threshold_row(item),)
              if prefer_source_grid_evidence else ()),
            -item["ranking_score"], -item["target_solubility_pct"],
            item["solvent"],
        ),
    )
    ranked = ranked_all[:limit]
    from .safety import condition_operability, merge_condition_operability

    for item in ranked:
        merge_condition_operability(
            item,
            condition_operability(item["solvent"], item["temperature_c"]),
        )
    if ranking_mode == "absolute_solubility":
        _assign_clipped_ceiling_ranks(ranked)
    else:
        for rank, item in enumerate(ranked, 1):
            item["rank"] = rank
    return ranked, ranked_all


def screen_polymer_separation(
    feed_polymers: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    target_polymers: Optional[list[str]] = None,
    solvents: Optional[list[str]] = None,
    ranking_mode: Literal[
        "target_dissolution", "separation_gap", "absolute_solubility",
    ] = "target_dissolution",
    strict_maximum: bool = False,
    temperature_step_c: float = 5.0,
    require_atmospheric: Optional[bool] = None,
    feed_mass_fractions: Optional[dict[str, float]] = None,
    top_k: Optional[int] = None,
    min_solubility_pct: float = 5.0,
    minimum_qualifying_solvent_count: Optional[int] = None,
) -> str:
    """Adaptively screen absolute dissolution or target/off-target separation.

    Use this directly when a user asks whether several named feed polymers can
    be distinguished by solubility and which solvents work for each target;
    choose ``target_dissolution`` for that independent target screen.  This
    tool does not calculate cooling or precipitation order.

    Choose this for broad solvent discovery, or for one/many-polymer
    dissolution selectivity over a temperature range.  Omit ``top_k`` to keep
    the historical shortlist (5 solvents for one polymer, 3 per target
    otherwise).  An explicit value is honoured in the raw payload; the compact
    may show fewer and then sets ``ranked_candidates_truncated`` and
    ``ranked_candidates_total``.

    Multi-polymer catalog provenance names only polymers whose actual
    pair-screen count at the highest screened temperature is below 90% of the
    fitted solvent catalog.  A one-polymer screen reports its exact count.

    For a lowest-temperature question about how many solvents clear a modeled
    solubility threshold, pass one feed polymer, the requested
    ``min_solubility_pct``, and ``minimum_qualifying_solvent_count``.  The
    result then reports full-candidate counts at each common grid setpoint;
    never infer that answer from the per-solvent best-temperature shortlist.
    Clipped 100 wt% ceilings and above-range extrapolations never qualify.
    """
    tool = "screen_polymer_separation"
    if not isinstance(feed_polymers, (list, tuple)) or not (requested := _unique_names(feed_polymers)):
        return tool_error(tool, "feed_polymers must contain at least one name.", error_code="invalid_feed_polymers")
    names, unsupported = [], []
    for name in requested:
        resolved = thermo.resolve_polymer(name)
        (names if resolved else unsupported).append(resolved or name)
    if unsupported:
        return tool_error(
            tool,
            "Unsupported feed polymer(s): " + ", ".join(unsupported),
            error_code="unknown_polymer",
            unsupported_polymers=unsupported,
            available_polymers=sorted(thermo.get_available_polymers()),
        )
    names = _unique_names(names)
    if ranking_mode not in {
        "target_dissolution", "separation_gap", "absolute_solubility",
    }:
        return tool_error(
            tool,
            "ranking_mode must be target_dissolution, separation_gap, or absolute_solubility.",
            error_code="invalid_ranking_mode",
        )
    if ranking_mode == "separation_gap" and len(names) < 2:
        return tool_error(
            tool,
            "separation_gap requires at least two feed polymers.",
            error_code="separation_gap_requires_multiple_polymers",
        )
    try:
        composition = normalize_feed_composition(feed_mass_fractions, names)
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_feed_composition")
    supplied_min, supplied_max = temperature_min_c is not None, temperature_max_c is not None
    start = thermo.FITTED_TEMP_MIN_C if temperature_min_c is None else float(temperature_min_c)
    end = thermo.FITTED_TEMP_MAX_C if temperature_max_c is None else float(temperature_max_c)
    try:
        step = float(temperature_step_c)
    except (TypeError, ValueError):
        step = 0.0
    if not all(math.isfinite(value) for value in (start, end, step)) or step <= 0 or end < start:
        return tool_error(tool, "Invalid finite temperature range or step.", error_code="invalid_temperature_range")
    if target_polymers is None:
        targets = list(names)
    elif not isinstance(target_polymers, (list, tuple)):
        return tool_error(tool, "target_polymers must be a list.", error_code="invalid_target_polymers")
    else:
        targets = []
        for target in _unique_names(target_polymers):
            resolved = thermo.resolve_polymer(target)
            if resolved not in names:
                return tool_error(
                    tool,
                    "Every target must be in feed_polymers.",
                    error_code="target_not_in_feed",
                    unknown_target_polymers=[target],
                )
            if resolved not in targets:
                targets.append(resolved)
        if not targets:
            return tool_error(tool, "target_polymers cannot be empty.", error_code="missing_target_polymers")
    constrained_solvents: Optional[list[str]] = None
    if solvents is not None:
        if not isinstance(solvents, (list, tuple)) or not solvents:
            return tool_error(
                tool, "solvents must contain at least one name when supplied.",
                error_code="invalid_solvents",
            )
        constrained_solvents = []
        unsupported_solvents = []
        for supplied in _unique_names(solvents):
            resolved = thermo.resolve_solvent(supplied)
            if resolved is None:
                unsupported_solvents.append(supplied)
            elif resolved not in constrained_solvents:
                constrained_solvents.append(resolved)
        if unsupported_solvents:
            return _solvent_resolution_errors(tool, unsupported_solvents)
    temperatures = _temperature_grid(start, end, step, bool(strict_maximum))
    if not temperatures:
        return tool_error(tool, "No temperatures remain after applying bounds.", error_code="empty_temperature_grid")
    catalog_provenance = _screen_catalog_provenance(names, max(temperatures))
    if require_atmospheric is None:
        resolved_require_atmospheric = not (
            constrained_solvents is not None and len(temperatures) == 1
        )
        atmospheric_filter_policy = (
            "auto_report_all_exact_shortlist"
            if not resolved_require_atmospheric else "auto_filter_discovery"
        )
    else:
        resolved_require_atmospheric = bool(require_atmospheric)
        atmospheric_filter_policy = "explicit"
    single = len(names) == 1
    try:
        solubility_threshold = max(0.0, float(min_solubility_pct))
    except (TypeError, ValueError):
        solubility_threshold = math.nan
    if not math.isfinite(solubility_threshold):
        return tool_error(
            tool,
            "min_solubility_pct must be finite.",
            error_code="invalid_solubility_threshold",
        )
    qualifying_count: Optional[int] = None
    if minimum_qualifying_solvent_count is not None:
        if (
            isinstance(minimum_qualifying_solvent_count, bool)
            or not isinstance(minimum_qualifying_solvent_count, (int, float))
            or not math.isfinite(float(minimum_qualifying_solvent_count))
            or float(minimum_qualifying_solvent_count) < 1
            or not float(minimum_qualifying_solvent_count).is_integer()
        ):
            return tool_error(
                tool,
                "minimum_qualifying_solvent_count must be a positive integer.",
                error_code="invalid_qualifying_solvent_count",
            )
        if not single:
            return tool_error(
                tool,
                "A common-temperature solvent count requires exactly one feed polymer.",
                error_code="threshold_count_requires_single_polymer",
            )
        qualifying_count = int(minimum_qualifying_solvent_count)
        # A same-setpoint solvent-count question is an absolute dissolution
        # screen even when the caller leaves the historical default in place.
        # Normalize the mode before candidates are evaluated so that no
        # target/off-target field can accidentally stand in for the requested
        # solubility threshold.
        ranking_mode = "absolute_solubility"
    extra: dict[str, Any] = {}
    if top_k is not None:
        candidate_limit = _top_k(top_k)
        extra["shortlist_requested"] = candidate_limit
    else:
        candidate_limit = 5 if single else 3
    # Inside the standard catalog envelope, newly served pair-fit
    # extrapolations are fallback evidence rather than silent replacements.
    # Multi-polymer process choices keep that evidence priority at every
    # range; only an explicitly extended single-polymer screen ranks the
    # extrapolation tier directly by the requested scientific metric.
    prefer_source_grid_evidence = bool(
        not single or end <= thermo.FITTED_TEMP_MAX_C
    )
    directions, combined, recommendations = [], [], {}
    screened_conditions = excluded_conditions = 0
    common_temperature_counts = (
        {
            temperature: {
                "qualifying": 0, "clipped": 0, "extrapolated": 0,
            }
            for temperature in temperatures
        }
        if qualifying_count is not None else None
    )
    common_temperature_candidates = (
        {temperature: [] for temperature in temperatures}
        if qualifying_count is not None else None
    )
    first_qualifying: Optional[tuple[float, int]] = None
    ranked_candidate_population: Optional[str] = None
    ranked_candidate_temperature: Optional[float] = None
    ranked_candidate_population_count: Optional[int] = None
    ranked_candidate_population_complete: Optional[bool] = None
    for target in targets:
        retained = (
            [] if ranking_mode == "absolute_solubility"
            else [polymer for polymer in names if polymer != target]
        )
        candidates, screened, excluded, complete_candidates = _screen_direction(
            target, retained, temperatures, resolved_require_atmospheric, candidate_limit,
            None if constrained_solvents is None else set(constrained_solvents),
            ranking_mode,
            solubility_threshold,
            common_temperature_counts,
            common_temperature_candidates,
            prefer_source_grid_evidence,
        )
        if (
            common_temperature_counts is not None
            and common_temperature_candidates is not None
        ):
            first_qualifying = next((
                (temperature, counts["qualifying"])
                for temperature, counts in common_temperature_counts.items()
                if counts["qualifying"] >= qualifying_count
            ), None)
            if first_qualifying is None:
                candidates, complete_candidates = [], []
                ranked_candidate_population = (
                    "none_meet_common_temperature_threshold"
                )
            else:
                ranked_candidate_temperature = first_qualifying[0]
                shared_population = [
                    candidate
                    for candidate in common_temperature_candidates[
                        ranked_candidate_temperature
                    ]
                    if candidate["meets_solubility_threshold"]
                ]
                shared_limit = (
                    candidate_limit if top_k is not None
                    else min(len(shared_population), 50)
                )
                candidates, complete_candidates = _rank_screen_candidates(
                    shared_population, shared_limit, ranking_mode,
                    prefer_source_grid_evidence=False,
                )
                ranked_candidate_population = (
                    "qualifying_fitted_domain_candidates_at_lowest_common_temperature"
                )
                ranked_candidate_population_count = len(shared_population)
                ranked_candidate_population_complete = (
                    len(candidates) == len(shared_population)
                )
        screened_conditions += screened
        excluded_conditions += excluded
        for candidate in candidates:
            candidate["dissolved_polymer"] = target
            candidate["retained_polymers"] = retained
            combined.append(candidate)
        best = candidates[0] if candidates else None
        if best:
            from .safety import attach_lower_hazard_disclosure

            decision_value_key = (
                "target_solubility_pct"
                if ranking_mode == "absolute_solubility"
                else "minimum_target_off_target_gap_pct"
                if ranking_mode == "separation_gap"
                else "selectivity_pct"
            )
            attach_lower_hazard_disclosure(
                best,
                complete_candidates,
                top_k=candidate_limit,
                decision_metric=decision_value_key,
                decision_value_key=decision_value_key,
                decision_unit=(
                    "wt_pct_solution_concentration"
                    if ranking_mode == "absolute_solubility"
                    else "percentage_points"
                ),
            )
        threshold_field = (
            "meets_solubility_threshold"
            if single or ranking_mode == "absolute_solubility"
            else "meets_selectivity_threshold"
        )
        predicted_viable = bool(best and best[threshold_field])
        strong_overlap = bool(
            ranking_mode == "target_dissolution"
            and predicted_viable
            and float(best["target_solubility_pct"]) > 0
            and float(best["max_off_target_solubility_pct"])
            / float(best["target_solubility_pct"]) >= _STRONG_OVERLAP_RATIO
        )
        recommendations[target] = ({
            "predicted_viable": predicted_viable,
            "strong_off_target_overlap": strong_overlap,
            **{key: best.get(key) for key in (
                "solvent", "temperature_c", "target_solubility_pct",
                "max_off_target_solubility_pct", "limiting_off_target_polymer",
                "selectivity_pct", "minimum_target_off_target_gap_pct",
                "signed_target_minus_limiting_off_target_pct",
                "closest_off_target_solubility_pct", "target_solubility_direction",
                "meets_solubility_threshold",
                "boiling_point_margin_c", "temperature_use_regime",
                "solubility_method_by_polymer",
                "ghs_signal_word", "typed_safety_evidence",
                "lower_hazard_search_scope", "lower_hazard_alternative_count",
                "lower_hazard_alternatives",
                "lower_hazard_alternatives_truncated", "ghs_danger_unavoidable",
            )},
        } if best else None)
        if best and recommendations[target] is not None:
            recommendations[target].update({
                key: best[key] for key in (
                    "temperature_use_regime_by_polymer",
                    "fitted_temperature_range_by_polymer",
                    "source_grid_temperature_range_by_polymer",
                ) if key in best
            })
        directions.append({
            "dissolved_polymer": target,
            "retained_polymers": retained,
            "predicted_viable": predicted_viable,
            "best_candidate": best,
            "failure_reason": (
                None if predicted_viable
                else "no_candidate_met_solubility_threshold"
                if single or ranking_mode == "absolute_solubility"
                else "no_candidate_met_selectivity_threshold"
            ),
        })
    combined.sort(key=lambda item: (
        *((_is_extrapolated_threshold_row(item),)
          if prefer_source_grid_evidence else ()),
        -item["ranking_score"], -item["target_solubility_pct"], item["solvent"],
    ))
    if single or ranking_mode == "absolute_solubility":
        _assign_clipped_ceiling_ranks(
            combined, rank_key="overall_rank",
        )
    else:
        for rank, candidate in enumerate(combined, 1):
            candidate["overall_rank"] = rank
    for candidate in combined:
        candidate.pop("ranking_score", None)
    recommended = combined[0] if combined else None
    weak = not recommended or not recommended["meets_solubility_threshold"] or (
        not single and ranking_mode != "absolute_solubility"
        and recommended["selectivity_pct"] < 5.0
    )
    threshold_margin = (
        None if single or ranking_mode == "absolute_solubility" or recommended is None
        else float(recommended["selectivity_pct"]) - 5.0
    )
    marginal = bool(
        not weak and threshold_margin is not None and 0.0 <= threshold_margin < 0.01
    )
    scope = (
        "user_bounded" if supplied_min and supplied_max
        else "fitted_min_to_user_max" if supplied_max
        else "user_min_to_fitted_max" if supplied_min
        else "full_fitted_domain"
    )
    warnings = (
        [
            "Predicted solubility is modeled wt% solution concentration, not feed recovery.",
            "Normal-boiling-point margin addresses phase-state operability only.",
            "Candidates are model-screened and require experimental validation.",
        ]
        if qualifying_count is not None else
        [
            "Each solvent is reported at its own best modeled temperature; this is not a common-temperature ranking.",
            "Predicted solubility is modeled wt% solution concentration, not feed recovery.",
            "Normal-boiling-point margin addresses phase-state operability only.",
            "Candidates are model-screened and require experimental validation.",
        ]
        if single or ranking_mode == "absolute_solubility" else [
            "Predicted solubility is modeled wt% solution concentration, not feed recovery.",
            "A low off-target prediction does not prove complete retention or residue purity.",
            "Normal-boiling-point margin addresses phase-state operability only.",
            "Candidates are model-screened and require experimental validation.",
            *(
                [
                    "Separation gap is the minimum absolute target/off-target modeled-solubility difference; read its direction separately.",
                    "A separation gap does not by itself establish which phase to recover, a precipitation cut, or feed recovery.",
                ]
                if ranking_mode == "separation_gap" else
                ["Selectivity is a signed percentage-point difference, not a percent or absolute value."]
            ),
        ]
    )
    if prefer_source_grid_evidence:
        evidence_priority_warning = (
            "Broad standard-domain scans rank source-grid evidence before "
            "pair-fit extrapolation."
        )
        if single or ranking_mode == "absolute_solubility":
            warnings[0] = (
                "Each solvent is reported at its best source-grid temperature "
                "when available; pair-fit extrapolation is fallback evidence. "
                "This is not a common-temperature ranking."
            )
        else:
            warnings.append(evidence_priority_warning)
    if any(bool(candidate.get("is_clipped")) for candidate in combined):
        warnings.append("A 100 wt% value is a clipped model ceiling, not a precise prediction.")
    if common_temperature_counts is not None:
        threshold_counts = {
            f"{temperature:g}": counts["qualifying"]
            for temperature, counts in common_temperature_counts.items()
        }
        clipped_counts = {
            f"{temperature:g}": counts["clipped"]
            for temperature, counts in common_temperature_counts.items()
            if counts["clipped"]
        }
        extrapolated_counts = {
            f"{temperature:g}": counts["extrapolated"]
            for temperature, counts in common_temperature_counts.items()
            if counts["extrapolated"]
        }
        extra.update({
            "minimum_qualifying_solvent_count": qualifying_count,
            "common_temperature_threshold_counts": threshold_counts,
            "common_temperature_clipped_counts": clipped_counts,
            "common_temperature_extrapolated_threshold_counts": (
                extrapolated_counts
            ),
            "threshold_count_candidate_scope": (
                "all_eligible_candidates_before_top_k_at_each_temperature"
            ),
            "threshold_count_excludes_clipped": True,
            "threshold_count_excludes_extrapolated": True,
            "ranked_candidate_population": ranked_candidate_population,
            "ranked_candidate_temperature_c": ranked_candidate_temperature,
            "ranked_candidate_population_count": (
                ranked_candidate_population_count
            ),
            "ranked_candidate_population_complete": (
                ranked_candidate_population_complete
            ),
            "lowest_common_temperature_meeting_threshold_c": (
                None if first_qualifying is None else first_qualifying[0]
            ),
            "qualifying_solvent_count_at_lowest_common_temperature": (
                None if first_qualifying is None else first_qualifying[1]
            ),
        })
        warnings.insert(
            0,
            "Common-temperature threshold counts use all eligible candidates "
            "at each shared grid setpoint and exclude clipped model ceilings "
            "and extrapolated estimates. The shortlist contains only qualifying "
            "fitted-domain candidates at the reported shared temperature.",
        )
    if not resolved_require_atmospheric and any(
        candidate.get("atmospheric_feasible") is False for candidate in combined
    ):
        warnings.append(
            "Candidates at or above their normal boiling point remain in this thermodynamic "
            "ranking but are not atmospheric liquid-phase conditions."
        )
    return tool_success(
        tool,
        display=_table(
            (
                "Rank", "Target", "Solvent", "T (C)", "Target wt%",
                "Min gap" if ranking_mode == "separation_gap" else
                f"Meets {solubility_threshold:g} wt%"
                if ranking_mode == "absolute_solubility" else
                "Selectivity",
            ),
            tuple((
                row["overall_rank"], row["dissolved_polymer"], row["solvent"],
                row["temperature_c"], f"{row['target_solubility_pct']:.6g}",
                ("yes" if row["meets_solubility_threshold"] else "no")
                if ranking_mode == "absolute_solubility"
                else f"{row['selectivity_pct']:.6g}",
            ) for row in combined),
        ),
        analysis_type=(
            "polymer_dissolution_screen"
            if single or ranking_mode == "absolute_solubility"
            else "polymer_separation_screen"
        ),
        screen_mode=(
            "absolute_dissolution" if single or ranking_mode == "absolute_solubility" else
            "separation_gap" if ranking_mode == "separation_gap" else "selectivity"
        ),
        ranking_mode=ranking_mode,
        ranking_metric=(
            "target_solubility_pct" if single or ranking_mode == "absolute_solubility" else
            "minimum_absolute_target_off_target_gap_pct"
            if ranking_mode == "separation_gap" else "selectivity_pct"
        ),
        polymers=names,
        target_polymers=targets,
        solvents=(
            [thermo.canonical_solvent_name(item) for item in constrained_solvents]
            if constrained_solvents is not None else []
        ),
        candidate_scope=(
            "requested_solvents" if constrained_solvents is not None
            else "all_supported_solvents"
        ),
        temperature_min_c=start,
        temperature_max_c=end,
        strict_maximum=bool(strict_maximum),
        temperature_step_c=step,
        temperature_scope=scope,
        require_atmospheric=resolved_require_atmospheric,
        atmospheric_filter_policy=atmospheric_filter_policy,
        selectivity_definition=(
            None if single or ranking_mode == "absolute_solubility" else
            "minimum absolute target/off-target modeled solution-concentration difference"
            if ranking_mode == "separation_gap" else
            "dissolved_polymer_solubility_pct - retained_polymer_solubility_pct"
        ),
        selectivity_unit=(
            None if single or ranking_mode == "absolute_solubility"
            else "percentage_points"
        ),
        selectivity_is_absolute=(
            None if single or ranking_mode == "absolute_solubility"
            else ranking_mode == "separation_gap"
        ),
        selectivity_threshold_pct=(
            None if single or ranking_mode == "absolute_solubility" else 5.0
        ),
        solubility_threshold_pct=(
            solubility_threshold
            if single or ranking_mode == "absolute_solubility" else None
        ),
        strong_overlap_ratio_threshold=(
            _STRONG_OVERLAP_RATIO
            if not single and ranking_mode == "target_dissolution" else None
        ),
        strong_overlap_definition=(
            None if single or ranking_mode in {"separation_gap", "absolute_solubility"} else
            "max_off_target_solubility_pct / target_solubility_pct at least threshold"
        ),
        solubility_unit="wt_pct_solution_concentration",
        target_recommendations=recommendations,
        screened_directions=directions,
        ranked_candidates=combined,
        recommended_condition=recommended,
        best_result_is_weak=weak,
        best_result_is_marginal=marginal,
        threshold_margin_pct=threshold_margin,
        screened_conditions=screened_conditions,
        excluded_for_boiling_point=excluded_conditions,
        solvent_catalog_provenance=catalog_provenance,
        warnings=warnings,
        model_basis=thermo.SOLUBILITY_MODEL_BASIS,
        feed_mass_fractions=composition,
        **extra,
    )


def screen_pairwise_solubility_overlap(
    feed_polymers: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    solvents: Optional[list[str]] = None,
    strict_maximum: bool = False,
    temperature_step_c: float = 5.0,
    require_atmospheric: bool = True,
    top_k: Optional[int] = None,
) -> str:
    """Rank all feed pairs by best gap; not a directional X-from-Y process screen.

    Choose this for a whole-feed polymer-pair solubility-window ranking over
    every modeled identity and inherited bound.  Reserve it for an explicit
    pair, overlap, hardest-pair, or pair-rank request.  Qualitative
    polymer-solvent RED cannot produce this thermodynamic ranking even when
    the user calls it Hansen; delegate only actual RED matrices.  Omit
    ``top_k`` to return every pair.  An explicit value still evaluates every
    pair, then keeps that many of the sorted ranking; the compact may show
    fewer and then sets ``ranked_pairs_truncated`` and ``ranked_pairs_total``.
    """
    tool = "screen_pairwise_solubility_overlap"
    if not isinstance(feed_polymers, (list, tuple)):
        return tool_error(tool, "feed_polymers must be a list.", error_code="invalid_feed_polymers")
    names, unsupported = [], []
    for supplied in _unique_names(feed_polymers):
        resolved = thermo.resolve_polymer(supplied)
        (names if resolved else unsupported).append(resolved or supplied)
    names = _unique_names(names)
    if unsupported:
        return tool_error(tool, "Unsupported feed polymer(s): " + ", ".join(unsupported),
                          error_code="unknown_polymer", unsupported_polymers=unsupported)
    if len(names) < 2:
        return tool_error(tool, "At least two polymers are required.", error_code="insufficient_feed_polymers")

    ranked: list[dict[str, Any]] = []
    scope: dict[str, Any] | None = None
    for index, first in enumerate(names[:-1]):
        for second in names[index + 1:]:
            envelope = parse_tool_result(screen_polymer_separation(
                [first, second], temperature_min_c, temperature_max_c,
                target_polymers=[first], solvents=solvents,
                ranking_mode="separation_gap", strict_maximum=strict_maximum,
                temperature_step_c=temperature_step_c,
                require_atmospheric=require_atmospheric,
            ))
            data = envelope["data"]
            if data.get("success") is not True or not data.get("recommended_condition"):
                return tool_error(tool, f"No comparable thermodynamic conditions for {first}/{second}.",
                                  error_code="pair_overlap_screen_failed", failed_pair=[first, second])
            scope = scope or data
            candidate = data["recommended_condition"]
            first_value = float(candidate["target_solubility_pct"])
            second_value = float(candidate["closest_off_target_solubility_pct"])
            ranked.append({
                "polymers": [first, second],
                "maximum_absolute_gap_pct": float(candidate["selectivity_pct"]),
                "best_discriminating_condition": {
                    "solvent": candidate["solvent"],
                    "temperature_c": candidate["temperature_c"],
                    "solubilities_wt_pct": {first: first_value, second: second_value},
                    "solubility_method_by_polymer": candidate[
                        "solubility_method_by_polymer"
                    ],
                    **({"ghs_signal_word": candidate["ghs_signal_word"]}
                       if "ghs_signal_word" in candidate else {}),
                },
            })
    ranked.sort(key=lambda row: (row["maximum_absolute_gap_pct"], row["polymers"]))
    for rank, row in enumerate(ranked, 1):
        row["relative_overlap_rank"] = rank
    assert scope is not None
    evaluated_pair_count = len(ranked)
    extra: dict[str, Any] = {}
    if top_k is not None:
        requested = _top_k(top_k)
        extra["shortlist_requested"] = requested
        ranked = ranked[:requested]
    return tool_success(
        tool,
        analysis_type="pairwise_thermodynamic_separability",
        polymers=names, evaluated_pair_count=evaluated_pair_count, ranked_pairs=ranked,
        ranking_definition=("ascending maximum absolute modeled-solubility gap available to each "
                            "pair; a smaller best gap means greater relative overlap"),
        temperature_min_c=scope["temperature_min_c"],
        temperature_max_c=scope["temperature_max_c"],
        strict_maximum=bool(strict_maximum), temperature_step_c=float(temperature_step_c),
        temperature_dependent=True, require_atmospheric=bool(require_atmospheric),
        solubility_unit="wt_pct_solution_concentration", gap_unit="percentage_points",
        hansen_parameters_used=False,
        hansen_applicability=("Hansen RED is a temperature-independent polymer-solvent compatibility "
                              "screen and cannot rank thermodynamic polymer-pair overlap."),
        evidence_class="temperature_dependent_thermodynamic_screen",
        solvent_catalog_provenance=thermo.get_solvent_catalog_provenance(),
        warnings=["Pair ranks are relative; they do not prove inseparability.",
                  "Each pair is optimized independently, not as a route.",
                  "Modeled wt% solution concentration is not recovery or purity."],
        model_basis="pairwise reuse of the unified grid-first solubility screen",
        **extra,
    )


screen_polymer_separation.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]
screen_pairwise_solubility_overlap.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]

