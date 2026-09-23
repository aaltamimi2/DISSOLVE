"""Closed registry vocabulary for admitted TEA/LCA record selection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable


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


