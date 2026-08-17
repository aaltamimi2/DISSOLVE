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
    MODELING_CAPABILITIES, UNAVAILABLE_MODELING_METHODS,
    _screen_catalog_provenance, _solvent_resolution_error, _temperature_grid,
    normalize_feed_composition, screen_polymer_separation,
)


def _declared_deliverable_solvents(typed_deliverable: Any) -> list[str]:
    """Read named solvents from the typed deliverable.

    Preference lives in ``runtime.declared_solvents``: ``candidate_solvents``
    wins whenever it is a list, including empty. The legacy ``solvents`` key
    is only the fallback when the candidate field is absent. Guard, projector,
    merge, and TEA readers consume that one rule.
    """
    return declared_solvents(typed_deliverable)


def _context_declared_solvents(context: dict[str, Any]) -> list[str]:
    """Prefer the typed deliverable, then the same dual-key rule on context."""
    declared = context.get("declared_deliverable")
    if has_declared_solvents(declared):
        return declared_solvents(declared)
    return declared_solvents(context)


def _bind_declared_solvents(context: dict[str, Any]) -> dict[str, Any]:
    """Write the preferred solvent list onto ``context['solvents']``."""
    if not has_declared_solvents(context.get("declared_deliverable")) and (
        not has_declared_solvents(context)
    ):
        return context
    names = _context_declared_solvents(context)
    if context.get("solvents") == names:
        return context
    projected = dict(context)
    projected["solvents"] = names
    return projected


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


def _authorized_plan_polymers(
    original_query: str,
    typed_context: dict[str, Any],
) -> set[str]:
    """User-named identities plus polymers already established in typed state."""
    return set(thermo.named_polymer_identities(original_query)) | set(
        thermo.context_polymer_identities(typed_context)
    )


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


def _invented_polymer_identities(
    arguments: dict[str, Any],
    authorized: set[str],
) -> list[str]:
    extras: list[str] = []
    for key in (
        "first_polymer", "second_polymer", "recovered_polymer",
        "sacrificial_getter_polymer", "target_polymer", "polymer_name",
        "polymer", "feed_polymers", "target_polymers", "other_polymers",
        "polymers",
    ):
        extras.extend(
            item for item in _argument_polymer_identities(arguments.get(key))
            if item not in authorized
        )
    return list(dict.fromkeys(extras))


def resolve_polymer_data_scope(
    feed_polymers: list[str],
    operating_solvent: Optional[str] = None,
    operating_temperature_c: Optional[float] = None,
    include_capability_inventory: bool = False,
) -> str:
    """Resolve labels, then use the HSP ML model only after a fitted-data gap."""
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
                    "method": evidence["method"],
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
        "data_capabilities": list(MODELING_CAPABILITIES), "available_thermodynamic_solvents": sorted(thermo.canonical_solvent_name(item) for item in thermo.get_available_solvents()), "available_thermodynamic_solvent_count": len(thermo.get_available_solvents()),
        "unavailable_modeling_methods": [
            {key: row[key] for key in ("method", "status")}
            for row in UNAVAILABLE_MODELING_METHODS
        ],
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
            "grid-first thermodynamics with labelled Apelblat extrapolation, "
            "then checksummed HSP Random Forest fallback"
        ),
    )


