"""PFAS/phthalate removal screens and isolated contaminant specialist."""

from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional, Sequence

import duckdb

from . import session, thermodynamics as thermo
from .contracts import tool_error, tool_success

_ASSET = Path(str(files("dissolve").joinpath("data/contaminants.duckdb")))
_ASSET_SHA256 = "19e585e019ad0ad1aac6e31ff49b5d47477789a5903b4fc3821e2d16a8596721"
_LOCAL = threading.local()
_FAMILY_ALIASES = {
    "pfas": "PFAS",
    "per- and polyfluoroalkyl substances": "PFAS",
    "perfluoroalkyl substances": "PFAS",
    "phthalate": "Phthalates",
    "phthalates": "Phthalates",
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


@lru_cache(maxsize=1)
def _contaminant_lookup() -> dict[str, tuple[str, str]]:
    return {
        str(key): (str(name), str(family))
        for family, name, key in _connection().execute(
            "SELECT family, contaminant, contaminant_key FROM contaminants"
        ).fetchall()
    }


def _contaminant_catalog(contaminants: Sequence[str]) -> list[dict[str, str]]:
    """Attach the admitted family identity to every expanded contaminant."""
    lookup = _contaminant_lookup()
    return [
        {
            "contaminant": str(contaminant),
            "contaminant_family": lookup[_key(contaminant)][1],
        }
        for contaminant in contaminants
        if _key(contaminant) in lookup
    ]


def _provenance() -> dict[str, str]:
    """Return the single source-and-model qualification for screen evidence."""
    return {
        "source_dataset": "Zhou workbook miscibility and logD",
        "source_asset": "contaminants.duckdb",
        "source_asset_sha256": _ASSET_SHA256,
        "thermodynamic_basis": thermo.SOLUBILITY_MODEL_BASIS,
        "evidence_class": "screening_proxy",
    }


def _expand(requested: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    supported, unsupported, families = [], [], set()
    lookup = _contaminant_lookup()
    for item in requested:
        text = str(item).strip()
        family = _FAMILY_ALIASES.get(_key(text))
        if family:
            supported.extend(_families().get(family, []))
            families.add(family)
        elif _key(text) in lookup:
            name, family = lookup[_key(text)]
            supported.append(name)
            families.add(family)
        elif text:
            unsupported.append(text)
    return list(dict.fromkeys(supported)), list(dict.fromkeys(unsupported)), sorted(families)


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


def _solvent_keys(name: str) -> tuple[str, ...]:
    raw = _key(name)
    resolved = thermo.resolve_solvent(name)
    return tuple(dict.fromkeys(item for item in (raw, _key(resolved)) if item))


def _miscibility(solvent: str, contaminant: str, regime: str) -> dict[str, Any] | None:
    solvent_keys = _solvent_keys(solvent)
    if not solvent_keys:
        return None
    placeholders = ",".join("?" for _ in solvent_keys)
    rows = _connection().execute(
        f"""SELECT temperature_regime, temperature_c, boiling_point_c,
                   t_higher_c, miscible
            FROM miscibility
            WHERE contaminant_key=? AND
                  (solvent_key IN ({placeholders}) OR solvent_normalized IN ({placeholders}))
            ORDER BY CASE WHEN temperature_regime=? THEN 0 ELSE 1 END, rowid""",
        [_key(contaminant), *solvent_keys, *solvent_keys, regime],
    ).fetchall()
    if not rows:
        return None
    row = rows[0]
    return {
        "temperature_regime": row[0], "temperature_c": row[1],
        "boiling_point_c": row[2], "t_higher_c": row[3], "miscible": row[4],
    }


def _logd(solvent: str, contaminant: str) -> Optional[float]:
    solvent_keys = _solvent_keys(solvent)
    if not solvent_keys:
        return None
    placeholders = ",".join("?" for _ in solvent_keys)
    row = _connection().execute(
        f"""SELECT logd FROM logd WHERE contaminant_key=? AND
            (solvent_key IN ({placeholders}) OR solvent_normalized IN ({placeholders}))
            ORDER BY rowid LIMIT 1""",
        [_key(contaminant), *solvent_keys, *solvent_keys],
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _regime(solvent: str, temperature: Optional[float]) -> str:
    solvent_keys = _solvent_keys(solvent)
    if temperature is None or not solvent_keys:
        return "rt"
    placeholders = ",".join("?" for _ in solvent_keys)
    rows = _connection().execute(
        f"""SELECT t_higher_c FROM miscibility
            WHERE (solvent_key IN ({placeholders}) OR solvent_normalized IN ({placeholders}))
              AND t_higher_c IS NOT NULL""",
        [*solvent_keys, *solvent_keys],
    ).fetchall()
    higher = [float(row[0]) for row in rows if row[0] is not None]
    return "t_higher" if higher and temperature >= (25.0 + max(higher)) / 2.0 else "rt"


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
        key: "user" if supplied[key] else "default"
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
        "solubility_method": result["method"],
    }


def _contaminant_rows(solvent: str, contaminants: Sequence[str], regime: str) -> tuple[list[dict], Optional[float], bool, bool]:
    rows, minimum, all_miscible, all_positive = [], None, True, True
    lookup = _contaminant_lookup()
    for contaminant in contaminants:
        miscibility = _miscibility(solvent, contaminant, regime)
        logd = _logd(solvent, contaminant)
        miscible = miscibility.get("miscible") if miscibility else None
        all_miscible &= miscible is True
        all_positive &= CONTAMINANT_LOGD_CRITERION.passes(logd)
        if logd is not None:
            minimum = logd if minimum is None else min(minimum, logd)
        rows.append({
            "contaminant": contaminant,
            "contaminant_family": lookup[_key(contaminant)][1],
            "miscible": miscible, "logd": logd,
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
    target = thermo.resolve_polymer(target_polymer)
    requested = _items(contaminants)
    supported, unsupported, families = _expand(requested)
    others_requested = _items(other_polymers)
    others = [resolved for item in others_requested if (resolved := thermo.resolve_polymer(item))]
    missing_others = [item for item in others_requested if not thermo.resolve_polymer(item)]
    explicit_solvents = None if solvents is None else _items(solvents)
    candidate_solvents, _ = session.resolve_candidate_argument(
        explicit_solvents, _inherited_candidate_solvents(tool),
    )
    requested_maximum = _finite(max_temperature_c)
    strict = bool(strict_maximum and requested_maximum is not None)
    maximum = _grid_ceiling(requested_maximum, strict)
    if not target:
        return {}, tool_error(tool, f"Unsupported target polymer: {target_polymer}.", error_code="unsupported_polymer")
    if not supported:
        return {}, tool_error(
            tool, "None of the requested contaminants are supported.",
            error_code="unsupported_contaminants", target_polymer=target,
            other_polymers=others, unsupported_other_polymers=missing_others,
            requested_contaminants=requested,
            unsupported_contaminants=unsupported or requested,
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
        "missing_others": missing_others, "solvents": candidate_solvents,
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
        "contaminant_families": inputs["families"],
        "candidate_solvents": rows,
        "recommended_solvents": [row["solvent"] for row in rows if row["passes"]],
        "best_result_is_weak": not any(row["passes"] for row in rows),
        "stage_count": len(rows),
        "family_record_count": len(rows) * len(inputs["families"]),
        "criterion_record_count": len(rows) * len(inputs["supported"]),
        "model_basis": (
            "Zhou workbook miscibility/logD plus grid-first thermodynamic "
            "screening proxies with labelled fit extrapolation"
        ),
        "provenance": _provenance(),
    }
    if inputs["threshold_basis"] == "user_requested":
        result.update({
            "swelling_min_wt_pct": inputs["swelling_min_wt_pct"],
            "swelling_max_wt_pct": inputs["swelling_max_wt_pct"],
            "dissolution_min_wt_pct": inputs["dissolution_min_wt_pct"],
            "threshold_basis": inputs["threshold_basis"],
            "threshold_sources": inputs["threshold_sources"],
        })
    return result


def _leaching(inputs: dict[str, Any]) -> dict[str, Any]:
    candidates = inputs["solvents"] or _solvent_names(inputs["supported"])
    rows = []
    for solvent in list(dict.fromkeys(_key(item) for item in candidates)):
        upper = _upper(solvent, inputs["maximum"])
        regime = _regime(solvent, upper)
        temperature = upper if regime == "t_higher" else min(upper, 25.0)
        contaminants, minimum, miscible, positive = _contaminant_rows(
            solvent, inputs["supported"], regime,
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
            "boiling_point_c": thermo.get_boiling_point(solvent),
            "contaminant_miscibility_pass": miscible,
            "contaminant_logd_pass": positive, "contaminant_logd_min": minimum,
            "contaminants": contaminants, "target_polymer_status": target["status"],
            "target_polymer_solubility_wt_pct": target["solubility_wt_pct"],
            "solubility_method_by_polymer": {
                inputs["target"]: target["solubility_method"],
                **{
                    polymer: item["solubility_method"]
                    for polymer, item in other_status.items()
                    if item.get("solubility_method") is not None
                },
            } if target.get("solubility_method") is not None else {},
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
    result.update({
        "decision_basis": [
            "all requested supported contaminants are miscible",
            "all workbook logD values are positive",
            polymer_decision,
        ],
        "warnings": [
            "Workbook miscibility and logD are screening inputs, not validated process partition coefficients or removal efficiency.",
            "Leaching-mode swelling is inferred from modeled polymer solubility, not measured swelling.",
            "A passing screen does not establish extraction recovery, kinetics, solvent loading, or product purity.",
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
    methods_by_temperature = {
        float(row["temperature"]): str(row["method"])
        for row in curve
    }
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
                "solubility_method": other_result.get("method"),
                "temperature_use_regime": other_result.get(
                    "temperature_use_regime",
                ),
            }
        if any(item["status"] != "undissolved" for item in other_status.values()):
            continue
        regime_by_polymer = {
            target: target_result.get("temperature_use_regime"),
            **{
                polymer: item.get("temperature_use_regime")
                for polymer, item in other_status.items()
            },
        }
        candidate = {
            "operating_temperature_c": float(temperature),
            "target_polymer_solubility_wt_pct": float(value),
            "solubility_method_by_polymer": {
                target: target_result["method"],
                **{
                    polymer: item["solubility_method"]
                    for polymer, item in other_status.items()
                    if item.get("solubility_method") is not None
                },
            },
            "precipitation_temperature_c": float(precipitation),
            "precipitation_method": methods_by_temperature[float(precipitation)],
            "cloud_point_c": float(cloud),
            "cloud_point_method": (
                methods_by_temperature.get(float(cloud)) if cloud else None
            ),
            "other_polymer_status": other_status,
            "temperature_use_regime": thermo.aggregate_temperature_use_regimes(
                float(temperature), regime_by_polymer.values(),
            ),
            "contains_extrapolated_evidence": any(
                "extrapolation" in str(regime or "")
                for regime in regime_by_polymer.values()
            ),
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
        rows.append({
            "solvent": solvent,
            "passes": hot_miscible and cold_miscible and positive and not inputs["missing_others"],
            "mode": "strap_contaminant_removal", **window,
            "boiling_point_c": thermo.get_boiling_point(solvent),
            "target_polymer_status": "dissolving_then_precipitating",
            "contaminant_miscibility_pass": hot_miscible,
            "contaminant_precipitation_regime_pass": cold_miscible,
            "contaminant_logd_pass": positive, "contaminant_logd_min": minimum,
            "contaminants": contaminants,
            "precipitation_regime_contaminants": cold_contaminants, "caveats": [],
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
                "The dissolution and 1 wt% cooling thresholds are fitted-model screening proxies, not validated precipitation recovery."
                if configured_defaults else
                "The active dissolution and cooling thresholds are fitted-model screening proxies, not validated precipitation recovery."
            ),
            "Workbook miscibility and logD are screening inputs, not validated process partition coefficients.",
            "A passing screen does not establish kinetics, solvent loading, contaminant removal efficiency, or product purity.",
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
) -> str:
    """Screen leaching with optional user-defined swelling/dissolution proxies."""
    tool = "screen_contaminant_leaching"
    inputs, error = _inputs(
        tool, target_polymer, contaminants, other_polymers, solvents,
        max_temperature_c, strict_maximum, swelling_min_wt_pct,
        swelling_max_wt_pct, dissolution_min_wt_pct,
    )
    return error or _tool_result(tool, _leaching(inputs))


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
    return error or _tool_result(tool, _strap(inputs))


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
    threshold_metadata = (
        {
            "swelling_min_wt_pct": inputs["swelling_min_wt_pct"],
            "swelling_max_wt_pct": inputs["swelling_max_wt_pct"],
            "dissolution_min_wt_pct": inputs["dissolution_min_wt_pct"],
            "threshold_basis": inputs["threshold_basis"],
            "threshold_sources": inputs["threshold_sources"],
        }
        if inputs["threshold_basis"] == "user_requested" else {}
    )
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
        precipitation_threshold_wt_pct=inputs["precipitation_threshold_wt_pct"],
        **threshold_metadata,
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
            "screening proxies with labelled fit extrapolation"
        ),
        warnings=list(dict.fromkeys(leaching["warnings"] + strap["warnings"])),
    )


