"""PFAS/phthalate removal screens and isolated contaminant specialist."""

from __future__ import annotations

import json
import math
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import duckdb

from . import session
from . import thermodynamics as thermo
from .contracts import parse_tool_result, tool_error, tool_success
from .thermodynamics import _polymer_ambiguity_error

_ASSET = Path(str(files("dissolve").joinpath("data/contaminants.duckdb")))
_ASSET_SHA256 = "866d769b6a140bf289c5036fd5c0d7d2b6f424cb7994e71c76e16a1a1d9a4c5f"
_LOCAL = threading.local()
_FAMILY_ALIASES = {
    "pfas": "PFAS",
    "per- and polyfluoroalkyl substances": "PFAS",
    "perfluoroalkyl substances": "PFAS",
    "phthalate": "Phthalates",
    "phthalates": "Phthalates",
}
_UNCOVERED_FAMILY_ALIASES = {
    "bfr": "BFR",
    "brominated flame retardant": "BFR",
    "brominated flame retardants": "BFR",
    "flame retardant": "BFR",
    "flame retardants": "BFR",
}
_DEFAULT_SWELLING_MIN = 1.0
_DEFAULT_SWELLING_MAX = 10.0
_DEFAULT_DISSOLUTION_MIN = 10.0
_DEFAULT_PRECIPITATION_THRESHOLD = 1.0
_ATM_MARGIN = 1.0
_MAX_T = 160.0


@dataclass(frozen=True)
class ContaminantCriterionContract:
    """One numeric contaminant criterion and what crossing it means.

    The logD verdict was an inline ``logd > 0`` in two places, which is fine
    until something has to DRAW the boundary: a figure that shades its own
    idea of the pass region can disagree with the verdict it is illustrating
    and nothing would catch it. Stating the threshold, its direction, and
    the physical reading once lets the screen and the view share one
    boundary, and lets the view label it without inventing prose.
    """

    name: str
    axis: str
    threshold: float
    pass_direction: str
    pass_meaning: str
    fail_meaning: str

    def passes(self, value: object) -> bool:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        number = float(value)
        if not math.isfinite(number):
            return False
        return (
            number > self.threshold if self.pass_direction == "above"
            else number < self.threshold
        )


CONTAMINANT_LOGD_CRITERION = ContaminantCriterionContract(
    name="logd_pass",
    axis="logD",
    threshold=0.0,
    pass_direction="above",
    pass_meaning="positive logD partitions into the solvent (removable)",
    fail_meaning="negative logD stays with the polymer (not removable)",
)


def default_thresholds(*, include_precipitation: bool) -> dict[str, float]:
    """Return the single authoritative documented contaminant-screen defaults."""
    result = {
        "swelling_min_wt_pct": _DEFAULT_SWELLING_MIN,
        "swelling_max_wt_pct": _DEFAULT_SWELLING_MAX,
        "dissolution_min_wt_pct": _DEFAULT_DISSOLUTION_MIN,
    }
    if include_precipitation:
        result["precipitation_threshold_wt_pct"] = (
            _DEFAULT_PRECIPITATION_THRESHOLD
        )
    return result


def _connection() -> duckdb.DuckDBPyConnection:
    connection = getattr(_LOCAL, "contaminant_connection", None)
    if connection is None:
        connection = duckdb.connect(str(_ASSET), read_only=True)
        _LOCAL.contaminant_connection = connection
    return connection