def _validate_scope_plan(
    calls: list[dict[str, Any]], original_query: str,
    typed_context: dict[str, Any],
) -> tuple[str, ...]:
    violations = []
    scope_call = next(
        (item for item in calls if item.get("name") == "resolve_polymer_data_scope"),
        None,
    )
    membership_call = next(
        (item for item in calls if item.get("name") == "lookup_material_database_membership"),
        None,
    )
    mentions = thermo.find_polymer_mentions(original_query)
    requested = list(dict.fromkeys(label for label, _ in mentions))
    declared_request_kind = typed_context.get(
        "declared_request_kind",
    )
    route_mode = (
        declared_request_kind
        == "separation_route"
    )
    getter_cycle_mode = (
        declared_request_kind
        == _COOL_THEN_REHEAT_PROCESS_KIND
    )
    impact_mode = (
        declared_request_kind
        == "impact_assessment"
    )
    violations.extend(_validate_material_membership_plan(
        membership_call, scope_call, declared_request_kind, typed_context,
    ))
    if scope_call is not None and _root_question_is_hsp_polymer_library(
        original_query,
    ):
        violations.append(
            "An HSP or Hansen polymer library roster is "
            "lookup_hansen_parameters with material_type=polymer and "
            "empty material_names, not resolve_polymer_data_scope."
        )
    if scope_call is not None and _root_question_is_tea_lca_optimization_capability(
        original_query,
    ):
        violations.append(
            "A TEA, LCA, or optimization capability question is not a "
            "thermodynamic inventory; do not call resolve_polymer_data_scope."
        )
    # Typed intent owns tool selection; the remaining checks validate arguments.
    scope_arguments = (scope_call.get("args") or {}) if scope_call else {}
    inventory_mode = bool(
        isinstance(scope_arguments, dict)
        and scope_arguments.get("include_capability_inventory") is True)
    if impact_mode and requested and scope_call is None:
        violations.append(
            "Use resolve_polymer_data_scope for each newly offered stream polymer; "
            "it evaluates grid-first thermodynamics first and the HSP Random Forest only as a fallback."
        )
    route_call = next(
        (item for item in calls if item.get("name") == "plan_multistage_separation"),
        None,
    )
    if route_mode:
        if route_call is None:
            violations.append(
                "Use plan_multistage_separation for this explicit multi-polymer "
                "process-route request; a coverage lookup cannot provide process conditions."
            )
        else:
            route_arguments = route_call.get("args") or {}
            supplied_route = list(dict.fromkeys(
                thermo.polymer_label_key(str(item))
                for item in route_arguments.get("feed_polymers", [])
            )) if isinstance(route_arguments, dict) else []
            requested_route = [thermo.polymer_label_key(item) for item in requested]
            if set(supplied_route) != set(requested_route):
                violations.append(
                    "Preserve every requested feed label in the route call: "
                    f"feed_polymers={requested}; received {supplied_route}."
                )
    getter_cycle_call = next((
        item for item in calls
        if item.get("name") == "screen_cool_then_reheat_getter"
    ), None)
    if getter_cycle_mode and getter_cycle_call is None:
        violations.append(
            "Use screen_cool_then_reheat_getter for an explicit dissolve -> "
            "cool -> reheat -> filter request; never substitute the single-ramp "
            "precipitation screen."
        )
    if getter_cycle_call is not None:
        arguments = getter_cycle_call.get("args") or {}
        if not isinstance(arguments, dict):
            violations.append(
                "screen_cool_then_reheat_getter arguments must be one JSON object."
            )
        else:
            getter_omitted = not str(
                arguments.get("sacrificial_getter_polymer") or ""
            ).strip()
            required_identities = ["recovered_polymer", "solvent"]
            if not getter_omitted:
                required_identities.append("sacrificial_getter_polymer")
            for key in required_identities:
                if not isinstance(arguments.get(key), str) or not str(
                    arguments.get(key)
                ).strip():
                    violations.append(
                        f"{key} must be one explicit named identity for the getter cycle."
                    )
            if getter_omitted:
                for key in (
                    "dissolution_temperature_c",
                    "cool_temperature_c",
                    "reheat_temperature_c",
                ):
                    if arguments.get(key) is None:
                        violations.append(
                            f"{key} is required when searching the polymer catalog."
                        )
            declared = typed_context.get("declared_deliverable")
            typed_deliverable = declared if isinstance(declared, dict) else {}
            declared_solvents = _declared_deliverable_solvents(typed_deliverable)
            if getter_cycle_mode and typed_deliverable and len(
                declared_solvents
            ) != 1:
                violations.append(
                    "Declare exactly one named solvent for a cool-then-reheat cycle."
                )
            if declared_solvents:
                expected_solvent = thermo.resolve_solvent(str(declared_solvents[0]))
                supplied_solvent = thermo.resolve_solvent(
                    str(arguments.get("solvent") or "")
                )
                if len(declared_solvents) != 1 or supplied_solvent != expected_solvent:
                    violations.append(
                        "Preserve the one named cycle solvent from the declared deliverable."
                    )
            declared_targets = list(
                typed_deliverable.get("target_polymers") or []
            )
            if getter_cycle_mode and typed_deliverable and len(
                declared_targets
            ) != 1:
                violations.append(
                    "Declare exactly one target polymer for the recovered-polymer role."
                )
            if declared_targets:
                expected_recovered = thermo.resolve_polymer(
                    str(declared_targets[0])
                )
                supplied_recovered = thermo.resolve_polymer(
                    str(arguments.get("recovered_polymer") or "")
                )
                if (
                    len(declared_targets) != 1
                    or supplied_recovered != expected_recovered
                ):
                    violations.append(
                        "Preserve the declared recovered polymer as recovered_polymer."
                    )
            declared_feed, unsupported_declared = _feed_names(
                typed_deliverable.get("feed_polymers")
            ) if typed_deliverable.get("feed_polymers") else ([], [])
            if getter_cycle_mode and typed_deliverable and not getter_omitted and (
                unsupported_declared or len(declared_feed) != 2
            ):
                violations.append(
                    "Declare exactly two supported feed polymers for the recovered "
                    "and sacrificial-getter roles."
                )
            if getter_omitted and len(declared_feed) == 2:
                violations.append(
                    "Do not invent a second polymer for a polymer-catalog getter "
                    "search; omit sacrificial_getter_polymer instead."
                )
            if not getter_omitted:
                supplied_roles, unsupported_roles = _feed_names([
                    arguments.get("recovered_polymer"),
                    arguments.get("sacrificial_getter_polymer"),
                ])
                if declared_feed and (
                    unsupported_declared or unsupported_roles
                    or set(supplied_roles) != set(declared_feed)
                ):
                    violations.append(
                        "Preserve both declared feed polymers in the recovered and "
                        "sacrificial-getter roles."
                    )
            declared_setpoints = list(
                typed_deliverable.get("explicit_setpoints_c") or []
            )
            if declared_setpoints:
                supplied_setpoints = [
                    arguments.get("dissolution_temperature_c"),
                    arguments.get("cool_temperature_c"),
                    arguments.get("reheat_temperature_c"),
                ]
                if supplied_setpoints != declared_setpoints:
                    violations.append(
                        "Preserve declared explicit_setpoints_c in dissolve, cool, "
                        "then reheat order."
                    )
    if scope_call is not None:
        arguments = scope_arguments
        supplied = list(dict.fromkeys(
            thermo.polymer_label_key(str(item))
            for item in arguments.get("feed_polymers", [])
        )) if isinstance(arguments, dict) else []
        requested_keys = [thermo.polymer_label_key(item) for item in requested]
        if requested and set(supplied) != set(requested_keys):
            violations.append(
                "feed_polymers must contain the exact requested labels "
                f"{requested}; received {supplied}"
            )
        if not requested and not inventory_mode:
            violations.append(
                "Set include_capability_inventory=true when feed_polymers is empty."
            )
        if requested and impact_mode and inventory_mode:
            violations.append(
                "Omit include_capability_inventory for this scoped impact request."
            )
        if impact_mode and isinstance(arguments, dict):
            screen_rows, _ = candidate_evidence(
                typed_context, {CANDIDATE_SHAPE_SCREEN},
            )
            leading = next(iter(screen_rows), {})
            expected_solvent = leading.get("solvent")
            expected_temperature = leading.get("temperature_c")
            if expected_solvent and thermo.resolve_solvent(
                str(arguments.get("operating_solvent") or "")
            ) != thermo.resolve_solvent(str(expected_solvent)):
                violations.append(
                    f"Preserve active operating_solvent={expected_solvent} for the impact assessment."
                )
            if (
                expected_temperature is not None
                and arguments.get("operating_temperature_c") != expected_temperature
            ):
                violations.append(
                    "Preserve active operating_temperature_c="
                    f"{float(expected_temperature):g} for the impact assessment."
                )

    precipitation_call = next(
        (item for item in calls if item.get("name") == "screen_precipitation_order"),
        None,
    )
    if getter_cycle_mode and precipitation_call is not None:
        violations.append(
            "A cool-then-reheat request cannot fall through to "
            "screen_precipitation_order."
        )
    if precipitation_call is not None:
        arguments = precipitation_call.get("args") or {}
        if not isinstance(arguments, dict):
            violations.append("screen_precipitation_order arguments must be one JSON object.")
        else:
            supplied_solvents = [
                str(item).strip() for item in arguments.get("solvents") or []
                if str(item).strip()
            ]
            declared = typed_context.get("declared_deliverable")
            typed_deliverable = declared if isinstance(declared, dict) else {}
            normalized_query = " " + re.sub(r"[^a-z0-9]+", " ", original_query.casefold()).strip() + " "
            inherited_solvents = []
            for item in supplied_solvents:
                normalized_solvent = re.sub(r"[^a-z0-9]+", " ", item.casefold()).strip()
                if f" {normalized_solvent} " not in normalized_query:
                    inherited_solvents.append(item)
            declared_solvents = _declared_deliverable_solvents(typed_deliverable)
            if typed_deliverable:
                inherited_solvents = (
                    supplied_solvents
                    if supplied_solvents != declared_solvents
                    else []
                )
            if inherited_solvents:
                violations.append(
                    "Preserve declared solvent constraints exactly; omit remembered solvents when the deliverable declares adaptive scope." if typed_deliverable else
                    "Do not inherit solvent constraints from typed context; omit solvents unless each is explicitly named in the original query."
                )
            full_feed = _typed_precipitation_full_feed(typed_context, original_query)
            if bool(arguments.get("include_full_feed_order")) != full_feed:
                violations.append(
                    "Set include_full_feed_order="
                    f"{str(full_feed).lower()} for this "
                    f"{'full-feed' if full_feed else 'pair-order'} request."
                )
            for key in ("first_polymer", "second_polymer"):
                value = arguments.get(key)
                if not isinstance(value, str) or not value.strip():
                    violations.append(f"{key} must be one polymer string, not a list.")
            for key in ("temperature_min_c", "temperature_max_c"):
                inherited = _typed_precipitation_value(typed_context, key)
                if inherited is not None and arguments.get(key) != inherited:
                    violations.append(f"Preserve inherited {key}={float(inherited):g}.")
            if _typed_precipitation_value(typed_context, "temperature_max_c") is not None:
                inherited_strict = bool(_typed_precipitation_value(typed_context, "strict_maximum"))
                if bool(arguments.get("strict_maximum")) != inherited_strict:
                    violations.append(
                        "Preserve inherited strict_maximum="
                        f"{str(inherited_strict).lower()}."
                    )
            ranked_pair_mode = (
                typed_context.get(
                    "declared_request_kind",
                )
                == "precipitation_ranked_pair"
            )
            # Pair ownership is declared before delegation.
            # Stored evidence supplies pair identity and order.
            expected_feed, manifest_owned_feed = _typed_precipitation_feed(typed_context)
            if full_feed and not manifest_owned_feed:
                for label, _ in mentions:
                    for identity in thermo.expand_polymer_identity(label):
                        if identity not in expected_feed:
                            expected_feed.append(identity)
            supplied_feed, unsupported_feed = _feed_names(arguments.get("feed_polymers"))
            if not ranked_pair_mode and len(expected_feed) > 1 and (
                unsupported_feed or set(supplied_feed) != set(expected_feed)
            ):
                violations.append(
                    f"Preserve the established feed_polymers exactly: {expected_feed}."
                )
            analysis = typed_context.get("last_analysis") or {}
            ranked_pair = next(iter(analysis.get("ranked_pairs") or []), {})
            pair = [str(value) for value in ranked_pair.get("polymers") or []]
            route = typed_context.get("last_route") or {}
            route_order = [
                str(step.get("dissolved_polymer"))
                for step in route.get("steps") or []
                if step.get("dissolved_polymer")
            ]
            if route.get("final_residue"):
                route_order.append(str(route["final_residue"]))
            expected_order = [value for value in route_order if value in set(pair)]
            if ranked_pair_mode and len(pair) == len(expected_order) == 2:
                supplied_pair, unsupported = _feed_names(arguments.get("feed_polymers"))
                if unsupported or set(supplied_pair) != set(pair):
                    violations.append(
                        "Preserve the typed hardest pair only in feed_polymers: "
                        f"{pair}."
                    )
                supplied_order = [
                    thermo.resolve_polymer(str(arguments.get(key) or ""))
                    for key in ("first_polymer", "second_polymer")
                ]
                if supplied_order != expected_order:
                    violations.append(
                        "Preserve the typed route order for the hardest pair: "
                        f"first_polymer={expected_order[0]}, "
                        f"second_polymer={expected_order[1]}."
                    )
    authorized = _authorized_plan_polymers(original_query, typed_context)
    if authorized:
        for call in calls:
            arguments = call.get("args") or {}
            if not isinstance(arguments, dict):
                continue
            extras = _invented_polymer_identities(arguments, authorized)
            if extras:
                violations.append(
                    "Do not invent polymer identities absent from the current "
                    "query and typed state "
                    f"({', '.join(extras)}). A polymer-catalog getter search "
                    "uses screen_cool_then_reheat_getter and omits "
                    "sacrificial_getter_polymer."
                )
                break
    return tuple(violations)


