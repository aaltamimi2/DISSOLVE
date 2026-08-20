"""campaign_basis.v1 — project a v2 run definition onto public held roles.

A v2 campaign has no field_role object. Absence of leftover/precipitation
switches from JSONL is not incompleteness: those plants ran at the worker
production hardcodes. Ranking burn=True against that slice is a held-field
mismatch, not a leftover-to-CHP landscape.
"""
from __future__ import annotations

from typing import Any, Mapping

from . import tea

CAMPAIGN_BASIS_PROJECTION = "campaign_basis.v1"
CAMPAIGN_DEFINITION_SCHEMA_V2 = "dissolve.process_campaign_definition.v2"
_DERIVED_PUBLIC = frozenset({
    "dissolution_temperature_c", "solvent_price_usd_per_kg",
})
_IDENTITY_PUBLIC = frozenset({"target_polymer", "solvent"})
_DERIVED_RULES = {
    "dissolution_temperature_c": (
        "setpoint_rule: lowest stored 25-160 C grid node with "
        "solubility >= 5 wt%"
    ),
    "solvent_price_usd_per_kg": (
        "solvent census price_usd_per_kg for that pair's solvent identity"
    ),
}


class CampaignBasisIncomplete(Exception):
    """The run definition cannot support campaign_basis.v1."""

    error_code = "campaign_basis_incomplete"

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.details = details


def _twelve_public_names() -> tuple[str, ...]:
    return tuple(public for _internal, public in tea._DESIGN_POINT_PUBLIC_FIELDS)


def _held_switch_or_coefficient(
    fixed_fields: Mapping[str, Any], field: str, production_value: Any,
) -> dict[str, Any]:
    """Declared fixed_fields win. JSONL-absent switches stay worker production."""
    if field in fixed_fields:
        return {
            "role": "held",
            "value": fixed_fields[field],
            "projection": "fixed_fields",
        }
    return {
        "role": "held",
        "value": production_value,
        "projection": "worker_production",
    }


def _held_values_equal(field: str, campaign: Any, requested: Any) -> bool:
    if campaign is requested:
        return True
    if field == "energy_case":
        campaign_s = str(campaign or "").upper()
        if isinstance(requested, (list, tuple)):
            return bool(requested) and all(
                str(item or "").upper() == campaign_s for item in requested
            )
        return campaign_s == str(requested or "").upper()
    if isinstance(campaign, (list, tuple)) or isinstance(requested, (list, tuple)):
        try:
            left = tuple(campaign)
            right = tuple(requested)
        except TypeError:
            return False
        if len(left) != len(right):
            return False
        try:
            if all(
                isinstance(item, int) and not isinstance(item, bool)
                for item in (*left, *right)
            ):
                return left == right
            return all(
                round(float(a), 10) == round(float(b), 10)
                for a, b in zip(left, right)
            )
        except (TypeError, ValueError):
            return False
    if isinstance(campaign, bool) or isinstance(requested, bool):
        return bool(campaign) is bool(requested)
    if isinstance(campaign, (int, float)) and not isinstance(campaign, bool):
        try:
            return round(float(campaign), 10) == round(float(requested), 10)
        except (TypeError, ValueError):
            return False
    return campaign == requested


def _delta(field: str, campaign: Any, requested: Any) -> dict[str, Any]:
    numeric = isinstance(campaign, (int, float)) and not isinstance(campaign, bool)
    try:
        requested_number = float(requested)
        numeric = numeric or not isinstance(requested, bool)
    except (TypeError, ValueError):
        requested_number = None
        numeric = False
    payload: dict[str, Any] = {
        "field": field,
        "campaign_value": campaign,
        "requested_value": requested,
    }
    if numeric and requested_number is not None:
        try:
            payload["delta"] = requested_number - float(campaign)
        except (TypeError, ValueError):
            payload["delta"] = None
    else:
        payload["delta"] = {
            "campaign": campaign,
            "requested": requested,
        }
    return payload


