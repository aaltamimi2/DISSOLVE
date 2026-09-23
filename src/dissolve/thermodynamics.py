"""Compact, deterministic thermodynamic kernel for DISSOLVE."""

from __future__ import annotations

import inspect
import math
import re
import threading
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache, wraps
from importlib.resources import files
from pathlib import Path
from typing import Annotated, Any, Callable, Iterator, Literal, Optional, Sequence

import duckdb
from langchain_core.tools import InjectedToolArg

from .contracts import parse_tool_result, tool_error, tool_success

GRID_EXACT = "grid_exact"
GRID_INTERPOLATION = "grid_interpolation"
SOLUBILITY_MODEL_BASIS = (
    "stored pair-specific values at 5 C nodes from 25 to 160 C; exact lookup only"
)

FITTED_TEMP_MIN_C = 25.0
FITTED_TEMP_MAX_C = 160.0
SENSITIVITY_EXTRAPOLATION_MAX_C = 200.0
EXTENDED_ADMISSION_MIN_BOILING_POINT_C = 25.0


def polymer_label_key(value: str) -> str:
    """Normalize a polymer label only for identity lookup, never as evidence."""
    return re.sub(r"[^A-Z0-9]+", "", str(value).strip().upper())


# The sole cross-capability polymer identity registry. Evidence availability is
# intentionally separate: aliases identify a material; thermodynamic_members
# declares how a generic feed label expands against the stored grid.
POLYMER_IDENTITIES: dict[str, dict[str, tuple[str, ...] | str]] = {
    "ABS": {"aliases": ("acrylonitrile butadiene styrene",)},
    "EVOH": {"aliases": ("ethylene vinyl alcohol", "ethylene-vinyl alcohol")},
    "FEP": {"aliases": ("fluorinated ethylene propylene",)},
    "HDPE": {"aliases": ("high density polyethylene", "high-density polyethylene")},
    "LDPE": {"aliases": ("low density polyethylene", "low-density polyethylene")},
    "NYLON": {
        "aliases": ("nylon", "nylons", "polyamide", "polyamides"),
        "thermodynamic_members": ("NYLON6", "NYLON66"),
        "hsp_family": "polyamides",
    },
    "NYLON6": {
        "aliases": ("nylon 6", "nylon-6", "nylon6", "PA6", "polyamide 6", "polyamide-6"),
    },
    "NYLON11": {"aliases": ("nylon 11", "nylon-11", "nylon11", "PA11", "polyamide 11")},
    "NYLON66": {
        "aliases": (
            "nylon 66", "nylon-66", "nylon66", "nylon 6,6", "nylon-6,6",
            "PA66", "PA 66", "polyamide 66", "polyamide 6,6",
        ),
    },
    "PC": {"aliases": ("polycarbonate",)},
    "PE": {
        "aliases": ("polyethylene",),
        "thermodynamic_members": ("LDPE", "HDPE"),
        # Retains the pinned low-level v10 point alias. Collection-valued user
        # boundaries expand thermodynamic_members; scalar roles reject a
        # multi-member family before calling the point resolver.
        "point_default": "HDPE",
    },
    "PEEK": {"aliases": ("polyether ether ketone", "polyetheretherketone")},
    "PES": {"aliases": ("polyethersulfone", "polyether sulfone")},
    "PET": {
        "aliases": (
            "polyethylene terephthalate", "poly(ethylene terephthalate)",
            "polyethylene-terephthalate",
        ),
    },
    "PETG": {"aliases": ("polyethylene terephthalate glycol", "glycol-modified PET")},
    "PLA": {"aliases": ("polylactic acid", "poly(lactic acid)", "polylactide")},
    "PCTFE": {"aliases": ("polychlorotrifluoroethylene",)},
    "PMMA": {"aliases": ("polymethyl methacrylate", "poly(methyl methacrylate)")},
    "POM": {"aliases": ("polyoxymethylene", "acetal")},
    "PP": {"aliases": ("polypropylene",)},
    "PS": {"aliases": ("polystyrene",)},
    "PSU": {"aliases": ("polysulfone",)},
    "PTFE": {"aliases": ("polytetrafluoroethylene",)},
    "PVAC": {"aliases": ("polyvinyl acetate", "poly(vinyl acetate)")},
    "PVDF": {"aliases": ("polyvinylidene fluoride", "poly(vinylidene fluoride)")},
    "PVC": {"aliases": ("polyvinyl chloride", "poly(vinyl chloride)")},
    "PVOH": {"aliases": ("polyvinyl alcohol", "poly(vinyl alcohol)")},
    "SAN": {"aliases": ("styrene acrylonitrile", "styrene-acrylonitrile")},
    # Family nouns resolve through the same authority as exact polymer names.
    # They intentionally have no thermodynamic_members: HSP family evidence is
    # qualitative and must never masquerade as measured solubility coverage.
    "POLYOLEFINS": {
        "aliases": ("polyolefin", "polyolefins"),
        "identity_kind": "family", "hsp_family": "polyolefins",
    },
    "POLYURETHANES": {
        # The grid stores one polyurethane row, labelled PU, with 78 solvents
        # against the other polymers' 990. Registering PU as this family's one
        # represented member keeps the family framing v11 chose while making the
        # stored values reachable; a PU answer is a thinner claim than an LDPE
        # answer and the coverage field says so.
        "aliases": ("polyurethane", "polyurethanes", "PU family", "PU"),
        "thermodynamic_members": ("PU",),
        "identity_kind": "family", "hsp_family": "polyurethanes",
    },
    "ACRYLICS": {
        "aliases": ("acrylic", "acrylics", "acrylic polymers"),
        "identity_kind": "family", "hsp_family": "acrylics",
    },
    "EPOXIES": {
        "aliases": ("epoxy", "epoxies", "epoxy resins"),
        "identity_kind": "family", "hsp_family": "epoxies",
    },
    "CELLULOSE_ESTERS": {
        "aliases": ("cellulose ester", "cellulose esters"),
        "identity_kind": "family", "hsp_family": "cellulose_esters",
    },
    "SILICONES": {
        "aliases": ("silicone", "silicones", "silicone polymers"),
        "identity_kind": "family", "hsp_family": "silicones",
    },
    "POLYESTERS": {
        "aliases": ("polyester", "polyesters"),
        "identity_kind": "family", "hsp_family": "polyesters",
    },
    "STYRENICS": {
        "aliases": ("styrenic", "styrenics", "styrenic polymers"),
        "identity_kind": "family", "hsp_family": "styrenics",
    },
    "VINYL_BARRIER_POLYMERS": {
        "aliases": ("vinyl polymers", "barrier polymers"),
        "identity_kind": "family", "hsp_family": "vinyl_barrier",
    },
    "ENGINEERING_POLYMERS": {
        "aliases": ("engineering polymers",),
        "identity_kind": "family", "hsp_family": "engineering",
    },
    "FLUOROPOLYMERS": {
        "aliases": ("fluoropolymer", "fluoropolymers"),
        "identity_kind": "family", "hsp_family": "fluoropolymers",
    },
}


def _identity_aliases(identity: str) -> tuple[str, ...]:
    record = POLYMER_IDENTITIES[identity]
    return (identity, *(str(item) for item in record.get("aliases", ())))


POLYMER_ALIASES = {
    polymer_label_key(alias): identity
    for identity in POLYMER_IDENTITIES
    for alias in _identity_aliases(identity)
}
# Source-system labels that identify an existing grid solvent but are not
# present in the V12-0 alias table. This label is used by the committed Zhou
# workbook and by the legacy GSK/BioSTEAM registry for methyl ethyl ketone.
SOURCE_SOLVENT_ALIASES = {"2-butanone": "butanone"}

