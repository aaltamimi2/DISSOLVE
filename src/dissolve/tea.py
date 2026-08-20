"""Route-aware TEA/LCA over cached or subprocess-isolated BioSTEAM evidence."""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import math
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
from functools import lru_cache
from importlib.resources import files
from itertools import combinations
from pathlib import Path
from typing import Any, Literal, Optional, Sequence

from . import tea_contracts, tea_polymer_parameters, tea_worker
from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success
from .session import (
    candidate_evidence, current_tool_session, handle_rows, load_handle,
    resolve_candidate_argument,
)
from .tools import _InputError, _polymer_ambiguity_detail

_ASSET = Path(str(files("dissolve").joinpath("data/tea_cache.json.gz")))
_ASSET_SHA256 = "f95dff48c68d57543e472ece51b59f4d807326cee432f1a1138029efcee12173"
_LCA_FACTORS_ASSET = Path(str(
    files("dissolve").joinpath("data/tea_lca_characterization_factors.json")
))
_LCA_FACTORS_ASSET_SHA256 = (
    "adba150af8192c57ccbd1eb3fe550206598ad7eda1f1e4abb49d07d636ea2adf"
)
_CONFIG_FIELDS = (
    "solvent", "target_plastic", "target_plastic_percent",
    "processing_capacity", "energy_case", "dissolution_temperature_c",
    "precipitation_temperature_c", "solvent_price", "solvent_loss_pct",
    "feedstock_distance_km", "dissolution_capacity", "labor_cost",
)
_NUMERIC_FIELDS = set(_CONFIG_FIELDS) - {"solvent", "target_plastic", "energy_case"}
_DESIGN_POINT_PUBLIC_FIELDS = (
    ("target_plastic", "target_polymer"),
    ("solvent", "solvent"),
    ("target_plastic_percent", "target_mass_percent"),
    ("processing_capacity", "processing_capacity_mt_per_yr"),
    ("energy_case", "energy_case"),
    ("dissolution_temperature_c", "dissolution_temperature_c"),
    ("precipitation_temperature_c", "precipitation_temperature_c"),
    ("solvent_price", "solvent_price_usd_per_kg"),
    ("solvent_loss_pct", "solvent_loss_pct"),
    ("feedstock_distance_km", "feedstock_distance_km"),
    ("dissolution_capacity", "dissolution_capacity"),
    ("labor_cost", "labor_cost_usd_per_employee_yr"),
)
_SCENARIO_ALIASES = {
    "target_mass_percent": "target_plastic_percent",
    "processing_capacity_mt_per_yr": "processing_capacity",
    "dissolution_temp_c": "dissolution_temperature_c",
    "precipitation_temp_c": "precipitation_temperature_c",
    "solvent_price_usd_per_kg": "solvent_price",
    "labor_cost_usd_per_employee_yr": "labor_cost",
}
_PUBLIC_REQUIRED_FIELDS = tuple(
    public for _internal, public in _DESIGN_POINT_PUBLIC_FIELDS
)
_D8_REMAINDER_PUBLIC_FIELDS = _PUBLIC_REQUIRED_FIELDS[7:]
_NINE_HELD_PUBLIC_FIELDS = tuple(
    name for name in _PUBLIC_REQUIRED_FIELDS
    if name not in {
        "target_polymer", "solvent", "dissolution_temperature_c",
    }
)
_PUBLIC_FIELD_SOURCE_KEYS = {
    public: frozenset({
        public,
        internal,
        *(
            alias
            for alias, target in _SCENARIO_ALIASES.items()
            if target == internal
        ),
        *(("target_plastic",) if public == "target_polymer" else ()),
    })
    for internal, public in _DESIGN_POINT_PUBLIC_FIELDS
}
# D-8 overlay vocabulary stays the twelve above. These switches change the
# flowsheet (or leftover disposition) and belong in the executed-config key.
# Production values are what the v12 worker hardcoded; existing cache records
# do not carry them and are projected at these defaults.
_FLOWSHEET_SWITCH_DEFAULTS = {
    "sell_leftover_plastic": False,
    "burn_leftover_plastic": False,
    "precipitation_temperature_format": "constant",
    "precipitation_configuration": "integrated heat transfer",
}
_FLOWSHEET_SWITCH_FIELDS = tuple(_FLOWSHEET_SWITCH_DEFAULTS)
_PRECIPITATION_FORMATS = frozenset({"constant", "drop"})
_PRECIPITATION_CONFIGURATIONS = frozenset({
    "integrated heat transfer", "solvent mixing",
})
# Unreached @parameter baselines after load_model. They move MSP; they join
# the serve key the same way the flowsheet switches do. Existing cache
# records do not carry them and are projected at these running values.
_IRR_DEFAULT = 0.10
_INCOME_TAX_DEFAULT = 0.21
_OPERATING_DAYS_DEFAULT = 350.4
_LABOR_BURDEN_DEFAULT = 0.90
_FINANCE_INTEREST_DEFAULT = 0.08
_FINANCE_YEARS_DEFAULT = 10
_FINANCE_FRACTION_DEFAULT = 0.0
_STARTUP_MONTHS_DEFAULT = 3
_STARTUP_FOCFRAC_DEFAULT = 1
_STARTUP_VOCFRAC_DEFAULT = 0.75
_STARTUP_SALESFRAC_DEFAULT = 0.5
_WC_OVER_FCI_DEFAULT = 0.05
_WAREHOUSE_DEFAULT = 0.04
_SITE_DEVELOPMENT_DEFAULT = 0.09
_ADDITIONAL_PIPING_DEFAULT = 0.045
_PRORATABLE_COSTS_DEFAULT = 0.10
_FIELD_EXPENSES_DEFAULT = 0.10
_CONSTRUCTION_DEFAULT = 0.20
_CONTINGENCY_DEFAULT = 0.4
_OTHER_INDIRECT_COSTS_DEFAULT = 0.10
_PROPERTY_INSURANCE_DEFAULT = 0.007
_MAINTENANCE_DEFAULT = 0.03
_FEEDSTOCK_PRICE_USD_PER_KG = 0.01
_CENTRIFUGED_PLASTIC_SOLVENT_CONTENT_PCT = 50.0
_NATURAL_GAS_PRICE_USD_PER_M3 = 4.73 * 35.3146667 / 1e3
_COEFFICIENT_DEFAULTS = {
    "irr": _IRR_DEFAULT,
    "income_tax": _INCOME_TAX_DEFAULT,
    "operating_days": _OPERATING_DAYS_DEFAULT,
    "labor_burden": _LABOR_BURDEN_DEFAULT,
    "finance_interest": _FINANCE_INTEREST_DEFAULT,
    "finance_years": _FINANCE_YEARS_DEFAULT,
    "finance_fraction": _FINANCE_FRACTION_DEFAULT,
    "startup_months": _STARTUP_MONTHS_DEFAULT,
    "startup_FOCfrac": _STARTUP_FOCFRAC_DEFAULT,
    "startup_VOCfrac": _STARTUP_VOCFRAC_DEFAULT,
    "startup_salesfrac": _STARTUP_SALESFRAC_DEFAULT,
    "WC_over_FCI": _WC_OVER_FCI_DEFAULT,
    "warehouse": _WAREHOUSE_DEFAULT,
    "site_development": _SITE_DEVELOPMENT_DEFAULT,
    "additional_piping": _ADDITIONAL_PIPING_DEFAULT,
    "proratable_costs": _PRORATABLE_COSTS_DEFAULT,
    "field_expenses": _FIELD_EXPENSES_DEFAULT,
    "construction": _CONSTRUCTION_DEFAULT,
    "contingency": _CONTINGENCY_DEFAULT,
    "other_indirect_costs": _OTHER_INDIRECT_COSTS_DEFAULT,
    "property_insurance": _PROPERTY_INSURANCE_DEFAULT,
    "maintenance": _MAINTENANCE_DEFAULT,
    "feedstock_price_usd_per_kg": _FEEDSTOCK_PRICE_USD_PER_KG,
    "centrifuged_plastic_solvent_content_pct": (
        _CENTRIFUGED_PLASTIC_SOLVENT_CONTENT_PCT
    ),
}
_INHERIT_OPTIONAL_KEYS = (
    *_FLOWSHEET_SWITCH_FIELDS,
    *_COEFFICIENT_DEFAULTS,
    "natural_gas_price_usd_per_m3",
)
_SCENARIO_ALLOWED_KEYS = frozenset({
    *_PUBLIC_REQUIRED_FIELDS,
    *_CONFIG_FIELDS,
    *_SCENARIO_ALIASES,
    "target_polymer",
    *_FLOWSHEET_SWITCH_FIELDS,
    *_COEFFICIENT_DEFAULTS,
    "natural_gas_price_usd_per_m3",
    "facilities",
    "turbogenerator",
    "lca_cfs",
    "label",
})
_STORED_ROUTE_PRODUCTION_REMAINDER = {
    "solvent_loss_pct": 0.01,
    "feedstock_distance_km": 0.0,
    "dissolution_capacity": 3.0,
    "labor_cost_usd_per_employee_yr": 120_000.0,
}
_SCREENING_SHORTLIST_SOURCES = frozenset({
    "plan_multistage_separation",
    "screen_polymer_separation",
    "explicit",
})
_SCREENING_SHORTLIST_KEYS = frozenset({"source", "handle", "items"})
_SCREENING_ITEM_KEYS = frozenset({
    "target_polymer", "target_plastic", "solvent",
    "dissolution_temperature_c", "dissolution_temp_c",
    "temperature_c",
})
_HELD_PROCESS_BASIS_KEYS = frozenset().union(
    *(
        _PUBLIC_FIELD_SOURCE_KEYS[name]
        for name in _NINE_HELD_PUBLIC_FIELDS
    ),
    _FLOWSHEET_SWITCH_FIELDS,
    _COEFFICIENT_DEFAULTS,
    ("natural_gas_price_usd_per_m3",),
)
_FIELD_NOT_ADJUSTABLE_NAMES = frozenset({
    "centrifuged_precipitate_solvent_content",
    "set_centrifuged_precipitate_solvent_content",
    "screw_press_solvent_content",
    "set_screw_press_solvent_content",
    "boiling_point",
    "set_boiling_point",
})
_FIELD_NOT_IN_MODEL_NAMES = frozenset({
    "recovery", "recovery_fraction", "set_recovery",
})
_EXPERT_SURFACE_ONLY_NAMES = frozenset({
    "tau_h", "dissolution_tau_h", "precipitation_tau_h",
    "rho_kg_m3", "cp_j_per_g_k", "tm_k", "tb_k",
    "oligomer", "precipitation_solubility_wt_wt",
    "precipitation_solubility",
})
_REFERENCE_DESIGN_POINT_ROLE = "context_only_not_route_cost_or_ranking"
_TEA_WORKER_PYTHON_ENV = "DISSOLVE_TEA_PYTHON"
_LIVE_PROCESS_MODEL_RELATIVE_PATH = Path("plastics/strap/process_model.py")
_LIVE_BIOREFINERIES_VERSION = "2.34.10"
_LIVE_RUNTIME_DISTRIBUTIONS = ("biosteam", "thermosteam", "biorefineries")
_LCA_CF_FIELDS = frozenset(
    f"{contributor}_{metric}"
    for contributor in ("natural_gas", "solvent", "water", "electricity")
    for metric in ("gwp", "htc", "htnc", "etox")
)
_ENERGY_CASES = {
    "C1": "on-site boiler and turbogenerator (CHP)",
    "C2": "grid electricity with no on-site utilities",
    "C3": "grid electricity plus on-site boiler",
}
_REQUESTED_METRIC_ALIASES = {
    "minimum_selling_price": "msp", "capital_cost": "tci",
    "total_capital_investment": "tci", "capex": "tci",
    "operating_cost": "aoc", "annual_operating_cost": "aoc", "opex": "aoc",
    "cost_per_kg": "msp", "capital": "tci",
}
_LEGACY_OPERATING_HOURS = 350.4 * 24.0
_METRICS = {
    "msp_usd_per_kg": ("tea", "msp_usd_per_kg", "USD/kg product"),
    "tci_usd": ("tea", "tci_usd", "USD"),
    "aoc_usd_per_yr": ("tea", "aoc_usd_per_yr", "USD/yr"),
    "gwp_kg_co2e_per_kg": ("lca", "gwp_kg_co2e_per_kg", "kg CO2e/kg product"),
    "total_energy_mj_per_kg": ("operations", "total_energy_mj_per_kg", "MJ/kg product"),
}
_LCA_COMPARISON_METRIC_UNITS = {
    "htc_ctuh_per_kg": "CTUh/kg product",
    "htnc_ctuh_per_kg": "CTUh/kg product",
    "etox_ctue_per_kg": "CTUe/kg product",
}
_CACHE_GWP_STATUS_CODE = "cache_gwp_natural_gas_combustion_double_count"
_CACHE_GWP_GRID_STATUS_CODE = "cache_gwp_grid_method_unharmonized"
_CACHE_PROPAGATED_GWP_STATUS_CODE = (
    "cache_propagated_gwp_not_current_for_scientific_use"
)
_CACHE_TOXICITY_STATUS_CODE = "cache_grid_toxicity_method_unharmonized"
_TOXICITY_METRIC_FIELDS = (
    "htc_ctuh_per_kg", "htnc_ctuh_per_kg", "etox_ctue_per_kg",
)
_LCA_STATUS_DEFINITIONS = {
    _CACHE_GWP_STATUS_CODE: {
        "status": "superseded_by_known_defect",
        "defect": "natural_gas_combustion_double_count",
        "reason": (
            "The cache generator applied a natural-gas factor that includes "
            "combustion while the process inventory also counted stack CO2."
        ),
        "served_value_role": "published_cache_record_for_comparability_only",
        "current_scientific_use": "not_admitted",
        "corrected_value_source": (
            "rerun the same complete scenario with engine_mode=live"
        ),
        "corrected_value_substituted_in_cache_payload": False,
    },
    _CACHE_GWP_GRID_STATUS_CODE: {
        "status": "method_unharmonized",
        "issue": "cache_and_live_grid_gwp_methods_differ",
        "reason": (
            "C2 has no natural-gas facilities, so its cache GWP does not carry "
            "the combustion double count. Its cache and live electricity "
            "characterization methods differ and the governing method choice "
            "has not been harmonized."
        ),
        "served_value_role": "published_cache_record_for_comparability_only",
        "current_scientific_use": "not_admitted",
        "comparison_value_source": (
            "rerun the same complete scenario with engine_mode=live"
        ),
        "method_choice_status": "pending",
        "live_value_substituted_in_cache_payload": False,
    },
    _CACHE_PROPAGATED_GWP_STATUS_CODE: {
        "status": "not_current_for_scientific_use",
        "reason": (
            "This comparison, ranking, or aggregate includes cache GWP inputs "
            "that are not admitted for current scientific use. Inspect the "
            "input metric statuses for the governing defect or method gap."
        ),
        "served_value_role": "cache_provenance_comparison_only",
        "current_scientific_use": "not_admitted",
        "corrected_value_source": (
            "rerun every contributing complete scenario with engine_mode=live"
        ),
        "corrected_value_substituted_in_cache_payload": False,
    },
    _CACHE_TOXICITY_STATUS_CODE: {
        "status": "method_unharmonized",
        "issue": "cache_and_live_toxicity_characterization_methods_differ",
        "reason": (
            "The C2/C3 cache generator replaces electricity and steam "
            "characterization rather than completing the live method."
        ),
        "served_value_role": "published_cache_record_for_comparability_only",
        "comparison_to_live": "not_admitted_as_same_method",
    },
}
_ADMITTED_RECORD_METRICS = {
    "msp": ("economics", "msp_usd_per_kg", "USD/kg product"),
    "tci": ("economics", "tci_usd", "USD"),
    "aoc": ("economics", "aoc_usd_per_yr", "USD/yr"),
    "gwp": ("lca", "gwp_kg_co2e_per_kg", "kg CO2e/kg product"),
    "etox": ("lca", "etox_ctue_per_kg", "CTUe/kg product"),
    "htc": ("lca", "htc_ctuh_per_kg", "CTUh/kg product"),
    "htnc": ("lca", "htnc_ctuh_per_kg", "CTUh/kg product"),
    "electricity": (
        "operations", "electricity_consumed_mj_per_kg", "MJ/kg product",
    ),
    "heating": ("operations", "heating_duty_mj_per_kg", "MJ/kg product"),
    "cooling": ("operations", "cooling_duty_mj_per_kg", "MJ/kg product"),
    "energy": ("operations", "total_energy_mj_per_kg", "MJ/kg product"),
}
_ADMITTED_RECORD_METRIC_ALIASES = {
    **_REQUESTED_METRIC_ALIASES,
    "gwp_per_kg": "gwp",
    "global_warming": "gwp",
    "global_warming_potential": "gwp",
    "cost_per_kg": "msp",
    "capital": "tci",
    "ecotoxicity": "etox",
    "human_toxicity_carcinogenic": "htc",
    "human_toxicity_noncarcinogenic": "htnc",
    "total_energy": "energy",
    "energy_per_kg": "energy",
}
_SENSITIVITY_AXIS_ALIASES = {
    item.name: item.aliases
    for item in tea_contracts.TEA_SENSITIVITY_AXIS_REGISTRY
}
_SENSITIVITY_LABEL_TOKEN_BY_AXIS = dict(
    tea_contracts.TEA_SENSITIVITY_CACHE_TOKEN_BY_AXIS,
)
TeaSensitivityAxis = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES,
)
TeaSensitivityLevelSelector = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS,
)
TeaRecordForm = Literal.__getitem__(
    tea_contracts.CANONICAL_TEA_RECORD_FORMS,
)


def _cache_lca_metric_status(
    config: dict[str, Any], lca: dict[str, Any],
) -> dict[str, str]:
    """Bind cache-method qualifications to every affected emitted metric.

    The predicate follows the generation method, not a list of the six route
    labels that exposed it. C1/C3 cache GWP carries the known natural-gas
    boundary defect; C2 carries the separate unresolved grid-method status.
    C2/C3 toxicity values carry their own method qualification. C1 toxicity
    and all financial fields remain unqualified.
    """
    status = {}
    energy_case = str(config.get("energy_case") or "").upper()
    if lca.get("gwp_kg_co2e_per_kg") is not None:
        status["gwp_kg_co2e_per_kg"] = (
            _CACHE_GWP_GRID_STATUS_CODE
            if energy_case == "C2" else _CACHE_GWP_STATUS_CODE
        )
    if energy_case in {"C2", "C3"}:
        status.update({
            field: _CACHE_TOXICITY_STATUS_CODE
            for field in _TOXICITY_METRIC_FIELDS
            if lca.get(field) is not None
        })
    return status


