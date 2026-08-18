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
class TeaImplicitRecordSelectors:
    """Selectors derived only from a manifest's harness-owned request units."""

    process_groups: tuple[str, ...] = ()
    target_polymers: tuple[str, ...] = ()
    solvents: tuple[str, ...] = ()
    energy_cases: tuple[str, ...] = ()
    sensitivity_axes: tuple[str, ...] = ()
    processing_capacity_mt_per_yr: float | None = None
    record_form: str = "per_record"


@dataclass(frozen=True)
class TeaToolBoundaryContract:
    """One manifest-selected TEA execution lane."""

    mode: str
    tool_name: str


@dataclass(frozen=True)
class TeaToolBoundaryDecision:
    """The execution lane or honest terminal selected from typed facts."""

    mode: str
    tool_name: str | None
    target_polymers: tuple[str, ...]
    solvents: tuple[str, ...]
    missing_input_fields: tuple[str, ...] = ()
    implicit_selectors: TeaImplicitRecordSelectors = (
        TeaImplicitRecordSelectors()
    )


@dataclass(frozen=True)
class ParetoSurfaceBoundaryContract:
    """One Pareto/trade-off surface and its manifest-fact prerequisites."""

    surface_type: str
    tool_name: str
    view_name: str | None
    requires_stored_route: bool
    requires_admitted_records: bool


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