_ASSET = Path(str(files("dissolve").joinpath("data/thermodynamics.duckdb")))
_ASSET_SHA256 = "4aa3adc7af54c295c6a647bb8b04a72b0ffc0a7adc75ec4b9d920099df0311dc"
_LOCAL = threading.local()
_QUERY_SOLVENT_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar(
    "dissolve_query_solvent_scope", default=None,
)
COMMON_INTERP_KEYS: frozenset[str] = frozenset((
    "1,2,4-trimethylbenzene", "1,2-dimethylbenzene", "1,3,5-trimethylbenzene",
    "1,3-dioxolan-2-one", "1,4-dimethylbenzene", "1,8-cineole", "1-heptanol",
    "1-hexanol", "1-methoxy-2-acetoxypropane", "1-methoxy2-propanol",
    "1-octanol", "1-pentanol", "2,3-dihydropyran", "2,6-dimethyl-4-heptanone",
    "2-butanol", "2-heptanone", "2-pentanol", "2-propanol", "4-methyl-2-pentanol",
    "4-oh-4-me-2-pentanone", "acetophenone", "acetylacetone", "anisole",
    "benzene", "benzylalcohol", "butanone", "ch2cl2", "chcl3", "chlorobenzene",
    "cyclohexane", "cyclohexanol", "cyclohexanone", "cyclopentanone",
    "di-n-butylether", "diethylcarbonate", "diethyleneglycol",
    "diethyleneglycolmonobutylether", "dimethylformamide", "dimethylsulfoxide",
    "dipentene", "diphenylether", "dodecane", "ethanol", "ethylacetate",
    "glycol", "gvl", "hexane", "isoamylacetate", "isophorone", "isopropylamine",
    "methanol", "methylacetate", "n-butylacetate", "n-heptane", "n-hexylacetate",
    "n-pentylacetate", "octane", "propanol", "propanone", "propylenecarbonate",
    "propyleneglycol", "tert-butanol", "tetrahydrothiophene-1,1-dioxide",
    "tetralin", "thf", "thp", "toluene", "triethylamine", "triethyleneglycol",
))


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return a read-only connection isolated to the calling thread."""
    connection = getattr(_LOCAL, "connection", None)
    if connection is None:
        connection = duckdb.connect(str(_ASSET), read_only=True)
        # Shadow the raw table only in this connection: the asset and its
        # recorded rejection reason remain unchanged.
        catalog = _ASSET.stem.replace('"', '""')
        connection.execute(
            "CREATE TEMP VIEW solubility_grid AS "
            "SELECT * REPLACE ("
            "(is_valid OR invalid_reason = 'exact_100_artifact') AS is_valid"
            ") FROM "
            f'"{catalog}".main.solubility_grid'
        )
        _LOCAL.connection = connection
    return connection


@lru_cache(maxsize=1)
def _grid_pairs() -> dict[tuple[str, str], dict]:
    """Load the unified source grid, retaining filtered-point dispositions."""
    cursor = get_connection().execute(
        "SELECT polymer, solvent, temperature_c, solubility_pct, is_valid, "
        "invalid_reason, source_table FROM solubility_grid "
        "ORDER BY polymer, solvent, temperature_c"
    )
    lookup: dict[tuple[str, str], dict] = {}
    for (
        polymer, solvent, temperature_c, solubility_pct, is_valid,
        invalid_reason, source_table,
    ) in cursor.fetchall():
        pair = (str(polymer).strip().upper(), str(solvent).strip().lower())
        record = lookup.setdefault(pair, {
            "point_count": 0,
            "valid_points": [],
            "filtered_reasons": {},
            "source_tables": set(),
        })
        record["point_count"] += 1
        record["source_tables"].add(str(source_table))
        if is_valid:
            record["valid_points"].append((
                float(temperature_c), float(solubility_pct),
            ))
        else:
            reason = str(invalid_reason or "invalid")
            record["filtered_reasons"][reason] = (
                int(record["filtered_reasons"].get(reason, 0)) + 1
            )
    for record in lookup.values():
        record["valid_points"] = tuple(sorted(record["valid_points"]))
        record["source_tables"] = tuple(sorted(record["source_tables"]))
    return lookup


@lru_cache(maxsize=1)
def _aliases() -> dict[str, dict]:
    cursor = get_connection().execute("SELECT * FROM solvent_aliases")
    columns = [item[0] for item in cursor.description]
    return {
        row[0].strip().lower(): dict(zip(columns, row))
        for row in cursor.fetchall()
    }


_TERMINAL_COMPOSITE_LABEL = re.compile(r"^\s*(.+?)\s*\(([^()]{1,24})\)\s*$")


def _resolve_registered_composite_identity(
    name: str, resolve_atom: Callable[[str], Optional[str]],
) -> Optional[str]:
    """Accept ``Name (ACRONYM)`` only when both labels resolve identically."""
    match = _TERMINAL_COMPOSITE_LABEL.fullmatch(str(name))
    if not match:
        return None
    base = resolve_atom(match.group(1))
    acronym = resolve_atom(match.group(2))
    return base if base is not None and base == acronym else None


@lru_cache(maxsize=1)
def _usable_grid_pairs() -> frozenset[tuple[str, str]]:
    """Pairs holding at least one valid stored value. This is the roster.

    v12: what the system says it has is what the grid actually holds. v11
    derived both rosters from ``solubility_coefficients``, so measured grid
    pairs missing from that table were invisible — which is why PU and 205
    solvents could not be reached.
    """
    return frozenset(
        pair for pair, record in _grid_pairs().items()
        if record["valid_points"]
    )


@lru_cache(maxsize=1)
def _available_polymers() -> frozenset[str]:
    return frozenset(polymer for polymer, _ in _usable_grid_pairs())


def get_available_polymers() -> set[str]:
    return set(_available_polymers())


@lru_cache(maxsize=1)
def _available_solvents() -> frozenset[str]:
    return frozenset(solvent for _, solvent in _usable_grid_pairs())


def parse_solvent_scope_token(value: object) -> str:
    """Return ``common`` or ``all``. Raise ``ValueError`` on any other token."""
    if isinstance(value, str):
        token = value.strip().casefold()
        if token in {"common", "all"}:
            return token
    raise ValueError("solvent_scope must be common or all")


def resolve_solvent_scope() -> tuple[str, str]:
    """Precedence: query override → session default → built-in ``all``."""
    query = _QUERY_SOLVENT_SCOPE.get()
    if query is not None:
        return query
    from .session import current_tool_session

    record = current_tool_session()
    stored = record.get("solvent_scope") if isinstance(record, dict) else None
    if isinstance(stored, dict):
        token = stored.get("scope")
        if token in {"common", "all"}:
            return str(token), "session_default"
    return "all", "built_in"


@contextmanager
def bind_query_solvent_scope(query_value: object | None) -> Iterator[None]:
    """Apply a tool-kwarg override for one call. ``None`` leaves session/built-in."""
    if query_value is None:
        yield
        return
    scope = parse_solvent_scope_token(query_value)
    token = _QUERY_SOLVENT_SCOPE.set((scope, "query"))
    try:
        yield
    finally:
        _QUERY_SOLVENT_SCOPE.reset(token)


def active_solvent_universe() -> set[str]:
    """The only intersection of the 990-solvent asset roster with the common set."""
    roster = set(_available_solvents())
    scope, _origin = resolve_solvent_scope()
    if scope == "all":
        return roster
    return roster & COMMON_INTERP_KEYS


def solvent_scope_stamp() -> dict[str, object]:
    scope, origin = resolve_solvent_scope()
    return {
        "solvent_scope": scope,
        "solvent_scope_origin": origin,
        "solvent_scope_n": len(active_solvent_universe()),
    }


def solvents_outside_active_scope(resolved: Sequence[str]) -> list[str]:
    """Return interp keys that resolve in the 990 but are not in the active universe."""
    active = active_solvent_universe()
    return [
        name for name in dict.fromkeys(resolved)
        if name not in active
    ]


def get_available_solvents() -> set[str]:
    return active_solvent_universe()


def resolve_polymer_identity(name: str) -> Optional[str]:
    """Resolve a user label independently of any scientific data source."""
    return POLYMER_ALIASES.get(polymer_label_key(name))


def polymer_identity_labels(name: str) -> tuple[str, ...]:
    """Return canonical and alias labels for one resolved polymer identity."""
    identity = resolve_polymer_identity(name)
    return _identity_aliases(identity) if identity else ()


def hsp_family_for_polymer_query(name: str) -> Optional[str]:
    """Return the HSP family selected by a centrally resolved family noun."""
    identity = resolve_polymer_identity(name)
    if not identity:
        return None
    family = POLYMER_IDENTITIES[identity].get("hsp_family")
    return str(family) if family else None


def expand_polymer_identity(
    name: str, known: Optional[set[str]] = None,
) -> tuple[str, ...]:
    """Return every stored-grid identity represented by a feed label."""
    candidates = known or _available_polymers()
    identity = resolve_polymer_identity(name)
    if identity is None:
        return ()
    record = POLYMER_IDENTITIES[identity]
    members = tuple(str(item) for item in record.get("thermodynamic_members", (identity,)))
    return tuple(item for item in members if item in candidates)


def resolve_polymer(name: str, known: Optional[set[str]] = None) -> Optional[str]:
    candidates = known or _available_polymers()
    identity = resolve_polymer_identity(name)
    if identity in candidates:
        return identity
    expanded = expand_polymer_identity(name, candidates)
    if len(expanded) == 1:
        return expanded[0]
    default = POLYMER_IDENTITIES.get(identity or "", {}).get("point_default")
    return str(default) if default in candidates else None


def _resolve_solvent_atom(name: str, candidates: set[str]) -> Optional[str]:
    """Resolve one exact solvent identity or registered alias without guessing."""
    normalized = str(name).strip().lower()
    if normalized in _ambiguous_property_identity_labels():
        return None
    resolved: Optional[str] = None
    if normalized in candidates:
        resolved = normalized
    else:
        source_alias = SOURCE_SOLVENT_ALIASES.get(normalized)
        if source_alias in candidates:
            resolved = source_alias
        else:
            registered_alias = _aliases().get(normalized, {}).get("interp_key")
            if registered_alias in candidates:
                resolved = str(registered_alias)
    if resolved is None:
        return None

    # A property-table label and a registered model alias must identify the
    # same molecule.  This rejects stale/conflicting aliases instead of
    # returning a valid number for a different CAS identity.
    supplied_identity = identify_known_solvent(normalized)
    if supplied_identity is None:
        return resolved
    resolved_identity = identify_known_solvent(resolved)
    if (
        resolved_identity is None
        or _property_identity_key(supplied_identity)
        != _property_identity_key(resolved_identity)
    ):
        return None
    return resolved


def resolve_solvent(name: str, known: Optional[set[str]] = None) -> Optional[str]:
    candidates = known or _available_solvents()
    direct = _resolve_solvent_atom(name, candidates)
    if direct is not None:
        return direct
    return _resolve_registered_composite_identity(
        name, lambda label: _resolve_solvent_atom(label, candidates),
    )


def resolve_names(polymer: str, solvent: str) -> tuple[Optional[str], Optional[str]]:
    return resolve_polymer(polymer), resolve_solvent(solvent)


def canonical_solvent_name(name: str) -> str:
    """Return the user-facing property-database name for a grid solvent."""
    resolved = resolve_solvent(name) or name.strip().lower()
    alias = _aliases().get(resolved, {})
    return str(alias.get("property_name") or resolved.title())


def solvent_identity_labels(name: str) -> tuple[str, ...]:
    """Return every registered label for one grid solvent identity."""
    resolved = resolve_solvent(name)
    if not resolved:
        return ()
    labels = {resolved, canonical_solvent_name(resolved)}
    labels.update(alias for alias, row in _aliases().items() if row.get("interp_key") == resolved)
    return tuple(sorted(labels, key=lambda item: (-len(item), item.casefold())))


def _unavailable_grid_result(
    temperature_c: float,
    pair: dict,
    reason: str,
) -> dict:
    return {
        "available": False,
        "solubility_pct": None,
        "temperature_c": float(temperature_c),
        "unavailable_reason": reason,
        "grid_valid_point_count": len(pair["valid_points"]),
        "grid_filtered_point_count": sum(pair["filtered_reasons"].values()),
        "grid_filtered_reasons": dict(pair["filtered_reasons"]),
        "grid_source_tables": list(pair["source_tables"]),
    }


def _pair_temperature_coverage(
    grid_pair: dict | None,
) -> dict[str, list[float]]:
    """Expose the retained-grid bounds for one pair."""
    coverage: dict[str, list[float]] = {}
    valid_points = list((grid_pair or {}).get("valid_points") or [])
    if valid_points:
        coverage["source_grid_temperature_range_c"] = [
            float(valid_points[0][0]), float(valid_points[-1][0]),
        ]
    return coverage


@lru_cache(maxsize=524_288)
def get_solubility_result(
    polymer: str,
    solvent: str,
    temperature_c: float,
) -> dict:
    """Return one measured grid value, or a typed refusal.

    A temperature between stored nodes is refused rather than estimated.
    """
    requested_temperature_c = float(temperature_c)
    resolved_polymer, resolved_solvent = resolve_names(polymer, solvent)
    if not resolved_polymer or not resolved_solvent:
        return {
            "available": False,
            "solubility_pct": None,
            "temperature_c": requested_temperature_c,
            "unavailable_reason": "pair_not_found",
        }
    pair_key = (resolved_polymer, resolved_solvent)

    grid_pair = _grid_pairs().get(pair_key)
    coverage = _pair_temperature_coverage(grid_pair)
    if grid_pair is not None:
        valid_points: tuple[tuple[float, float], ...] = grid_pair["valid_points"]
        if not valid_points:
            return {
                **_unavailable_grid_result(
                    requested_temperature_c, grid_pair,
                    "all_grid_points_filtered",
                ),
                **coverage,
            }
        grid_min_c, grid_max_c = valid_points[0][0], valid_points[-1][0]
        if grid_min_c <= requested_temperature_c <= grid_max_c:
            exact = next((
                point for point in valid_points
                if math.isclose(
                    point[0], requested_temperature_c,
                    rel_tol=0.0, abs_tol=1e-9,
                )
            ), None)
            if exact is not None:
                return {
                    "available": True,
                    "solubility_pct": exact[1],
                    "temperature_c": requested_temperature_c,
                    "source_temperatures_c": [exact[0]],
                    "grid_valid_point_count": len(valid_points),
                    "grid_filtered_point_count": sum(
                        grid_pair["filtered_reasons"].values()
                    ),
                    "grid_source_tables": list(grid_pair["source_tables"]),
                    **coverage,
                }
            # v12: a temperature between two grid nodes is not a question this
            # engine answers. v11 interpolated the bracket here.

    if grid_pair is not None:
        grid_range = coverage.get("source_grid_temperature_range_c")
        above_grid = bool(
            grid_range and requested_temperature_c > grid_range[1]
        )
        reason = (
            "above_retained_grid_range"
            if above_grid else "temperature_not_a_grid_node"
        )
        return {
            **_unavailable_grid_result(
                requested_temperature_c, grid_pair, reason,
            ),
            **coverage,
        }
    return {
        "available": False,
        "solubility_pct": None,
        "temperature_c": requested_temperature_c,
        "unavailable_reason": "pair_not_found",
        **coverage,
    }


def has_solubility_pair(polymer: str, solvent: str) -> bool:
    resolved_polymer, resolved_solvent = resolve_names(polymer, solvent)
    if not resolved_polymer or not resolved_solvent:
        return False
    return (resolved_polymer, resolved_solvent) in _usable_grid_pairs()


@lru_cache(maxsize=262_144)
def get_solubility(
    polymer: str,
    solvent: str,
    temperature_c: float,
) -> Optional[float]:
    result = get_solubility_result(polymer, solvent, temperature_c)
    value = result.get("solubility_pct")
    return float(value) if result.get("available") and value is not None else None


@lru_cache(maxsize=1)
def _grid_nodes() -> tuple[float, ...]:
    """The temperatures the asset actually stores. Read once, never asserted."""
    return tuple(float(row[0]) for row in get_connection().execute(
        "SELECT DISTINCT temperature_c FROM solubility_grid ORDER BY 1"
    ).fetchall())


def _nearest_nodes(temperature: float) -> list[float]:
    """Return the stored grid nodes immediately bracketing one temperature."""
    nodes = _grid_nodes()
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


def _nodes_within(start: float, end: float, step: float, strict: bool) -> list[float]:
    """Return the stored grid nodes inside the requested bounds.

    v12: a sweep may only visit temperatures the grid holds. v11 anchored the
    sweep to ``start`` and stepped from there, so a bound one degree off a node
    produced a list of temperatures with no stored value — every point
    interpolated. With interpolation gone those points return nothing, and a
    screen that evaluated nothing reported "no candidate qualified" rather than
    "nothing was evaluable".

    Snapping is inward only: every temperature returned is a node with a stored
    value, and none lies outside the requested bounds. An empty result is the
    caller's cue to refuse, which every call site already does.
    """
    if end < start:
        return []
    lower, upper = float(start), float(end)
    inside = [
        node for node in _grid_nodes()
        if node >= lower - 1e-9
        and (node < upper - 1e-9 if strict else node <= upper + 1e-9)
    ]
    stride = max(1, int(round(float(step) / 5.0)))
    return inside[::stride]


def _temperature_grid(start: float, end: float, step: float, strict: bool = False) -> list[float]:
    """Grid nodes inside the bounds. See _nodes_within.

    The single choke point for every range sweep in the tree — this tool, the
    two safety screens, and the two separation screens. Bounding it to stored
    nodes is what stops a screen from evaluating nothing and reporting that
    nothing qualified.
    """
    return _nodes_within(start, end, step, strict)


def get_solubility_curve(
    polymer: str,
    solvent: str,
    t_start_c: float = 25.0,
    t_end_c: float = 160.0,
    t_step_c: float = 5.0,
) -> list[dict]:
    resolved_polymer, resolved_solvent = resolve_names(polymer, solvent)
    if not resolved_polymer or not resolved_solvent:
        return []
    temperatures = _temperature_grid(t_start_c, t_end_c, t_step_c)
    rows = []
    for temperature in temperatures:
        result = get_solubility_result(
            resolved_polymer, resolved_solvent, temperature,
        )
        if not result.get("available"):
            continue
        rows.append({
            "temperature": temperature,
            "solubility": float(result["solubility_pct"]),
            "source_grid_temperature_range_c": result.get(
                "source_grid_temperature_range_c",
            ),
        })
    return rows


def get_available_solvents_for_polymer(polymer: str) -> set[str]:
    resolved = resolve_polymer(polymer)
    if not resolved:
        return set()
    active = active_solvent_universe()
    return {
        solvent for (candidate, solvent) in _usable_grid_pairs()
        if candidate == resolved and solvent in active
    }


def get_all_solvents_selectivity(
    target: str, others: list[str], temperature_c: float
) -> list[dict]:
    target_name = resolve_polymer(target)
    other_names = [resolve_polymer(other) for other in others]
    if not target_name or any(other is None for other in other_names):
        return []
    results = []
    for solvent in sorted(get_available_solvents()):
        target_result = get_solubility_result(
            target_name, solvent, temperature_c,
        )
        other_results = [
            get_solubility_result(other, solvent, temperature_c)
            for other in other_names
        ]
        target_value = target_result.get("solubility_pct")
        other_values = [result.get("solubility_pct") for result in other_results]
        if target_value is None or target_value <= 0 or any(value is None for value in other_values):
            continue
        maximum = max(other_values, default=0.0)
        results.append({
            "solvent": solvent,
            "selectivity": target_value - maximum,
            "target_sol": target_value,
            "max_other_sol": maximum,
            "source_grid_temperature_range_by_polymer": {
                target_name: target_result.get(
                    "source_grid_temperature_range_c",
                ),
                **{
                    str(other): result.get(
                        "source_grid_temperature_range_c",
                    )
                    for other, result in zip(other_names, other_results)
                },
            },
        })
    return sorted(results, key=lambda item: (
        -item["selectivity"], item["solvent"],
    ))


def _property_identity_key(row: dict) -> tuple[str, str]:
    """Return the strongest locally available identity key for one row."""
    cas_number = str(row.get("cas_number") or "").strip().lower()
    if cas_number:
        return "cas_number", cas_number
    cosmobase_name = str(row.get("cosmobase_name") or "").strip().lower()
    if cosmobase_name:
        return "cosmobase_name", cosmobase_name
    return "solvent_name", str(row.get("solvent_name") or "").strip().lower()


@lru_cache(maxsize=1)
def _property_identity_index() -> tuple[dict[str, dict], frozenset[str]]:
    """Index exact property identities and expose labels with conflicting CAS."""
    cursor = get_connection().execute(
        "SELECT * FROM solvent_data ORDER BY source_row"
    )
    columns = [item[0] for item in cursor.description]
    candidates: dict[str, dict[tuple[str, str], dict]] = {}
    for values in cursor.fetchall():
        row = dict(zip(columns, values))
        identity_key = _property_identity_key(row)
        for value in (
            row.get("solvent_name"),
            row.get("cosmobase_name"),
            row.get("cas_number"),
        ):
            key = str(value or "").strip().lower()
            if key:
                candidates.setdefault(key, {}).setdefault(identity_key, row)
    ambiguous = frozenset(
        label for label, rows in candidates.items() if len(rows) > 1
    )
    unambiguous = {
        label: next(iter(rows.values()))
        for label, rows in candidates.items()
        if len(rows) == 1
    }
    return unambiguous, ambiguous


def _property_identity_rows() -> dict[str, dict]:
    """Return exact, unambiguous property identities without model claims."""
    return _property_identity_index()[0]


def _ambiguous_property_identity_labels() -> frozenset[str]:
    return _property_identity_index()[1]


def identify_known_solvent(name: str) -> Optional[dict]:
    """Return an exact unambiguous identity independently of model scope."""
    normalized = str(name).strip().lower()
    if normalized in _ambiguous_property_identity_labels():
        return None
    alias = _aliases().get(normalized, {})
    candidates = (
        normalized,
        alias.get("property_name"),
        alias.get("bp_db_key"),
        alias.get("cas_number"),
    )
    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if key in _ambiguous_property_identity_labels():
            continue
        row = _property_identity_rows().get(key)
        if row is not None:
            return {
                "solvent_name": row.get("solvent_name"),
                "cosmobase_name": row.get("cosmobase_name"),
                "cas_number": row.get("cas_number"),
            }
    return None


def get_fitted_solvent_status(name: str) -> str:
    """Classify stored-grid availability independently from chemical identity."""
    return "unavailable" if resolve_solvent(name) is None else "available"


@lru_cache(maxsize=1)
def get_property_solvent_record_count() -> int:
    """Return property-record cardinality, which is not a model denominator."""
    return int(
        get_connection().execute("SELECT COUNT(*) FROM solvent_data").fetchone()[0]
    )


@lru_cache(maxsize=1)
def _solvent_admission_rows() -> dict[str, dict]:
    """Return locally admitted identity framing, or an empty pre-admission set.

    F5a intentionally lands before the extended rows.  Keeping the optional
    table read here makes the guard active for the pre-admission asset and lets
    F5b populate it without changing candidate-generation code.
    """
    try:
        cursor = get_connection().execute(
            "SELECT * FROM solvent_admission_data ORDER BY interp_key"
        )
    except duckdb.Error:
        return {}
    columns = [item[0] for item in cursor.description]
    return {
        str(row[0]).strip().lower(): dict(zip(columns, row))
        for row in cursor.fetchall()
    }


def get_solvent_admission_record(solvent: str) -> dict[str, object]:
    """Return the governed admission record behind one grid solvent label."""
    resolved = resolve_solvent(solvent) or str(solvent).strip().lower()
    return dict(_solvent_admission_rows().get(resolved, {}))


def get_solvent_hazard_framing(solvent: str) -> dict[str, object]:
    """Return a total GHS decision for a thermodynamic candidate.

    Missing signal words are represented as ``unknown`` rather than as an
    absent or implicitly safe value.  Danger and unknown both carry an
    explicit framing requirement for downstream prose.
    """
    resolved = resolve_solvent(solvent) or str(solvent).strip().lower()
    row = _solvent_admission_rows().get(resolved, {})
    reported = str(row.get("ghs_signal_word") or "").strip()
    signal = reported if reported else "unknown"
    return {"ghs_signal_word": signal}


@lru_cache(maxsize=1)
def get_solvent_catalog_provenance() -> dict[str, object]:
    """Describe the deterministic admission guards beside every discovery result."""
    try:
        metadata = dict(get_connection().execute(
            "SELECT key, value FROM thermodynamic_metadata "
            "WHERE key LIKE 'extended_%'"
        ).fetchall())
    except duckdb.Error:
        metadata = {}
    admitted = int(metadata.get("extended_admitted_solvent_count", "0"))
    result: dict[str, object] = {
        "fitted_solvent_count": len(_available_solvents()),
        "extended_admitted_solvent_count": admitted,
        "admission_guards": {
            "boiling_point_required": True,
            "minimum_boiling_point_c": EXTENDED_ADMISSION_MIN_BOILING_POINT_C,
            "cas_required": True,
            "hazard_lookup": "resolved",
            "ghs_unknown_is_safe": False,
        },
    }
    return result


def _property(solvent: str, field: str) -> Optional[float]:
    normalized = solvent.strip().lower()
    if normalized in _ambiguous_property_identity_labels():
        return None
    resolved = resolve_solvent(solvent)
    alias = _aliases().get(normalized, {}) or _aliases().get(resolved or "", {})
    candidates = (
        normalized,
        resolved,
        alias.get("interp_key"),
        alias.get("property_name"),
        alias.get("bp_db_key"),
    )
    for candidate in candidates:
        row = (
            _property_identity_rows().get(str(candidate).strip().lower())
            if candidate else None
        )
        value = row.get(field) if row else None
        if value is not None and not math.isnan(float(value)):
            return float(value)
    if field == "boiling_point_c":
        admission = _solvent_admission_rows().get(resolved or normalized, {})
        value = admission.get("boiling_point_c")
        if value is not None and not math.isnan(float(value)):
            return float(value)
    return None


def get_boiling_point(solvent: str) -> Optional[float]:
    return _property(solvent, "boiling_point_c")


# --- tools: Model-selectable deterministic thermodynamic tools.


_STRONG_OVERLAP_RATIO = 0.70
MATERIAL_UNDER_COVERAGE_RATIO = 0.90
_TOOL = "solubility_query"


def _with_solvent_scope(fn: Callable[..., str]) -> Callable[..., str]:
    """Bind optional ``solvent_scope`` for one tool call. Does not swallow ``_InputError``."""

    @wraps(fn)
    def wrapped(*args: Any, solvent_scope: Any = None, **kwargs: Any) -> str:
        try:
            bound = bind_query_solvent_scope(solvent_scope)
            bound.__enter__()
        except ValueError:
            return tool_error(
                fn.__name__,
                "solvent_scope must be common or all.",
                error_code="invalid_solvent_scope",
                requested=solvent_scope,
            )
        try:
            return fn(*args, **kwargs)
        finally:
            bound.__exit__(None, None, None)

    signature = inspect.signature(fn)
    if "solvent_scope" not in signature.parameters:
        extra = inspect.Parameter(
            "solvent_scope",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=Optional[str],
        )
        wrapped.__signature__ = signature.replace(
            parameters=(*signature.parameters.values(), extra),
        )
    return wrapped


def _refuse_solvents_out_of_scope(tool: str, resolved: Sequence[str]) -> str | None:
    outside = solvents_outside_active_scope(list(resolved))
    if not outside:
        return None
    return tool_error(
        tool,
        "Solvent(s) not in the active solvent scope: " + ", ".join(outside),
        error_code="solvent_not_in_scope",
        solvents=outside,
        **solvent_scope_stamp(),
    )


_HANSEN_ASSET = Path(str(files("dissolve").joinpath("data/hansen.duckdb")))
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


def _polymer_ambiguity_detail(
    value: object, field: str,
) -> dict[str, Any] | None:
    """Describe a known family only when a scalar boundary needs one member."""
    supplied = str(value or "").strip()
    members = expand_polymer_identity(supplied)
    if len(members) <= 1:
        return None
    return {
        "supplied_polymer": supplied,
        "polymer_field": field,
        "polymer_members": list(members),
    }


def _polymer_ambiguity_error(
    tool: str, value: object, field: str,
) -> str | None:
    """Return the shared scalar-family refusal, or None for a point identity."""
    detail = _polymer_ambiguity_detail(value, field)
    if detail is None:
        return None
    return tool_error(
        tool,
        f"{field} names a polymer family; choose one member.",
        error_code="ambiguous_polymer",
        **detail,
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


def _solvent_resolution_detail(solvent_name: str) -> dict[str, Any]:
    """Describe one unresolved model identity without collapsing its cause."""
    identity = identify_known_solvent(solvent_name)
    model_status = get_fitted_solvent_status(solvent_name)
    identity_status = "not_found" if identity is None else "known_without_grid_values"
    return {
        "solvent_name": solvent_name,
        "solvent_identity_status": identity_status,
        "fitted_model_status": model_status,
        "known_solvent_identity": identity,
    }


def _solvent_resolution_error(tool: str, solvent_name: str) -> str:
    """Route one unresolved name through the shared plural refusal."""
    return _solvent_resolution_errors(tool, (solvent_name,))


def _solvent_resolution_errors(tool: str, solvent_names: Sequence[str]) -> str:
    """Return per-name identity evidence for a plural unresolved request."""
    unsupported = list(dict.fromkeys(str(item) for item in solvent_names))
    details = [_solvent_resolution_detail(item) for item in unsupported]
    single = details[0] if len(details) == 1 else {}
    return tool_error(
        tool,
        "Unsupported solvent(s): " + ", ".join(unsupported),
        error_code="unknown_solvents",
        unsupported_solvents=unsupported,
        unsupported_solvent_details=details,
        **single,
        available_count=len(_available_solvents()),
    )


def _pair_result(
    polymer: str,
    solvent: str,
    temperature_c: float,
) -> Optional[dict]:
    evidence = get_solubility_result(polymer, solvent, temperature_c)
    value = evidence.get("solubility_pct")
    if not evidence.get("available") or value is None:
        return None
    return {
        "polymer": polymer,
        "solvent": canonical_solvent_name(solvent),
        "solvent_data_key": solvent,
        "temperature_c": float(temperature_c),
        "solubility_pct": float(value),
        "source_temperatures_c": evidence.get("source_temperatures_c"),
        "source_grid_temperature_range_c": evidence.get(
            "source_grid_temperature_range_c",
        ),
        "is_clipped": bool(float(value) >= 100.0),
        "clip_limit_wt_percent": 100.0,
        **get_solvent_hazard_framing(solvent),
    }


def _single_polymer_catalog_provenance(
    screened_count: int,
) -> dict[str, object]:
    """Qualify catalog size with the count evaluated for one polymer."""
    provenance = dict(get_solvent_catalog_provenance())
    count = int(screened_count)
    assert 0 <= count <= int(provenance["fitted_solvent_count"])
    provenance["fitted_solvents_for_polymer"] = count
    return provenance


def _screened_solvent_counts(
    polymers: Sequence[str], temperature_c: float,
) -> dict[str, int]:
    """Count catalog solvents accepted by the actual pair-screen kernel."""
    solvents = sorted(get_available_solvents())
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
    provenance = dict(get_solvent_catalog_provenance())
    catalog_count = int(solvent_scope_stamp()["solvent_scope_n"])
    under_covered = {
        polymer: count for polymer, count in counts.items()
        if count < MATERIAL_UNDER_COVERAGE_RATIO * catalog_count
    }
    if under_covered:
        provenance["under_covered_polymers"] = under_covered
    return provenance


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
    expander: Callable[[str], Sequence[str]] | None = None,
    unresolved_detail: Callable[[str], dict[str, Any]] | None = None,
    available_count: int | None = None,
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
        canonicals = list(expander(value)) if expander is not None else []
        if expander is None:
            canonical = resolver(value)
            canonicals = [] if canonical is None else [canonical]
        if not canonicals:
            unsupported.append(value)
            continue
        for canonical in canonicals:
            if canonical in seen:
                duplicate_count += 1
                continue
            seen.add(canonical)
            resolved.append(canonical)
    if unsupported:
        detail: dict[str, Any] = {
            "field": axis,
            "unsupported": unsupported,
            "unsupported_count": len(unsupported),
            "available_count": (
                len(universe) if available_count is None else int(available_count)
            ),
        }
        if unresolved_detail is not None:
            detail[f"unsupported_{axis[:-1]}_details"] = [
                unresolved_detail(item) for item in unsupported
            ]
        raise _InputError(
            f"unknown_{axis}",
            f"Unsupported {axis}: "
            + ", ".join(repr(item) for item in unsupported),
            **detail,
        )
    return resolved, False, duplicate_count


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
                    "nearest_nodes_c": _nearest_nodes(temperature),
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
    return get_connection().execute(
        "SELECT polymer, solvent, temperature_c, solubility_pct, is_valid, "
        "invalid_reason, source_table FROM solubility_grid WHERE "
        + " AND ".join(filters),
        parameters,
    ).fetchall()


def _fetch_red_source_rows(
    polymers: Sequence[str],
    solvents: Sequence[str],
) -> tuple[list[tuple[Any, ...]], dict[str, int]]:
    """Return every admitted HSP source-row pair without collapsing grades."""
    polymer_slots = ", ".join("?" for _ in polymers)
    solvent_slots = ", ".join("?" for _ in solvents)
    connection = duckdb.connect(str(_HANSEN_ASSET), read_only=True)
    try:
        rows = connection.execute(
            "SELECT pm.thermo_polymer, sm.grid_solvent, "
            "rg.hsp_polymer_row_id, rg.hsp_solvent_row_id, "
            "rg.hsp_polymer_name, rg.hsp_solvent_name, "
            "pm.route, sm.route, rg.red, rg.ra "
            "FROM red_grid rg "
            "JOIN polymer_identity_map pm "
            "ON pm.hsp_row_id = rg.hsp_polymer_row_id "
            "JOIN solvent_identity_map sm "
            "ON sm.hsp_name = rg.hsp_solvent_name "
            f"WHERE pm.thermo_polymer IN ({polymer_slots}) "
            f"AND sm.grid_solvent IN ({solvent_slots}) "
            "ORDER BY pm.thermo_polymer, sm.grid_solvent, "
            "rg.hsp_polymer_row_id, rg.hsp_solvent_row_id",
            [*polymers, *solvents],
        ).fetchall()
        polymer_map_rows, polymer_targets = connection.execute(
            "SELECT count(*), count(DISTINCT thermo_polymer) "
            "FROM polymer_identity_map"
        ).fetchone()
        solvent_map_names, solvent_targets = connection.execute(
            "SELECT count(*), count(DISTINCT grid_solvent) "
            "FROM solvent_identity_map"
        ).fetchone()
        matched_solvent_source_rows = connection.execute(
            "SELECT count(DISTINCT rg.hsp_solvent_row_id) "
            "FROM red_grid rg JOIN solvent_identity_map sm "
            "ON sm.hsp_name = rg.hsp_solvent_name"
        ).fetchone()[0]
    finally:
        connection.close()
    return rows, {
        "matched_hsp_polymer_source_row_count": int(polymer_map_rows),
        "total_hsp_polymer_source_row_count": 466,
        "thermodynamic_polymer_with_hsp_count": int(polymer_targets),
        "matched_hsp_solvent_source_row_count": int(matched_solvent_source_rows),
        "total_hsp_solvent_source_row_count": 1_180,
        "matched_hsp_solvent_name_count": int(solvent_map_names),
        "total_unique_hsp_solvent_name_count": 1_149,
        "grid_solvent_with_hsp_count": int(solvent_targets),
    }


@lru_cache(maxsize=1)
def _hsp_interaction_radii() -> dict[int, float]:
    """Read R0 from its existing component-record home, never the RED asset."""
    # Imported lazily because analysis uses the shared ambiguity helper from
    # this module. At tool-call time both engines are fully initialized.
    from .analysis import asset_payload

    records = asset_payload()["hsp"]["polymer_source_records"]
    return {
        int(record["source_record_id"]): float(record["interaction_radius"])
        for record in records
        if record.get("interaction_radius") is not None
    }


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
            int(row.get("hsp_polymer_row_id", -1)),
            int(row.get("hsp_solvent_row_id", -1)),
        )

    rows.sort(key=key)


def _display(rows: Sequence[dict[str, Any]]) -> str:
    if not rows:
        return "No solubility cells qualified."

    def value(item: object) -> str:
        return "—" if item is None else f"{float(item):.6g}"

    if "red" in rows[0]:
        return _table(
            ("Polymer", "HSP polymer", "HSP row", "Solvent", "HSP solvent row",
             "T (C)", "Solubility (wt%)", "RED", "Ra", "R0"),
            tuple(
                (
                    row["polymer"], row["hsp_polymer_name"],
                    row["hsp_polymer_row_id"], row["solvent_name"],
                    row["hsp_solvent_row_id"], value(row["temperature_c"]),
                    value(row["solubility_pct"]), value(row["red"]),
                    value(row["ra"]), value(row["r0"]),
                )
                for row in rows
            ),
        )
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


@_with_solvent_scope
def solubility_query(
    polymers: list[str] | None = None,
    solvents: list[str] | None = None,
    temperatures: list[float] | None = None,
    min_solubility_pct: float | None = None,
    max_solubility_pct: float | None = None,
    min_red: float | None = None,
    max_red: float | None = None,
    require_atmospheric: bool = False,
    min_boiling_point_margin_c: float | None = None,
    order_by: str = "solubility",
    descending: bool = True,
    top_k: int = 50,
    offset: int = 0,
) -> str:
    """Query measured solubility cells across any combination of grid axes.

    Supplying ``min_red`` or ``max_red`` activates the Hansen join. Each
    qualifying HSP source-record pair is published separately; omitting both
    bounds leaves the thermodynamic response unchanged.
    """
    try:
        minimum = _finite_optional(min_solubility_pct, "min_solubility_pct")
        maximum = _finite_optional(max_solubility_pct, "max_solubility_pct")
        minimum_red = _finite_optional(min_red, "min_red")
        maximum_red = _finite_optional(max_red, "max_red")
        red_active = minimum_red is not None or maximum_red is not None
        minimum_margin = _finite_optional(
            min_boiling_point_margin_c, "min_boiling_point_margin_c",
        )
        if minimum is not None and maximum is not None and minimum > maximum:
            raise _InputError(
                "invalid_solubility_range",
                "min_solubility_pct cannot exceed max_solubility_pct.",
                min_solubility_pct=minimum, max_solubility_pct=maximum,
            )
        if (
            minimum_red is not None
            and maximum_red is not None
            and minimum_red > maximum_red
        ):
            raise _InputError(
                "invalid_red_range",
                "min_red cannot exceed max_red.",
                min_red=minimum_red, max_red=maximum_red,
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

        polymer_universe = sorted(get_available_polymers())
        solvent_universe = sorted(get_available_solvents())
        temperature_universe = list(_grid_nodes())
        selected_polymers, all_polymers, duplicate_polymers = _name_axis(
            polymers,
            axis="polymers",
            universe=polymer_universe,
            resolver=resolve_polymer,
            expander=expand_polymer_identity,
        )
        selected_solvents, all_solvents, duplicate_solvents = _name_axis(
            solvents,
            axis="solvents",
            universe=solvent_universe,
            resolver=resolve_solvent,
            unresolved_detail=_solvent_resolution_detail,
            available_count=len(_available_solvents()),
        )
        if not all_solvents:
            scoped = _refuse_solvents_out_of_scope(_TOOL, selected_solvents)
            if scoped is not None:
                return scoped
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
            boiling_point = get_boiling_point(solvent_key)
            hazard = get_solvent_hazard_framing(solvent_key)
            solvent_context[solvent_key] = (
                canonical_solvent_name(solvent_key),
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

    red_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    hsp_catalog_coverage: dict[str, int] = {}
    if red_active:
        interaction_radii = _hsp_interaction_radii()
        red_source_rows, hsp_catalog_coverage = _fetch_red_source_rows(
            selected_polymers, selected_solvents,
        )
        for (
            polymer, solvent, hsp_polymer_row_id, hsp_solvent_row_id,
            hsp_polymer_name, hsp_solvent_name, polymer_route, solvent_route,
            red, ra,
        ) in red_source_rows:
            polymer_source_id = int(hsp_polymer_row_id)
            if polymer_source_id not in interaction_radii:
                raise RuntimeError(
                    f"HSP polymer row {polymer_source_id} has no interaction radius"
                )
            red_by_pair.setdefault((str(polymer), str(solvent)), []).append({
                "red": float(red),
                "ra": float(ra),
                "r0": interaction_radii[polymer_source_id],
                "hsp_source_id": {
                    "polymer_row_id": polymer_source_id,
                    "solvent_row_id": int(hsp_solvent_row_id),
                },
                "hsp_polymer_row_id": polymer_source_id,
                "hsp_solvent_row_id": int(hsp_solvent_row_id),
                "hsp_polymer_name": str(hsp_polymer_name),
                "hsp_solvent_name": str(hsp_solvent_name),
                "hsp_polymer_identity_route": str(polymer_route),
                "hsp_solvent_identity_route": str(solvent_route),
            })

    qualifying_red_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    if red_active:
        for pair, candidates in red_by_pair.items():
            qualifying_red_by_pair[pair] = [
                candidate for candidate in candidates
                if (
                    minimum_red is None or candidate["red"] >= minimum_red
                ) and (
                    maximum_red is None or candidate["red"] <= maximum_red
                )
            ]

    exclusion_counts = {
        "below_min_solubility_pct": 0,
        "above_max_solubility_pct": 0,
        "failed_atmospheric_requirement": 0,
        "unknown_boiling_point_for_atmospheric_requirement": 0,
        "below_min_boiling_point_margin_c": 0,
        "unknown_boiling_point_for_margin_requirement": 0,
        "failed_any_constraint": 0,
    }
    if red_active:
        exclusion_counts.update({
            "thermodynamic_cells_without_red": 0,
            "thermodynamic_cells_without_qualifying_red_source_record": 0,
            "red_source_record_pairs_below_min_red": 0,
            "red_source_record_pairs_above_max_red": 0,
        })
    qualified: list[dict[str, Any]] = []
    qualified_thermodynamic_cell_count = 0
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
        red_candidates: list[dict[str, Any]] = []
        qualifying_red_candidates: list[dict[str, Any]] = []
        if red_active:
            pair = (row["polymer"], row["solvent"])
            red_candidates = red_by_pair.get(pair, [])
            qualifying_red_candidates = qualifying_red_by_pair.get(pair, [])
            if not red_candidates:
                exclusion_counts["thermodynamic_cells_without_red"] += 1
                failed = True
            elif not qualifying_red_candidates:
                exclusion_counts[
                    "thermodynamic_cells_without_qualifying_red_source_record"
                ] += 1
                failed = True
            if minimum_red is not None:
                exclusion_counts["red_source_record_pairs_below_min_red"] += sum(
                    candidate["red"] < minimum_red for candidate in red_candidates
                )
            if maximum_red is not None:
                exclusion_counts["red_source_record_pairs_above_max_red"] += sum(
                    candidate["red"] > maximum_red for candidate in red_candidates
                )
        if failed:
            exclusion_counts["failed_any_constraint"] += 1
        elif red_active:
            qualified_thermodynamic_cell_count += 1
            qualified.extend(
                {**row, **candidate} for candidate in qualifying_red_candidates
            )
        else:
            qualified.append(row)

    total = len(qualified)
    if red_active:
        assert (
            qualified_thermodynamic_cell_count
            + exclusion_counts["failed_any_constraint"]
            == len(evaluable_rows)
        )
    else:
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

    constraints = {
        "min_solubility_pct": minimum,
        "max_solubility_pct": maximum,
        "require_atmospheric": require_atmospheric,
        "min_boiling_point_margin_c": minimum_margin,
    }
    red_response: dict[str, Any] = {}
    if red_active:
        constraints.update({"min_red": minimum_red, "max_red": maximum_red})
        solubility_pairs = {
            (row["polymer"], row["solvent"]) for row in evaluable_rows
        }
        solubility_solvents = {solvent for _, solvent in solubility_pairs}
        red_pairs = set(red_by_pair)
        red_solvents = {solvent for _, solvent in red_pairs}
        both_pairs = solubility_pairs & red_pairs
        both_solvents = {solvent for _, solvent in both_pairs}
        qualifying_red_pairs = {
            pair for pair, candidates in qualifying_red_by_pair.items() if candidates
        }
        qualifying_both_pairs = solubility_pairs & qualifying_red_pairs
        qualifying_both_solvents = {
            solvent for _, solvent in qualifying_both_pairs
        }
        result_solvents = {row["solvent"] for row in qualified}
        red_response = {
            "result_grain": "thermodynamic_cell_x_hsp_source_record_pair",
            "red_constraint_semantics": {
                "quantifier": "per_hsp_source_record",
                "bounds_are_inclusive": True,
                "distinct_solvent_inclusion_rule": (
                    "at_least_one_published_hsp_source_record_pair_satisfies_bounds"
                ),
                "scope_warning": (
                    "A qualifying solvent does not imply that every mapped HSP grade "
                    "satisfies the RED bounds."
                ),
            },
            "red_join_coverage": {
                "requested_solvent_count": len(selected_solvents),
                "solvents_with_red_count": len(red_solvents),
                "solvents_with_solubility_count": len(solubility_solvents),
                "solvents_with_both_count": len(both_solvents),
                "solvents_with_qualifying_red_and_solubility_count": len(
                    qualifying_both_solvents
                ),
                "requested_polymer_count": len(selected_polymers),
                "polymers_with_red_count": len({polymer for polymer, _ in red_pairs}),
                "requested_polymer_solvent_pair_count": (
                    len(selected_polymers) * len(selected_solvents)
                ),
                "pairs_with_red_count": len(red_pairs),
                "pairs_with_solubility_count": len(solubility_pairs),
                "pairs_with_both_count": len(both_pairs),
            },
            "hsp_catalog_coverage": {
                **hsp_catalog_coverage,
                "thermodynamic_polymer_count": len(polymer_universe),
                "grid_solvent_count": len(solvent_universe),
            },
            "qualified_thermodynamic_cell_count": qualified_thermodynamic_cell_count,
            "qualifying_joined_row_count": total,
            "distinct_qualifying_solvent_count": len(result_solvents),
            "red_unmatched_row_policy": "excluded_with_explicit_coverage_and_reason_counts",
            "red_unmatched_reason": "no_reviewed_hsp_identity_match",
            "hsp_units": {"red": "dimensionless", "ra": "MPa^0.5", "r0": "MPa^0.5"},
            "red_temperature_dependent": False,
            "red_evidence_class": "qualitative_hansen_compatibility",
            "exclusion_count_grains": {
                "thermodynamic_cells": [
                    "thermodynamic_cells_without_red",
                    "thermodynamic_cells_without_qualifying_red_source_record",
                    "failed_any_constraint",
                ],
                "hsp_source_record_pairs": [
                    "red_source_record_pairs_below_min_red",
                    "red_source_record_pairs_above_max_red",
                ],
            },
        }

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
        constraints=constraints,
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
        **red_response,
        **solvent_scope_stamp(),
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
        supplied_polymer = str(name).strip()
        ambiguity = _polymer_ambiguity_detail(
            supplied_polymer, "feed_mass_fractions",
        )
        if ambiguity is not None:
            raise _InputError(
                "ambiguous_polymer",
                "feed_mass_fractions names a polymer family; choose one member.",
                **ambiguity,
            )
        members = expand_polymer_identity(supplied_polymer)
        polymer = members[0] if members else resolve_polymer(supplied_polymer)
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


def _atmospheric_exclusion_counts() -> dict[str, int]:
    """Return the shared, explicit vocabulary for atmospheric exclusions."""
    return {
        "excluded_for_missing_boiling_point": 0,
        "excluded_for_boiling_point": 0,
    }


def _atmospheric_exclusion_reason(
    boiling_point_c: Optional[float],
    temperature_c: float,
) -> Optional[str]:
    """Distinguish absent data from operation at or above a known boiling point."""
    if boiling_point_c is None:
        return "excluded_for_missing_boiling_point"
    if temperature_c >= boiling_point_c:
        return "excluded_for_boiling_point"
    return None


def _atmospheric_exclusion_applies(
    reason: Optional[str],
    require_atmospheric: Optional[bool],
) -> bool:
    """Apply the tri-state policy without treating missing data as a property."""
    if reason is None or require_atmospheric is False:
        return False
    if require_atmospheric is True:
        return True
    return reason == "excluded_for_boiling_point"


def _atmospheric_filter_policy_name(
    require_atmospheric: Optional[bool],
) -> str:
    """Name the caller-visible meaning of the tri-state atmospheric option."""
    if require_atmospheric is True:
        return "strict_exclude_unknown_and_known_too_low"
    if require_atmospheric is False:
        return "off"
    return "default_keep_unknown_exclude_known_too_low"


def _screen_direction(
    target: str,
    retained: list[str],
    temperatures: list[float],
    require_atmospheric: Optional[bool],
    limit: int,
    candidate_solvents: Optional[set[str]] = None,
    ranking_mode: Literal[
        "target_dissolution", "separation_gap", "absolute_solubility",
    ] = "target_dissolution",
    solubility_threshold_pct: float = 5.0,
    common_temperature_counts: Optional[dict[float, dict[str, int]]] = None,
    common_temperature_candidates: Optional[dict[float, list[dict]]] = None,
) -> tuple[list[dict], int, dict[str, int], list[dict]]:
    best_by_solvent, screened = {}, 0
    atmospheric_exclusions = _atmospheric_exclusion_counts()
    for temperature in temperatures:
        rows = (
            [{
                "solvent": row["solvent"],
                "solvent_data_key": row["solvent_data_key"],
                "target_sol": row["solubility_pct"],
                "max_other_sol": None,
            } for solvent in sorted(get_available_solvents())
             if (row := _pair_result(target, solvent, temperature)) is not None]
            if ranking_mode == "absolute_solubility"
            else get_all_solvents_selectivity(target, retained, temperature)
        )
        for row in rows:
            display_solvent = str(row["solvent"])
            resolved_solvent = resolve_solvent(display_solvent)
            if candidate_solvents is not None and resolved_solvent not in candidate_solvents:
                continue
            screened += 1
            solvent_key = (
                row.get("solvent_data_key")
                or resolved_solvent
                or row["solvent"]
            )
            boiling_point = get_boiling_point(solvent_key)
            atmospheric_exclusion = _atmospheric_exclusion_reason(
                boiling_point, temperature,
            )
            atmospheric = (
                None if boiling_point is None else atmospheric_exclusion is None
            )
            if _atmospheric_exclusion_applies(
                atmospheric_exclusion, require_atmospheric,
            ):
                assert atmospheric_exclusion is not None
                atmospheric_exclusions[atmospheric_exclusion] += 1
                continue
            clipped = float(row["target_sol"]) >= 100.0
            if common_temperature_counts is not None:
                counts = common_temperature_counts[temperature]
                if clipped:
                    counts["clipped"] += 1
                if float(row["target_sol"]) >= solubility_threshold_pct:
                    counts["qualifying"] += 1
            off_target_evidence = ({
                polymer: get_solubility_result(
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
            canonical_solvent = canonical_solvent_name(solvent_key)
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
                "boiling_point_c": boiling_point,
                "boiling_point_margin_c": None if boiling_point is None else boiling_point - temperature,
                "atmospheric_feasible": atmospheric,
                "meets_selectivity_threshold": (
                    None if ranking_mode == "absolute_solubility" else score >= 5.0
                ),
                "meets_solubility_threshold": bool(
                    float(row["target_sol"]) >= solubility_threshold_pct
                ),
                "is_clipped": clipped,
                "clip_limit_wt_percent": 100.0,
                **get_solvent_hazard_framing(solvent_key),
            }
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
                candidate["ranking_score"], candidate["target_solubility_pct"],
            )
            existing_priority = (
                existing["ranking_score"], existing["target_solubility_pct"],
            ) if existing is not None else None
            if existing_priority is None or candidate_priority > existing_priority:
                best_by_solvent[key] = candidate
    ranked, ranked_all = _rank_screen_candidates(
        list(best_by_solvent.values()), limit, ranking_mode,
    )
    return ranked, screened, atmospheric_exclusions, ranked_all


def _rank_screen_candidates(
    candidates: list[dict],
    limit: int,
    ranking_mode: Literal[
        "target_dissolution", "separation_gap", "absolute_solubility",
    ],
) -> tuple[list[dict], list[dict]]:
    """Sort and annotate one candidate population without changing its scope."""
    ranked_all = sorted(
        candidates,
        key=lambda item: (
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
    if ranking_mode in ("absolute_solubility", "target_dissolution"):
        _assign_clipped_ceiling_ranks(ranked)
    else:
        for rank, item in enumerate(ranked, 1):
            item["rank"] = rank
    return ranked, ranked_all


@_with_solvent_scope
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
    stored-grid solvent catalog.  A one-polymer screen reports its exact count.

    For a lowest-temperature question about how many solvents clear a modeled
    solubility threshold, pass one feed polymer, the requested
    ``min_solubility_pct``, and ``minimum_qualifying_solvent_count``.  The
    result then reports full-candidate counts at each common grid setpoint;
    never infer that answer from the per-solvent best-temperature shortlist.
    Saturated 100 wt% ceilings qualify through 100 and share a ceiling rank.

    ``require_atmospheric`` is tri-state: ``None`` keeps solvents with missing
    boiling-point data but excludes conditions at or above a recorded boiling
    point; ``True`` excludes both causes; ``False`` excludes neither.
    """
    tool = "screen_polymer_separation"
    if not isinstance(feed_polymers, (list, tuple)) or not (requested := _unique_names(feed_polymers)):
        return tool_error(tool, "feed_polymers must contain at least one name.", error_code="invalid_feed_polymers")
    names, unsupported = [], []
    for name in requested:
        members = expand_polymer_identity(name)
        if not members:
            unsupported.append(name)
            continue
        names.extend(members)
    if unsupported:
        return tool_error(
            tool,
            "Unsupported feed polymer(s): " + ", ".join(unsupported),
            error_code="unknown_polymer",
            unsupported_polymers=unsupported,
            available_polymers=sorted(get_available_polymers()),
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
    except _InputError as error:
        return tool_error(
            tool, str(error), error_code=error.code, **error.detail,
        )
    except (TypeError, ValueError) as error:
        return tool_error(tool, str(error), error_code="invalid_feed_composition")
    supplied_min, supplied_max = temperature_min_c is not None, temperature_max_c is not None
    start = FITTED_TEMP_MIN_C if temperature_min_c is None else float(temperature_min_c)
    end = FITTED_TEMP_MAX_C if temperature_max_c is None else float(temperature_max_c)
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
            members = expand_polymer_identity(target)
            if not members or any(member not in names for member in members):
                return tool_error(
                    tool,
                    "Every target must be in feed_polymers.",
                    error_code="target_not_in_feed",
                    unknown_target_polymers=[target],
                )
            for member in members:
                if member not in targets:
                    targets.append(member)
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
            resolved = resolve_solvent(supplied)
            if resolved is None:
                unsupported_solvents.append(supplied)
            elif resolved not in constrained_solvents:
                constrained_solvents.append(resolved)
        if unsupported_solvents:
            return _solvent_resolution_errors(tool, unsupported_solvents)
        scoped = _refuse_solvents_out_of_scope(tool, constrained_solvents)
        if scoped is not None:
            return scoped
    temperatures = _temperature_grid(start, end, step, bool(strict_maximum))
    if not temperatures:
        return tool_error(tool, "No temperatures remain after applying bounds.", error_code="empty_temperature_grid")
    catalog_provenance = _screen_catalog_provenance(names, max(temperatures))
    resolved_require_atmospheric = require_atmospheric
    atmospheric_filter_policy = _atmospheric_filter_policy_name(
        require_atmospheric,
    )
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
    directions, combined, recommendations = [], [], {}
    screened_conditions = 0
    atmospheric_exclusions = _atmospheric_exclusion_counts()
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
        candidates, screened, exclusions, complete_candidates = _screen_direction(
            target, retained, temperatures, resolved_require_atmospheric, candidate_limit,
            None if constrained_solvents is None else set(constrained_solvents),
            ranking_mode,
            solubility_threshold,
            common_temperature_counts,
            common_temperature_candidates,
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
                )
                ranked_candidate_population = (
                    "qualifying_stored_grid_candidates_at_lowest_common_temperature"
                )
                ranked_candidate_population_count = len(shared_population)
                ranked_candidate_population_complete = (
                    len(candidates) == len(shared_population)
                )
        screened_conditions += screened
        for reason, count in exclusions.items():
            atmospheric_exclusions[reason] += count
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
                "boiling_point_margin_c",
                "ghs_signal_word", "typed_safety_evidence",
                "lower_hazard_search_scope", "lower_hazard_alternative_count",
                "lower_hazard_alternatives",
                "lower_hazard_alternatives_truncated", "ghs_danger_unavoidable",
            )},
        } if best else None)
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
        else "grid_min_to_user_max" if supplied_max
        else "user_min_to_grid_max" if supplied_min
        else "full_stored_grid_domain"
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
    if any(bool(candidate.get("is_clipped")) for candidate in combined):
        warnings.append(
            "A 100 wt% value marks the stored saturation ceiling; tied values "
            "are not ordered beyond that ceiling."
        )
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
            "threshold_count_excludes_clipped": False,
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
            "at each shared grid setpoint and include saturated 100 wt% values. "
            "The shortlist contains only qualifying "
            "stored-grid candidates at the reported shared temperature.",
        )
    if not resolved_require_atmospheric and any(
        candidate.get("atmospheric_feasible") is False for candidate in combined
    ):
        warnings.append(
            "Candidates at or above their normal boiling point remain in this thermodynamic "
            "ranking but are not atmospheric liquid-phase conditions."
        )
    if not resolved_require_atmospheric and any(
        candidate.get("atmospheric_feasible") is None for candidate in combined
    ):
        warnings.append(
            "Candidates without a recorded normal boiling point remain in this "
            "thermodynamic ranking; their atmospheric feasibility is unknown."
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
            [canonical_solvent_name(item) for item in constrained_solvents]
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
        **atmospheric_exclusions,
        solvent_catalog_provenance=catalog_provenance,
        warnings=warnings,
        model_basis=SOLUBILITY_MODEL_BASIS,
        feed_mass_fractions=composition,
        **extra,
        **solvent_scope_stamp(),
    )