def project_campaign_basis_v1(run_definition: Mapping[str, Any]) -> dict[str, Any]:
    """Project dissolve.process_campaign_definition.v2 onto campaign_basis.v1.

    D-8 overlay names stay the twelve. Public extras that change the plant
    (flowsheet switches and B coefficients) join as held at the production
    values the v12 worker actually ran, even when JSONL omitted them.
    """
    if not isinstance(run_definition, Mapping):
        raise CampaignBasisIncomplete(
            "run definition must be an object",
            missing=["schema", "fixed_fields", "setpoint_rule", "pair_definitions"],
        )
    missing: list[str] = []
    schema = run_definition.get("schema")
    if schema != CAMPAIGN_DEFINITION_SCHEMA_V2:
        missing.append("schema")
    fixed_fields = run_definition.get("fixed_fields")
    if not isinstance(fixed_fields, Mapping):
        missing.append("fixed_fields")
    setpoint_rule = run_definition.get("setpoint_rule")
    if not isinstance(setpoint_rule, Mapping):
        missing.append("setpoint_rule")
    pair_definitions = run_definition.get("pair_definitions")
    if not isinstance(pair_definitions, list) or not pair_definitions:
        missing.append("pair_definitions")
    if missing:
        raise CampaignBasisIncomplete(
            "run definition cannot support campaign_basis.v1",
            missing=missing,
            schema=schema,
        )

    field_role: dict[str, Any] = {}
    unassigned: list[str] = []
    for public in _twelve_public_names():
        worker = next(
            internal for internal, name in tea._DESIGN_POINT_PUBLIC_FIELDS
            if name == public
        )
        if worker in fixed_fields:
            field_role[public] = {
                "role": "held",
                "value": fixed_fields[worker],
                "projection": "fixed_fields",
            }
        elif public in _DERIVED_PUBLIC:
            rule = setpoint_rule.get(public) or _DERIVED_RULES[public]
            field_role[public] = {
                "role": "derived",
                "rule": rule,
                "values_vary": True,
            }
        elif public in _IDENTITY_PUBLIC:
            field_role[public] = {"role": "varied", "axis": "identity"}
        else:
            unassigned.append(public)
    if unassigned:
        raise CampaignBasisIncomplete(
            "twelve public fields could not each be assigned a closed role",
            missing=unassigned,
        )

    for field, value in tea._FLOWSHEET_SWITCH_DEFAULTS.items():
        field_role[field] = _held_switch_or_coefficient(fixed_fields, field, value)
    energy = str(
        (field_role.get("energy_case") or {}).get("value") or "C1"
    ).upper()
    for field, value in tea._COEFFICIENT_DEFAULTS.items():
        field_role[field] = _held_switch_or_coefficient(fixed_fields, field, value)
    if energy in {"C1", "C3"}:
        field_role["natural_gas_price_usd_per_m3"] = _held_switch_or_coefficient(
            fixed_fields,
            "natural_gas_price_usd_per_m3",
            tea._NATURAL_GAS_PRICE_USD_PER_M3,
        )
        field_role["steam_power_depreciation"] = _held_switch_or_coefficient(
            fixed_fields,
            "steam_power_depreciation",
            tea._STEAM_POWER_DEPRECIATION_DEFAULT,
        )

    return {
        "campaign_basis_projection": CAMPAIGN_BASIS_PROJECTION,
        "campaign_definition_schema": CAMPAIGN_DEFINITION_SCHEMA_V2,
        "campaign_basis": {
            "field_role": field_role,
            "n_pairs": len(pair_definitions),
            "complete": True,
        },
    }


def held_field_mismatches(
    basis: Mapping[str, Any],
    requested: Mapping[str, Any] | None,
    *,
    seconds_per_pair: float | None = None,
) -> dict[str, Any]:
    """Compare requested public fields against projected HELD roles only.

    Absent keys are unconstrained. Varied and derived fields are not a
    held mismatch. 55 / 20000 / C1 is not hardcoded here.
    """
    campaign_basis = dict(basis.get("campaign_basis") or basis)
    field_role = dict(campaign_basis.get("field_role") or {})
    supplied = dict(requested or {})
    mismatches: list[dict[str, Any]] = []
    for field, spec in field_role.items():
        if spec.get("role") != "held":
            continue
        if field not in supplied or supplied[field] is None:
            continue
        campaign_value = spec.get("value")
        if _held_values_equal(field, campaign_value, supplied[field]):
            continue
        mismatches.append(_delta(field, campaign_value, supplied[field]))
    payload: dict[str, Any] = {
        "mismatches": mismatches,
        "n_held_mismatches": len(mismatches),
    }
    if mismatches:
        payload["error_code"] = "campaign_basis_mismatch"
        n_pairs = campaign_basis.get("n_pairs")
        if seconds_per_pair is not None and n_pairs is not None:
            payload["live_rerun_quote"] = {
                "n_pairs": n_pairs,
                "seconds_per_pair": seconds_per_pair,
                "estimated_wall_seconds": float(n_pairs) * float(seconds_per_pair),
            }
    return payload