def _project_specialist_context(
    context: dict[str, Any], original_query: str,
) -> dict[str, Any]:
    """Prevent remembered solvents from becoming precipitation constraints."""
    impact_request = bool(re.search(
        r"\b(?:impact|effect|affect|residual|what\s+if|present\s+in|in\s+the\s+stream)\b",
        original_query, re.I,
    ))
    precipitation_request = context.get("declared_request_kind") in {
        "precipitation_order", "precipitation_ranked_pair",
    }
    getter_cycle_request = (
        context.get("declared_request_kind")
        == _COOL_THEN_REHEAT_PROCESS_KIND
    )
    if getter_cycle_request:
        explicit_solvents = _context_declared_solvents(context)
        projected = dict(context)
        projected["solvents"] = explicit_solvents
        projected["last_candidates"] = []
        projected["planning_constraints"] = {
            "process_kind": _COOL_THEN_REHEAT_PROCESS_KIND,
            "solvent_scope": "one_named_solvent",
            "explicit_solvents": explicit_solvents,
            "automatic_catalog_search": False,
            "single_ramp_substitution_allowed": False,
            "constraint_source": (
                "declared_deliverable"
                if has_declared_solvents(context.get("declared_deliverable"))
                else "original_query_only"
            ),
        }
        return projected
    if not precipitation_request:
        if not impact_request:
            return _bind_declared_solvents(context)
        projected = dict(context)
        screen_rows, _ = candidate_evidence(context, {CANDIDATE_SHAPE_SCREEN})
        leading = next(iter(screen_rows), {})
        if leading.get("solvent") and leading.get("temperature_c") is not None:
            projected["active_separation_condition"] = {
                "solvent": leading["solvent"],
                "temperature_c": leading["temperature_c"],
            }
        return _bind_declared_solvents(projected)
    declared = context.get("declared_deliverable")
    typed_deliverable = declared if isinstance(declared, dict) else {}
    if typed_deliverable:
        explicit = declared_solvents(typed_deliverable)
    else:
        query_key = " " + re.sub(
            r"[^a-z0-9]+", " ", original_query.casefold(),
        ).strip() + " "
        explicit = []
        for solvent in declared_solvents(context):
            solvent_key = re.sub(
                r"[^a-z0-9]+", " ", str(solvent).casefold(),
            ).strip()
            if f" {solvent_key} " in query_key:
                explicit.append(str(solvent))
    projected = dict(context)
    if impact_request:
        screen_rows, _ = candidate_evidence(context, {CANDIDATE_SHAPE_SCREEN})
        leading = next(iter(screen_rows), {})
        if leading.get("solvent") and leading.get("temperature_c") is not None:
            projected["active_separation_condition"] = {
                "solvent": leading["solvent"],
                "temperature_c": leading["temperature_c"],
            }
    full_feed = (
        typed_deliverable.get("kind") == "precipitation_order"
        and typed_deliverable.get("candidate_scope") == "full_feed"
        if typed_deliverable else False  # v12: caller declares scope; no prose sniffing
    )
    declared_feed = typed_deliverable.get("feed_polymers")
    required_feed = list(
        declared_feed if isinstance(declared_feed, list)
        else context.get("polymers") or []
    )
    if full_feed and not typed_deliverable:
        resolved_feed, _ = _feed_names(required_feed)
        required_feed = resolved_feed
        for label, _ in thermo.find_polymer_mentions(original_query):
            for identity in thermo.expand_polymer_identity(label):
                if identity not in required_feed:
                    required_feed.append(identity)
    projected["planning_constraints"] = {
        "solvent_scope": "explicit" if explicit else "adaptive_all_modeled",
        "explicit_solvents": explicit,
        "required_feed_polymers": required_feed,
        "include_full_feed_order": full_feed,
        "constraint_source": (
            "declared_deliverable" if typed_deliverable
            else "original_query_only"
        ),
    }
    projected["solvents"] = explicit
    if not explicit:
        projected["last_candidates"] = [{
            key: value for key, value in item.items() if key != "solvent"
        } for item in context.get("last_candidates") or []]
    return projected


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