def _key(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _items(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        # Providers routinely serialize an array argument as a JSON string.
        # Comma-splitting that produces '["PFAS"' and '"phthalates"]', which
        # match no catalog entry, so a well-formed request would be reported
        # as entirely unsupported. Parse the JSON form first and keep the
        # comma-delimited fallback, matching analysis._items and
        # research._items.
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return [item.strip() for item in value.split(",") if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _finite(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


@lru_cache(maxsize=1)
def _families() -> dict[str, list[str]]:
    rows = _connection().execute(
        "SELECT family, contaminant FROM contaminants ORDER BY family, contaminant"
    ).fetchall()
    result: dict[str, list[str]] = {}
    for family, contaminant in rows:
        result.setdefault(str(family), []).append(str(contaminant))
    return result


@dataclass(frozen=True)
class _ResolvedContaminant:
    name: str
    family: str
    key: str
    cas_number: Optional[str]
    resolution_basis: str
    pubchem_cid: Optional[str]

    @property
    def identity_verified(self) -> bool:
        return self.resolution_basis == "cas_verified"


# Standard acronyms for the workbook's 26 PFAS, which it names in full: "Which wash solvents remove PFOA from LDPE"
# came back unsupported (2026-09-24). Each points at the workbook's own entry; one it lacks adds nothing.
_PFAS_ACRONYMS = {
    "PFBA": "perfluorobutanoic acid", "PFPeA": "perfluoropentanoic acid", "PFHxA": "perfluorohexanoic acid",
    "PFHpA": "perfluoroheptanoic acid", "PFOA": "perfluorooctanoic acid", "PFNA": "perfluorononanoic acid",
    "PFDA": "perfluorodecanoic acid", "PFUnDA": "perfluoroundecanoic acid", "PFUnA": "perfluoroundecanoic acid",
    "PFDoDA": "perfluorododecanoic acid", "PFDoA": "perfluorododecanoic acid", "PFTrDA": "perfluorotridecanoic acid",
    "PFTeDA": "perfluorotetradecanoic acid", "PFBS": "perfluorobutanesulfonic acid",
    "PFPeS": "perfluoropentanesulfonic acid", "PFHxS": "perfluorohexanesulfonic acid",
    "PFHpS": "perfluoroheptanesulfonic acid", "PFOS": "perfluorooctanesulfonic acid", "PFNS": "perfluorononanesulfonate",
    "PFDS": "perfluorodecanesulfonic acid", "PFUnDS": "perfluoroundecanesulfonic acid",
    "PFDoDS": "perfluorododecanesulfonic acid", "PFTrDS": "perfluorotridecanesulfonic acid",
    "HFPO-DA": "2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoic acid",
    "GenX": "ammonium 2,3,3,3-tetrafluoro-2-(heptafluoropropoxy)propanoate",
    "ADONA": "ammonium 4,8-dioxa-3h-perfluorononanoate", "NaDONA": "sodium dodecafluoro-3h-4, 8-dioxanonanoate",
    "F-53B": "potassium 9-chlorohexadecafluoro-3-oxanonane-1-sulfonate",
}


@lru_cache(maxsize=1)
def _contaminant_lookup() -> dict[str, _ResolvedContaminant]:
    """Folded alias → catalog identity: the catalog table, plus standard PFAS acronyms for entries it names in full."""
    result: dict[str, _ResolvedContaminant] = {}
    for alias, key, name, family, cas, basis, cid in _connection().execute(
        "SELECT alias, contaminant_key, canonical_name, family, "
        "cas_number, resolution_basis, pubchem_cid "
        "FROM contaminant_aliases ORDER BY alias"
    ).fetchall():
        folded = _key(alias)
        if not folded:
            continue
        result.setdefault(
            folded,
            _ResolvedContaminant(
                name=str(name),
                family=str(family),
                key=str(key),
                cas_number=cas,
                resolution_basis=str(basis),
                pubchem_cid=cid,
            ),
        )
    for acronym, name in _PFAS_ACRONYMS.items():
        identity = result.get(_key(name))
        if identity is not None:
            result.setdefault(_key(acronym), identity)
    return result


def _served_identity(identity: _ResolvedContaminant) -> dict[str, Any]:
    """Public identity. Only cas_verified may be served as verified."""
    return {
        "contaminant": identity.name,
        "contaminant_key": identity.key,
        "contaminant_family": identity.family,
        "resolution_basis": identity.resolution_basis,
        "identity_verified": identity.identity_verified,
        "cas_number": identity.cas_number,
        "pubchem_cid": identity.pubchem_cid,
    }


def _contaminant_catalog(contaminants: Sequence[str]) -> list[dict[str, Any]]:
    """Attach family and resolution_basis to every expanded contaminant."""
    lookup = _contaminant_lookup()
    catalog = []
    for contaminant in contaminants:
        identity = lookup.get(_key(contaminant))
        if identity is None:
            continue
        catalog.append(_served_identity(identity))
    return catalog


def _provenance() -> dict[str, str]:
    """Return the single source-and-model qualification for screen evidence."""
    return {
        "source_dataset": "Zhou workbook miscibility and logD",
        "source_asset": "contaminants.duckdb",
        "source_asset_sha256": _ASSET_SHA256,
        "thermodynamic_basis": thermo.SOLUBILITY_MODEL_BASIS,
        "evidence_class": "screening_proxy",
    }


_UNSOURCED_SWELL_DISSOLVE_WARNING = (
    "Default swelling / dissolution proxies (1 / 10 wt%, swelling max 10) "
    "have no regulatory or literature citation on this function. Zhou "
    "workbook provenance is the miscibility/logD evidence class, not the "
    "swelling or dissolution threshold basis."
)
_PAPER_PRECIPITATION_NOTE = (
    "Default precipitation_threshold_wt_pct 1 wt% follows Zhou et al., "
    "Green Chem. 2026, 28, 9061 ('we set a threshold of 1 wt%')."
)
_UNSOURCED_SWELL_DISSOLVE_KEYS = frozenset({
    "swelling_min_wt_pct", "swelling_max_wt_pct", "dissolution_min_wt_pct",
})


def _source_label(key: str, supplied: bool) -> str:
    if supplied:
        return "user"
    if key == "precipitation_threshold_wt_pct":
        return "paper"
    return "default"


def _served_threshold_fields(
    inputs: dict[str, Any], *, include_precipitation: bool | None = None,
) -> dict[str, Any]:
    """Publish active proxies. Precipitation 1 wt% is paper-sourced; 1/10 is not."""
    sources = dict(inputs["threshold_sources"])
    if include_precipitation is None:
        include_precipitation = "precipitation_threshold_wt_pct" in inputs
    published_sources = {
        key: value for key, value in sources.items()
        if include_precipitation or key != "precipitation_threshold_wt_pct"
    }
    unsourced = any(
        published_sources.get(key) == "default"
        for key in _UNSOURCED_SWELL_DISSOLVE_KEYS
    )
    if unsourced:
        citation_status = "unsourced"
    elif "user" in published_sources.values():
        citation_status = "user_requested"
    elif published_sources.get("precipitation_threshold_wt_pct") == "paper":
        citation_status = "paper_sourced"
    else:
        citation_status = "user_requested"
    citations = {
        key: (
            "zhou_green_chem_2026" if source == "paper"
            else "unsourced" if source == "default"
            else "user"
        )
        for key, source in published_sources.items()
    }
    fields: dict[str, Any] = {
        "swelling_min_wt_pct": inputs["swelling_min_wt_pct"],
        "swelling_max_wt_pct": inputs["swelling_max_wt_pct"],
        "dissolution_min_wt_pct": inputs["dissolution_min_wt_pct"],
        "threshold_basis": inputs["threshold_basis"],
        "threshold_sources": published_sources,
        "threshold_citations": citations,
        "threshold_citation_status": citation_status,
    }
    if include_precipitation and "precipitation_threshold_wt_pct" in inputs:
        fields["precipitation_threshold_wt_pct"] = inputs[
            "precipitation_threshold_wt_pct"
        ]
    return fields


def _threshold_warnings(
    inputs: dict[str, Any], *, include_precipitation: bool | None = None,
) -> list[str]:
    fields = _served_threshold_fields(
        inputs, include_precipitation=include_precipitation,
    )
    warnings: list[str] = []
    if any(
        fields["threshold_sources"].get(key) == "default"
        for key in _UNSOURCED_SWELL_DISSOLVE_KEYS
    ):
        warnings.append(_UNSOURCED_SWELL_DISSOLVE_WARNING)
    if fields["threshold_sources"].get("precipitation_threshold_wt_pct") == "paper":
        warnings.append(_PAPER_PRECIPITATION_NOTE)
    return warnings


def _expand(
    requested: Sequence[str],
) -> tuple[list[str], list[str], list[str], list[str]]:
    supported, unsupported, families, uncovered = [], [], set(), []
    lookup = _contaminant_lookup()
    for item in requested:
        text = str(item).strip()
        folded = _key(text)
        family = _FAMILY_ALIASES.get(folded)
        uncovered_family = _UNCOVERED_FAMILY_ALIASES.get(folded)
        if family:
            supported.extend(_families().get(family, []))
            families.add(family)
        elif uncovered_family:
            uncovered.append(uncovered_family)
            unsupported.append(text)
        elif folded in lookup:
            identity = lookup[folded]
            supported.append(identity.name)
            families.add(identity.family)
        elif text:
            unsupported.append(text)
    return (
        list(dict.fromkeys(supported)),
        list(dict.fromkeys(unsupported)),
        sorted(families),
        list(dict.fromkeys(uncovered)),
    )


def _solvent_names(contaminants: Sequence[str]) -> list[str]:
    keys = [_key(item) for item in contaminants]
    if not keys:
        return []
    placeholders = ",".join("?" for _ in keys)
    rows = _connection().execute(
        f"""SELECT DISTINCT solvent_key FROM (
            SELECT solvent_key FROM miscibility WHERE contaminant_key IN ({placeholders})
            UNION SELECT solvent_key FROM logd WHERE contaminant_key IN ({placeholders})
        ) ORDER BY solvent_key""",
        [*keys, *keys],
    ).fetchall()
    return [str(row[0]) for row in rows]


@lru_cache(maxsize=1)
def _workbook_solvent_rows() -> tuple[tuple[str, str, str], ...]:
    rows = _connection().execute(
        """SELECT DISTINCT solvent_raw, solvent_key, solvent_normalized FROM (
             SELECT solvent_raw, solvent_key, solvent_normalized FROM logd
             UNION
             SELECT solvent_raw, solvent_key, solvent_normalized FROM miscibility
           )"""
    ).fetchall()
    return tuple(
        (str(raw or ""), str(key or ""), str(normalized or ""))
        for raw, key, normalized in rows
    )


def _solvent_where(tokens: Sequence[str]) -> tuple[str, list[str]]:
    """Match any of the three workbook solvent identity columns."""
    if not tokens:
        return "FALSE", []
    placeholders = ",".join("?" for _ in tokens)
    sql = (
        f"(solvent_key IN ({placeholders}) OR "
        f"solvent_normalized IN ({placeholders}) OR "
        f"lower(trim(CAST(solvent_raw AS VARCHAR))) IN ({placeholders}))"
    )
    return sql, list(tokens) * 3


def _solvent_keys(name: str) -> tuple[str, ...]:
    """Every label that can hit a workbook row for this thermo identity.

    ``solvent_raw`` / ``solvent_key`` / ``solvent_normalized`` plus
    ``thermo.solvent_identity_labels`` and any workbook row whose
    ``resolve_solvent`` matches. ``butanone`` must reach ``2-butanone``;
    ``aceticacid`` must reach ``acetic acid``. ``o-xylene`` must not become
    catalog ``xylene`` (p-xylene).
    """
    raw = _key(name)
    resolved = thermo.resolve_solvent(name)
    tokens: list[str] = []
    if raw:
        tokens.append(raw)
    if resolved:
        tokens.append(_key(resolved))
        for label in thermo.solvent_identity_labels(resolved):
            folded = _key(label)
            if folded:
                tokens.append(folded)
    token_set = {item for item in tokens if item}
    resolved_key = _key(resolved) if resolved else ""
    for workbook_raw, workbook_key, workbook_normalized in _workbook_solvent_rows():
        members = {
            _key(workbook_raw),
            _key(workbook_key),
            _key(workbook_normalized),
        }
        members.discard("")
        if token_set & members:
            token_set.update(members)
            continue
        if not resolved_key:
            continue
        for candidate in (workbook_key, workbook_normalized, workbook_raw):
            other = thermo.resolve_solvent(candidate) if candidate else None
            if other and _key(other) == resolved_key:
                token_set.update(members)
                break
    return tuple(item for item in token_set if item)


def _miscibility(solvent: str, contaminant: str, regime: str) -> dict[str, Any] | None:
    solvent_keys = _solvent_keys(solvent)
    if not solvent_keys:
        return None
    where, params = _solvent_where(solvent_keys)
    rows = _connection().execute(
        f"""SELECT temperature_regime, temperature_c, boiling_point_c,
                   t_higher_c, miscible
            FROM miscibility
            WHERE contaminant_key=? AND {where}
            ORDER BY CASE WHEN temperature_regime=? THEN 0 ELSE 1 END, rowid""",
        [_key(contaminant), *params, regime],
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return {
        "temperature_regime": row[0], "temperature_c": row[1],
        "boiling_point_c": row[2], "t_higher_c": row[3], "miscible": row[4],
    }


def _miscibility_regimes(solvent: str, contaminant: str) -> tuple[str, ...]:
    """Every workbook regime for this pair. Does not require the asked _regime."""
    solvent_keys = _solvent_keys(solvent)
    if not solvent_keys:
        return ()
    where, params = _solvent_where(solvent_keys)
    rows = _connection().execute(
        f"""SELECT DISTINCT temperature_regime FROM miscibility
            WHERE contaminant_key=? AND {where}""",
        [_key(contaminant), *params],
    ).fetchall()
    return tuple(str(row[0]) for row in rows if row and row[0])


def _unspecified_only_basis(solvent: str, contaminants: Sequence[str]) -> bool:
    """True if any contaminant has workbook rows and none of them is rt/t_higher."""
    for contaminant in contaminants:
        regimes = set(_miscibility_regimes(solvent, contaminant))
        if regimes and regimes <= {"unspecified"}:
            return True
    return False


def _leaching_basis(contaminants: Sequence[str]) -> tuple[bool, list[str]]:
    """Workbook solvents whose logD meets CONTAMINANT_LOGD_CRITERION.

    Named on the unspecified-STRAP refuse so the caller can see whether
    leaching exists. Not a mode fallback.
    """
    names: list[str] = []
    for solvent in _solvent_names(contaminants):
        if any(
            CONTAMINANT_LOGD_CRITERION.passes(_logd(solvent, item))
            for item in contaminants
        ):
            names.append(solvent)
    return bool(names), names


def _logd(solvent: str, contaminant: str) -> Optional[float]:
    solvent_keys = _solvent_keys(solvent)
    if not solvent_keys:
        return None
    where, params = _solvent_where(solvent_keys)
    row = _connection().execute(
        f"""SELECT logd FROM logd WHERE contaminant_key=? AND {where}
            ORDER BY rowid LIMIT 1""",
        [_key(contaminant), *params],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _regime(solvent: str, temperature: Optional[float]) -> str:
    solvent_keys = _solvent_keys(solvent)
    if temperature is None or not solvent_keys:
        return "rt"
    where, params = _solvent_where(solvent_keys)
    rows = _connection().execute(
        f"""SELECT t_higher_c FROM miscibility
            WHERE {where} AND t_higher_c IS NOT NULL""",
        params,
    ).fetchall()
    higher = [float(row[0]) for row in rows if row[0] is not None]
    return "t_higher" if higher and temperature >= (25.0 + max(higher)) / 2.0 else "rt"


def _solvent_in_workbook(name: str) -> bool:
    tokens = _solvent_keys(name)
    if not tokens:
        return False
    where, params = _solvent_where(tokens)
    row = _connection().execute(
        f"""SELECT 1 FROM (
              SELECT 1 FROM logd WHERE {where}
              UNION ALL
              SELECT 1 FROM miscibility WHERE {where}
            ) LIMIT 1""",
        [*params, *params],
    ).fetchone()
    return row is not None


def _upper(solvent: str, maximum: Optional[float]) -> float:
    upper = _MAX_T if maximum is None else maximum
    boiling = thermo.get_boiling_point(solvent)
    return min(upper, boiling - _ATM_MARGIN) if boiling is not None else upper


def _grid_ceiling(maximum: Optional[float], strict: bool) -> Optional[float]:
    if maximum is None or not strict:
        return maximum
    return 25.0 + 5.0 * (math.ceil((maximum - 25.0) / 5.0) - 1)


def _inherited_candidate_solvents(tool: str) -> list[str]:
    state = session.current_tool_session()
    prior = state.last_contaminant if state and state.last_contaminant else {}
    recommended = prior.get("recommended_solvents") or []
    if isinstance(recommended, dict):
        mode = {
            "screen_contaminant_leaching": "leaching",
            "screen_contaminant_strap_removal": "strap_contaminant_removal",
        }.get(tool)
        if mode:
            candidates = list(recommended.get(mode) or [])
        else:
            candidates = [
                item
                for values in recommended.values()
                for item in list(values or [])
            ]
    else:
        candidates = list(recommended)
    if not candidates:
        candidates = [
            item.get("solvent")
            for item in prior.get("candidate_conditions") or []
            if item.get("solvent")
        ]
    return list(dict.fromkeys(
        str(item).strip() for item in candidates if str(item).strip()
    ))


def _active_thresholds(
    tool: str,
    swelling_min_wt_pct: Optional[float],
    swelling_max_wt_pct: Optional[float],
    dissolution_min_wt_pct: Optional[float],
    precipitation_threshold_wt_pct: Optional[float],
    *,
    include_precipitation: bool,
) -> tuple[dict[str, Any], str | None]:
    """Resolve request thresholds once and reject invalid science explicitly."""
    supplied = {
        "swelling_min_wt_pct": swelling_min_wt_pct is not None,
        "swelling_max_wt_pct": swelling_max_wt_pct is not None,
        "dissolution_min_wt_pct": dissolution_min_wt_pct is not None,
        "precipitation_threshold_wt_pct": (
            include_precipitation and precipitation_threshold_wt_pct is not None
        ),
    }
    raw: dict[str, Any] = default_thresholds(
        include_precipitation=include_precipitation,
    )
    raw.update({
        key: value for key, value in {
            "swelling_min_wt_pct": swelling_min_wt_pct,
            "swelling_max_wt_pct": swelling_max_wt_pct,
            "dissolution_min_wt_pct": dissolution_min_wt_pct,
            "precipitation_threshold_wt_pct": precipitation_threshold_wt_pct,
        }.items()
        if value is not None
        and (include_precipitation or not key.startswith("precipitation"))
    })
    values = {key: _finite(value) for key, value in raw.items()}
    constraint = (
        "0 < swelling_min_wt_pct < swelling_max_wt_pct <= "
        "dissolution_min_wt_pct <= 100"
    )
    if any(value is None for value in values.values()) or not (
        0 < float(values["swelling_min_wt_pct"])
        < float(values["swelling_max_wt_pct"])
        <= float(values["dissolution_min_wt_pct"])
        <= 100
    ):
        return {}, tool_error(
            tool,
            f"Contaminant thresholds must satisfy {constraint}.",
            error_code="invalid_contaminant_thresholds",
            threshold_constraint=constraint,
            supplied_thresholds_wt_pct=raw,
        )
    if include_precipitation and not (
        0 < float(values["precipitation_threshold_wt_pct"])
        < float(values["dissolution_min_wt_pct"])
    ):
        precipitation_constraint = (
            "0 < precipitation_threshold_wt_pct < dissolution_min_wt_pct"
        )
        return {}, tool_error(
            tool,
            f"The swing threshold must satisfy {precipitation_constraint}.",
            error_code="invalid_contaminant_thresholds",
            threshold_constraint=precipitation_constraint,
            supplied_thresholds_wt_pct=raw,
        )
    active = {key: float(value) for key, value in values.items()}
    active["threshold_basis"] = (
        "user_requested" if any(supplied.values()) else "default_proxy"
    )
    active["threshold_sources"] = {
        key: _source_label(key, supplied[key])
        for key in values
    }
    return active, None


def _polymer_status(
    polymer: str, solvent: str, temperature: float, thresholds: dict[str, Any],
) -> dict[str, Any]:
    resolved = thermo.resolve_polymer(polymer)
    if not resolved:
        return {"polymer": polymer, "status": "unsupported_polymer", "solubility_wt_pct": None}
    result = thermo.get_solubility_result(resolved, solvent, temperature)
    value = result.get("solubility_pct")
    if value is None:
        return {"polymer": resolved, "status": "unsupported_pair", "solubility_wt_pct": None}
    swelling_min = float(thresholds["swelling_min_wt_pct"])
    swelling_max = float(thresholds["swelling_max_wt_pct"])
    dissolution_min = float(thresholds["dissolution_min_wt_pct"])
    status = (
        "dissolving" if value >= dissolution_min
        else "non_dissolving_proxy_swelling_candidate"
        if swelling_min <= value < swelling_max
        else "non_dissolving_above_swelling_window" if value >= swelling_max
        else "non_dissolving_low_swelling_confidence"
    )
    return {
        "polymer": resolved,
        "status": status,
        "solubility_wt_pct": float(value),
    }


_FIELD_ORIGIN_TABULATED = "tabulated"
_FIELD_ORIGIN_COMPUTED = "computed"

#: Overlay identity pins. Catalog rows have no InChIKey column; the producer
#: stamps one. Unknown keys do not bind.
_OVERLAY_INCHIKEY_ALIASES = {
    "FLKPEMZONWLCSK-UHFFFAOYSA-N": "DEP",
    "DOIRQSBPFJWKBE-UHFFFAOYSA-N": "DBP",
    "IRIAEXORFWYRCZ-UHFFFAOYSA-N": "BBP",
    "BJQHLKABXJIVAM-UHFFFAOYSA-N": "DEHP",
    "BJQHLKABXJIVAM-PMACEKPBSA-N": "DEHP",
}


def _overlay_contaminant_key(item: Mapping[str, Any]) -> str | None:
    """Contaminant the overlay is about. Solvent-only rows do not bind."""
    lookup = _contaminant_lookup()
    for field in ("contaminant_key", "contaminant"):
        raw = item.get(field)
        if not raw:
            continue
        identity = lookup.get(_key(raw))
        if identity is not None:
            return identity.key
    inchikey = str(item.get("inchikey") or "").strip()
    alias = _OVERLAY_INCHIKEY_ALIASES.get(inchikey)
    if alias:
        identity = lookup.get(_key(alias))
        if identity is not None:
            return identity.key
    return None


def _index_computed_deltas(
    computed_deltas: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Index overlay rows by (contaminant_key, solvent_key).

    A row informs only when it names a contaminant and carries field_origin.
    A {solvent: float} map has neither, so it does not bind. Overlay cannot
    wear tabulated for the served logD; tabulated stamps stay labels.
    """
    if not computed_deltas:
        return {}
    items: list[Mapping[str, Any]]
    if isinstance(computed_deltas, Mapping):
        if "delta_logd" in computed_deltas:
            items = [computed_deltas]
        else:
            items = []
            for key, value in computed_deltas.items():
                if isinstance(value, Mapping):
                    items.append({**value, "solvent_key": value.get("solvent_key") or key})
    else:
        items = [item for item in computed_deltas if isinstance(item, Mapping)]
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for item in items:
        if item.get("delta_logd") is None:
            continue
        raw_origin = item.get("field_origin")
        if raw_origin is None or str(raw_origin).strip() == "":
            continue
        origin = str(raw_origin).strip()
        serves_logd = origin == _FIELD_ORIGIN_COMPUTED
        if origin == _FIELD_ORIGIN_TABULATED:
            origin = _FIELD_ORIGIN_COMPUTED
        elif origin != _FIELD_ORIGIN_COMPUTED:
            continue
        contaminant_key = _overlay_contaminant_key(item)
        solvent_key = _key(
            item.get("solvent_key") or item.get("query") or item.get("solvent") or ""
        )
        if not contaminant_key or not solvent_key:
            continue
        indexed[(contaminant_key, solvent_key)] = {
            **dict(item),
            "field_origin": origin,
            "_serves_logd": serves_logd,
        }
    return indexed


def _contaminant_rows(
    solvent: str, contaminants: Sequence[str], regime: str,
    computed_by_pair: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> tuple[list[dict], Optional[float], bool, bool]:
    rows, minimum, all_miscible, all_positive = [], None, True, True
    lookup = _contaminant_lookup()
    solvent_key = _key(solvent)
    for contaminant in contaminants:
        identity = lookup.get(_key(contaminant))
        overlay = None
        if identity is not None and computed_by_pair:
            overlay = computed_by_pair.get((identity.key, solvent_key))
        miscibility = _miscibility(solvent, contaminant, regime)
        table_logd = _logd(solvent, contaminant)
        computed_value = None
        computed_origin = None
        computed_reference = None
        serves_logd = False
        if overlay is not None and overlay.get("delta_logd") is not None:
            computed_value = float(overlay["delta_logd"])
            computed_origin = overlay.get("field_origin") or _FIELD_ORIGIN_COMPUTED
            if computed_origin == _FIELD_ORIGIN_TABULATED:
                computed_origin = _FIELD_ORIGIN_COMPUTED
            computed_reference = overlay.get("reference")
            serves_logd = bool(overlay.get("_serves_logd"))
        if table_logd is not None:
            logd = table_logd
            origin = _FIELD_ORIGIN_TABULATED
        elif computed_value is not None and serves_logd:
            logd = computed_value
            origin = _FIELD_ORIGIN_COMPUTED
        else:
            logd = None
            origin = None
        miscible = miscibility.get("miscible") if miscibility else None
        all_miscible &= miscible is True
        all_positive &= CONTAMINANT_LOGD_CRITERION.passes(logd)
        if logd is not None:
            minimum = logd if minimum is None else min(minimum, logd)
        rows.append({
            **_served_identity(lookup[_key(contaminant)]),
            "miscible": miscible, "logd": logd,
            "field_origin": origin,
            "tabulated_logd": table_logd,
            "computed_delta_logd": computed_value,
            "computed_field_origin": computed_origin,
            "computed_reference": computed_reference,
            "miscibility_regime": (
                miscibility.get("temperature_regime") if miscibility else regime
            ),
        })
    return rows, minimum, bool(all_miscible), bool(all_positive)


def _sort_key(row: dict[str, Any]) -> tuple:
    status = row.get("target_polymer_status")
    proxy = 2 if status == "non_dissolving_proxy_swelling_candidate" else 1 if status == "non_dissolving_low_swelling_confidence" else 0
    return (
        int(bool(row.get("passes"))), int(bool(row.get("contaminant_miscibility_pass"))),
        int(bool(row.get("contaminant_logd_pass"))), proxy,
        row.get("contaminant_logd_min") if row.get("contaminant_logd_min") is not None else -999.0,
    )


def _inputs(
    tool: str, target_polymer: str, contaminants: str | Sequence[str],
    other_polymers: str | Sequence[str] | None, solvents: str | Sequence[str] | None,
    max_temperature_c: Optional[float], strict_maximum: bool,
    swelling_min_wt_pct: Optional[float],
    swelling_max_wt_pct: Optional[float],
    dissolution_min_wt_pct: Optional[float],
    precipitation_threshold_wt_pct: Optional[float] = None,
    *,
    include_precipitation: bool = False,
) -> tuple[dict[str, Any], str | None]:
    thresholds, threshold_error = _active_thresholds(
        tool, swelling_min_wt_pct, swelling_max_wt_pct,
        dissolution_min_wt_pct, precipitation_threshold_wt_pct,
        include_precipitation=include_precipitation,
    )
    if threshold_error:
        return {}, threshold_error
    ambiguity = _polymer_ambiguity_error(
        tool, target_polymer, "target_polymer",
    )
    if ambiguity:
        return {}, ambiguity
    target = thermo.resolve_polymer(target_polymer)
    requested = _items(contaminants)
    supported, unsupported, families, uncovered_families = _expand(requested)
    others_requested = _items(other_polymers)
    others: list[str] = []
    missing_others: list[str] = []
    for item in others_requested:
        members = thermo.expand_polymer_identity(item)
        if not members:
            missing_others.append(item)
            continue
        for member in members:
            if member not in others:
                others.append(member)
    explicit_solvents = None if solvents is None else _items(solvents)
    unsupported_solvents: list[str] = []
    if explicit_solvents is not None:
        known_solvents = [
            item for item in explicit_solvents if _solvent_in_workbook(item)
        ]
        unsupported_solvents = [
            item for item in explicit_solvents if item not in known_solvents
        ]
        candidate_solvents = known_solvents
    else:
        candidate_solvents, _ = session.resolve_candidate_argument(
            None, _inherited_candidate_solvents(tool),
        )
    requested_maximum = _finite(max_temperature_c)
    strict = bool(strict_maximum and requested_maximum is not None)
    maximum = _grid_ceiling(requested_maximum, strict)
    if not target:
        return {}, tool_error(tool, f"Unsupported target polymer: {target_polymer}.", error_code="unsupported_polymer")
    if explicit_solvents and not candidate_solvents:
        return {}, tool_error(
            tool,
            "None of the requested solvents are in the contaminant workbook.",
            error_code="unknown_contaminant_solvent",
            target_polymer=target,
            requested_solvents=explicit_solvents,
            unsupported_solvents=unsupported_solvents,
            requested_contaminants=requested,
        )
    if not supported and uncovered_families:
        return {}, tool_error(
            tool,
            "The contaminant corpus does not cover this family.",
            error_code="unsupported_contaminant_family",
            target_polymer=target,
            other_polymers=others,
            unsupported_other_polymers=missing_others,
            requested_contaminants=requested,
            unsupported_contaminants=unsupported or requested,
            unsupported_families=uncovered_families,
            supported_families=sorted(_families()),
            warnings=[
                "The held corpus is PFAS and Phthalates only. Empty is not clean."
            ],
        )
    if not supported:
        elsewhere = _in_plastchem(unsupported or requested)
        return {}, tool_error(
            tool, "None of the requested contaminants are supported." + (
                f" screen_contaminant_partitioning covers {', '.join(elsewhere)} (PlastChem; one polymer and one "
                "solvent per call)." if elsewhere else ""),
            error_code="unsupported_contaminants", target_polymer=target,
            other_polymers=others, unsupported_other_polymers=missing_others,
            requested_contaminants=requested,
            unsupported_contaminants=unsupported or requested,
            plastchem_partitioning_covers=elsewhere,
            supported_families=sorted(_families()),
            warnings=[
                "No contaminant-specific solvent, wash chemistry, physical-sorting "
                "method, temperature, or removal performance is supported for these inputs."
            ],
        )
    if max_temperature_c is not None and requested_maximum is None:
        return {}, tool_error(tool, "Maximum temperature must be finite.", error_code="invalid_temperature")
    if maximum is not None and maximum < 25.0:
        return {}, tool_error(
            tool, "The requested temperature range contains no 5 C grid point at or above 25 C.",
            error_code="empty_temperature_range",
        )
    return {
        "target": target, "requested": requested, "supported": supported,
        "unsupported": unsupported, "families": families, "others": others,
        "uncovered_families": uncovered_families,
        "missing_others": missing_others, "solvents": candidate_solvents,
        "unsupported_solvents": unsupported_solvents,
        "maximum": maximum, "requested_maximum": requested_maximum,
        "strict_maximum": strict,
        **thresholds,
    }, None


def _base_result(inputs: dict[str, Any], mode: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows.sort(key=_sort_key, reverse=True)
    result = {
        "analysis_type": "contaminant_screen_analysis",
        "mode": mode, "target_polymer": inputs["target"],
        "other_polymers": inputs["others"],
        "unsupported_other_polymers": inputs["missing_others"],
        "temperature_max_c": (
            inputs["requested_maximum"]
            if inputs["requested_maximum"] is not None else _MAX_T
        ),
        "highest_evaluated_temperature_c": (
            inputs["maximum"] if inputs["maximum"] is not None else _MAX_T
        ),
        "strict_maximum": inputs["strict_maximum"],
        "requested_contaminants": inputs["requested"],
        "contaminants": inputs["supported"],
        "supported_contaminants": inputs["supported"],
        "contaminant_catalog": _contaminant_catalog(inputs["supported"]),
        "n_supported_contaminants": len(inputs["supported"]),
        "unsupported_contaminants": inputs["unsupported"],
        "unsupported_families": inputs.get("uncovered_families") or [],
        "unsupported_solvents": inputs.get("unsupported_solvents") or [],
        "contaminant_families": inputs["families"],
        "candidate_solvents": rows,
        "recommended_solvents": [row["solvent"] for row in rows if row["passes"]],
        "best_result_is_weak": not any(row["passes"] for row in rows),
        "stage_count": len(rows),
        "family_record_count": len(rows) * len(inputs["families"]),
        "criterion_record_count": len(rows) * len(inputs["supported"]),
        "model_basis": (
            "Zhou workbook miscibility/logD plus grid-first thermodynamic "
            "screening proxies from stored values"
        ),
        "provenance": _provenance(),
        **_served_threshold_fields(
            inputs, include_precipitation=mode != "leaching",
        ),
    }
    return result


def _leaching(inputs: dict[str, Any]) -> dict[str, Any]:
    candidates = inputs["solvents"] or _solvent_names(inputs["supported"])
    rows = []
    for solvent in list(dict.fromkeys(_key(item) for item in candidates)):
        upper = _upper(solvent, inputs["maximum"])
        regime = _regime(solvent, upper)
        # The polymer's solubility exists only at grid nodes: a hot wash runs at the highest node at or below the
        # boiling-point cap. At the cap itself (ethanol: 77.2 °C) the lookup found nothing and failed the wash as an
        # "unsupported pair" although the miscibility and logD checks had passed (review finding A06-R1).
        wanted = upper if regime == "t_higher" else min(upper, 25.0)
        temperature = max((node for node in thermo._grid_nodes() if node <= wanted), default=wanted)
        contaminants, minimum, miscible, positive = _contaminant_rows(
            solvent, inputs["supported"], regime,
            computed_by_pair=_index_computed_deltas(inputs.get("computed_deltas")),
        )
        target = _polymer_status(inputs["target"], solvent, temperature, inputs)
        other_status = {
            polymer: _polymer_status(polymer, solvent, temperature, inputs)
            for polymer in inputs["others"]
        }
        other_ok = all(
            item["status"] not in {
                "dissolving", "non_dissolving_above_swelling_window",
                "unsupported_pair", "unsupported_polymer",
            }
            for item in other_status.values()
        )
        passes = (
            miscible and positive and target["status"] not in {
                "dissolving", "non_dissolving_above_swelling_window",
                "unsupported_pair", "unsupported_polymer",
            } and other_ok and not inputs["missing_others"]
        )
        caveats = []
        if target["status"] == "non_dissolving_proxy_swelling_candidate":
            caveats.append(
                "polymer swelling is proxy-inferred from "
                f"{inputs['swelling_min_wt_pct']:g}-{inputs['swelling_max_wt_pct']:g} "
                "wt% modeled solubility, not measured"
            )
        elif target["status"] == "non_dissolving_above_swelling_window":
            caveats.append(
                "polymer is modeled above the active swelling-window maximum; "
                "the intact-polymer leaching screen rejects this candidate"
            )
        elif target["status"] == "non_dissolving_low_swelling_confidence":
            caveats.append(
                "polymer remains modeled below "
                f"{inputs['swelling_min_wt_pct']:g} wt%; swelling evidence is weak"
            )
        rows.append({
            "solvent": solvent, "passes": passes, "mode": "leaching",
            "operating_temperature_c": temperature,
            "operating_temperature_basis": (
                "requested or capped temperature, a grid node" if temperature == wanted
                else f"highest solubility-grid node at or below {wanted:g} °C (the boiling-point or requested cap)"
            ),
            "boiling_point_c": thermo.get_boiling_point(solvent),
            "contaminant_miscibility_pass": miscible,
            "contaminant_logd_pass": positive, "contaminant_logd_min": minimum,
            "contaminants": contaminants, "target_polymer_status": target["status"],
            "target_polymer_solubility_wt_pct": target["solubility_wt_pct"],
            "other_polymer_status": other_status, "caveats": caveats,
        })
    result = _base_result(inputs, "leaching", rows)
    configured_defaults = (
        inputs["swelling_min_wt_pct"] == _DEFAULT_SWELLING_MIN
        and inputs["swelling_max_wt_pct"] == _DEFAULT_SWELLING_MAX
        and inputs["dissolution_min_wt_pct"] == _DEFAULT_DISSOLUTION_MIN
    )
    polymer_decision = (
        "the polymer remains below the 10 wt% dissolution proxy"
        if configured_defaults
        else "the polymer remains below the active dissolution proxy and does "
        "not exceed the active swelling-window maximum"
    )
    informed = any(
        item.get("field_origin") == _FIELD_ORIGIN_COMPUTED
        for row in rows
        for item in row.get("contaminants") or []
    )
    result.update({
        "decision_basis": [
            "all requested supported contaminants are miscible",
            (
                "computed Δ logD with field_origin=computed informed missing table cells; "
                "tabulated logD still wins when present"
                if informed else
                "all workbook logD values are positive"
            ),
            polymer_decision,
        ],
        "warnings": [
            "Workbook miscibility and logD are screening inputs, not validated process partition coefficients or removal efficiency.",
            "Leaching-mode swelling is inferred from modeled polymer solubility, not measured swelling.",
            "A passing screen does not establish extraction recovery, kinetics, solvent loading, or product purity.",
            *_threshold_warnings(inputs, include_precipitation=False),
            *(
                [
                    "A computed Δ logD informed this screen; "
                    "field_origin=computed is not tabulated logD."
                ]
                if any(
                    item.get("field_origin") == _FIELD_ORIGIN_COMPUTED
                    for row in rows
                    for item in row.get("contaminants") or []
                )
                else []
            ),
        ],
    })
    return result


def _precipitation_window(
    target: str, solvent: str, others: Sequence[str], maximum: Optional[float],
    *, precipitation_threshold_wt_pct: float,
    dissolution_min_wt_pct: float,
) -> dict[str, Any] | None:
    if solvent not in thermo.get_available_solvents_for_polymer(target):
        return None
    upper = min(_upper(solvent, maximum), _MAX_T)
    if upper < 25.0:
        return None
    curve = thermo.get_solubility_curve(target, solvent, 25.0, _MAX_T, 5.0)
    values = [(float(row["temperature"]), float(row["solubility"])) for row in curve]
    below_one = [
        temperature for temperature, value in values
        if value < precipitation_threshold_wt_pct
    ]
    if not below_one:
        return None
    precipitation = max(below_one)
    above_fifty = [temperature for temperature, value in values if value >= 50.0]
    below_fifty = [temperature for temperature, value in values if value < 50.0]
    cloud = max(below_fifty) if above_fifty and below_fifty else 0.0
    best = None
    for temperature in range(25, int(upper) + 1, 5):
        target_result = thermo.get_solubility_result(
            target, solvent, float(temperature),
        )
        value = target_result.get("solubility_pct")
        if value is None or value < dissolution_min_wt_pct or precipitation >= temperature:
            continue
        other_status = {}
        for polymer in others:
            other_result = thermo.get_solubility_result(
                polymer, solvent, float(temperature),
            )
            other_value = other_result.get("solubility_pct")
            status = (
                "unsupported_pair" if other_value is None else
                "undissolved" if other_value <= precipitation_threshold_wt_pct
                else "dissolving"
            )
            other_status[polymer] = {
                "status": status,
                "solubility_wt_pct": other_value,
            }
        if any(item["status"] != "undissolved" for item in other_status.values()):
            continue
        candidate = {
            "operating_temperature_c": float(temperature),
            "target_polymer_solubility_wt_pct": float(value),
            "precipitation_temperature_c": float(precipitation),
            "cloud_point_c": float(cloud),
            "other_polymer_status": other_status,
            "contains_extrapolated_evidence": False,
        }
        candidate_priority = (
            not candidate["contains_extrapolated_evidence"],
            value,
        )
        best_priority = (
            not best["contains_extrapolated_evidence"],
            best["target_polymer_solubility_wt_pct"],
        ) if best is not None else None
        if best_priority is None or candidate_priority > best_priority:
            best = candidate
    return best


def _strap(inputs: dict[str, Any]) -> dict[str, Any]:
    candidate_source = inputs["solvents"] or sorted(
        set(_solvent_names(inputs["supported"]))
        & set(thermo.get_available_solvents_for_polymer(inputs["target"]))
    )
    rows = []
    for solvent in list(dict.fromkeys(_key(item) for item in candidate_source)):
        window = _precipitation_window(
            inputs["target"], solvent, inputs["others"], inputs["maximum"],
            precipitation_threshold_wt_pct=inputs["precipitation_threshold_wt_pct"],
            dissolution_min_wt_pct=inputs["dissolution_min_wt_pct"],
        )
        if window is None:
            rows.append({
                "solvent": solvent, "passes": False, "mode": "strap_contaminant_removal",
                "operating_temperature_c": None, "boiling_point_c": thermo.get_boiling_point(solvent),
                "target_polymer_status": "no_feasible_dissolution_precipitation_window",
                "other_polymer_status": {}, "contaminant_miscibility_pass": False,
                "contaminant_precipitation_regime_pass": False,
                "contaminant_logd_pass": False, "contaminant_logd_min": None,
                "contaminants": [], "caveats": ["no modeled dissolution/cooling window"],
                "unspecified_not_a_strap_basis": False,
            })
            continue
        dissolution_regime = _regime(solvent, window["operating_temperature_c"])
        precipitation_regime = _regime(solvent, window["precipitation_temperature_c"])
        contaminants, minimum, hot_miscible, positive = _contaminant_rows(
            solvent, inputs["supported"], dissolution_regime,
        )
        cold_contaminants, _, cold_miscible, _ = _contaminant_rows(
            solvent, inputs["supported"], precipitation_regime,
        )
        unspecified_only = _unspecified_only_basis(solvent, inputs["supported"])
        rows.append({
            "solvent": solvent,
            "passes": (
                hot_miscible and cold_miscible and positive
                and not inputs["missing_others"]
                and not unspecified_only
            ),
            "mode": "strap_contaminant_removal", **window,
            "boiling_point_c": thermo.get_boiling_point(solvent),
            "target_polymer_status": "dissolving_then_precipitating",
            "contaminant_miscibility_pass": hot_miscible,
            "contaminant_precipitation_regime_pass": cold_miscible,
            "contaminant_logd_pass": positive, "contaminant_logd_min": minimum,
            "contaminants": contaminants,
            "precipitation_regime_contaminants": cold_contaminants,
            "unspecified_not_a_strap_basis": unspecified_only,
            "caveats": (
                ["unspecified is not a STRAP temperature-regime basis"]
                if unspecified_only else []
            ),
        })
    result = _base_result(inputs, "strap_contaminant_removal", rows)
    configured_defaults = (
        inputs["swelling_min_wt_pct"] == _DEFAULT_SWELLING_MIN
        and inputs["swelling_max_wt_pct"] == _DEFAULT_SWELLING_MAX
        and inputs["dissolution_min_wt_pct"] == _DEFAULT_DISSOLUTION_MIN
        and inputs["precipitation_threshold_wt_pct"]
        == _DEFAULT_PRECIPITATION_THRESHOLD
    )
    result.update({
        "min_dissolution_solubility_wt_pct": inputs["dissolution_min_wt_pct"],
        "precipitation_threshold_wt_pct": inputs["precipitation_threshold_wt_pct"],
        "decision_basis": [
            (
                "target polymer reaches at least 10 wt% modeled solubility"
                if configured_defaults else
                "target polymer reaches the active minimum modeled dissolution capacity"
            ),
            *(
                [(
                    "non-target polymers remain at or below 1 wt%"
                    if configured_defaults else
                    "non-target polymers remain at or below the active "
                    "precipitation proxy"
                )]
                if inputs["others"] else []
            ),
            (
                "target polymer crosses below the 1 wt% precipitation proxy on cooling"
                if configured_defaults else
                "target polymer crosses below the active precipitation proxy on cooling"
            ),
            "contaminants remain miscible with positive workbook logD",
        ],
        "warnings": [
            (
                "The dissolution and 1 wt% cooling thresholds are stored-grid screening proxies, not validated precipitation recovery."
                if configured_defaults else
                "The active dissolution and cooling thresholds are stored-grid screening proxies, not validated precipitation recovery."
            ),
            "Workbook miscibility and logD are screening inputs, not validated process partition coefficients.",
            "A passing screen does not establish kinetics, solvent loading, contaminant removal efficiency, or product purity.",
            *_threshold_warnings(inputs, include_precipitation=True),
        ],
    })
    return result


def _tool_result(tool: str, result: dict[str, Any]) -> str:
    return tool_success(tool, **result)


def screen_contaminant_leaching(
    target_polymer: str, contaminants: str | list[str],
    other_polymers: str | list[str] | None = None,
    solvents: str | list[str] | None = None,
    max_temperature_c: Optional[float] = None,
    strict_maximum: bool = False,
    swelling_min_wt_pct: Optional[float] = None,
    swelling_max_wt_pct: Optional[float] = None,
    dissolution_min_wt_pct: Optional[float] = None,
    computed_deltas: Sequence[Mapping[str, Any]] | Mapping[str, Any] | None = None,
) -> str:
    """Screen leaching. Computed Δ may inform only with field_origin. Never write logd."""
    tool = "screen_contaminant_leaching"
    inputs, error = _inputs(
        tool, target_polymer, contaminants, other_polymers, solvents,
        max_temperature_c, strict_maximum, swelling_min_wt_pct,
        swelling_max_wt_pct, dissolution_min_wt_pct,
    )
    if error:
        return error
    inputs["computed_deltas"] = computed_deltas
    return _tool_result(tool, _leaching(inputs))


def screen_contaminant_strap_removal(
    target_polymer: str, contaminants: str | list[str],
    other_polymers: str | list[str] | None = None,
    solvents: str | list[str] | None = None,
    max_temperature_c: Optional[float] = None,
    strict_maximum: bool = False,
    swelling_min_wt_pct: Optional[float] = None,
    swelling_max_wt_pct: Optional[float] = None,
    dissolution_min_wt_pct: Optional[float] = None,
    precipitation_threshold_wt_pct: Optional[float] = None,
) -> str:
    """Screen a swing with optional active swelling/dissolution/cooling proxies."""
    tool = "screen_contaminant_strap_removal"
    inputs, error = _inputs(
        tool, target_polymer, contaminants, other_polymers, solvents,
        max_temperature_c, strict_maximum, swelling_min_wt_pct,
        swelling_max_wt_pct, dissolution_min_wt_pct,
        precipitation_threshold_wt_pct, include_precipitation=True,
    )
    if error:
        return error
    result = _strap(inputs)
    candidates = result.get("candidate_solvents") or []
    if candidates and all(
        row.get("unspecified_not_a_strap_basis") for row in candidates
    ):
        available, leaching_solvents = _leaching_basis(inputs["supported"])
        return tool_error(
            tool,
            "Every strap candidate has only unspecified miscibility; "
            "unspecified is not a STRAP temperature-regime basis.",
            error_code="unspecified_not_a_strap_basis",
            target_polymer=inputs["target"],
            other_polymers=inputs["others"],
            requested_contaminants=inputs["requested"],
            supported_contaminants=inputs["supported"],
            unsupported_contaminants=inputs["unsupported"],
            solvents=[row.get("solvent") for row in candidates],
            leaching_basis_available=available,
            leaching_basis_solvents=leaching_solvents,
            **_served_threshold_fields(inputs),
            provenance=_provenance(),
        )
    return _tool_result(tool, result)


def compare_contaminant_removal_modes(
    target_polymer: str, contaminants: str | list[str],
    other_polymers: str | list[str] | None = None,
    solvents: str | list[str] | None = None,
    max_temperature_c: Optional[float] = None,
    strict_maximum: bool = False,
    swelling_min_wt_pct: Optional[float] = None,
    swelling_max_wt_pct: Optional[float] = None,
    dissolution_min_wt_pct: Optional[float] = None,
    precipitation_threshold_wt_pct: Optional[float] = None,
) -> str:
    """Compare modes under one validated active threshold basis."""
    tool = "compare_contaminant_removal_modes"
    inputs, error = _inputs(
        tool, target_polymer, contaminants, other_polymers, solvents,
        max_temperature_c, strict_maximum, swelling_min_wt_pct,
        swelling_max_wt_pct, dissolution_min_wt_pct,
        precipitation_threshold_wt_pct, include_precipitation=True,
    )
    if error:
        return error
    leaching, strap = _leaching(inputs), _strap(inputs)
    left, right = len(leaching["recommended_solvents"]), len(strap["recommended_solvents"])
    mode = "leaching" if left > right else "strap_contaminant_removal" if right > left else "tie"
    summaries = {
        item["mode"]: {
            "passing_count": len(item["recommended_solvents"]),
            "recommended_solvents": item["recommended_solvents"][:10],
            "top_candidates": item["candidate_solvents"][:3],
        }
        for item in (leaching, strap)
    }
    return tool_success(
        tool, analysis_type="contaminant_screen_analysis",
        mode="comparison", target_polymer=inputs["target"],
        other_polymers=inputs["others"], requested_contaminants=inputs["requested"],
        unsupported_other_polymers=inputs["missing_others"],
        temperature_max_c=(
            inputs["requested_maximum"]
            if inputs["requested_maximum"] is not None else _MAX_T
        ),
        highest_evaluated_temperature_c=(
            inputs["maximum"] if inputs["maximum"] is not None else _MAX_T
        ),
        strict_maximum=inputs["strict_maximum"],
        contaminants=inputs["supported"], supported_contaminants=inputs["supported"],
        contaminant_catalog=_contaminant_catalog(inputs["supported"]),
        n_supported_contaminants=len(inputs["supported"]),
        unsupported_contaminants=inputs["unsupported"],
        contaminant_families=inputs["families"], recommended_mode=mode,
        min_dissolution_solubility_wt_pct=inputs["dissolution_min_wt_pct"],
        **_served_threshold_fields(inputs),
        recommended_solvents={
            "leaching": leaching["recommended_solvents"],
            "strap_contaminant_removal": strap["recommended_solvents"],
        },
        mode_summaries=summaries, leaching=leaching, strap_contaminant_removal=strap,
        stage_count=(
            len(leaching["candidate_solvents"])
            + len(strap["candidate_solvents"])
        ),
        family_record_count=(
            (
                len(leaching["candidate_solvents"])
                + len(strap["candidate_solvents"])
            ) * len(inputs["families"])
        ),
        criterion_record_count=(
            (
                len(leaching["candidate_solvents"])
                + len(strap["candidate_solvents"])
            ) * len(inputs["supported"])
        ),
        provenance=_provenance(),
        model_basis=(
            "Zhou workbook miscibility/logD plus grid-first thermodynamic "
            "screening proxies from stored values"
        ),
        warnings=list(dict.fromkeys(leaching["warnings"] + strap["warnings"])),
    )


def feed_state_at_step(
    *,
    feed_order: Sequence[str],
    remaining: Sequence[str],
    contaminants: Sequence[str],
) -> dict[str, Any]:
    """Polymers still in the solid, feed order. No residual-mass update."""
    present = {str(item) for item in remaining}
    return {
        "polymers": [str(name) for name in feed_order if str(name) in present],
        "contaminants": [str(item) for item in contaminants],
        "inventory_model": "none",
    }


def others_from_feed_state(
    feed_state: dict[str, Any],
    target_polymer: str,
) -> list[str]:
    """Others are remaining solid polymers minus the named target."""
    target = str(target_polymer)
    polymers = [
        str(name) for name in (feed_state or {}).get("polymers") or [] if str(name)
    ]
    return [name for name in polymers if name != target]


def evaluate_contaminant_at_feed_state(
    path: str,
    target_polymer: str,
    others: Sequence[str],
    contaminants: str | Sequence[str],
    solvents: str | Sequence[str] | None = None,
    *,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
) -> dict[str, Any]:
    """One evaluator for leaching washes and later STRAP stamps."""
    token = _key(path)
    if token == "swing":
        token = "strap"
    kwargs: dict[str, Any] = dict(
        target_polymer=target_polymer,
        contaminants=contaminants,
        other_polymers=list(others),
        solvents=solvents,
        max_temperature_c=temperature_max_c,
        strict_maximum=strict_maximum,
    )
    if token == "leaching":
        raw = screen_contaminant_leaching(**kwargs)
    elif token == "strap":
        raw = screen_contaminant_strap_removal(**kwargs)
    else:
        return parse_tool_result(tool_error(
            "evaluate_contaminant_at_feed_state",
            "path must be leaching or strap.",
            error_code="invalid_contaminant_path",
            requested=path,
        ))["data"]
    return parse_tool_result(raw)["data"]


# --- PlastChem, promoted from the openCOSMO-RS campaign. Every accepted PlastChem contaminant is partitioned between
# each campaign polymer and each panel solvent (neutral species, 298.15 K) and checked for liquid-liquid miscibility
# with each solvent at room temperature and at the workbook's high temperature. plastchem_release builds the asset
# from a sealed release (this module only reads it); until then the screen refuses with plastchem_data_unavailable.

_PLASTCHEM_ASSET = Path(str(files("dissolve").joinpath("data/plastchem_opencosmo.duckdb")))
_PLASTCHEM_FAMILIES = Path(str(files("dissolve").joinpath("data/plastchem_families.json")))
_SERVED_CONVENTION = "normalized"  # owner, 2026-09-23 ("may revisit"); the existing convention is stored beside it
_MISCIBLE_BASIS = "15 wt%"  # the paper's cutoff; the owner set its basis to wt% (2026-09-23)
_PLASTCHEM_THRESHOLDS = {
    "swelling_min_wt_pct": _DEFAULT_SWELLING_MIN,
    "swelling_max_wt_pct": _DEFAULT_SWELLING_MAX,
    "dissolution_min_wt_pct": _DEFAULT_DISSOLUTION_MIN,
}


def _in_plastchem(names: Sequence[str]) -> list[str]:
    """The names the PlastChem screen computes, contaminants or families. A workbook screen that refuses them says so,
    so the agent can turn to it: "compare removal of bisphenol A from HDPE" stopped at the workbook's refusal
    (2026-09-24)."""
    con = _plastchem()
    if con is None:
        return []
    covered = []
    for name in names:
        family = _plastchem_family(name)
        if family is not None:
            hit = con.execute("SELECT 1 FROM contaminants WHERE computed AND inchikey IN (SELECT unnest(?)) LIMIT 1",
                              [family["members"]]).fetchone()
        else:
            hit = con.execute("SELECT 1 FROM aliases a JOIN contaminants c ON c.id = a.id WHERE a.alias = ? "
                              "AND c.computed LIMIT 1", [_key(name)]).fetchone()
        if hit:
            covered.append(str(name))
    return covered


@lru_cache(maxsize=1)
def _family_table() -> tuple[dict[str, Any], ...]:
    """The contaminant families a user can search by, built by `plastchem_release --families` from the PlastChem
    workbook: plain names ("bisphenols", "UV stabilizers"), aliases, and members by InChIKey, with the PlastChem
    entries the release lacks and why."""
    if not _PLASTCHEM_FAMILIES.is_file():
        return ()
    return tuple(json.loads(_PLASTCHEM_FAMILIES.read_text(encoding="utf-8"))["families"])


def _plastchem_family(name: Any) -> dict[str, Any] | None:
    """The family a name asks for, or None. "All bisphenols", "the bisphenol family" and "Bisphenols" are one."""
    key = _key(name)
    key = key.removeprefix("all ").removeprefix("the ")
    for suffix in (" family", " class", " group"):
        key = key.removesuffix(suffix)
    for family in _family_table():
        names = {_key(family["name"]), _key(family["term"]), *map(_key, family["aliases"])}
        if key in names:
            return family
    return None


def _cas_like(name: str) -> bool:
    """A bare CAS number, which PlastChem uses as the name of an entry PubChem did not name."""
    parts = name.split("-")
    return len(parts) == 3 and all(part.isdigit() for part in parts)


def _family_coverage(family: dict[str, Any], rows: list[dict[str, Any]],
                     not_computed: list[dict[str, Any]]) -> dict[str, Any]:
    """How much of a family a screen covered: members screened, members the campaign has not computed, and the
    PlastChem entries the release lacks, counted by reason, with a few named."""
    keys = set(family["members"])
    outside = family["outside_release"]
    reasons: dict[str, int] = {}
    for item in outside:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    named = sorted((item for item in outside if not _cas_like(item["name"])), key=lambda item: len(item["name"]))
    return {
        "family": family["name"], "description": family["description"], "basis": family["basis"],
        "screened": sum(row["inchikey"] in keys for row in rows),
        "not_computed": sum(item["inchikey"] in keys for item in not_computed),
        "outside_release": len(outside), "outside_release_by_reason": reasons,
        "outside_release_examples": [f"{item['name']} ({item['reason']})" for item in named[:5]],
    }


def contaminant_families() -> list[dict[str, Any]]:
    """The families a user can search contaminants by, for pickers: each PlastChem family with the members the release
    computed, then any workbook family the release has none of (PFAS), which the workbook screens serve."""
    con = _plastchem()
    out = []
    for family in _family_table():
        members = [name for (name,) in con.execute(
            "SELECT name FROM contaminants WHERE computed AND inchikey IN (SELECT unnest(?)) ORDER BY lower(name)",
            [family["members"]]).fetchall()] if con is not None else []
        out.append({"name": family["name"], "term": family["term"], "description": family["description"],
                    "aliases": family["aliases"], "examples": family["examples"], "count": len(members),
                    "members": members, "source": "plastchem"})
    covered = {_key(alias) for item in out for alias in (item["name"], item["term"], *item["aliases"])}
    for family, names in _families().items():
        if _key(family) not in covered:
            out.append({"name": family, "term": family, "aliases": [], "examples": names[:3], "count": len(names),
                        "description": "in the COSMOtherm workbook screens; the PlastChem release has none",
                        "members": names, "source": "workbook"})
    return out


def _plastchem() -> duckdb.DuckDBPyConnection | None:
    """The promoted asset, read-only; DISSOLVE_PLASTCHEM_ASSET points elsewhere (tests, a staged release)."""
    path = Path(os.environ.get("DISSOLVE_PLASTCHEM_ASSET") or _PLASTCHEM_ASSET)
    cached = getattr(_LOCAL, "plastchem", None)
    if cached and cached[0] == path:
        return cached[1]
    if not path.is_file():
        return None
    connection = duckdb.connect(str(path), read_only=True)
    _LOCAL.plastchem = (path, connection)
    return connection


LOG_DISPLAY_BOUND = 6.0


def bounded_log(value: Any, figures: int | None = 3) -> Any:
    """A log10 partition value as shown: past ±6 its bound, where the substance is effectively all in one phase;
    otherwise the value to `figures` significant figures (None keeps it). Significant figures, not decimals, keep a
    tiny positive value positive beside a strict-sign verdict."""
    if value is None or isinstance(value, (str, bool)):
        return value
    if value > LOG_DISPLAY_BOUND:
        return f"> {LOG_DISPLAY_BOUND:g}"
    if value < -LOG_DISPLAY_BOUND:
        return f"< -{LOG_DISPLAY_BOUND:g}"
    return value if figures is None else float(f"{value:.{figures}g}")


def _partition_coefficient(logp: Any) -> Any:
    """K = 10^logP to two significant figures, or its bound."""
    if logp is None:
        return None
    if abs(logp) > LOG_DISPLAY_BOUND:
        return f"{'>' if logp > 0 else '<'} 10^{'' if logp > 0 else '-'}{LOG_DISPLAY_BOUND:g}"
    return float(f"{10 ** logp:.2g}")


def _logp_range(values: list[Any]) -> dict[str, Any] | None:
    ordered = sorted(value for value in values if value is not None)
    if not ordered:
        return None
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return {"min": bounded_log(ordered[0]), "median": bounded_log(median), "max": bounded_log(ordered[-1]),
            "count": len(ordered)}


def _leaching_verdict(logp: Any, miscible: Any, polymer_status: str) -> str:
    if polymer_status == "dissolving":
        return "polymer dissolves"
    if logp is not None and logp <= 0:
        return "stays in polymer"
    if miscible is False:
        return "not miscible"
    if logp is None or miscible is None or polymer_status.startswith("unsupported"):
        return "undetermined"
    return "leaches"


def _plastchem_solvent(con: duckdb.DuckDBPyConnection, solvent: str, tool: str) -> tuple[str | None, str | None]:
    """The panel's key for a solvent, or a refusal that names the panel and its close relatives: "xylene" is mixed or
    p-xylene, and the panel computed o-xylene (2026-09-25)."""
    panel = [row[0] for row in con.execute("SELECT DISTINCT solvent FROM partition ORDER BY 1").fetchall()]
    keys = set(_solvent_keys(solvent)) | {_key(solvent)}
    solvent_key = next((key for key in panel if key in keys), None)
    if solvent_key is not None:
        return solvent_key, None
    def words(name: str) -> set[str]:
        return {word for word in name.replace("-", " ").replace(",", " ").split() if len(word) > 3}

    asked = words(_key(solvent))  # a word inside another counts: "xylenes" and "o-xylene", "propanol" and "isopropanol"
    close = [key for key in panel if any(a in w or w in a for a in asked for w in words(key))]
    detail = f" Close panel solvents: {', '.join(close)}." if close else ""
    return None, tool_error(tool, f"{solvent} is not in the {len(panel)}-solvent panel.{detail}",
                            error_code="solvent_not_in_panel", requested_solvent=solvent, panel_solvents=panel,
                            closest_panel_solvents=close)


def _requested(contaminants: str | list[str] | None) -> list[str]:
    requested = list(contaminants or []) if not isinstance(contaminants, str) else [contaminants]
    if isinstance(contaminants, str) and contaminants.lstrip().startswith("["):
        try:  # providers serialize arrays as JSON strings; names like "1,6-hexanediyl dioleate" are never comma-split
            requested = [str(item) for item in json.loads(contaminants)]
        except json.JSONDecodeError:
            pass
    return requested


def _resolve_plastchem(con: duckdb.DuckDBPyConnection, requested: Sequence[str]
                       ) -> tuple[list[int], list[dict[str, Any]], list[str], dict[str, list[str]]]:
    """Release ids for names, CAS numbers, InChIKeys, abbreviations and families; with the families asked for, the
    names nothing matched, and the names that match more than one contaminant."""
    chosen, families, unknown, ambiguous = [], [], [], {}
    for item in requested:
        family = _plastchem_family(item)
        if family is not None:  # every member the release holds; those it did not compute are listed as such
            families.append(family)
            chosen.extend(row[0] for row in con.execute(
                "SELECT id FROM contaminants WHERE inchikey IN (SELECT unnest(?))", [family["members"]]).fetchall())
            continue
        hits = [row[0] for row in con.execute("SELECT DISTINCT id FROM aliases WHERE alias = ?",
                                              [_key(item)]).fetchall()]
        if len(hits) > 1:
            ambiguous[str(item)] = [row[0] for row in con.execute(
                "SELECT name || ' (' || inchikey || ')' FROM contaminants WHERE id IN (SELECT unnest(?))", [hits]).fetchall()]
        elif hits:
            chosen.append(hits[0])
        else:
            unknown.append(str(item))
    return chosen, families, unknown, ambiguous


def lookup_plastchem_contaminants(contaminants: str | list[str], solvent: str | None = None) -> str:
    """Look up PlastChem contaminants by name, CAS number, InChIKey, abbreviation or family (phthalates, antioxidants, ...): each one's name, CAS number, InChIKey, SMILES, molecular weight and families, and whether the openCOSMO-RS release computed it; with a solvent, also its miscibility with that solvent and its logP between the solvent and every polymer, no polymer needed."""
    tool = "lookup_plastchem_contaminants"
    con = _plastchem()
    if con is None:
        return tool_error(tool, "The PlastChem contaminant release has not been promoted into DISSOLVE yet.",
                          error_code="plastchem_data_unavailable")
    requested = _requested(contaminants)
    if not requested:
        return tool_error(tool, "Name at least one contaminant or family.", error_code="no_contaminants_named")
    solvent_key = None
    if solvent:
        solvent_key, refusal = _plastchem_solvent(con, solvent, tool)
        if refusal is not None:
            return refusal
    chosen, families, unknown, ambiguous = _resolve_plastchem(con, requested)
    member_of: dict[str, list[str]] = {}
    for family in _family_table():
        for key in family["members"]:
            member_of.setdefault(key, []).append(family["name"])
    rows = [{"contaminant": name, "cas": cas, "inchikey": key, "smiles": smiles,
             "molecular_weight_g_mol": None if weight is None else round(weight, 2), "families": member_of.get(key, []),
             "computed": bool(computed), "campaign_status": status}
            for name, cas, key, smiles, weight, computed, status in con.execute(
                """SELECT name, cas, inchikey, smiles, molecular_weight, computed, status FROM contaminants
                   WHERE id IN (SELECT unnest(?)) ORDER BY lower(name)""", [sorted(set(chosen))]).fetchall()]
    done = [row for row in rows if row["computed"]]
    pending = [row for row in rows if not row["computed"]]
    coverage = [_family_coverage(family, done, pending) for family in families]
    solvent_fields: dict[str, Any] = {}
    if solvent_key is not None:
        _add_solvent_data(con, solvent_key, done)
        solvent_fields = {
            "solvent": solvent_key,
            "solvent_method": "miscibility: the contaminant and the solvent alone, one liquid phase at 15 wt% or not, at "
                              "25 °C and at the solvent's high temperature, with the solubility in wt% where two phases "
                              "form. logP: log10 of the solvent/polymer concentration ratio for the neutral species at "
                              "25 °C, positive favouring the solvent, one per polymer model; past ±6 the bound.",
        }
    return tool_success(
        tool,
        display=f"{len(rows)} PlastChem contaminants found, {len(done)} computed"
                + (f"; in {solvent_key}." if solvent_key else "."),
        rows=rows, found=len(rows), computed=len(done), unsupported_contaminants=unknown,
        ambiguous_contaminants=ambiguous, **({"family_coverage": coverage} if coverage else {}), **solvent_fields,
        identity_basis="PubChem's names, CAS numbers, InChIKeys and SMILES as the PlastChem release records them; "
                       "identities, not predictions",
    )


def _add_solvent_data(con: duckdb.DuckDBPyConnection, solvent_key: str, rows: list[dict[str, Any]]) -> None:
    """Give each computed row its miscibility with one solvent and its logP between that solvent and every polymer
    model. LDPE and HDPE share one polyethylene model, so they share a column."""
    labels: dict[str, list[str]] = {}
    for product, campaign in con.execute("SELECT product, campaign FROM polymers ORDER BY product").fetchall():
        labels.setdefault(campaign, []).append(product)
    label = {campaign: "/".join(products) for campaign, products in labels.items()}
    keys = [row["inchikey"] for row in rows]
    logp: dict[str, dict[str, Any]] = {}
    for key, polymer, value in con.execute(
            """SELECT c.inchikey, p.polymer, p.logp FROM partition p JOIN contaminants c ON c.id = p.id
               WHERE p.solvent = ? AND c.inchikey IN (SELECT unnest(?)) ORDER BY p.polymer""",
            [solvent_key, keys]).fetchall():
        logp.setdefault(key, {})[label.get(polymer, polymer)] = bounded_log(value)
    miscibility: dict[str, list[dict[str, Any]]] = {}
    for key, regime, temperature, status, miscible, weight in con.execute(
            """SELECT c.inchikey, l.regime, l.temperature_c, l.status, l.miscible, l.wt_percent FROM lle l
               JOIN contaminants c ON c.id = l.id WHERE l.solvent = ? AND c.inchikey IN (SELECT unnest(?))
               ORDER BY l.temperature_c""", [solvent_key, keys]).fetchall():
        miscibility.setdefault(key, []).append({
            "temperature_c": round(temperature, 1), "miscible_at_15_wt_pct": miscible,
            "solubility_wt_pct": weight if status == "two_liquid_phases" else None, "state": status})
    for row in rows:
        row["in_solvent"] = {"miscibility": miscibility.get(row["inchikey"], []),
                             "logp_solvent_over_polymer": logp.get(row["inchikey"], {})}


def screen_contaminant_partitioning(
    polymer: str, solvent: str, contaminants: str | list[str] | None = None, temperature_c: float = 25.0,
) -> str:
    """Screen PlastChem contaminants for leaching from one polymer into one solvent: all 5,830, those named, or whole families (phthalates, terephthalates, bisphenols, alkylphenols, antioxidants, UV stabilizers, benzophenones, aromatic amines, slip agents, salicylates, parabens)."""
    tool = "screen_contaminant_partitioning"
    con = _plastchem()
    if con is None:
        return tool_error(tool, "The PlastChem contaminant release has not been promoted into DISSOLVE yet.",
                          error_code="plastchem_data_unavailable")
    product = thermo.resolve_polymer(polymer) or str(polymer).strip().upper()
    mapped = con.execute("SELECT campaign, conformers, shared_model FROM polymers WHERE product = ?",
                         [product]).fetchone()
    if mapped is None:
        known = [row[0] for row in con.execute("SELECT product FROM polymers ORDER BY 1").fetchall()]
        return tool_error(tool, f"No PlastChem partitioning for {polymer}.", error_code="polymer_not_in_release",
                          requested_polymer=polymer, available_polymers=known)
    solvent_key, refusal = _plastchem_solvent(con, solvent, tool)
    if refusal is not None:
        return refusal
    requested = _requested(contaminants)
    if not requested or [_key(item) for item in requested] == ["all"]:
        chosen = [row[0] for row in con.execute("SELECT id FROM contaminants WHERE computed").fetchall()]
        families, unknown, ambiguous = [], [], {}
    else:
        chosen, families, unknown, ambiguous = _resolve_plastchem(con, requested)
    regimes = dict(con.execute("SELECT regime, max(temperature_c) FROM lle WHERE solvent = ? GROUP BY 1",
                               [solvent_key]).fetchall())
    high = regimes.get("high")
    regime = "high" if high is not None and temperature_c >= (25.0 + high) / 2.0 else "rt"
    state = _polymer_status(product, solvent_key, temperature_c, _PLASTCHEM_THRESHOLDS)
    rows, not_computed = [], []
    records = con.execute(
        """SELECT c.inchikey, c.name, c.cas, c.smiles, c.computed, c.status, c.reason, p.logp, p.status, l.miscible,
                  l.status, l.wt_percent
           FROM contaminants c
           LEFT JOIN partition p ON p.id = c.id AND p.solvent = ? AND p.polymer = ?
           LEFT JOIN lle l ON l.id = c.id AND l.solvent = ? AND l.regime = ?
           WHERE c.id IN (SELECT unnest(?))""",
        [solvent_key, mapped[0], solvent_key, regime, sorted(set(chosen))],
    ).fetchall()
    for inchikey, name, cas, smiles, computed, status, reason, logp, logp_status, miscible, lle_status, wt in records:
        if not computed:
            not_computed.append({"contaminant": name, "inchikey": inchikey, "campaign_status": status,
                                 "reason": reason})
            continue
        rows.append({
            "contaminant": name, "inchikey": inchikey, "cas": cas, "smiles": smiles,
            "logp_solvent_over_polymer": logp, "partition_coefficient_k": _partition_coefficient(logp),
            "partition_status": logp_status,
            "partitions_toward": None if logp is None else "solvent" if logp > 0 else "polymer",
            "miscible_at_15_wt_pct": miscible, "miscibility_state": lle_status,
            "contaminant_solubility_wt_pct": wt if lle_status == "two_liquid_phases" else None,
            "leaching_verdict": _leaching_verdict(logp, miscible, state["status"]),
        })
        if families:
            names = [family["name"] for family in families if inchikey in family["members"]]
            rows[-1]["family"] = ", ".join(names) or None
    order = {"leaches": 0, "undetermined": 1, "not miscible": 2, "stays in polymer": 3, "polymer dissolves": 4}
    rows.sort(key=lambda row: (order[row["leaching_verdict"]], -(row["logp_solvent_over_polymer"] or -1e9)))
    verdicts = {name: sum(row["leaching_verdict"] == name for row in rows) for name in order}
    logps = [row["logp_solvent_over_polymer"] for row in rows]
    by_verdict = {name: _logp_range([row["logp_solvent_over_polymer"] for row in rows if row["leaching_verdict"] == name])
                  for name in order if verdicts[name]}
    near_even = sum(logp is not None and abs(logp) < 0.5 for logp in logps)
    for row in rows:
        row["logp_solvent_over_polymer"] = bounded_log(row["logp_solvent_over_polymer"])
    meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
    asked = f" ({', '.join(family['term'] for family in families)})" if families else ""
    coverage = [_family_coverage(family, rows, not_computed) for family in families]
    return tool_success(
        tool,
        display=(f"{len(rows)} PlastChem contaminants{asked}, {product} into {solvent_key}: "
                 f"{verdicts['leaches']} leach."),
        polymer=product, polymer_model=f"{mapped[0]} oligomer ensemble ({mapped[1]} conformers)",
        polymer_model_shared_with_other_materials=bool(mapped[2]),
        solvent=solvent_key, temperature_c=temperature_c, logp_temperature_c=25.0,
        miscibility_regime=regime, miscibility_temperature_c=regimes.get(regime), miscibility_basis=_MISCIBLE_BASIS,
        polymer_state=state, polymer_state_basis="stored COSMO-RS polymer solubility grid (legacy)",
        served_convention=meta.get("served_convention"), release_status=meta.get("release_status"),
        evaluated=len(rows), verdict_counts=verdicts,
        partitions_toward_solvent=sum(row["partitions_toward"] == "solvent" for row in rows),
        logp_range=_logp_range(logps), logp_range_by_verdict=by_verdict, near_even_count=near_even,
        rows=rows, unsupported_contaminants=unknown, ambiguous_contaminants=ambiguous,
        not_computed_contaminants=not_computed, **({"family_coverage": coverage} if coverage else {}),
        coverage=("PlastChem compounds of carbon, hydrogen, nitrogen and oxygen, found by name, CAS number, InChIKey, "
                  "common abbreviation or family (" + ", ".join(f["term"] for f in _family_table()) + "). "
                  "Additives with halogens, sulfur, phosphorus, silicon or boron (PFAS, organophosphates, bisphenol S) "
                  "are outside it; the workbook screens cover 26 PFAS."),
        method="Leaches when logP(solvent/polymer) > 0, the contaminant is miscible with the solvent at 15 wt%, "
               "and the polymer does not dissolve; logP is for the neutral species at 25 °C. logP is log10 of the "
               "solvent/polymer concentration ratio and K = 10^logP. near_even_count counts |logP| < 0.5 "
               "(K 0.3-3), a nearly even split whatever the verdict. Past ±6 values are shown as bounds: "
               "effectively all in one phase.",
    )