TEA_RECORD_SELECTOR_REGISTRY = (
    TeaRecordSelectorContract(
        "target_polymer",
        ("polymer", "target_plastic"),
        "target_polymer",
    ),
    TeaRecordSelectorContract(
        "solvent",
        ("solvent_name", "solvent identity"),
        "solvent",
    ),
    TeaRecordSelectorContract(
        "energy_case",
        ("case", "energy cases"),
        "energy_cases",
    ),
    TeaRecordSelectorContract(
        "sensitivity_label",
        ("sensitivity_axis", "sensitivity labels"),
        "sensitivity_axes",
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

TEA_ENERGY_CASE_SCOPE_REGISTRY = (
    TeaEnergyCaseScopeContract(
        "all_energy_cases",
        (
            "all energy cases",
            "all three energy cases",
            "three energy scenarios",
            "three energy configurations",
            "three utility setups",
            "utility setups",
            "energy setups",
        ),
        ("C1", "C2", "C3"),
    ),
)

TEA_ADMITTED_PROCESS_GROUP_REGISTRY = (
    TeaAdmittedProcessGroupContract(
        "ldpe_dodecane",
        "LDPE",
        "Dodecane",
        ("LDPE", "LDPE process", "LDPE recovery"),
        (
            "Dodecane",
            "Dodecane process",
            "Dodecane recovery",
        ),
    ),
    TeaAdmittedProcessGroupContract(
        "evoh_ethylene_glycol",
        "EVOH",
        "Ethylene Glycol",
        ("EVOH", "EVOH process", "EVOH recovery"),
        (
            "Ethylene Glycol",
            "Ethylene Glycol process",
            "Ethylene Glycol recovery",
        ),
    ),
)

TEA_PROCESS_GROUP_SCOPE_REGISTRY = (
    TeaProcessGroupScopeContract(
        "all_admitted_processes",
        (
            "both recovery processes",
            "both processes",
            "both simulated processes",
            "both recovery routes",
            "all admitted processes",
        ),
        tuple(
            item.name for item in TEA_ADMITTED_PROCESS_GROUP_REGISTRY
        ),
    ),
)

TEA_CAPACITY_UNIT_REGISTRY = (
    TeaCapacityUnitContract(
        "metric_tonnes_per_year",
        (
            "mt per yr",
            "metric tonnes per year",
            "metric tons per year",
            "tonnes per year",
            "tons per year",
        ),
        1.0,
    ),
    TeaCapacityUnitContract(
        "kilotonnes_per_year",
        (
            "kiloton scale",
            "kilotonne scale",
            "kilotons per year",
            "kilotonnes per year",
            "kt per year",
        ),
        1_000.0,
    ),
)

TEA_IMPLICIT_SENSITIVITY_ALIASES = MappingProxyType({
    "solvent_price": ("solvent price",),
    "plant_scale": (
        "plant capacity",
        "plant scale",
        "plant size",
        "bigger or smaller",
        "scale up",
        "scale down",
        "capacity change",
    ),
    "solvent_loss": ("solvent loss",),
    "dissolution_temperature": ("dissolution temperature",),
    "precipitation_temperature": ("precipitation temperature",),
    "feedstock_distance": (
        "feedstock distance",
        "travel farther",
        "haul distance",
    ),
    "feed_composition": ("feed composition", "target plastic fraction"),
})

TEA_TOOL_BOUNDARY_REGISTRY = (
    TeaToolBoundaryContract(
        "route_integrated",
        "evaluate_stored_route_tea_lca",
    ),
    TeaToolBoundaryContract(
        "admitted_records",
        "lookup_admitted_process_records",
    ),
)
TEA_TOOL_BOUNDARY_BY_MODE = MappingProxyType({
    item.mode: item for item in TEA_TOOL_BOUNDARY_REGISTRY
})

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


@dataclass(frozen=True)
class TeaRecordCardSelectionContract:
    """How record cards are chosen when the request spans several boundaries.

    Coverage is the primary obligation: one representative per requested
    boundary is chosen before any boundary receives a second card, so a
    request spanning two stages can never render one stage twice while
    dropping the other. ``render_cap`` bounds the figure, and anything the
    cap excludes is reported rather than dropped.
    """

    name: str
    boundary: TeaRecordCardBoundaryContract
    energy_case_preference: tuple[str, ...]
    render_cap: int
    description: str


TEA_RECORD_CARD_BOUNDARY = TeaRecordCardBoundaryContract(
    "target_polymer", "target_plastic", "target_polymers",
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


_SENSITIVITY_AXIS_BY_NAME = _index(TEA_SENSITIVITY_AXIS_REGISTRY)
_SENSITIVITY_LEVEL_SELECTOR_BY_NAME = _index(
    TEA_SENSITIVITY_LEVEL_SELECTOR_REGISTRY,
)
_RECORD_FORM_BY_NAME = _index(TEA_RECORD_FORM_REGISTRY)
_RECORD_SELECTOR_BY_NAME = _index(TEA_RECORD_SELECTOR_REGISTRY)
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
CANONICAL_TEA_ENERGY_CASES = tuple(
    item.name for item in TEA_ENERGY_CASE_REGISTRY
)


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


def _manifest_names(
    deliverable: Mapping[str, Any],
    fields: tuple[str, ...],
) -> tuple[str, ...]:
    values = [
        str(value).strip()
        for field in fields
        for value in (
            deliverable.get(field)
            if isinstance(deliverable.get(field), list) else ()
        )
        if str(value).strip()
    ]
    return tuple(dict.fromkeys(values))


def _text_tokens(value: object) -> tuple[str, ...]:
    """Tokenize registry input while preserving finite numeric literals."""
    text = str(value or "").casefold()
    tokens: list[str] = []
    current: list[str] = []
    for index, character in enumerate(text):
        numeric_separator = bool(
            character in {".", ","}
            and current
            and all(item.isdigit() or item in {".", ","} for item in current)
            and index + 1 < len(text)
            and text[index + 1].isdigit()
        )
        if character.isalnum() or numeric_separator:
            current.append(character)
        elif current:
            tokens.append("".join(current))
            current = []
    if current:
        tokens.append("".join(current))
    return tuple(tokens)


def _alias_tokens(value: object) -> tuple[str, ...]:
    return tuple(
        item for item in normalize_tea_vocabulary(value).split("_")
        if item
    )


def _contains_alias(
    source_tokens: tuple[str, ...],
    alias: object,
) -> bool:
    expected = _alias_tokens(alias)
    if not expected or len(expected) > len(source_tokens):
        return False
    return any(
        source_tokens[index:index + len(expected)] == expected
        for index in range(len(source_tokens) - len(expected) + 1)
    )


def _matching_contracts(
    source_tokens: tuple[str, ...],
    contracts: Iterable[Any],
) -> tuple[Any, ...]:
    return tuple(
        contract for contract in contracts
        if any(
            _contains_alias(source_tokens, alias)
            for alias in (contract.name, *contract.aliases)
        )
    )


def _manifest_source_text(
    deliverable: Mapping[str, Any],
    request_units: Iterable[Mapping[str, Any]],
) -> str:
    unit_ids = {
        str(value) for value in deliverable.get("unit_ids") or ()
    }
    return " ".join(
        str(unit.get("source_text") or "")
        for unit in request_units
        if (
            isinstance(unit, Mapping)
            and str(unit.get("unit_id") or "") in unit_ids
        )
    ).strip()


def _capacity_from_tokens(
    source_tokens: tuple[str, ...],
) -> float | None:
    values: list[float] = []
    for contract in TEA_CAPACITY_UNIT_REGISTRY:
        for alias in contract.aliases:
            expected = _alias_tokens(alias)
            for index in range(
                len(source_tokens) - len(expected) + 1,
            ):
                if source_tokens[index:index + len(expected)] != expected:
                    continue
                if index == 0:
                    continue
                try:
                    literal = float(
                        source_tokens[index - 1].replace(",", ""),
                    )
                except ValueError:
                    continue
                candidate = literal * contract.mt_per_year_factor
                if candidate > 0 and candidate not in values:
                    values.append(candidate)
    return values[0] if len(values) == 1 else None


def resolve_implicit_tea_record_selectors(
    deliverable: Mapping[str, Any] | None,
    request_units: Iterable[Mapping[str, Any]] = (),
) -> TeaImplicitRecordSelectors:
    """Resolve bounded cache selectors from exact manifest-owned unit text."""
    manifest = deliverable or {}
    source_text = _manifest_source_text(manifest, request_units)
    source_tokens = _text_tokens(source_text)
    group_names = [
        group.name
        for scope in _matching_contracts(
            source_tokens,
            TEA_PROCESS_GROUP_SCOPE_REGISTRY,
        )
        for group in TEA_ADMITTED_PROCESS_GROUP_REGISTRY
        if group.name in scope.group_names
    ]
    for group in TEA_ADMITTED_PROCESS_GROUP_REGISTRY:
        if any(
            _contains_alias(source_tokens, alias)
            for alias in (
                *group.target_aliases,
                *group.solvent_aliases,
            )
        ):
            group_names.append(group.name)
    selected_groups = [
        group for group in TEA_ADMITTED_PROCESS_GROUP_REGISTRY
        if group.name in dict.fromkeys(group_names)
    ]

    energy_cases = [
        case
        for scope in _matching_contracts(
            source_tokens,
            TEA_ENERGY_CASE_SCOPE_REGISTRY,
        )
        for case in scope.cases
    ]
    energy_cases.extend(
        contract.name for contract in _matching_contracts(
            source_tokens,
            TEA_ENERGY_CASE_REGISTRY,
        )
    )
    sensitivity_axes = [
        contract.name for contract in TEA_SENSITIVITY_AXIS_REGISTRY
        if any(
            _contains_alias(source_tokens, alias)
            for alias in TEA_IMPLICIT_SENSITIVITY_ALIASES[
                contract.name
            ]
        )
    ]
    return TeaImplicitRecordSelectors(
        process_groups=tuple(
            dict.fromkeys(group.name for group in selected_groups)
        ),
        target_polymers=tuple(dict.fromkeys(
            group.target_polymer for group in selected_groups
        )),
        solvents=tuple(dict.fromkeys(
            group.solvent for group in selected_groups
        )),
        energy_cases=tuple(
            case for case in CANONICAL_TEA_ENERGY_CASES
            if case in energy_cases
        ),
        sensitivity_axes=tuple(
            axis for axis in CANONICAL_TEA_SENSITIVITY_AXES
            if axis in sensitivity_axes
        ),
        processing_capacity_mt_per_yr=_capacity_from_tokens(
            source_tokens,
        ),
        record_form=(
            "grouped_comparison"
            if len(selected_groups) > 1 or len(set(energy_cases)) > 1
            else "per_record"
        ),
    )


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