def _validate_material_membership_plan(
    membership_call: dict[str, Any] | None,
    scope_call: dict[str, Any] | None,
    declared_request_kind: Any,
    typed_context: dict[str, Any],
) -> tuple[str, ...]:
    labels = _declared_material_labels(typed_context)
    declared = typed_context.get("declared_deliverable")
    membership_requested = bool(
        declared_request_kind == "capability_inventory"
        and isinstance(declared, dict)
        and "membership" in (declared.get("requested_quantities") or [])
        and labels
    )
    roster_requested = bool(
        declared_request_kind == "capability_inventory"
        and isinstance(declared, dict)
        and not labels
    )
    violations: list[str] = []
    if membership_call is None:
        if membership_requested:
            violations.append(
                "Use lookup_material_database_membership for the declared "
                f"material labels {labels}; it checks polymer and solvent "
                "namespaces before answering database membership."
            )
        return tuple(violations)
    if roster_requested:
        violations.append(
            "For a capability_inventory with no named material labels, use "
            "resolve_polymer_data_scope with feed_polymers=[] and "
            "include_capability_inventory=true; membership lookup requires "
            "named labels."
        )
        return tuple(violations)
    if not membership_requested:
        violations.append(
            "Use lookup_material_database_membership only when the declared "
            "capability_inventory requests quantity membership."
        )
    if scope_call is not None:
        violations.append(
            "Use one membership lookup, not a polymer-scope lookup beside it."
        )
    arguments = membership_call.get("args")
    supplied = (
        list(dict.fromkeys(
            str(item).strip()
            for item in arguments.get("material_names", [])
            if str(item).strip()
        ))
        if isinstance(arguments, dict)
        and isinstance(arguments.get("material_names"), list)
        else []
    )
    if not labels:
        violations.append(
            "The declared capability_inventory deliverable must name each "
            "material whose database membership is being checked."
        )
    elif supplied != labels:
        violations.append(
            f"Preserve declared material_names exactly: {labels}; "
            f"received {supplied}."
        )
    return tuple(violations)


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


