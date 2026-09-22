"""Compact, deterministic thermodynamic kernel for DISSOLVE."""

from __future__ import annotations

import math
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Callable, Iterator, Optional, Sequence

import duckdb

GRID_EXACT = "grid_exact"
GRID_INTERPOLATION = "grid_interpolation"
SOLUBILITY_MODEL_BASIS = (
    "stored pair-specific values at 5 C nodes from 25 to 160 C; exact lookup only"
)

FITTED_TEMP_MIN_C = 25.0
FITTED_TEMP_MAX_C = 160.0
SENSITIVITY_EXTRAPOLATION_MAX_C = 200.0
EXTENDED_ADMISSION_MIN_BOILING_POINT_C = 25.0
EXTENDED_SOURCE_ASSET_SHA256 = (
    "963e5da06463ed6fbd29f44f8d8366d9fb6ac4a84da6c6ebf4d47c2d64e4c638"
)


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
def _thermodynamic_metadata() -> dict[str, str]:
    return dict(get_connection().execute(
        "SELECT key, value FROM thermodynamic_metadata"
    ).fetchall())


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


@lru_cache(maxsize=1)
def _polymer_mention_surfaces() -> tuple[tuple[str, str], ...]:
    surfaces = {
        (alias, identity)
        for identity in POLYMER_IDENTITIES
        for alias in _identity_aliases(identity)
    }
    return tuple(sorted(surfaces, key=lambda item: (-len(item[0]), item[0].casefold())))


def find_polymer_mentions(text: str) -> list[tuple[str, str]]:
    """Extract non-overlapping user labels using the canonical identity registry."""
    matches: list[tuple[int, int, str, str]] = []
    for surface, identity in _polymer_mention_surfaces():
        for match in re.finditer(
            rf"(?<![A-Za-z0-9]){re.escape(surface)}(?![A-Za-z0-9])", text, re.I,
        ):
            matches.append((match.start(), match.end(), match.group(0), identity))
    selected: list[tuple[int, int, str, str]] = []
    for candidate in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]))):
        if any(candidate[0] < item[1] and item[0] < candidate[1] for item in selected):
            continue
        selected.append(candidate)
    identities = {item[3] for item in selected}
    selected = [
        item for item in selected
        if not (
            POLYMER_IDENTITIES[item[3]].get("thermodynamic_members")
            and any(
                member in identities
                for member in POLYMER_IDENTITIES[item[3]]["thermodynamic_members"]
            )
        )
    ]
    return [(label, identity) for _, _, label, identity in sorted(selected)]


def named_polymer_identities(text: str) -> tuple[str, ...]:
    """Return registry identities the user actually named, with family expansion.

    An invented second polymer is not a named identity. Family labels such as
    PE authorize every thermodynamic member; a point identity authorizes itself.
    """
    identities: list[str] = []
    for label, identity in find_polymer_mentions(text):
        members = expand_polymer_identity(label)
        for item in members or (identity,):
            if item not in identities:
                identities.append(item)
    return tuple(identities)


def context_polymer_identities(payload: dict | None) -> tuple[str, ...]:
    """Return established polymer identities from typed session or plan context.

    The bounded prompt projection may drop the top-level polymers list.
    Candidate rows, routes, and declared feeds remain the durable prior.
    """
    if not isinstance(payload, dict):
        return ()
    identities: list[str] = []

    def add(value: object) -> None:
        texts: list[str] = []
        if isinstance(value, dict):
            texts.extend(str(item).strip() for item in value)
        elif isinstance(value, (list, tuple, set)):
            texts.extend(str(item).strip() for item in value)
        else:
            texts.append(str(value or "").strip())
        for text in texts:
            if not text:
                continue
            members = expand_polymer_identity(text)
            identity = resolve_polymer(text) if not members else None
            for item in members or ((identity,) if identity else ()):
                if item and item not in identities:
                    identities.append(item)

    add(payload.get("polymers"))
    declared = payload.get("declared_deliverable")
    if isinstance(declared, dict):
        for field in ("polymers", "feed_polymers", "target_polymers"):
            add(declared.get(field))
    constraints = payload.get("planning_constraints")
    if isinstance(constraints, dict):
        add(constraints.get("required_feed_polymers"))
    for row in payload.get("last_candidates") or []:
        if not isinstance(row, dict):
            continue
        add(row.get("first_polymer"))
        add(row.get("second_polymer"))
        add(row.get("dissolved_polymer"))
        add(row.get("polymer"))
        add(row.get("other_polymer_crossings"))
        add(row.get("retained_polymers"))
        add(row.get("polymers"))
    route = payload.get("last_route")
    if isinstance(route, dict):
        add(route.get("final_residue"))
        for step in route.get("steps") or []:
            if isinstance(step, dict):
                add(step.get("dissolved_polymer"))
                add(step.get("retained_polymers"))
    cycle = payload.get("last_process_cycle")
    if isinstance(cycle, dict):
        add(cycle.get("recovered_polymer"))
        add(cycle.get("sacrificial_getter_polymer"))
    analysis = payload.get("last_analysis")
    if isinstance(analysis, dict):
        add(analysis.get("requested_polymers"))
        add(analysis.get("modeled_polymers"))
        for pair in analysis.get("ranked_pairs") or []:
            if isinstance(pair, dict):
                add(pair.get("polymers"))
    return tuple(identities)


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


# ``tools.py`` aliases this live module as ``thermo``; a call there to
# ``thermo._nearest_nodes`` intentionally lands here.
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


def _temperature_grid(start: float, end: float, step: float) -> list[float]:
    return _nodes_within(start, end, step, False)


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


def get_available_pairs() -> set[tuple[str, str]]:
    return set(_usable_grid_pairs())


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


def get_logp(solvent: str) -> Optional[float]:
    return _property(solvent, "logp")
