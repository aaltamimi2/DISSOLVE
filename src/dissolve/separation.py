"""Adaptive multi-stage separation capability and its isolated specialist."""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Optional

from langchain_core.tools import InjectedToolArg

from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success
from .session import (
    candidate_evidence, current_tool_session,
    resolve_candidate_argument,
)
from .tools import (
    _atmospheric_exclusion_applies, _atmospheric_exclusion_counts,
    _atmospheric_exclusion_reason,
    _screen_catalog_provenance, _solvent_resolution_error, _temperature_grid,
    normalize_feed_composition, screen_polymer_separation,
)


def _context_declared_solvents(context: dict[str, Any]) -> list[str]:
    """Prefer the typed deliverable, then the same dual-key rule on context."""
    declared = context.get("declared_deliverable")
    if has_declared_solvents(declared):
        return declared_solvents(declared)
    return declared_solvents(context)


def _feed_names(values: object) -> tuple[list[str], list[str]]:
    if not isinstance(values, (list, tuple)):
        return [], ["feed_polymers must be a list"]
    names: list[str] = []
    unsupported: list[str] = []
    for value in values:
        supplied = str(value).strip()
        expanded = thermo.expand_polymer_identity(supplied)
        if not expanded:
            unsupported.append(supplied)
        for resolved in expanded:
            if resolved not in names:
                names.append(resolved)
    return names, list(dict.fromkeys(unsupported))