def _typed_precipitation_value(context: dict[str, Any], key: str) -> Any:
    declared = context.get("declared_deliverable")
    return (
        declared.get(key)
        if isinstance(declared, dict)
        else context.get(key)
    )


def _typed_precipitation_full_feed(
    context: dict[str, Any], original_query: str,
) -> bool:
    declared = context.get("declared_deliverable")
    if isinstance(declared, dict):
        return (
            declared.get("kind") == "precipitation_order"
            and declared.get("candidate_scope") == "full_feed"
        )
    return False  # v12: caller declares scope; no prose sniffing


def _typed_precipitation_feed(
    context: dict[str, Any],
) -> tuple[list[str], bool]:
    declared = context.get("declared_deliverable")
    if isinstance(declared, dict):
        feed, _ = _feed_names(declared.get("feed_polymers"))
        return feed, True
    feed, _ = _feed_names(context.get("polymers"))
    return feed, False


def _full_feed_order_summary(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "solvent", "dissolution_temperature_c", "precipitation_proxy_order",
        "precipitation_proxy_crossings_c", "adjacent_proxy_windows_c",
        "crossing_states",
        "minimum_adjacent_proxy_window_c", "unresolved_at_temperature_min",
        "full_feed_proxy_order_resolved", "meets_full_feed_minimum_window",
        "boiling_point_c", "boiling_point_margin_c", "atmospheric_feasible",
        "dissolution_value_clipped",
        "dissolution_solubility_method_by_polymer",
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
        (temperature, float(evidence["solubility_pct"]), evidence["method"])
        for temperature, evidence in points
    ]
    if numeric[-1][1] < threshold:
        return {
            "temperature_c": None,
            "status": "below_threshold_at_dissolution",
            "method": numeric[-1][2],
        }
    for index in range(len(numeric) - 1, 0, -1):
        low_t, low_value, low_method = numeric[index - 1]
        high_t, high_value, high_method = numeric[index]
        if low_value < threshold <= high_value:
            if (
                low_method != high_method
                and not thermo.methods_share_continuous_basis(
                    low_method, high_method,
                )
            ):
                return {
                    "temperature_c": None,
                    "status": "method_boundary_discontinuity",
                    "method": None,
                }
            fraction = (threshold - low_value) / (high_value - low_value)
            return {
                "temperature_c": round(low_t + fraction * (high_t - low_t), 6),
                "status": "crossing_within_screen",
                "method": (
                    thermo.GRID_INTERPOLATION
                    if thermo.methods_share_continuous_basis(
                        low_method, high_method,
                    )
                    else low_method
                ),
            }
    return {
        "temperature_c": None,
        "status": "remains_above_threshold_at_temperature_min",
        "method": numeric[0][2],
    }