@_with_solvent_scope
def screen_pairwise_solubility_overlap(
    feed_polymers: list[str],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    solvents: Optional[list[str]] = None,
    strict_maximum: bool = False,
    temperature_step_c: float = 5.0,
    require_atmospheric: Optional[bool] = None,
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
    Its tri-state ``require_atmospheric`` policy is inherited unchanged by
    every underlying directional screen.
    """
    tool = "screen_pairwise_solubility_overlap"
    if not isinstance(feed_polymers, (list, tuple)):
        return tool_error(tool, "feed_polymers must be a list.", error_code="invalid_feed_polymers")
    names, unsupported = [], []
    for supplied in _unique_names(feed_polymers):
        members = expand_polymer_identity(supplied)
        if not members:
            unsupported.append(supplied)
            continue
        names.extend(members)
    names = _unique_names(names)
    if unsupported:
        return tool_error(tool, "Unsupported feed polymer(s): " + ", ".join(unsupported),
                          error_code="unknown_polymer", unsupported_polymers=unsupported)
    if len(names) < 2:
        return tool_error(tool, "At least two polymers are required.", error_code="insufficient_feed_polymers")

    ranked: list[dict[str, Any]] = []
    atmospheric_exclusions = _atmospheric_exclusion_counts()
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
            for reason in atmospheric_exclusions:
                atmospheric_exclusions[reason] += int(data.get(reason, 0))
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
        temperature_dependent=True, require_atmospheric=require_atmospheric,
        **atmospheric_exclusions,
        solubility_unit="wt_pct_solution_concentration", gap_unit="percentage_points",
        hansen_parameters_used=False,
        hansen_applicability=("Hansen RED is a temperature-independent polymer-solvent compatibility "
                              "screen and cannot rank thermodynamic polymer-pair overlap."),
        evidence_class="temperature_dependent_thermodynamic_screen",
        solvent_catalog_provenance=get_solvent_catalog_provenance(),
        warnings=["Pair ranks are relative; they do not prove inseparability.",
                  "Each pair is optimized independently, not as a route.",
                  "Modeled wt% solution concentration is not recovery or purity."],
        model_basis="pairwise reuse of the unified grid-first solubility screen",
        **extra,
        **solvent_scope_stamp(),
    )


screen_polymer_separation.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]
screen_pairwise_solubility_overlap.__annotations__["temperature_step_c"] = Annotated[
    float, InjectedToolArg,
]
