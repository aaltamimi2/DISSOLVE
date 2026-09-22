"""Closed registry vocabulary for admitted TEA/LCA record selection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping


def route_evidence_signature(route: Any) -> str | None:
    """Return the canonical identity used to bind route-derived evidence."""
    if not isinstance(route, dict) or not route:
        return None
    canonical = json.dumps(route, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class TeaSensitivityAxisContract:
    """One admitted sensitivity axis and its model-facing aliases."""

    name: str
    aliases: tuple[str, ...]
    cache_label_token: str


@dataclass(frozen=True)
class TeaSensitivityLevelSelectorContract:
    """One bounded selection over sensitivity-record levels."""

    name: str
    aliases: tuple[str, ...]
    levels: tuple[str, ...]


@dataclass(frozen=True)
class TeaRecordFormContract:
    """One output cardinality form for admitted records."""

    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class TeaRecordSelectorContract:
    """One typed identity or record-coordinate selector."""

    name: str
    aliases: tuple[str, ...]
    tool_argument: str


@dataclass(frozen=True)
class TeaEnergyCaseContract:
    """One admitted energy case and its bounded model-facing aliases."""

    name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class TeaEnergyCaseScopeContract:
    """One finite selection over admitted energy cases."""

    name: str
    aliases: tuple[str, ...]
    cases: tuple[str, ...]


@dataclass(frozen=True)
class TeaAdmittedProcessGroupContract:
    """One process identity represented by exact admitted cache records."""

    name: str
    target_polymer: str
    solvent: str
    target_aliases: tuple[str, ...]
    solvent_aliases: tuple[str, ...]


@dataclass(frozen=True)
class TeaProcessGroupScopeContract:
    """One finite selection over admitted process groups."""

    name: str
    aliases: tuple[str, ...]
    group_names: tuple[str, ...]


@dataclass(frozen=True)
class TeaCapacityUnitContract:
    """One capacity phrase and its conversion to metric tonnes per year."""

    name: str
    aliases: tuple[str, ...]
    mt_per_year_factor: float


@dataclass(frozen=True)
class TeaToolBoundaryContract:
    """One manifest-selected TEA execution lane."""

    mode: str
    tool_name: str


TEA_SENSITIVITY_AXIS_REGISTRY = (
    TeaSensitivityAxisContract(
        "solvent_price",
        ("price", "solvent_cost"),
        "price",
    ),
    TeaSensitivityAxisContract(
        "plant_scale",
        (
            "scale", "capacity", "processing_capacity",
            "plant capacity", "plant size",
        ),
        "scale",
    ),
    TeaSensitivityAxisContract(
        "solvent_loss",
        (
            "loss", "solvent_makeup",
        ),
        "loss",
    ),
    TeaSensitivityAxisContract(
        "dissolution_temperature",
        (
            "dissolution_temp", "dissolution",
        ),
        "dissolution",
    ),
    TeaSensitivityAxisContract(
        "precipitation_temperature",
        (
            "precipitation_temp", "precipitation",
        ),
        "precipitation",
    ),
    TeaSensitivityAxisContract(
        "feedstock_distance",
        (
            "distance", "transport_distance",
        ),
        "distance",
    ),
    TeaSensitivityAxisContract(
        "feed_composition",
        (
            "feed", "target_fraction", "target plastic fraction",
        ),
        "feed",
    ),
)

TEA_SENSITIVITY_LEVEL_SELECTOR_REGISTRY = (
    TeaSensitivityLevelSelectorContract(
        "low_high",
        (
            "lows and highs", "low and high", "low high pair",
            "extremes",
        ),
        ("low", "high"),
    ),
)

TEA_RECORD_FORM_REGISTRY = (
    TeaRecordFormContract(
        "per_record",
        ("individual records", "recordwise"),
    ),
    TeaRecordFormContract(
        "grouped_comparison",
        (
            "aggregate", "comparison", "aggregate comparison", "grouped",
        ),
    ),
)

TEA_ENERGY_CASE_REGISTRY = (
    TeaEnergyCaseContract(
        "C1",
        (
            "case 1",
            "CHP",
            "onsite CHP",
            "on site boiler and turbogenerator",
            "combined heat and power",
        ),
    ),
    TeaEnergyCaseContract(
        "C2",
        (
            "case 2",
            "grid electricity",
            "grid power",
            "grid only",
        ),
    ),
    TeaEnergyCaseContract(
        "C3",
        (
            "case 3",
            "hybrid",
            "grid plus onsite boiler",
        ),
    ),
)

@dataclass(frozen=True)
class TeaRecordCardBoundaryContract:
    """The record field whose distinct values are the coverage boundaries.

    ``config_key`` is the admitted-cache spelling and ``metadata_key`` the
    spelling the same boundary takes in artifact metadata. They differ
    ("target_plastic" vs "target_polymers"), and stating the mapping once
    here is what stops a consumer from guessing which vocabulary an
    artifact speaks.
    """

    name: str
    config_key: str
    metadata_key: str


def normalize_tea_vocabulary(value: object) -> str:
    """Normalize case and separators without interpreting request prose."""
    groups: list[str] = []
    current: list[str] = []
    for character in str(value or "").casefold():
        if character.isalnum():
            current.append(character)
        elif current:
            groups.append("".join(current))
            current = []
    if current:
        groups.append("".join(current))
    return "_".join(groups)


def _index(
    contracts: Iterable[
        TeaSensitivityAxisContract
        | TeaSensitivityLevelSelectorContract
        | TeaRecordFormContract
        | TeaRecordSelectorContract
    ],
) -> MappingProxyType:
    indexed = {}
    for contract in contracts:
        for value in (contract.name, *contract.aliases):
            normalized = normalize_tea_vocabulary(value)
            if not normalized or normalized in indexed:
                raise ValueError(
                    f"Duplicate TEA vocabulary value: {value!r}",
                )
            indexed[normalized] = contract
    return MappingProxyType(indexed)


_SENSITIVITY_LEVEL_SELECTOR_BY_NAME = _index(
    TEA_SENSITIVITY_LEVEL_SELECTOR_REGISTRY,
)
_RECORD_FORM_BY_NAME = _index(TEA_RECORD_FORM_REGISTRY)
_ENERGY_CASE_BY_NAME = _index(TEA_ENERGY_CASE_REGISTRY)

CANONICAL_TEA_SENSITIVITY_AXES = tuple(
    item.name for item in TEA_SENSITIVITY_AXIS_REGISTRY
)
CANONICAL_TEA_SENSITIVITY_LEVEL_SELECTORS = tuple(
    item.name for item in TEA_SENSITIVITY_LEVEL_SELECTOR_REGISTRY
)
CANONICAL_TEA_RECORD_FORMS = tuple(
    item.name for item in TEA_RECORD_FORM_REGISTRY
)
TEA_SENSITIVITY_CACHE_TOKEN_BY_AXIS = MappingProxyType({
    item.name: item.cache_label_token
    for item in TEA_SENSITIVITY_AXIS_REGISTRY
})


def _canonical(value: object, indexed: MappingProxyType) -> str:
    contract = indexed.get(normalize_tea_vocabulary(value))
    return contract.name if contract is not None else str(value or "")


def canonical_tea_sensitivity_level_selector(value: object) -> str:
    return _canonical(value, _SENSITIVITY_LEVEL_SELECTOR_BY_NAME)


def canonical_tea_record_form(value: object) -> str:
    return _canonical(value, _RECORD_FORM_BY_NAME)


def canonical_tea_energy_case(value: object) -> str:
    return _canonical(value, _ENERGY_CASE_BY_NAME)


def tea_sensitivity_levels(value: object) -> tuple[str, ...]:
    contract = _SENSITIVITY_LEVEL_SELECTOR_BY_NAME.get(
        normalize_tea_vocabulary(value),
    )
    return contract.levels if contract is not None else ()


_TEA_CONFIG_LABEL_SUFFIXES = (
    "_usd_per_kg",
    "_usd_per_employee_yr",
    "_mt_per_yr",
    "_percent",
    "_pct",
    "_km",
    "_c",
)


@dataclass(frozen=True)
class TeaConfigFieldContract:
    """One numeric source-locked TEA configuration field.

    ``config_key`` is the admitted-cache spelling. ``observation_keys`` are
    the spellings the same field takes at the observation boundary — the
    compacted ``record_assumptions`` manifest and the admitted-record
    summaries. ``unit`` is the money unit class a truthful prose mention of
    the value carries (``None`` when the field never appears as a money
    literal). ``label`` is derived mechanically from the configuration key
    so claim binding never depends on hand-picked vocabulary.
    """

    config_key: str
    observation_keys: tuple[str, ...]
    unit: str | None
    label: str


def _tea_config_field(
    config_key: str,
    observation_keys: tuple[str, ...],
    unit: str | None,
) -> TeaConfigFieldContract:
    label = config_key
    for suffix in _TEA_CONFIG_LABEL_SUFFIXES:
        if label.endswith(suffix):
            label = label[: -len(suffix)]
            break
    return TeaConfigFieldContract(
        config_key=config_key,
        observation_keys=observation_keys,
        unit=unit,
        label=label.replace("_", " "),
    )


TEA_CONFIG_FIELD_CONTRACTS: tuple[TeaConfigFieldContract, ...] = (
    _tea_config_field(
        "solvent_price", ("solvent_price_usd_per_kg",), "USD/kg",
    ),
    _tea_config_field(
        "labor_cost",
        ("labor_cost", "labor_cost_usd_per_employee_yr"),
        "USD/yr",
    ),
    _tea_config_field(
        "target_plastic_percent", ("target_mass_percent",), "%",
    ),
    _tea_config_field(
        "processing_capacity", ("processing_capacity_mt_per_yr",), "mt/yr",
    ),
    _tea_config_field(
        "dissolution_temperature_c", ("dissolution_temperature_c",), "°C",
    ),
    _tea_config_field(
        "precipitation_temperature_c", ("precipitation_temperature_c",), "°C",
    ),
    _tea_config_field("solvent_loss_pct", ("solvent_loss_pct",), "%"),
    _tea_config_field(
        "feedstock_distance_km", ("feedstock_distance_km",), "km",
    ),
    _tea_config_field("dissolution_capacity", ("dissolution_capacity",), None),
)