def _fit_quality_by_polymer(
    polymers: list[str], solvent: str,
) -> dict[str, float | None]:
    """Return governed F6 pair MAPE values without changing fitted science."""
    quality: dict[str, float | None] = {}
    for polymer in polymers:
        entry = thermo.get_entry(polymer, solvent) or {}
        value = entry.get("fit_mape_pct")
        quality[polymer] = None if value is None else round(float(value), 6)
    return quality


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
            "solubility_method_by_polymer": {
                polymer: result["method"]
                for polymer, result in evidence.items()
            },
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
    recommended_temperature_use_regime: Optional[str] = None,
) -> str:
    """Describe the pair-specific basis without advertising a global boundary."""
    del highest_temperature_c
    if recommended_temperature_use_regime in {
        "exploratory_extrapolation",
        "mixed_source_and_extrapolation",
    }:
        return (
            "Recommended condition includes exploratory extrapolation beyond a "
            "pair-specific fitted range and requires experimental validation."
        )
    if recommended_on_grid_boundary:
        return (
            "Recommended condition is on a pair-specific grid boundary; "
            "crossings are interpolated from the retained source grid and "
            "require experimental validation."
        )
    return (
        "Crossings use exact retained-grid nodes or between-node interpolation; "
        "pair-fit extrapolations are labelled and require experimental validation."
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
    available_evidence_methods: Optional[list[str]] = None,
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
        available_evidence_methods=sorted(set(available_evidence_methods or [])),
        recommended_as_literature_getter=False,
        warnings=[
            "The requested cool-then-reheat cycle was not replaced with a single-ramp precipitation screen.",
        ],
    )