def _lca_status_definitions(
    metric_status: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Return governed definitions for the status references in one row."""
    return {
        code: copy.deepcopy(_LCA_STATUS_DEFINITIONS[code])
        for code in dict.fromkeys(metric_status.values())
    }


def _collect_lca_status_definitions(
    rows: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Collect definitions from result or projected-row status references."""
    codes = {
        str(code)
        for row in rows
        if isinstance(row, dict)
        for code in (row.get("lca_metric_status") or {}).values()
    }
    definitions = {
        str(code): copy.deepcopy(definition)
        for row in rows
        if isinstance(row, dict)
        for code, definition in (
            row.get("lca_status_definitions") or {}
        ).items()
    }
    for code in codes:
        definitions.setdefault(
            code, copy.deepcopy(_LCA_STATUS_DEFINITIONS[code]),
        )
    return definitions


def _lca_method_status_by_energy_case(
    rows: Sequence[dict[str, Any]],
) -> dict[str, dict[str, str | list[str]]]:
    """Bind exceptional metric methods to their stable energy-case identity."""
    by_case: dict[str, dict[str, str | list[str]]] = {}

    def add(case: str, field: str, code: str) -> None:
        case_status = by_case.setdefault(str(case).upper(), {})
        previous = case_status.get(field)
        if previous is None:
            case_status[field] = code
        elif isinstance(previous, list):
            if code not in previous:
                previous.append(code)
        elif previous != code:
            case_status[field] = [previous, code]

    for row in rows:
        if not isinstance(row, dict):
            continue
        for case, status in (
            row.get("lca_method_status_by_energy_case") or {}
        ).items():
            if isinstance(status, dict):
                for field, codes in status.items():
                    if field not in {
                        "gwp_kg_co2e_per_kg", *_TOXICITY_METRIC_FIELDS,
                    }:
                        continue
                    for code in codes if isinstance(codes, list) else [codes]:
                        add(str(case), str(field), str(code))
        config = row.get("config") or {}
        case = row.get("energy_case") or config.get("energy_case")
        status = row.get("lca_metric_status") or {}
        if case and status:
            for field, code in status.items():
                if field in {
                    "gwp_kg_co2e_per_kg", *_TOXICITY_METRIC_FIELDS,
                }:
                    add(str(case), str(field), str(code))
    return dict(sorted(by_case.items()))


def _cache_lca_status_gaps(
    rows: Sequence[dict[str, Any]],
) -> list[str]:
    """State exceptional cache-method qualifications in ordinary language."""
    definitions = _collect_lca_status_definitions(rows)
    gaps = []
    if (
        _CACHE_GWP_STATUS_CODE in definitions
        or _CACHE_GWP_GRID_STATUS_CODE in definitions
        or _CACHE_PROPAGATED_GWP_STATUS_CODE in definitions
    ):
        if (
            _CACHE_GWP_STATUS_CODE in definitions
        ):
            gaps.append(
                "Cached C1/C3 GWP is a published record superseded by a known "
                "natural-gas combustion double count; use engine_mode=live for "
                "corrected complete-scenario values."
            )
        if (
            _CACHE_GWP_GRID_STATUS_CODE in definitions
        ):
            gaps.append(
                "Cached C2 GWP is retained only as the published record. C2 has "
                "no natural-gas combustion double count, but its cache and live "
                "grid-characterization methods are not harmonized."
            )
    if _CACHE_TOXICITY_STATUS_CODE in definitions:
        gaps.append(
            "Cached C2/C3 HTC, HTNC, and ETOX use an unharmonized "
            "electricity/steam characterization method and must not be "
            "compared with live toxicity as though the methods were identical."
        )
    return gaps


def _has_noncurrent_cache_gwp(rows: Sequence[dict[str, Any]]) -> bool:
    """Whether any input GWP is not admitted for current scientific use."""
    superseded_codes = {
        _CACHE_GWP_STATUS_CODE,
        _CACHE_GWP_GRID_STATUS_CODE,
        _CACHE_PROPAGATED_GWP_STATUS_CODE,
    }
    return any(
        code in superseded_codes
        for row in rows if isinstance(row, dict)
        for code in (row.get("lca_metric_status") or {}).values()
    )


def _propagated_gwp_metric_status(
    rows: Sequence[dict[str, Any]], fields: Sequence[str],
) -> dict[str, str]:
    """Qualify each GWP-derived field when any contributing input is stale."""
    if not _has_noncurrent_cache_gwp(rows):
        return {}
    return {
        field: _CACHE_PROPAGATED_GWP_STATUS_CODE for field in fields
    }


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


@lru_cache(maxsize=1)
def cache_payload() -> dict[str, Any]:
    raw = _ASSET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _ASSET_SHA256:
        raise RuntimeError(f"TEA cache checksum mismatch: {digest}")
    return json.loads(gzip.decompress(raw))


def _lca_factor_attribution_summary(
    payload: dict[str, Any],
) -> dict[str, int]:
    """Validate method/citation coverage for every governed factor entry."""

    attribution = payload.get("method_citation_attribution")
    if not isinstance(attribution, dict) or attribution.get("schema") != (
        "dissolve.tea-lca-method-citation-attribution.v1"
    ):
        raise RuntimeError("TEA LCA method/citation attribution is absent")
    unknown = attribution.get("unknown_token")
    if unknown != "UNKNOWN":
        raise RuntimeError("TEA LCA attribution UNKNOWN token is not governed")

    required_fields = {
        "attribution_status",
        "lcia_method_or_package",
        "version_or_year",
        "geography",
        "citation",
        "source_evidence",
        "generator_description",
        "finding",
    }
    declared_fields = attribution.get("required_fields")
    if not isinstance(declared_fields, list) or (
        len(declared_fields) != len(required_fields)
        or set(declared_fields) != required_fields
    ):
        raise RuntimeError("TEA LCA attribution fields are not governed")

    shared = payload.get("shared")
    shared_attribution = attribution.get("shared_entries")
    if not isinstance(shared, dict) or not isinstance(
        shared_attribution, dict
    ) or set(shared_attribution) != set(shared):
        raise RuntimeError(
            "Every shared TEA LCA factor entry must have attribution"
        )

    solvents = payload.get("solvents")
    tier_attribution = attribution.get("solvent_source_tiers")
    if not isinstance(solvents, dict) or not isinstance(
        tier_attribution, dict
    ):
        raise RuntimeError("TEA LCA solvent attribution is absent")
    solvent_tiers: dict[str, str] = {}
    for solvent, row in solvents.items():
        if not isinstance(row, list) or len(row) < 2 or not isinstance(
            row[1], str
        ) or not row[1].strip():
            raise RuntimeError(
                f"TEA LCA solvent source tier is malformed: {solvent}"
            )
        solvent_tiers[str(solvent)] = row[1]
    if set(tier_attribution) != set(solvent_tiers.values()):
        raise RuntimeError(
            "Every TEA LCA solvent source tier must have attribution"
        )

    known_source_hashes = {
        value
        for key, value in (payload.get("provenance") or {}).items()
        if str(key).endswith("_sha256")
        and isinstance(value, str)
        and re.fullmatch(r"[0-9a-f]{64}", value)
    }
    entries = [
        (f"shared.{name}", record)
        for name, record in shared_attribution.items()
    ] + [
        (f"solvents.{name}", tier_attribution[tier])
        for name, tier in solvent_tiers.items()
    ]
    status_counts = {
        "documented": 0,
        "generator_method_and_citation_unrecorded": 0,
    }
    scientific_fields = (
        "lcia_method_or_package",
        "version_or_year",
        "geography",
        "citation",
    )
    for label, record in entries:
        if not isinstance(record, dict) or not required_fields.issubset(record):
            raise RuntimeError(f"TEA LCA attribution is incomplete: {label}")
        status = record.get("attribution_status")
        if status not in status_counts:
            raise RuntimeError(f"TEA LCA attribution status is invalid: {label}")
        for field in (*scientific_fields, "generator_description", "finding"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(
                    f"TEA LCA attribution {field} is absent: {label}"
                )
        if status == "documented":
            if any(record[field] == unknown for field in scientific_fields):
                raise RuntimeError(
                    f"Documented TEA LCA attribution contains UNKNOWN: {label}"
                )
        elif any(record[field] != unknown for field in scientific_fields):
            raise RuntimeError(
                "Unrecorded TEA LCA attribution must use explicit UNKNOWN: "
                + label
            )
        evidence = record.get("source_evidence")
        if not isinstance(evidence, list) or not evidence:
            raise RuntimeError(f"TEA LCA source evidence is absent: {label}")
        for index, source in enumerate(evidence):
            source_label = f"{label}.source_evidence[{index}]"
            if not isinstance(source, dict) or any(
                not isinstance(source.get(field), str)
                or not source[field].strip()
                for field in ("source_file", "source_sha256", "source_location")
            ):
                raise RuntimeError(
                    f"TEA LCA source evidence is malformed: {source_label}"
                )
            if source["source_sha256"] not in known_source_hashes:
                raise RuntimeError(
                    f"TEA LCA source hash is not governed: {source_label}"
                )
        status_counts[status] += 1
    return {
        "entry_count": len(entries),
        "indicator_factor_count": len(entries) * len(
            payload.get("metric_order") or []
        ),
        "documented_entry_count": status_counts["documented"],
        "generator_method_and_citation_unrecorded_entry_count": (
            status_counts["generator_method_and_citation_unrecorded"]
        ),
    }


@lru_cache(maxsize=1)
def lca_factor_payload() -> dict[str, Any]:
    """Load the governed cache-generator factor table by content digest."""
    raw = _LCA_FACTORS_ASSET.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != _LCA_FACTORS_ASSET_SHA256:
        raise RuntimeError(
            f"TEA LCA characterization-factor checksum mismatch: {digest}"
        )
    payload = json.loads(raw)
    if payload.get("schema") != (
        "dissolve.tea-lca-characterization-factors.v1"
    ):
        raise RuntimeError("Unsupported TEA LCA characterization-factor schema")
    _lca_factor_attribution_summary(payload)
    return payload


def _validated_lca_cfs(value: Any) -> dict[str, float]:
    """Validate an explicit live-run override without silently filling it."""
    if not isinstance(value, dict):
        raise ValueError("lca_cfs must be an object of characterization factors")
    unknown = sorted(set(value) - _LCA_CF_FIELDS)
    if unknown:
        raise ValueError("Unsupported lca_cfs fields: " + ", ".join(unknown))
    return {key: _finite(raw, f"lca_cfs.{key}") for key, raw in value.items()}


def _default_live_lca_cfs(config: dict[str, Any]) -> dict[str, float]:
    """Rebuild the factor payload recorded by the admitted cache generator."""
    payload = lca_factor_payload()
    order = [str(item).casefold() for item in payload["metric_order"]]
    if order != ["gwp", "htc", "htnc", "etox"]:
        raise RuntimeError("Unexpected TEA LCA characterization-factor order")
    shared = payload["shared"]
    result: dict[str, float] = {}
    for prefix, source in (
        ("natural_gas", "natural_gas"),
        ("water", "water"),
    ):
        values = shared[source]["values"]
        result.update({
            f"{prefix}_{metric}": float(value)
            for metric, value in zip(order, values)
        })

    solvent_name = str(config.get("solvent") or "")
    solvent_rows = payload.get("solvents") or {}
    solvent_row = next(
        (
            row for name, row in solvent_rows.items()
            if _key(name) == _key(solvent_name)
        ),
        None,
    )
    if solvent_row is None:
        raise ValueError(
            "No governed cache-generator LCA factors are admitted for "
            f"{solvent_name or '(missing solvent)'}; supply an explicit "
            "lca_cfs mapping for a live comparison."
        )
    base_values = solvent_row[2:]
    result.update({
        f"solvent_{metric}": float(base)
        for metric, base in zip(order, base_values)
    })

    if str(config.get("energy_case") or "").upper() in {"C2", "C3"}:
        values = shared["grid_electricity"]["values"]
        result.update({
            f"electricity_{metric}": float(value)
            for metric, value in zip(order, values)
        })
    return result


def _declared_lca_factor_bases() -> dict[str, str]:
    """Translate governed source declarations into BioSTEAM call bases.

    The strings in the factor asset are deliberately load-bearing. A changed
    declaration must fail here rather than being fed through a call whose
    default basis happens to look compatible.
    """
    payload = lca_factor_payload()
    shared = payload["shared"]
    declarations = {
        "natural_gas": shared["natural_gas"]["basis"],
        "water": shared["water"]["basis"],
        "solvent_transport": shared["solvent_transport_offset"]["basis"],
        "electricity": shared["grid_electricity"]["basis"],
    }
    expected = {
        "natural_gas": "per kg natural-gas feed",
        "water": "per kg purchased makeup-water feed",
        "solvent_transport": "per kg solvent feed",
        "electricity": "per MJ purchased electricity",
    }
    mismatches = {
        key: {"expected": expected[key], "declared": declarations[key]}
        for key in expected
        if declarations[key] != expected[key]
    }
    solvent_fields = list(payload.get("solvent_entry_fields") or [])
    expected_solvent_fields = [
        "generator_identity", "source_tier",
        "gwp_per_kg_solvent", "htc_per_kg_solvent",
        "htnc_per_kg_solvent", "etox_per_kg_solvent",
    ]
    if solvent_fields != expected_solvent_fields:
        mismatches["solvent"] = {
            "expected": expected_solvent_fields,
            "declared": solvent_fields,
        }
    if mismatches:
        raise RuntimeError(
            "TEA LCA factor basis is not admitted for application: "
            + json.dumps(mismatches, sort_keys=True, separators=(",", ":"))
        )
    return {
        "natural_gas": "kg",
        "water": "kg",
        "solvent": "kg",
        "electricity": "MJ",
    }


def _governed_live_lca_application(
    config: dict[str, Any],
) -> tuple[dict[str, float], dict[str, str], dict[str, float], dict[str, Any]]:
    """Select only method-compatible governed factors for a live run.

    The cache-generator GWP table spans a different system boundary: its
    natural-gas value includes combustion while this process already records
    stack CO2 directly. All generator GWP substitutions therefore stay out of
    the coherent model-native GWP calculation. C2/C3 toxicity factors are also
    held because applying them would replace existing electricity/steam
    methods, not fill omitted characterization. C1 toxicity has no direct
    process inventory counterpart and can be restored without either conflict.
    """
    recorded = _default_live_lca_cfs(config)
    bases = _declared_lca_factor_bases()
    energy_case = str(config.get("energy_case") or "C1").upper()
    metric_fields = {
        "gwp_kg_co2e_per_kg": "GWP",
        "htc_ctuh_per_kg": "HTC",
        "htnc_ctuh_per_kg": "HTNC",
        "etox_ctue_per_kg": "ETOX",
    }
    if energy_case == "C1":
        applied = {
            key: value for key, value in recorded.items()
            if not key.endswith("_gwp")
        }
        metric_sources = {
            "gwp_kg_co2e_per_kg": "live_process_model_native_boundary",
            **{
                field: "governed_cache_generator_table"
                for field in metric_fields
                if field != "gwp_kg_co2e_per_kg"
            },
        }
        offsets = dict(zip(
            ("gwp", "htc", "htnc", "etox"),
            lca_factor_payload()["shared"]["solvent_transport_offset"][
                "values"
            ],
        ))
        offsets.pop("gwp")
        notes = [
            "Cache-generator GWP factors were not applied: their production-"
            "plus-use natural-gas boundary overlaps the model's direct stack-"
            "CO2 inventory; live GWP retains the coherent model-native boundary.",
            "C1 HTC, HTNC, and ETOX factors were applied because those process "
            "impact inventories are explicitly zero and have no direct-inventory "
            "counterpart.",
        ]
        source = "mixed_live_model_and_governed_table"
    else:
        applied = {}
        offsets = {}
        metric_sources = {
            field: "live_process_model_native_unharmonized"
            for field in metric_fields
        }
        notes = [
            "Cache-generator GWP factors were not applied because their system "
            "boundary overlaps the model's direct stack-CO2 inventory.",
            "Cache-generator C2/C3 toxicity factors were not applied because "
            "they replace, rather than complete, the model's existing electricity "
            "and steam characterization; no harmonized method has been admitted.",
        ]
        source = "live_process_model_native_unharmonized"
    provenance = {
        "source": source,
        "factor_asset_sha256": _LCA_FACTORS_ASSET_SHA256,
        "cache_generator_commit": lca_factor_payload()["provenance"][
            "cache_generator_commit"
        ],
        "metric_factor_sources": metric_sources,
        "factor_application_notes": notes,
    }
    return applied, bases, offsets, provenance


def _records() -> list[dict[str, Any]]:
    return list(cache_payload().get("records") or [])


def _solvent_assumption(solvent: str) -> Optional[dict[str, Any]]:
    target = thermo.resolve_solvent(solvent)
    if target is None:
        return None
    for row in (cache_payload().get("solvent_assumptions") or {}).get("records") or []:
        for candidate in (row.get("name_cosmobase"), row.get("name_biosteam")):
            if candidate and thermo.resolve_solvent(str(candidate)) == target:
                return dict(row)
    return None


def _record_for_pair(polymer: str, solvent: str) -> Optional[dict[str, Any]]:
    matches = [
        item for item in _records()
        if _key(item["config"].get("target_plastic")) == _key(polymer)
        and _key(item["config"].get("solvent")) == _key(solvent)
        and str(item["config"].get("energy_case") or "").upper() == "C1"
    ]
    preferred = [item for item in matches if str(item.get("label", "")).endswith("route-c1")]
    return (preferred or matches or [None])[0]


def _public_design_point(
    config: dict[str, Any], *, record_id: Optional[str] = None,
) -> dict[str, Any]:
    """Project one exact cache key into the stored-route public vocabulary."""
    point = {
        public: copy.deepcopy(config.get(internal))
        for internal, public in _DESIGN_POINT_PUBLIC_FIELDS
    }
    if record_id is not None:
        point = {"reference_record_id": record_id, **point}
    return point


PROCESS_CONFIRM_TOOLS = frozenset({
    "evaluate_tea_lca_scenarios",
    "analyze_tea_sensitivity",
})
_TOOL1_PROCESS_ROWS_TOOLS = frozenset({
    "evaluate_tea_lca_scenarios",
    "lookup_admitted_process_records",
})
_SHEET_REQUIRED_FIELDS = (
    "target_polymer", "solvent", "dissolution_temperature_c",
)


def public_process_field_names(*, energy_case: str = "C1") -> tuple[str, ...]:
    """Public process_config names the confirmation sheet can edit."""
    names = tuple(public for _internal, public in _DESIGN_POINT_PUBLIC_FIELDS)
    names += _FLOWSHEET_SWITCH_FIELDS
    names += (
        "irr",
        "income_tax",
        "operating_days",
        "labor_burden",
        "finance_interest",
        "finance_years",
        "finance_fraction",
        "startup_months",
        "startup_FOCfrac",
        "startup_VOCfrac",
        "startup_salesfrac",
        "WC_over_FCI",
        "warehouse",
        "site_development",
        "additional_piping",
        "proratable_costs",
        "field_expenses",
        "construction",
        "contingency",
        "other_indirect_costs",
        "property_insurance",
        "maintenance",
        "feedstock_price_usd_per_kg",
        "centrifuged_plastic_solvent_content_pct",
    )
    if str(energy_case or "C1").upper() in {"C1", "C3"}:
        names += ("natural_gas_price_usd_per_m3",)
    return names


def first_run_sheet_defaults(*, energy_case: str = "C1") -> dict[str, Any]:
    """Named first-run defaults. Not evaluate silent fill.

    Precipitation is the generic-factory 35 °C. Headless evaluate does not
    apply a 25 °C leftover or a cache-pair overlay.
    """
    case = str(energy_case or "C1").upper() or "C1"
    defaults: dict[str, Any] = {
        "target_mass_percent": 60.0,
        "processing_capacity_mt_per_yr": 20_000.0,
        "energy_case": case if case in _ENERGY_CASES else "C1",
        "precipitation_temperature_c": (
            tea_polymer_parameters.GENERIC_FACTORY_PRECIPITATION_T_C
        ),
        "solvent_loss_pct": 0.01,
        "feedstock_distance_km": 0.0,
        "dissolution_capacity": 3.0,
        "labor_cost_usd_per_employee_yr": 120_000.0,
        **dict(_FLOWSHEET_SWITCH_DEFAULTS),
        **dict(_COEFFICIENT_DEFAULTS),
    }
    if defaults["energy_case"] in {"C1", "C3"}:
        defaults["natural_gas_price_usd_per_m3"] = _NATURAL_GAS_PRICE_USD_PER_M3
    return defaults


def seed_public_process_config(
    scenario: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Map a scenario object onto the public sheet vocabulary."""
    raw = dict(scenario or {})
    energy = str(raw.get("energy_case") or "C1").upper() or "C1"
    seeded = first_run_sheet_defaults(energy_case=energy)
    internal_to_public = dict(_DESIGN_POINT_PUBLIC_FIELDS)
    public_names = set(public_process_field_names(energy_case=energy)) | {
        "target_polymer", "solvent",
    }
    for key, value in raw.items():
        if value is None or str(key).startswith("_"):
            continue
        if key in internal_to_public:
            public = internal_to_public[key]
        elif key in _SCENARIO_ALIASES:
            internal = _SCENARIO_ALIASES[key]
            public = internal_to_public.get(internal, key)
        elif key in public_names:
            public = key
        else:
            continue
        seeded[public] = value
    if energy == "C2":
        seeded.pop("natural_gas_price_usd_per_m3", None)
    return seeded


def missing_public_process_fields(config: dict[str, Any]) -> list[str]:
    missing = []
    for field in _SHEET_REQUIRED_FIELDS:
        value = config.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


_STANDING_PREVIEW_SETPOINTS = (
    "dissolution_temperature_c",
    "precipitation_temperature_c",
    "dissolution_capacity",
)


def _sheet_standing_unavailable(
    reason: str, *, plastics_root: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "badge": "unavailable",
        "can_cite_as_validated_process": False,
        "reason": reason,
        "plastics_root": plastics_root,
        "passed_plastics_root": False,
    }


def sheet_standing_preview(buffer: dict[str, Any]) -> dict[str, Any]:
    """Standing badge for the confirmation sheet. Not an MSP preview.

    Always inspects the resolved plastics root. Calling
    ``live_parameter_standing_payload`` with only row/solvent/config
    skips package inspection and would cite PE/Toluene 95/35/3 as
    validated on that thin path.
    """
    polymer = str(buffer.get("target_polymer") or "").strip()
    solvent = str(buffer.get("solvent") or "").strip()
    if not polymer or not solvent:
        missing = [
            name for name, value in (
                ("target_polymer", polymer),
                ("solvent", solvent),
            )
            if not value
        ]
        return {
            "badge": "incomplete",
            "can_cite_as_validated_process": False,
            "reason": "incomplete_process_config",
            "missing": missing,
            "plastics_root": None,
            "passed_plastics_root": False,
        }
    row = tea_polymer_parameters.polymer_row(polymer)
    if row is None:
        return _sheet_standing_unavailable(
            "unsupported_live_target", plastics_root=None,
        )
    root = tea_polymer_parameters.resolve_plastics_path()
    if root is None:
        return _sheet_standing_unavailable("missing_plastics_root")
    layout = tea_polymer_parameters.plastics_layout_diagnosis(root)
    if layout["layout"] == "unrecognised":
        return _sheet_standing_unavailable(
            "missing_package", plastics_root=str(root),
        )
    setpoints = {
        key: buffer[key]
        for key in _STANDING_PREVIEW_SETPOINTS
        if key in buffer and buffer[key] is not None
    }
    try:
        tea_polymer_parameters.inspect_cited_strap_sources(root)
        disagreements = tea_polymer_parameters.surface_package_disagreements(
            row, solvent, root,
        )
    except tea_polymer_parameters.PackageInspectionError as error:
        return _sheet_standing_unavailable(
            error.diagnostic_reason, plastics_root=str(root),
        )
    payload = tea_polymer_parameters.live_parameter_standing_payload(
        row,
        solvent=solvent,
        config=setpoints,
        package_disagreements=disagreements,
        plastics_root=root,
    )
    standing = dict(payload.get("live_parameter_standing") or {})
    validated = bool(payload.get("can_cite_as_validated_process"))
    return {
        "badge": "validated" if validated else "provisional",
        "can_cite_as_validated_process": validated,
        "reason": None if validated else "provisional_process",
        "plastics_root": str(root),
        "passed_plastics_root": True,
        "live_parameter_standing": standing,
        "provisional_parameters": list(
            standing.get("provisional_parameters") or [],
        ),
    }


def _design_point_value_equal(field: str, left: Any, right: Any) -> bool:
    if field in {"target_plastic", "solvent"}:
        return _key(left) == _key(right)
    if field == "energy_case":
        return str(left or "").upper() == str(right or "").upper()
    try:
        return math.isclose(
            float(left), float(right), rel_tol=0, abs_tol=1e-9,
        )
    except (TypeError, ValueError):
        return left == right


def _reference_design_point_contract(
    requested_config: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Disclose same-pair cache rows without substituting their coordinates."""
    if _cache_index().get(_config_key(requested_config)) is not None:
        return None
    pair_records = [
        record for record in _records()
        if _key(record["config"].get("target_plastic"))
        == _key(requested_config.get("target_plastic"))
        and _key(record["config"].get("solvent"))
        == _key(requested_config.get("solvent"))
    ]
    if not pair_records:
        return None

    references = []
    differences = []
    for record in pair_records:
        record_id = str(record.get("label") or "")
        reference_config = record["config"]
        differing_fields = []
        for internal, public in _DESIGN_POINT_PUBLIC_FIELDS:
            requested_value = requested_config.get(internal)
            reference_value = reference_config.get(internal)
            if _design_point_value_equal(
                internal, requested_value, reference_value,
            ):
                continue
            differing_fields.append(public)
        if not differing_fields:
            continue
        references.append(_public_design_point(
            reference_config, record_id=record_id,
        ))
        differences.append({
            "reference_record_id": record_id,
            "differing_fields": differing_fields,
        })

    if not references:
        return None
    default_record = _record_for_pair(
        str(requested_config.get("target_plastic") or ""),
        str(requested_config.get("solvent") or ""),
    )
    default_record_id = str((default_record or {}).get("label") or "")
    order = sorted(
        range(len(references)),
        key=lambda index: (
            differences[index]["reference_record_id"] != default_record_id,
            len(differences[index]["differing_fields"]),
            differences[index]["reference_record_id"],
        ),
    )
    references = [references[index] for index in order]
    differences = [differences[index] for index in order]
    return {
        "requested_stage_basis": _public_design_point(requested_config),
        "available_reference_design_points": references,
        "basis_differences": differences,
        "example_reference_design_point": references[0],
    }


def _resolve_polymer(value: Any, field: str = "target_polymer") -> str:
    supplied = str(value or "").strip()
    ambiguity = _polymer_ambiguity_detail(supplied, field)
    if ambiguity is not None:
        raise _InputError(
            "ambiguous_polymer",
            f"{field} names a polymer family; choose one member.",
            **ambiguity,
        )
    expanded = thermo.expand_polymer_identity(supplied)
    if len(expanded) != 1:
        raise ValueError(f"Unsupported target polymer: {supplied or '(missing)'}")
    return expanded[0]


def _expand_polymers(values: Sequence[Any], field: str) -> list[str]:
    """Flatten a collection-valued polymer field without choosing a member."""
    resolved: list[str] = []
    for value in values:
        supplied = str(value or "").strip()
        members = thermo.expand_polymer_identity(supplied)
        if not members:
            raise ValueError(f"Unsupported {field}: {supplied or '(missing)'}")
        for member in members:
            if member not in resolved:
                resolved.append(member)
    return resolved


class _ScenarioInputError(ValueError):
    """A classified user-supplied scenario value cannot enter TEA."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        **details: Any,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.details = details


def _known_assumption(identity: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Join an exact known identity to the admitted TEA row by stable CAS."""
    cas_number = str(identity.get("cas_number") or "").strip()
    if not cas_number:
        return None
    return next(
        (
            dict(row)
            for row in (
                (cache_payload().get("solvent_assumptions") or {}).get(
                    "records"
                ) or []
            )
            if str(row.get("cas_number") or "").strip() == cas_number
        ),
        None,
    )


def _resolve_tea_solvent(value: Any) -> dict[str, Any]:
    """Resolve user, public, and BioSTEAM identities without conflating them."""
    supplied = str(value or "").strip()
    resolved = thermo.resolve_solvent(supplied)
    if resolved is None:
        known_identity = thermo.identify_known_solvent(supplied)
        fitted_status = thermo.get_fitted_solvent_status(supplied)
        if fitted_status == "excluded_data_quality":
            canonical = str(
                (known_identity or {}).get("solvent_name") or supplied
            )
            raise _ScenarioInputError(
                f"Solvent '{supplied or '(missing)'}' is recognized as "
                f"{canonical} but is excluded from the fitted solvent model "
                "pending data-quality review; choose another solvent.",
                error_code="solvent_excluded_data_quality",
                requested_solvent=supplied or None,
                canonical_solvent=canonical,
                solvent_support_status="known_but_excluded",
            )
        if known_identity is not None:
            canonical = str(
                known_identity.get("solvent_name") or supplied
            )
            admitted_row = _known_assumption(known_identity)
            registered_identity = thermo.resolve_solvent(str(
                known_identity.get("cosmobase_name")
                or known_identity.get("solvent_name") or ""
            ))
            if admitted_row is not None and registered_identity is not None:
                message = (
                    f"Solvent token '{supplied or '(missing)'}' identifies "
                    f"{canonical}, but that token is not a registered TEA "
                    f"solvent name; use '{canonical}' or another registered alias."
                )
                status = "known_identity_unregistered_alias"
                error_code = "unregistered_solvent_alias"
            else:
                message = (
                    f"Solvent '{supplied or '(missing)'}' is a known chemical "
                    f"identity ({canonical}) but is not admitted to the fitted "
                    "TEA solvent model; choose an admitted solvent."
                )
                status = "known_but_not_modelled"
                error_code = "solvent_not_admitted"
            raise _ScenarioInputError(
                message,
                error_code=error_code,
                requested_solvent=supplied or None,
                canonical_solvent=canonical,
                solvent_support_status=status,
            )
        raise _ScenarioInputError(
            f"Unknown solvent name '{supplied or '(missing)'}'; correct the "
            "spelling or use a registered solvent name.",
            error_code="unknown_solvent",
            requested_solvent=supplied or None,
            solvent_support_status="unknown_name",
        )

    canonical = thermo.canonical_solvent_name(resolved)
    identity = thermo.identify_known_solvent(resolved) or {}
    assumption = _solvent_assumption(resolved)
    engine_name = str(
        (assumption or {}).get("name_biosteam")
        or canonical
    ).strip()
    cas_number = str(
        (assumption or {}).get("cas_number")
        or identity.get("cas_number")
        or ""
    ).strip()
    return {
        "requested": supplied,
        "resolved": resolved,
        "canonical": canonical,
        "engine": engine_name,
        "cas_number": cas_number,
        "assumption": assumption,
    }


def _resolve_solvent(value: Any) -> str:
    """Return the canonical public identity for backward-compatible callers."""
    return str(_resolve_tea_solvent(value)["canonical"])


class _MissingScenarioBasis(_ScenarioInputError):
    """A valid route condition lacks an admitted process input."""


def _coefficient_defaults_for(config: dict[str, Any]) -> dict[str, float]:
    defaults = dict(_COEFFICIENT_DEFAULTS)
    energy = str(config.get("energy_case") or "").upper()
    if energy in {"C1", "C3"}:
        defaults["natural_gas_price_usd_per_m3"] = _NATURAL_GAS_PRICE_USD_PER_M3
    return defaults


def _project_coefficients(config: dict[str, Any]) -> dict[str, float]:
    """Fill absent MSP-moving coefficients with load_model baselines."""
    projected = {}
    for key, default in _coefficient_defaults_for(config).items():
        value = config.get(key)
        projected[key] = default if value is None else float(value)
    return projected


def _refuse_reserved_process_fields(supplied: dict[str, Any]) -> None:
    for key in supplied:
        name = str(key)
        if name in _FIELD_NOT_ADJUSTABLE_NAMES:
            raise _ScenarioInputError(
                f"{name} is commented out on BaselineSTRAPProcess; it is "
                "not a setter on this instance.",
                error_code="field_not_adjustable",
                field=name,
            )
        if name in _FIELD_NOT_IN_MODEL_NAMES:
            raise _ScenarioInputError(
                "Recovery is an output of the unit operations, not an "
                "input; there is no set_recovery on the governed model.",
                error_code="field_not_in_model",
                field=name,
            )
        if name in _EXPERT_SURFACE_ONLY_NAMES:
            raise _ScenarioInputError(
                f"{name} lives on the expert surface "
                "src/dissolve/tea_polymer_parameters.py, not the process "
                "sheet.",
                error_code="expert_surface_only",
                field=name,
                expert_surface="src/dissolve/tea_polymer_parameters.py",
            )
        if name == "polymer_ratio":
            raise _ScenarioInputError(
                "polymer_ratio is registered only on a multistep instance; "
                "pair TEA is single-step.",
                error_code="field_not_on_this_instance",
                field="polymer_ratio",
            )
        if name == "precipitation_temperature_drop_pct":
            raise _ScenarioInputError(
                "precipitation_temperature_drop_pct exists only when "
                "precipitation_temperature_format is 'drop'.",
                error_code="field_not_on_this_instance",
                field="precipitation_temperature_drop_pct",
            )


def _validated_coefficients(
    supplied: dict[str, Any], *, energy_case: str,
) -> dict[str, float]:
    coefficients = dict(_COEFFICIENT_DEFAULTS)
    if "irr" in supplied:
        irr = _finite(supplied["irr"], "irr")
        if irr >= 1:
            raise _ScenarioInputError(
                "irr is a fraction (0.10 is 10 percent), not a percent "
                "integer.",
                error_code="invalid_scenario",
                field="irr",
                supplied=supplied["irr"],
            )
        if not 0 < irr < 1:
            raise _ScenarioInputError(
                "irr must be a fraction between 0 and 1 exclusive.",
                error_code="invalid_scenario",
                field="irr",
                supplied=supplied["irr"],
            )
        coefficients["irr"] = irr
    if "income_tax" in supplied:
        tax = _finite(supplied["income_tax"], "income_tax")
        if tax >= 1:
            raise _ScenarioInputError(
                "income_tax is a fraction (0.21 is 21 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="income_tax",
                supplied=supplied["income_tax"],
            )
        if not 0 <= tax < 1:
            raise _ScenarioInputError(
                "income_tax must be a fraction in [0, 1).",
                error_code="invalid_scenario",
                field="income_tax",
                supplied=supplied["income_tax"],
            )
        coefficients["income_tax"] = tax
    if "operating_days" in supplied:
        days = _finite(supplied["operating_days"], "operating_days")
        if days <= 0:
            raise _ScenarioInputError(
                "operating_days must be positive.",
                error_code="invalid_scenario",
                field="operating_days",
                supplied=supplied["operating_days"],
            )
        coefficients["operating_days"] = days
    if "labor_burden" in supplied:
        burden = _finite(supplied["labor_burden"], "labor_burden")
        if burden < 0:
            raise _ScenarioInputError(
                "labor_burden must be nonnegative.",
                error_code="invalid_scenario",
                field="labor_burden",
                supplied=supplied["labor_burden"],
            )
        coefficients["labor_burden"] = burden
    if "finance_interest" in supplied:
        rate = _finite(supplied["finance_interest"], "finance_interest")
        if rate >= 1:
            raise _ScenarioInputError(
                "finance_interest is a fraction (0.08 is 8 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="finance_interest",
                supplied=supplied["finance_interest"],
            )
        if not 0 <= rate < 1:
            raise _ScenarioInputError(
                "finance_interest must be a fraction in [0, 1).",
                error_code="invalid_scenario",
                field="finance_interest",
                supplied=supplied["finance_interest"],
            )
        coefficients["finance_interest"] = rate
    if "finance_years" in supplied:
        years = _finite(supplied["finance_years"], "finance_years")
        if years <= 0:
            raise _ScenarioInputError(
                "finance_years must be positive.",
                error_code="invalid_scenario",
                field="finance_years",
                supplied=supplied["finance_years"],
            )
        if not float(years).is_integer():
            raise _ScenarioInputError(
                "finance_years must be a whole number of years.",
                error_code="invalid_scenario",
                field="finance_years",
                supplied=supplied["finance_years"],
            )
        coefficients["finance_years"] = years
    if "finance_fraction" in supplied:
        fraction = _finite(supplied["finance_fraction"], "finance_fraction")
        if fraction > 1:
            raise _ScenarioInputError(
                "finance_fraction is a fraction (0.4 is 40 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="finance_fraction",
                supplied=supplied["finance_fraction"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "finance_fraction must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="finance_fraction",
                supplied=supplied["finance_fraction"],
            )
        coefficients["finance_fraction"] = fraction
    if "startup_months" in supplied:
        months = _finite(supplied["startup_months"], "startup_months")
        if months < 0:
            raise _ScenarioInputError(
                "startup_months must be nonnegative.",
                error_code="invalid_scenario",
                field="startup_months",
                supplied=supplied["startup_months"],
            )
        if months > 12:
            raise _ScenarioInputError(
                "startup_months must be at most 12.",
                error_code="invalid_scenario",
                field="startup_months",
                supplied=supplied["startup_months"],
            )
        coefficients["startup_months"] = months
    if "startup_FOCfrac" in supplied:
        fraction = _finite(supplied["startup_FOCfrac"], "startup_FOCfrac")
        if fraction > 1:
            raise _ScenarioInputError(
                "startup_FOCfrac is a fraction (1 is 100 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="startup_FOCfrac",
                supplied=supplied["startup_FOCfrac"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "startup_FOCfrac must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="startup_FOCfrac",
                supplied=supplied["startup_FOCfrac"],
            )
        coefficients["startup_FOCfrac"] = fraction
    if "startup_VOCfrac" in supplied:
        fraction = _finite(supplied["startup_VOCfrac"], "startup_VOCfrac")
        if fraction > 1:
            raise _ScenarioInputError(
                "startup_VOCfrac is a fraction (0.75 is 75 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="startup_VOCfrac",
                supplied=supplied["startup_VOCfrac"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "startup_VOCfrac must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="startup_VOCfrac",
                supplied=supplied["startup_VOCfrac"],
            )
        coefficients["startup_VOCfrac"] = fraction
    if "startup_salesfrac" in supplied:
        fraction = _finite(supplied["startup_salesfrac"], "startup_salesfrac")
        if fraction > 1:
            raise _ScenarioInputError(
                "startup_salesfrac is a fraction (0.5 is 50 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="startup_salesfrac",
                supplied=supplied["startup_salesfrac"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "startup_salesfrac must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="startup_salesfrac",
                supplied=supplied["startup_salesfrac"],
            )
        coefficients["startup_salesfrac"] = fraction
    if "WC_over_FCI" in supplied:
        fraction = _finite(supplied["WC_over_FCI"], "WC_over_FCI")
        if fraction > 1:
            raise _ScenarioInputError(
                "WC_over_FCI is a fraction (0.05 is 5 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="WC_over_FCI",
                supplied=supplied["WC_over_FCI"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "WC_over_FCI must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="WC_over_FCI",
                supplied=supplied["WC_over_FCI"],
            )
        coefficients["WC_over_FCI"] = fraction
    if "warehouse" in supplied:
        fraction = _finite(supplied["warehouse"], "warehouse")
        if fraction > 1:
            raise _ScenarioInputError(
                "warehouse is a fraction (0.04 is 4 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="warehouse",
                supplied=supplied["warehouse"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "warehouse must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="warehouse",
                supplied=supplied["warehouse"],
            )
        coefficients["warehouse"] = fraction
    if "site_development" in supplied:
        fraction = _finite(supplied["site_development"], "site_development")
        if fraction > 1:
            raise _ScenarioInputError(
                "site_development is a fraction (0.09 is 9 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="site_development",
                supplied=supplied["site_development"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "site_development must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="site_development",
                supplied=supplied["site_development"],
            )
        coefficients["site_development"] = fraction
    if "additional_piping" in supplied:
        fraction = _finite(supplied["additional_piping"], "additional_piping")
        if fraction > 1:
            raise _ScenarioInputError(
                "additional_piping is a fraction (0.045 is 4.5 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="additional_piping",
                supplied=supplied["additional_piping"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "additional_piping must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="additional_piping",
                supplied=supplied["additional_piping"],
            )
        coefficients["additional_piping"] = fraction
    if "proratable_costs" in supplied:
        fraction = _finite(supplied["proratable_costs"], "proratable_costs")
        if fraction > 1:
            raise _ScenarioInputError(
                "proratable_costs is a fraction (0.10 is 10 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="proratable_costs",
                supplied=supplied["proratable_costs"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "proratable_costs must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="proratable_costs",
                supplied=supplied["proratable_costs"],
            )
        coefficients["proratable_costs"] = fraction
    if "field_expenses" in supplied:
        fraction = _finite(supplied["field_expenses"], "field_expenses")
        if fraction > 1:
            raise _ScenarioInputError(
                "field_expenses is a fraction (0.10 is 10 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="field_expenses",
                supplied=supplied["field_expenses"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "field_expenses must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="field_expenses",
                supplied=supplied["field_expenses"],
            )
        coefficients["field_expenses"] = fraction
    if "construction" in supplied:
        fraction = _finite(supplied["construction"], "construction")
        if fraction > 1:
            raise _ScenarioInputError(
                "construction is a fraction (0.20 is 20 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="construction",
                supplied=supplied["construction"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "construction must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="construction",
                supplied=supplied["construction"],
            )
        coefficients["construction"] = fraction
    if "contingency" in supplied:
        fraction = _finite(supplied["contingency"], "contingency")
        if fraction > 1:
            raise _ScenarioInputError(
                "contingency is a fraction (0.4 is 40 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="contingency",
                supplied=supplied["contingency"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "contingency must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="contingency",
                supplied=supplied["contingency"],
            )
        coefficients["contingency"] = fraction
    if "other_indirect_costs" in supplied:
        fraction = _finite(supplied["other_indirect_costs"], "other_indirect_costs")
        if fraction > 1:
            raise _ScenarioInputError(
                "other_indirect_costs is a fraction (0.10 is 10 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="other_indirect_costs",
                supplied=supplied["other_indirect_costs"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "other_indirect_costs must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="other_indirect_costs",
                supplied=supplied["other_indirect_costs"],
            )
        coefficients["other_indirect_costs"] = fraction
    if "property_insurance" in supplied:
        fraction = _finite(supplied["property_insurance"], "property_insurance")
        if fraction > 1:
            raise _ScenarioInputError(
                "property_insurance is a fraction (0.007 is 0.7 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="property_insurance",
                supplied=supplied["property_insurance"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "property_insurance must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="property_insurance",
                supplied=supplied["property_insurance"],
            )
        coefficients["property_insurance"] = fraction
    if "maintenance" in supplied:
        fraction = _finite(supplied["maintenance"], "maintenance")
        if fraction > 1:
            raise _ScenarioInputError(
                "maintenance is a fraction (0.03 is 3 percent), not a "
                "percent integer.",
                error_code="invalid_scenario",
                field="maintenance",
                supplied=supplied["maintenance"],
            )
        if not 0 <= fraction <= 1:
            raise _ScenarioInputError(
                "maintenance must be a fraction in [0, 1].",
                error_code="invalid_scenario",
                field="maintenance",
                supplied=supplied["maintenance"],
            )
        coefficients["maintenance"] = fraction
    if "feedstock_price_usd_per_kg" in supplied:
        price = _finite(
            supplied["feedstock_price_usd_per_kg"],
            "feedstock_price_usd_per_kg",
        )
        if price < 0:
            raise _ScenarioInputError(
                "feedstock_price_usd_per_kg must be nonnegative",
                error_code="invalid_scenario",
                field="feedstock_price_usd_per_kg",
                supplied=supplied["feedstock_price_usd_per_kg"],
            )
        coefficients["feedstock_price_usd_per_kg"] = price
    if "centrifuged_plastic_solvent_content_pct" in supplied:
        content = _finite(
            supplied["centrifuged_plastic_solvent_content_pct"],
            "centrifuged_plastic_solvent_content_pct",
        )
        if not 0 <= content <= 100:
            raise _ScenarioInputError(
                "centrifuged_plastic_solvent_content_pct must be between "
                "0 and 100.",
                error_code="invalid_scenario",
                field="centrifuged_plastic_solvent_content_pct",
                supplied=supplied["centrifuged_plastic_solvent_content_pct"],
            )
        coefficients["centrifuged_plastic_solvent_content_pct"] = content
    if "natural_gas_price_usd_per_m3" in supplied:
        if energy_case == "C2":
            raise _ScenarioInputError(
                "natural_gas_price_usd_per_m3 is not on this instance; "
                "energy_case C2 has no boiler.",
                error_code="energy_case_contract",
                field="natural_gas_price_usd_per_m3",
                energy_case=energy_case,
            )
        price = _finite(
            supplied["natural_gas_price_usd_per_m3"],
            "natural_gas_price_usd_per_m3",
        )
        if price < 0:
            raise _ScenarioInputError(
                "natural_gas_price_usd_per_m3 must be nonnegative",
                error_code="invalid_scenario",
                field="natural_gas_price_usd_per_m3",
                supplied=supplied["natural_gas_price_usd_per_m3"],
            )
        coefficients["natural_gas_price_usd_per_m3"] = price
    elif energy_case in {"C1", "C3"}:
        coefficients["natural_gas_price_usd_per_m3"] = (
            _NATURAL_GAS_PRICE_USD_PER_M3
        )
    return coefficients


def _project_flowsheet_switches(config: dict[str, Any]) -> dict[str, Any]:
    """Fill absent switches with the worker's production hardcodes.

    This is cache/campaign projection, not ``field_origin=default``. Existing
    records and twelve-only callers keep today's plant. A supplied value that
    differs is a different executed plant.
    """
    projected = {}
    for key, default in _FLOWSHEET_SWITCH_DEFAULTS.items():
        value = config.get(key)
        projected[key] = default if value is None else value
    return projected


def _coerce_flowsheet_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in {"true", "yes"}:
            return True
        if token in {"false", "no"}:
            return False
    raise _ScenarioInputError(
        f"{field} must be a boolean",
        error_code="invalid_scenario",
        field=field,
        supplied=value,
    )


def _validated_flowsheet_switches(
    supplied: dict[str, Any], *, energy_case: str,
) -> dict[str, Any]:
    if "facilities" in supplied or "turbogenerator" in supplied:
        raise _ScenarioInputError(
            "facilities and turbogenerator are derived from energy_case; "
            "they are not independent knobs.",
            error_code="energy_case_contract",
            energy_case=energy_case,
            energy_case_map=dict(_ENERGY_CASES),
        )
    switches = dict(_FLOWSHEET_SWITCH_DEFAULTS)
    if "sell_leftover_plastic" in supplied:
        switches["sell_leftover_plastic"] = _coerce_flowsheet_bool(
            supplied["sell_leftover_plastic"], "sell_leftover_plastic",
        )
    if "burn_leftover_plastic" in supplied:
        switches["burn_leftover_plastic"] = _coerce_flowsheet_bool(
            supplied["burn_leftover_plastic"], "burn_leftover_plastic",
        )
    if "precipitation_temperature_format" in supplied:
        fmt = str(supplied["precipitation_temperature_format"] or "").strip()
        if fmt not in _PRECIPITATION_FORMATS:
            raise _ScenarioInputError(
                "precipitation_temperature_format must be 'constant' or 'drop'",
                error_code="invalid_scenario",
                field="precipitation_temperature_format",
                supplied=supplied["precipitation_temperature_format"],
            )
        if fmt == "drop":
            raise _ScenarioInputError(
                "precipitation_temperature_format='drop' registers "
                "set_precipitation_temperature_drop instead of "
                "set_precipitation_temperature; this slice accepts 'constant' "
                "only.",
                error_code="field_not_on_this_instance",
                field="precipitation_temperature_format",
                requested="drop",
            )
        switches["precipitation_temperature_format"] = fmt
    if "precipitation_configuration" in supplied:
        configuration = str(
            supplied["precipitation_configuration"] or ""
        ).strip()
        if configuration not in _PRECIPITATION_CONFIGURATIONS:
            raise _ScenarioInputError(
                "precipitation_configuration must be 'integrated heat "
                "transfer' or 'solvent mixing'",
                error_code="invalid_scenario",
                field="precipitation_configuration",
                supplied=supplied["precipitation_configuration"],
            )
        switches["precipitation_configuration"] = configuration
    if (
        switches["sell_leftover_plastic"]
        and switches["burn_leftover_plastic"]
    ):
        raise _ScenarioInputError(
            "sell_leftover_plastic and burn_leftover_plastic cannot both "
            "be true; leftover cannot be sold and burned.",
            error_code="leftover_disposition_conflict",
            sell_leftover_plastic=True,
            burn_leftover_plastic=True,
        )
    if switches["burn_leftover_plastic"] and energy_case == "C2":
        raise _ScenarioInputError(
            "burn_leftover_plastic requires on-site facilities; energy_case "
            "C2 has no boiler.",
            error_code="burn_requires_facilities",
            energy_case=energy_case,
            burn_leftover_plastic=True,
        )
    return switches


def _twelve_normalized(config: dict[str, Any]) -> dict[str, Any]:
    """D-8 overlay coordinates. Not a serve key once switches are public."""
    return {
        key: (
            _key(config[key]) if key in {"solvent", "target_plastic"}
            else str(config[key]).upper() if key == "energy_case"
            else round(float(config[key]), 10)
        )
        for key in _CONFIG_FIELDS
    }


def _design_point_key(config: dict[str, Any]) -> str:
    """Hash the D-8 twelve only. Existing records are unique on this."""
    return json.dumps(
        _twelve_normalized(config), sort_keys=True, separators=(",", ":"),
    )


def _scenario_value_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _missing_required_public_fields(scenario: dict[str, Any]) -> list[str]:
    missing = []
    for public in _PUBLIC_REQUIRED_FIELDS:
        keys = _PUBLIC_FIELD_SOURCE_KEYS[public]
        if not any(
            key in scenario and _scenario_value_present(scenario.get(key))
            for key in keys
        ):
            missing.append(public)
    return missing


def _missing_held_public_fields(basis: dict[str, Any]) -> list[str]:
    missing = []
    for public in _NINE_HELD_PUBLIC_FIELDS:
        keys = _PUBLIC_FIELD_SOURCE_KEYS[public]
        if not any(
            key in basis and _scenario_value_present(basis.get(key))
            for key in keys
        ):
            missing.append(public)
    return missing


def _canonical_held_process_basis(basis: dict[str, Any]) -> dict[str, Any]:
    supplied = {
        _SCENARIO_ALIASES.get(str(key), str(key)): value
        for key, value in basis.items()
    }
    public: dict[str, Any] = {}
    for internal, public_name in _DESIGN_POINT_PUBLIC_FIELDS:
        if public_name not in _NINE_HELD_PUBLIC_FIELDS:
            continue
        if internal in supplied:
            public[public_name] = supplied[internal]
        elif public_name in supplied:
            public[public_name] = supplied[public_name]
    for key in (
        *_FLOWSHEET_SWITCH_FIELDS,
        *_COEFFICIENT_DEFAULTS,
        "natural_gas_price_usd_per_m3",
    ):
        if key in supplied:
            public[key] = supplied[key]
        elif key in basis:
            public[key] = basis[key]
    return public


def _handoff_field_origin(
    held: dict[str, Any], *, nine_origin: str = "supplied",
) -> dict[str, str]:
    origin = {
        "target_polymer": "from_screen",
        "solvent": "from_screen",
        "dissolution_temperature_c": "from_screen",
    }
    for name in _NINE_HELD_PUBLIC_FIELDS:
        origin[name] = nine_origin
    for key in _INHERIT_OPTIONAL_KEYS:
        if key in held:
            origin[key] = nine_origin
    return origin


def _canonical_screening_shortlist_item(
    item: Any, index: int,
) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise _ScenarioInputError(
            "screening_shortlist items must be objects",
            error_code="screening_shortlist_incomplete",
            item_index=index,
            missing=["target_polymer", "solvent", "dissolution_temperature_c"],
        )
    unknown = sorted(
        str(key) for key in item if str(key) not in _SCREENING_ITEM_KEYS
    )
    if unknown:
        raise _ScenarioInputError(
            "unknown extra process field: " + ", ".join(unknown),
            error_code="unknown_process_field",
            extra_keys=unknown,
            item_index=index,
        )
    mapped = dict(item)
    has_dissolution = any(
        key in mapped and _scenario_value_present(mapped.get(key))
        for key in (
            "dissolution_temperature_c", "dissolution_temp_c",
        )
    )
    if "temperature_c" in mapped:
        if has_dissolution:
            raise _ScenarioInputError(
                "unknown extra process field: temperature_c",
                error_code="unknown_process_field",
                extra_keys=["temperature_c"],
                item_index=index,
            )
        mapped["dissolution_temperature_c"] = mapped.pop("temperature_c")
    polymer = mapped.get("target_polymer") or mapped.get("target_plastic")
    missing = []
    if not _scenario_value_present(polymer):
        missing.append("target_polymer")
    if not _scenario_value_present(mapped.get("solvent")):
        missing.append("solvent")
    temperature = mapped.get("dissolution_temperature_c")
    if temperature is None:
        temperature = mapped.get("dissolution_temp_c")
    if not _scenario_value_present(temperature):
        missing.append("dissolution_temperature_c")
    if missing:
        raise _ScenarioInputError(
            "screening_shortlist item is incomplete; missing: "
            + ", ".join(missing),
            error_code="screening_shortlist_incomplete",
            item_index=index,
            missing=missing,
        )
    return {
        "target_polymer": polymer,
        "solvent": mapped.get("solvent"),
        "dissolution_temperature_c": temperature,
    }


def _expand_screening_evaluate_handoff(
    shortlist: Any,
    held: Any,
    *,
    nine_origin: str = "supplied",
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Compose 3 from_screen + 9 supplied or inherited. Not a cache-pair fill."""
    if not isinstance(shortlist, dict):
        raise _ScenarioInputError(
            "screening_shortlist must be an object",
            error_code="screening_shortlist_incomplete",
            missing=["source", "items"],
        )
    unknown = sorted(
        str(key) for key in shortlist
        if str(key) not in _SCREENING_SHORTLIST_KEYS
    )
    if unknown:
        raise _ScenarioInputError(
            "unknown extra process field: " + ", ".join(unknown),
            error_code="unknown_process_field",
            extra_keys=unknown,
        )
    source = str(shortlist.get("source") or "").strip()
    if source not in _SCREENING_SHORTLIST_SOURCES:
        raise _ScenarioInputError(
            "screening_shortlist.source must be "
            "plan_multistage_separation, screen_polymer_separation, "
            "or explicit",
            error_code="screening_shortlist_incomplete",
            missing=["source"],
            allowed_sources=sorted(_SCREENING_SHORTLIST_SOURCES),
        )
    handle = shortlist.get("handle")
    if handle is not None and not isinstance(handle, str):
        raise _ScenarioInputError(
            "screening_shortlist.handle must be a string when supplied",
            error_code="screening_shortlist_incomplete",
            missing=["handle"],
        )
    items = shortlist.get("items")
    if not isinstance(items, list) or not items:
        raise _ScenarioInputError(
            "screening_shortlist.items must be a non-empty list",
            error_code="screening_shortlist_incomplete",
            missing=["items"],
        )
    if held is None or not isinstance(held, dict):
        raise _ScenarioInputError(
            "held_process_basis is required with screening_shortlist",
            error_code="screening_basis_incomplete",
            missing=list(_NINE_HELD_PUBLIC_FIELDS),
        )
    unknown_held = sorted(
        str(key) for key in held if str(key) not in _HELD_PROCESS_BASIS_KEYS
    )
    if unknown_held:
        raise _ScenarioInputError(
            "unknown extra process field: " + ", ".join(unknown_held),
            error_code="unknown_process_field",
            extra_keys=unknown_held,
        )
    missing_held = _missing_held_public_fields(held)
    if missing_held:
        raise _ScenarioInputError(
            "held_process_basis is incomplete; missing public fields: "
            + ", ".join(missing_held),
            error_code="screening_basis_incomplete",
            missing=missing_held,
        )
    if len(items) > 20:
        raise _ScenarioInputError(
            "At most 20 scenarios may run per call",
            error_code="too_many_scenarios",
        )
    canonical_held = _canonical_held_process_basis(held)
    scenarios = []
    for index, item in enumerate(items):
        three = _canonical_screening_shortlist_item(item, index)
        scenarios.append({**canonical_held, **three})
    return scenarios, _handoff_field_origin(held, nine_origin=nine_origin)


def _public_name_for_inherit_key(key: str) -> str | None:
    internal_to_public = dict(_DESIGN_POINT_PUBLIC_FIELDS)
    if key in _SCENARIO_ALIASES:
        return internal_to_public.get(_SCENARIO_ALIASES[key], key)
    if key in internal_to_public:
        return internal_to_public[key]
    if key in _PUBLIC_REQUIRED_FIELDS or key in _INHERIT_OPTIONAL_KEYS:
        return key
    if key == "polymer":
        return "target_polymer"
    return None


def _public_twelve_from_row(row: dict[str, Any]) -> dict[str, Any] | None:
    """Executed D-8 twelve from an economics comparison row. Not a screen."""
    if not isinstance(row, dict):
        return None
    public: dict[str, Any] = {}
    for _internal, public_name in _DESIGN_POINT_PUBLIC_FIELDS:
        found = None
        for key in _PUBLIC_FIELD_SOURCE_KEYS[public_name]:
            if key in row and _scenario_value_present(row.get(key)):
                found = row[key]
                break
        if (
            found is None
            and public_name == "target_polymer"
            and _scenario_value_present(row.get("polymer"))
        ):
            found = row["polymer"]
        if found is None:
            return None
        public[public_name] = found
    return public


def _executed_public_config(row: dict[str, Any]) -> dict[str, Any] | None:
    twelve = _public_twelve_from_row(row)
    if twelve is None:
        return None
    executed = dict(twelve)
    for key in _INHERIT_OPTIONAL_KEYS:
        if key in row and _scenario_value_present(row.get(key)):
            executed[key] = row[key]
    return executed


def _held_nine_from_inherit(inherited: dict[str, Any]) -> dict[str, Any]:
    held: dict[str, Any] = {}
    for name in _NINE_HELD_PUBLIC_FIELDS:
        if name in inherited:
            held[name] = inherited[name]
    for key in _INHERIT_OPTIONAL_KEYS:
        if key in inherited:
            held[key] = inherited[key]
    return held


def _select_economics_handle_row(
    rows: list[dict[str, Any]],
    row_id: Any,
    *,
    handle: str,
) -> dict[str, Any]:
    if not rows:
        raise _ScenarioInputError(
            "handle has no economics comparison rows",
            error_code="not_economics_handle",
            handle=handle,
        )
    if row_id is None:
        if len(rows) > 1:
            raise _ScenarioInputError(
                "multi-row handle requires row_id",
                error_code="ambiguous_handle_row",
                handle=handle,
                n_rows=len(rows),
            )
        return rows[0]
    index = None
    if isinstance(row_id, int) and not isinstance(row_id, bool):
        index = row_id
    elif isinstance(row_id, str) and row_id.strip().isdigit():
        index = int(row_id.strip())
    if index is not None:
        if index < 1 or index > len(rows):
            raise _ScenarioInputError(
                f"row_id {index} is out of range for {len(rows)} rows",
                error_code="unknown_handle",
                handle=handle,
                row_id=row_id,
                n_rows=len(rows),
            )
        return rows[index - 1]
    token = str(row_id).strip()
    matches = [
        row for row in rows
        if str(row.get("label") or "") == token
        or str(row.get("record_id") or "") == token
        or str(row.get("pair_id") or "") == token
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise _ScenarioInputError(
            "row_id matches more than one handle row",
            error_code="ambiguous_handle_row",
            handle=handle,
            row_id=row_id,
            n_rows=len(matches),
        )
    raise _ScenarioInputError(
        "row_id does not match a handle row",
        error_code="unknown_handle",
        handle=handle,
        row_id=row_id,
        n_rows=len(rows),
    )


def _flatten_grouped_landscape_points(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Grouped fronts nest the ranked points. Inherit reads those points."""
    if not rows or not all(isinstance(row, dict) for row in rows):
        return rows
    if any(_executed_public_config(row) is not None for row in rows):
        return rows
    nested: list[dict[str, Any]] = []
    for block in rows:
        inner = block.get("landscape_points")
        if not isinstance(inner, list):
            return rows
        nested.extend(item for item in inner if isinstance(item, dict))
    return nested or rows


def _economics_handle_rows(stored: dict[str, Any]) -> list[dict[str, Any]]:
    return _flatten_grouped_landscape_points(handle_rows(stored))


def _load_economics_inherit(handle: Any, row_id: Any) -> dict[str, Any]:
    """Copy executed twelve from a prior economics handle. Not a screen."""
    token = handle.strip() if isinstance(handle, str) else ""
    if not isinstance(handle, str) or not token:
        raise _ScenarioInputError(
            "handle is required to inherit omitted process fields",
            error_code="unknown_handle",
            handle=handle,
        )
    record = current_tool_session()
    stored = load_handle(record, token) if record is not None else None
    if stored is None:
        raise _ScenarioInputError(
            "unknown handle",
            error_code="unknown_handle",
            handle=token,
        )
    try:
        rows = _economics_handle_rows(stored)
    except (ValueError, KeyError, TypeError):
        raise _ScenarioInputError(
            "handle has no economics comparison rows",
            error_code="not_economics_handle",
            handle=token,
            source_tool=stored.get("tool"),
        )
    if not any(_executed_public_config(row) is not None for row in rows):
        raise _ScenarioInputError(
            "handle row has no executed twelve-field process config",
            error_code="not_economics_handle",
            handle=token,
            source_tool=stored.get("tool"),
        )
    row = _select_economics_handle_row(rows, row_id, handle=token)
    executed = _executed_public_config(row)
    if executed is None:
        raise _ScenarioInputError(
            "handle row has no executed twelve-field process config",
            error_code="not_economics_handle",
            handle=token,
            source_tool=stored.get("tool"),
        )
    return executed


def _merge_inherited_scenario(
    scenario: dict[str, Any],
    inherited: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    origin = {
        (_public_name_for_inherit_key(key) or key): "inherited"
        for key in inherited
        if _public_name_for_inherit_key(key)
    }
    merged = dict(inherited)
    for key, value in scenario.items():
        if not _scenario_value_present(value):
            continue
        merged[key] = value
        public = _public_name_for_inherit_key(str(key))
        if public:
            origin[public] = "supplied"
    return merged, origin


def _scenario_has_complete_twelve(scenario: Any) -> bool:
    return (
        isinstance(scenario, dict)
        and not _missing_required_public_fields(scenario)
    )


def _compose_evaluate_scenarios(
    scenarios: Optional[list[dict[str, Any]]],
    screening_shortlist: Optional[dict[str, Any]],
    held_process_basis: Optional[dict[str, Any]],
    handle: Any,
    row_id: Any,
) -> tuple[
    list[dict[str, Any]],
    dict[str, str] | None,
    list[dict[str, str]] | None,
]:
    """Resolve evaluate composition, including handle inherit.

    Complete twelve-field scenarios run as sent. Incomplete follow-up
    copies omitted fields from an economics handle. A screening payload
    cannot be that handle.
    """
    has_scenarios = isinstance(scenarios, list) and bool(scenarios)
    has_shortlist = screening_shortlist is not None
    has_held = held_process_basis is not None
    if has_scenarios and has_shortlist:
        raise _ScenarioInputError(
            "send either scenarios or screening_shortlist, not both",
            error_code="conflicting_evaluate_composition",
        )
    if has_held and not has_shortlist:
        raise _ScenarioInputError(
            "held_process_basis is only legal with screening_shortlist",
            error_code="conflicting_evaluate_composition",
        )
    handle_present = isinstance(handle, str) and bool(handle.strip())
    if row_id is not None and not handle_present and not (
        has_shortlist and has_held
    ) and not (has_scenarios and all(
        _scenario_has_complete_twelve(item) for item in (scenarios or [])
    )):
        raise _ScenarioInputError(
            "row_id requires handle",
            error_code="unknown_handle",
            row_id=row_id,
        )
    if has_shortlist:
        if has_held:
            return _expand_screening_evaluate_handoff(
                screening_shortlist, held_process_basis,
            ) + (None,)
        if not handle_present:
            raise _ScenarioInputError(
                "held_process_basis is required with screening_shortlist",
                error_code="screening_basis_incomplete",
                missing=list(_NINE_HELD_PUBLIC_FIELDS),
            )
        inherited = _load_economics_inherit(handle, row_id)
        held = _held_nine_from_inherit(inherited)
        expanded, origin = _expand_screening_evaluate_handoff(
            screening_shortlist, held, nine_origin="inherited",
        )
        return expanded, origin, None
    if has_scenarios:
        items = list(scenarios or [])
        if all(_scenario_has_complete_twelve(item) for item in items):
            return items, None, None
        if not handle_present:
            return items, None, None
        inherited = _load_economics_inherit(handle, row_id)
        merged = []
        origins = []
        for item in items:
            if not isinstance(item, dict):
                raise _ScenarioInputError(
                    "Each scenario must be an object",
                    error_code="invalid_scenario",
                )
            scenario, origin = _merge_inherited_scenario(item, inherited)
            merged.append(scenario)
            origins.append(origin)
        return merged, None, origins
    if handle_present or row_id is not None:
        inherited = _load_economics_inherit(handle, row_id)
        origin = {
            (_public_name_for_inherit_key(key) or key): "inherited"
            for key in inherited
            if _public_name_for_inherit_key(key)
        }
        return [dict(inherited)], origin, None
    raise _ScenarioInputError(
        "scenarios must be a non-empty list",
        error_code="missing_scenarios",
    )


def _compose_sensitivity_scenario(
    scenario: Any,
    handle: Any,
    row_id: Any,
) -> tuple[dict[str, Any], dict[str, str] | None]:
    """Fill a sensitivity baseline from an economics handle. Not a screen."""
    handle_present = isinstance(handle, str) and bool(handle.strip())
    complete = _scenario_has_complete_twelve(scenario)
    if row_id is not None and not handle_present and not complete:
        raise _ScenarioInputError(
            "row_id requires handle",
            error_code="unknown_handle",
            row_id=row_id,
        )
    if scenario is None:
        if not handle_present:
            raise _ScenarioInputError(
                "process_config is incomplete; missing public fields: "
                + ", ".join(_PUBLIC_REQUIRED_FIELDS),
                error_code="incomplete_process_config",
                missing=list(_PUBLIC_REQUIRED_FIELDS),
            )
        inherited = _load_economics_inherit(handle, row_id)
        origin = {
            (_public_name_for_inherit_key(key) or key): "inherited"
            for key in inherited
            if _public_name_for_inherit_key(key)
        }
        return dict(inherited), origin
    if not isinstance(scenario, dict):
        raise _ScenarioInputError(
            "scenario must be an object",
            error_code="invalid_scenario",
        )
    if complete:
        return dict(scenario), None
    if not handle_present:
        return dict(scenario), None
    inherited = _load_economics_inherit(handle, row_id)
    return _merge_inherited_scenario(scenario, inherited)


def _sensitivity_row_process_fields(config: dict[str, Any]) -> dict[str, Any]:
    """Executed twelve plus exposed tunables on a sensitivity row."""
    public = {
        public_name: config.get(internal)
        for internal, public_name in _DESIGN_POINT_PUBLIC_FIELDS
    }
    public["polymer"] = public.get("target_polymer")
    public.update(_project_flowsheet_switches(config))
    public.update(_project_coefficients(config))
    return public


def _stored_route_named_remainder(solvent: str) -> dict[str, Any]:
    """Name the stored-route stage remainder. Not evaluate silent fill.

    Stored-route overlay already supplies mass%, capacity, energy, T, and
    precip. The remaining public fields are this tool's production plant,
    plus an admitted solvent price. Evaluate and sensitivity require the
    twelve before `_scenario_config` and do not reach this helper.
    """
    identity = _resolve_tea_solvent(solvent)
    remainder = dict(_STORED_ROUTE_PRODUCTION_REMAINDER)
    assumption = identity.get("assumption")
    if assumption is None or assumption.get("price_usd_per_kg") is None:
        raise _MissingScenarioBasis(
            f"Solvent '{identity['requested']}' is recognized as "
            f"{identity['canonical']}, but no admitted solvent-price basis "
            "is available; supply solvent_price or choose a priced solvent.",
            error_code="solvent_price_unavailable",
            requested_solvent=identity["requested"],
            canonical_solvent=identity["canonical"],
            solvent_support_status="known_but_unpriced",
        )
    remainder["solvent_price_usd_per_kg"] = assumption["price_usd_per_kg"]
    return remainder


def _flowsheet_switch_deltas(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Public extras that differ from the projected production plant."""
    projected_switches = _project_flowsheet_switches(config)
    deltas = [
        {
            "field": key,
            "recorded_value": default,
            "requested_value": projected_switches[key],
        }
        for key, default in _FLOWSHEET_SWITCH_DEFAULTS.items()
        if projected_switches[key] != default
    ]
    projected_coefficients = _project_coefficients(config)
    for key, default in _coefficient_defaults_for(config).items():
        requested = projected_coefficients[key]
        if math.isclose(float(requested), float(default), rel_tol=0, abs_tol=1e-12):
            continue
        deltas.append({
            "field": key,
            "recorded_value": default,
            "requested_value": requested,
        })
    return deltas


def _scenario_config(
    scenario: dict[str, Any],
    *,
    require_complete_twelve: bool = False,
) -> dict[str, Any]:
    if not isinstance(scenario, dict):
        raise ValueError("Each scenario must be an object")
    _refuse_reserved_process_fields(scenario)
    unknown = sorted(
        str(key) for key in scenario if str(key) not in _SCENARIO_ALLOWED_KEYS
    )
    if unknown:
        raise _ScenarioInputError(
            "unknown extra process field: " + ", ".join(unknown),
            error_code="unknown_process_field",
            extra_keys=unknown,
        )
    working = dict(scenario)
    missing = _missing_required_public_fields(working)
    if (
        missing
        and not require_complete_twelve
        and all(name in _D8_REMAINDER_PUBLIC_FIELDS for name in missing)
        and working.get("solvent") not in (None, "")
    ):
        remainder = _stored_route_named_remainder(str(working["solvent"]))
        missing_set = set(missing)
        for key, value in remainder.items():
            if key in missing_set:
                working[key] = value
        missing = _missing_required_public_fields(working)
    if missing:
        raise _ScenarioInputError(
            "process_config is incomplete; missing public fields: "
            + ", ".join(missing),
            error_code="incomplete_process_config",
            missing=list(missing),
        )
    supplied = {
        _SCENARIO_ALIASES.get(str(key), str(key)): value
        for key, value in working.items()
    }
    polymer = _resolve_polymer(
        supplied.get("target_polymer") or supplied.get("target_plastic")
    )
    solvent_identity = _resolve_tea_solvent(supplied.get("solvent"))
    solvent = str(solvent_identity["canonical"])
    config = {
        **{key: supplied[key] for key in _CONFIG_FIELDS if key in supplied},
        "target_plastic": polymer,
        "solvent": solvent,
    }
    config["energy_case"] = str(config.get("energy_case") or "").upper()
    if config["energy_case"] not in _ENERGY_CASES:
        raise ValueError("energy_case must be C1, C2, or C3")
    for field in _NUMERIC_FIELDS:
        config[field] = _finite(config.get(field), field)
    if not 0 < config["target_plastic_percent"] <= 100:
        raise ValueError("target_plastic_percent must be above 0 and at most 100")
    if config["processing_capacity"] <= 0 or config["dissolution_capacity"] <= 0:
        raise ValueError("processing and dissolution capacity must be positive")
    if config["solvent_price"] < 0 or config["solvent_loss_pct"] < 0:
        raise ValueError("solvent price and loss must be nonnegative")
    switches = _validated_flowsheet_switches(
        supplied, energy_case=str(config["energy_case"]),
    )
    coefficients = _validated_coefficients(
        supplied, energy_case=str(config["energy_case"]),
    )
    normalized = {key: config[key] for key in _CONFIG_FIELDS}
    normalized.update(switches)
    normalized.update(coefficients)
    normalized.update({
        "_requested_solvent": solvent_identity["requested"],
        "_engine_solvent": solvent_identity["canonical"],
        "_admitted_engine_solvent": solvent_identity["engine"],
        "_engine_solvent_cas": solvent_identity["cas_number"],
        "_allow_cas_safe_alias": False,
    })
    if "lca_cfs" in supplied:
        normalized["lca_cfs"] = _validated_lca_cfs(supplied["lca_cfs"])
    return normalized


def _config_key(config: dict[str, Any]) -> str:
    """Serve key: D-8 twelve plus every public field that changes the number."""
    normalized = _twelve_normalized(config)
    switches = _project_flowsheet_switches(config)
    for key in _FLOWSHEET_SWITCH_FIELDS:
        value = switches[key]
        if isinstance(_FLOWSHEET_SWITCH_DEFAULTS[key], bool):
            normalized[key] = bool(value)
        else:
            normalized[key] = str(value)
    for key, value in _project_coefficients(config).items():
        normalized[key] = round(float(value), 10)
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _record_for_design_point(config: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Find a cache row that matches the twelve regardless of switches."""
    key = _design_point_key(config)
    for item in _records():
        try:
            if _design_point_key(item["config"]) == key:
                return item
        except (TypeError, ValueError, KeyError):
            continue
    return None


@lru_cache(maxsize=1)
def _cache_index() -> dict[str, dict[str, Any]]:
    return {_config_key(item["config"]): item for item in _records()}


def _tea_worker_python() -> str:
    """Resolve the isolated worker interpreter without changing the default."""
    configured = str(os.getenv(_TEA_WORKER_PYTHON_ENV) or "").strip()
    return os.path.expanduser(configured) if configured else sys.executable


def _tea_worker_source_provenance() -> tuple[dict[str, Any], Optional[str]]:
    """Bind child execution to the same worker source imported by the parent."""
    worker_path = Path(str(tea_worker.__file__)).resolve()
    provenance: dict[str, Any] = {"worker_source_path": str(worker_path)}
    try:
        digest = hashlib.sha256(
            tea_polymer_parameters._read_strap_bytes(worker_path)
        ).hexdigest()
    except tea_polymer_parameters.PackageInspectionError as error:
        provenance["unreadable_source"] = str(error.path)
        return provenance, str(error)
    provenance["expected_worker_source_sha256"] = digest
    return provenance, None


def _tea_worker_environment() -> dict[str, str]:
    """Expose this source tree and the optional process model to the worker."""
    environment = os.environ.copy()
    python_paths = [str(Path(__file__).resolve().parents[1])]
    if path := str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip():
        python_paths.append(str(Path(path).expanduser().resolve()))
    python_paths.extend(
        item for item in environment.get("PYTHONPATH", "").split(os.pathsep)
        if item
    )
    environment["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(python_paths))
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _expected_live_runtime_versions() -> dict[str, str]:
    """Read cache simulator pins plus the compatible Biorefineries release."""
    generator = cache_payload().get("generator") or {}
    expected = {
        "biosteam": str(generator.get("biosteam_version") or "").strip(),
        "thermosteam": str(generator.get("thermosteam_version") or "").strip(),
        "biorefineries": _LIVE_BIOREFINERIES_VERSION,
    }
    missing = [name for name, version in expected.items() if not version]
    if missing:
        raise RuntimeError(
            "TEA cache lacks live runtime provenance for: " + ", ".join(missing)
        )
    return expected


def _python_meets_live_requirement(version: str) -> bool:
    """True when a resolved interpreter version is 3.12 or newer."""
    parts = str(version or "").split(".")
    try:
        major, minor = int(parts[0]), int(parts[1])
    except (IndexError, ValueError):
        return False
    return (major, minor) >= (3, 12)


def _probe_live_runtime_versions(
    worker_python: str,
) -> tuple[Optional[dict[str, str]], Optional[str]]:
    """Resolve distributions in the selected worker without importing BioSTEAM."""
    probe = (
        "import json, platform\n"
        "from importlib.metadata import version\n"
        "print(json.dumps({"
        "'python': platform.python_version(),"
        + ",".join(
            f"'{name}': version('{name}')"
            for name in _LIVE_RUNTIME_DISTRIBUTIONS
        )
        + "}))"
    )
    try:
        completed = subprocess.run(
            [worker_python, "-c", probe],
            capture_output=True, text=True, timeout=15,
            env=_tea_worker_environment(),
            cwd=str(Path(__file__).resolve().parents[1]),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"Worker dependency probe could not run: {error}"
    if completed.returncode:
        detail = (completed.stderr or completed.stdout or "").strip()[-1_000:]
        return None, "Worker dependency probe failed" + (
            f": {detail}" if detail else "."
        )
    try:
        resolved = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        return None, "Worker dependency probe returned invalid JSON."
    if not isinstance(resolved, dict) or any(
        not str(resolved.get(name) or "").strip()
        for name in ("python", *_LIVE_RUNTIME_DISTRIBUTIONS)
    ):
        return None, "Worker dependency probe returned incomplete version data."
    return {
        name: str(resolved[name]).strip()
        for name in ("python", *_LIVE_RUNTIME_DISTRIBUTIONS)
    }, None


def _live_process_model_provenance(path: str) -> tuple[dict[str, Any], Optional[str]]:
    """Hash the configured physics source against the admitted cache pin."""
    expected = str(
        (cache_payload().get("generator") or {}).get("process_model_sha256") or ""
    ).strip().casefold()
    model_path = (
        Path(path).expanduser().resolve() / _LIVE_PROCESS_MODEL_RELATIVE_PATH
    )
    provenance: dict[str, Any] = {
        "process_model_path": str(model_path),
        "expected_process_model_sha256": expected or None,
    }
    if not expected:
        return provenance, "TEA cache lacks a process-model SHA-256 pin."
    if not model_path.is_file():
        return provenance, f"Configured process model does not exist: {model_path}"
    try:
        tea_polymer_parameters._parse_strap_source(model_path)
        actual = hashlib.sha256(
            tea_polymer_parameters._read_strap_bytes(model_path)
        ).hexdigest()
    except tea_polymer_parameters.PackageInspectionError as error:
        provenance["unreadable_source"] = str(error.path)
        return provenance, str(error)
    provenance["process_model_sha256"] = actual
    if actual != expected:
        return provenance, (
            "Configured process model does not match the admitted TEA cache: "
            f"expected {expected}, resolved {actual} at {model_path}."
        )
    return provenance, None


def _live_cited_package_provenance(path: str) -> tuple[dict[str, Any], Optional[str]]:
    """Hash the package files the expert table cites, not only process_model.py."""
    root = Path(path).expanduser().resolve()
    try:
        cited = tea_polymer_parameters.cited_strap_source_provenance(root)
    except tea_polymer_parameters.PackageInspectionError as error:
        return {
            "cited_package_sources": {},
            "cited_package_missing": [],
            "unreadable_source": str(error.path),
            "unreadable_reason": error.diagnostic_reason,
        }, str(error)
    provenance = {
        "cited_package_sources": cited["sources"],
        "cited_package_missing": list(cited["missing"]),
    }
    if cited["missing"]:
        return provenance, (
            "Expert-table package sources are missing under "
            f"{root}: " + ", ".join(cited["missing"]) + "."
        )
    try:
        tea_polymer_parameters.inspect_cited_strap_sources(root)
    except tea_polymer_parameters.PackageInspectionError as error:
        provenance["unreadable_source"] = str(error.path)
        provenance["unreadable_reason"] = error.diagnostic_reason
        return provenance, str(error)
    return provenance, None


def live_engine_status() -> dict[str, Any]:
    path = str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip()
    configured_python = bool(
        str(os.getenv(_TEA_WORKER_PYTHON_ENV) or "").strip()
    )
    worker_python = _tea_worker_python()
    if not configured_python and sys.version_info < (3, 12):
        return {
            "available": False, "reason": "python_version",
            "detail": (
                "Live BioSTEAM execution requires Python 3.12 or newer. "
                f"This process is Python {platform.python_version()}. "
                "Set DISSOLVE_TEA_PYTHON to the dissolve-tea-312 interpreter "
                "(biosteam==2.52.17, thermosteam==0.52.16) and "
                "DISSOLVE_PLASTICS_PATH to unpublished plastics 0.1.4. "
                "Public plastics 0.1.3 is incompatible."
            ),
        }
    if configured_python and shutil.which(worker_python) is None:
        return {
            "available": False, "reason": "worker_interpreter_missing",
            "detail": (
                f"{_TEA_WORKER_PYTHON_ENV} must identify an executable "
                "Python interpreter."
            ),
        }
    if not path:
        return {
            "available": False, "reason": "plastics_path_required",
            "detail": (
                "Set DISSOLVE_PLASTICS_PATH to the hash-verifiable unpublished "
                "plastics model root."
            ),
        }
    model_provenance, model_error = _live_process_model_provenance(path)
    if model_error:
        if model_provenance.get("expected_process_model_sha256") is None:
            reason = "cache_provenance_missing"
            provenance_status = "unverifiable"
        elif model_provenance.get("unreadable_source"):
            reason = "process_model_unreadable"
            provenance_status = "unverifiable"
        elif model_provenance.get("process_model_sha256") is None:
            reason = "process_model_missing"
            provenance_status = "unverifiable"
        else:
            reason = "process_model_checksum_mismatch"
            provenance_status = "mismatch"
        return {
            "available": False,
            "reason": reason,
            "detail": model_error,
            "live_provenance": {
                "status": provenance_status,
                **model_provenance,
            },
        }
    cited_provenance, cited_error = _live_cited_package_provenance(path)
    if cited_error:
        return {
            "available": False,
            "reason": cited_provenance.get("unreadable_reason")
            or "cited_package_source_missing",
            "detail": cited_error,
            "live_provenance": {
                "status": "unverifiable",
                **model_provenance,
                **cited_provenance,
            },
        }
    model_provenance = {**model_provenance, **cited_provenance}
    try:
        expected_versions = _expected_live_runtime_versions()
    except RuntimeError as error:
        return {
            "available": False, "reason": "cache_provenance_missing",
            "detail": str(error),
            "live_provenance": {"status": "unverifiable", **model_provenance},
        }
    resolved_versions, probe_error = _probe_live_runtime_versions(worker_python)
    if probe_error:
        return {
            "available": False, "reason": "worker_environment_probe_failed",
            "detail": probe_error,
            "live_provenance": {
                "status": "unverifiable",
                **model_provenance,
                "expected_runtime_versions": expected_versions,
            },
        }
    assert resolved_versions is not None
    runtime_without_worker = {
        **model_provenance,
        "expected_runtime_versions": expected_versions,
        "resolved_runtime_versions": resolved_versions,
    }
    if not _python_meets_live_requirement(resolved_versions["python"]):
        return {
            "available": False,
            "reason": "python_version",
            "detail": (
                "Live BioSTEAM execution requires Python 3.12 or newer. "
                f"DISSOLVE_TEA_PYTHON resolved Python "
                f"{resolved_versions['python']}."
            ),
            "live_provenance": {
                "status": "mismatch",
                **runtime_without_worker,
            },
        }
    mismatches = {
        name: {
            "expected": expected_versions[name],
            "resolved": resolved_versions[name],
        }
        for name in expected_versions
        if resolved_versions[name] != expected_versions[name]
    }
    worker_provenance, worker_error = _tea_worker_source_provenance()
    if worker_error:
        return {
            "available": False,
            "reason": "worker_source_unreadable",
            "detail": worker_error,
            "live_provenance": {
                "status": "unverifiable",
                **runtime_without_worker,
                **worker_provenance,
            },
        }
    live_runtime = {
        **runtime_without_worker,
        **worker_provenance,
    }
    live_provenance = {
        "status": "mismatch" if mismatches else "verified",
        **live_runtime,
    }
    if mismatches:
        return {
            "available": False, "reason": "runtime_version_mismatch",
            "detail": (
                "Live BioSTEAM dependency versions do not match the admitted "
                "TEA cache: " + "; ".join(
                    f"{name} expected {values['expected']}, resolved "
                    f"{values['resolved']}"
                    for name, values in mismatches.items()
                ) + "."
            ),
            "live_provenance": live_provenance,
        }
    handshake = live_child_handshake(live_provenance)
    live_provenance = {
        **live_provenance,
        "child_handshake_target": LIVE_CHILD_HANDSHAKE_TARGET,
        "child_handshake_error_type": handshake.get("error_type"),
        "child_handshake_ok": _child_handshake_started(handshake),
    }
    if not live_provenance["child_handshake_ok"]:
        reason = (
            "invalid_worker_output"
            if handshake.get("error_type") == "invalid_worker_output"
            else "worker_child_handshake_failed"
        )
        error = str(handshake.get("error") or handshake.get("error_type") or "")
        return {
            "available": False,
            "reason": reason,
            "detail": (
                "Live worker child did not start. Readiness launches the "
                "documented runpy path with refused target "
                f"{LIVE_CHILD_HANDSHAKE_TARGET}; expected named JSON "
                "unsupported_live_target, got "
                f"{handshake.get('error_type')!r}"
                + (f": {error[:300]}" if error else ".")
            ),
            "live_provenance": {
                **live_provenance,
                "status": "unverifiable",
            },
        }
    return {
        "available": True,
        "reason": None,
        "detail": (
            "subprocess engine ready; child handshake "
            f"{LIVE_CHILD_HANDSHAKE_TARGET} "
            f"{handshake.get('error_type')}"
        ),
        "live_provenance": live_provenance,
    }


_LIVE_MISCONFIGURED_REASONS = frozenset({
    "worker_interpreter_missing",
    "process_model_missing",
    "process_model_checksum_mismatch",
    "runtime_version_mismatch",
    "worker_environment_probe_failed",
    "cache_provenance_missing",
    "worker_source_mismatch",
    "worker_source_unreadable",
    "property_package_unreadable",
    "cited_package_source_missing",
    "cited_package_checksum_mismatch",
    "cited_package_unreadable",
    "process_model_unreadable",
    "invalid_worker_output",
    "worker_child_handshake_failed",
})

# Live BioSTEAM never runs in this process. The 3.12 interpreter is a
# subprocess that runpy-executes tea_worker.py across a JSON boundary.
# Importing dissolve.tea_worker (or python -m dissolve.tea_worker) in that
# interpreter loads dissolve.__init__ → registry → thermodynamics → duckdb.
# ModuleNotFoundError: duckdb there is a wrong entry point, not a missing
# live-TEA dependency. duckdb belongs to the parent engine.
LIVE_TEA_EXECUTION_PATH = (
    "Live TEA is a two-environment path: this process never imports BioSTEAM; "
    "DISSOLVE_TEA_PYTHON runs src/dissolve/tea_worker.py as a subprocess via "
    "runpy (JSON boundary). Do not import dissolve.tea_worker or use "
    "python -m dissolve.tea_worker in that interpreter — that pulls the "
    "parent engine and fails with ModuleNotFoundError: duckdb, which is not "
    "a live-TEA dependency."
)


def live_environment_report() -> dict[str, Any]:
    """Diagnose the two-environment live TEA path without importing BioSTEAM.

    The default repo interpreter is 3.11; live execution needs 3.12 plus
    unpublished plastics 0.1.4. Doctor uses this so that fact is visible
    before anyone rediscovers it at the moment of failure.
    """
    status = live_engine_status()
    configured_python = str(os.getenv(_TEA_WORKER_PYTHON_ENV) or "").strip()
    plastics = str(os.getenv("DISSOLVE_PLASTICS_PATH") or "").strip()
    root = tea_polymer_parameters.resolve_plastics_path(plastics or None)
    layout = (
        tea_polymer_parameters.plastics_layout_diagnosis(root)
        if root is not None else None
    )
    package_ids: Optional[frozenset[str]] = None
    inspection_error: Optional[tea_polymer_parameters.PackageInspectionError] = None
    if root is not None:
        try:
            package_ids = tea_polymer_parameters.package_chemical_ids(root)
            tea_polymer_parameters.inspect_ready_claim_sources(root)
        except tea_polymer_parameters.PackageInspectionError as error:
            inspection_error = error
        except (OSError, UnicodeDecodeError, SyntaxError, ValueError) as error:
            source = None
            if layout is not None and layout.get("property_package_path"):
                source = Path(str(layout["property_package_path"]))
            inspection_error = tea_polymer_parameters.PackageInspectionError(
                source or root, error,
            )
    refused = tuple(sorted(
        name for name, row in tea_polymer_parameters.POLYMERS.items()
        if row.admission != tea_polymer_parameters.ADMISSION_LIVE
    ))
    why: list[str] = []
    if sys.version_info < (3, 12) and not configured_python:
        why.append(
            f"this process is Python {platform.python_version()}, "
            "which is below 3.12, and DISSOLVE_TEA_PYTHON is unset"
        )
    elif configured_python and shutil.which(
        os.path.expanduser(configured_python)
    ) is None:
        why.append(
            f"DISSOLVE_TEA_PYTHON={configured_python} is not an executable"
        )
    if not plastics:
        why.append("DISSOLVE_PLASTICS_PATH is unset")
    elif layout is None or layout.get("layout") == "unrecognised":
        why.append(
            f"the plastics package is missing under {plastics}"
        )
    elif layout.get("layout") == "inner_package_dir":
        why.append(
            "DISSOLVE_PLASTICS_PATH points at the inner package directory; "
            "set it to the parent so `import plastics.strap` works"
        )
    if inspection_error is not None:
        why.append(str(inspection_error))
    if (
        not why
        and not status.get("available")
        and status.get("detail")
    ):
        why.append(str(status["detail"]))
    reason = status.get("reason")
    available = bool(status.get("available")) and inspection_error is None
    if inspection_error is not None:
        check_status = "fail"
        reason = inspection_error.diagnostic_reason
        why_text = "; ".join(why) if why else str(inspection_error)
    elif available:
        check_status = "pass"
        why_text = (
            "live TEA path is available; child handshake "
            f"{LIVE_CHILD_HANDSHAKE_TARGET} "
            f"{(status.get('live_provenance') or {}).get('child_handshake_error_type')}"
        )
    elif reason in _LIVE_MISCONFIGURED_REASONS or (
        reason == "python_version" and configured_python
    ):
        check_status = "fail"
        why_text = "; ".join(why) if why else str(status.get("detail") or reason)
    elif layout is not None and layout.get("layout") == "inner_package_dir":
        check_status = "fail"
        reason = "plastics_path_inner_package_dir"
        why_text = "; ".join(why)
    else:
        check_status = "warn"
        why_text = "; ".join(why) if why else str(status.get("detail") or reason)
    return {
        "check_status": check_status,
        "available": available,
        "reason": reason,
        "why_unavailable": why_text if not available else None,
        "unavailable_reasons": why,
        "detail": status.get("detail"),
        "parent_python": platform.python_version(),
        "parent_meets_live_requirement": sys.version_info >= (3, 12),
        "live_requires_python": ">=3.12",
        "DISSOLVE_TEA_PYTHON": configured_python or None,
        "DISSOLVE_PLASTICS_PATH": plastics or None,
        "plastics_layout": layout,
        "package_chemical_id_count": (
            len(package_ids) if package_ids is not None else None
        ),
        "admitted_live_targets": list(
            tea_polymer_parameters.live_grid_targets()
        ),
        "refused_grid_targets": list(refused),
        "parameter_surface": "src/dissolve/tea_polymer_parameters.py",
        "execution_path": LIVE_TEA_EXECUTION_PATH,
        "child_handshake_ok": (
            (status.get("live_provenance") or {}).get("child_handshake_ok")
        ),
        "child_handshake_error_type": (
            (status.get("live_provenance") or {}).get(
                "child_handshake_error_type"
            )
        ),
        "live_engine": {
            key: status[key]
            for key in ("available", "reason", "detail")
            if key in status
        },
    }


LIVE_CHILD_HANDSHAKE_TARGET = "PU"
_LIVE_CHILD_HANDSHAKE_CACHE: dict[tuple[str, ...], dict[str, Any]] = {}


def _child_handshake_started(result: dict[str, Any]) -> bool:
    """True when the documented child started and wrote named JSON."""
    return (
        result.get("success") is False
        and result.get("error_type") == "unsupported_live_target"
        and str(result.get("target_plastic") or "").upper()
        == LIVE_CHILD_HANDSHAKE_TARGET
    )


def live_child_handshake(
    provenance: dict[str, Any],
    *,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Launch the exact runpy child readiness claims to be ready.

    A refused target binds bootstrap and JSON without BioSTEAM. Hashes
    and dependency probes are not a substitute: they declared the
    5c0f4be child ready while it died before writing JSON.
    """
    key = (
        _tea_worker_python(),
        str(provenance.get("worker_source_path") or ""),
        str(provenance.get("expected_worker_source_sha256") or ""),
        str(os.getenv(_TEA_WORKER_PYTHON_ENV) or ""),
        str(os.getenv("DISSOLVE_PLASTICS_PATH") or ""),
    )
    cached = _LIVE_CHILD_HANDSHAKE_CACHE.get(key)
    if cached is not None:
        return cached
    result = _launch_live_worker(
        {"target_plastic": LIVE_CHILD_HANDSHAKE_TARGET},
        timeout_seconds=timeout_seconds,
        environment=_tea_worker_environment(),
        provenance={
            "worker_source_path": provenance["worker_source_path"],
            "expected_worker_source_sha256": provenance[
                "expected_worker_source_sha256"
            ],
        },
    )
    _LIVE_CHILD_HANDSHAKE_CACHE[key] = result
    return result


# Execute the exact file whose digest the parent has admitted.
# ``-m dissolve.tea_worker`` imports the package registry first,
# leaking unrelated engine dependencies into the isolated TEA
# environment before the worker can reach its JSON boundary.
# Executing that file as a script is also unsafe: its package
# directory becomes sys.path[0] and shadows the scientific
# ``thermo`` package with the sibling dissolve module. A -c
# bootstrap keeps the source root as sys.path[0], while runpy
# still sets __file__ to the admitted path for the handshake.
LIVE_WORKER_RUNPY_BOOTSTRAP = (
    "import runpy,sys;"
    "worker_path=sys.argv.pop(1);"
    "runpy.run_path(worker_path,run_name='__main__')"
)


def live_worker_runpy_argv(
    worker_config: dict[str, Any],
    *,
    python: Optional[str] = None,
    worker_path: Optional[str] = None,
) -> list[str]:
    """Exact argv ``_launch_live_worker`` uses. Tests must reuse this.

    A refused target (PU) binds bootstrap and JSON without BioSTEAM.
    """
    return [
        python or _tea_worker_python(),
        "-c",
        LIVE_WORKER_RUNPY_BOOTSTRAP,
        worker_path or str(Path(str(tea_worker.__file__)).resolve()),
        json.dumps(worker_config),
    ]


def _launch_live_worker(
    worker_config: dict[str, Any],
    *,
    timeout_seconds: int,
    environment: dict[str, str],
    provenance: dict[str, Any],
) -> dict[str, Any]:
    """Run one fresh worker and verify its source handshake."""
    try:
        completed = subprocess.run(
            live_worker_runpy_argv(
                worker_config,
                worker_path=provenance["worker_source_path"],
            ),
            capture_output=True, text=True, timeout=timeout_seconds,
            env=environment,
            cwd=str(Path(__file__).resolve().parents[1]),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": "BioSTEAM subprocess timed out",
            "error_type": "timeout",
        }
    except OSError as error:
        return {
            "success": False,
            "error": f"BioSTEAM worker interpreter could not start: {error}",
            "error_type": "worker_interpreter_unavailable",
        }
    stdout = (completed.stdout or "")[:10_000_000]
    stderr = (completed.stderr or "")[-2_000:]
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "success": False,
            "error": "BioSTEAM worker returned invalid JSON",
            "error_type": "invalid_worker_output",
            "stderr": stderr,
        }
    if result.get("success") is True:
        child_provenance = result.get("live_provenance") or {}
        child_worker_path = str(
            child_provenance.get("worker_source_path") or ""
        )
        child_worker_sha256 = str(
            child_provenance.get("worker_source_sha256") or ""
        ).casefold()
        expected_worker_path = provenance["worker_source_path"]
        expected_worker_sha256 = provenance["expected_worker_source_sha256"]
        if (
            child_provenance.get("status") != "verified"
            or child_worker_path != expected_worker_path
            or child_worker_sha256 != expected_worker_sha256
        ):
            return {
                "success": False,
                "error": (
                    "Live worker response did not bind to the parent worker "
                    "source: expected "
                    f"{expected_worker_path} ({expected_worker_sha256}), "
                    f"resolved {child_worker_path or '(missing)'} "
                    f"({child_worker_sha256 or '(missing)'})."
                ),
                "error_type": "worker_source_mismatch",
                "live_provenance": {
                    **provenance,
                    "status": "mismatch",
                    "resolved_worker_source_path": child_worker_path or None,
                    "resolved_worker_source_sha256": (
                        child_worker_sha256 or None
                    ),
                },
            }
    if completed.returncode and result.get("success") is not True:
        result.setdefault("stderr", stderr)
    result.setdefault("live_provenance", provenance)
    return result


def _live(config: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    status = live_engine_status()
    if not status["available"]:
        return {
            "success": False,
            "error": status["detail"],
            "error_type": status["reason"],
            **(
                {"live_provenance": status["live_provenance"]}
                if status.get("live_provenance") else {}
            ),
        }
    environment = _tea_worker_environment()
    provenance = status["live_provenance"]
    supplied_lca_cfs = "lca_cfs" in config
    try:
        if supplied_lca_cfs:
            lca_cfs = _validated_lca_cfs(config["lca_cfs"])
            factor_bases = {
                "natural_gas": "kg", "water": "kg",
                "solvent": "kg", "electricity": "MJ",
            }
            transport_offsets: dict[str, float] = {}
            factor_provenance = {
                "source": "config.lca_cfs",
                "metric_factor_sources": {
                    field: "config.lca_cfs"
                    for field in (
                        "gwp_kg_co2e_per_kg", "htc_ctuh_per_kg",
                        "htnc_ctuh_per_kg", "etox_ctue_per_kg",
                    )
                },
                "factor_application_notes": [
                    "Explicit lca_cfs material factors use a per-kg basis and "
                    "electricity factors use a per-MJ basis."
                ],
            }
        else:
            (
                lca_cfs, factor_bases, transport_offsets,
                factor_provenance,
            ) = _governed_live_lca_application(config)
    except (RuntimeError, ValueError) as error:
        return {
            "success": False,
            "error": str(error),
            "error_type": "lca_factor_basis_unavailable",
            "live_provenance": provenance,
        }
    worker_config = {
        **config,
        "lca_cfs": lca_cfs,
        "_lca_factor_bases": factor_bases,
        "_lca_factor_provenance": factor_provenance,
        "_lca_solvent_transport_offsets": transport_offsets,
        "_live_provenance_expectations": {
            "process_model_sha256": provenance[
                "expected_process_model_sha256"
            ],
            "worker_source_path": provenance["worker_source_path"],
            "worker_source_sha256": provenance[
                "expected_worker_source_sha256"
            ],
            "runtime_versions": provenance["expected_runtime_versions"],
            "cited_package_sha256": {
                name: str(
                    ((provenance.get("cited_package_sources") or {}).get(name) or {})
                    .get("sha256") or ""
                )
                for name in tea_polymer_parameters.CITED_STRAP_SOURCE_NAMES
            },
        },
    }
    result = _launch_live_worker(
        worker_config,
        timeout_seconds=timeout_seconds,
        environment=environment,
        provenance=provenance,
    )
    gap_kind = str(
        (result.get("solvent_model_gap") or {}).get("kind") or ""
    )
    admitted_engine = str(
        config.get("_admitted_engine_solvent") or ""
    ).strip()
    if (
        gap_kind in {"invalid_engine_alias", "engine_identity_not_recognized"}
        and admitted_engine
        and config.get("_solvent_identity_retry") is not True
    ):
        retry_config = {
            **worker_config,
            "_engine_solvent": admitted_engine,
            "_allow_cas_safe_alias": True,
            "_solvent_identity_retry": True,
        }
        result = _launch_live_worker(
            retry_config,
            timeout_seconds=timeout_seconds,
            environment=environment,
            provenance=provenance,
        )
        result["solvent_identity_retry"] = {
            "trigger": gap_kind,
            "initial_engine_solvent": config.get("_engine_solvent"),
            "admitted_engine_solvent": admitted_engine,
        }
    return result


def _run(config: dict[str, Any], engine_mode: str, timeout_seconds: int) -> dict[str, Any]:
    mode = str(engine_mode or "auto").strip().casefold()
    if mode not in {"auto", "cache", "live"}:
        return {"success": False, "error": "engine_mode must be auto, cache, or live", "error_type": "invalid_engine_mode"}
    lca_override = "lca_cfs" in config
    record = _cache_index().get(_config_key(config))
    if mode != "live" and record and not lca_override:
        result = copy.deepcopy(record["result"])
        operations = result.get("operations") or {}
        for field in (
            "electricity_consumed_mj_per_kg", "heating_duty_mj_per_kg",
            "cooling_duty_mj_per_kg", "total_energy_mj_per_kg",
            "electricity_intensity_mj_per_kg",
        ):
            if operations.get(field) is not None:
                operations[field] = float(operations[field]) / _LEGACY_OPERATING_HOURS
        result["energy_normalization"] = {
            "status": "corrected_legacy_annual_hour_basis",
            "operating_hours_per_year": _LEGACY_OPERATING_HOURS,
            "basis": "BioSTEAM annual kWh-or-kJ divided by annual resin kg",
        }
        metric_status = _cache_lca_metric_status(
            config, result.get("lca") or {},
        )
        result.update({
            "engine_mode": "cache", "cache_match_status": "exact",
            "cache_record_label": record["label"], "config": config,
            "lca_metric_status": metric_status,
            "lca_status_definitions": _lca_status_definitions(metric_status),
        })
        return result
    if mode == "cache":
        if lca_override:
            return {
                "success": False,
                "error": (
                    "An lca_cfs override requires live execution; cached LCA "
                    "values were generated with the governed factor table."
                ),
                "error_type": "cache_lca_override_unsupported",
                "engine_mode": "cache",
                "cache_match_status": "not_applicable",
                "config": config,
            }
        analog = _record_for_design_point(config)
        deltas = _flowsheet_switch_deltas(config)
        if analog is not None and deltas:
            return {
                "success": False,
                "error": (
                    "A cached record matches the D-8 twelve but not the "
                    "flowsheet switches; refusing to serve the other plant's "
                    "MSP."
                ),
                "error_type": "cache_flowsheet_mismatch",
                "engine_mode": "cache",
                "cache_match_status": "miss",
                "config": config,
                "cache_record_label": analog.get("label"),
                "flowsheet_switch_deltas": deltas,
            }
        return {
            "success": False, "error": "No exact cached simulation matches this configuration.",
            "error_type": "cache_miss", "engine_mode": "cache",
            "cache_match_status": "miss", "config": config,
        }
    result = _live(config, timeout_seconds)
    result.update({
        "engine_mode": "live", "cache_match_status": "bypassed" if mode == "live" else "miss",
        "config": config,
    })
    if result.get("success") is True:
        result = _require_live_parameter_standing(result, config)
    return result


def _require_live_parameter_standing(
    result: dict[str, Any], config: dict[str, Any],
) -> dict[str, Any]:
    """Refuse to serve a live number whose process standing is missing.

    The parameter surface is the authority, not the worker's word.
    Overlay standing from the table on every live success. A provisional
    result without ``process_parameter_status`` is not served — same
    shape as gate_tea (number + standing) and D-8 (structural flag).
    """
    polymer = str(
        config.get("target_plastic") or result.get("target_plastic") or ""
    )
    row = tea_polymer_parameters.polymer_row(polymer)
    if row is None or row.admission != tea_polymer_parameters.ADMISSION_LIVE:
        result.update({
            "success": False,
            "error": (
                "Live TEA produced a number for a polymer that is not "
                "admitted on the parameter surface; refusing to serve it."
            ),
            "error_type": "live_parameter_standing_missing",
        })
        result.pop("tea", None)
        return result
    solvent = str(
        result.get("engine_solvent")
        or config.get("solvent")
        or result.get("solvent")
        or ""
    )
    attached = tea_polymer_parameters.attach_live_parameter_standing(
        result,
        row,
        solvent=solvent,
        config=config,
        plastics_root=tea_polymer_parameters.resolve_plastics_path(),
    )
    if (
        attached.get("can_cite_as_validated_process") is False
        and not attached.get("process_parameter_status")
    ):
        attached.update({
            "success": False,
            "error": (
                "Live TEA produced a provisional number without "
                "process_parameter_status; refusing to serve an unlabelled "
                "result."
            ),
            "error_type": "live_parameter_standing_missing",
        })
        attached.pop("tea", None)
    return attached


def _same_config(
    left: dict[str, Any], right: dict[str, Any], varying: set[str],
) -> bool:
    for key in _CONFIG_FIELDS:
        if key in varying:
            continue
        if key in {"solvent", "target_plastic"}:
            if _key(left.get(key)) != _key(right.get(key)):
                return False
        elif key == "energy_case":
            if str(left.get(key)).upper() != str(right.get(key)).upper():
                return False
        elif not math.isclose(float(left.get(key)), float(right.get(key)), rel_tol=0, abs_tol=1e-9):
            return False
    left_switches = _project_flowsheet_switches(left)
    right_switches = _project_flowsheet_switches(right)
    for key in _FLOWSHEET_SWITCH_FIELDS:
        if key in varying:
            continue
        if left_switches[key] != right_switches[key]:
            return False
    left_coeff = _project_coefficients(left)
    right_coeff = _project_coefficients(right)
    for key in set(left_coeff) | set(right_coeff):
        if key in varying:
            continue
        if key not in left_coeff or key not in right_coeff:
            return False
        if not math.isclose(
            float(left_coeff[key]), float(right_coeff[key]),
            rel_tol=0, abs_tol=1e-12,
        ):
            return False
    return True


def _record_metric(record: dict[str, Any], section: str, field: str) -> float:
    value = float((record["result"].get(section) or {})[field])
    if section == "operations" and field in {
        "electricity_consumed_mj_per_kg", "heating_duty_mj_per_kg",
        "cooling_duty_mj_per_kg", "total_energy_mj_per_kg",
    }:
        value /= _LEGACY_OPERATING_HOURS
    return value


def _power_estimate(
    rows: list[dict[str, Any]], capacity: float, section: str, field: str,
) -> tuple[float, float]:
    points = [
        (math.log(float(row["config"]["processing_capacity"])),
         math.log(_record_metric(row, section, field)))
        for row in rows if _record_metric(row, section, field) > 0
    ]
    if len(points) < 2:
        raise ValueError("At least two cached scale points are required")
    x_mean = statistics.mean(point[0] for point in points)
    y_mean = statistics.mean(point[1] for point in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    if denominator <= 0:
        raise ValueError("Cached scale points must use distinct capacities")
    exponent = sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator
    estimate = math.exp(y_mean + exponent * (math.log(capacity) - x_mean))
    return estimate, exponent


def _linear_slope(
    rows: list[dict[str, Any]], config_field: str, section: str, metric: str,
) -> float:
    points = [
        (float(row["config"][config_field]), _record_metric(row, section, metric))
        for row in rows
    ]
    if len({point[0] for point in points}) < 2:
        return 0.0
    x_mean = statistics.mean(point[0] for point in points)
    y_mean = statistics.mean(point[1] for point in points)
    denominator = sum((x - x_mean) ** 2 for x, _ in points)
    return sum((x - x_mean) * (y - y_mean) for x, y in points) / denominator


def _screening_estimate(
    config: dict[str, Any], failed_evaluation: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    analogs = [
        row for row in _records()
        if _key(row["config"].get("target_plastic")) == _key(config["target_plastic"])
        and str(row["config"].get("energy_case") or "").upper() == config["energy_case"]
    ]
    if not analogs:
        return {
            "success": False, "error": "No cached polymer analog is available.",
            "error_type": "surrogate_unavailable", "config": config,
        }
    def support(row: dict[str, Any]) -> int:
        return sum(
            sum(_same_config(item["config"], row["config"], {field}) for item in analogs)
            for field in (
                "processing_capacity", "solvent_price", "dissolution_temperature_c",
            )
        )

    base = min(analogs, key=lambda row: (
        -support(row),
        abs(float(row["config"]["target_plastic_percent"]) - config["target_plastic_percent"]),
        abs(float(row["config"]["dissolution_temperature_c"]) - config["dissolution_temperature_c"]),
        abs(math.log(float(row["config"]["processing_capacity"]) / config["processing_capacity"])),
    ))
    base_config = base["config"]
    scale_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"processing_capacity"})
    ]
    price_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"solvent_price"})
    ]
    temperature_rows = [
        row for row in analogs
        if _same_config(row["config"], base_config, {"dissolution_temperature_c"})
    ]
    metrics = {
        "msp_usd_per_kg": ("tea", "msp_usd_per_kg"),
        "tci_usd": ("tea", "tci_usd"),
        "aoc_usd_per_yr": ("tea", "aoc_usd_per_yr"),
        "gwp_kg_co2e_per_kg": ("lca", "gwp_kg_co2e_per_kg"),
        "total_energy_mj_per_kg": ("operations", "total_energy_mj_per_kg"),
    }
    estimates: dict[str, float] = {}
    exponents: dict[str, float] = {}
    try:
        for name, (section, field) in metrics.items():
            estimate, exponent = _power_estimate(
                scale_rows, config["processing_capacity"], section, field,
            )
            if section == "tea":
                estimate += _linear_slope(price_rows, "solvent_price", section, field) * (
                    config["solvent_price"] - float(base_config["solvent_price"])
                )
                estimate += _linear_slope(
                    temperature_rows, "dissolution_temperature_c", section, field,
                ) * (
                    config["dissolution_temperature_c"]
                    - float(base_config["dissolution_temperature_c"])
                )
            estimates[name] = max(0.0, estimate)
            exponents[name] = exponent
    except (KeyError, ValueError) as error:
        return {
            "success": False, "error": str(error),
            "error_type": "insufficient_surrogate_evidence", "config": config,
        }
    capacities = sorted(float(row["config"]["processing_capacity"]) for row in scale_rows)
    requested_capacity = float(config["processing_capacity"])
    nearest_capacity_ratio = (
        requested_capacity / capacities[0]
        if requested_capacity < capacities[0]
        else requested_capacity / capacities[-1]
        if requested_capacity > capacities[-1]
        else 1.0
    )
    price_values = {float(row["config"]["solvent_price"]) for row in price_rows}
    temperature_values = {
        float(row["config"]["dissolution_temperature_c"])
        for row in temperature_rows
    }
    assumptions = cache_payload().get("solvent_assumptions") or {}
    metric_status = {
        "gwp_kg_co2e_per_kg": (
            _CACHE_GWP_GRID_STATUS_CODE
            if str(config.get("energy_case") or "").upper() == "C2"
            else _CACHE_GWP_STATUS_CODE
        ),
    }
    return {
        "success": True,
        "tea": {key: estimates[key] for key in (
            "msp_usd_per_kg", "tci_usd", "aoc_usd_per_yr",
        )},
        "lca": {"gwp_kg_co2e_per_kg": estimates["gwp_kg_co2e_per_kg"]},
        "operations": {"total_energy_mj_per_kg": estimates["total_energy_mj_per_kg"]},
        "engine_mode": "screening_estimate", "cache_match_status": "surrogate",
        "config": config,
        "lca_metric_status": metric_status,
        "lca_status_definitions": _lca_status_definitions(metric_status),
        "energy_normalization": {
            "status": "corrected_legacy_annual_hour_basis",
            "operating_hours_per_year": _LEGACY_OPERATING_HOURS,
            "basis": "cached analog annual energy divided by annual product mass",
        },
        "estimate_basis": {
            "quality": "screening_only", "analog_record": base["label"],
            "analog_solvent": base_config["solvent"],
            "scale_record_labels": [row["label"] for row in scale_rows],
            "cached_capacity_range_mt_per_yr": capacities,
            "capacity_extrapolated": not capacities[0] <= requested_capacity <= capacities[-1],
            "capacity_ratio_to_nearest_cached_bound": nearest_capacity_ratio,
            "metric_scale_exponents": exponents,
            "solvent_price_usd_per_kg": config["solvent_price"],
            "solvent_price_adjustment_available": len(price_values) > 1,
            "temperature_adjustment_available": len(temperature_values) > 1,
            "solvent_assumption_source": assumptions.get("source"),
            "solvent_assumption_source_sha256": assumptions.get("source_sha256"),
            "selected_solvent_simulated": False,
            "solvent_substitution_reflected_in_lca": False,
            "fallback_from": {
                key: (failed_evaluation or {}).get(key)
                for key in ("engine_mode", "cache_match_status", "error_type", "error")
                if (failed_evaluation or {}).get(key) is not None
            },
        },
    }


def _comparison_row(label: str, result: dict[str, Any]) -> dict[str, Any]:
    tea, lca, operations = result.get("tea") or {}, result.get("lca") or {}, result.get("operations") or {}
    config = result.get("config") or {}
    switches = _project_flowsheet_switches(config)
    coefficients = _project_coefficients(config)
    return {
        "label": label, "success": bool(result.get("success")),
        "polymer": config.get("target_plastic") or result.get("target_plastic"),
        "target_polymer": config.get("target_plastic") or result.get("target_plastic"),
        "solvent": config.get("solvent") or result.get("solvent"),
        "energy_case": config.get("energy_case") or result.get("energy_case"),
        "target_mass_percent": config.get("target_plastic_percent"),
        "processing_capacity_mt_per_yr": config.get("processing_capacity"),
        "dissolution_temperature_c": config.get("dissolution_temperature_c"),
        "precipitation_temperature_c": config.get("precipitation_temperature_c"),
        "solvent_price_usd_per_kg": config.get("solvent_price"),
        "solvent_loss_pct": config.get("solvent_loss_pct"),
        "feedstock_distance_km": config.get("feedstock_distance_km"),
        "dissolution_capacity": config.get("dissolution_capacity"),
        "labor_cost_usd_per_employee_yr": config.get("labor_cost"),
        "sell_leftover_plastic": switches["sell_leftover_plastic"],
        "burn_leftover_plastic": switches["burn_leftover_plastic"],
        "precipitation_temperature_format": switches[
            "precipitation_temperature_format"
        ],
        "precipitation_configuration": switches["precipitation_configuration"],
        "irr": coefficients.get("irr"),
        "income_tax": coefficients.get("income_tax"),
        "operating_days": coefficients.get("operating_days"),
        "labor_burden": coefficients.get("labor_burden"),
        "finance_interest": coefficients.get("finance_interest"),
        "finance_years": coefficients.get("finance_years"),
        "finance_fraction": coefficients.get("finance_fraction"),
        "startup_months": coefficients.get("startup_months"),
        "startup_FOCfrac": coefficients.get("startup_FOCfrac"),
        "startup_VOCfrac": coefficients.get("startup_VOCfrac"),
        "startup_salesfrac": coefficients.get("startup_salesfrac"),
        "WC_over_FCI": coefficients.get("WC_over_FCI"),
        "warehouse": coefficients.get("warehouse"),
        "site_development": coefficients.get("site_development"),
        "additional_piping": coefficients.get("additional_piping"),
        "proratable_costs": coefficients.get("proratable_costs"),
        "field_expenses": coefficients.get("field_expenses"),
        "construction": coefficients.get("construction"),
        "contingency": coefficients.get("contingency"),
        "other_indirect_costs": coefficients.get("other_indirect_costs"),
        "property_insurance": coefficients.get("property_insurance"),
        "maintenance": coefficients.get("maintenance"),
        "feedstock_price_usd_per_kg": coefficients.get(
            "feedstock_price_usd_per_kg"
        ),
        "centrifuged_plastic_solvent_content_pct": coefficients.get(
            "centrifuged_plastic_solvent_content_pct"
        ),
        **(
            {
                "natural_gas_price_usd_per_m3": coefficients[
                    "natural_gas_price_usd_per_m3"
                ]
            }
            if "natural_gas_price_usd_per_m3" in coefficients else {}
        ),
        "msp_usd_per_kg": tea.get("msp_usd_per_kg"),
        "tci_usd": tea.get("tci_usd"), "aoc_usd_per_yr": tea.get("aoc_usd_per_yr"),
        "gwp_kg_co2e_per_kg": lca.get("gwp_kg_co2e_per_kg"),
        **{
            field: lca[field]
            for field in _LCA_COMPARISON_METRIC_UNITS
            if lca.get(field) is not None
        },
        "water_consumed_m3_per_yr": operations.get("water_consumed_m3_yr"),
        "water_circulated_m3_per_yr": operations.get("water_circulated_m3_yr"),
        "electricity_mj_per_kg": operations.get("electricity_consumed_mj_per_kg"),
        "heating_mj_per_kg": operations.get("heating_duty_mj_per_kg"),
        "cooling_mj_per_kg": operations.get("cooling_duty_mj_per_kg"),
        "total_energy_mj_per_kg": operations.get("total_energy_mj_per_kg"),
        "waste_generated_kg_per_yr": operations.get("waste_generated_kg_yr"),
        "waste_diverted_kg_per_yr": operations.get("waste_diverted_kg_yr"),
        "unit_operations": operations.get("unit_operations"),
        "energy_normalization_status": (result.get("energy_normalization") or {}).get("status"),
        "equipment_count": len((result.get("process_details") or {}).get("equipment") or []),
        "stream_count": len((result.get("process_details") or {}).get("stream_mass_balance") or []),
        "engine_mode": result.get("engine_mode"),
        "cache_record_label": result.get("cache_record_label"),
        "estimate_basis": result.get("estimate_basis"),
        **(
            {
                "lca_metric_status": copy.deepcopy(
                    result["lca_metric_status"],
                )
            }
            if result.get("lca_metric_status") else {}
        ),
        **(
            {
                "process_parameter_status": copy.deepcopy(
                    result["process_parameter_status"],
                )
            }
            if result.get("process_parameter_status") else {}
        ),
        **(
            {
                "process_parameter_status_definitions": copy.deepcopy(
                    result["process_parameter_status_definitions"],
                )
            }
            if result.get("process_parameter_status_definitions") else {}
        ),
        **(
            {
                "live_parameter_standing": copy.deepcopy(
                    result["live_parameter_standing"],
                )
            }
            if result.get("live_parameter_standing") else {}
        ),
        **(
            {
                "can_cite_as_validated_process": result[
                    "can_cite_as_validated_process"
                ]
            }
            if "can_cite_as_validated_process" in result else {}
        ),
        **(
            {"lca_coverage": copy.deepcopy(result["lca_coverage"])}
            if result.get("lca_coverage") else {}
        ),
        **(
            {"live_provenance": copy.deepcopy(result["live_provenance"])}
            if result.get("live_provenance") else {}
        ),
        **(
            {
                key: copy.deepcopy(result[key])
                for key in (
                    "error", "error_type", "requested_solvent",
                    "canonical_solvent", "engine_solvent",
                    "solvent_support_status", "solvent_model_gap",
                    "flowsheet_switch_deltas",
                )
                if result.get(key) is not None
            }
            if not result.get("success") else {}
        ),
    }


def _comparison_metric_units(
    rows: Sequence[dict[str, Any]],
) -> dict[str, str]:
    """Return units only for metrics projected by at least one result row."""
    units = {key: unit for key, (_, _, unit) in _METRICS.items()}
    units.update({
        field: unit
        for field, unit in _LCA_COMPARISON_METRIC_UNITS.items()
        if any(row.get(field) is not None for row in rows)
    })
    return units


def _live_toxicity_data_gaps(
    results: Sequence[dict[str, Any]],
) -> list[str]:
    """Disclose partial live characterization on the public tool payload."""
    partial = [
        result for result in results
        if (result.get("lca_coverage") or {}).get("lca_metrics_status")
        == "partial"
        and result.get("engine_mode") == "live"
        and result.get("success") is True
    ]
    if not partial:
        return []
    contributor_names = sorted({
        str(item.get("name"))
        for result in partial
        for item in (
            (result.get("lca_coverage") or {}).get(
                "uncharacterized_active_contributors"
            ) or []
        )
        if item.get("name")
    })
    contributor_clause = (
        " Uncharacterized active contributors: "
        + ", ".join(contributor_names)
        + "."
        if contributor_names else ""
    )
    application_notes = list(dict.fromkeys(
        str(note)
        for result in partial
        for note in (
            (result.get("lca_coverage") or {}).get(
                "factor_application_notes"
            ) or []
        )
        if str(note).strip()
    ))
    return [
        "Live LCA values are reported separately from characterization "
        "coverage; lca_coverage identifies each metric's factor source and "
        "active contributors that remain uncharacterized."
        + contributor_clause,
        *application_notes,
    ]


def _provenance(modes: Sequence[str]) -> dict[str, Any]:
    generator = cache_payload().get("generator") or {}
    return {
        "engine_modes": sorted(set(modes)), "cache_asset_sha256": _ASSET_SHA256,
        "cache_records": len(_records()), "generation_v10_commit": generator.get("v10_commit"),
        "worker_sha256": generator.get("worker_sha256"),
        "process_model_sha256": generator.get("process_model_sha256"),
        "biosteam_version": generator.get("biosteam_version"),
        "thermosteam_version": generator.get("thermosteam_version"),
        "energy_normalization": "legacy annual/hour basis corrected at v11 cache boundary",
    }


def _process_details(label: str, result: dict[str, Any]) -> Optional[dict[str, Any]]:
    details = result.get("process_details") or {}
    if not details:
        return None
    return {
        "label": label,
        "feed_mass_flow_kg_per_hr": details.get("feed_mass_flow_kg_per_hr"),
        "product_mass_flow_kg_per_hr": details.get("product_mass_flow_kg_per_hr"),
        "mass_balance_closure_kg_per_hr": details.get("mass_balance_closure_kg_per_hr"),
        "stream_mass_balance": list(details.get("stream_mass_balance") or [])[:8],
        "equipment": list(details.get("equipment") or [])[:8],
    }


def _lookup_token(value: Any) -> str:
    return tea_contracts.normalize_tea_vocabulary(value)


def _sensitivity_axis_for_record(label: str) -> str | None:
    normalized = f"-{str(label).casefold().replace('_', '-')}-"
    return next((
        axis for axis, token in _SENSITIVITY_LABEL_TOKEN_BY_AXIS.items()
        if f"-{token}-" in normalized
    ), None)


def _normalize_sensitivity_labels(
    values: Sequence[str] | None,
    axes_values: Sequence[str] | None = None,
) -> tuple[set[str], set[str], list[str]]:
    axes: set[str] = set()
    exact_labels: set[str] = set()
    unknown: list[str] = []
    available_labels = {
        str(record.get("label") or "").casefold(): str(record.get("label") or "")
        for record in _records()
    }
    alias_to_axis = {
        _lookup_token(alias): axis
        for axis, aliases in _SENSITIVITY_AXIS_ALIASES.items()
        for alias in (axis, *aliases)
    }
    for supplied in (*tuple(values or ()), *tuple(axes_values or ())):
        text = str(supplied or "").strip()
        if not text:
            continue
        if text.casefold() in available_labels:
            exact_labels.add(available_labels[text.casefold()])
            continue
        normalized = _lookup_token(text)
        axis = alias_to_axis.get(normalized)
        if axis is None:
            axis = next((
                candidate_axis
                for alias, candidate_axis in alias_to_axis.items()
                if normalized in {
                    f"{alias}_low", f"{alias}_mid", f"{alias}_high",
                }
            ), None)
        if axis is None:
            unknown.append(text)
        else:
            axes.add(axis)
    return axes, exact_labels, unknown


def _normalized_admitted_record(record: dict[str, Any]) -> dict[str, Any]:
    config = copy.deepcopy(record.get("config") or {})
    result = _run(config, "cache", 1)
    label = str(record.get("label") or "")
    axis = _sensitivity_axis_for_record(label)
    level = (
        label.rsplit("-", 1)[-1].casefold()
        if axis is not None and "-" in label else None
    )
    return {
        "record_id": label,
        "record_group": "sensitivity" if axis else "energy_case",
        **({"sensitivity_axis": axis, "sensitivity_level": level} if axis else {}),
        "config": config,
        "economics": copy.deepcopy(result.get("tea") or {}),
        "lca": copy.deepcopy(result.get("lca") or {}),
        "operations": copy.deepcopy(result.get("operations") or {}),
        "energy_normalization": copy.deepcopy(
            result.get("energy_normalization") or {},
        ),
        "lca_metric_status": copy.deepcopy(
            result.get("lca_metric_status") or {},
        ),
    }


def _admitted_record_summary(record: dict[str, Any]) -> dict[str, Any]:
    config = record["config"]
    summary = {
        "label": record["record_id"],
        "record_group": record["record_group"],
        "polymer": config.get("target_plastic"),
        "target_polymer": config.get("target_plastic"),
        "solvent": config.get("solvent"),
        "energy_case": config.get("energy_case"),
        "target_mass_percent": config.get("target_plastic_percent"),
        "processing_capacity_mt_per_yr": config.get("processing_capacity"),
        "dissolution_temperature_c": config.get(
            "dissolution_temperature_c",
        ),
        "precipitation_temperature_c": config.get(
            "precipitation_temperature_c",
        ),
        "solvent_price_usd_per_kg": config.get("solvent_price"),
        "solvent_loss_pct": config.get("solvent_loss_pct"),
        "feedstock_distance_km": config.get("feedstock_distance_km"),
        "dissolution_capacity": config.get("dissolution_capacity"),
        "labor_cost_usd_per_employee_yr": config.get("labor_cost"),
        "energy_normalization_status": record[
            "energy_normalization"
        ].get("status"),
        "engine_mode": "cache",
        "cache_match_status": "exact",
    }
    if record.get("sensitivity_axis"):
        summary.update({
            "sensitivity_axis": record["sensitivity_axis"],
            "sensitivity_level": record.get("sensitivity_level"),
        })
    if record.get("lca_metric_status"):
        summary["lca_metric_status"] = copy.deepcopy(
            record["lca_metric_status"],
        )
    for _metric, (section, field, _unit) in (
        _ADMITTED_RECORD_METRICS.items()
    ):
        value = (record.get(section) or {}).get(field)
        if value is not None:
            summary[field] = value
    return summary


def _lookup_campaign_process_records(
    tool: str,
    *,
    target_polymer: str | list[str] | None,
    solvent: Optional[str],
    energy_cases: Optional[list[Literal["C1", "C2", "C3"]]],
    dissolution_temperature_c: Optional[float],
    processing_capacity_mt_per_yr: Optional[float],
    campaign_fingerprint: Optional[str],
    process_config: Optional[dict[str, Any]],
    allow_partial_campaign: bool,
) -> str:
    from . import campaign_consume

    if not str(campaign_fingerprint or "").strip():
        return tool_error(
            tool,
            "source=campaign requires campaign_fingerprint",
            error_code="missing_campaign_fingerprint",
        )
    if process_config is not None and not isinstance(process_config, dict):
        return tool_error(
            tool,
            "process_config must be an object.",
            error_code="invalid_admitted_record_query",
        )
    requested = dict(process_config or {})
    if energy_cases:
        requested["energy_cases"] = list(energy_cases)
    if dissolution_temperature_c is not None:
        requested["dissolution_temperature_c"] = dissolution_temperature_c
    if processing_capacity_mt_per_yr is not None:
        requested["processing_capacity_mt_per_yr"] = (
            processing_capacity_mt_per_yr
        )
    polymers = None
    resolved_solvent = None
    try:
        if target_polymer not in (None, "", []):
            supplied_polymers = (
                target_polymer
                if isinstance(target_polymer, list)
                else [target_polymer]
            )
            polymers = _expand_polymers(supplied_polymers, "target polymer")
        if solvent:
            resolved_solvent = _resolve_solvent(solvent)
    except _ScenarioInputError as error:
        return tool_error(
            tool, str(error), error_code=error.error_code, **error.details,
        )
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )
    except ValueError as error:
        return tool_error(
            tool, str(error), error_code="invalid_admitted_record_query",
        )
    try:
        payload = campaign_consume.consume_campaign_lookup(
            fingerprint=campaign_fingerprint,
            requested=requested,
            polymers=polymers,
            solvent=resolved_solvent,
            allow_partial=allow_partial_campaign,
        )
    except campaign_consume.CampaignConsumeError as error:
        return tool_error(
            tool, str(error), error_code=error.error_code, **error.details,
        )
    return tool_success(
        tool,
        analysis_type="campaign_process_records",
        engine_mode="campaign",
        **payload,
    )


def lookup_admitted_process_records(
    target_polymer: str | list[str] | None = None,
    solvent: Optional[str] = None,
    energy_cases: Optional[list[Literal["C1", "C2", "C3"]]] = None,
    sensitivity_labels: Optional[list[str]] = None,
    sensitivity_axes: Optional[list[TeaSensitivityAxis]] = None,
    sensitivity_level_selector: Optional[
        TeaSensitivityLevelSelector
    ] = None,
    record_form: TeaRecordForm = "per_record",
    dissolution_temperature_c: Optional[float] = None,
    processing_capacity_mt_per_yr: Optional[float] = None,
    requested_metrics: Optional[
        list[
            Literal[
                "msp", "tci", "aoc", "gwp", "etox", "htc", "htnc",
                "electricity", "heating", "cooling", "energy",
            ]
        ]
    ] = None,
    source: Literal["admitted_cache", "campaign"] = "admitted_cache",
    campaign_fingerprint: Optional[str] = None,
    process_config: Optional[dict[str, Any]] = None,
    allow_partial_campaign: bool = False,
) -> str:
    """Query exact admitted BioSTEAM records without requiring a stored route.

    Admitted-cache lookup still requires a target polymer. Campaign lookup
    (`source=campaign`) requires campaign_fingerprint and locates the
    campaign through DISSOLVE_CAMPAIGN_REGISTRY; it does not ingest JSONL
    into the admitted cache. Solvent, energy cases, exact dissolution
    temperature, exact plant capacity, and sensitivity labels are optional
    filters. With no sensitivity labels, the canonical C1/C2/C3 energy-case
    records are returned. Sensitivity labels accept the seven admitted axes
    (solvent price, plant scale, solvent loss, dissolution temperature,
    precipitation temperature, feedstock distance, and feed composition) or an
    exact cache record label. Returned cache records retain their full
    configuration, economics, LCA, and normalized operations payloads. No
    interpolation, surrogate, or live provider is used.
    """
    tool = "lookup_admitted_process_records"
    source_token = str(source or "admitted_cache").strip().casefold()
    if source_token not in {"admitted_cache", "campaign"}:
        return tool_error(
            tool,
            "source must be admitted_cache or campaign.",
            error_code="invalid_admitted_record_query",
        )
    if source_token == "campaign":
        return _lookup_campaign_process_records(
            tool,
            target_polymer=target_polymer,
            solvent=solvent,
            energy_cases=energy_cases,
            dissolution_temperature_c=dissolution_temperature_c,
            processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
            campaign_fingerprint=campaign_fingerprint,
            process_config=process_config,
            allow_partial_campaign=allow_partial_campaign,
        )
    supplied_polymers = (
        target_polymer
        if isinstance(target_polymer, list)
        else [target_polymer]
    )
    if not any(str(item or "").strip() for item in supplied_polymers):
        return tool_error(
            tool,
            "Admitted-cache lookup requires target_polymer.",
            error_code="missing_target_polymer",
        )
    try:
        polymers = _expand_polymers(supplied_polymers, "target polymer")
        if not polymers:
            return tool_error(
                tool,
                "Admitted-cache lookup requires target_polymer.",
                error_code="missing_target_polymer",
            )
        resolved_solvent = _resolve_solvent(solvent) if solvent else None
        temperature = (
            _finite(dissolution_temperature_c, "dissolution_temperature_c")
            if dissolution_temperature_c is not None else None
        )
        capacity = (
            _finite(
                processing_capacity_mt_per_yr,
                "processing_capacity_mt_per_yr",
            )
            if processing_capacity_mt_per_yr is not None else None
        )
        if capacity is not None and capacity <= 0:
            raise ValueError("processing_capacity_mt_per_yr must be positive")
    except ValueError as error:
        return tool_error(
            tool, str(error), error_code="invalid_admitted_record_query",
        )

    metrics = list(dict.fromkeys(
        _ADMITTED_RECORD_METRIC_ALIASES.get(token, token)
        for value in requested_metrics or list(_ADMITTED_RECORD_METRICS)
        if (
            token := _lookup_token(value)
        )
    ))
    invalid_metrics = sorted(
        set(metrics) - set(_ADMITTED_RECORD_METRICS),
    )
    if invalid_metrics:
        return tool_error(
            tool,
            "Unsupported admitted-record metrics: "
            + ", ".join(invalid_metrics) + ".",
            error_code="invalid_admitted_record_metrics",
            available_metrics=list(_ADMITTED_RECORD_METRICS),
        )
    requested_cases = list(dict.fromkeys(
        tea_contracts.canonical_tea_energy_case(value)
        for value in energy_cases or []
        if str(value or "").strip()
    ))
    invalid_cases = sorted(set(requested_cases) - set(_ENERGY_CASES))
    if invalid_cases:
        return tool_error(
            tool,
            "energy_cases must contain only C1, C2, or C3.",
            error_code="invalid_admitted_energy_case",
            invalid_energy_cases=invalid_cases,
        )
    axes, exact_labels, unknown_labels = _normalize_sensitivity_labels(
        sensitivity_labels, sensitivity_axes,
    )
    if unknown_labels:
        return tool_error(
            tool,
            "Unknown sensitivity labels: " + ", ".join(unknown_labels) + ".",
            error_code="unknown_admitted_sensitivity_label",
            available_sensitivity_axes=list(_SENSITIVITY_AXIS_ALIASES),
            available_sensitivity_labels=sorted(
                str(item.get("label") or "")
                for item in _records()
                if _sensitivity_axis_for_record(
                    str(item.get("label") or ""),
                )
            ),
        )
    canonical_level_selector = (
        tea_contracts.canonical_tea_sensitivity_level_selector(
            sensitivity_level_selector,
        )
        if sensitivity_level_selector is not None else None
    )
    if (
        canonical_level_selector is not None
        and canonical_level_selector
        not in tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS
    ):
        return tool_error(
            tool,
            "Unknown sensitivity level selector.",
            error_code="unknown_sensitivity_level_selector",
            available_sensitivity_level_selectors=list(
                tea_contracts.CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS,
            ),
        )
    canonical_record_form = tea_contracts.canonical_tea_record_form(
        record_form,
    )
    if canonical_record_form not in tea_contracts.CANONICAL_TEA_RECORD_FORMS:
        return tool_error(
            tool,
            "Unknown admitted-record output form.",
            error_code="unknown_admitted_record_form",
            available_record_forms=list(
                tea_contracts.CANONICAL_TEA_RECORD_FORMS,
            ),
        )

    pair_records = [
        record for record in _records()
        if any(
            _key((record.get("config") or {}).get("target_plastic"))
            == _key(polymer)
            for polymer in polymers
        )
        and (
            resolved_solvent is None
            or _key((record.get("config") or {}).get("solvent"))
            == _key(resolved_solvent)
        )
    ]
    sensitivity_requested = bool(sensitivity_labels or sensitivity_axes)
    if sensitivity_requested:
        selected = [
            record for record in pair_records
            if (
                str(record.get("label") or "") in exact_labels
                or _sensitivity_axis_for_record(
                    str(record.get("label") or ""),
                ) in axes
            )
        ]
        baseline = next((
            record for record in pair_records
            if str(record.get("label") or "").casefold().endswith(
                "-route-c1",
            )
        ), None)
        if baseline is not None and baseline not in selected:
            selected.insert(0, baseline)
    else:
        cases = requested_cases or list(_ENERGY_CASES)
        selected = [
            record for record in pair_records
            if str(record.get("label") or "").casefold().endswith(tuple(
                f"-route-{case.casefold()}" for case in cases
            ))
        ]
    selected_levels = tea_contracts.tea_sensitivity_levels(
        canonical_level_selector,
    )
    if sensitivity_requested and selected_levels:
        by_axis: dict[str, list[dict[str, Any]]] = {}
        ungrouped: list[dict[str, Any]] = []
        for record in selected:
            axis = _sensitivity_axis_for_record(
                str(record.get("label") or ""),
            )
            if axis is None:
                ungrouped.append(record)
            else:
                by_axis.setdefault(axis, []).append(record)
        selected = list(ungrouped)
        for axis in tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES:
            records = by_axis.get(axis, [])
            endpoint_records = [
                record for record in records
                if str(record.get("label") or "").casefold().rsplit(
                    "-", 1,
                )[-1] in selected_levels
            ]
            # Some admitted axes use ``mid`` for the lower measured endpoint.
            # Select the stored endpoint pair without relabeling source data.
            selected.extend(
                endpoint_records
                if len(endpoint_records) >= 2
                else (
                    records
                    if len(records) <= 2
                    else [records[0], records[-1]]
                )
            )
    if temperature is not None:
        selected = [
            record for record in selected
            if math.isclose(
                float(record["config"]["dissolution_temperature_c"]),
                temperature,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    if capacity is not None:
        selected = [
            record for record in selected
            if math.isclose(
                float(record["config"]["processing_capacity"]),
                capacity,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        ]
    if not selected:
        available_pair_configs = [{
            "record_id": str(record.get("label") or ""),
            "solvent": (record.get("config") or {}).get("solvent"),
            "energy_case": (record.get("config") or {}).get("energy_case"),
            "processing_capacity_mt_per_yr": (
                record.get("config") or {}
            ).get("processing_capacity"),
            "dissolution_temperature_c": (
                record.get("config") or {}
            ).get("dissolution_temperature_c"),
        } for record in pair_records[:24]]
        return tool_error(
            tool,
            "No admitted process record matches the requested configuration.",
            error_code="admitted_process_record_gap",
            analysis_type="admitted_process_record_gap",
            target_polymer=(
                polymers[0] if len(polymers) == 1 else None
            ),
            target_polymers=polymers,
            solvent=resolved_solvent,
            requested_energy_cases=requested_cases,
            requested_sensitivity_labels=list(sensitivity_labels or []),
            dissolution_temperature_c=temperature,
            processing_capacity_mt_per_yr=capacity,
            requested_metrics=metrics,
            can_estimate_msp=False,
            can_estimate_gwp=False,
            exact_cache_basis_available=False,
            available_pair_configurations=available_pair_configs,
            cached_target_polymers=sorted({
                str((record.get("config") or {}).get("target_plastic"))
                for record in _records()
            }),
            missing_basis_codes=["admitted_configuration"],
            process_data_gaps=[
                "The admitted cache contains no record for this exact "
                "polymer, solvent, condition, and requested record group.",
            ],
            provenance=_provenance(["cache"]),
        )

    normalized_records = [
        _normalized_admitted_record(record) for record in selected
    ]
    comparison_rows = [
        _admitted_record_summary(record) for record in normalized_records
    ]
    units = {
        metric: _ADMITTED_RECORD_METRICS[metric][2]
        for metric in metrics
    }
    baseline = next((
        row for row in normalized_records
        if str(row["record_id"]).casefold().endswith("-route-c1")
    ), None)
    assumptions = [{
        "record_id": row["record_id"],
        **copy.deepcopy(row["config"]),
    } for row in normalized_records]
    status_definitions = _collect_lca_status_definitions(
        normalized_records,
    )
    return tool_success(
        tool,
        analysis_type=(
            "admitted_process_sensitivity_records"
            if sensitivity_requested
            else "admitted_process_energy_case_records"
        ),
        engine_mode="cache",
        cache_match_status="exact",
        target_polymer=(
            polymers[0] if len(polymers) == 1 else None
        ),
        target_polymers=polymers,
        solvent=resolved_solvent,
        requested_metrics=metrics,
        requested_energy_cases=requested_cases or list(_ENERGY_CASES),
        selected_sensitivity_labels=[
            axis
            for axis in tea_contracts.CANONICAL_TEA_SENSITIVITY_AXES
            if axis in axes
        ],
        sensitivity_level_selector=canonical_level_selector,
        record_form=canonical_record_form,
        record_count=len(normalized_records),
        baseline_record_id=(
            baseline["record_id"] if baseline is not None else None
        ),
        records=normalized_records,
        comparison_rows=comparison_rows,
        metric_units=units,
        record_assumptions=assumptions,
        record_basis=(
            "exact admitted BioSTEAM cache records; no interpolation, "
            "surrogate, or live execution"
        ),
        energy_normalization_status="corrected_legacy_annual_hour_basis",
        energy_case_descriptions=dict(_ENERGY_CASES),
        exact_cache_basis_available=True,
        lca_method_status_by_energy_case=_lca_method_status_by_energy_case(
            normalized_records,
        ),
        lca_status_definitions=status_definitions,
        provenance=_provenance(["cache"]),
        process_data_gaps=[
            "These are admitted single-process records, not a newly integrated "
            "multistage route or experimental recovery/purity result.",
            *_cache_lca_status_gaps(normalized_records),
        ],
        warnings=[
            "Energy intensities apply the recorded legacy annual-hour "
            "normalization correction at the cache boundary.",
            "Every metric uses the product basis and configuration stored with "
            "its record; records are not interpolated across conditions.",
        ],
    )


def _load_process_rows_handle(
    handle: Any,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Load tool-1 rows already in a handle. Rank does not consume JSONL here."""
    token = handle.strip() if isinstance(handle, str) else ""
    if not isinstance(handle, str) or not token:
        raise _ScenarioInputError(
            "unknown handle",
            error_code="unknown_handle",
            handle=handle,
        )
    record = current_tool_session()
    stored = load_handle(record, token) if record is not None else None
    if stored is None:
        raise _ScenarioInputError(
            "unknown handle",
            error_code="unknown_handle",
            handle=token,
        )
    source_tool = str(stored.get("tool") or "")
    if source_tool not in _TOOL1_PROCESS_ROWS_TOOLS:
        raise _ScenarioInputError(
            "handle is not a tool-1 process_rows source",
            error_code="not_economics_handle",
            handle=token,
            source_tool=source_tool,
        )
    try:
        rows = _economics_handle_rows(stored)
    except (ValueError, KeyError, TypeError):
        raise _ScenarioInputError(
            "handle has no economics comparison rows",
            error_code="not_economics_handle",
            handle=token,
            source_tool=source_tool,
        )
    return source_tool, rows, stored


def _campaign_handle_exact(stored: dict[str, Any]) -> dict[str, Any] | None:
    exact = stored.get("exact") if isinstance(stored, dict) else None
    if not isinstance(exact, dict):
        return None
    if str(exact.get("source") or "").strip().casefold() != "campaign":
        return None
    if not str(exact.get("campaign_fingerprint") or "").strip():
        return None
    return exact


def _campaign_rank_census(exact: dict[str, Any]) -> dict[str, Any]:
    census: dict[str, Any] = {}
    for key in (
        "campaign_fingerprint",
        "append_log_fingerprint",
        "campaign_basis",
        "campaign_basis_projection",
        "campaign_definition_schema",
    ):
        if key in exact:
            census[key] = exact[key]
    return census


def _rank_handle_engine_mode(rows: Sequence[dict[str, Any]]) -> str:
    modes = {
        str(row.get("engine_mode") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("engine_mode") or "").strip()
    }
    if modes == {"cache"}:
        return "cache"
    if len(modes) == 1:
        return next(iter(modes))
    return "handle"


_SUPERSTRUCTURE_FORMULATIONS = (
    "sequence",
    "solvent",
    "sequence_solvent",
    "wash_train",
)


def _formulation_token(formulation: Any) -> str:
    return str(formulation or "").strip().casefold()


def _supplied_feed_names(target_polymer: Any) -> list[str]:
    if target_polymer in (None, "", []):
        return []
    if isinstance(target_polymer, list):
        return [str(item) for item in target_polymer]
    return [str(target_polymer)]


def _planner_solvent_map_shape(value: Any) -> bool:
    if not isinstance(value, dict) or not value:
        return False
    for polymer, solvent in value.items():
        if not str(polymer or "").strip():
            return False
        if not isinstance(solvent, str) or not solvent.strip():
            return False
    return True


def _allowed_solvents_shape(value: Any) -> bool:
    if isinstance(value, list):
        return bool(value) and all(
            isinstance(item, str) and item.strip() for item in value
        )
    if not isinstance(value, dict) or not value:
        return False
    for polymer, solvents in value.items():
        if not str(polymer or "").strip():
            return False
        if not isinstance(solvents, (list, tuple)) or not solvents:
            return False
        if not all(isinstance(item, str) and item.strip() for item in solvents):
            return False
    return True


def _public_solvent_token(value: str) -> str:
    supplied = str(value).strip()
    return thermo.resolve_solvent(supplied) or supplied


def _canonical_planner_solvent_map(value: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for raw_polymer, raw_solvent in value.items():
        members = _expand_polymers([raw_polymer], "planner solvent map polymer")
        solvent = _public_solvent_token(raw_solvent)
        for member in members:
            mapping[member] = solvent
    return mapping


def _canonical_allowed_solvent_map(value: dict[str, Any]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for raw_polymer, raw_solvents in value.items():
        members = _expand_polymers([raw_polymer], "allowed solvents polymer")
        solvents: list[str] = []
        for item in raw_solvents:
            token = _public_solvent_token(item)
            if token not in solvents:
                solvents.append(token)
        for member in members:
            mapping[member] = list(solvents)
    return mapping


def _canonical_allowed_solvent_keys(value: dict[str, Any]) -> set[str]:
    return set(_canonical_allowed_solvent_map(value))


def _remnant_cell(
    polymer: str,
    solvent: Any,
    target_mass_percent: Any = None,
    processing_capacity_mt_per_yr: Any = None,
) -> dict[str, Any]:
    return {
        "polymer": polymer,
        "target_mass_percent": target_mass_percent,
        "processing_capacity_mt_per_yr": processing_capacity_mt_per_yr,
        "solvent": solvent,
    }


def _unnamed_remnant_cell(**fields: Any) -> dict[str, Any]:
    cell = {
        "polymer": None,
        "target_mass_percent": None,
        "processing_capacity_mt_per_yr": None,
        "solvent": None,
    }
    cell.update(fields)
    return cell


def _requested_plant_capacity(process_config: Any) -> float | None:
    """Plant scale from process_config. Do not default 20 kt."""
    if not isinstance(process_config, dict):
        return None
    if (
        "processing_capacity_mt_per_yr" in process_config
        and process_config["processing_capacity_mt_per_yr"] is not None
    ):
        raw = process_config["processing_capacity_mt_per_yr"]
    elif (
        "processing_capacity" in process_config
        and process_config["processing_capacity"] is not None
    ):
        raw = process_config["processing_capacity"]
    else:
        return None
    capacity = _finite(raw, "processing_capacity_mt_per_yr")
    if capacity <= 0:
        raise ValueError("processing_capacity_mt_per_yr must be positive")
    return capacity


def _requested_energy_case(process_config: Any) -> str | None:
    """Held energy_case from process_config. Do not default C1."""
    if not isinstance(process_config, dict):
        return None
    if "energy_case" not in process_config or process_config["energy_case"] in (
        None, "",
    ):
        return None
    token = str(process_config["energy_case"]).strip().upper()
    if token not in _ENERGY_CASES:
        raise _ScenarioInputError(
            "energy_case must be C1, C2, or C3",
            error_code="invalid_admitted_record_query",
            energy_case=str(process_config["energy_case"]).strip(),
        )
    return token


def _requested_dissolution_temperature_c(process_config: Any) -> float | None:
    """Held dissolution T from process_config. Do not default a cache T."""
    if not isinstance(process_config, dict):
        return None
    if (
        "dissolution_temperature_c" in process_config
        and process_config["dissolution_temperature_c"] not in (None, "")
    ):
        raw = process_config["dissolution_temperature_c"]
    elif (
        "dissolution_temp_c" in process_config
        and process_config["dissolution_temp_c"] not in (None, "")
    ):
        raw = process_config["dissolution_temp_c"]
    else:
        return None
    return round(_finite(raw, "dissolution_temperature_c"), 10)


def _requested_precipitation_temperature_c(process_config: Any) -> float | None:
    """Held precipitation T from process_config. Do not default a cache T."""
    if not isinstance(process_config, dict):
        return None
    if (
        "precipitation_temperature_c" in process_config
        and process_config["precipitation_temperature_c"] not in (None, "")
    ):
        raw = process_config["precipitation_temperature_c"]
    elif (
        "precipitation_temp_c" in process_config
        and process_config["precipitation_temp_c"] not in (None, "")
    ):
        raw = process_config["precipitation_temp_c"]
    else:
        return None
    return round(_finite(raw, "precipitation_temperature_c"), 10)


def _requested_solvent_price_usd_per_kg(process_config: Any) -> float | None:
    """Held solvent price from process_config. Do not default a cache price."""
    if not isinstance(process_config, dict):
        return None
    if (
        "solvent_price_usd_per_kg" in process_config
        and process_config["solvent_price_usd_per_kg"] not in (None, "")
    ):
        raw = process_config["solvent_price_usd_per_kg"]
    elif (
        "solvent_price" in process_config
        and process_config["solvent_price"] not in (None, "")
    ):
        raw = process_config["solvent_price"]
    else:
        return None
    return round(_finite(raw, "solvent_price_usd_per_kg"), 10)


def _requested_solvent_loss_pct(process_config: Any) -> float | None:
    """Held solvent loss from process_config. Do not default 0.01."""
    if not isinstance(process_config, dict):
        return None
    if (
        "solvent_loss_pct" not in process_config
        or process_config["solvent_loss_pct"] in (None, "")
    ):
        return None
    return round(
        _finite(process_config["solvent_loss_pct"], "solvent_loss_pct"), 10,
    )


def _requested_feedstock_distance_km(process_config: Any) -> float | None:
    """Held haul distance from process_config. Do not default 0 km."""
    if not isinstance(process_config, dict):
        return None
    if (
        "feedstock_distance_km" not in process_config
        or process_config["feedstock_distance_km"] in (None, "")
    ):
        return None
    return round(
        _finite(process_config["feedstock_distance_km"], "feedstock_distance_km"),
        10,
    )


def _requested_dissolution_capacity(process_config: Any) -> float | None:
    """Held dissolution capacity from process_config. Do not default 3.0."""
    if not isinstance(process_config, dict):
        return None
    if (
        "dissolution_capacity" not in process_config
        or process_config["dissolution_capacity"] in (None, "")
    ):
        return None
    return round(
        _finite(process_config["dissolution_capacity"], "dissolution_capacity"),
        10,
    )


def _requested_labor_cost_usd_per_employee_yr(
    process_config: Any,
) -> float | None:
    """Held labor cost from process_config. Do not default 120000."""
    if not isinstance(process_config, dict):
        return None
    if (
        "labor_cost_usd_per_employee_yr" in process_config
        and process_config["labor_cost_usd_per_employee_yr"] not in (None, "")
    ):
        raw = process_config["labor_cost_usd_per_employee_yr"]
    elif (
        "labor_cost" in process_config
        and process_config["labor_cost"] not in (None, "")
    ):
        raw = process_config["labor_cost"]
    else:
        return None
    return round(_finite(raw, "labor_cost_usd_per_employee_yr"), 10)


def _requested_sell_leftover_plastic(process_config: Any) -> bool | None:
    """Held leftover-sale switch. Do not default False."""
    if not isinstance(process_config, dict):
        return None
    if (
        "sell_leftover_plastic" not in process_config
        or process_config["sell_leftover_plastic"] in (None, "")
    ):
        return None
    try:
        return _coerce_flowsheet_bool(
            process_config["sell_leftover_plastic"], "sell_leftover_plastic",
        )
    except _ScenarioInputError:
        raise _ScenarioInputError(
            "sell_leftover_plastic must be a boolean",
            error_code="invalid_admitted_record_query",
            field="sell_leftover_plastic",
            supplied=process_config["sell_leftover_plastic"],
        )


def _requested_burn_leftover_plastic(process_config: Any) -> bool | None:
    """Held leftover-burn switch. Do not default False."""
    if not isinstance(process_config, dict):
        return None
    if (
        "burn_leftover_plastic" not in process_config
        or process_config["burn_leftover_plastic"] in (None, "")
    ):
        return None
    try:
        return _coerce_flowsheet_bool(
            process_config["burn_leftover_plastic"], "burn_leftover_plastic",
        )
    except _ScenarioInputError:
        raise _ScenarioInputError(
            "burn_leftover_plastic must be a boolean",
            error_code="invalid_admitted_record_query",
            field="burn_leftover_plastic",
            supplied=process_config["burn_leftover_plastic"],
        )


def _requested_precipitation_temperature_format(
    process_config: Any,
) -> str | None:
    """Held precipitation-temperature mode. Do not default constant."""
    if not isinstance(process_config, dict):
        return None
    if (
        "precipitation_temperature_format" not in process_config
        or process_config["precipitation_temperature_format"] in (None, "")
    ):
        return None
    token = str(
        process_config["precipitation_temperature_format"]
    ).strip()
    if token not in _PRECIPITATION_FORMATS:
        raise _ScenarioInputError(
            "precipitation_temperature_format must be 'constant' or 'drop'",
            error_code="invalid_admitted_record_query",
            field="precipitation_temperature_format",
            supplied=process_config["precipitation_temperature_format"],
        )
    return token


def _requested_precipitation_configuration(
    process_config: Any,
) -> str | None:
    """Held precipitation configuration. Do not default integrated heat transfer."""
    if not isinstance(process_config, dict):
        return None
    if (
        "precipitation_configuration" not in process_config
        or process_config["precipitation_configuration"] in (None, "")
    ):
        return None
    token = str(process_config["precipitation_configuration"]).strip()
    if token not in _PRECIPITATION_CONFIGURATIONS:
        raise _ScenarioInputError(
            "precipitation_configuration must be 'integrated heat "
            "transfer' or 'solvent mixing'",
            error_code="invalid_admitted_record_query",
            field="precipitation_configuration",
            supplied=process_config["precipitation_configuration"],
        )
    return token


def _requested_irr(process_config: Any) -> float | None:
    """Held IRR. Do not default 0.10."""
    if not isinstance(process_config, dict):
        return None
    if "irr" not in process_config or process_config["irr"] in (None, ""):
        return None
    irr = _finite(process_config["irr"], "irr")
    if irr >= 1:
        raise _ScenarioInputError(
            "irr is a fraction (0.10 is 10 percent), not a percent integer.",
            error_code="invalid_admitted_record_query",
            field="irr",
            supplied=process_config["irr"],
        )
    if not 0 < irr < 1:
        raise _ScenarioInputError(
            "irr must be a fraction between 0 and 1 exclusive.",
            error_code="invalid_admitted_record_query",
            field="irr",
            supplied=process_config["irr"],
        )
    return round(irr, 10)


def _requested_income_tax(process_config: Any) -> float | None:
    """Held income tax. Do not default 0.21."""
    if not isinstance(process_config, dict):
        return None
    if (
        "income_tax" not in process_config
        or process_config["income_tax"] in (None, "")
    ):
        return None
    tax = _finite(process_config["income_tax"], "income_tax")
    if tax >= 1:
        raise _ScenarioInputError(
            "income_tax is a fraction (0.21 is 21 percent), not a "
            "percent integer.",
            error_code="invalid_admitted_record_query",
            field="income_tax",
            supplied=process_config["income_tax"],
        )
    if not 0 <= tax < 1:
        raise _ScenarioInputError(
            "income_tax must be a fraction in [0, 1).",
            error_code="invalid_admitted_record_query",
            field="income_tax",
            supplied=process_config["income_tax"],
        )
    return round(tax, 10)


def _requested_operating_days(process_config: Any) -> float | None:
    """Held operating days. Do not default 350.4."""
    if not isinstance(process_config, dict):
        return None
    if (
        "operating_days" not in process_config
        or process_config["operating_days"] in (None, "")
    ):
        return None
    days = _finite(process_config["operating_days"], "operating_days")
    if days <= 0:
        raise _ScenarioInputError(
            "operating_days must be positive.",
            error_code="invalid_admitted_record_query",
            field="operating_days",
            supplied=process_config["operating_days"],
        )
    return round(days, 10)


def _requested_labor_burden(process_config: Any) -> float | None:
    """Held labor burden. Do not default 0.90. Do not refuse ≥1."""
    if not isinstance(process_config, dict):
        return None
    if (
        "labor_burden" not in process_config
        or process_config["labor_burden"] in (None, "")
    ):
        return None
    burden = _finite(process_config["labor_burden"], "labor_burden")
    if burden < 0:
        raise _ScenarioInputError(
            "labor_burden must be nonnegative.",
            error_code="invalid_admitted_record_query",
            field="labor_burden",
            supplied=process_config["labor_burden"],
        )
    return round(burden, 10)


def _requested_finance_interest(process_config: Any) -> float | None:
    """Held finance interest. Do not default 0.08."""
    if not isinstance(process_config, dict):
        return None
    if (
        "finance_interest" not in process_config
        or process_config["finance_interest"] in (None, "")
    ):
        return None
    rate = _finite(process_config["finance_interest"], "finance_interest")
    if rate >= 1:
        raise _ScenarioInputError(
            "finance_interest is a fraction (0.08 is 8 percent), not a "
            "percent integer.",
            error_code="invalid_admitted_record_query",
            field="finance_interest",
            supplied=process_config["finance_interest"],
        )
    if not 0 <= rate < 1:
        raise _ScenarioInputError(
            "finance_interest must be a fraction in [0, 1).",
            error_code="invalid_admitted_record_query",
            field="finance_interest",
            supplied=process_config["finance_interest"],
        )
    return round(rate, 10)


def _requested_finance_years(process_config: Any) -> float | None:
    """Held finance years. Do not default 10."""
    if not isinstance(process_config, dict):
        return None
    if (
        "finance_years" not in process_config
        or process_config["finance_years"] in (None, "")
    ):
        return None
    years = _finite(process_config["finance_years"], "finance_years")
    if years <= 0:
        raise _ScenarioInputError(
            "finance_years must be positive.",
            error_code="invalid_admitted_record_query",
            field="finance_years",
            supplied=process_config["finance_years"],
        )
    if not float(years).is_integer():
        raise _ScenarioInputError(
            "finance_years must be a whole number of years.",
            error_code="invalid_admitted_record_query",
            field="finance_years",
            supplied=process_config["finance_years"],
        )
    return round(years, 10)


def _requested_finance_fraction(process_config: Any) -> float | None:
    """Held finance fraction. Do not default 0."""
    if not isinstance(process_config, dict):
        return None
    if (
        "finance_fraction" not in process_config
        or process_config["finance_fraction"] in (None, "")
    ):
        return None
    fraction = _finite(process_config["finance_fraction"], "finance_fraction")
    if fraction > 1:
        raise _ScenarioInputError(
            "finance_fraction is a fraction (0.4 is 40 percent), not a "
            "percent integer.",
            error_code="invalid_admitted_record_query",
            field="finance_fraction",
            supplied=process_config["finance_fraction"],
        )
    if not 0 <= fraction <= 1:
        raise _ScenarioInputError(
            "finance_fraction must be a fraction in [0, 1].",
            error_code="invalid_admitted_record_query",
            field="finance_fraction",
            supplied=process_config["finance_fraction"],
        )
    return round(fraction, 10)


def _requested_startup_months(process_config: Any) -> float | None:
    """Held startup months. Do not default 3."""
    if not isinstance(process_config, dict):
        return None
    if (
        "startup_months" not in process_config
        or process_config["startup_months"] in (None, "")
    ):
        return None
    months = _finite(process_config["startup_months"], "startup_months")
    if months < 0:
        raise _ScenarioInputError(
            "startup_months must be nonnegative.",
            error_code="invalid_admitted_record_query",
            field="startup_months",
            supplied=process_config["startup_months"],
        )
    if months > 12:
        raise _ScenarioInputError(
            "startup_months must be at most 12.",
            error_code="invalid_admitted_record_query",
            field="startup_months",
            supplied=process_config["startup_months"],
        )
    return round(months, 10)


def _requested_startup_FOCfrac(process_config: Any) -> float | None:
    """Held startup FOC fraction. Do not default 1."""
    if not isinstance(process_config, dict):
        return None
    if (
        "startup_FOCfrac" not in process_config
        or process_config["startup_FOCfrac"] in (None, "")
    ):
        return None
    fraction = _finite(process_config["startup_FOCfrac"], "startup_FOCfrac")
    if fraction > 1:
        raise _ScenarioInputError(
            "startup_FOCfrac is a fraction (1 is 100 percent), not a "
            "percent integer.",
            error_code="invalid_admitted_record_query",
            field="startup_FOCfrac",
            supplied=process_config["startup_FOCfrac"],
        )
    if not 0 <= fraction <= 1:
        raise _ScenarioInputError(
            "startup_FOCfrac must be a fraction in [0, 1].",
            error_code="invalid_admitted_record_query",
            field="startup_FOCfrac",
            supplied=process_config["startup_FOCfrac"],
        )
    return round(fraction, 10)


def _requested_startup_VOCfrac(process_config: Any) -> float | None:
    """Held startup VOC fraction. Do not default 0.75."""
    if not isinstance(process_config, dict):
        return None
    if (
        "startup_VOCfrac" not in process_config
        or process_config["startup_VOCfrac"] in (None, "")
    ):
        return None
    fraction = _finite(process_config["startup_VOCfrac"], "startup_VOCfrac")
    if fraction > 1:
        raise _ScenarioInputError(
            "startup_VOCfrac is a fraction (0.75 is 75 percent), not a "
            "percent integer.",
            error_code="invalid_admitted_record_query",
            field="startup_VOCfrac",
            supplied=process_config["startup_VOCfrac"],
        )
    if not 0 <= fraction <= 1:
        raise _ScenarioInputError(
            "startup_VOCfrac must be a fraction in [0, 1].",
            error_code="invalid_admitted_record_query",
            field="startup_VOCfrac",
            supplied=process_config["startup_VOCfrac"],
        )
    return round(fraction, 10)


def _superstructure_composition_and_capacity(
    feed_mass_fractions: Any,
    process_config: Any,
) -> tuple[dict[str, float] | None, float | None]:
    composition = None
    if feed_mass_fractions is not None:
        composition = _composition(feed_mass_fractions)
    return composition, _requested_plant_capacity(process_config)


def _align_feed_with_composition(
    feed: list[str],
    composition: dict[str, float] | None,
) -> list[str]:
    resolved = _expand_polymers(feed, "target polymer") if feed else []
    if composition is None:
        return resolved
    names = list(composition)
    if resolved and set(resolved) != set(names):
        raise _ScenarioInputError(
            "target_polymer must match feed_mass_fractions",
            error_code="invalid_admitted_record_query",
            feed=resolved,
            composition_polymers=names,
        )
    return names


def _d18_remnant_bases(
    composition: dict[str, float],
    plant_capacity: float | None,
) -> list[dict[str, Any]]:
    """Union of remnant (polymer, mass%, capacity) cells. Not a lookup table."""
    polymers = sorted(composition)
    bases: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for polymer in polymers:
        others = [name for name in polymers if name != polymer]
        for count in range(len(others) + 1):
            for taken in combinations(others, count):
                remaining = {
                    name: composition[name]
                    for name in polymers
                    if name not in taken
                }
                entering = sum(remaining.values())
                mass_pct = round(100.0 * composition[polymer] / entering, 10)
                capacity = (
                    round(plant_capacity * entering, 10)
                    if plant_capacity is not None
                    else None
                )
                key = (polymer, mass_pct, capacity)
                if key in seen:
                    continue
                seen.add(key)
                bases.append({
                    "polymer": polymer,
                    "target_mass_percent": mass_pct,
                    "processing_capacity_mt_per_yr": capacity,
                })
    return bases


def _solvents_by_polymer_from_cells(
    cells: list[dict[str, Any]],
) -> tuple[dict[str, list[Any]], list[Any]]:
    solvents_by_polymer: dict[str, list[Any]] = {}
    unnamed: list[Any] = []
    for cell in cells:
        polymer = cell["polymer"]
        solvent = cell["solvent"]
        if polymer is None:
            if solvent not in unnamed:
                unnamed.append(solvent)
            continue
        solvents_by_polymer.setdefault(polymer, [])
        if solvent not in solvents_by_polymer[polymer]:
            solvents_by_polymer[polymer].append(solvent)
    return solvents_by_polymer, unnamed


def _expand_identity_cells_with_d18(
    identity_cells: list[dict[str, Any]],
    composition: dict[str, float],
    plant_capacity: float | None,
) -> list[dict[str, Any]]:
    solvents_by_polymer, unnamed = _solvents_by_polymer_from_cells(
        identity_cells,
    )
    cells: list[dict[str, Any]] = []
    for base in _d18_remnant_bases(composition, plant_capacity):
        solvents = solvents_by_polymer.get(base["polymer"]) or unnamed or [None]
        for solvent in solvents:
            cells.append(_remnant_cell(
                base["polymer"],
                solvent,
                target_mass_percent=base["target_mass_percent"],
                processing_capacity_mt_per_yr=base[
                    "processing_capacity_mt_per_yr"
                ],
            ))
    return cells or identity_cells


def _missing_keys_from_sequence_solvent(
    *,
    planner_solvent_map: Any,
    allowed_solvents: Any,
    feed: list[str],
    composition: dict[str, float] | None = None,
    plant_capacity: float | None = None,
) -> list[dict[str, Any]]:
    """Name remnant×solvent cells the caller identified. Do not fill them."""
    if _allowed_solvents_shape(allowed_solvents):
        identity = _missing_keys_from_allowed(allowed_solvents, feed)
    elif _planner_solvent_map_shape(planner_solvent_map):
        mapping = _canonical_planner_solvent_map(planner_solvent_map)
        identity = _missing_keys_from_planner_map(mapping)
    elif feed:
        identity = [_unnamed_remnant_cell(polymer=name) for name in feed]
    else:
        identity = [_unnamed_remnant_cell()]
    if composition is None:
        return identity
    return _expand_identity_cells_with_d18(
        identity, composition, plant_capacity,
    )


def _missing_keys_from_planner_map(
    mapping: dict[str, str],
    *,
    composition: dict[str, float] | None = None,
    plant_capacity: float | None = None,
) -> list[dict[str, Any]]:
    identity = [
        _remnant_cell(polymer, mapping[polymer])
        for polymer in sorted(mapping)
    ]
    if composition is None:
        return identity
    return _expand_identity_cells_with_d18(
        identity, composition, plant_capacity,
    )


def _missing_keys_from_allowed(
    allowed_solvents: Any,
    feed: list[str],
) -> list[dict[str, Any]]:
    if isinstance(allowed_solvents, list):
        solvents = []
        for item in allowed_solvents:
            token = _public_solvent_token(item)
            if token not in solvents:
                solvents.append(token)
        polymers = list(feed) if feed else []
        if not polymers:
            return [
                {
                    "polymer": None,
                    "target_mass_percent": None,
                    "processing_capacity_mt_per_yr": None,
                    "solvent": solvent,
                }
                for solvent in solvents
            ]
        return [
            _remnant_cell(polymer, solvent)
            for polymer in polymers
            for solvent in solvents
        ]
    mapping = _canonical_allowed_solvent_map(allowed_solvents)
    return [
        _remnant_cell(polymer, solvent)
        for polymer in sorted(mapping)
        for solvent in mapping[polymer]
    ]


def _incomplete_stage_basis_grid(
    *,
    formulation: str,
    missing_keys: list[dict[str, Any]],
) -> str:
    details: dict[str, Any] = {
        "source": "superstructure",
        "formulation": formulation,
        "missing_keys": missing_keys,
        "error_type": "incomplete_stage_basis_grid",
    }
    if formulation == "sequence":
        details["pending_blockers"] = [
            {
                "error_type": "sequence_coupling_unproven",
                "gate": "gate_sequence_order_coupling",
                "status": "would_fire_if_remnant_grid_complete",
            },
        ]
    return tool_error(
        "rank_landscape",
        "remnant-basis coefficient table is incomplete",
        error_code="incomplete_stage_basis_grid",
        **details,
    )


def _sequence_coupling_unproven(formulation: str) -> str:
    return tool_error(
        "rank_landscape",
        "sequence-order coupling is unproven",
        error_code="sequence_coupling_unproven",
        error_type="sequence_coupling_unproven",
        source="superstructure",
        formulation=formulation,
        gate="gate_sequence_order_coupling",
    )


def _remnant_key_from_row(
    row: Any,
    *,
    energy_case: str | None = None,
    dissolution_temperature_c: float | None = None,
    precipitation_temperature_c: float | None = None,
    solvent_price_usd_per_kg: float | None = None,
    solvent_loss_pct: float | None = None,
    feedstock_distance_km: float | None = None,
    dissolution_capacity: float | None = None,
    labor_cost_usd_per_employee_yr: float | None = None,
    sell_leftover_plastic: bool | None = None,
    burn_leftover_plastic: bool | None = None,
    precipitation_temperature_format: str | None = None,
    precipitation_configuration: str | None = None,
    irr: float | None = None,
    income_tax: float | None = None,
    operating_days: float | None = None,
    labor_burden: float | None = None,
    finance_interest: float | None = None,
    finance_years: float | None = None,
    finance_fraction: float | None = None,
    startup_months: float | None = None,
    startup_FOCfrac: float | None = None,
    startup_VOCfrac: float | None = None,
) -> tuple[Any, ...] | None:
    """Remnant coordinate of a tool-1 row. Failures are not a fill."""
    if not isinstance(row, dict) or row.get("success") is False:
        return None
    raw_polymer = row.get("target_polymer") or row.get("polymer")
    raw_solvent = row.get("solvent")
    mass = row.get("target_mass_percent")
    capacity = row.get("processing_capacity_mt_per_yr")
    if raw_polymer in (None, "") or raw_solvent in (None, ""):
        return None
    if mass is None or capacity is None:
        return None
    try:
        polymer = _resolve_polymer(raw_polymer, "handle polymer")
        mass_n = round(_finite(mass, "target_mass_percent"), 10)
        cap_n = round(_finite(capacity, "processing_capacity_mt_per_yr"), 10)
    except (ValueError, _InputError, _ScenarioInputError):
        return None
    key: tuple[Any, ...] = (
        polymer, mass_n, cap_n, _public_solvent_token(str(raw_solvent)),
    )
    if energy_case is not None:
        row_case = str(row.get("energy_case") or "").strip().upper()
        if row_case != energy_case:
            return None
        key = key + (energy_case,)
    for field, held in (
        ("dissolution_temperature_c", dissolution_temperature_c),
        ("precipitation_temperature_c", precipitation_temperature_c),
        ("solvent_price_usd_per_kg", solvent_price_usd_per_kg),
        ("solvent_loss_pct", solvent_loss_pct),
        ("feedstock_distance_km", feedstock_distance_km),
        ("dissolution_capacity", dissolution_capacity),
        ("labor_cost_usd_per_employee_yr", labor_cost_usd_per_employee_yr),
    ):
        if held is None:
            continue
        raw = row.get(field)
        if raw in (None, ""):
            return None
        try:
            row_v = round(_finite(raw, field), 10)
        except ValueError:
            return None
        if row_v != held:
            return None
        key = key + (held,)
    for field, held in (
        ("sell_leftover_plastic", sell_leftover_plastic),
        ("burn_leftover_plastic", burn_leftover_plastic),
    ):
        if held is None:
            continue
        raw = row.get(field)
        if raw in (None, ""):
            return None
        try:
            row_v = _coerce_flowsheet_bool(raw, field)
        except _ScenarioInputError:
            return None
        if row_v != held:
            return None
        key = key + (held,)
    for field, held in (
        ("precipitation_temperature_format", precipitation_temperature_format),
        ("precipitation_configuration", precipitation_configuration),
    ):
        if held is None:
            continue
        raw = row.get(field)
        if raw in (None, ""):
            return None
        if str(raw).strip() != held:
            return None
        key = key + (held,)
    for field, held in (
        ("irr", irr),
        ("income_tax", income_tax),
        ("operating_days", operating_days),
        ("labor_burden", labor_burden),
        ("finance_interest", finance_interest),
        ("finance_years", finance_years),
        ("finance_fraction", finance_fraction),
        ("startup_months", startup_months),
        ("startup_FOCfrac", startup_FOCfrac),
        ("startup_VOCfrac", startup_VOCfrac),
    ):
        if held is None:
            continue
        raw = row.get(field)
        if raw in (None, ""):
            return None
        try:
            row_v = round(_finite(raw, field), 10)
        except ValueError:
            return None
        if row_v != held:
            return None
        key = key + (held,)
    return key


def _cell_remnant_key(cell: dict[str, Any]) -> tuple[Any, ...] | None:
    polymer = cell.get("polymer")
    solvent = cell.get("solvent")
    mass = cell.get("target_mass_percent")
    capacity = cell.get("processing_capacity_mt_per_yr")
    if polymer is None or solvent in (None, "") or mass is None or capacity is None:
        return None
    try:
        mass_n = round(_finite(mass, "target_mass_percent"), 10)
        cap_n = round(_finite(capacity, "processing_capacity_mt_per_yr"), 10)
    except ValueError:
        return None
    key: tuple[Any, ...] = (polymer, mass_n, cap_n, solvent)
    case = cell.get("energy_case")
    if case not in (None, ""):
        key = key + (str(case).strip().upper(),)
    for field in (
        "dissolution_temperature_c",
        "precipitation_temperature_c",
        "solvent_price_usd_per_kg",
        "solvent_loss_pct",
        "feedstock_distance_km",
        "dissolution_capacity",
        "labor_cost_usd_per_employee_yr",
    ):
        temp = cell.get(field)
        if temp not in (None, ""):
            try:
                key = key + (round(_finite(temp, field), 10),)
            except ValueError:
                return None
    for field in ("sell_leftover_plastic", "burn_leftover_plastic"):
        value = cell.get(field)
        if value not in (None, ""):
            try:
                key = key + (_coerce_flowsheet_bool(value, field),)
            except _ScenarioInputError:
                return None
    for field in (
        "precipitation_temperature_format",
        "precipitation_configuration",
    ):
        value = cell.get(field)
        if value not in (None, ""):
            key = key + (str(value).strip(),)
    for field in (
        "irr", "income_tax", "operating_days", "labor_burden",
        "finance_interest", "finance_years", "finance_fraction",
        "startup_months", "startup_FOCfrac", "startup_VOCfrac",
    ):
        value = cell.get(field)
        if value not in (None, ""):
            try:
                key = key + (round(_finite(value, field), 10),)
            except ValueError:
                return None
    return key


def _held_rounded_field(
    missing_keys: list[dict[str, Any]], field: str,
) -> float | None:
    return next(
        (
            round(_finite(cell[field], field), 10)
            for cell in missing_keys
            if cell.get(field) not in (None, "")
        ),
        None,
    )


def _held_bool_field(
    missing_keys: list[dict[str, Any]], field: str,
) -> bool | None:
    return next(
        (
            _coerce_flowsheet_bool(cell[field], field)
            for cell in missing_keys
            if cell.get(field) not in (None, "")
        ),
        None,
    )


def _unmatched_remnant_keys(
    missing_keys: list[dict[str, Any]],
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    held_case = next(
        (
            str(cell["energy_case"]).strip().upper()
            for cell in missing_keys
            if cell.get("energy_case") not in (None, "")
        ),
        None,
    )
    held_dissolution = _held_rounded_field(
        missing_keys, "dissolution_temperature_c",
    )
    held_precipitation = _held_rounded_field(
        missing_keys, "precipitation_temperature_c",
    )
    held_price = _held_rounded_field(
        missing_keys, "solvent_price_usd_per_kg",
    )
    held_loss = _held_rounded_field(missing_keys, "solvent_loss_pct")
    held_distance = _held_rounded_field(missing_keys, "feedstock_distance_km")
    held_dissolution_capacity = _held_rounded_field(
        missing_keys, "dissolution_capacity",
    )
    held_labor = _held_rounded_field(
        missing_keys, "labor_cost_usd_per_employee_yr",
    )
    held_sell = _held_bool_field(missing_keys, "sell_leftover_plastic")
    held_burn = _held_bool_field(missing_keys, "burn_leftover_plastic")
    held_format = next(
        (
            str(cell["precipitation_temperature_format"]).strip()
            for cell in missing_keys
            if cell.get("precipitation_temperature_format") not in (None, "")
        ),
        None,
    )
    held_configuration = next(
        (
            str(cell["precipitation_configuration"]).strip()
            for cell in missing_keys
            if cell.get("precipitation_configuration") not in (None, "")
        ),
        None,
    )
    held_irr = _held_rounded_field(missing_keys, "irr")
    held_income_tax = _held_rounded_field(missing_keys, "income_tax")
    held_operating_days = _held_rounded_field(missing_keys, "operating_days")
    held_labor_burden = _held_rounded_field(missing_keys, "labor_burden")
    held_finance_interest = _held_rounded_field(missing_keys, "finance_interest")
    held_finance_years = _held_rounded_field(missing_keys, "finance_years")
    held_finance_fraction = _held_rounded_field(
        missing_keys, "finance_fraction",
    )
    held_startup_months = _held_rounded_field(missing_keys, "startup_months")
    held_startup_FOCfrac = _held_rounded_field(
        missing_keys, "startup_FOCfrac",
    )
    held_startup_VOCfrac = _held_rounded_field(
        missing_keys, "startup_VOCfrac",
    )
    present = {
        key for row in rows
        if (
            key := _remnant_key_from_row(
                row,
                energy_case=held_case,
                dissolution_temperature_c=held_dissolution,
                precipitation_temperature_c=held_precipitation,
                solvent_price_usd_per_kg=held_price,
                solvent_loss_pct=held_loss,
                feedstock_distance_km=held_distance,
                dissolution_capacity=held_dissolution_capacity,
                labor_cost_usd_per_employee_yr=held_labor,
                sell_leftover_plastic=held_sell,
                burn_leftover_plastic=held_burn,
                precipitation_temperature_format=held_format,
                precipitation_configuration=held_configuration,
                irr=held_irr,
                income_tax=held_income_tax,
                operating_days=held_operating_days,
                labor_burden=held_labor_burden,
                finance_interest=held_finance_interest,
                finance_years=held_finance_years,
                finance_fraction=held_finance_fraction,
                startup_months=held_startup_months,
                startup_FOCfrac=held_startup_FOCfrac,
                startup_VOCfrac=held_startup_VOCfrac,
            )
        ) is not None
    }
    unmatched: list[dict[str, Any]] = []
    for cell in missing_keys:
        key = _cell_remnant_key(cell)
        if key is not None and key in present:
            continue
        unmatched.append(cell)
    return unmatched


def _refuse_listed_or_complete(
    *,
    formulation: str,
    missing_keys: list[dict[str, Any]],
    handle: Any,
    process_config: Any = None,
) -> str | None:
    """Subtract handle rows from listed D-18 keys. Not a cache or campaign fill."""
    held = {
        "energy_case": _requested_energy_case(process_config),
        "dissolution_temperature_c": (
            _requested_dissolution_temperature_c(process_config)
        ),
        "precipitation_temperature_c": (
            _requested_precipitation_temperature_c(process_config)
        ),
        "solvent_price_usd_per_kg": (
            _requested_solvent_price_usd_per_kg(process_config)
        ),
        "solvent_loss_pct": _requested_solvent_loss_pct(process_config),
        "feedstock_distance_km": _requested_feedstock_distance_km(process_config),
        "dissolution_capacity": _requested_dissolution_capacity(process_config),
        "labor_cost_usd_per_employee_yr": (
            _requested_labor_cost_usd_per_employee_yr(process_config)
        ),
        "sell_leftover_plastic": _requested_sell_leftover_plastic(process_config),
        "burn_leftover_plastic": _requested_burn_leftover_plastic(process_config),
        "precipitation_temperature_format": (
            _requested_precipitation_temperature_format(process_config)
        ),
        "precipitation_configuration": (
            _requested_precipitation_configuration(process_config)
        ),
        "irr": _requested_irr(process_config),
        "income_tax": _requested_income_tax(process_config),
        "operating_days": _requested_operating_days(process_config),
        "labor_burden": _requested_labor_burden(process_config),
        "finance_interest": _requested_finance_interest(process_config),
        "finance_years": _requested_finance_years(process_config),
        "finance_fraction": _requested_finance_fraction(process_config),
        "startup_months": _requested_startup_months(process_config),
        "startup_FOCfrac": _requested_startup_FOCfrac(process_config),
        "startup_VOCfrac": _requested_startup_VOCfrac(process_config),
    }
    if any(value is not None for value in held.values()):
        stamped: list[dict[str, Any]] = []
        for cell in missing_keys:
            item = dict(cell)
            for name, value in held.items():
                if value is not None:
                    item[name] = value
            stamped.append(item)
        missing_keys = stamped
    if handle is not None:
        _source, rows, _stored = _load_process_rows_handle(handle)
        missing_keys = _unmatched_remnant_keys(missing_keys, rows)
    if missing_keys:
        return _incomplete_stage_basis_grid(
            formulation=formulation,
            missing_keys=missing_keys,
        )
    if formulation == "sequence":
        return _sequence_coupling_unproven(formulation)
    return None


def _superstructure_map_refusal(
    *,
    formulation: Any,
    planner_solvent_map: Any,
    allowed_solvents: Any,
    target_polymer: Any,
    feed_mass_fractions: Any = None,
    process_config: Any = None,
    handle: Any = None,
) -> str | None:
    """Missing-map refuses, then incomplete grid or D-20. Not a ranking."""
    tool = "rank_landscape"
    token = _formulation_token(formulation)
    feed = _supplied_feed_names(target_polymer)

    def _identity_error(error: Exception) -> str:
        if isinstance(error, _ScenarioInputError):
            return tool_error(
                tool, str(error), error_code=error.error_code, **error.details,
            )
        if isinstance(error, _InputError):
            return tool_error(
                tool, str(error), error_code=error.code, **error.detail,
            )
        return tool_error(
            tool, str(error), error_code="invalid_admitted_record_query",
        )

    if not token:
        return tool_error(
            tool,
            "formulation is required on source=superstructure",
            error_code="missing_formulation",
            source="superstructure",
            legal_formulations=list(_SUPERSTRUCTURE_FORMULATIONS),
        )
    if token not in _SUPERSTRUCTURE_FORMULATIONS:
        return tool_error(
            tool,
            "formulation must be sequence, solvent, sequence_solvent, "
            "or wash_train.",
            error_code="invalid_admitted_record_query",
            source="superstructure",
            formulation=token,
            legal_formulations=list(_SUPERSTRUCTURE_FORMULATIONS),
        )
    if token == "wash_train":
        return tool_error(
            tool,
            "formulation=wash_train is unavailable until the process "
            "model exposes wash-train algebra",
            error_code="process_model_wash_train_unavailable",
            source="superstructure",
            formulation=token,
        )
    if token == "sequence_solvent":
        try:
            composition, plant_capacity = (
                _superstructure_composition_and_capacity(
                    feed_mass_fractions, process_config,
                )
            )
            resolved_feed = _align_feed_with_composition(feed, composition)
            missing_keys = _missing_keys_from_sequence_solvent(
                planner_solvent_map=planner_solvent_map,
                allowed_solvents=allowed_solvents,
                feed=resolved_feed,
                composition=composition,
                plant_capacity=plant_capacity,
            )
            return _refuse_listed_or_complete(
                formulation=token,
                missing_keys=missing_keys,
                handle=handle,
                process_config=process_config,
            )
        except (_ScenarioInputError, _InputError, ValueError) as error:
            return _identity_error(error)
    if token == "sequence":
        if not _planner_solvent_map_shape(planner_solvent_map):
            return tool_error(
                tool,
                "formulation=sequence requires planner_solvent_map",
                error_code="missing_planner_solvent_map",
                formulation=token,
                polymers=feed,
                feed=feed,
            )
        try:
            mapping = _canonical_planner_solvent_map(planner_solvent_map)
            composition, plant_capacity = (
                _superstructure_composition_and_capacity(
                    feed_mass_fractions, process_config,
                )
            )
            resolved_feed = _align_feed_with_composition(feed, composition)
            missing = [name for name in resolved_feed if name not in mapping]
            if missing:
                return tool_error(
                    tool,
                    "formulation=sequence requires planner_solvent_map",
                    error_code="missing_planner_solvent_map",
                    formulation=token,
                    polymers=missing,
                    feed=resolved_feed,
                )
            return _refuse_listed_or_complete(
                formulation=token,
                missing_keys=_missing_keys_from_planner_map(
                    mapping,
                    composition=composition,
                    plant_capacity=plant_capacity,
                ),
                handle=handle,
                process_config=process_config,
            )
        except (_ScenarioInputError, _InputError, ValueError) as error:
            return _identity_error(error)
    if token == "solvent":
        if not _allowed_solvents_shape(allowed_solvents):
            return tool_error(
                tool,
                "formulation=solvent requires allowed_solvents",
                error_code="missing_allowed_solvents",
                formulation=token,
                polymers=feed,
                feed=feed,
            )
        try:
            composition, _plant_capacity = (
                _superstructure_composition_and_capacity(
                    feed_mass_fractions, process_config,
                )
            )
            resolved_feed = _align_feed_with_composition(feed, composition)
            if isinstance(allowed_solvents, list):
                return _refuse_listed_or_complete(
                    formulation=token,
                    missing_keys=_missing_keys_from_allowed(
                        allowed_solvents, resolved_feed,
                    ),
                    handle=handle,
                    process_config=process_config,
                )
            keys = _canonical_allowed_solvent_keys(allowed_solvents)
            missing = [name for name in resolved_feed if name not in keys]
            if missing:
                return tool_error(
                    tool,
                    "formulation=solvent requires allowed_solvents",
                    error_code="missing_allowed_solvents",
                    formulation=token,
                    polymers=missing,
                    feed=resolved_feed,
                )
            return _refuse_listed_or_complete(
                formulation=token,
                missing_keys=_missing_keys_from_allowed(
                    allowed_solvents, resolved_feed,
                ),
                handle=handle,
                process_config=process_config,
            )
        except (_ScenarioInputError, _InputError, ValueError) as error:
            return _identity_error(error)
    return None


def rank_landscape(
    source: Literal["process_rows", "residual_route", "superstructure"] = (
        "process_rows"
    ),
    operation: Literal[
        "sort", "pareto_dominance", "optimum", "epsilon",
    ] = "pareto_dominance",
    campaign_fingerprint: Optional[str] = None,
    target_polymer: str | list[str] | None = None,
    solvent: Optional[str] = None,
    energy_cases: Optional[list[Literal["C1", "C2", "C3"]]] = None,
    process_config: Optional[dict[str, Any]] = None,
    polymer_grouping: Literal[
        "per_target_polymer", "mixed_polymer",
    ] = "per_target_polymer",
    formulation: Optional[str] = None,
    allow_partial_campaign: bool = False,
    handle: Optional[str] = None,
    planner_solvent_map: Optional[dict[str, Any]] = None,
    allowed_solvents: Optional[dict[str, Any] | list[Any]] = None,
    feed_mass_fractions: Optional[dict[str, Any]] = None,
    **unexpected: Any,
) -> str:
    """Rank already-run process rows. Does not spawn BioSTEAM.

    source=process_rows ranks a tool-1 handle (evaluate batch or admitted
    lookup) at the configs those rows were run at, or locates a campaign
    through DISSOLVE_CAMPAIGN_REGISTRY and campaign_fingerprint. The usable
    projection returns the landscape AND the frontier. Held-field mismatch
    is not a ranking. source=superstructure takes planner_solvent_map.v1
    (formulation=sequence) or allowed_solvents.v1 (formulation=solvent) as
    arguments, not a third public tool. Missing maps refuse by name before
    incomplete_stage_basis_grid. A bound map still has no remnant table:
    the data-gate lists missing (polymer, mass%, capacity, solvent) cells
    and, for formulation=sequence, attaches pending_blockers for D-20.
    When feed_mass_fractions is supplied, those cells use the D-18 remnant
    subset union (n × 2^{n−1}); plant capacity comes from process_config
    and is not defaulted to 20 kt. Maps name solvents only. A tool-1
    handle on source=superstructure is the remnant coefficient table:
    rows matching listed D-18 keys are subtracted; unmatched stay
    missing. When process_config names energy_case, listed keys include
    that held case and matching requires it; omitted energy_case is not
    a silent C1 default. When process_config names
    dissolution_temperature_c, precipitation_temperature_c,
    solvent_price_usd_per_kg, solvent_loss_pct,
    feedstock_distance_km, dissolution_capacity,
    labor_cost_usd_per_employee_yr, sell_leftover_plastic,
    burn_leftover_plastic, precipitation_temperature_format,
    precipitation_configuration, irr, income_tax, operating_days,
    labor_burden, finance_interest, finance_years, finance_fraction,
    startup_months, startup_FOCfrac, or startup_VOCfrac, listed keys
    include that held value and matching requires it; omitted is not a
    silent cache or production default. A complete sequence grid with production check red is
    sequence_coupling_unproven as primary (no pending_blockers).
    Do not scan top_k_sequences for either map.
    formulation is required iff source=superstructure;
    formulation=wash_train is
    process_model_wash_train_unavailable. formulation=sequence_solvent
    lists the remnant×solvent table as incomplete_stage_basis_grid and
    does not take a shortlist or maps as a coefficient fill. The rest of
    the twelve-field key and epsilon are not this slice.
    """
    tool = "rank_landscape"
    if unexpected:
        return tool_error(
            tool,
            "unknown extra argument",
            error_code="unknown_process_field",
            extra_keys=sorted(str(key) for key in unexpected),
        )
    source_token = str(source or "process_rows").strip().casefold()
    operation_token = str(operation or "pareto_dominance").strip().casefold()
    grouping_token = str(
        polymer_grouping or "per_target_polymer"
    ).strip().casefold()
    if grouping_token not in {"per_target_polymer", "mixed_polymer"}:
        return tool_error(
            tool,
            "polymer_grouping must be per_target_polymer or mixed_polymer.",
            error_code="invalid_admitted_record_query",
        )
    if source_token not in {"process_rows", "residual_route", "superstructure"}:
        return tool_error(
            tool,
            "source must be process_rows, residual_route, or superstructure.",
            error_code="invalid_admitted_record_query",
        )
    map_fields = [
        name for name, value in (
            ("planner_solvent_map", planner_solvent_map),
            ("allowed_solvents", allowed_solvents),
            ("feed_mass_fractions", feed_mass_fractions),
        )
        if value is not None
    ]
    if map_fields and source_token != "superstructure":
        return tool_error(
            tool,
            "planner_solvent_map, allowed_solvents, and feed_mass_fractions "
            "apply on source=superstructure",
            error_code="not_applicable_in_source",
            source=source_token,
            inapplicable_fields=map_fields,
        )
    if source_token in {"residual_route", "superstructure"}:
        if source_token == "superstructure":
            refused = _superstructure_map_refusal(
                formulation=formulation,
                planner_solvent_map=planner_solvent_map,
                allowed_solvents=allowed_solvents,
                target_polymer=target_polymer,
                feed_mass_fractions=feed_mass_fractions,
                process_config=process_config,
                handle=handle,
            )
            if refused is not None:
                return refused
        return tool_error(
            tool,
            "this slice ranks process_rows only",
            error_code="tool_not_wired",
            source=source_token,
        )
    if operation_token in {"optimum", "epsilon"}:
        return tool_error(
            tool,
            "optimum and epsilon are not legal on source=process_rows",
            error_code="not_applicable_in_source",
            source=source_token,
            operation=operation_token,
        )
    if operation_token not in {"sort", "pareto_dominance"}:
        return tool_error(
            tool,
            "operation must be sort or pareto_dominance on process_rows.",
            error_code="not_applicable_in_source",
            source=source_token,
            operation=operation_token,
        )
    if formulation is not None and str(formulation).strip() != "":
        return tool_error(
            tool,
            "formulation is not applicable on source=process_rows",
            error_code="not_applicable_in_source",
            source=source_token,
        )
    if process_config is not None and not isinstance(process_config, dict):
        return tool_error(
            tool,
            "process_config must be an object.",
            error_code="invalid_admitted_record_query",
        )
    from . import campaign_consume, landscape

    requested = dict(process_config or {})
    if energy_cases:
        requested["energy_cases"] = list(energy_cases)
    polymers = None
    resolved_solvent = None
    try:
        if target_polymer not in (None, "", []):
            supplied = (
                target_polymer
                if isinstance(target_polymer, list)
                else [target_polymer]
            )
            polymers = _expand_polymers(supplied, "target polymer")
        if solvent:
            resolved_solvent = _resolve_solvent(solvent)
    except _ScenarioInputError as error:
        return tool_error(
            tool, str(error), error_code=error.error_code, **error.details,
        )
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )
    except ValueError as error:
        return tool_error(
            tool, str(error), error_code="invalid_admitted_record_query",
        )
    if handle is not None:
        try:
            _source_tool, rows, stored = _load_process_rows_handle(handle)
            campaign_exact = _campaign_handle_exact(stored)
            if campaign_exact is not None:
                handle_fp = str(
                    campaign_exact.get("campaign_fingerprint") or "",
                ).strip()
                supplied_fp = str(campaign_fingerprint or "").strip()
                if supplied_fp and supplied_fp.casefold() != handle_fp.casefold():
                    raise campaign_consume.CampaignConsumeError(
                        "campaign_fingerprint disagrees with the handle",
                        error_code="campaign_fingerprint_mismatch",
                        supplied=supplied_fp,
                        canonical=handle_fp,
                    )
                if requested:
                    campaign_consume.prepare_registered_campaign(
                        fingerprint=handle_fp,
                        requested=requested,
                        allow_partial=allow_partial_campaign,
                    )
                payload = landscape.rank_handle_process_rows(
                    rows,
                    polymers=polymers,
                    solvent=resolved_solvent,
                    polymer_grouping=grouping_token,
                    operation=operation_token,
                    skip_campaign_identity=False,
                    canonical=handle_fp.casefold(),
                    extra_census=_campaign_rank_census(campaign_exact),
                )
                return tool_success(
                    tool,
                    analysis_type="campaign_process_rows_landscape",
                    engine_mode="campaign",
                    source="process_rows",
                    **payload,
                )
            payload = landscape.rank_handle_process_rows(
                rows,
                polymers=polymers,
                solvent=resolved_solvent,
                polymer_grouping=grouping_token,
                operation=operation_token,
            )
        except _ScenarioInputError as error:
            return tool_error(
                tool, str(error), error_code=error.error_code, **error.details,
            )
        except campaign_consume.CampaignConsumeError as error:
            return tool_error(
                tool, str(error), error_code=error.error_code, **error.details,
            )
        return tool_success(
            tool,
            analysis_type="process_rows_landscape",
            engine_mode=_rank_handle_engine_mode(rows),
            source="process_rows",
            **payload,
        )
    try:
        bound = campaign_consume.prepare_registered_campaign(
            fingerprint=campaign_fingerprint,
            requested=requested,
            allow_partial=allow_partial_campaign,
        )
        payload = landscape.rank_process_rows(
            bound,
            polymers=polymers,
            solvent=resolved_solvent,
            polymer_grouping=grouping_token,
            operation=operation_token,
        )
    except campaign_consume.CampaignConsumeError as error:
        return tool_error(
            tool, str(error), error_code=error.error_code, **error.details,
        )
    return tool_success(
        tool,
        analysis_type="campaign_process_rows_landscape",
        engine_mode="campaign",
        source="process_rows",
        **payload,
    )


_LOOKUP_MODE_SELECTORS = (
    "sensitivity_labels",
    "sensitivity_axes",
    "sensitivity_level_selector",
)
_INAPPLICABLE_ON_EVALUATE = {
    **{name: "lookup" for name in _LOOKUP_MODE_SELECTORS},
    "energy_cases": "lookup",
    "record_form": "lookup",
    "requested_metrics": "lookup",
    "parameter": "sensitivity",
    "values": "sensitivity",
    "analysis_mode": "sensitivity",
    "metric": "sensitivity",
}


def evaluate_tea_lca_scenarios(
    scenarios: Optional[list[dict[str, Any]]] = None,
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
    screening_shortlist: Optional[dict[str, Any]] = None,
    held_process_basis: Optional[dict[str, Any]] = None,
    handle: Optional[str] = None,
    row_id: Optional[str | int] = None,
    **kwargs: Any,
) -> str:
    """Evaluate complete independent scenarios, or fill omitted fields from a handle.

    Each scenario requires the twelve public D-8 process fields unless a
    prior economics handle supplies the omitted ones. screening_shortlist.v1
    plus held_process_basis.v1 expands to one complete process_config per
    item (three from_screen, nine supplied). The same shortlist may inherit
    the nine from an economics handle when held_process_basis is omitted.
    temperature_c maps to dissolution_temperature_c only on that handoff.
    Unknown extra keys refuse. Wrong-mode scalars (lookup selectors,
    energy_cases, record_form, requested_metrics, parameter / values /
    analysis_mode / metric) refuse not_applicable_in_mode. Omitted
    switches and coefficients keep the production plant. This is not a
    third public TEA name and does not fill the nine from a cache pair or
    a screening payload.
    """
    tool = "evaluate_tea_lca_scenarios"
    inapplicable = [
        name for name in _INAPPLICABLE_ON_EVALUATE if name in kwargs
    ]
    leftover = {
        name: kwargs[name]
        for name in kwargs
        if name not in _INAPPLICABLE_ON_EVALUATE
    }
    if inapplicable:
        modes = list(dict.fromkeys(
            _INAPPLICABLE_ON_EVALUATE[name] for name in inapplicable
        ))
        details: dict[str, Any] = {
            "mode": "evaluate",
            "inapplicable_fields": inapplicable,
            "applicable_mode_by_field": {
                name: _INAPPLICABLE_ON_EVALUATE[name] for name in inapplicable
            },
        }
        if len(modes) == 1:
            details["applicable_mode"] = modes[0]
        return tool_error(
            tool,
            "These arguments are not applicable in evaluate mode.",
            error_code="not_applicable_in_mode",
            **details,
        )
    if leftover:
        unexpected = ", ".join(repr(name) for name in sorted(leftover))
        raise TypeError(
            "evaluate_tea_lca_scenarios() got unexpected keyword "
            f"argument(s): {unexpected}"
        )
    field_origin = None
    field_origins = None
    try:
        scenarios, field_origin, field_origins = _compose_evaluate_scenarios(
            scenarios, screening_shortlist, held_process_basis, handle, row_id,
        )
    except _ScenarioInputError as error:
        return tool_error(
            tool,
            str(error),
            error_code=error.error_code,
            **error.details,
        )
    if not isinstance(scenarios, list) or not scenarios:
        return tool_error(
            tool, "scenarios must be a non-empty list",
            error_code="missing_scenarios",
        )
    if len(scenarios) > 20:
        return tool_error(
            tool, "At most 20 scenarios may run per call",
            error_code="too_many_scenarios",
        )
    try:
        timeout = max(1, min(int(timeout_seconds), 600))
        configs = [
            _scenario_config(item, require_complete_twelve=True)
            for item in scenarios
        ]
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )

    except _ScenarioInputError as error:
        return tool_error(
            tool,
            str(error),
            error_code=error.error_code,
            **error.details,
        )
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_scenario")
    results = [_run(config, engine_mode, timeout) for config in configs]
    labels = [str(item.get("label") or f"scenario-{index}") for index, item in enumerate(scenarios, 1)]
    rows = [_comparison_row(label, result) for label, result in zip(labels, results)]
    if field_origins is not None:
        for row, origin in zip(rows, field_origins):
            row["field_origin"] = dict(origin)
    elif field_origin is not None:
        for row in rows:
            row["field_origin"] = dict(field_origin)
    successes = [row for row in rows if row["success"]]
    failures = [row for row in rows if not row["success"]]
    if not successes:
        failure_reasons = list(dict.fromkeys(
            str(row.get("error") or "").strip()[:500]
            for row in failures if str(row.get("error") or "").strip()
        ))
        failure_summary = "No scenario could be evaluated."
        if failure_reasons:
            shown = failure_reasons[:3]
            failure_summary += " Failure reasons: " + " | ".join(shown)
            if len(failure_reasons) > len(shown):
                failure_summary += (
                    f" | {len(failure_reasons) - len(shown)} additional "
                    "distinct reason(s) omitted."
                )
        classified = {
            str(row.get("error_type") or "") for row in failures
        }
        all_priced_unmodellable = classified == {
            "priced_solvent_unmodellable"
        }
        all_flowsheet_mismatch = classified == {
            "cache_flowsheet_mismatch"
        }
        primary = failures[0] if len(failures) == 1 else {}
        return tool_error(
            tool,
            failure_summary,
            error_code=(
                "priced_solvent_unmodellable"
                if all_priced_unmodellable
                else "cache_flowsheet_mismatch"
                if all_flowsheet_mismatch
                else "no_simulation_result"
            ),
            engine_mode=engine_mode, cache_match_status="miss",
            failures=failures, live_engine=live_engine_status(),
            provenance=_provenance([str(result.get("engine_mode")) for result in results]),
            **{
                key: copy.deepcopy(primary[key])
                for key in (
                    "requested_solvent", "canonical_solvent",
                    "engine_solvent", "solvent_support_status",
                    "solvent_model_gap",
                )
                if primary.get(key) is not None
            },
        )
    by_msp = sorted(successes, key=lambda row: float(row["msp_usd_per_kg"]))
    by_gwp = sorted(successes, key=lambda row: float(row["gwp_kg_co2e_per_kg"]))
    modes = [str(row["engine_mode"]) for row in successes]
    comparison_status = _propagated_gwp_metric_status(
        successes, ("lowest_gwp_scenario",),
    )
    status_definitions = {
        **_collect_lca_status_definitions(results),
        **_lca_status_definitions(comparison_status),
    }
    process_details = [
        detail for label, result in zip(labels, results)
        if (detail := _process_details(label, result)) is not None
    ]
    return tool_success(
        tool, analysis_type="tea_lca_scenario_comparison",
        engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        cache_match_status="exact" if set(modes) == {"cache"} else "mixed_or_live",
        scenarios_requested=len(scenarios), completed=len(successes), failed=len(failures),
        comparison_rows=rows, lowest_msp_scenario=by_msp[0]["label"],
        lowest_gwp_scenario=by_gwp[0]["label"], energy_cases=_ENERGY_CASES,
        **(
            {
                "lca_metric_status": comparison_status,
                "lca_method_status_by_energy_case": (
                    _lca_method_status_by_energy_case(results)
                ),
                "lca_status_definitions": status_definitions,
                "gwp_ranking_status": {
                "status": "published_cache_comparison_only",
                "current_scientific_ranking": False,
                "lowest_cached_record_scenario": by_gwp[0]["label"],
                "reason": _CACHE_PROPAGATED_GWP_STATUS_CODE,
                },
            }
            if comparison_status else {}
        ),
        metric_units=_comparison_metric_units(rows),
        process_details=process_details,
        process_data_gaps=(
            ["Cached corpus does not contain unit-level equipment sizes or full stream mass balances."]
            if set(modes) == {"cache"} else []
        ) + _live_toxicity_data_gaps(results)
        + _cache_lca_status_gaps(results),
        provenance=_provenance(modes),
        warnings=[
            "Cached results are exact prior subprocess simulations, never interpolated process economics.",
            "Cached energy intensities correct the legacy worker's annual-total/hourly-flow normalization error using 8,409.6 operating hours per year.",
            "MSP and GWP are per kilogram of modeled recovered product; TCI and AOC are scenario-level estimates.",
            "Process simulation does not establish polymer recovery, purity, kinetics, or integrated plant performance.",
        ],
    )


def _composition(
    values: dict[str, float], *, resolve_polymers: bool = True,
) -> dict[str, float]:
    if not isinstance(values, dict) or not values:
        raise ValueError("feed_mass_fractions must map each feed polymer to its mass fraction or percent")
    resolved: dict[str, float] = {}
    for polymer, value in values.items():
        name = (
            _resolve_polymer(polymer, "feed_mass_fractions") if resolve_polymers
            else str(polymer or "").strip()
        )
        if not name:
            raise ValueError("every feed polymer must have a name")
        resolved[name] = resolved.get(name, 0.0) + _finite(value, f"feed fraction for {polymer}")
    total = sum(resolved.values())
    if total <= 0:
        raise ValueError("feed composition must have positive mass")
    if total > 1.000001:
        if abs(total - 100.0) > 0.01:
            raise ValueError("feed percentages must sum to 100")
        resolved = {key: value / 100.0 for key, value in resolved.items()}
    elif abs(total - 1.0) > 0.0001:
        raise ValueError("feed mass fractions must sum to 1")
    if any(value <= 0 for value in resolved.values()):
        raise ValueError("every feed mass fraction must be positive")
    return resolved


def _stored_candidate_screen(state: Any) -> Optional[dict[str, Any]]:
    """Recover a typed screening basis without promoting it to a process route."""
    compact = getattr(state, "last_result", None) if state else None
    if compact:
        try:
            observation = json.loads(compact)
        except (TypeError, json.JSONDecodeError):
            observation = {}
        if observation.get("contract") == "tool.observation.v1":
            facts = observation.get("relevant_facts") or {}
            source_tool = str(observation.get("tool") or facts.get("tool_name") or "")
            if source_tool == "screen_precipitation_order":
                rows = [
                    dict(row) for row in facts.get("candidate_solvents") or []
                    if isinstance(row, dict) and row.get("solvent")
                ]
                if not rows and isinstance(facts.get("recommended_condition"), dict):
                    rows = [dict(facts["recommended_condition"])]
                if rows:
                    return {
                        "source_tool": source_tool,
                        "target_product": facts.get("first_polymer"),
                        "other_polymers": [
                            value for value in (facts.get("second_polymer"),)
                            if value
                        ],
                        "temperature_min_c": facts.get("temperature_min_c"),
                        "temperature_max_c": facts.get("temperature_max_c"),
                        "strict_maximum": facts.get("strict_maximum"),
                        "precipitation_threshold_wt_pct": facts.get("precipitation_threshold_wt_pct"),
                        "min_dissolution_solubility_wt_pct": facts.get("min_dissolution_solubility_wt_pct"),
                        "candidate_conditions": rows,
                    }
    # Candidate shape is a harness routing concern. This engine consumes any
    # bound rows only when their actual fields establish the screening basis it
    # needs; otherwise the ordinary basis-gap path declines them.
    rows, candidate_source = candidate_evidence(state)
    rows = [row for row in rows if row.get("solvent")]
    constraints = dict(getattr(state, "last_screen_constraints", None) or {})
    target = next((
        row.get("dissolved_polymer") or row.get("target_polymer")
        or row.get("first_polymer")
        for row in rows
        if row.get("dissolved_polymer") or row.get("target_polymer")
        or row.get("first_polymer")
    ), None) or constraints.get("target_polymer")
    others = list(dict.fromkeys(
        str(polymer)
        for row in rows for polymer in row.get("retained_polymers") or []
        if polymer
    )) or list(dict.fromkeys(
        str(row["second_polymer"])
        for row in rows if row.get("second_polymer")
    )) or list(constraints.get("other_polymers") or [])
    if rows and target:
        return {
            "source_tool": (candidate_source or {}).get("source_tool")
            or "screen_polymer_separation",
            "target_product": target,
            "other_polymers": others,
            "temperature_min_c": getattr(state, "temperature_min_c", None),
            "temperature_max_c": getattr(state, "temperature_max_c", None),
            "strict_maximum": getattr(state, "strict_maximum", None),
            "candidate_conditions": rows,
        }
    prior = dict(getattr(state, "last_tea", None) or {})
    rows = [
        dict(row) for row in prior.get("candidate_conditions") or []
        if isinstance(row, dict) and row.get("solvent")
    ]
    if prior.get("route_source") != "typed_session_candidate_screen" or not rows:
        return None
    return {
        "source_tool": "screen_precipitation_order",
        "target_product": prior.get("target_product"),
        "other_polymers": list(prior.get("other_polymers") or []),
        "temperature_min_c": getattr(state, "temperature_min_c", None),
        "temperature_max_c": getattr(state, "temperature_max_c", None),
        "strict_maximum": getattr(state, "strict_maximum", None),
        "candidate_conditions": rows,
    }


def _candidate_tea_basis_gap(
    state: Any,
    basis: dict[str, Any],
    *,
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    energy_case: Optional[str],
    product_quality_intent: Optional[str],
    requested_metrics: Sequence[str],
) -> str:
    """Describe why a scientific screen is not yet a costable process basis."""
    tool = "evaluate_stored_route_tea_lca"
    prior = dict(getattr(state, "last_tea", None) or {})
    if processing_capacity_mt_per_yr is None and product_capacity_mt_per_yr is None:
        prior_capacity = prior.get("requested_capacity_mt_per_yr")
        if prior.get("requested_capacity_basis") == "recovered_product":
            product_capacity_mt_per_yr = prior_capacity
        elif prior.get("requested_capacity_basis") == "total_feed":
            processing_capacity_mt_per_yr = prior_capacity
    if product_quality_intent is None:
        product_quality_intent = prior.get("product_quality_intent")
    if energy_case is None:
        energy_case = prior.get("energy_case")
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    try:
        capacity = _finite(
            product_capacity_mt_per_yr
            if product_capacity_mt_per_yr is not None
            else processing_capacity_mt_per_yr,
            "requested_capacity_mt_per_yr",
        ) if (
            product_capacity_mt_per_yr is not None
            or processing_capacity_mt_per_yr is not None
        ) else None
        if capacity is not None and capacity <= 0:
            raise ValueError("requested capacity must be positive")
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")

    quality_intent = str(product_quality_intent or "").strip() or None
    if quality_intent:
        quality_words = " ".join(filter(None, re.split(r"[_\s-]+", quality_intent)))
        quality_intent = (
            "food-grade intent"
            if quality_words.casefold() in {"food grade", "food grade intent"}
            else quality_words
        )
    if quality_intent and len(quality_intent) > 120:
        return tool_error(
            tool, "product_quality_intent must be at most 120 characters",
            error_code="invalid_route_basis",
        )
    target = str(basis.get("target_product") or "").strip() or None
    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    target_records = [
        row for row in _records()
        if target and _key(row["config"].get("target_plastic")) == _key(target)
    ]
    candidate_rows = []
    missing_prices = []
    for row in basis.get("candidate_conditions") or []:
        solvent = str(row.get("solvent") or "")
        assumption = _solvent_assumption(solvent)
        pair_record = _record_for_pair(target, solvent) if target else None
        if assumption is None:
            missing_prices.append(solvent)
        candidate_rows.append({
            key: value for key, value in {
                "solvent": solvent,
                "dissolution_temperature_c": (
                    row.get("dissolution_temperature_c")
                    if row.get("dissolution_temperature_c") is not None
                    else row.get("temperature_c")
                ),
                "dissolution_solubilities_wt_pct": row.get("dissolution_solubilities_wt_pct"),
                "target_solubility_wt_pct": row.get("target_solubility_pct"),
                "max_off_target_solubility_wt_pct": row.get("max_off_target_solubility_pct"),
                "selectivity_percentage_points": row.get("selectivity_pct"),
                "first_polymer": row.get("first_polymer"),
                "first_precipitation_proxy_c": row.get("first_precipitation_proxy_c"),
                "second_polymer": row.get("second_polymer"),
                "second_precipitation_proxy_c": row.get("second_precipitation_proxy_c"),
                "crossing_temperatures_c": (
                    row.get("crossing_temperatures_c")
                    or row.get("cooling_window_c")
                ),
                "recovery_window_c": row.get("recovery_window_c"),
                "solvent_price_usd_per_kg": (
                    assumption.get("price_usd_per_kg") if assumption else None
                ),
                "exact_target_solvent_cache_record_available": bool(pair_record),
            }.items() if value is not None
        })

    if set(requested_metrics) == {"gwp"}:
        lca_candidates = [{
            key: row[key] for key in (
                "solvent", "dissolution_temperature_c",
                "exact_target_solvent_cache_record_available",
            ) if key in row
        } for row in candidate_rows]
        return tool_error(
            tool,
            "No defensible process-GWP ranking can be calculated from the stored candidate screen.",
            error_code="insufficient_candidate_lca_basis",
            analysis_type="candidate_lca_basis_gap",
            requested_metrics=list(requested_metrics),
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_rank_gwp=False,
            lowest_gwp_solvent=None,
            route_source="typed_session_candidate_screen",
            candidate_source_tool=basis.get("source_tool"),
            target_product=target,
            other_polymers=list(basis.get("other_polymers") or []),
            temperature_min_c=basis.get("temperature_min_c"),
            temperature_max_c=basis.get("temperature_max_c"),
            candidate_count=len(candidate_rows),
            candidate_conditions=lca_candidates,
            target_cache_record_count=len(target_records),
            cached_target_polymers=cached_targets,
            missing_basis_codes=[
                "comparative_functional_unit", "system_boundary",
                "solvent_production_lci", "solvent_recovery_disposal_lci",
                "solvent_loading", "solvent_makeup_recycle_loss",
                "process_energy_case", "target_process_evidence",
                "feed_recovery_product_basis",
            ],
            missing_process_inputs=[
                "Define one comparative functional unit and life-cycle system boundary.",
                "Supply candidate-specific solvent-production and recovery/disposal life-cycle inventories.",
                "Define solvent loading, makeup, recycle efficiency, and process losses for every candidate.",
                "Define the process energy case and exact feed, recovery, and product basis.",
                f"Generate exact {target or 'target-polymer'} process evidence for each candidate; do not substitute records for other polymer/solvent pairs.",
            ],
            process_data_gaps=[
                "The current evidence establishes thermodynamic and safety screening, not comparative process life-cycle impacts.",
                "The admitted process cache has no exact target/candidate records for this comparison.",
            ],
            warnings=[
                "No GWP value or lowest-GWP solvent was calculated.",
                "G-score, Hansen RED, flash/GHS/exposure evidence, and records for other polymer/solvent pairs cannot substitute for process LCA.",
            ],
        )

    contaminant = dict(getattr(state, "last_contaminant", None) or {})
    unsupported = list(contaminant.get("unsupported_contaminants") or [])
    material_basis = "/".join(filter(None, (
        target, *[str(item) for item in basis.get("other_polymers") or []]
    ))) or "target/off-target"
    missing = []
    missing_codes = []
    if len(candidate_rows) > 1:
        missing.append("Select one screened candidate solvent as the process basis.")
        missing_codes.append("candidate_selection")
    missing.append(
        "Define an exact collection/precipitation setpoint, solvent loading, residence time, and solvent recycle/loss; the screen supplies only loading-dependent proxy crossings."
    )
    missing_codes.extend((
        "exact_collection_setpoint", "solvent_loading", "residence_time",
        "solvent_recycle_loss",
    ))
    if not getattr(state, "feed_mass_fractions", None):
        missing.append(
            f"Supply the {material_basis} feed composition and total-feed throughput."
        )
        missing_codes.extend(("feed_composition", "total_feed_throughput"))
    if product_capacity_mt_per_yr is not None:
        missing.append(
            f"Supply a validated {target or 'target-product'} recovery yield to convert recovered-product capacity to total-feed throughput."
        )
        missing_codes.append("product_recovery_yield")
    if not energy_case:
        missing.append("Select an energy case for the process evaluation.")
        missing_codes.append("energy_case")
    if not target_records:
        missing.append(
            f"Generate target-specific process-simulation evidence; the admitted TEA cache has no {target or 'requested-polymer'} record."
        )
        missing_codes.append("target_process_evidence")
    if missing_prices:
        missing.append(
            "Supply an admitted solvent-price basis for: "
            + ", ".join(dict.fromkeys(missing_prices)) + "."
        )
        missing_codes.append("solvent_price")
    if unsupported:
        missing.append(
            "Define and cost contaminant-removal or wash unit operations for: "
            + ", ".join(unsupported) + "."
        )
        missing_codes.append("contaminant_removal_cost")
    if quality_intent:
        missing.append(
            f"Validate product purity and decontamination against the requested {quality_intent} specification."
        )
        missing_codes.append("product_quality_validation")
    return tool_error(
        tool,
        "No defensible MSP can be calculated from the stored candidate screen.",
        error_code="uncostable_candidate_screen",
        analysis_type="tea_route_basis_gap",
        can_estimate_msp=False,
        route_source="typed_session_candidate_screen",
        candidate_source_tool=basis.get("source_tool"),
        target_product=target,
        other_polymers=list(basis.get("other_polymers") or []),
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        energy_case=str(energy_case).upper() if energy_case else None,
        product_quality_intent=quality_intent,
        product_quality_validation="not_modeled",
        temperature_min_c=basis.get("temperature_min_c"),
        temperature_max_c=basis.get("temperature_max_c"),
        strict_maximum=basis.get("strict_maximum"),
        precipitation_threshold_wt_pct=basis.get("precipitation_threshold_wt_pct"),
        min_dissolution_solubility_wt_pct=basis.get("min_dissolution_solubility_wt_pct"),
        candidate_count=len(candidate_rows),
        candidate_conditions=candidate_rows,
        target_cache_record_count=len(target_records),
        cached_target_polymers=cached_targets,
        unsupported_contaminants=unsupported,
        supported_families=list(contaminant.get("supported_families") or []),
        missing_basis_codes=missing_codes,
        missing_process_inputs=missing,
        process_data_gaps=[
            "The current scientific result is a precipitation-order screen, not a complete process route.",
            "The admitted process cache contains no target-specific simulation evidence for this product.",
            "Recovery, product purity, contaminant removal, and integrated-facility performance are not established.",
        ],
        warnings=[
            "No MSP, TCI, AOC, or GWP value was calculated.",
            "Do not substitute the direct-scenario defaults or a different target polymer as an economic analog.",
            *(
                ["The requested product-quality intent is an objective, not validated product performance."]
                if quality_intent else []
            ),
        ],
    )


def _live_process_model_targets() -> tuple[str, ...]:
    return tea_polymer_parameters.live_grid_targets()


def _classify_live_feed_polymers(
    names: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split feed labels into unrecognised vs recognised-but-unmodelled.

    Recognition is thermo.resolve_polymer_identity. Unmodelled is absence
    from the live parameter surface after that lookup, not membership in a
    bad-name list. Generic PE expands to modelled LDPE/HDPE and is neither.
    """
    modelled = set(tea_polymer_parameters.live_identity_map())
    unrecognised: list[str] = []
    unmodelled: list[str] = []
    seen_unrecognised: set[str] = set()
    seen_unmodelled: set[str] = set()
    for name in names:
        token = str(name or "").strip()
        if not token:
            continue
        identity = thermo.resolve_polymer_identity(token)
        if identity is None:
            if token not in seen_unrecognised:
                seen_unrecognised.add(token)
                unrecognised.append(token)
            continue
        key = str(identity).upper()
        if key in modelled:
            continue
        if any(
            str(member).upper() in modelled
            for member in thermo.expand_polymer_identity(token)
        ):
            continue
        if key not in seen_unmodelled:
            seen_unmodelled.add(key)
            unmodelled.append(str(identity))
    return tuple(unrecognised), tuple(unmodelled)


def _unsupported_live_feed_polymers(names: Sequence[str]) -> tuple[str, ...]:
    """Return recognised feed identities that have no live TEA process model."""
    _unrecognised, unmodelled = _classify_live_feed_polymers(names)
    return unmodelled


def _join_polymer_names(names: Sequence[str]) -> str:
    items = [str(name) for name in names]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"


def _unsupported_live_target_feed_error(
    *,
    unrecognised: Sequence[str] = (),
    unsupported: Sequence[str] = (),
    feed_names: Sequence[str],
    feed: dict[str, float],
    requested_metrics: Sequence[str],
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    capacity: Optional[float],
    energy_case: Optional[str],
) -> str:
    """Refuse a feed whose labels are unrecognised or have no live process model.

    Unrecognised names must not be worded as missing a process model.
    Recognised-but-unmodelled names keep the modelled-set refusal.
    """
    unrecognised = tuple(unrecognised)
    unsupported = tuple(unsupported)
    sentences: list[str] = []
    process_data_gaps: list[str] = []
    missing_codes: list[str] = []
    if unrecognised:
        named = _join_polymer_names(unrecognised)
        copula = "is" if len(unrecognised) == 1 else "are"
        noun = "identity" if len(unrecognised) == 1 else "identities"
        sentences.append(f"{named} {copula} not a recognised polymer {noun}.")
        process_data_gaps.append(
            f"No TEA/LCA process-model claim applies to {named}."
        )
        missing_codes.append("recognised_polymer_identity")
    if unsupported:
        named = _join_polymer_names(unsupported)
        modelled = _join_polymer_names(_live_process_model_targets())
        verb = "has" if len(unsupported) == 1 else "have"
        sentences.append(
            f"{named} {verb} no live TEA process model, so completing a "
            "separation route and plant basis cannot produce TEA/LCA for this "
            f"feed. Live process models exist only for {modelled}; LDPE and "
            "HDPE share the PE process."
        )
        process_data_gaps.append(
            f"No additional process inputs can create a TEA/LCA model for {named}."
        )
        missing_codes.append("live_process_model")
    warnings = [
        "No MSP, TCI, AOC, or GWP value was calculated.",
    ]
    if unrecognised:
        warnings.append(
            "Do not treat an unrecognised name as a polymer this system cannot cost."
        )
    if unsupported:
        warnings.append(
            "Do not collect a route or plant basis as if that would enable this TEA."
        )
    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    selected_energy_case = str(energy_case).upper() if energy_case else None
    return tool_error(
        "evaluate_stored_route_tea_lca",
        " ".join(sentences),
        error_code="uncostable_initial_feed_basis",
        analysis_type=(
            "tea_unrecognised_feed_polymer" if unrecognised
            else "tea_unsupported_live_target"
        ),
        can_estimate_msp=False,
        can_estimate_gwp=False,
        estimate_quality="not_calculated",
        exact_cache_basis_available=False,
        route_source="requested_feed_without_route",
        requested_feed_polymers=list(feed_names),
        requested_feed_mass_fractions=dict(feed),
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        requested_metrics=list(requested_metrics),
        energy_case=selected_energy_case,
        cached_target_polymers=cached_targets,
        unrecognised_feed_polymers=list(unrecognised),
        unsupported_live_targets=list(unsupported),
        supported_live_targets=list(_live_process_model_targets()),
        missing_basis_codes=missing_codes,
        missing_process_inputs=[],
        process_data_gaps=process_data_gaps,
        warnings=warnings,
    )


def _feed_tea_basis_gap(
    feed_mass_fractions: Optional[dict[str, float]],
    feed_polymers: Optional[list[str]],
    *,
    processing_capacity_mt_per_yr: Optional[float],
    product_capacity_mt_per_yr: Optional[float],
    comparison_capacities_mt_per_yr: Optional[list[float]],
    energy_case: Optional[str],
    requested_metrics: Sequence[str],
) -> str:
    """Preserve an initial feed request without silently choosing its route."""
    tool = "evaluate_stored_route_tea_lca"
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    try:
        if feed_mass_fractions:
            feed = _composition(feed_mass_fractions, resolve_polymers=False)
            feed_names = list(feed)
        elif isinstance(feed_polymers, list) and feed_polymers:
            feed = {}
            feed_names = []
            for item in feed_polymers:
                supplied = str(item).strip()
                if not supplied:
                    continue
                members = thermo.expand_polymer_identity(supplied)
                identities = members or (
                    thermo.resolve_polymer_identity(supplied) or supplied,
                )
                for identity in identities:
                    if identity not in feed_names:
                        feed_names.append(identity)
            if not feed_names:
                raise ValueError("feed_polymers must contain at least one material label")
        else:
            raise ValueError(
                "Supply feed_polymers or feed_mass_fractions for a new feed-level request"
            )
        capacity_value = product_capacity_mt_per_yr if (
            product_capacity_mt_per_yr is not None
        ) else processing_capacity_mt_per_yr
        capacity = _finite(capacity_value, "requested_capacity_mt_per_yr") if (
            capacity_value is not None
        ) else None
        if capacity is not None and capacity <= 0:
            raise ValueError("requested capacity must be positive")
        scale_capacities = None
        if comparison_capacities_mt_per_yr is not None:
            if not feed:
                raise ValueError(
                    "scale comparison requires feed mass fractions, not material labels alone"
                )
            if not isinstance(comparison_capacities_mt_per_yr, list) or capacity is None:
                raise ValueError(
                    "scale comparison requires a baseline total-feed capacity and a list of comparison capacities"
                )
            scale_capacities = list(dict.fromkeys([
                capacity,
                *(
                    _finite(value, "comparison_capacities_mt_per_yr")
                    for value in comparison_capacities_mt_per_yr
                ),
            ]))
            if (
                len(scale_capacities) < 2 or len(scale_capacities) > 6
                or any(value <= 0 for value in scale_capacities)
            ):
                raise ValueError(
                    "scale comparison requires two to six distinct positive capacities"
                )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")

    unrecognised, unmodelled = _classify_live_feed_polymers(feed_names)
    if unrecognised or unmodelled:
        return _unsupported_live_target_feed_error(
            unrecognised=unrecognised,
            unsupported=unmodelled,
            feed_names=feed_names,
            feed=feed,
            requested_metrics=requested_metrics,
            processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
            product_capacity_mt_per_yr=product_capacity_mt_per_yr,
            capacity=capacity,
            energy_case=energy_case,
        )

    unresolved = [name for name in feed_names if name.casefold() == "pe"]
    interpretations = {name: ["LDPE", "HDPE"] for name in unresolved}
    missing_codes = ["complete_separation_route", "solvent_selection",
                     "dissolution_collection_setpoints", "recovery_product_basis"]
    missing = [
        "Define a complete separation route and identify which feed polymer is dissolved at each stage.",
        "Select the solvent and exact dissolution and collection setpoints for each stage.",
        "Define recovery yields and the product basis needed to convert feed throughput into product economics.",
    ]
    if not feed:
        missing_codes.insert(0, "feed_composition")
        missing.insert(
            0,
            "Supply the mass fraction of every named feed polymer; no composition was inferred.",
        )
    if unresolved:
        missing_codes.insert(0, "polymer_grade")
        missing.insert(
            0,
            "Clarify whether PE means LDPE or HDPE; those grades do not share one dissolution route.",
        )
    if capacity is None:
        missing_codes.append("plant_capacity")
        missing.append("Supply the plant capacity and state whether it is total feed or recovered product.")
    if not energy_case:
        missing_codes.append("energy_case")
        missing.append("Select the process energy case.")

    cached_targets = sorted({
        str(row["config"].get("target_plastic"))
        for row in _records() if row.get("config", {}).get("target_plastic")
    })
    cache_lca_fields = {
        str(key).casefold() for row in _records()
        for key in (row.get("result", {}).get("lca") or {})
    }
    cache_energy_case_coverage = {
        case: sorted({
            str(row["config"].get("target_plastic"))
            for row in _records()
            if str(row.get("config", {}).get("energy_case") or "").upper() == case
            and row.get("config", {}).get("target_plastic")
        })
        for case in _ENERGY_CASES
    }
    scope_available = all(any(
        re.search(rf"scope[_ -]?{number}\b", key) for key in cache_lca_fields
    ) for number in (1, 2))
    selected_energy_case = str(energy_case).upper() if energy_case else None
    if scale_capacities:
        candidate_targets = sorted(
            (set(feed_names) - set(unresolved))
            | {item for values in interpretations.values() for item in values}
        )
        capacity_ranges = {
            polymer: sorted({
                float(row["config"]["processing_capacity"])
                for row in _records()
                if _key(row["config"].get("target_plastic")) == _key(polymer)
                and (
                    not selected_energy_case
                    or str(row["config"].get("energy_case") or "").upper()
                    == selected_energy_case
                )
            })
            for polymer in candidate_targets
        }
        stage_capacities = {
            f"{total:g}": {
                polymer: total * fraction for polymer, fraction in feed.items()
            }
            for total in scale_capacities
        }
        coverage = {}
        for total, stages in stage_capacities.items():
            coverage[total] = {}
            for polymer in candidate_targets:
                ranges = capacity_ranges[polymer]
                stage = stages.get(polymer, stages.get("PE"))
                coverage[total][polymer] = (
                    "no_record" if not ranges else
                    "below" if stage < ranges[0] else
                    "above" if stage > ranges[-1] else "within"
                )
        return tool_error(
            tool,
            "The requested scales can be checked against stage-cache coverage, but no integrated economies-of-scale or emissions-intensity result can be calculated without a complete route.",
            error_code="uncostable_feed_scale_basis",
            analysis_type="tea_feed_scale_basis_gap",
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_compare_scale=False,
            economies_of_scale_calculated=False,
            emissions_intensity_change_calculated=False,
            estimate_quality="not_calculated",
            exact_cache_basis_available=False,
            route_source="requested_feed_without_route",
            requested_feed_polymers=feed_names,
            requested_feed_mass_fractions=feed,
            unresolved_polymer_identities=unresolved,
            supported_interpretations=interpretations,
            requested_capacity_mt_per_yr=capacity,
            requested_capacity_basis="total_feed",
            requested_scale_capacities_mt_per_yr=scale_capacities,
            conditional_stage_capacities_mt_per_yr=stage_capacities,
            cache_capacity_ranges_mt_per_yr=capacity_ranges,
            conditional_cache_coverage=coverage,
            requested_metrics=list(requested_metrics),
            energy_case=selected_energy_case,
            energy_case_description=_ENERGY_CASES.get(selected_energy_case),
            available_cache_energy_cases=list(_ENERGY_CASES),
            energy_case_descriptions=dict(_ENERGY_CASES),
            can_compare_energy_cases=False,
            missing_basis_codes=[*missing_codes, "integrated_scale_process_evidence"],
            missing_process_inputs=missing,
            scale_comparison_basis="conditional stage-cache coverage only",
            process_data_gaps=[
                "The stage capacities are feed-fraction arithmetic, not recovered-product throughput or an integrated process mass balance.",
                "Being inside a cached stage-capacity range does not establish an exact record for the requested grade, route, solvent, setpoints, recovery, or product basis.",
                "No MSP, GWP intensity, or total annual emissions value was calculated at either scale.",
            ],
            warnings=[
                "Do not infer economies of scale or an emissions-intensity trend from cache-range coverage.",
                *(
                    ["Do not silently map generic PE to LDPE or HDPE or combine unmatched stage records."]
                    if unresolved else
                    ["The requested polymer identities are explicit; do not introduce a polymer-grade ambiguity."]
                ),
            ],
        )
    return tool_error(
        tool,
        "No defensible MSP, capital cost, or operating cost can be calculated from this feed request alone.",
        error_code="uncostable_initial_feed_basis",
        analysis_type="tea_feed_basis_gap",
        can_estimate_msp=False,
        estimate_quality="not_calculated",
        exact_cache_basis_available=False,
        route_source="requested_feed_without_route",
        requested_feed_polymers=feed_names,
        requested_feed_mass_fractions=feed,
        unresolved_polymer_identities=unresolved,
        supported_interpretations=interpretations,
        requested_capacity_mt_per_yr=capacity,
        requested_capacity_basis=(
            "recovered_product" if product_capacity_mt_per_yr is not None
            else "total_feed" if processing_capacity_mt_per_yr is not None
            else "unspecified"
        ),
        requested_metrics=list(requested_metrics),
        energy_case=selected_energy_case,
        cached_target_polymers=cached_targets,
        available_cache_energy_cases=list(_ENERGY_CASES),
        energy_case_descriptions=dict(_ENERGY_CASES),
        cache_energy_case_coverage=cache_energy_case_coverage,
        can_compare_energy_cases=False,
        cache_total_gwp_available="gwp_kg_co2e_per_kg" in cache_lca_fields,
        scope_1_2_attribution_available=scope_available,
        scope_attribution_data_gap=(
            None if scope_available else
            "The admitted cache has no sourced fields separating direct/on-site fuel emissions from purchased electricity."
        ),
        missing_basis_codes=missing_codes,
        missing_process_inputs=missing,
        process_data_gaps=[
            "The admitted cache contains stage records for selected polymers, solvents, capacities, and energy cases; it contains no exact integrated record for this requested feed and capacity.",
            "Any estimate based on a different polymer grade, route, composition, or scale would be a screening analog rather than an exact process result.",
        ],
        warnings=[
            "No MSP, TCI, AOC, or GWP value was calculated.",
            *(
                ["Do not silently map generic PE to LDPE or HDPE or combine unmatched stage records."]
                if unresolved else
                ["The requested polymer identities are explicit; do not introduce a polymer-grade ambiguity."]
            ),
        ],
    )


def evaluate_stored_route_tea_lca(
    feed_mass_fractions: Optional[dict[str, float]] = None,
    processing_capacity_mt_per_yr: Optional[float] = None,
    product_capacity_mt_per_yr: Optional[float] = None,
    comparison_capacities_mt_per_yr: Optional[list[float]] = None,
    energy_case: Optional[str] = None,
    precipitation_temperature_c: Optional[float] = None,
    product_quality_intent: Optional[str] = None,
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
    allow_screening_estimate: bool = False,
    requested_metrics: Optional[list[str]] = None,
    requested_product_count: Optional[int] = None,
    product_selection_basis: Optional[str] = None,
    feed_polymers: Optional[list[str]] = None,
    compare_route_variants: bool = False,
) -> str:
    """Evaluate every dissolution stage from the exact route in typed session state.

    The route or current candidate screen is deliberately not a model argument:
    this tool consumes typed session state, preventing a follow-up from silently
    re-deriving solvents and setpoints. It returns a bounded basis gap when a
    candidate comparison lacks process evidence. An initial feed without a
    route returns an unresolved process-basis gap. Supply one or more
    `comparison_capacities_mt_per_yr` to compare the same route across scales;
    the first processing capacity remains the baseline and the last comparison
    becomes the durable basis for downstream optimization. A request to choose
    products by value returns a typed portfolio-basis gap until the product
    identities and value basis are supplied; the model never makes that choice.
    """
    tool = "evaluate_stored_route_tea_lca"
    metrics = list(dict.fromkeys(
        _REQUESTED_METRIC_ALIASES.get(token, token)
        for metric in requested_metrics or []
        if (token := str(metric).strip().casefold())
    ))
    allowed_metrics = {"msp", "tci", "aoc", "gwp", "energy"}
    if not metrics or any(metric not in allowed_metrics for metric in metrics):
        if requested_metrics is not None:
            return tool_error(
                tool,
                "requested_metrics must contain one or more of: msp, tci, aoc, gwp, energy.",
                error_code="invalid_requested_metrics",
            )
    state = current_tool_session()
    route = copy.deepcopy(getattr(state, "last_route", None)) if state else None
    candidate_basis = _stored_candidate_screen(state) if state and not route else None
    candidate_feed_matches = False
    if candidate_basis:
        candidate_labels = [
            str(candidate_basis.get("target_product") or ""),
            *(str(item) for item in candidate_basis.get("other_polymers") or []),
        ]
        try:
            candidate_identities = set(_expand_polymers(
                [item for item in candidate_labels if item], "candidate feed polymer",
            ))
            supplied_identities = set(_expand_polymers(
                feed_polymers or [], "feed polymer",
            ))
            candidate_feed_matches = bool(
                candidate_identities
                and (not supplied_identities or supplied_identities == candidate_identities)
            )
        except ValueError:
            candidate_feed_matches = False
    if compare_route_variants:
        basis = copy.deepcopy(
            getattr(state, "route_substitution_basis", None)
        ) if state else None
        if not basis:
            return tool_error(
                tool,
                "No stored original/substituted route pair is available for comparison.",
                error_code="missing_route_variant_basis",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
            )
        original_route = basis.get("original_route")
        substituted_route = basis.get("recommended_route")
        if not original_route or not substituted_route:
            return tool_error(
                tool, "The stored route-variant basis is incomplete.",
                error_code="invalid_route_variant_basis",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
            )
        original_state_route = state.last_route
        variant_results = []
        try:
            for label, variant_route in (
                ("original", original_route),
                ("substituted", substituted_route),
            ):
                state.last_route = copy.deepcopy(variant_route)
                variant = parse_tool_result(evaluate_stored_route_tea_lca(
                    feed_mass_fractions=feed_mass_fractions,
                    processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                    product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                    comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                    energy_case=energy_case,
                    precipitation_temperature_c=precipitation_temperature_c,
                    product_quality_intent=product_quality_intent,
                    engine_mode=engine_mode,
                    timeout_seconds=timeout_seconds,
                    allow_screening_estimate=allow_screening_estimate,
                    requested_metrics=requested_metrics,
                    requested_product_count=requested_product_count,
                    product_selection_basis=product_selection_basis,
                    feed_polymers=feed_polymers,
                    compare_route_variants=False,
                ))["data"]
                variant_results.append((label, variant_route, variant))
        finally:
            state.last_route = original_state_route

        rows = []
        for label, variant_route, variant in variant_results:
            row = {
                "label": label.title(), "route_variant": label,
                "success": variant.get("success"),
                "route_signature": variant.get("route_signature"),
                "engine_mode": variant.get("engine_mode"),
                "cache_match_status": variant.get("cache_match_status"),
                "processing_capacity_mt_per_yr": variant.get(
                    "processing_capacity_mt_per_yr"
                ),
                "energy_case": variant.get("energy_case"),
                "steps": list(variant_route.get("steps") or []),
                "mass_weighted_recovered_msp_usd_per_kg": variant.get(
                    "mass_weighted_recovered_msp_usd_per_kg"
                ),
                "mass_weighted_recovered_gwp_kg_co2e_per_kg": variant.get(
                    "mass_weighted_recovered_gwp_kg_co2e_per_kg"
                ),
                "lca_metric_status": copy.deepcopy(
                    variant.get("lca_metric_status") or {}
                ),
                "total_stage_tci_usd": variant.get("total_stage_tci_usd"),
                "total_stage_aoc_usd_per_yr": variant.get(
                    "total_stage_aoc_usd_per_yr"
                ),
                "estimate_quality": variant.get("estimate_quality"),
                "estimate_limitations": list(
                    variant.get("estimate_limitations") or []
                ),
                "error_code": variant.get("error_code"),
                "missing_basis_codes": list(
                    variant.get("missing_basis_codes") or []
                ),
            }
            rows.append({key: value for key, value in row.items() if value is not None})
        failed = [row for row in rows if row.get("success") is not True]
        if failed:
            return tool_error(
                tool,
                "The original and substituted routes do not share a complete admitted TEA/LCA basis.",
                error_code="route_variant_tea_basis_gap",
                analysis_type="route_variant_tea_basis_gap",
                can_estimate_msp=False, can_estimate_gwp=False,
                route_source="typed_session_state",
                requested_metrics=metrics,
                comparison_rows=rows,
                lca_status_definitions=_collect_lca_status_definitions(
                    [variant for _label, _route, variant in variant_results]
                ),
                missing_basis_codes=sorted({
                    code for row in failed
                    for code in row.get("missing_basis_codes") or [
                        "route_variant_process_evidence"
                    ]
                }),
                warnings=[
                    "No cross-route MSP or GWP difference was calculated.",
                    "Thermodynamic selectivity and safety scores cannot substitute for a common TEA/LCA basis.",
                    *_cache_lca_status_gaps(
                        [variant for _label, _route, variant in variant_results]
                    ),
                ],
            )
        original, substituted = rows
        msp_delta = (
            float(substituted["mass_weighted_recovered_msp_usd_per_kg"])
            - float(original["mass_weighted_recovered_msp_usd_per_kg"])
            if "msp" in metrics else None
        )
        gwp_delta = (
            float(substituted["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            - float(original["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            if "gwp" in metrics else None
        )
        modes = {str(row.get("engine_mode")) for row in rows}
        evidence_class = (
            "exact_cache" if modes == {"cache"}
            else "screening_estimate" if modes == {"screening_estimate"}
            else "mixed_exact_and_screening"
        )
        comparison_status = _propagated_gwp_metric_status(
            rows,
            (
                "gwp_difference_substituted_minus_original_kg_co2e_per_kg",
            ) if gwp_delta is not None else (),
        )
        status_definitions = {
            **_collect_lca_status_definitions(
                [variant for _label, _route, variant in variant_results]
            ),
            **_lca_status_definitions(comparison_status),
        }
        return tool_success(
            tool,
            analysis_type="route_substitution_tea_lca_comparison",
            route_source="typed_session_state",
            engine_mode=(next(iter(modes)) if len(modes) == 1 else "mixed"),
            processing_capacity_mt_per_yr=original.get(
                "processing_capacity_mt_per_yr"
            ),
            energy_case=original.get("energy_case"),
            requested_metrics=metrics,
            comparison_rows=rows,
            common_route_variant_basis={
                "feed_mass_fractions": dict(
                    feed_mass_fractions
                    or getattr(state, "feed_mass_fractions", {}) or {}
                ),
                "processing_capacity_mt_per_yr": original.get(
                    "processing_capacity_mt_per_yr"
                ),
                "energy_case": original.get("energy_case"),
                "precipitation_temperature_c": (
                    25.0 if precipitation_temperature_c is None
                    else float(precipitation_temperature_c)
                ),
            },
            comparison_evidence_class=evidence_class,
            msp_difference_substituted_minus_original_usd_per_kg=msp_delta,
            gwp_difference_substituted_minus_original_kg_co2e_per_kg=gwp_delta,
            **(
                {
                "lca_metric_status": comparison_status,
                "lca_method_status_by_energy_case": (
                    _lca_method_status_by_energy_case(
                        [
                            variant
                            for _label, _route, variant in variant_results
                        ]
                    )
                ),
                "lca_status_definitions": status_definitions,
                }
                if status_definitions else {}
            ),
            provenance={
                "original_route_signature": original.get("route_signature"),
                "substituted_route_signature": substituted.get("route_signature"),
            },
            warnings=[
                "The original and substituted routes share feed, capacity, energy case, and metric definitions, but their evidence classes remain distinct.",
                "A screening-estimate difference is not an experimentally validated cost or carbon premium.",
                *_cache_lca_status_gaps(
                    [variant for _label, _route, variant in variant_results]
                ),
            ],
        )
    if feed_polymers and route:
        route_labels = [
            str(step.get("dissolved_polymer"))
            for step in route.get("steps") or []
            if step.get("dissolved_polymer")
        ] + ([str(route["final_residue"])] if route.get("final_residue") else [])
        try:
            supplied_identities = set(_expand_polymers(
                feed_polymers, "feed polymer",
            ))
            route_identities = {_resolve_polymer(item) for item in route_labels}
        except ValueError:
            supplied_identities = route_identities = set()
        if not route_identities or supplied_identities != route_identities:
            return _feed_tea_basis_gap(
                feed_mass_fractions,
                feed_polymers,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                energy_case=energy_case,
                requested_metrics=metrics,
            )
    elif (
        feed_polymers or (feed_mass_fractions and not route)
    ) and not candidate_feed_matches:
        return _feed_tea_basis_gap(
            feed_mass_fractions,
            feed_polymers,
            processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
            product_capacity_mt_per_yr=product_capacity_mt_per_yr,
            comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
            energy_case=energy_case,
            requested_metrics=metrics,
        )
    if not route:
        if candidate_basis and candidate_feed_matches:
            return _candidate_tea_basis_gap(
                state,
                candidate_basis,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                energy_case=energy_case,
                product_quality_intent=product_quality_intent,
                requested_metrics=metrics,
            )
        prior_tea = dict(getattr(state, "last_tea", None) or {}) if state else {}
        if (
            comparison_capacities_mt_per_yr is not None
            and not feed_mass_fractions
            and prior_tea.get("analysis_type") in {
                "tea_feed_basis_gap", "tea_feed_scale_basis_gap",
            }
        ):
            feed_mass_fractions = dict(
                prior_tea.get("requested_feed_mass_fractions") or {}
            )
            feed_polymers = list(prior_tea.get("requested_feed_polymers") or [])
            if processing_capacity_mt_per_yr is None:
                processing_capacity_mt_per_yr = prior_tea.get(
                    "requested_capacity_mt_per_yr"
                )
            if energy_case is None:
                energy_case = prior_tea.get("energy_case")
        if feed_mass_fractions or feed_polymers:
            return _feed_tea_basis_gap(
                feed_mass_fractions,
                feed_polymers,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                comparison_capacities_mt_per_yr=comparison_capacities_mt_per_yr,
                energy_case=energy_case,
                requested_metrics=metrics,
            )
        candidate_basis = candidate_basis or _stored_candidate_screen(state)
        if candidate_basis:
            return _candidate_tea_basis_gap(
                state,
                candidate_basis,
                processing_capacity_mt_per_yr=processing_capacity_mt_per_yr,
                product_capacity_mt_per_yr=product_capacity_mt_per_yr,
                energy_case=energy_case,
                product_quality_intent=product_quality_intent,
                requested_metrics=metrics,
            )
        return tool_error(
            tool, "No stored separation route is available in this session.",
            error_code="missing_stored_route",
        )
    if not route.get("complete"):
        return tool_error(
            tool, "The stored separation route is incomplete and cannot define every TEA stage.",
            error_code="incomplete_stored_route", consumed_route=route,
        )
    if processing_capacity_mt_per_yr is not None and product_capacity_mt_per_yr is not None:
        return tool_error(
            tool,
            "Supply either total-feed capacity or recovered-product capacity, not both.",
            error_code="conflicting_capacity_basis",
        )
    selection_basis = str(product_selection_basis or "").strip().casefold()
    if requested_product_count is not None or selection_basis:
        route_products = [
            str(step["dissolved_polymer"]) for step in route.get("steps") or []
            if step.get("dissolved_polymer")
        ] + ([str(route["final_residue"])] if route.get("final_residue") else [])
        if (
            isinstance(requested_product_count, bool)
            or not isinstance(requested_product_count, int)
            or requested_product_count < 1
            or requested_product_count > len(route_products)
            or selection_basis != "highest_value"
        ):
            return tool_error(
                tool,
                "Product selection requires a valid requested_product_count and product_selection_basis='highest_value'.",
                error_code="invalid_product_selection_basis",
            )
        capacity_value = (
            product_capacity_mt_per_yr
            if product_capacity_mt_per_yr is not None
            else processing_capacity_mt_per_yr
        )
        try:
            capacity = (
                _finite(capacity_value, "requested_capacity_mt_per_yr")
                if capacity_value is not None else None
            )
            if capacity is not None and capacity <= 0:
                raise ValueError("requested capacity must be positive")
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_basis")
        missing = [
            "Name the requested product polymers and define the comparable product-value or market-price basis; the thermodynamic route does not rank products by value.",
            "Supply the full feed composition and per-polymer recovery yields needed to convert feed into recovered products.",
            "State whether the requested recovered-product capacity is combined across products or applies to each product, then allocate that capacity.",
            "Define route-stage collection, product specifications, solvent loading/recycle/loss, and the process energy case.",
            "Generate route- and product-specific process evidence on one MSP and cradle-to-gate GWP basis.",
        ]
        return tool_error(
            tool,
            "No defensible product-value ranking, MSP, or GWP estimate can be calculated from the stored thermodynamic route.",
            error_code="uncostable_product_portfolio",
            analysis_type="tea_route_portfolio_basis_gap",
            can_estimate_msp=False,
            can_estimate_gwp=False,
            can_rank_product_value=False,
            route_source="typed_session_state",
            route_products=route_products,
            requested_product_count=requested_product_count,
            product_selection_basis=selection_basis,
            requested_metrics=metrics,
            requested_capacity_mt_per_yr=capacity,
            requested_capacity_basis=(
                "recovered_product" if product_capacity_mt_per_yr is not None
                else "total_feed" if processing_capacity_mt_per_yr is not None
                else "unspecified"
            ),
            feed_composition_available=bool(getattr(state, "feed_mass_fractions", None)),
            target_products=[],
            value_ranking_available=False,
            exact_route_process_basis_available=False,
            missing_basis_codes=[
                "target_product_identity", "product_value_basis",
                "feed_composition", "product_recovery_yields",
                "product_capacity_allocation", "collection_product_basis",
                "solvent_recycle_loss", "energy_case",
                "route_product_process_evidence",
            ],
            missing_process_inputs=missing,
            process_data_gaps=[
                "The stored route is a thermodynamic sequence, not an integrated process or product-value ranking.",
                "No common product-specific MSP or cradle-to-gate GWP basis was evaluated.",
            ],
            warnings=[
                "No polymers were selected as highest value.",
                "No MSP, TCI, AOC, GWP, recovery, or landfill allocation was calculated.",
            ],
        )
    if product_capacity_mt_per_yr is not None:
        try:
            product_capacity = _finite(product_capacity_mt_per_yr, "product_capacity_mt_per_yr")
            if product_capacity <= 0:
                raise ValueError("product_capacity_mt_per_yr must be positive")
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_basis")
        return tool_error(
            tool,
            "Recovered-product capacity cannot be converted to route feed throughput without a validated recovery basis.",
            error_code="product_capacity_requires_recovery_basis",
            analysis_type="tea_route_basis_gap", can_estimate_msp=False,
            route_source="typed_session_state",
            requested_capacity_mt_per_yr=product_capacity,
            requested_capacity_basis="recovered_product",
            product_quality_intent=str(product_quality_intent or "").strip() or None,
            product_quality_validation="not_modeled",
            steps=list(route.get("steps") or []),
            final_residue=route.get("final_residue"),
            missing_process_inputs=[
                "Supply a validated recovery yield or state total-feed throughput instead."
            ],
            warnings=["No MSP, TCI, AOC, or GWP value was calculated."],
        )
    feed_capacity = 20_000.0 if processing_capacity_mt_per_yr is None else processing_capacity_mt_per_yr
    selected_energy_case = str(energy_case or "C1").upper()
    selected_precipitation_c = 25.0 if precipitation_temperature_c is None else precipitation_temperature_c
    if comparison_capacities_mt_per_yr is not None:
        if not isinstance(comparison_capacities_mt_per_yr, list):
            return tool_error(
                tool, "comparison_capacities_mt_per_yr must be a list",
                error_code="invalid_scale_comparison",
            )
        try:
            requested = [
                _finite(feed_capacity, "processing_capacity_mt_per_yr"),
                *[
                    _finite(value, "comparison_capacities_mt_per_yr")
                    for value in comparison_capacities_mt_per_yr
                ],
            ]
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_scale_comparison")
        capacities = list(dict.fromkeys(requested))
        if len(capacities) < 2 or len(capacities) > 6 or any(value <= 0 for value in capacities):
            return tool_error(
                tool,
                "Scale comparison requires two to six distinct positive capacities.",
                error_code="invalid_scale_comparison",
            )
        scale_results = []
        for capacity in capacities:
            result = parse_tool_result(evaluate_stored_route_tea_lca(
                feed_mass_fractions=feed_mass_fractions,
                processing_capacity_mt_per_yr=capacity,
                product_capacity_mt_per_yr=None,
                comparison_capacities_mt_per_yr=None,
                energy_case=selected_energy_case,
                precipitation_temperature_c=selected_precipitation_c,
                product_quality_intent=product_quality_intent,
                engine_mode=engine_mode,
                timeout_seconds=timeout_seconds,
                allow_screening_estimate=allow_screening_estimate,
            ))["data"]
            if result.get("success") is not True:
                return tool_error(
                    tool, f"Stored-route scale evaluation failed at {capacity:g} mt/yr.",
                    error_code="route_scale_failed", failed_capacity_mt_per_yr=capacity,
                    cause={
                        key: result.get(key) for key in (
                            "error", "error_code", "failed_stage", "live_engine",
                        ) if result.get(key) is not None
                    },
                )
            scale_results.append(result)
        baseline = scale_results[0]
        baseline_msp = float(baseline["mass_weighted_recovered_msp_usd_per_kg"])
        baseline_gwp = float(baseline["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
        baseline_annual_gwp = float(baseline["modeled_dissolution_stage_gwp_t_co2e_per_yr"])
        if min(baseline_msp, baseline_gwp, baseline_annual_gwp) <= 0:
            return tool_error(
                tool, "Scale-comparison baseline metrics must be positive.",
                error_code="invalid_scale_baseline",
            )
        scale_rows = []
        for capacity, result in zip(capacities, scale_results):
            msp = float(result["mass_weighted_recovered_msp_usd_per_kg"])
            gwp = float(result["mass_weighted_recovered_gwp_kg_co2e_per_kg"])
            annual_gwp = float(result["modeled_dissolution_stage_gwp_t_co2e_per_yr"])
            row_status = _propagated_gwp_metric_status(
                [result],
                (
                    "gwp_kg_co2e_per_kg", "gwp_t_co2e_per_mt",
                    "gwp_change_t_co2e_per_mt_from_baseline",
                    "gwp_change_percent_from_baseline",
                    "modeled_dissolution_stage_gwp_t_co2e_per_yr",
                    "annual_gwp_factor_from_baseline",
                ),
            )
            scale_rows.append({
                "label": f"{capacity:g}-mt-per-yr",
                "processing_capacity_mt_per_yr": capacity,
                "engine_mode": result.get("engine_mode"),
                "cache_match_status": result.get("cache_match_status"),
                "estimate_quality": result.get("estimate_quality"),
                "provenance_class": (
                    "exact_cached_simulation"
                    if result.get("engine_mode") == "cache"
                    else "cache_derived_screening_estimate"
                    if result.get("engine_mode") == "screening_estimate"
                    else "mixed_exact_and_screening"
                ),
                "capacity_factor_from_baseline": capacity / capacities[0],
                "msp_usd_per_kg": msp,
                "msp_change_usd_per_kg_from_baseline": msp - baseline_msp,
                "msp_change_percent_from_baseline": 100.0 * (msp / baseline_msp - 1.0),
                "gwp_kg_co2e_per_kg": gwp,
                "gwp_t_co2e_per_mt": gwp,
                "gwp_change_t_co2e_per_mt_from_baseline": gwp - baseline_gwp,
                "gwp_change_percent_from_baseline": 100.0 * (gwp / baseline_gwp - 1.0),
                "modeled_dissolution_stage_gwp_t_co2e_per_yr": annual_gwp,
                "annual_gwp_factor_from_baseline": annual_gwp / baseline_annual_gwp,
                **(
                    {"lca_metric_status": row_status}
                    if row_status else {}
                ),
            })
        selected = scale_results[-1]
        analog_solvents = sorted({
            str(basis["analog_solvent"])
            for result in scale_results for row in result.get("stage_results") or []
            if (basis := row.get("estimate_basis") or {}).get("analog_solvent")
        })
        extrapolated = [
            f"{capacity:g}:{row['label']}"
            for capacity, result in zip(capacities, scale_results)
            for row in result.get("stage_results") or []
            if (row.get("estimate_basis") or {}).get("capacity_extrapolated")
        ]
        missing_temperature = sorted({
            str(row["label"])
            for result in scale_results for row in result.get("stage_results") or []
            if row.get("estimate_basis")
            and not row["estimate_basis"].get("temperature_adjustment_available")
        })
        modes = [str(result.get("engine_mode")) for result in scale_results]
        mode_set = set(modes)
        estimate = any(result.get("estimate_quality") == "screening_only" for result in scale_results)
        process_gaps = list(dict.fromkeys(
            str(gap) for result in scale_results for gap in result.get("process_data_gaps") or []
        ))
        scale_status = _propagated_gwp_metric_status(
            scale_results,
            ("scale_gwp_comparison",),
        )
        status_definitions = {
            **_collect_lca_status_definitions(scale_results),
            **_lca_status_definitions(scale_status),
        }
        return tool_success(
            tool, analysis_type="multistage_route_scale_comparison",
            engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
            cache_match_status=(
                "exact" if mode_set == {"cache"}
                else "surrogate" if mode_set == {"screening_estimate"}
                else "mixed_exact_and_surrogate" if "screening_estimate" in mode_set
                else "mixed_or_live"
            ),
            route_source="typed_session_state",
            feed_composition_source=selected.get("feed_composition_source"),
            requested_metrics=metrics,
            route_signature=selected.get("route_signature"),
            consumed_route=selected.get("consumed_route"),
            feed_mass_fractions=selected.get("feed_mass_fractions"),
            processing_capacity_mt_per_yr=capacities[-1],
            scale_baseline_capacity_mt_per_yr=capacities[0],
            scale_selected_capacity_mt_per_yr=capacities[-1],
            energy_case=selected.get("energy_case"),
            energy_case_description=selected.get("energy_case_description"),
            stage_results=selected.get("stage_results"),
            scale_comparison_rows=scale_rows,
            **(
                {
                    "lca_metric_status": scale_status,
                    "lca_method_status_by_energy_case": (
                        _lca_method_status_by_energy_case(scale_results)
                    ),
                    "lca_status_definitions": status_definitions,
                }
                if status_definitions else {}
            ),
            scale_comparison_basis=(
                "same stored route, typed feed composition, energy case, dissolution setpoints, "
                "and precipitation setpoint at every capacity"
            ),
            final_residue=selected.get("final_residue"),
            designated_residue_feed_fraction=selected.get("designated_residue_feed_fraction"),
            gwp_system_boundary=selected.get("gwp_system_boundary"),
            metric_units={
                **selected.get("metric_units", {}),
                "gwp_t_co2e_per_mt": "t CO2e/metric ton modeled dissolution-stage product",
            },
            provenance=selected.get("provenance"),
            process_data_gaps=list(dict.fromkeys([
                *process_gaps,
                *_cache_lca_status_gaps(scale_results),
            ])),
            estimate_quality="screening_only" if estimate else "exact_simulation_evidence",
            estimate_limitations=(
                {
                    "selected_route_solvents_directly_simulated": False,
                    "analog_solvents": analog_solvents,
                    "capacity_extrapolated_stages": extrapolated,
                    "uncertainty_interval_available": False,
                    "solvent_substitution_reflected_in_gwp": False,
                    "missing_temperature_adjustment_stages": missing_temperature,
                }
                if estimate else {}
            ),
            warnings=[
                "Scale changes use cache-derived capacity exponents and analog-solvent evidence; they are not measured scale-up data.",
                "kg CO2e/kg product is numerically equal to t CO2e/metric ton product; total annual emissions can rise while this intensity falls.",
                "The stored route does not establish recovery or an integrated-facility design, and designated-residue impacts are excluded.",
            ],
        )
    stored_composition = getattr(state, "feed_mass_fractions", None) if state else None
    composition_source = "typed_session_state" if stored_composition else "tool_argument"
    supplied_composition = stored_composition or feed_mass_fractions
    if not supplied_composition:
        return tool_error(
            tool, "No stored feed composition is available for this route.",
            error_code="missing_feed_composition", consumed_route=route,
        )
    try:
        composition = _composition(supplied_composition)
        if stored_composition and feed_mass_fractions:
            echoed = _composition(feed_mass_fractions)
            if echoed != composition:
                raise ValueError(
                    "feed_mass_fractions conflicts with the stored route composition"
                )
        capacity = _finite(feed_capacity, "processing_capacity_mt_per_yr")
        precipitation = _finite(selected_precipitation_c, "precipitation_temperature_c")
        if capacity <= 0:
            raise ValueError("processing_capacity_mt_per_yr must be positive")
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_route_basis")
    expected = {
        str(item.get("dissolved_polymer")) for item in route.get("steps") or []
        if item.get("dissolved_polymer")
    }
    if route.get("final_residue"):
        expected.add(str(route["final_residue"]))
    if set(composition) != expected:
        return tool_error(
            tool, "Feed composition must cover exactly the polymers in the stored route.",
            error_code="route_feed_mismatch", route_polymers=sorted(expected),
            supplied_polymers=sorted(composition), consumed_route=route,
        )
    remaining = dict(composition)
    results, rows = [], []
    for index, step in enumerate(route.get("steps") or [], 1):
        polymer = str(step["dissolved_polymer"])
        entering_fraction = sum(remaining.values())
        target_fraction = remaining[polymer]
        stage_capacity = round(capacity * entering_fraction, 10)
        scenario = {
            "solvent": step["solvent"], "target_polymer": polymer,
            "target_mass_percent": round(100.0 * target_fraction / entering_fraction, 10),
            "processing_capacity_mt_per_yr": stage_capacity,
            "energy_case": selected_energy_case,
            "dissolution_temp_c": step["temperature_c"],
            "precipitation_temp_c": precipitation,
        }
        config = None
        try:
            scenario.update(_stored_route_named_remainder(str(step["solvent"])))
            config = _scenario_config(scenario)
        except _MissingScenarioBasis as error:
            result = {
                "success": False,
                "error_type": "insufficient_surrogate_evidence",
                "error": str(error),
                "config": {
                    "target_plastic": polymer,
                    "solvent": thermo.canonical_solvent_name(str(step["solvent"])),
                    "energy_case": selected_energy_case,
                    "target_plastic_percent": scenario["target_mass_percent"],
                    "processing_capacity": stage_capacity,
                    "dissolution_temperature_c": step["temperature_c"],
                    "precipitation_temperature_c": precipitation,
                },
            }
        except _ScenarioInputError as error:
            return tool_error(
                tool,
                str(error),
                error_code=error.error_code,
                stage=index,
                **error.details,
            )
        except ValueError as error:
            return tool_error(tool, str(error), error_code="invalid_route_stage", stage=index)
        else:
            result = _run(config, engine_mode, max(1, min(int(timeout_seconds), 600)))
            if (
                result.get("success") is not True
                and allow_screening_estimate
                and result.get("error_type") != "invalid_engine_mode"
            ):
                result = _screening_estimate(config, result)
        label = f"stage-{index}-{polymer}"
        row = _comparison_row(label, result)
        row.update({
            "stage": index, "original_feed_mass_fraction": target_fraction,
            "entering_feed_mass_fraction": entering_fraction,
        })
        results.append(result)
        rows.append(row)
        if not result.get("success"):
            if result.get("error_type") in {
                "cache_miss", "python_version", "surrogate_unavailable",
                "insufficient_surrogate_evidence",
            }:
                design_point = (
                    _reference_design_point_contract(config)
                    if config is not None else None
                )
                if design_point is not None:
                    requested = design_point["requested_stage_basis"]
                    reference = design_point[
                        "example_reference_design_point"
                    ]
                    message = (
                        "No admitted process record matches route stage "
                        f"{index} at the feed-derived design point "
                        f"({requested['target_mass_percent']:g} wt%, "
                        f"{requested['processing_capacity_mt_per_yr']:g} "
                        "MT/yr). A disclosed reference uses "
                        f"{reference['target_mass_percent']:g} wt% at "
                        f"{reference['processing_capacity_mt_per_yr']:g} "
                        f"MT/yr under {reference['energy_case']}, "
                        f"{reference['dissolution_temperature_c']:g} C "
                        "dissolution, and "
                        f"{reference['precipitation_temperature_c']:g} C "
                        "precipitation. It is a different design point for a "
                        "potentially different plant and was not used to cost "
                        "or rank the route."
                    )
                    return tool_error(
                        tool,
                        message,
                        error_code="route_stage_design_point_unavailable",
                        analysis_type="tea_route_design_point_gap",
                        can_estimate_msp=False,
                        can_estimate_gwp=False,
                        can_cost_route=False,
                        can_rank_route=False,
                        reference_evidence_role=(
                            _REFERENCE_DESIGN_POINT_ROLE
                        ),
                        requested_stage_basis={
                            "stage": index,
                            **requested,
                        },
                        available_reference_design_points=design_point[
                            "available_reference_design_points"
                        ],
                        basis_differences=design_point[
                            "basis_differences"
                        ],
                        route_source="typed_session_state",
                        route_signature=(
                            tea_contracts.route_evidence_signature(route)
                        ),
                        consumed_route=route,
                        feed_mass_fractions=composition,
                        processing_capacity_mt_per_yr=capacity,
                        requested_capacity_basis="total_feed",
                        energy_case=selected_energy_case,
                        requested_metrics=metrics,
                        failed_stage=row,
                        completed_stage_results=rows[:-1],
                        lca_status_definitions=(
                            _collect_lca_status_definitions(results)
                        ),
                        missing_basis_codes=[
                            "exact_route_stage_design_point",
                            "route_stage_process_evidence",
                        ],
                        missing_process_inputs=[
                            "Generate an admitted process record matching "
                            "requested_stage_basis, or explicitly restate the "
                            "feed and capacity to an available reference "
                            "design point.",
                            "Define stage solvent loading, collection and "
                            "recovery, recycle loss, and product specification "
                            "on that same design point.",
                        ],
                        process_data_gaps=[
                            "A same-pair process record exists, but its process "
                            "coordinates do not match the feed-derived route "
                            "stage.",
                            "Reference design points describe their own plant "
                            "basis and cannot be substituted into this route "
                            "cost or ranking.",
                            *_cache_lca_status_gaps(results),
                        ],
                        warnings=[
                            "No MSP, TCI, AOC, GWP, or energy value was "
                            "calculated for the full route.",
                            "Restating the question to an available reference "
                            "design point changes the plant basis being asked "
                            "about.",
                            "Completed earlier stage rows, if any, do not "
                            "establish integrated-route economics.",
                        ],
                    )
                return tool_error(
                    tool,
                    f"No defensible process basis is available for route stage {index} ({polymer}).",
                    error_code="uncostable_route_stage",
                    analysis_type="tea_route_basis_gap",
                    can_estimate_msp=False,
                    can_estimate_gwp=False,
                    route_source="typed_session_state",
                    route_signature=tea_contracts.route_evidence_signature(route),
                    consumed_route=route,
                    feed_mass_fractions=composition,
                    processing_capacity_mt_per_yr=capacity,
                    requested_capacity_basis="total_feed",
                    energy_case=selected_energy_case,
                    requested_metrics=metrics,
                    failed_stage=row,
                    completed_stage_results=rows[:-1],
                    lca_status_definitions=_collect_lca_status_definitions(
                        results,
                    ),
                    missing_basis_codes=[
                        "route_stage_process_evidence",
                        "exact_or_admitted_analog_process_basis",
                    ],
                    missing_process_inputs=[
                        f"Generate an admitted process record or validated analog basis for {polymer} in {step['solvent']} at {step['temperature_c']:g} C.",
                        "Define stage solvent loading, collection and recovery, recycle loss, and product specification on the same route basis.",
                    ],
                    process_data_gaps=[
                        "The thermodynamic route is complete, but a complete separation route is not itself a TEA/LCA process basis.",
                        f"The admitted cache has no process analog for {polymer}; records for other polymers are not substituted.",
                        *_cache_lca_status_gaps(results),
                    ],
                    warnings=[
                        "No MSP, TCI, AOC, GWP, or energy value was calculated for the full route.",
                        "Completed earlier stage rows, if any, do not establish integrated-route economics.",
                    ],
                )
            return tool_error(
                tool, f"TEA stage {index} ({polymer}) could not be evaluated.",
                error_code="route_stage_failed", failed_stage=row,
                completed_stage_results=rows[:-1], route_source="typed_session_state",
                consumed_route=route, live_engine=live_engine_status(),
                lca_status_definitions=_collect_lca_status_definitions(results),
                process_data_gaps=_cache_lca_status_gaps(results),
            )
        row["modeled_stage_product_mt_per_yr"] = round(capacity * target_fraction, 10)
        row["modeled_stage_gwp_t_co2e_per_yr"] = (
            row["modeled_stage_product_mt_per_yr"]
            * float(row["gwp_kg_co2e_per_kg"])
        )
        if (row.get("lca_metric_status") or {}).get(
            "gwp_kg_co2e_per_kg"
        ):
            row["lca_metric_status"][
                "modeled_stage_gwp_t_co2e_per_yr"
            ] = _CACHE_PROPAGATED_GWP_STATUS_CODE
        remaining.pop(polymer)
    dissolution_stage_fraction = sum(
        float(row["original_feed_mass_fraction"]) for row in rows
    )

    def weighted(field: str) -> float:
        return sum(
            float(row[field]) * float(row["original_feed_mass_fraction"])
            for row in rows
        ) / dissolution_stage_fraction

    modes = [str(row["engine_mode"]) for row in rows]
    uses_estimate = "screening_estimate" in modes
    all_estimated = set(modes) == {"screening_estimate"}
    analog_solvents = sorted({
        str(basis["analog_solvent"])
        for row in rows
        if (basis := row.get("estimate_basis") or {}).get("analog_solvent")
    })
    extrapolated_stages = [
        row["label"] for row in rows
        if (row.get("estimate_basis") or {}).get("capacity_extrapolated")
    ]
    missing_temperature_adjustment = [
        row["label"] for row in rows
        if row.get("estimate_basis")
        and not row["estimate_basis"].get("temperature_adjustment_available")
    ]
    total_stage_gwp = sum(float(row["modeled_stage_gwp_t_co2e_per_yr"]) for row in rows)
    for row in rows:
        row["modeled_stage_gwp_contribution_fraction"] = (
            float(row["modeled_stage_gwp_t_co2e_per_yr"]) / total_stage_gwp
            if total_stage_gwp > 0 else 0.0
        )
        if (row.get("lca_metric_status") or {}).get(
            "gwp_kg_co2e_per_kg"
        ):
            row["lca_metric_status"][
                "modeled_stage_gwp_contribution_fraction"
            ] = _CACHE_PROPAGATED_GWP_STATUS_CODE
    dominant_gwp = max(rows, key=lambda row: float(row["modeled_stage_gwp_t_co2e_per_yr"]))
    aggregate_status = _propagated_gwp_metric_status(
        rows,
        (
            "mass_weighted_recovered_gwp_kg_co2e_per_kg",
            "modeled_dissolution_stage_gwp_t_co2e_per_yr",
            "dominant_gwp_stage",
        ),
    )
    status_definitions = {
        **_collect_lca_status_definitions(results),
        **_lca_status_definitions(aggregate_status),
    }
    return tool_success(
        tool, analysis_type="multistage_route_tea_lca",
        engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        cache_match_status=(
            "exact" if set(modes) == {"cache"}
            else "surrogate" if all_estimated
            else "mixed_exact_and_surrogate" if uses_estimate
            else "mixed_or_live"
        ),
        route_source="typed_session_state",
        feed_composition_source=composition_source,
        requested_metrics=metrics,
        route_signature=tea_contracts.route_evidence_signature(route),
        consumed_route=route, feed_mass_fractions=composition,
        processing_capacity_mt_per_yr=capacity, energy_case=selected_energy_case,
        energy_case_description=_ENERGY_CASES.get(selected_energy_case),
        stage_results=rows,
        dissolution_stage_feed_fraction=dissolution_stage_fraction,
        final_residue=route.get("final_residue"),
        designated_residue_feed_fraction=sum(remaining.values()),
        total_stage_tci_usd=sum(float(row["tci_usd"]) for row in rows),
        total_stage_aoc_usd_per_yr=sum(float(row["aoc_usd_per_yr"]) for row in rows),
        mass_weighted_recovered_msp_usd_per_kg=weighted("msp_usd_per_kg"),
        mass_weighted_recovered_gwp_kg_co2e_per_kg=weighted("gwp_kg_co2e_per_kg"),
        modeled_dissolution_stage_gwp_t_co2e_per_yr=total_stage_gwp,
        **(
            {
                "lca_metric_status": aggregate_status,
                "lca_method_status_by_energy_case": (
                    _lca_method_status_by_energy_case(results)
                ),
                "lca_status_definitions": status_definitions,
            }
            if aggregate_status else {}
        ),
        dominant_gwp_stage={
            key: dominant_gwp[key] for key in (
                "stage", "polymer", "solvent",
                "modeled_stage_product_mt_per_yr",
                "gwp_kg_co2e_per_kg",
                "modeled_stage_gwp_t_co2e_per_yr",
                "modeled_stage_gwp_contribution_fraction",
            )
        },
        gwp_contribution_basis=(
            "stage product mass from typed feed composition multiplied by stage GWP intensity; "
            "excludes designated-residue impacts and does not establish recovery"
        ),
        gwp_system_boundary=(
            "cached process-model cradle-to-gate screen per modeled dissolution-stage product; "
            "not a full product life cycle"
        ),
        metric_units=_comparison_metric_units(rows),
        provenance=_provenance(modes),
        process_details=[
            detail for row, result in zip(rows, results)
            if (detail := _process_details(str(row["label"]), result)) is not None
        ],
        process_data_gaps=(
            ["Cached corpus does not contain unit-level equipment sizes or full stream mass balances."]
            if set(modes) == {"cache"} or uses_estimate else []
        ) + ([
            "Stage economics are cache-derived analog estimates, not simulations of the selected solvents or an integrated plant.",
            "The screening estimator does not provide uncertainty or confidence intervals.",
            "Solvent prices are adjusted from the V12-0 TEA assumptions table; solvent substitution is not reflected in estimated GWP.",
        ] + ([
            "Requested capacities are outside the cached scale range for: "
            + ", ".join(extrapolated_stages) + "."
        ] if extrapolated_stages else []) + ([
            "No cached temperature sensitivity was available for: "
            + ", ".join(missing_temperature_adjustment) + "."
        ] if missing_temperature_adjustment else []) if uses_estimate else [])
        + _live_toxicity_data_gaps(results)
        + _cache_lca_status_gaps(results),
        estimate_quality="screening_only" if uses_estimate else "exact_simulation_evidence",
        estimate_limitations=(
            {
                "selected_route_solvents_directly_simulated": False,
                "analog_solvents": analog_solvents,
                "capacity_extrapolated_stages": extrapolated_stages,
                "uncertainty_interval_available": False,
                "solvent_substitution_reflected_in_gwp": False,
                "missing_temperature_adjustment_stages": missing_temperature_adjustment,
            }
            if uses_estimate else {}
        ),
        warnings=[
            "The stored thermodynamic route supplies solvents and setpoints; it does not establish stage recovery or purity.",
            "Summed TCI/AOC assume separate stage simulations and are not an integrated-facility design or cash flow.",
            "Mass-weighted MSP/GWP cover modeled dissolved-stage products only; the final residue is not economically credited.",
            (
                "Screening estimates fit cache-derived capacity exponents and apply source-table solvent-price and available temperature adjustments."
                if uses_estimate else
                "Cached results are exact prior subprocess simulations, never interpolated process economics."
            ),
            "Cached energy intensities correct the legacy worker's annual-total/hourly-flow normalization error using 8,409.6 operating hours per year.",
        ],
    )


def _metric(row: dict[str, Any], metric: str) -> Optional[float]:
    section, key, _ = _METRICS[metric]
    value = (row.get(section) or {}).get(key)
    return None if value is None else float(value)


def analyze_tea_sensitivity(
    scenario: Optional[dict[str, Any]] = None,
    parameter: str = "",
    values: Optional[list[float]] = None,
    metric: str = "msp_usd_per_kg",
    analysis_mode: str = "sweep",
    engine_mode: str = "auto",
    timeout_seconds: int = 180,
    handle: Optional[str] = None,
    row_id: Optional[str | int] = None,
) -> str:
    """Run a deterministic parameter sweep/tornado slice or sampled uncertainty summary.

    The baseline requires the same complete twelve public D-8 fields as
    evaluate unless a prior economics handle supplies the omitted ones.
    With values omitted, exact cached one-at-a-time variants are discovered
    around that baseline. Supported parameter names are every numeric
    scenario field the worker consumes, including dissolution_capacity
    and labor_cost. `uncertainty` describes only the supplied/discovered
    scenario sample; it is not a probabilistic Monte Carlo claim. This is
    not a cache-pair fill and not a screening payload.
    """
    tool = "analyze_tea_sensitivity"
    field = _SCENARIO_ALIASES.get(
        str(parameter or "").strip(), str(parameter or "").strip(),
    )
    mode = str(analysis_mode or "sweep").casefold()
    if field not in _NUMERIC_FIELDS:
        return tool_error(
            tool,
            "Unsupported sensitivity parameter.",
            error_code="unsupported_parameter",
            supported_parameters=sorted(_NUMERIC_FIELDS),
        )
    if metric not in _METRICS:
        return tool_error(tool, "Unsupported sensitivity metric.", error_code="unsupported_metric", supported_metrics=sorted(_METRICS))
    if mode not in {"sweep", "tornado", "uncertainty"}:
        return tool_error(tool, "analysis_mode must be sweep, tornado, or uncertainty", error_code="invalid_analysis_mode")
    field_origin = None
    try:
        scenario, field_origin = _compose_sensitivity_scenario(
            scenario, handle, row_id,
        )
        baseline = _scenario_config(scenario, require_complete_twelve=True)
        requested_values = [] if values is None else [_finite(value, field) for value in values]
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )

    except _ScenarioInputError as error:
        return tool_error(
            tool,
            str(error),
            error_code=error.error_code,
            **error.details,
        )
    except ValueError as error:
        return tool_error(tool, str(error), error_code="invalid_sensitivity_basis")
    if not requested_values:
        comparison_key = {
            key: baseline[key]
            for key in _CONFIG_FIELDS
            if key != field
        }
        requested_values = sorted({
            float(record["config"][field]) for record in _records()
            if all(
                _key(record["config"][key]) == _key(value)
                if key in {"solvent", "target_plastic"}
                else str(record["config"][key]).upper() == str(value).upper()
                if key == "energy_case"
                else abs(float(record["config"][key]) - float(value)) < 1e-9
                for key, value in comparison_key.items()
            )
        })
    requested_values = list(dict.fromkeys([float(baseline[field]), *requested_values]))
    if len(requested_values) < 2:
        return tool_error(tool, "At least two exact sensitivity values are required.", error_code="insufficient_sensitivity_values")
    if len(requested_values) > 20:
        return tool_error(tool, "At most 20 sensitivity values may run per call.", error_code="too_many_sensitivity_values")
    results = [_run({**baseline, field: value}, engine_mode, max(1, min(int(timeout_seconds), 600))) for value in requested_values]
    rows = []
    for value, result in zip(requested_values, results):
        measured = _metric(result, metric) if result.get("success") else None
        source_status = (result.get("lca_metric_status") or {}).get(
            "gwp_kg_co2e_per_kg"
        ) if metric == "gwp_kg_co2e_per_kg" else None
        run_config = dict(result.get("config") or {**baseline, field: value})
        row = {
            "parameter": field, "value": value, "metric": metric,
            "metric_value": measured, "success": bool(result.get("success")),
            "engine_mode": result.get("engine_mode"),
            "cache_record_label": result.get("cache_record_label"),
            **_sensitivity_row_process_fields(run_config),
            **(
                {"lca_metric_status": {"metric_value": source_status}}
                if source_status else {}
            ),
            **({"error": result.get("error")} if not result.get("success") else {}),
        }
        if field_origin is not None:
            row["field_origin"] = dict(field_origin)
        rows.append(row)
    successes = [row for row in rows if row["success"] and row["metric_value"] is not None]
    if len(successes) < 2:
        return tool_error(tool, "Fewer than two sensitivity scenarios completed.", error_code="insufficient_sensitivity_results", sensitivity_rows=rows, live_engine=live_engine_status())
    values_out = [float(row["metric_value"]) for row in successes]
    baseline_row = min(successes, key=lambda row: abs(float(row["value"]) - float(baseline[field])))
    modes = [str(row["engine_mode"]) for row in successes]
    conclusion_status = (
        {
            field: _CACHE_PROPAGATED_GWP_STATUS_CODE
            for field in (
                "baseline_metric_value", "minimum_metric_value",
                "maximum_metric_value", "metric_span",
                "sample_median_metric_value",
            )
        }
        if _has_noncurrent_cache_gwp(successes) else {}
    )
    status_definitions = {
        **_collect_lca_status_definitions(results),
        **_lca_status_definitions(conclusion_status),
    }
    return tool_success(
        tool, analysis_type=f"tea_{mode}", engine_mode=modes[0] if len(set(modes)) == 1 else "mixed",
        polymer=baseline["target_plastic"], solvent=baseline["solvent"],
        energy_case=baseline["energy_case"],
        processing_capacity_mt_per_yr=baseline["processing_capacity"],
        temperature_c=baseline["dissolution_temperature_c"],
        parameter=field, metric=metric, metric_unit=_METRICS[metric][2],
        baseline_parameter_value=baseline[field], baseline_metric_value=baseline_row["metric_value"],
        sensitivity_rows=rows,
        **({"field_origin": dict(field_origin)} if field_origin is not None else {}),
        minimum_metric_value=min(values_out), maximum_metric_value=max(values_out),
        metric_span=max(values_out) - min(values_out),
        sample_median_metric_value=statistics.median(values_out),
        sample_count=len(successes),
        **(
            {
                "lca_metric_status": conclusion_status,
                "lca_method_status_by_energy_case": (
                    _lca_method_status_by_energy_case(results)
                ),
                "lca_status_definitions": status_definitions,
                "metric_analysis_status": {
                    "status": "published_cache_comparison_only",
                    "current_scientific_conclusion": False,
                    "reason": _CACHE_PROPAGATED_GWP_STATUS_CODE,
                },
            }
            if conclusion_status else {}
        ),
        process_data_gaps=_cache_lca_status_gaps(results),
        provenance=_provenance(modes),
        warnings=[
            "This is a one-parameter scenario analysis; it does not establish causal sensitivity outside the evaluated values.",
            "An uncertainty-mode summary describes the finite scenario sample, not a fitted probability distribution or Monte Carlo confidence interval."
            if mode == "uncertainty" else
            "Cached sensitivity points are exact prior simulations and are not interpolated between evaluated values.",
        ],
    )