def _argument_polymer_identities(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        identities: list[str] = []
        for item in value:
            identities.extend(_argument_polymer_identities(item))
        return identities
    text = str(value or "").strip()
    if not text:
        return []
    members = thermo.expand_polymer_identity(text)
    if members:
        return list(members)
    resolved = thermo.resolve_polymer(text)
    return [resolved or text]


def resolve_polymer_data_scope(
    feed_polymers: list[str],
    operating_solvent: Optional[str] = None,
    operating_temperature_c: Optional[float] = None,
    include_capability_inventory: bool = False,
) -> str:
    """Resolve labels, then use the HSP ML model only after a grid-data gap."""
    tool = "resolve_polymer_data_scope"
    if not isinstance(feed_polymers, (list, tuple)):
        return tool_error(tool, "feed_polymers must be a list", error_code="invalid_feed_polymers")
    requested = list(dict.fromkeys(str(value).strip() for value in feed_polymers if str(value).strip()))
    if not requested and include_capability_inventory is not True:
        return tool_error(tool, "feed_polymers cannot be empty", error_code="invalid_feed_polymers")
    has_solvent = bool(str(operating_solvent or "").strip())
    has_temperature = operating_temperature_c is not None
    if has_solvent != has_temperature:
        return tool_error(
            tool, "operating_solvent and operating_temperature_c must be supplied together.",
            error_code="incomplete_operating_condition",
        )
    if has_temperature:
        try:
            operating_temperature_c = float(operating_temperature_c)
        except (TypeError, ValueError):
            return tool_error(tool, "operating_temperature_c must be numeric.", error_code="invalid_temperature")
        if not math.isfinite(operating_temperature_c):
            return tool_error(tool, "operating_temperature_c must be finite.", error_code="invalid_temperature")
    solvent_key = thermo.resolve_solvent(str(operating_solvent)) if has_solvent else None
    modeled, unsupported = _feed_names(requested)
    rows = []
    for label in requested:
        resolved, _ = _feed_names([label])
        row: dict[str, Any] = {
            "requested_polymer": label,
            "canonical_identity": thermo.resolve_polymer_identity(label),
            "modeled_polymers": resolved,
            "status": "supported" if resolved else "out_of_scope",
            "evidence_path": "thermodynamic" if resolved else "no_local_data",
        }
        if resolved and solvent_key and operating_temperature_c is not None:
            operating_results = []
            for polymer in resolved:
                evidence = thermo.get_solubility_result(
                    polymer, solvent_key, operating_temperature_c,
                )
                if not evidence.get("available"):
                    continue
                operating_results.append({
                    "polymer": polymer,
                    "solvent": thermo.canonical_solvent_name(solvent_key),
                    "temperature_c": operating_temperature_c,
                    "solubility_wt_pct": evidence["solubility_pct"],
                })
            row["operating_condition_results"] = operating_results
        if not resolved:
            from .analysis import hsp_fallback_evidence

            fallback = hsp_fallback_evidence(
                label, [str(operating_solvent)] if has_solvent else None,
            )
            row["hsp_fallback"] = fallback
            if fallback["status"] != "no_hsp_record":
                row["evidence_path"] = "hsp_fallback"
        rows.append(row)
    supported = [row["requested_polymer"] for row in rows if row["status"] == "supported"]
    fallback = [row["requested_polymer"] for row in rows if row["evidence_path"] == "hsp_fallback"]
    no_data = [row["requested_polymer"] for row in rows if row["evidence_path"] == "no_local_data"]
    operating_condition = (
        {
            "solvent": thermo.canonical_solvent_name(solvent_key) if solvent_key else str(operating_solvent),
            "temperature_c": operating_temperature_c,
        }
        if has_solvent else None
    )
    capability_inventory = ({
        "available_thermodynamic_solvents": sorted(thermo.canonical_solvent_name(item) for item in thermo.get_available_solvents()), "available_thermodynamic_solvent_count": len(thermo.get_available_solvents()),
    } if include_capability_inventory else {})
    return tool_success(
        tool, analysis_type="polymer_data_scope",
        requested_polymers=requested, requested_label_count=len(requested),
        supported_requested_polymers=supported,
        unsupported_requested_polymers=unsupported,
        hsp_fallback_requested_polymers=fallback,
        no_local_data_requested_polymers=no_data,
        available_thermodynamic_polymers=(
            sorted(thermo.get_available_polymers()) if unsupported or include_capability_inventory is True else []
        ),
        polymers=modeled, modeled_polymer_count=len(modeled), results=rows,
        operating_condition=operating_condition,
        **capability_inventory,
        warnings=[
            "The HSP Random Forest is temperature-independent binary screening trained on RED < 1 labels; it is not wt% solubility.",
            "Coverage does not establish a feasible route or experimental validation.",
        ],
        model_basis=(
            "stored-grid thermodynamics, "
            "then checksummed HSP Random Forest fallback"
        ),
    )


def _declared_material_labels(context: dict[str, Any]) -> list[str]:
    declared = context.get("declared_deliverable")
    if not isinstance(declared, dict):
        return []
    labels: list[str] = []
    for field in (
        "polymers", "solvents", "feed_polymers", "target_polymers",
        "candidate_solvents",
    ):
        values = declared.get(field)
        if not isinstance(values, list):
            continue
        for value in values:
            label = str(value).strip()
            if label and label not in labels:
                labels.append(label)
    return labels


def lookup_material_database_membership(material_names: list[str]) -> str:
    """Check each label independently against polymer and solvent namespaces."""
    tool = "lookup_material_database_membership"
    if not isinstance(material_names, (list, tuple)):
        return tool_error(
            tool, "material_names must be a list",
            error_code="invalid_material_names",
        )
    requested = list(dict.fromkeys(
        str(value).strip() for value in material_names
        if str(value).strip()
    ))
    if not requested:
        return tool_error(
            tool, "material_names cannot be empty",
            error_code="invalid_material_names",
        )
    results = []
    for label in requested:
        polymer_identity = thermo.resolve_polymer_identity(label)
        modeled_polymers = list(thermo.expand_polymer_identity(label))
        solvent_key = thermo.resolve_solvent(label)
        namespaces = []
        if polymer_identity is not None:
            namespaces.append("polymer_identity")
        if modeled_polymers:
            namespaces.append("thermodynamic_polymer")
        if solvent_key is not None:
            namespaces.append("thermodynamic_solvent")
        results.append({
            "requested_material": label,
            "namespace_memberships": namespaces,
            "polymer_identity": polymer_identity,
            "modeled_polymer_identities": modeled_polymers,
            "solvent_identity": (
                thermo.canonical_solvent_name(solvent_key)
                if solvent_key is not None else None
            ),
            "present_in_database": bool(namespaces),
        })
    return tool_success(
        tool,
        analysis_type="material_database_membership",
        requested_materials=requested,
        known_materials=[
            row["requested_material"] for row in results
            if row["present_in_database"]
        ],
        unknown_materials=[
            row["requested_material"] for row in results
            if not row["present_in_database"]
        ],
        results=results,
        warnings=[
            "Namespace membership identifies local records; it does not by "
            "itself establish a process condition or experimental validation.",
        ],
        model_basis="canonical polymer identity registry and admitted thermodynamic solvent aliases",
    )


def _full_feed_order_summary(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "solvent", "dissolution_temperature_c", "precipitation_proxy_order",
        "precipitation_proxy_crossings_c", "adjacent_proxy_windows_c",
        "crossing_states",
        "minimum_adjacent_proxy_window_c", "unresolved_at_temperature_min",
        "full_feed_proxy_order_resolved", "meets_full_feed_minimum_window",
        "boiling_point_c", "boiling_point_margin_c", "atmospheric_feasible",
        "dissolution_value_clipped",
        "crossing_method_by_polymer",
        "ghs_signal_word",
    )
    return {key: row[key] for key in keys if key in row}


def _threshold_crossing(
    polymer: str, solvent: str, lower: float, upper: float, threshold: float,
) -> dict[str, Any]:
    """Find the first capacity-threshold crossing encountered while cooling."""
    temperatures = _temperature_grid(lower, upper, 1.0, False)
    points = [
        (
            temperature,
            thermo.get_solubility_result(polymer, solvent, temperature),
        )
        for temperature in temperatures
    ]
    if not points or any(
        not evidence.get("available") for _, evidence in points
    ):
        return {
            "temperature_c": None,
            "status": "missing_solubility_evidence",
            "method": None,
        }
    numeric = [
        (temperature, float(evidence["solubility_pct"]))
        for temperature, evidence in points
    ]
    if numeric[-1][1] < threshold:
        return {
            "temperature_c": None,
            "status": "below_threshold_at_dissolution",
            "method": thermo.GRID_EXACT,
        }
    for index in range(len(numeric) - 1, 0, -1):
        low_t, low_value = numeric[index - 1]
        high_t, high_value = numeric[index]
        if low_value < threshold <= high_value:
            fraction = (threshold - low_value) / (high_value - low_value)
            return {
                "temperature_c": round(low_t + fraction * (high_t - low_t), 6),
                "status": "crossing_within_screen",
                "method": thermo.GRID_INTERPOLATION,
            }
    return {
        "temperature_c": None,
        "status": "remains_above_threshold_at_temperature_min",
        "method": thermo.GRID_EXACT,
    }


def _crossing_states(
    solvent: str,
    first_polymer: str,
    second_polymer: str,
    crossings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Evaluate both ordered polymers at each modeled crossing."""
    states: list[dict[str, Any]] = []
    for crossing_polymer in (first_polymer, second_polymer):
        temperature = crossings[crossing_polymer].get("temperature_c")
        if not isinstance(temperature, (int, float)):
            continue
        evidence = {
            polymer: thermo.get_solubility_result(
                polymer, solvent, float(temperature),
            )
            for polymer in (first_polymer, second_polymer)
        }
        if any(not result.get("available") for result in evidence.values()):
            continue
        solubilities = {
            polymer: round(float(result["solubility_pct"]), 6)
            for polymer, result in evidence.items()
        }
        numerator = solubilities[second_polymer]
        denominator = solubilities[first_polymer]
        states.append({
            "polymer": crossing_polymer,
            "temperature_c": float(temperature),
            "solubilities_wt_pct": solubilities,
            "selectivity_ratio": (
                None if denominator == 0 else round(numerator / denominator, 6)
            ),
            "selectivity_ratio_orientation": (
                f"{second_polymer}/{first_polymer}"
            ),
            "selectivity_gap_wt_pct": round(numerator - denominator, 6),
        })
    return states


def _crossing_model_warning(
    highest_temperature_c: float,
    *,
    recommended_on_grid_boundary: bool = False,
) -> str:
    """Describe the pair-specific basis without advertising a global boundary."""
    del highest_temperature_c
    if recommended_on_grid_boundary:
        return (
            "Recommended condition is on a pair-specific grid boundary; "
            "crossings are interpolated from the retained source grid and "
            "require experimental validation."
        )
    return (
        "Crossings use exact retained-grid nodes or between-node interpolation "
        "and require experimental validation."
    )


def _recommended_precipitation_condition(
    candidates: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Prefer disclosed below-flash evidence without treating unknown as safe."""
    if not candidates:
        return None
    leading = candidates[0]
    if leading.get("operating_at_or_above_flash_point") is True:
        disclosed_below_flash = next((
            item for item in candidates[1:]
            if item.get("flash_point_available") is True
            and item.get("operating_at_or_above_flash_point") is False
        ), None)
        if disclosed_below_flash is not None:
            return disclosed_below_flash
        leading["above_flash_point"] = True
    return leading


def _attach_precipitation_hazard_disclosure(
    recommendation: Optional[dict[str, Any]],
    qualifying_candidates: list[dict[str, Any]],
    *,
    top_k: int,
) -> Optional[dict[str, Any]]:
    """Surface GHS-Warning choices without changing thermodynamic order.

    The comparison is deliberately made against the full qualifying set,
    before the display ``top_k`` is applied.  An unknown GHS signal is not
    evidence of lower hazard and is therefore never promoted to a Warning.
    """
    from .safety import attach_lower_hazard_disclosure

    return attach_lower_hazard_disclosure(
        recommendation,
        qualifying_candidates,
        top_k=top_k,
        decision_metric="ordering_window_c",
        decision_value_key="ordering_window_c",
        decision_unit="C",
        legacy_value_key="ordering_window_c",
        legacy_cost_key="ordering_window_cost_c",
    )


_COOL_THEN_REHEAT_PROCESS_KIND = "cool_then_reheat_getter"
_OCTANE_GETTER_REFERENCE_ID = "octane_ldpe_pp_125_70_85"
_OCTANE_GETTER_REFERENCE_TEMPERATURES_C = {
    "dissolve": 125.0,
    "cool": 70.0,
    "reheat": 85.0,
}
_GETTER_DISPOSITION = "deliberately_not_recovered"


def _cycle_requested_roles(
    recovered_polymer: object,
    sacrificial_getter_polymer: object,
    getter_disposition: object,
) -> list[dict[str, Any]]:
    """Keep the caller's requested roles visible in typed cycle refusals."""
    return [
        {
            "polymer": str(recovered_polymer).strip(),
            "role": "recovered_polymer",
            "intended_terminal_disposition": "redissolved_product_stream",
        },
        {
            "polymer": str(sacrificial_getter_polymer).strip(),
            "role": "sacrificial_getter",
            "intended_terminal_disposition": (
                str(getter_disposition).strip()
                if getter_disposition is not None else None
            ),
        },
    ]


def _unsupported_cycle(
    *,
    message: str,
    recovered_polymer: object,
    sacrificial_getter_polymer: object,
    getter_disposition: object,
    solvent: object,
    temperatures: dict[str, Any],
    invalid_states: list[dict[str, Any]],
) -> str:
    """Return the only failure terminal for an unevaluable process cycle."""
    return tool_error(
        "screen_cool_then_reheat_getter",
        message,
        error_code="unsupported_process_cycle",
        process_kind=_COOL_THEN_REHEAT_PROCESS_KIND,
        requested_component_roles=_cycle_requested_roles(
            recovered_polymer,
            sacrificial_getter_polymer,
            getter_disposition,
        ),
        solvent=str(solvent).strip(),
        requested_temperatures_c=temperatures,
        missing_or_invalid_states=invalid_states,
        recommended_as_literature_getter=False,
        warnings=[
            "The requested cool-then-reheat cycle was not replaced with a single-ramp precipitation screen.",
        ],
    )


def _cycle_value_source(evidence: dict[str, Any]) -> dict[str, Any]:
    """Retain stored-point provenance without inventing a second seam."""
    return {
        key: evidence[key] for key in (
            "source_temperatures_c",
        ) if evidence.get(key) is not None
    }


def _getter_discovery_row(
    polymer: str,
    recovered: str,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Compact one catalog-search candidate without echoing a feed pair."""
    solubilities = {
        str(state.get("state_id") or ""): dict(state.get("solubilities_wt_pct") or {})
        for state in data.get("states") or []
        if isinstance(state, dict)
    }
    dissolve = solubilities.get("dissolve") or {}
    reheat = solubilities.get("reheat") or {}
    return {
        "polymer": polymer,
        "recommended_as_literature_getter": False,
        "recommended_by_capacity_proxy": bool(
            data.get("literature_selectivity_match")
        ),
        "dissolve_getter_wt_pct": dissolve.get(polymer),
        "reheat_getter_wt_pct": reheat.get(polymer),
        "dissolve_recovered_wt_pct": dissolve.get(recovered),
        "reheat_recovered_wt_pct": reheat.get(recovered),
        "dissolution_selectivity_gap_wt_pct": data.get(
            "dissolution_selectivity_gap_wt_pct"
        ),
        "getter_at_or_above_capacity_threshold_at_dissolve": data.get(
            "getter_at_or_above_capacity_threshold_at_dissolve"
        ),
        "getter_below_capacity_threshold_at_reheat": data.get(
            "getter_below_capacity_threshold_at_reheat"
        ),
    }


def _screen_getter_polymer_catalog(
    recovered_polymer: str,
    solvent: str,
    dissolution_temperature_c: Optional[float],
    cool_temperature_c: Optional[float],
    reheat_temperature_c: Optional[float],
    capacity_threshold_wt_pct: float,
    getter_disposition: Optional[str],
    reference_cycle_id: Optional[str],
) -> str:
    """Search the stored-grid polymer catalog for a sacrificial getter."""
    recovered = thermo.resolve_polymer(str(recovered_polymer))
    solvent_key = thermo.resolve_solvent(str(solvent))
    temperatures = {
        "dissolve": dissolution_temperature_c,
        "cool": cool_temperature_c,
        "reheat": reheat_temperature_c,
    }
    invalid_identities = []
    if recovered is None:
        invalid_identities.append({
            "state_id": "component_roles",
            "polymer": str(recovered_polymer).strip(),
            "reason": "recovered_polymer_not_modelable",
        })
    if solvent_key is None:
        invalid_identities.append({
            "state_id": "solvent",
            "solvent": str(solvent).strip(),
            "reason": "named_solvent_not_modelable",
        })
    if reference_cycle_id not in (None, ""):
        invalid_identities.append({
            "state_id": "reference_cycle",
            "reason": "polymer_catalog_search_cannot_pin_named_pair_reference",
            "reference_cycle_id": str(reference_cycle_id),
        })
    if invalid_identities:
        return _unsupported_cycle(
            message="The polymer-catalog getter search contains an unsupported identity.",
            recovered_polymer=recovered_polymer,
            sacrificial_getter_polymer="",
            getter_disposition=getter_disposition,
            solvent=solvent,
            temperatures=temperatures,
            invalid_states=invalid_identities,
        )
    assert recovered is not None and solvent_key is not None
    if getter_disposition != _GETTER_DISPOSITION:
        return _unsupported_cycle(
            message=(
                "A polymer-catalog getter search must explicitly designate "
                "the sacrificial getter as deliberately_not_recovered."
            ),
            recovered_polymer=recovered,
            sacrificial_getter_polymer="",
            getter_disposition=getter_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=[{
                "state_id": "component_roles",
                "reason": "missing_or_invalid_getter_disposition",
            }],
        )
    missing_states = [
        {"state_id": state_id, "reason": "temperature_required"}
        for state_id, value in temperatures.items() if value is None
    ]
    if missing_states:
        return _unsupported_cycle(
            message=(
                "All three thermal setpoints are required for a polymer-catalog "
                "getter search; the octane/LDPE/PP 85 C reheat is not a default."
            ),
            recovered_polymer=recovered,
            sacrificial_getter_polymer="",
            getter_disposition=getter_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=missing_states,
        )
    recommended: list[dict[str, Any]] = []
    evaluated_count = 0
    unavailable: list[dict[str, Any]] = []
    for polymer in sorted(thermo.get_available_polymers()):
        if polymer == recovered:
            continue
        envelope = parse_tool_result(screen_cool_then_reheat_getter(
            recovered,
            polymer,
            solvent,
            dissolution_temperature_c,
            cool_temperature_c,
            reheat_temperature_c,
            capacity_threshold_wt_pct,
            getter_disposition,
            None,
        ))
        data = envelope["data"]
        if not data.get("success"):
            unavailable.append({
                "polymer": polymer,
                "error_code": data.get("error_code"),
                "reason": next((
                    str(item.get("reason") or "")
                    for item in data.get("missing_or_invalid_states") or []
                    if isinstance(item, dict) and item.get("reason")
                ), data.get("error_code") or "unavailable"),
            })
            continue
        evaluated_count += 1
        row = _getter_discovery_row(polymer, recovered, data)
        if row["recommended_by_capacity_proxy"]:
            recommended.append(row)
    return tool_success(
        "screen_cool_then_reheat_getter",
        display=(
            "Polymer-catalog getter search for "
            f"{recovered} in {thermo.canonical_solvent_name(solvent_key)}: "
            + (
                ", ".join(item["polymer"] for item in recommended)
                if recommended else "no recommended getters"
            )
        ),
        analysis_type="cool_then_reheat_getter_discovery",
        process_kind=_COOL_THEN_REHEAT_PROCESS_KIND,
        getter_search_axis="stored_grid_polymer_catalog",
        cycle_evaluation_status=(
            "evaluated_recommended" if recommended else "evaluated_not_recommended"
        ),
        recovered_polymer=recovered,
        solvent=thermo.canonical_solvent_name(solvent_key),
        getter_disposition=_GETTER_DISPOSITION,
        requested_temperatures_c={
            "dissolve": float(dissolution_temperature_c),
            "cool": float(cool_temperature_c),
            "reheat": float(reheat_temperature_c),
        },
        capacity_threshold_wt_pct=float(capacity_threshold_wt_pct),
        recommended_as_literature_getter=False,
        recommended_getters=recommended,
        evaluated_getter_count=evaluated_count,
        unavailable_getter_count=len(unavailable),
        component_roles=[{
            "polymer": recovered,
            "role": "recovered_polymer",
            "intended_terminal_disposition": "redissolved_product_stream",
        }],
        recovery_model_status="not_modeled",
        mass_balance_status="not_quantified",
        limitations=[
            "phase_state_not_established_by_capacity_proxy",
            "cloud_points_not_modeled",
            "miscibility_and_phase_separation_not_modeled",
            "recovery_purity_yield_not_modeled",
            "mass_balance_not_quantified",
        ],
        warnings=[
            "This search covers the stored-grid polymer catalog for one named solvent "
            "and never searches the solvent catalog.",
            "Recommended getters are capacity-proxy hits, not literature getters "
            "or proven phase states.",
            "Miscibility and phase separation are not modeled.",
        ],
        model_basis=(
            "D3 grid-first solubility capacity at each named thermal state; "
            "the 1 wt% threshold is a disclosed proxy, not a phase boundary"
        ),
    )


def screen_cool_then_reheat_getter(
    recovered_polymer: str,
    sacrificial_getter_polymer: Optional[str] = None,
    solvent: Optional[str] = None,
    dissolution_temperature_c: Optional[float] = None,
    cool_temperature_c: Optional[float] = None,
    reheat_temperature_c: Optional[float] = None,
    capacity_threshold_wt_pct: float = 1.0,
    getter_disposition: Optional[str] = None,
    reference_cycle_id: Optional[str] = None,
) -> str:
    """Evaluate one named dissolve -> cool -> reheat cycle, or search polymers.

    This operation never searches or ranks the solvent catalog.  A named
    sacrificial getter evaluates that recovered/getter pair.  Omitting the
    getter searches the stored-grid polymer catalog for the named recovered
    polymer, named solvent, and three explicit setpoints.  The canonical
    octane/LDPE/PP reference may omit its three setpoints and getter
    disposition; a catalog search and every other identity must state all
    four explicitly.  Returned capacities come from the centralized D3
    grid-first thermodynamic seam and do not prove cloud points, phase
    fractions, recovery, purity, or a mass balance.
    """
    if not str(sacrificial_getter_polymer or "").strip():
        return _screen_getter_polymer_catalog(
            recovered_polymer,
            solvent,
            dissolution_temperature_c,
            cool_temperature_c,
            reheat_temperature_c,
            capacity_threshold_wt_pct,
            getter_disposition,
            reference_cycle_id,
        )
    tool = "screen_cool_then_reheat_getter"
    supplied_temperatures = {
        "dissolve": dissolution_temperature_c,
        "cool": cool_temperature_c,
        "reheat": reheat_temperature_c,
    }
    recovered = thermo.resolve_polymer(str(recovered_polymer))
    getter = thermo.resolve_polymer(str(sacrificial_getter_polymer))
    solvent_key = thermo.resolve_solvent(str(solvent))
    invalid_identities = []
    if recovered is None:
        invalid_identities.append({
            "state_id": "component_roles",
            "polymer": str(recovered_polymer).strip(),
            "reason": "recovered_polymer_not_modelable",
        })
    if getter is None:
        invalid_identities.append({
            "state_id": "component_roles",
            "polymer": str(sacrificial_getter_polymer).strip(),
            "reason": "sacrificial_getter_polymer_not_modelable",
        })
    if recovered is not None and recovered == getter:
        invalid_identities.append({
            "state_id": "component_roles",
            "reason": "recovered_and_getter_roles_must_be_distinct",
        })
    if solvent_key is None:
        invalid_identities.append({
            "state_id": "solvent",
            "solvent": str(solvent).strip(),
            "reason": "named_solvent_not_modelable",
        })
    if invalid_identities:
        return _unsupported_cycle(
            message="The named cycle contains an unsupported or ambiguous component identity.",
            recovered_polymer=recovered_polymer,
            sacrificial_getter_polymer=sacrificial_getter_polymer,
            getter_disposition=getter_disposition,
            solvent=solvent,
            temperatures=supplied_temperatures,
            invalid_states=invalid_identities,
        )
    assert recovered is not None and getter is not None and solvent_key is not None

    reference_identity = (
        recovered == "LDPE" and getter == "PP" and solvent_key == "octane"
    )
    if reference_cycle_id not in (None, _OCTANE_GETTER_REFERENCE_ID):
        return _unsupported_cycle(
            message="The requested reference cycle identity is not registered.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=getter_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=supplied_temperatures,
            invalid_states=[{
                "state_id": "reference_cycle",
                "reason": "unregistered_reference_cycle_id",
                "reference_cycle_id": str(reference_cycle_id),
            }],
        )
    if reference_cycle_id == _OCTANE_GETTER_REFERENCE_ID and not reference_identity:
        return _unsupported_cycle(
            message="The registered reference cycle does not match the requested components.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=getter_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=supplied_temperatures,
            invalid_states=[{
                "state_id": "reference_cycle",
                "reason": "reference_cycle_component_mismatch",
                "reference_cycle_id": _OCTANE_GETTER_REFERENCE_ID,
            }],
        )

    effective_disposition = getter_disposition
    if effective_disposition is None and reference_identity:
        effective_disposition = _GETTER_DISPOSITION
    if effective_disposition != _GETTER_DISPOSITION:
        return _unsupported_cycle(
            message=(
                "A generic cycle must explicitly designate the sacrificial getter "
                "as deliberately_not_recovered."
            ),
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=getter_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=supplied_temperatures,
            invalid_states=[{
                "state_id": "component_roles",
                "reason": "missing_or_invalid_getter_disposition",
            }],
        )

    effective_temperatures = dict(supplied_temperatures)
    if reference_identity:
        for state_id, default in _OCTANE_GETTER_REFERENCE_TEMPERATURES_C.items():
            if effective_temperatures[state_id] is None:
                effective_temperatures[state_id] = default
    missing_states = [
        {"state_id": state_id, "reason": "temperature_required"}
        for state_id, value in effective_temperatures.items() if value is None
    ]
    if missing_states:
        return _unsupported_cycle(
            message="All three thermal setpoints are required for this non-reference cycle.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=effective_temperatures,
            invalid_states=missing_states,
        )
    try:
        temperatures = {
            state_id: float(value)
            for state_id, value in effective_temperatures.items()
        }
        threshold = float(capacity_threshold_wt_pct)
    except (TypeError, ValueError):
        return _unsupported_cycle(
            message="Cycle temperatures and the capacity threshold must be numeric.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=effective_temperatures,
            invalid_states=[{
                "state_id": "cycle_parameters",
                "reason": "nonnumeric_temperature_or_threshold",
            }],
        )
    if not all(math.isfinite(value) for value in (*temperatures.values(), threshold)):
        return _unsupported_cycle(
            message="Cycle temperatures and the capacity threshold must be finite.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=[{
                "state_id": "cycle_parameters",
                "reason": "nonfinite_temperature_or_threshold",
            }],
        )
    if threshold <= 0:
        return _unsupported_cycle(
            message="The capacity threshold must be positive.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=[{
                "state_id": "cycle_parameters",
                "reason": "nonpositive_capacity_threshold",
            }],
        )
    if not (
        temperatures["cool"] < temperatures["reheat"]
        <= temperatures["dissolve"]
    ):
        return _unsupported_cycle(
            message=(
                "A cool-then-reheat cycle requires cool_temperature_c < "
                "reheat_temperature_c <= dissolution_temperature_c."
            ),
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=[{
                "state_id": "thermal_order",
                "reason": "invalid_cool_reheat_dissolution_order",
            }],
        )
    if (
        reference_cycle_id == _OCTANE_GETTER_REFERENCE_ID
        and temperatures != _OCTANE_GETTER_REFERENCE_TEMPERATURES_C
    ):
        return _unsupported_cycle(
            message="The registered reference cycle requires its 125 -> 70 -> 85 C setpoints.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=[{
                "state_id": "reference_cycle",
                "reason": "reference_cycle_setpoint_mismatch",
                "reference_cycle_id": _OCTANE_GETTER_REFERENCE_ID,
            }],
        )

    evidence_by_state: dict[str, dict[str, dict[str, Any]]] = {}
    unavailable: list[dict[str, Any]] = []
    for state_id in ("dissolve", "cool", "reheat"):
        evidence_by_state[state_id] = {}
        for polymer in (recovered, getter):
            evidence = thermo.get_solubility_result(
                polymer, solvent_key, temperatures[state_id],
            )
            evidence_by_state[state_id][polymer] = evidence
            if not evidence.get("available"):
                unavailable.append({
                    "state_id": state_id,
                    "polymer": polymer,
                    "temperature_c": temperatures[state_id],
                    "reason": evidence.get("unavailable_reason") or "value_unavailable",
                })
    if unavailable:
        return _unsupported_cycle(
            message="The D3 thermodynamic seam cannot evaluate every requested cycle state.",
            recovered_polymer=recovered,
            sacrificial_getter_polymer=getter,
            getter_disposition=effective_disposition,
            solvent=thermo.canonical_solvent_name(solvent_key),
            temperatures=temperatures,
            invalid_states=unavailable,
        )

    values = {
        state_id: {
            polymer: float(evidence_by_state[state_id][polymer]["solubility_pct"])
            for polymer in (recovered, getter)
        }
        for state_id in ("dissolve", "cool", "reheat")
    }
    dissolution_gap = values["dissolve"][recovered] - values["dissolve"][getter]
    reheat_gap = values["reheat"][recovered] - values["reheat"][getter]
    dissolution_match = dissolution_gap > 0
    getter_dissolved = values["dissolve"][getter] >= threshold
    getter_below_threshold = values["reheat"][getter] < threshold
    reheat_match = reheat_gap > 0 and getter_below_threshold
    literature_match = (
        dissolution_match and getter_dissolved and reheat_match
    )
    mismatch_stage = (
        None if literature_match else
        "dissolve" if not dissolution_match or not getter_dissolved else "reheat"
    )
    ratio = (
        None if values["reheat"][recovered] == 0 else
        values["reheat"][getter] / values["reheat"][recovered]
    )
    reheat_interpretation = (
        "direction_supported_not_phase_state_proven"
        if reheat_match else
        "reheat_direction_contradicted"
        if reheat_gap <= 0 else
        "getter_not_below_threshold_phase_state_not_established"
    )

    state_specs = (
        (
            "dissolve", 1, "dissolution_hold",
            "preferentially_dissolve_recovered_polymer",
            (
                "getter_below_capacity_threshold_not_in_solution"
                if not getter_dissolved else
                "direction_supported_not_recovery_proven"
                if dissolution_match else "dissolution_direction_contradicted"
            ),
        ),
        (
            "cool", 2, "cooled_hold", "precipitate_both_polymers",
            "phase_state_not_established",
        ),
        (
            "reheat", 3, "reheat_hold",
            "redissolve_recovered_while_getter_remains_solid",
            reheat_interpretation,
        ),
    )
    states = []
    for state_id, sequence, state_kind, requested_intent, interpretation in state_specs:
        states.append({
            "state_id": state_id,
            "sequence": sequence,
            "state_kind": state_kind,
            "temperature_c": temperatures[state_id],
            "requested_material_intent": requested_intent,
            "solubilities_wt_pct": values[state_id],
            "value_source_by_polymer": {
                polymer: _cycle_value_source(evidence_by_state[state_id][polymer])
                for polymer in (recovered, getter)
            },
            "recovered_minus_getter_wt_pct": round(
                values[state_id][recovered] - values[state_id][getter], 8,
            ),
            "capacity_threshold_state": {
                polymer: (
                    "below" if value < threshold else "at_or_above"
                )
                for polymer, value in values[state_id].items()
            },
            "capacity_proxy_interpretation": interpretation,
        })

    cool_changes = {
        polymer: round(
            values["cool"][polymer] - values["dissolve"][polymer], 8,
        ) for polymer in (recovered, getter)
    }
    reheat_changes = {
        polymer: round(
            values["reheat"][polymer] - values["cool"][polymer], 8,
        ) for polymer in (recovered, getter)
    }
    cool_evaluation = (
        "both_capacities_fall_phase_state_not_established"
        if all(change < 0 for change in cool_changes.values())
        else "capacity_changes_reported_phase_state_not_established"
    )
    if reheat_match:
        reheat_evaluation = (
            "requested_reheat_direction_supported_not_phase_state_proven"
        )
    elif reheat_gap <= 0:
        reheat_evaluation = (
            "getter_capacity_rises_more_requested_direction_contradicted"
            if reheat_changes[getter] > reheat_changes[recovered]
            else "getter_capacity_at_or_above_recovered_direction_contradicted"
        )
    else:
        reheat_evaluation = (
            "getter_not_below_threshold_requested_phase_state_not_established"
        )
    transitions = [
        {
            "transition_id": "establish_dissolution",
            "from_state_id": "feed",
            "to_state_id": "dissolve",
            "operation": "dissolve_hold",
            "capacity_change_wt_pct": None,
            "evaluation": (
                "getter_below_threshold_not_in_solution"
                if not getter_dissolved else
                "recovered_polymer_direction_supported"
                if dissolution_match else "requested_dissolution_direction_contradicted"
            ),
        },
        {
            "transition_id": "cool_both",
            "from_state_id": "dissolve",
            "to_state_id": "cool",
            "operation": "cool",
            "capacity_change_wt_pct": cool_changes,
            "evaluation": cool_evaluation,
        },
        {
            "transition_id": "selective_reheat",
            "from_state_id": "cool",
            "to_state_id": "reheat",
            "operation": "reheat",
            "capacity_change_wt_pct": reheat_changes,
            "evaluation": reheat_evaluation,
        },
        {
            "transition_id": "filter_getter",
            "from_state_id": "reheat",
            "to_state_id": "terminal_dispositions",
            "operation": "filter",
            "capacity_change_wt_pct": None,
            "evaluation": (
                "represented_and_recommended"
                if literature_match else "represented_not_recommended"
            ),
        },
    ]
    component_roles = [
        {
            "polymer": recovered,
            "role": "recovered_polymer",
            "intended_terminal_disposition": "redissolved_product_stream",
        },
        {
            "polymer": getter,
            "role": "sacrificial_getter",
            "intended_terminal_disposition": _GETTER_DISPOSITION,
            "counts_as_target_recovery_loss": False,
            "material_accounting": "tracked_sacrificial_process_aid",
        },
    ]
    reference_temperatures_match = temperatures == (
        _OCTANE_GETTER_REFERENCE_TEMPERATURES_C
    )
    return tool_success(
        tool,
        analysis_type="cool_then_reheat_getter_cycle",
        process_kind=_COOL_THEN_REHEAT_PROCESS_KIND,
        cycle_evaluation_status=(
            "evaluated_recommended" if literature_match
            else "evaluated_not_recommended"
        ),
        reference_cycle_id=(
            _OCTANE_GETTER_REFERENCE_ID
            if reference_identity and reference_temperatures_match else None
        ),
        recovered_polymer=recovered,
        sacrificial_getter_polymer=getter,
        solvent=thermo.canonical_solvent_name(solvent_key),
        getter_disposition=_GETTER_DISPOSITION,
        component_roles=component_roles,
        states=states,
        transitions=transitions,
        decision_basis="stored_grid_values",
        capacity_proxy_basis="wt_pct_solution_concentration",
        capacity_threshold_wt_pct=threshold,
        dissolution_direction_match=dissolution_match,
        dissolution_selectivity_gap_wt_pct=round(dissolution_gap, 8),
        cool_both_phase_status="not_established_by_capacity_proxy",
        reheat_selectivity_match=reheat_match,
        reheat_selectivity_gap_wt_pct=round(reheat_gap, 8),
        getter_to_recovered_capacity_ratio_at_reheat=(
            None if ratio is None else round(ratio, 9)
        ),
        getter_at_or_above_capacity_threshold_at_dissolve=getter_dissolved,
        getter_below_capacity_threshold_at_reheat=getter_below_threshold,
        literature_selectivity_match=literature_match,
        recommended_as_literature_getter=literature_match,
        mismatch_stage_id=mismatch_stage,
        recovery_model_status="not_modeled",
        mass_balance_status="not_quantified",
        limitations=[
            "phase_state_not_established_by_capacity_proxy",
            "cloud_points_not_modeled",
            "miscibility_and_phase_separation_not_modeled",
            "recovery_purity_yield_not_modeled",
            "mass_balance_not_quantified",
            "grade_molecular_weight_transfer_requires_validation",
        ],
        warnings=[
            "Stored-grid solution capacities are not cloud points or observed phase states.",
            "The cycle does not calculate phase fractions, recovery, purity, yield, or a mass balance.",
            "Polymer grade, molecular weight, kinetics, filtration, and pigment capture require experimental validation.",
        ],
        model_basis=(
            "D3 grid-first solubility capacity at each named thermal state; "
            "the 1 wt% threshold is a disclosed proxy, not a phase boundary"
        ),
    )


def screen_precipitation_order(
    feed_polymers: list[str],
    first_polymer: str,
    second_polymer: str,
    solvents: Optional[list[str]] = None,
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
    precipitation_threshold_wt_pct: float = 1.0,
    min_dissolution_solubility_wt_pct: float = 5.0,
    require_atmospheric: Optional[bool] = None,
    min_ordering_window_c: float = 0.0,
    min_recovery_window_c: float = 0.0,
    max_first_precipitation_temperature_c: Optional[float] = None,
    top_k: int = 5,
    include_full_feed_order: bool = False,
    include_pubchem: bool = False,
) -> str:
    """Screen a user-named polymer pair across the stored-grid solvent catalog.

    This tool searches solvents, not polymers.  It requires a user-named
    polymer pair and cannot invent a second polymer.  A question that names
    one polymer and one solvent and asks for another polymer must use
    ``screen_cool_then_reheat_getter`` with the getter omitted.

    ``include_pubchem`` is opt-in for a current request that explicitly asks
    for flash-point or safety evidence.  Ordinary screens remain offline and
    disclose that flash evidence is unavailable rather than varying with
    network health.  Live enrichment is winner-only, so below-flash
    alternative demotion remains inactive until candidate-wide offline flash
    coverage is admitted; unavailable flash data are never treated as safe.

    ``min_ordering_window_c`` is a disclosed selectivity filter, not an
    intrinsic physical cutoff.  Use a nonzero value only when the user named
    that window or when the answer will disclose the applied threshold.  For
    an alternatives follow-up such as "what else" or "a different solvent",
    pass 0 unless the user explicitly named an ordering window.

    ``min_recovery_window_c`` and ``max_first_precipitation_temperature_c``
    are disclosed recovery-window filters, not measured process windows.
    ``recovery_window_c`` is dissolution temperature minus the first
    precipitation proxy.  A nonzero recovery floor or a maximum first-crossing
    temperature excludes candidates only when a query asks.

    Multi-polymer catalog provenance names only polymers whose actual
    pair-screen count at the highest screened temperature is below 90% of the
    stored-grid solvent catalog.

    ``require_atmospheric=None`` keeps missing boiling-point data while
    excluding known-too-low conditions; ``True`` excludes both and ``False``
    excludes neither.
    """
    tool = "screen_precipitation_order"
    names, unsupported = _feed_names(feed_polymers)
    first = thermo.resolve_polymer(str(first_polymer))
    second = thermo.resolve_polymer(str(second_polymer))
    if unsupported or first not in names or second not in names or first == second:
        return tool_error(
            tool,
            "The feed and two distinct ordered polymers must all be supported.",
            error_code="invalid_precipitation_order",
            unsupported_polymers=unsupported,
        )
    try:
        # A domain referent is never invented by a literal default: an
        # absent bound resolves to the engine's stored-grid window.
        lower = float(
            thermo.FITTED_TEMP_MIN_C
            if temperature_min_c is None else temperature_min_c
        )
        upper = float(
            thermo.FITTED_TEMP_MAX_C
            if temperature_max_c is None else temperature_max_c
        )
        threshold = float(precipitation_threshold_wt_pct)
        minimum_dissolution = float(min_dissolution_solubility_wt_pct)
        minimum_window = float(min_ordering_window_c)
        recovery_floor = float(min_recovery_window_c)
        max_first = (
            None if max_first_precipitation_temperature_c is None
            else float(max_first_precipitation_temperature_c)
        )
        limit = max(1, min(int(top_k), 10))
    except (TypeError, ValueError):
        return tool_error(tool, "Precipitation screen inputs must be numeric.", error_code="invalid_numeric_input")
    numeric = [lower, upper, threshold, minimum_dissolution, minimum_window, recovery_floor]
    if max_first is not None:
        numeric.append(max_first)
    if not all(math.isfinite(value) for value in numeric) or upper < lower:
        return tool_error(tool, "Temperature bounds and thresholds must be finite and ordered.", error_code="invalid_numeric_input")
    if threshold <= 0 or minimum_dissolution <= threshold or minimum_window < 0 or recovery_floor < 0:
        return tool_error(
            tool,
            "Use a positive precipitation proxy below the dissolution threshold and nonnegative ordering and recovery windows.",
            error_code="invalid_precipitation_threshold",
        )
    available_solvents = thermo.get_available_solvents()
    requested_solvents = []
    for supplied in solvents or sorted(available_solvents):
        resolved = thermo.resolve_solvent(str(supplied))
        if resolved is None:
            return _solvent_resolution_error(tool, str(supplied))
        if resolved not in requested_solvents:
            requested_solvents.append(resolved)
    solvent_universe = {
        "kind": "stored_grid_thermodynamic_catalog",
        "modelable_solvent_count": len(available_solvents),
        "modelable_solvents": sorted(
            thermo.canonical_solvent_name(item) for item in available_solvents
        ),
        "screened_entire_modelable_universe": (
            set(requested_solvents) == available_solvents
        ),
        "property_record_count": thermo.get_property_solvent_record_count(),
        "property_records_are_solubility_models": False,
        "screened_entire_property_catalog": False,
    }
    temperatures = _temperature_grid(lower, upper, 5.0, bool(strict_maximum))
    if not temperatures:
        return tool_error(tool, "No temperatures remain inside the requested bounds.", error_code="empty_temperature_grid")
    highest_supported_temperature = min(
        max(temperatures), thermo.SENSITIVITY_EXTRAPOLATION_MAX_C,
    )
    catalog_provenance = _screen_catalog_provenance(
        names, highest_supported_temperature,
    )
    recovery_filter_active = recovery_floor > 0 or max_first is not None

    candidates: list[dict[str, Any]] = []
    atmospheric_exclusions = _atmospheric_exclusion_counts()
    excluded_for_dissolution = excluded_for_missing_model = 0
    excluded_for_crossing = 0
    missing_model_details: list[dict[str, Any]] = []
    for solvent in requested_solvents:
        boiling_point = thermo.get_boiling_point(solvent)
        if all(
            _atmospheric_exclusion_applies(
                _atmospheric_exclusion_reason(boiling_point, temperature),
                require_atmospheric,
            )
            for temperature in temperatures
        ):
            reason = _atmospheric_exclusion_reason(
                boiling_point, temperatures[0],
            )
            assert reason is not None
            atmospheric_exclusions[reason] += 1
            continue
        missing_polymers = [
            polymer for polymer in names
            if not thermo.has_solubility_pair(polymer, solvent)
        ]
        if missing_polymers:
            excluded_for_missing_model += 1
            missing_model_details.append({
                "solvent": thermo.canonical_solvent_name(solvent),
                "missing_polymers": missing_polymers,
            })
            continue
        dissolution = None
        qualifying_atmospheric_exclusion: Optional[str] = None
        dissolution_values: dict[str, float] = {}
        dissolution_source_grid_ranges: dict[str, list[float] | None] = {}
        for temperature in temperatures:
            atmospheric_exclusion = _atmospheric_exclusion_reason(
                boiling_point, temperature,
            )
            evidence = {
                polymer: thermo.get_solubility_result(
                    polymer, solvent, temperature,
                )
                for polymer in names
            }
            if any(not result.get("available") for result in evidence.values()):
                continue
            values = {
                polymer: float(result["solubility_pct"])
                for polymer, result in evidence.items()
            }
            if all(float(value) >= minimum_dissolution for value in values.values()):
                if _atmospheric_exclusion_applies(
                    atmospheric_exclusion, require_atmospheric,
                ):
                    assert atmospheric_exclusion is not None
                    qualifying_atmospheric_exclusion = atmospheric_exclusion
                    continue
                dissolution = temperature
                dissolution_values = {
                    polymer: float(value) for polymer, value in values.items()
                }
                dissolution_source_grid_ranges = {
                    polymer: result.get("source_grid_temperature_range_c")
                    for polymer, result in evidence.items()
                }
                break
        if dissolution is None:
            if qualifying_atmospheric_exclusion is not None:
                atmospheric_exclusions[qualifying_atmospheric_exclusion] += 1
            else:
                excluded_for_dissolution += 1
            continue
        crossings = {
            polymer: _threshold_crossing(
                polymer, solvent, max(lower, thermo.FITTED_TEMP_MIN_C),
                dissolution, threshold,
            )
            for polymer in names
        }
        first_crossing = crossings[first]["temperature_c"]
        second_crossing = crossings[second]["temperature_c"]
        if first_crossing is None or second_crossing is None:
            excluded_for_crossing += 1
            continue
        ordering_window = float(first_crossing) - float(second_crossing)
        direction_matches = ordering_window > 0
        ordered_crossings = sorted(
            (
                (polymer, float(crossing["temperature_c"]))
                for polymer, crossing in crossings.items()
                if crossing["temperature_c"] is not None
            ),
            key=lambda item: (-item[1], item[0]),
        )
        adjacent_windows = {
            f"{first_item[0]}->{second_item[0]}": round(
                first_item[1] - second_item[1], 6,
            )
            for first_item, second_item in zip(
                ordered_crossings, ordered_crossings[1:]
            )
        }
        unresolved = [
            polymer for polymer in names
            if crossings[polymer]["temperature_c"] is None
        ]
        minimum_adjacent_window = min(
            adjacent_windows.values(), default=None,
        )
        full_feed_resolved = not unresolved and len(ordered_crossings) == len(names)
        recovery_window = round(float(dissolution) - float(first_crossing), 6)
        meets_recovery_window = (
            not recovery_filter_active
            or (
                recovery_window >= recovery_floor
                and (max_first is None or float(first_crossing) <= max_first)
            )
        )
        row = {
            "solvent": thermo.canonical_solvent_name(solvent),
            "dissolution_temperature_c": dissolution,
            "dissolution_solubilities_wt_pct": dissolution_values,
            "dissolution_on_source_grid_boundary": bool(
                dissolution_source_grid_ranges
                and all(
                    pair_range is not None
                    and math.isclose(
                        float(dissolution), float(pair_range[1]),
                        rel_tol=0.0, abs_tol=1e-9,
                    )
                    for pair_range in dissolution_source_grid_ranges.values()
                )
            ),
            "first_polymer": first,
            "first_precipitation_proxy_c": first_crossing,
            "second_polymer": second,
            "second_precipitation_proxy_c": second_crossing,
            "ordering_window_c": round(ordering_window, 6),
            "ordering_window_magnitude_c": round(abs(ordering_window), 6),
            "ordering_matches_request": direction_matches,
            "meets_minimum_ordering_window": (
                direction_matches and ordering_window >= minimum_window
            ),
            "recovery_window_c": recovery_window,
            "meets_recovery_window": meets_recovery_window,
            "crossing_temperatures_c": sorted((first_crossing, second_crossing)),
            "other_polymer_crossings": {
                polymer: crossings[polymer]
                for polymer in names if polymer not in {first, second}
            },
            "crossing_method_by_polymer": {
                polymer: crossing.get("method")
                for polymer, crossing in crossings.items()
                if crossing.get("method") is not None
            },
            "boiling_point_c": boiling_point,
            "boiling_point_margin_c": (
                None if boiling_point is None else round(boiling_point - dissolution, 6)
            ),
            "atmospheric_feasible": (
                None if boiling_point is None else dissolution < boiling_point
            ),
            "crossing_states": _crossing_states(
                solvent, first, second, crossings,
            ),
            "dissolution_value_clipped": any(
                value >= 100.0 for value in dissolution_values.values()
            ),
            **thermo.get_solvent_hazard_framing(solvent),
        }
        if include_full_feed_order:
            row.update({
                "precipitation_proxy_order": [item[0] for item in ordered_crossings],
                "precipitation_proxy_crossings_c": {
                    polymer: crossing["temperature_c"]
                    for polymer, crossing in crossings.items()
                },
                "adjacent_proxy_windows_c": adjacent_windows,
                "minimum_adjacent_proxy_window_c": minimum_adjacent_window,
                "unresolved_at_temperature_min": unresolved,
                "full_feed_proxy_order_resolved": full_feed_resolved,
                "meets_full_feed_minimum_window": bool(
                    full_feed_resolved
                    and minimum_adjacent_window is not None
                    and minimum_adjacent_window >= minimum_window
                ),
            })
        candidates.append(row)
    ranked = sorted(candidates, key=lambda item: (
        not item["meets_minimum_ordering_window"],
        -float(item["ordering_window_c"]),
        bool(item["dissolution_value_clipped"]),
        float(item["dissolution_temperature_c"]),
        -float(item["boiling_point_margin_c"] or -math.inf),
        str(item["solvent"]),
    ))
    matching = [
        item for item in ranked
        if item["meets_minimum_ordering_window"] and item["meets_recovery_window"]
    ]
    narrow = sorted(
        (
            item for item in candidates
            if item["ordering_matches_request"]
            and not item["meets_minimum_ordering_window"]
        ),
        key=lambda item: (-float(item["ordering_window_c"]), str(item["solvent"])),
    )
    opposite = sorted(
        (item for item in candidates if not item["ordering_matches_request"]),
        key=lambda item: (float(item["ordering_window_c"]), str(item["solvent"])),
    )
    selected = matching[:limit]
    from .safety import (
        condition_operability, merge_condition_operability,
        recommended_condition_operability,
    )

    for item in selected:
        merge_condition_operability(
            item,
            condition_operability(
                item["solvent"], item["dissolution_temperature_c"],
            ),
        )
    if selected and include_pubchem:
        merge_condition_operability(
            selected[0],
            recommended_condition_operability(
                selected[0]["solvent"],
                selected[0]["dissolution_temperature_c"],
                include_pubchem=True,
            ),
        )
    recommended_condition = _attach_precipitation_hazard_disclosure(
        _recommended_precipitation_condition(selected),
        matching,
        top_k=limit,
    )
    full_feed_candidates = sorted(
        (item for item in candidates if item["meets_full_feed_minimum_window"]),
        key=lambda item: (
            -float(item["minimum_adjacent_proxy_window_c"]),
            bool(item["dissolution_value_clipped"]),
            -float(item["boiling_point_margin_c"] or -math.inf),
            str(item["solvent"]),
        ),
    ) if include_full_feed_order else []
    partial_full_feed = sorted(
        (item for item in candidates if not item["meets_full_feed_minimum_window"]),
        key=lambda item: (
            len(item["unresolved_at_temperature_min"]),
            -float(item["minimum_adjacent_proxy_window_c"] or -math.inf),
            str(item["solvent"]),
        ),
    ) if include_full_feed_order else []
    display_rows = selected or narrow[:limit] or opposite[:limit]
    display_rows_text = [
        f"{item['solvent']}: dissolve {item['dissolution_temperature_c']:g} C; "
        f"{first} proxy {item['first_precipitation_proxy_c']:.2f} C; "
        f"{second} proxy {item['second_precipitation_proxy_c']:.2f} C; "
        f"window {item['ordering_window_c']:.2f} C"
        + (
            " [at or above flash point; conditional option]"
            if item.get("operating_at_or_above_flash_point") is True
            else ""
        )
        for item in display_rows
    ]
    display_rows_text.append(
        "Ordering-window filter: "
        f"{sum(1 for item in candidates if item['meets_minimum_ordering_window'])} "
        "requested-direction candidate(s) met "
        f"min_ordering_window_c={minimum_window:g} C; "
        f"{len(narrow)} additional requested-direction candidate(s) were "
        "excluded solely by this threshold."
    )
    excluded_recovery = sum(
        1 for item in candidates if not item["meets_recovery_window"]
    )
    if recovery_filter_active:
        display_rows_text.append(
            "Recovery-window filter: "
            f"{sum(1 for item in candidates if item['meets_recovery_window'])} "
            f"candidate(s) met the requested recovery bound; "
            f"{excluded_recovery} candidate(s) were excluded by "
            "min_recovery_window_c / max_first_precipitation_temperature_c."
        )
    display = "\n".join(display_rows_text)
    return tool_success(
        tool,
        display=display,
        analysis_type="precipitation_order_screen",
        polymers=names,
        first_polymer=first,
        second_polymer=second,
        temperature_min_c=max(lower, thermo.FITTED_TEMP_MIN_C),
        temperature_max_c=min(upper, thermo.SENSITIVITY_EXTRAPOLATION_MAX_C),
        strict_maximum=bool(strict_maximum),
        precipitation_threshold_wt_pct=threshold,
        min_dissolution_solubility_wt_pct=minimum_dissolution,
        min_ordering_window_c=minimum_window,
        ordering_window_threshold_c=minimum_window,
        min_recovery_window_c=recovery_floor,
        **({} if max_first is None else {
            "max_first_precipitation_temperature_c": max_first,
        }),
        require_atmospheric=require_atmospheric,
        ordering_definition=(
            "On cooling, the polymer with the higher modeled capacity-threshold "
            "crossing is the first precipitation proxy."
        ),
        candidate_solvents=selected,
        candidate_solvent_count=len(matching),
        narrow_order_examples=narrow[:limit],
        narrow_order_example_count=len(narrow),
        opposite_order_examples=[] if selected else opposite[:limit],
        opposite_order_example_count=len(opposite),
        no_matching_order=not bool(selected),
        requested_direction_found=bool(matching or narrow),
        include_full_feed_order=bool(include_full_feed_order),
        full_feed_proxy_order_candidates=[
            _full_feed_order_summary(item) for item in full_feed_candidates[:limit]
        ],
        full_feed_proxy_order_candidate_count=len(full_feed_candidates),
        partial_full_feed_proxy_order_examples=[
            _full_feed_order_summary(item) for item in partial_full_feed[:limit]
        ],
        best_full_feed_proxy_order_condition=(
            _full_feed_order_summary(full_feed_candidates[0])
            if full_feed_candidates else None
        ),
        evaluated_candidate_count=len(candidates),
        screened_solvent_count=len(requested_solvents),
        solvent_universe=solvent_universe,
        **atmospheric_exclusions,
        excluded_for_dissolution=excluded_for_dissolution,
        excluded_for_missing_model=excluded_for_missing_model,
        excluded_for_missing_model_details=missing_model_details,
        excluded_for_crossing=excluded_for_crossing,
        excluded_for_ordering_window=len(narrow),
        excluded_for_recovery_window=excluded_recovery,
        solvent_catalog_provenance=catalog_provenance,
        recommended_condition=recommended_condition,
        warnings=[
            "The capacity threshold is a grade/MW-dependent loading proxy, not a measured cloud point or validated precipitation recovery, and does not predict cloud-point ordering.",
            "The dissolution threshold is a solution-capacity screen, not proof that the feed dissolves at a practical solvent-to-solid ratio or residence time.",
            _crossing_model_warning(
                highest_supported_temperature,
                recommended_on_grid_boundary=bool(
                    recommended_condition
                    and recommended_condition.get(
                        "dissolution_on_source_grid_boundary"
                    )
                ),
            ),
            (
                "NBP margin is operability, not safety; the recommended condition "
                "is at or above its flash point and remains a conditional option."
                if recommended_condition
                and recommended_condition.get("above_flash_point") is True
                else (
                    "NBP margin is operability, not safety; flash evidence is "
                    "attached for the recommended condition."
                    if recommended_condition
                    and recommended_condition.get("flash_point_available") is True
                    else "NBP margin is operability, not safety; local flash point was not checked."
                )
            ),
        ],
        model_basis=(
            "pair-specific stored-grid values plus between-node threshold "
            "crossing interpolation; shared wt% precipitation proxy"
        ),
    )


def plan_multistage_separation(
    feed_polymers: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
    temperature_step_c: float = 5.0,
    require_atmospheric: Optional[bool] = None,
    min_target_solubility_pct: float = 5.0,
    min_selectivity_pct: float = 5.0,
    top_k_routes: int = 5,
    feed_mass_fractions: Optional[dict[str, float]] = None,
) -> str:
    """Recursively rank complete or explicitly partial routes for any supported feed.

    The tri-state atmospheric policy is passed unchanged to every subset
    screen: ``None`` keeps unknown BP and excludes known-too-low conditions,
    ``True`` excludes both, and ``False`` excludes neither.
    """
    tool = "plan_multistage_separation"
    supplied_min = temperature_min_c is not None
    supplied_max = temperature_max_c is not None
    if not isinstance(feed_polymers, (list, tuple)):
        return tool_error(
            tool, "feed_polymers must be a list.", error_code="invalid_feed_polymers"
        )
    names, unsupported = _feed_names(feed_polymers)
    if unsupported:
        return tool_error(
            tool, "Unsupported feed polymer(s): " + ", ".join(unsupported),
            error_code="unknown_polymer", unsupported_polymers=unsupported,
            available_polymers=sorted(thermo.get_available_polymers()),
        )
    if len(names) < 2:
        return tool_error(
            tool, "A route requires at least two distinct polymers.",
            error_code="insufficient_feed_polymers", polymers=names,
        )
    try:
        composition = normalize_feed_composition(feed_mass_fractions, names)
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_feed_composition")
    numeric: dict[str, Any] = {
        "temperature_step_c": temperature_step_c,
        "min_target_solubility_pct": min_target_solubility_pct,
        "min_selectivity_pct": min_selectivity_pct,
    }
    if temperature_min_c is not None:
        numeric["temperature_min_c"] = temperature_min_c
    if temperature_max_c is not None:
        numeric["temperature_max_c"] = temperature_max_c
    try:
        numeric = {key: float(value) for key, value in numeric.items()}
        top_k_routes = max(1, min(int(top_k_routes), 10))
    except (TypeError, ValueError):
        return tool_error(tool, "Temperature, threshold, and route-count inputs must be numeric.", error_code="invalid_numeric_input")
    if not all(math.isfinite(value) for value in numeric.values()):
        return tool_error(tool, "Temperature and threshold inputs must be finite.", error_code="non_finite_numeric_input")
    step_c = numeric["temperature_step_c"]
    min_target = numeric["min_target_solubility_pct"]
    min_selectivity = numeric["min_selectivity_pct"]
    lower, upper = numeric.get("temperature_min_c"), numeric.get("temperature_max_c")
    if step_c <= 0 or min_target < 0 or min_selectivity < 0:
        return tool_error(tool, "Temperature step must be positive and thresholds nonnegative.", error_code="invalid_route_parameters")
    if lower is not None and upper is not None and upper < lower:
        return tool_error(tool, "temperature_max_c must be at least temperature_min_c.", error_code="invalid_temperature_range")

    # Refuse before planning if no stored temperature node lies inside the
    # bounds. Without this the recursion below calls screen_polymer_separation,
    # which correctly refuses with empty_temperature_grid, and then reads
    # ["data"] off the refusal — turning "nothing was evaluable" into
    # "these polymers could not be separated".
    stored_nodes = thermo._grid_nodes()
    if not thermo._nodes_within(
        lower if lower is not None else stored_nodes[0],
        upper if upper is not None else stored_nodes[-1],
        step_c, bool(strict_maximum),
    ):
        return tool_error(
            tool,
            "No stored temperature node lies inside the requested bounds.",
            error_code="empty_temperature_grid",
            temperature_min_c=lower, temperature_max_c=upper,
            nearest_stored_nodes_c=[
                node for node in (
                    max((n for n in stored_nodes if lower is None or n <= lower), default=None),
                    min((n for n in stored_nodes if upper is None or n >= upper), default=None),
                ) if node is not None
            ],
        )

    screens: dict[tuple[str, ...], dict[str, Any]] = {}
    solved: dict[tuple[str, ...], list[dict[str, Any]]] = {}

    def screen(subset: tuple[str, ...]) -> dict[str, Any]:
        if subset not in screens:
            screens[subset] = parse_tool_result(screen_polymer_separation(
                list(subset), temperature_min_c=lower, temperature_max_c=upper,
                target_polymers=list(subset), strict_maximum=bool(strict_maximum),
                temperature_step_c=step_c, require_atmospheric=require_atmospheric,
            ))["data"]
        return screens[subset]

    def finish(route: dict[str, Any]) -> dict[str, Any]:
        route = {**route, "steps": [dict(item) for item in route.get("steps", [])]}
        unresolved = set(route.get("unresolved_polymers", []))
        route["unresolved_polymers"] = [name for name in names if name in unresolved]
        for index, item in enumerate(route["steps"], 1):
            item["step"] = index
        route["sequence"] = [item["dissolved_polymer"] for item in route["steps"]]
        if route.get("complete") and route.get("final_residue"):
            route["sequence"].append(route["final_residue"])
        route["solvent_mapping"] = {
            item["dissolved_polymer"]: item["solvent"] for item in route["steps"]
        }
        route["bottleneck_selectivity_pct"] = min(
            (item["selectivity_pct"] for item in route["steps"]), default=None
        )
        route["bottleneck_target_solubility_pct"] = min(
            (item["target_solubility_pct"] for item in route["steps"]), default=None
        )
        route["cumulative_off_target_burden_wt_pct_sum"] = sum(
            float(value)
            for item in route["steps"]
            for value in (item.get("off_target_solubilities_pct") or {}).values()
        )
        route["peak_temperature_c"] = max(
            (item["temperature_c"] for item in route["steps"]), default=None
        )
        return route

    def score(route: dict[str, Any]) -> tuple[float, ...]:
        return (
            float(bool(route.get("complete"))),
            float(len(names) - len(route.get("unresolved_polymers", []))),
            float(route.get("bottleneck_selectivity_pct") or -math.inf),
            -float(
                route["cumulative_off_target_burden_wt_pct_sum"]
                if route.get("cumulative_off_target_burden_wt_pct_sum") is not None
                else math.inf
            ),
            float(route.get("bottleneck_target_solubility_pct") or -math.inf),
            -float(route.get("peak_temperature_c") or math.inf),
        )

    def solve(subset: tuple[str, ...]) -> list[dict[str, Any]]:
        if subset in solved:
            return solved[subset]
        if len(subset) == 1:
            solved[subset] = [finish({
                "complete": True, "steps": [], "final_residue": subset[0],
                "unresolved_polymers": [],
            })]
            return solved[subset]
        result = screen(subset)
        if result.get("success") is not True:
            solved[subset] = [finish({
                "complete": False, "steps": [], "final_residue": None,
                "unresolved_polymers": list(subset),
                "failure_reason": result.get("error_code") or "subset_screen_failed",
            })]
            return solved[subset]
        directions = {
            item.get("dissolved_polymer"): item.get("best_candidate")
            for item in result.get("screened_directions", []) if isinstance(item, dict)
        }
        routes: list[dict[str, Any]] = []
        for target in subset:
            candidate = directions.get(target)
            if not isinstance(candidate, dict):
                continue
            target_value = float(candidate.get("target_solubility_pct") or 0.0)
            selectivity = float(candidate.get("selectivity_pct") or 0.0)
            if target_value < min_target or selectivity < min_selectivity:
                continue
            retained = tuple(item for item in subset if item != target)
            stage = {
                "dissolved_polymer": target,
                "polymer": target,
                "retained_polymers": list(retained),
                **{key: candidate.get(key) for key in (
                    "solvent", "temperature_c", "target_solubility_pct",
                    "max_off_target_solubility_pct", "off_target_solubilities_pct",
                    "limiting_off_target_polymer", "selectivity_pct", "boiling_point_c",
                    "boiling_point_margin_c", "atmospheric_feasible",
                    "is_clipped", "clip_limit_wt_percent",
                    "heating_risk_level", "heating_flags", "catalog_hazard_reference_c",
                    "ghs_signal_word", "g_score", "peroxide_former_class", "flash_point_c",
                    "flash_point_available", "operating_at_or_above_flash_point",
                    "safety_source", "typed_safety_evidence",
                    "lower_hazard_search_scope", "lower_hazard_alternative_count",
                    "lower_hazard_alternatives",
                    "lower_hazard_alternatives_truncated", "ghs_danger_unavoidable",
                )},
                "selectivity_unit": "percentage_points",
            }
            for tail in solve(retained):
                routes.append(finish({
                    "complete": bool(tail.get("complete")),
                    "steps": [stage, *tail.get("steps", [])],
                    "final_residue": tail.get("final_residue"),
                    "unresolved_polymers": list(tail.get("unresolved_polymers", [])),
                    "failure_reason": tail.get("failure_reason"),
                }))
        if not routes:
            routes = [finish({
                "complete": False, "steps": [], "final_residue": None,
                "unresolved_polymers": list(subset),
                "failure_reason": "no_viable_next_partition",
            })]
        solved[subset] = sorted(routes, key=score, reverse=True)[:top_k_routes]
        return solved[subset]

    root_subset = tuple(sorted(names))
    routes = sorted(solve(root_subset), key=score, reverse=True)[:top_k_routes]
    for rank, route in enumerate(routes, 1):
        route["rank"] = rank
    best = routes[0]
    runner_up = routes[1] if len(routes) > 1 else None
    threshold_margin = (
        None if best.get("bottleneck_selectivity_pct") is None
        else float(best["bottleneck_selectivity_pct"]) - min_selectivity
    )
    root_screen = screens.get(root_subset, {})
    atmospheric_exclusions = _atmospheric_exclusion_counts()
    for result in screens.values():
        for reason in atmospheric_exclusions:
            atmospheric_exclusions[reason] += int(result.get(reason, 0))
    temperature_scope = (
        "user_bounded" if supplied_min and supplied_max
        else "grid_min_to_user_max" if supplied_max
        else "user_min_to_grid_max" if supplied_min
        else "full_stored_grid_domain"
    )
    return tool_success(
        tool,
        analysis_type="multistage_separation_route",
        polymers=names,
        feed_mass_fractions=composition,
        temperature_min_c=root_screen.get("temperature_min_c", lower),
        temperature_max_c=root_screen.get("temperature_max_c", upper),
        strict_maximum=bool(strict_maximum),
        temperature_step_c=step_c,
        temperature_scope=temperature_scope,
        require_atmospheric=require_atmospheric,
        **atmospheric_exclusions,
        min_target_solubility_pct=min_target,
        min_selectivity_pct=min_selectivity,
        solubility_unit="wt_pct_solution_concentration",
        selectivity_unit="percentage_points",
        complete=bool(best.get("complete")),
        best_sequence=best.get("sequence", []),
        steps=best.get("steps", []),
        solvent_mapping=best.get("solvent_mapping", {}),
        final_residue=best.get("final_residue"),
        unresolved_polymers=best.get("unresolved_polymers", []),
        bottleneck_selectivity_pct=best.get("bottleneck_selectivity_pct"),
        bottleneck_target_solubility_pct=best.get("bottleneck_target_solubility_pct"),
        cumulative_off_target_burden_wt_pct_sum=best.get(
            "cumulative_off_target_burden_wt_pct_sum"
        ),
        cumulative_off_target_burden_unit="sum_of_stage_off_target_wt_pct_values",
        runner_up_sequence=None if runner_up is None else runner_up.get("sequence"),
        runner_up_steps=None if runner_up is None else runner_up.get("steps"),
        runner_up_cumulative_off_target_burden_wt_pct_sum=(
            None if runner_up is None
            else runner_up.get("cumulative_off_target_burden_wt_pct_sum")
        ),
        best_result_is_marginal=bool(
            best.get("complete") and threshold_margin is not None
            and 0.0 <= threshold_margin < 0.01
        ),
        threshold_margin_pct=threshold_margin,
        peak_temperature_c=best.get("peak_temperature_c"),
        top_k_sequences=routes,
        complete_route_count=sum(bool(route.get("complete")) for route in routes),
        subset_screens_evaluated=len(screens),
        warnings=[
            "Modeled solubility is wt% solution concentration, not feed recovery or product purity.",
            "A 100 wt% value is a clipped model ceiling, not a precise prediction.",
            "Each stage assumes complete prior-solvent removal before the next screen.",
            "Solvent loading, carryover, kinetics, residence time, filtration, washing, precipitation, and solvent recovery are not modeled.",
            "NBP margin is operability, not safety; local flash point was not checked.",
            "Candidates require experimental validation.",
        ],
        model_basis="recursive application of stored-grid solubility values",
    )


plan_multistage_separation.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]