def _cycle_value_source(evidence: dict[str, Any]) -> dict[str, Any]:
    """Retain D3 method and point provenance without inventing a second seam."""
    return {
        key: evidence[key] for key in (
            "method", "source_kind", "grid_point_status",
            "source_temperatures_c", "extrapolation",
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
    """Search the fitted polymer catalog for a sacrificial getter."""
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
        getter_search_axis="fitted_polymer_catalog",
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
            "This search covers the fitted polymer catalog for one named solvent "
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
    getter searches the fitted polymer catalog for the named recovered
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
    available_methods: list[str] = []
    for state_id in ("dissolve", "cool", "reheat"):
        evidence_by_state[state_id] = {}
        for polymer in (recovered, getter):
            evidence = thermo.get_solubility_result(
                polymer, solvent_key, temperatures[state_id],
            )
            evidence_by_state[state_id][polymer] = evidence
            if evidence.get("method"):
                available_methods.append(str(evidence["method"]))
            if not evidence.get("available"):
                unavailable.append({
                    "state_id": state_id,
                    "polymer": polymer,
                    "temperature_c": temperatures[state_id],
                    "reason": evidence.get("unavailable_reason") or "value_unavailable",
                    "method": evidence.get("method"),
                    "grid_point_status": evidence.get("grid_point_status"),
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
            available_evidence_methods=available_methods,
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
    all_sources = {
        evidence_by_state[state_id][polymer].get("source_kind")
        for state_id in evidence_by_state
        for polymer in (recovered, getter)
    }
    reference_temperatures_match = temperatures == (
        _OCTANE_GETTER_REFERENCE_TEMPERATURES_C
    )
    decision_basis = (
        "ground_truth_grid"
        if all_sources == {"ground_truth_grid"}
        else "d3_grid_first_selected_methods"
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
        decision_basis=decision_basis,
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
            "D3 grid-first solution capacities and any labeled fit values are not cloud points or observed phase states.",
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
    require_atmospheric: bool = True,
    min_ordering_window_c: float = 0.0,
    min_recovery_window_c: float = 0.0,
    max_first_precipitation_temperature_c: Optional[float] = None,
    top_k: int = 5,
    include_full_feed_order: bool = False,
    include_pubchem: bool = False,
) -> str:
    """Screen a user-named polymer pair across the fitted solvent catalog.

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
    fitted solvent catalog.
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
        # absent bound resolves to the engine's typed fitted window.
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
        "kind": "fitted_thermodynamic_catalog",
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
    excluded_for_bp = excluded_for_dissolution = excluded_for_missing_model = 0
    excluded_for_crossing = 0
    missing_model_details: list[dict[str, Any]] = []
    for solvent in requested_solvents:
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
        boiling_point = thermo.get_boiling_point(solvent)
        dissolution = None
        dissolution_values: dict[str, float] = {}
        dissolution_methods: dict[str, str] = {}
        dissolution_regimes: dict[str, str] = {}
        dissolution_fitted_ranges: dict[str, list[float] | None] = {}
        dissolution_source_grid_ranges: dict[str, list[float] | None] = {}
        for temperature in temperatures:
            if require_atmospheric and (boiling_point is None or temperature >= boiling_point):
                continue
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
                dissolution = temperature
                dissolution_values = {
                    polymer: float(value) for polymer, value in values.items()
                }
                dissolution_methods = {
                    polymer: str(result["method"])
                    for polymer, result in evidence.items()
                }
                dissolution_regimes = {
                    polymer: str(result["temperature_use_regime"])
                    for polymer, result in evidence.items()
                }
                dissolution_fitted_ranges = {
                    polymer: result.get("fitted_temperature_range_c")
                    for polymer, result in evidence.items()
                }
                dissolution_source_grid_ranges = {
                    polymer: result.get("source_grid_temperature_range_c")
                    for polymer, result in evidence.items()
                }
                break
        if dissolution is None:
            if require_atmospheric and boiling_point is not None and boiling_point <= lower:
                excluded_for_bp += 1
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
            "dissolution_solubility_method_by_polymer": dissolution_methods,
            "temperature_use_regime": thermo.aggregate_temperature_use_regimes(
                dissolution, dissolution_regimes.values(),
            ),
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
                boiling_point is not None and dissolution < boiling_point
            ),
            "pair_fit_mape_pct": _fit_quality_by_polymer(names, solvent),
            "crossing_states": _crossing_states(
                solvent, first, second, crossings,
            ),
            "dissolution_value_clipped": any(
                value >= 100.0 for value in dissolution_values.values()
            ),
            **thermo.get_solvent_hazard_framing(solvent),
        }
        if (
            "extrapolation" in row["temperature_use_regime"]
            or row["temperature_use_regime"] == "mixed"
        ):
            row.update({
                "dissolution_temperature_use_regime_by_polymer": (
                    dissolution_regimes
                ),
                "dissolution_fitted_temperature_range_by_polymer": (
                    dissolution_fitted_ranges
                ),
                "dissolution_source_grid_temperature_range_by_polymer": (
                    dissolution_source_grid_ranges
                ),
            })
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
        temperature_use_regime=thermo.aggregate_temperature_use_regimes(
            highest_supported_temperature,
            (
                item.get("temperature_use_regime")
                for item in (selected or candidates)
            ),
        ),
        strict_maximum=bool(strict_maximum),
        precipitation_threshold_wt_pct=threshold,
        min_dissolution_solubility_wt_pct=minimum_dissolution,
        min_ordering_window_c=minimum_window,
        ordering_window_threshold_c=minimum_window,
        min_recovery_window_c=recovery_floor,
        **({} if max_first is None else {
            "max_first_precipitation_temperature_c": max_first,
        }),
        require_atmospheric=bool(require_atmospheric),
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
        excluded_for_boiling_point=excluded_for_bp,
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
                recommended_temperature_use_regime=(
                    recommended_condition.get("temperature_use_regime")
                    if recommended_condition else None
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
            "pair-specific exact/interpolated grid plus labelled pair-fit "
            "extrapolation; shared wt% precipitation proxy"
        ),
    )


def plan_multistage_separation(
    feed_polymers: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
    temperature_step_c: float = 5.0,
    require_atmospheric: bool = True,
    min_target_solubility_pct: float = 5.0,
    min_selectivity_pct: float = 5.0,
    top_k_routes: int = 5,
    feed_mass_fractions: Optional[dict[str, float]] = None,
) -> str:
    """Recursively rank complete or explicitly partial routes for any supported feed."""
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

    screens: dict[tuple[str, ...], dict[str, Any]] = {}
    solved: dict[tuple[str, ...], list[dict[str, Any]]] = {}

    def screen(subset: tuple[str, ...]) -> dict[str, Any]:
        if subset not in screens:
            screens[subset] = parse_tool_result(screen_polymer_separation(
                list(subset), temperature_min_c=lower, temperature_max_c=upper,
                target_polymers=list(subset), strict_maximum=bool(strict_maximum),
                temperature_step_c=step_c, require_atmospheric=bool(require_atmospheric),
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
                    "solubility_method_by_polymer",
                    "limiting_off_target_polymer", "selectivity_pct", "boiling_point_c",
                    "boiling_point_margin_c", "atmospheric_feasible", "temperature_use_regime",
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
    temperature_scope = (
        "user_bounded" if supplied_min and supplied_max
        else "fitted_min_to_user_max" if supplied_max
        else "user_min_to_fitted_max" if supplied_min
        else "full_fitted_domain"
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
        require_atmospheric=bool(require_atmospheric),
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
        model_basis="recursive application of the grid-first solubility model with fitted extrapolation",
    )


plan_multistage_separation.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]

SEPARATION_PROMPT = """You are DISSOLVE's separation engineer. Call exactly one
scoped tool. For a newly mentioned, residual, uncertain, or either/or polymer in
an existing stream, use resolve_polymer_data_scope and include every offered
variant. If typed_session_context.active_separation_condition exists, pass its
solvent and temperature so the result evaluates impact at the active condition.
That tool owns the evidence ladder: grid-first thermodynamics first, the HSP Random
Forest only when fitted coverage is absent, then an explicit no-data result. Never route
a thermodynamically supported identity through HSP. Obey
typed_session_context.planning_constraints exactly: copy required_feed_polymers
and include_full_feed_order; adaptive_all_modeled means omit remembered solvents.
When declared_deliverable is present, obey its complete structured scope exactly;
it is authoritative over resolved_objective.
Obey typed_session_context.declared_request_kind. For polymer_scope or
impact_assessment, use resolve_polymer_data_scope and copy every feed label
exactly without canonicalizing, collapsing alternatives, or expanding PE.
For capability_inventory with named material labels and requested_quantities
containing membership, use lookup_material_database_membership; it checks
polymer and solvent namespaces. A capability_inventory with no named labels is
the fitted-thermodynamic roster, not the HSP polymer library and not a
TEA/LCA/optimization catalog: use
resolve_polymer_data_scope with feed_polymers=[] and
include_capability_inventory=true. Otherwise use resolve_polymer_data_scope,
copy any named polymer labels as the feed, and set
include_capability_inventory=true.
For separation_route use plan_multistage_separation. A declared multi-polymer
process route always owns that route tool; never substitute a data-scope
lookup. For cool_then_reheat_getter use screen_cool_then_reheat_getter with one
named solvent. A named sacrificial getter evaluates that recovered/getter
pair. If the user named one recovered polymer and one solvent and is
searching for another polymer, omit sacrificial_getter_polymer so the tool
searches the fitted polymer catalog; do not invent a second polymer and do
not substitute the solvent-catalog precipitation screen. The
canonical Octane/LDPE/PP reference alone may use its 125 -> 70 -> 85 C defaults;
other identities and every polymer-catalog search require all three setpoints and getter_disposition=
deliberately_not_recovered. Never replace this non-monotone cycle with a
single-ramp precipitation screen. For precipitation_order or
precipitation_ranked_pair use screen_precipitation_order only when the user
named a polymer pair and the question searches solvents.
screen_precipitation_order searches the fitted solvent catalog for a
user-named polymer pair. It does not search for a polymer and cannot run on
one named polymer.
Use screen_precipitation_order for cooling order, precipitation windows,
or reversed precipitation order. Set include_pubchem=true only when the
current request explicitly asks for flash-point or safety evidence; otherwise
leave it false so the screen stays offline and explicitly reports unavailable
flash evidence. Live enrichment covers only the already-selected winning
condition and cannot activate below-flash alternative demotion. For a cooling-versus-solvent comparison of a
ranked pair, require precipitation_ranked_pair and pass those two typed polymers in
stored order; do not add unrelated feed polymers or invent a preferred direction.
Treat min_ordering_window_c as a disclosed selectivity filter, not an
intrinsic cutoff. Use a nonzero value only when the user named the window or
the answer will disclose the threshold. On an alternatives follow-up such as
"what else" or "a different solvent", pass 0 unless the user explicitly named
an ordering window. Treat min_recovery_window_c and
max_first_precipitation_temperature_c as disclosed recovery-window filters,
not measured process windows. recovery_window_c is dissolution temperature
minus the first precipitation proxy. Pass a nonzero recovery floor or a
maximum first-crossing temperature only when the user named how far the first
polymer must cool or by when it must leave solution; omit them on an
alternatives follow-up unless that recovery constraint was restated.
For other pair requests preserve every feed polymer so returned off-pair behavior
remains visible, and preserve which two polymers the user wants first and second.
Pass a named solvent only when the
user constrains it; otherwise let the tool search adaptively. For a question
about rejecting an off-target from a desired precipitated product, make the
desired product first_polymer and the off-target second_polymer. For exactly two
feed polymers keep include_full_feed_order=false: co-dissolving is not a
full-feed or co-precipitation request. For a question
about where another feed polymer falls, a full-feed cut, or co-precipitation,
set include_full_feed_order=true so the same engine returns all proxy crossings
and adjacent windows. Leave it false for an ordinary pair-order request even
when the feed contains additional polymers. Use solvent names only when they
appear in the original user request; names in typed context or the resolved
objective are not constraints. For a full-feed follow-up, preserve the
originating screen bounds; do not turn a candidate dissolution temperature or
plot endpoint into the adaptive-search ceiling. Preserve exact temperature bounds;
pass every stated feed mass fraction so downstream economics can reuse it;
for below/under use strict_maximum=true. Expand generic polyethylene to both
LDPE and HDPE. Keep atmospheric filtering unless pressure operation is explicit.
Do not call another tool, calculate values, or write prose. The parent model
will explain the compact validated result."""
