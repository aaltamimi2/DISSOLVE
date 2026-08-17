"""Merged solvent-safety tools, terminal artifacts, and isolated specialist."""

from __future__ import annotations

import json
import math
import re
import socket
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Optional

import duckdb

from . import thermodynamics as thermo
from .contracts import tool_error, tool_success
from .session import (
    candidate_evidence, current_tool_session,
    resolve_candidate_argument,
)

_ASSET = Path(str(files("dissolve").joinpath("data/safety.duckdb")))
_ASSET_SHA256 = "88ce0d09ac28de17045702a8a283de6610b5fe1ab33aa5f90bf6e98edfd75a74"
_LOCAL = threading.local()
_HEADINGS = (
    "Flash Point", "Autoignition Temperature", "Vapor Pressure",
    "GHS Classification", "Non-Human Toxicity Values", "Biodegradation",
    "NIOSH Recommendations", "OSHA Standards",
)
_HEADING_ERROR_KEY = "_dissolve_heading_error"


# Preserve source positions for the remaining identity-ratcheted regex sites.







def _connection() -> duckdb.DuckDBPyConnection:
    connection = getattr(_LOCAL, "safety_connection", None)
    if connection is None:
        connection = duckdb.connect(str(_ASSET), read_only=True)
        _LOCAL.safety_connection = connection
    return connection


def _key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _round_sig(value: Any, significant: int = 5) -> Optional[float]:
    number = _number(value)
    if number is None or number == 0:
        return number
    places = significant - 1 - int(math.floor(math.log10(abs(number))))
    return round(number, places)


@lru_cache(maxsize=1_024)
def _local_properties(query: str) -> dict[str, Any]:
    connection = thermo.get_connection()
    candidates = [query]
    alias = connection.execute(
        "SELECT property_name, interp_key, bp_db_key, cas_number FROM solvent_aliases "
        "WHERE lower(alias)=lower(?) LIMIT 1", [query],
    ).fetchone()
    if alias:
        candidates.extend(value for value in alias if value)
    for candidate in dict.fromkeys(str(value).strip() for value in candidates if value):
        row = connection.execute(
            "SELECT solvent_name, cosmobase_name, cas_number, cid, boiling_point_c, "
            "hazard_temperature_c, logp, heat_capacity_j_g_k, energy_j_g "
            "FROM solvent_data WHERE lower(solvent_name)=lower(?) "
            "OR lower(cosmobase_name)=lower(?) OR lower(cas_number)=lower(?) LIMIT 1",
            [candidate, candidate, candidate],
        ).fetchone()
        if row:
            result = dict(zip((
                "name", "cosmobase_name", "cas_number", "cid", "boiling_point_c",
                "catalog_hazard_reference_c", "logp", "heat_capacity_j_g_k", "energy_j_g",
            ), row))
            admission = thermo.get_solvent_admission_record(query)
            if admission:
                result["cas_number"] = (
                    result.get("cas_number") or admission.get("cas_number")
                )
                result["cid"] = result.get("cid") or admission.get("cid")
                result["boiling_point_c"] = (
                    result.get("boiling_point_c")
                    if _number(result.get("boiling_point_c")) is not None
                    else admission.get("boiling_point_c")
                )
            return result
    return {"name": query}


@lru_cache(maxsize=1_024)
def _gscore(name: str, cas_number: str = "") -> dict[str, Any]:
    gsk_query = (
        "SELECT common_name, classification, cas_number, g_score, smiles "
        "FROM gsk_safety WHERE lower(common_name)=lower(?)"
    )
    gsk_parameters: list[str] = [name]
    if cas_number:
        gsk_query += " OR cas_number=?"
        gsk_parameters.append(cas_number)
    row = _connection().execute(
        gsk_query + " LIMIT 1", gsk_parameters,
    ).fetchone()
    if row:
        return {
            "name": row[0], "classification": row[1], "cas_number": row[2],
            "g_score": row[3], "smiles": row[4], "ml_predicted": False,
            "source": "GSK_dataset.csv",
        }
    green_query = (
        "SELECT name, cas_number, g_score, uncertainty, data_quality, smiles "
        "FROM green_solvent WHERE lower(name)=lower(?)"
    )
    green_parameters: list[str] = [name]
    if cas_number:
        green_query += " OR cas_number=?"
        green_parameters.append(cas_number)
    row = _connection().execute(
        green_query + " LIMIT 1", green_parameters,
    ).fetchone()
    return ({
        "name": row[0], "cas_number": row[1], "g_score": row[2],
        "uncertainty": row[3], "data_quality": row[4], "smiles": row[5],
        "ml_predicted": True, "source": "GreenSolventDB_10k.csv",
    } if row else {})


@lru_cache(maxsize=1)
def _curated_rows() -> tuple[dict[str, Any], ...]:
    cursor = _connection().execute("SELECT * FROM curated_safety")
    columns = [item[0] for item in cursor.description]
    return tuple(dict(zip(columns, row)) for row in cursor.fetchall())


def _curated(name: str, cas_number: str = "") -> dict[str, Any]:
    target = {_key(name), _key(cas_number)} - {""}
    for row in _curated_rows():
        aliases = str(row.get("aliases") or "").split("|")
        keys = {_key(row.get("canonical_name")), _key(row.get("cas_number")), *map(_key, aliases)}
        if target & keys:
            return dict(row)
    return {}


def _request_json(url: str) -> dict[str, Any]:
    for attempt in range(3):
        request = urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": "DISSOLVE/0.4"}
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            if attempt == 2:
                raise
            time.sleep(0.25 * (2 ** attempt))
    return {}


@lru_cache(maxsize=2_048)
def _heading(cid: int, heading: str) -> dict[str, Any]:
    url = (
        f"https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/{cid}/JSON"
        f"?heading={urllib.parse.quote(heading)}"
    )
    try:
        return _request_json(url)
    except Exception as error:
        if isinstance(error, (json.JSONDecodeError, UnicodeDecodeError)):
            failure_class = "parse"
        elif isinstance(error, urllib.error.HTTPError):
            failure_class = "http"
        elif (
            isinstance(error, (TimeoutError, socket.timeout))
            or (
                isinstance(error, urllib.error.URLError)
                and isinstance(error.reason, (TimeoutError, socket.timeout))
            )
        ):
            failure_class = "timeout"
        else:
            failure_class = "request"
        return {_HEADING_ERROR_KEY: {"failure_class": failure_class}}


def _strings(node: Any) -> list[str]:
    values: list[str] = []
    if isinstance(node, dict):
        for item in node.get("StringWithMarkup") or []:
            if isinstance(item, dict) and item.get("String"):
                values.append(str(item["String"]))
        if node.get("Number") is not None:
            number = node["Number"]
            values.append(f"{number} {node.get('Unit') or ''}".strip())
        for value in node.values():
            if isinstance(value, (dict, list)):
                values.extend(_strings(value))
    elif isinstance(node, list):
        for value in node:
            values.extend(_strings(value))
    return list(dict.fromkeys(re.sub(r"\s+", " ", value).strip() for value in values if value))


def _temperatures(values: list[str]) -> list[float]:
    result: list[float] = []
    for text in values:
        result.extend(float(value) for value in re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?C\b", text, re.I))
        result.extend(
            (float(value) - 32.0) * 5.0 / 9.0
            for value in re.findall(r"(-?\d+(?:\.\d+)?)\s*(?:°\s*)?F\b", text, re.I)
        )
    return result


_PRESSURE = re.compile(r"(\d+(?:\.\d+)?)\s*(mmhg|torr|kpa|pa|atm|bar)", re.I)


def _vapor_pressure(values: list[str]) -> tuple[Optional[float], Optional[float]]:
    for text in values:
        match = _PRESSURE.search(text)
        if not match:
            continue
        pressure, unit = float(match.group(1)), match.group(2).casefold()
        factors = {"mmhg": 0.133322, "torr": 0.133322, "pa": 0.001, "atm": 101.325, "bar": 100.0, "kpa": 1.0}
        temperatures = _temperatures([text])
        return pressure * factors[unit], temperatures[0] if temperatures else None
    return None, None


def _ghs(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"signal_word": None, "pictograms": [], "hazard_statements": []}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            name, value = node.get("Name"), node.get("Value") or {}
            strings = _strings(value)
            if name == "Signal" and strings:
                result["signal_word"] = strings[0]
            elif name == "GHS Hazard Statements":
                result["hazard_statements"].extend(strings)
            elif name == "Pictogram(s)":
                for item in value.get("StringWithMarkup") or []:
                    for markup in item.get("Markup") or []:
                        if markup.get("Extra"):
                            result["pictograms"].append(str(markup["Extra"]))
            for child in node.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(payload)
    result["pictograms"] = list(dict.fromkeys(result["pictograms"]))[:6]
    result["hazard_statements"] = list(dict.fromkeys(result["hazard_statements"]))[:6]
    return result


def _exposure_limit(payload: dict[str, Any], authority: str) -> Optional[dict[str, Any]]:
    strings = _strings(payload)
    if not strings:
        return None
    text = strings[0]
    ppm = re.search(r"(\d+(?:\.\d+)?)\s*ppm\b", text, re.I)
    mass = re.search(r"(\d+(?:\.\d+)?)\s*mg/(?:cu\s*m|m(?:3|³))\b", text, re.I)
    hours = re.search(r"(\d+(?:\.\d+)?)\s*(?:-|\s)?(?:hr|hour)\b", text, re.I)
    return {
        "authority": authority,
        "ppm": _number(ppm.group(1)) if ppm else None,
        "mg_m3": _number(mass.group(1)) if mass else None,
        "basis": f"{hours.group(1)}-hour TWA" if hours else None,
        "skin": bool(re.search(r"\bskin(?: designation)?\b", text, re.I)),
        "reported_text": text[:300],
    }


@lru_cache(maxsize=512)
def _pubchem(cid: int) -> dict[str, Any]:
    with ThreadPoolExecutor(max_workers=4) as pool:
        payloads = dict(zip(_HEADINGS, pool.map(lambda name: _heading(cid, name), _HEADINGS)))
    heading_errors = {
        name: dict(payload[_HEADING_ERROR_KEY])
        for name, payload in payloads.items()
        if isinstance(payload, dict)
        and isinstance(payload.get(_HEADING_ERROR_KEY), dict)
    }
    flash = _temperatures(_strings(payloads["Flash Point"]))
    autoignition = _temperatures(_strings(payloads["Autoignition Temperature"]))
    vapor, vapor_temp = _vapor_pressure(_strings(payloads["Vapor Pressure"]))
    toxicity = _strings(payloads["Non-Human Toxicity Values"])
    exposure_limits = [
        item for item in (
            _exposure_limit(payloads["NIOSH Recommendations"], "NIOSH REL"),
            _exposure_limit(payloads["OSHA Standards"], "OSHA PEL"),
        ) if item is not None
    ]
    return {
        "flash_point_c": min(flash) if flash else None,
        "autoignition_c": min(autoignition) if autoignition else None,
        "vapor_pressure_kpa": vapor,
        "vapor_pressure_temp_c": vapor_temp,
        "ghs": _ghs(payloads["GHS Classification"]),
        "ld50_values": [value for value in toxicity if "LD50" in value.upper()][:3],
        "lc50_values": [value for value in toxicity if "LC50" in value.upper()][:3],
        "biodegradation": _strings(payloads["Biodegradation"])[:3],
        "occupational_exposure_limits": exposure_limits,
        "failed_headings": [
            name for name, payload in payloads.items()
            if not payload or name in heading_errors
        ],
        "heading_errors": heading_errors,
    }


def _volatility(value: Optional[float]) -> str:
    if value is None:
        return "unknown"
    return "very high" if value >= 50 else "high" if value >= 10 else "moderate" if value >= 1 else "low"


def _assessment(
    operating: Optional[float], boiling: Optional[float], flash: Optional[float],
    autoignition: Optional[float], vapor: Optional[float], vapor_temp: Optional[float],
) -> dict[str, Any]:
    flags: list[str] = []
    boiling_margin = None if operating is None or boiling is None else boiling - operating
    auto_margin = None if operating is None or autoignition is None else autoignition - operating
    if operating is not None and boiling_margin is not None:
        if boiling_margin <= 0:
            flags.append("at_or_above_normal_boiling_point")
        elif boiling_margin <= 10:
            flags.append("near_normal_boiling_point")
    if operating is not None and flash is not None and operating >= flash:
        flags.append("above_flash_point")
    if operating is not None and auto_margin is not None:
        if auto_margin <= 0:
            flags.append("at_or_above_autoignition")
        elif auto_margin <= 50:
            flags.append("low_autoignition_margin")
    if autoignition is not None and boiling is not None and autoignition - boiling <= 75:
        flags.append("autoignition_close_to_boiling_point")
    volatility = _volatility(vapor)
    vapor_matches_operating = (
        operating is not None and vapor is not None and vapor_temp is not None
        and abs(operating - vapor_temp) <= 10.0
    )
    if operating is not None and vapor is not None and not vapor_matches_operating:
        flags.append("vapor_pressure_not_at_operating_temperature")
    elif vapor_matches_operating and volatility in {"high", "very high"}:
        flags.append(volatility.replace(" ", "_") + "_volatility")
    if operating is None:
        risk = "not_assessed"
    elif {"at_or_above_normal_boiling_point", "at_or_above_autoignition"} & set(flags):
        risk = "critical"
    elif {"near_normal_boiling_point", "above_flash_point", "low_autoignition_margin", "very_high_volatility"} & set(flags):
        risk = "high"
    elif flash is None or autoignition is None:
        risk = "incomplete"
    elif flags:
        risk = "moderate"
    else:
        risk = "low"
    return {
        "operating_temp_c": operating, "risk_level": risk, "flags": flags,
        "boiling_margin_c": boiling_margin, "autoignition_margin_c": auto_margin,
        "atmospheric_feasible": None if operating is None or boiling is None else operating < boiling,
        "operating_at_or_above_flash_point": None if operating is None or flash is None else operating >= flash,
    }


def build_safety_profile(
    solvent_name: str, operating_temp_c: Optional[float] = None, include_pubchem: bool = True,
) -> dict[str, Any]:
    local = _local_properties(solvent_name)
    resolved = thermo.resolve_solvent(solvent_name)
    name = (
        thermo.canonical_solvent_name(resolved)
        if resolved else str(local.get("name") or solvent_name)
    )
    cas = str(local.get("cas_number") or "")
    cid = int(local["cid"]) if _number(local.get("cid")) is not None else None
    pubchem = _pubchem(cid) if include_pubchem and cid is not None else {}
    gscore, curated = _gscore(name, cas), _curated(name, cas)
    vapor = _number(pubchem.get("vapor_pressure_kpa"))
    vapor_temp = _number(pubchem.get("vapor_pressure_temp_c"))
    reported_volatility = _volatility(vapor)
    operating = _number(operating_temp_c)
    volatility = reported_volatility
    if operating is not None and vapor is not None and (
        vapor_temp is None or abs(operating - vapor_temp) > 10.0
    ):
        volatility = f"{reported_volatility} at reported temperature; unknown at operating temperature"
    physical = {
        "boiling_point_c": _number(local.get("boiling_point_c")),
        "flash_point_c": _number(pubchem.get("flash_point_c")),
        "autoignition_c": _number(pubchem.get("autoignition_c")),
        "vapor_pressure_kpa": vapor,
        "vapor_pressure_temp_c": vapor_temp,
        "volatility_class": volatility,
        "logp": _number(local.get("logp")),
    }
    assessment = _assessment(
        operating, physical["boiling_point_c"], physical["flash_point_c"],
        physical["autoignition_c"], physical["vapor_pressure_kpa"], vapor_temp,
    )
    gaps = [field for field in ("flash_point_c", "autoignition_c", "vapor_pressure_kpa") if physical[field] is None]
    if operating is not None and vapor is not None and (
        vapor_temp is None or abs(operating - vapor_temp) > 10.0
    ):
        gaps.append("vapor_pressure_at_operating_temp")
    if not gscore:
        gaps.append("g_score")
    if not curated:
        gaps.append("peroxide_former_class")
    if not pubchem.get("ld50_values"):
        gaps.append("ld50_values")
    if not pubchem.get("occupational_exposure_limits"):
        gaps.append("occupational_exposure_limits")
    return {
        "identity": {"name": name, "cas_number": cas or None, "pubchem_cid": cid},
        "physical_properties": physical,
        "gscore": gscore,
        "ghs": pubchem.get("ghs") or {},
        "toxicity": {
            "ld50_values": pubchem.get("ld50_values") or [],
            "lc50_values": pubchem.get("lc50_values") or [],
            "biodegradation": pubchem.get("biodegradation") or [],
        },
        "occupational_exposure_limits": pubchem.get("occupational_exposure_limits") or [],
        "peroxide_risk": {
            "peroxide_former_class": curated.get("peroxide_former_class") or "unknown",
            "peroxide_former_label": curated.get("peroxide_former_label"),
            "peroxide_notes": curated.get("peroxide_notes"),
            "sds_storage_category": curated.get("sds_storage_category"),
        },
        "process_temperature_assessment": assessment,
        "data_gaps": gaps,
        "provenance": {
            "local_properties": "thermodynamics.duckdb:solvent_data (V12-0 Solvent_Data.csv snapshot)",
            "g_score": gscore.get("source"),
            "pubchem": f"https://pubchem.ncbi.nlm.nih.gov/compound/{cid}" if cid else None,
            "pubchem_temperature_basis": (
                "minimum parsed reported value in each PubChem heading" if pubchem else None
            ),
            "pubchem_failed_headings": pubchem.get("failed_headings") or [],
            "pubchem_heading_errors": pubchem.get("heading_errors") or {},
            "curated_peroxide": curated.get("source_url"),
        },
    }


_HEALTH_HAZARD_CODE = re.compile(
    r"\bH(?:30[45]|334|34[01]|35[01][A-Z]*|36[0-2][A-Z]*|37[0-3])\b",
    re.I,
)
_HEALTH_HAZARD_CLASSES = (
    "aspiration hazard",
    "carcinogenicity",
    "germ cell mutagenicity",
    "reproductive toxicity",
    "respiratory sensitization",
    "specific target organ toxicity",
)


def _project_pictograms_with_statement_basis(
    pictograms: list[Any], hazard_statements: list[Any],
) -> tuple[list[Any], list[Any]]:
    """Project GHS08 only when the collected GHS taxonomy assigns it."""
    statement_text = " ".join(
        str(item).casefold() for item in hazard_statements
    )
    health_hazard_is_assigned = bool(
        _HEALTH_HAZARD_CODE.search(statement_text)
    ) or any(
        hazard_class in statement_text
        for hazard_class in _HEALTH_HAZARD_CLASSES
    )
    retained: list[Any] = []
    omitted: list[Any] = []
    for pictogram in pictograms:
        if (
            str(pictogram).casefold() != "health hazard"
            or health_hazard_is_assigned
        ):
            retained.append(pictogram)
        else:
            omitted.append(pictogram)
    return retained, omitted


def _row(profile: dict[str, Any]) -> dict[str, Any]:
    identity, physical = profile["identity"], profile["physical_properties"]
    assessment, gscore = profile["process_temperature_assessment"], profile["gscore"]
    hazard_statements = list(profile["ghs"].get("hazard_statements", []))
    pictograms, pictograms_without_statement = (
        _project_pictograms_with_statement_basis(
            list(profile["ghs"].get("pictograms", [])),
            hazard_statements,
        )
    )
    return {
        "solvent": identity["name"], "operating_temp_c": _round_sig(assessment["operating_temp_c"]),
        "heating_risk_level": assessment["risk_level"], "heating_flags": assessment["flags"],
        "boiling_point_c": _round_sig(physical["boiling_point_c"]),
        "boiling_margin_c": _round_sig(assessment["boiling_margin_c"]),
        "atmospheric_feasible": assessment["atmospheric_feasible"],
        "flash_point_c": _round_sig(physical["flash_point_c"]),
        "operating_at_or_above_flash_point": assessment["operating_at_or_above_flash_point"],
        "autoignition_c": _round_sig(physical["autoignition_c"]),
        "autoignition_margin_c": _round_sig(assessment["autoignition_margin_c"]),
        "vapor_pressure_kpa": _round_sig(physical["vapor_pressure_kpa"]),
        "vapor_pressure_temp_c": _round_sig(physical["vapor_pressure_temp_c"]),
        "volatility_class": physical["volatility_class"], "g_score": _round_sig(gscore.get("g_score")),
        "g_score_is_ml_predicted": gscore.get("ml_predicted"), "logp": _round_sig(physical["logp"]),
        "ghs_signal_word": profile["ghs"].get("signal_word"),
        "ghs_pictograms": pictograms,
        "ghs_pictograms_without_retained_statement": (
            pictograms_without_statement
        ),
        # Collection is already bounded at the PubChem adapter.  Do not create
        # three contradictory safety cards by slicing those bounded facts again
        # in reporter/insertion order: that order is not hazard importance.
        "key_hazard_statements": hazard_statements,
        "occupational_exposure_limits": list(
            profile.get("occupational_exposure_limits", []),
        ),
        "ld50_values": list(profile["toxicity"].get("ld50_values", [])),
        "biodegradation": list(
            profile["toxicity"].get("biodegradation", []),
        ),
        "peroxide_former_class": profile["peroxide_risk"].get("peroxide_former_class"),
        "sds_storage_category": profile["peroxide_risk"].get("sds_storage_category"),
        "data_gaps": profile["data_gaps"], "provenance": profile["provenance"],
    }


def condition_operability(
    solvent_key: str, operating_temp_c: float,
) -> dict[str, Any]:
    """Return the offline safety facts available for one modeled condition."""
    profile = build_safety_profile(
        solvent_key, operating_temp_c, include_pubchem=False,
    )
    row = _row(profile)
    local = _local_properties(solvent_key)
    return {
        key: row.get(key) for key in (
            "heating_risk_level", "heating_flags", "boiling_point_c",
            "atmospheric_feasible", "g_score", "peroxide_former_class",
            "flash_point_c",
            "operating_at_or_above_flash_point",
        )
    } | {
        "boiling_point_margin_c": row.get("boiling_margin_c"),
        "catalog_hazard_reference_c": _round_sig(
            local.get("catalog_hazard_reference_c")
        ),
        "flash_point_available": row.get("flash_point_c") is not None,
        "safety_source": "local",
    }


def typed_solvent_safety_evidence(
    solvent_key: str, *, ghs_signal_word: object,
    ghs_source: str = "local",
) -> list[dict[str, Any]]:
    """Label independent safety metrics without turning one into another.

    G-score is a green-chemistry metric, not a GHS hazard verdict.  A missing
    GSK or peroxide match stays explicit instead of being read as evidence of
    no hazard.
    """
    profile = build_safety_profile(
        solvent_key, operating_temp_c=None, include_pubchem=False,
    )
    gscore = profile.get("gscore") or {}
    peroxide = profile.get("peroxide_risk") or {}
    gsk_classification = (
        gscore.get("classification")
        if gscore.get("source") == "GSK_dataset.csv"
        else None
    )
    gscore_value = _round_sig(gscore.get("g_score"))
    return [
        {
            "metric_type": "GHS signal word",
            "value": str(ghs_signal_word or "unknown"),
            "source": (
                "CAS/PubChem hazard lookup"
                if ghs_source == "pubchem"
                else "local NIH PubChem-derived admission snapshot"
            ),
        },
        {
            "metric_type": "G-score (green chemistry; not a hazard verdict)",
            "value": (
                gscore_value if gscore_value is not None else "unavailable"
            ),
            "source": gscore.get("source") or "unavailable",
        },
        {
            "metric_type": "GSK solvent class",
            "value": gsk_classification or "unavailable",
            "source": "GSK_dataset.csv",
        },
        {
            "metric_type": "peroxide-former class",
            "value": peroxide.get("peroxide_former_class") or "unknown",
            "source": "curated peroxide reference",
        },
    ]


_LOWER_HAZARD_DISCLOSURE_LIMIT = 3


def attach_lower_hazard_disclosure(
    recommendation: Optional[dict[str, Any]],
    qualifying_candidates: list[dict[str, Any]],
    *,
    top_k: int,
    decision_metric: str,
    decision_value_key: str,
    decision_unit: str,
    legacy_value_key: Optional[str] = None,
    legacy_cost_key: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Attach one common, ranking-neutral Danger-choice disclosure.

    The hazard framing comes from the locally admitted snapshot.  It does not
    imply that PubChem was queried during this tool call.  Warning choices are
    searched in the complete qualifying decision set before the public
    shortlist is sliced; unknown signals are never treated as lower hazard.
    """
    if not recommendation:
        return recommendation
    if str(recommendation.get("ghs_signal_word") or "").casefold() != "danger":
        return recommendation

    recommendation["typed_safety_evidence"] = typed_solvent_safety_evidence(
        str(recommendation.get("solvent") or ""),
        ghs_signal_word=recommendation.get("ghs_signal_word"),
        ghs_source=str(recommendation.get("safety_source") or "local"),
    )
    recommendation_value = recommendation.get(decision_value_key)
    if not isinstance(recommendation_value, (int, float)):
        raise ValueError(
            f"Danger recommendation lacks numeric {decision_value_key!r}."
        )

    alternatives: list[dict[str, Any]] = []
    for rank, candidate in enumerate(qualifying_candidates):
        if str(candidate.get("ghs_signal_word") or "").casefold() != "warning":
            continue
        candidate_value = candidate.get(decision_value_key)
        if not isinstance(candidate_value, (int, float)):
            continue
        value = float(candidate_value)
        cost = round(float(recommendation_value) - value, 6)
        alternative: dict[str, Any] = {
            "solvent": candidate.get("solvent"),
            "ghs_signal_word": candidate.get("ghs_signal_word"),
            "ghs_evidence_source": (
                "local NIH PubChem-derived admission snapshot"
            ),
            "qualifying_rank": rank,
            "decision_metric": decision_metric,
            "decision_value": value,
            "decision_cost": cost,
            "decision_unit": decision_unit,
            "outside_top_k": rank >= top_k,
        }
        if legacy_value_key:
            alternative[legacy_value_key] = value
        if legacy_cost_key:
            alternative[legacy_cost_key] = cost
        alternatives.append(alternative)

    disclosed = alternatives[:_LOWER_HAZARD_DISCLOSURE_LIMIT]
    recommendation.update({
        "lower_hazard_search_scope": "full_qualifying_set_before_top_k",
        "lower_hazard_alternative_count": len(alternatives),
        "lower_hazard_alternatives": disclosed,
        "ghs_danger_unavoidable": not bool(alternatives),
    })
    if len(disclosed) < len(alternatives):
        recommendation["lower_hazard_alternatives_truncated"] = True
    return recommendation


def merge_condition_operability(
    target: dict[str, Any], evidence: dict[str, Any],
) -> dict[str, Any]:
    """Attach safety evidence without rounding existing thermodynamic facts."""
    preserved = {
        key: target[key]
        for key in (
            "boiling_point_c", "boiling_point_margin_c",
            "atmospheric_feasible",
        )
        if target.get(key) is not None
    }
    target.update(evidence)
    target.update(preserved)
    return target


def recommended_condition_operability(
    solvent_key: str,
    operating_temp_c: float,
    *,
    include_pubchem: bool = False,
) -> dict[str, Any]:
    """Resolve flash for one selected condition while keeping offline defaults."""
    local = condition_operability(solvent_key, operating_temp_c)
    if not include_pubchem:
        return local
    row = _row(build_safety_profile(
        solvent_key, operating_temp_c, include_pubchem=True,
    ))
    if row.get("flash_point_c") is None:
        return local
    return local | {
        "flash_point_c": row["flash_point_c"],
        "flash_point_available": True,
        "operating_at_or_above_flash_point": row[
            "operating_at_or_above_flash_point"
        ],
        "ghs_signal_word": row.get("ghs_signal_word"),
        "safety_source": "pubchem",
    }


def _model_source_families(row: dict[str, Any]) -> list[str]:
    """Keep exact source identifiers in artifacts while giving prose safe labels."""
    labels: list[str] = []
    for key, source in row.get("provenance", {}).items():
        if key in {
            "pubchem_temperature_basis", "pubchem_failed_headings",
            "pubchem_heading_errors",
        }:
            continue
        if not source or isinstance(source, list):
            continue
        source = str(source)
        if source.startswith("thermodynamics.duckdb:solvent_data"):
            label = "thermodynamics database solvent-property snapshot"
        elif source.startswith("https://pubchem.ncbi.nlm.nih.gov/compound/"):
            label = "PubChem compound database"
        elif source == "GSK_dataset.csv":
            label = "GSK solvent guide"
        elif source == "GreenSolventDB_10k.csv":
            label = "GreenSolventDB"
        elif key == "curated_peroxide" and source.startswith(("http://", "https://")):
            label = "curated peroxide-former reference"
        else:
            label = source
        if label not in labels:
            labels.append(label)
    return labels


def _model_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = dict(row)
    compact.pop("provenance", None)
    compact["source_families"] = _model_source_families(row)
    # Hazard statements and toxicity values are already collection-bounded.
    # Preserve them in full; first-seen PubChem order is reporter frequency,
    # not severity.  Biodegradation is a different evidence class, so keep its
    # historical display bound with explicit cardinality instead of reusing a
    # hazard policy for it.
    compact["key_hazard_statements"] = [
        str(value) for value in compact.get("key_hazard_statements", [])
    ]
    compact["ld50_values"] = [
        str(value) for value in compact.get("ld50_values", [])
    ]
    biodegradation = list(compact.get("biodegradation", []))
    compact["biodegradation"] = [
        str(value)[:180] for value in biodegradation[:2]
    ]
    if len(biodegradation) > len(compact["biodegradation"]):
        compact["biodegradation_total"] = len(biodegradation)
        compact["biodegradation_truncated"] = True
    compact["occupational_exposure_limits"] = [
        {
            key: value for key, value in item.items()
            if key in {"authority", "ppm", "mg_m3", "basis", "skin"}
            and value is not None
        }
        for item in compact.get("occupational_exposure_limits", [])
    ]
    return compact


def _exposure_lines(row: dict[str, Any]) -> list[str]:
    limits = row.get("occupational_exposure_limits") or []
    if not limits:
        return ["not available in current sources"]
    lines: list[str] = []
    for item in limits:
        values = []
        if item.get("ppm") is not None:
            values.append(f"{float(item['ppm']):g} ppm")
        if item.get("mg_m3") is not None:
            values.append(f"{float(item['mg_m3']):g} mg/m3")
        basis = f" ({item['basis']})" if item.get("basis") else ""
        skin = "; skin notation" if item.get("skin") else ""
        lines.append(
            f"{item.get('authority') or 'reported limit'}{basis}: "
            f"{' / '.join(values) or 'value not parsed'}{skin}"
        )
    return lines


def _box(title: str, lines: list[str], width: int = 100) -> str:
    label = f" {title} "[: width - 2]
    top = "╭" + "─" * ((width - 2 - len(label)) // 2) + label
    top += "─" * (width - 1 - len(top)) + "╮"
    output = [top]
    for line in lines:
        wrapped = textwrap.wrap(str(line), width=width - 4, break_long_words=False) or [""]
        output.extend(f"│ {item:<{width - 4}} │" for item in wrapped)
    output.append("╰" + "─" * (width - 2) + "╯")
    return "\n".join(output)


def format_safety_card(profile: dict[str, Any]) -> str:
    row, identity = _row(profile), profile["identity"]
    value = lambda item, digits=1: "not available" if item is None else f"{float(item):.{digits}f}"
    lines = [
        f"{identity['name']}  |  HEATING RISK: {str(row['heating_risk_level']).upper()}",
        f"CAS: {identity.get('cas_number') or 'not available'}  |  PubChem CID: {identity.get('pubchem_cid') or 'not available'}",
        f"G-score: {value(row.get('g_score'), 2)}  |  LogP: {value(row.get('logp'), 2)}",
        "THERMAL / VOLATILITY",
        f"BP: {value(row.get('boiling_point_c'))} C  |  Flash: {value(row.get('flash_point_c'))} C  |  Autoignition: {value(row.get('autoignition_c'))} C",
        f"Vapor pressure: {value(row.get('vapor_pressure_kpa'), 2)} kPa @ {value(row.get('vapor_pressure_temp_c'))} C  |  Volatility: {row.get('volatility_class')}",
    ]
    if row.get("operating_temp_c") is not None:
        lines.extend([
            "PROCESS TEMPERATURE",
            f"Operating: {value(row['operating_temp_c'])} C  |  BP margin: {value(row.get('boiling_margin_c'))} C  |  Autoignition margin: {value(row.get('autoignition_margin_c'))} C",
            "Flags: " + (", ".join(str(flag).replace("_", " ") for flag in row.get("heating_flags") or []) or "none"),
        ])
    lines.extend([
        "PEROXIDE / STORAGE",
        f"Class: {row.get('peroxide_former_class') or 'unknown'}  |  Storage: {row.get('sds_storage_category') or 'not available'}",
        "GHS / TOXICITY",
        f"Signal: {row.get('ghs_signal_word') or 'not available'}  |  Pictograms: {', '.join(row.get('ghs_pictograms') or []) or 'not available'}",
    ])
    if row.get("ghs_pictograms_without_retained_statement"):
        lines.append(
            "Source pictograms without a retained assigning statement: "
            + ", ".join(
                row["ghs_pictograms_without_retained_statement"],
            )
        )
    lines.extend("• " + value for value in row.get("key_hazard_statements", []))
    lines.extend([
        "OCCUPATIONAL EXPOSURE LIMITS",
        *("• " + value for value in _exposure_lines(row)),
    ])
    lines.append("Data gaps: " + (", ".join(row.get("data_gaps") or []) or "none identified in current sources"))
    sources = [str(value) for value in row["provenance"].values() if value and not isinstance(value, list)]
    lines.extend(["SOURCES", *('• ' + value for value in sources)])
    return _box("DISSOLVE SAFETY CARD", lines)


def format_safety_comparison(rows: list[dict[str, Any]]) -> str:
    lines = [
        "Candidate-specific setpoints; input order preserved.",
        f"{'Solvent':<20} {'Set C':>6} {'BP C':>6} {'BP Δ':>6} {'Flash':>6} {'AI Δ':>6} {'G':>5} {'GHS':<7} {'Risk':<10}",
        "─" * 96,
    ]
    value = lambda item: "—" if item is None else f"{float(item):.1f}"
    for row in rows:
        display_name = re.sub(r"\s*\([^()]{0,24}\)\s*$", "", str(row["solvent"]))
        lines.append(
            f"{display_name[:20]:<20} {value(row['operating_temp_c']):>6} "
            f"{value(row['boiling_point_c']):>6} {value(row['boiling_margin_c']):>6} "
            f"{value(row['flash_point_c']):>6} {value(row['autoignition_margin_c']):>6} "
            f"{value(row['g_score']):>5} {str(row.get('ghs_signal_word') or '—')[:7]:<7} "
            f"{str(row['heating_risk_level']):<10}"
        )
    for row in rows:
        lines.append(
            f"Exposure · {row['solvent']}: " + "; ".join(_exposure_lines(row))
        )
    for row in rows:
        gaps = ", ".join(row.get("data_gaps") or []) or "none identified"
        lines.append(f"Data gaps · {row['solvent']}: {gaps}")
    sources = sorted({
        str(source) for row in rows for source in row.get("provenance", {}).values()
        if source and not isinstance(source, list)
    })
    lines.extend([
        "", "Normal-BP margin is an atmospheric operability check, not a complete safety assessment.",
        "Sources: " + "; ".join(sources),
    ])
    return _box("DISSOLVE SAFETY COMPARISON", lines)


_RISK_PRIORITY = {
    "not_assessed": 0, "low": 1, "moderate": 2,
    "incomplete": 3, "high": 4, "critical": 5,
}


def _safety_priority(row: dict[str, Any]) -> tuple[float, ...]:
    flags = set(row.get("heating_flags") or [])
    g_score = _number(row.get("g_score"))
    return (
        float(_RISK_PRIORITY.get(str(row.get("heating_risk_level")), 0)),
        float("at_or_above_autoignition" in flags),
        float("at_or_above_normal_boiling_point" in flags),
        float(row.get("operating_at_or_above_flash_point") is True),
        float(str(row.get("ghs_signal_word") or "").casefold() == "danger"),
        -float(g_score if g_score is not None else -math.inf),
        float(len(row.get("ghs_pictograms") or [])),
    )


def _decision_safety(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "solvent", "operating_temp_c", "heating_risk_level", "heating_flags",
        "boiling_point_c", "flash_point_c", "operating_at_or_above_flash_point",
        "autoignition_c", "g_score", "g_score_is_ml_predicted", "ghs_signal_word",
        "ghs_pictograms", "ghs_pictograms_without_retained_statement",
        "key_hazard_statements", "occupational_exposure_limits", "ld50_values",
        "data_gaps", "source_families",
    )
    return {key: row.get(key) for key in keys if row.get(key) is not None}


def _format_substitution_comparison(
    current: dict[str, Any], replacement: Optional[dict[str, Any]], retention: float,
) -> str:
    value = lambda item: "—" if item is None else f"{float(item):.2f}"
    lines = [
        "G-score is a sourced screening proxy; higher is treated as preferable.",
        f"Comparable means retaining at least {retention:.2f} of current modeled selectivity.",
        f"{'Role':<12} {'Solvent':<20} {'T C':>6} {'Sel pts':>8} {'G':>6} {'Flash C':>8} {'Heat risk':<10}",
        "─" * 78,
    ]
    for role, row in (("current", current), ("candidate", replacement)):
        if not row:
            continue
        safety = row.get("safety") or {}
        lines.append(
            f"{role:<12} {str(row.get('solvent') or '')[:20]:<20} "
            f"{value(row.get('temperature_c')):>6} {value(row.get('selectivity_pct')):>8} "
            f"{value(safety.get('g_score')):>6} {value(safety.get('flash_point_c')):>8} "
            f"{str(safety.get('heating_risk_level') or 'unknown'):<10}"
        )
    if replacement:
        lines.extend([
            "",
            f"Selectivity give-up: {value(replacement.get('selectivity_loss_points'))} percentage points",
            f"G-score change: {value(replacement.get('g_score_change'))}",
        ])
    return _box("DISSOLVE ROUTE SOLVENT SUBSTITUTION", lines)


def get_solvent_safety_card(
    solvent_name: str, operating_temp_c: Optional[float] = None, include_pubchem: bool = True,
) -> str:
    """Build one merged solvent safety card and terminal artifact."""
    name = str(solvent_name or "").strip()
    if not name:
        return tool_error("get_solvent_safety_card", "A solvent name is required.", error_code="missing_solvent")
    operating = _number(operating_temp_c)
    if operating_temp_c is not None and operating is None:
        return tool_error("get_solvent_safety_card", "Operating temperature must be finite.", error_code="invalid_temperature")
    profile = build_safety_profile(name, operating, bool(include_pubchem))
    row = _row(profile)
    display = format_safety_card(profile)
    return tool_success(
        "get_solvent_safety_card", display=display, solvent_name=profile["identity"]["name"],
        operating_temp_c=operating, include_pubchem=bool(include_pubchem), safety_profile=profile,
        comparison_rows=[_model_row(row)],
        provenance={"source_families": _model_source_families(row)},
        artifact={"kind": "safety_card", "format": "text", "title": f"Safety card · {profile['identity']['name']}"},
        warnings=["Normal-boiling-point margin is an atmospheric operability check, not a complete safety assessment."],
    )


def _normalize_safety_candidate(candidate: Any) -> Any:
    """Copy one candidate and expose its supplied identity canonically."""
    if not isinstance(candidate, dict):
        return candidate
    normalized = dict(candidate)
    normalized["solvent_name"] = next((
        str(candidate.get(key) or "").strip()
        for key in ("solvent_name", "solvent", "name")
        if str(candidate.get(key) or "").strip()
    ), "")
    return normalized


def compare_solvent_safety_at_conditions(
    candidates: list[dict[str, Any]] | None = None,
    include_pubchem: bool = True,
    limit: int = 6,
) -> str:
    """Compare ordered process solvents at their own exact candidate temperatures."""
    tool = "compare_solvent_safety_at_conditions"
    if candidates is not None and not isinstance(candidates, list):
        return tool_error(tool, "Candidates must be a list.", error_code="invalid_candidates")
    if candidates is not None:
        candidates = [_normalize_safety_candidate(item) for item in candidates]
    stored_rows, candidate_source = candidate_evidence(current_tool_session())
    inherited_candidates: list[dict[str, Any]] = []
    seen_inherited: set[tuple[str, Optional[float]]] = set()
    for item in stored_rows:
        name = str(item.get("solvent") or "").strip()
        if not name:
            continue
        temperature = _number(
            item.get("dissolution_temperature_c")
            if item.get("dissolution_temperature_c") is not None
            else item.get("temperature_c")
        )
        key = (thermo.resolve_solvent(name) or _key(name), temperature)
        if key not in seen_inherited:
            inherited_candidates.append({
                "solvent_name": name,
                "operating_temp_c": temperature,
            })
            seen_inherited.add(key)
    candidates, inherited = resolve_candidate_argument(
        candidates, inherited_candidates,
    )
    if not candidates:
        return tool_error(tool, "At least one candidate object is required.", error_code="missing_candidates")
    try:
        bounded = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        return tool_error(tool, "limit must be an integer.", error_code="invalid_limit")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, Optional[float]]] = set()
    for candidate in candidates:
        if not isinstance(candidate, dict) or not str(candidate.get("solvent_name") or "").strip():
            return tool_error(tool, "Every candidate requires solvent_name.", error_code="missing_solvent")
        name = str(candidate["solvent_name"]).strip()
        operating = _number(candidate.get("operating_temp_c"))
        if candidate.get("operating_temp_c") is not None and operating is None:
            return tool_error(tool, f"Invalid temperature for {name}.", error_code="invalid_temperature")
        key = (name.casefold(), operating)
        if key not in seen:
            normalized.append({"solvent_name": name, "operating_temp_c": operating})
            seen.add(key)
        if len(normalized) >= bounded:
            break
    profiles = [
        build_safety_profile(item["solvent_name"], item["operating_temp_c"], bool(include_pubchem))
        for item in normalized
    ]
    rows = [_row(profile) for profile in profiles]
    inherited_scope = candidate_source if inherited else None
    source_families = list(dict.fromkeys(
        source for row in rows for source in _model_source_families(row)
    ))
    risk_order = {"critical": 5, "high": 4, "moderate": 3, "low": 2, "incomplete": 1, "not_assessed": 0}
    highest = max((row["heating_risk_level"] for row in rows), key=lambda value: risk_order.get(value, 0))
    display = format_safety_comparison(rows)
    return tool_success(
        tool, display=display, comparison_mode="candidate_specific_temperature",
        candidate_count=len(rows), candidate_conditions=normalized,
        candidate_scope_stored_count=(
            len(stored_rows) if inherited_scope else None
        ),
        candidate_scope_count_total=(
            inherited_scope.get("candidate_count_total") if inherited_scope else None
        ),
        candidate_scope_truncated=(
            bool(inherited_scope.get("truncated")) if inherited_scope else False
        ),
        comparison_rows=[_model_row(row) for row in rows],
        highest_heating_risk_level=highest,
        highest_heating_risk_solvents=[row["solvent"] for row in rows if row["heating_risk_level"] == highest],
        all_atmospheric_feasible=(
            None if any(row["atmospheric_feasible"] is None for row in rows)
            else all(row["atmospheric_feasible"] for row in rows)
        ),
        all_at_or_above_flash_point=(
            None if any(row["operating_at_or_above_flash_point"] is None for row in rows)
            else all(row["operating_at_or_above_flash_point"] for row in rows)
        ),
        artifact={"kind": "safety_comparison", "format": "text", "title": "Candidate-specific safety comparison"},
        warnings=[
            "Normal-boiling-point feasibility is an atmospheric operability check, not a complete safety assessment.",
            "Missing safety fields remain explicit data gaps and must not be inferred.",
        ],
        provenance={"tool": tool, "source_families": source_families},
        source_records=[row["provenance"] for row in rows],
        safety_profiles=profiles,
    )


def screen_green_solvent_candidates(
    feed_polymers: list[str],
    target_polymer: str,
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
    require_atmospheric: bool = True,
    exclude_chlorinated: bool = True,
    minimum_g_score: float = 6.0,
    minimum_target_solubility_wt_pct: float = 5.0,
    minimum_selectivity_points: float = 5.0,
    limit: int = 5,
) -> str:
    """Apply sourced green/safety filters before thermodynamic tie-breaks."""
    tool = "screen_green_solvent_candidates"
    if not isinstance(feed_polymers, list) or len(feed_polymers) < 2:
        return tool_error(tool, "At least two feed polymers are required.", error_code="invalid_feed")
    requested = list(dict.fromkeys(str(item).strip() for item in feed_polymers if str(item).strip()))
    polymers: list[str] = []
    unsupported: list[str] = []
    for supplied in feed_polymers:
        expanded = thermo.expand_polymer_identity(str(supplied))
        if not expanded:
            unsupported.append(str(supplied))
        for resolved in expanded:
            if resolved not in polymers:
                polymers.append(resolved)
    target = thermo.resolve_polymer(str(target_polymer))
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
        g_floor = float(minimum_g_score)
        solubility_floor = float(minimum_target_solubility_wt_pct)
        selectivity_floor = float(minimum_selectivity_points)
        bounded_limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        return tool_error(tool, "Bounds, thresholds, and limit must be numeric.", error_code="invalid_numeric_input")
    if not all(math.isfinite(value) for value in (
        lower, upper, g_floor, solubility_floor, selectivity_floor,
    )) or upper < lower:
        return tool_error(tool, "Bounds and thresholds must be finite and ordered.", error_code="invalid_numeric_input")
    if unsupported or target not in polymers:
        return tool_success(
            tool, analysis_type="green_first_solvent_scope_gap", complete=False,
            requested_polymers=requested, polymers=polymers,
            target_polymer=target or str(target_polymer),
            unsupported_thermodynamic_polymers=unsupported,
            temperature_min_c=lower, temperature_max_c=upper,
            strict_maximum=bool(strict_maximum), exclude_chlorinated=bool(exclude_chlorinated),
            minimum_g_score=g_floor,
            minimum_target_solubility_wt_pct=solubility_floor,
            minimum_selectivity_points=selectivity_floor,
            selectivity_computable=False, screen_executed=False,
            available_thermodynamic_polymers=sorted(thermo.get_available_polymers()),
            provenance={"thermodynamics": "thermodynamics.duckdb", "g_score_screen": "not executed"},
            warnings=[
                "No solvent shortlist was ranked because every feed polymer needs thermodynamic coverage for target/off-target selectivity.",
                "HSP fallback evidence is qualitative and cannot satisfy a numerical selectivity constraint.",
            ],
        )
    if len(polymers) < 2:
        return tool_error(tool, "At least two distinct feed polymers are required.", error_code="invalid_feed")
    retained = [polymer for polymer in polymers if polymer != target]
    from .tools import _temperature_grid

    temperatures = _temperature_grid(lower, upper, 5.0, bool(strict_maximum))
    if not temperatures:
        return tool_error(tool, "No temperatures remain inside the requested bound.", error_code="empty_temperature_grid")
    eligible, chlorinated, below_g, missing_g, no_condition = [], [], [], [], []
    sources: list[str] = []
    for solvent_key in sorted(thermo.get_available_solvents()):
        properties = _local_properties(solvent_key)
        score = _gscore(
            str(properties.get("name") or solvent_key),
            str(properties.get("cas_number") or ""),
        )
        canonical = thermo.canonical_solvent_name(solvent_key)
        g_score = _number(score.get("g_score"))
        if g_score is None:
            missing_g.append(canonical)
            continue
        contains_chlorine = "Cl" in str(score.get("smiles") or "")
        if exclude_chlorinated and contains_chlorine:
            chlorinated.append(canonical)
            continue
        if g_score < g_floor:
            below_g.append(canonical)
            continue
        condition = None
        boiling = thermo.get_boiling_point(solvent_key)
        for temperature in temperatures:
            if require_atmospheric and (boiling is None or temperature >= boiling):
                continue
            target_result = thermo.get_solubility_result(
                target, solvent_key, temperature,
            )
            off_target_results = {
                polymer: thermo.get_solubility_result(
                    polymer, solvent_key, temperature,
                )
                for polymer in retained
            }
            target_value = target_result.get("solubility_pct")
            off_targets = {
                polymer: result.get("solubility_pct")
                for polymer, result in off_target_results.items()
            }
            if target_value is None or any(value is None for value in off_targets.values()):
                continue
            limiting = max(off_targets, key=off_targets.get)
            limiting_value = float(off_targets[limiting])
            selectivity = float(target_value) - limiting_value
            if target_value >= solubility_floor and selectivity >= selectivity_floor:
                condition = {
                    "solvent": canonical,
                    "temperature_c": temperature,
                    "target_polymer": target,
                    "target_solubility_pct": float(target_value),
                    "limiting_off_target_polymer": limiting,
                    "max_off_target_solubility_pct": limiting_value,
                    "off_target_solubilities_pct": off_targets,
                    "solubility_method_by_polymer": {
                        target: target_result["method"],
                        **{
                            polymer: result["method"]
                            for polymer, result in off_target_results.items()
                        },
                    },
                    "selectivity_pct": selectivity,
                    "boiling_point_c": boiling,
                    "boiling_point_margin_c": None if boiling is None else boiling - temperature,
                    "atmospheric_feasible": None if boiling is None else temperature < boiling,
                    "g_score": g_score,
                    "g_score_rating": "Excellent" if g_score >= 8.0 else "Good",
                    "g_score_source": score.get("source"),
                    "g_score_is_ml_predicted": bool(score.get("ml_predicted")),
                    "g_score_uncertainty": score.get("uncertainty"),
                    "g_score_data_quality": score.get("data_quality"),
                    "solvent_classification": score.get("classification"),
                    "contains_chlorine": contains_chlorine,
                    **thermo.get_solvent_hazard_framing(solvent_key),
                }
                sources.append(str(score.get("source") or ""))
                break
        if condition is None:
            no_condition.append(canonical)
        else:
            eligible.append(condition)
    eligible.sort(key=lambda row: (
        -float(row["g_score"]), float(row["temperature_c"]),
        -float(row["selectivity_pct"]), str(row["solvent"]),
    ))
    ranked = eligible[:bounded_limit]
    for rank, row in enumerate(ranked, 1):
        row["rank"] = rank
    display_lines = [
        "Green-first filter: non-chlorinated; G-score >= " + f"{g_floor:g}.",
        f"{'Rank':<5} {'Solvent':<22} {'G':>6} {'T C':>7} {'Target wt%':>10} {'Gap pts':>9}",
        "─" * 64,
        *(
            f"{row['rank']:<5} {str(row['solvent'])[:22]:<22} {row['g_score']:>6.3f} "
            f"{row['temperature_c']:>7.1f} {row['target_solubility_pct']:>9.3f} "
            f"{row['selectivity_pct']:>9.3f}"
            for row in ranked
        ),
    ]
    return tool_success(
        tool, display=_box("DISSOLVE GREEN-FIRST SOLVENT SCREEN", display_lines),
        analysis_type="green_first_solvent_screen", polymers=polymers,
        target_polymer=target, other_polymers=retained,
        temperature_min_c=lower, temperature_max_c=upper,
        strict_maximum=bool(strict_maximum), require_atmospheric=bool(require_atmospheric),
        exclude_chlorinated=bool(exclude_chlorinated), minimum_g_score=g_floor,
        g_score_rating_floor=("Excellent" if g_floor >= 8.0 else "Good" if g_floor >= 6.0 else "Problematic"),
        minimum_target_solubility_wt_pct=solubility_floor,
        minimum_selectivity_points=selectivity_floor,
        condition_selection=(
            "lowest screened atmospheric temperature meeting both thermodynamic references"
            if require_atmospheric else
            "lowest screened temperature meeting both thermodynamic references"
        ),
        ranking_basis="G-score descending, then lower qualifying temperature and higher selectivity",
        eligible_candidate_count=len(eligible), ranked_candidates=ranked,
        excluded_chlorinated=sorted(set(chlorinated)),
        excluded_below_g_score_count=len(set(below_g)),
        excluded_missing_g_score_count=len(set(missing_g)),
        excluded_no_qualifying_condition_count=len(set(no_condition)),
        best_result_is_weak=not ranked,
        provenance={
            "thermodynamics": "thermodynamics.duckdb",
            "g_score_sources": "; ".join(sorted(set(filter(None, sources)))),
        },
        solvent_catalog_provenance=thermo.get_solvent_catalog_provenance(),
        artifact={
            "kind": "green_solvent_screen", "format": "text",
            "title": "Green-first solvent screen",
        },
        warnings=[
            f"G-score >= {g_floor:g} is an EHS screening cutoff, not proof of low process hazard.",
            f"The {solubility_floor:g} wt% target-solubility and {selectivity_floor:g}-percentage-point selectivity references are screening heuristics, not recovery or purity criteria.",
            "Normal-boiling-point feasibility is an operability check; flash point, toxicity, and heated handling still require candidate-specific assessment.",
        ],
        model_basis="green-first filter over the unified grid-first solubility model and sourced solvent scores",
    )


def screen_route_solvent_substitutions(
    feed_polymers: list[str],
    route_steps: list[dict[str, Any]],
    temperature_min_c: Optional[float] = None,
    temperature_max_c: Optional[float] = None,
    strict_maximum: bool = False,
    require_atmospheric: bool = True,
    min_selectivity_retention_fraction: float = 0.8,
    include_pubchem: bool = True,
) -> str:
    """Screen a whole route and replace its worst solvent without hard-coded candidates."""
    tool = "screen_route_solvent_substitutions"
    if not isinstance(feed_polymers, list) or len(feed_polymers) < 2:
        return tool_error(tool, "At least two feed polymers are required.", error_code="invalid_feed")
    if not isinstance(route_steps, list) or not route_steps:
        return tool_error(tool, "At least one route step is required.", error_code="missing_route_steps")
    polymers: list[str] = []
    for supplied in feed_polymers:
        resolved = thermo.resolve_polymer(str(supplied))
        if resolved is None:
            return tool_error(tool, f"Unknown polymer: {supplied}", error_code="unknown_polymer")
        if resolved not in polymers:
            polymers.append(resolved)
    try:
        lower = thermo.FITTED_TEMP_MIN_C if temperature_min_c is None else float(temperature_min_c)
        upper = thermo.FITTED_TEMP_MAX_C if temperature_max_c is None else float(temperature_max_c)
        retention = float(min_selectivity_retention_fraction)
    except (TypeError, ValueError):
        return tool_error(tool, "Bounds and retention fraction must be numeric.", error_code="invalid_numeric_input")
    if not all(math.isfinite(value) for value in (lower, upper, retention)) or upper < lower:
        return tool_error(tool, "Bounds and retention fraction must be finite and ordered.", error_code="invalid_numeric_input")
    if not 0.0 < retention <= 1.0:
        return tool_error(tool, "Retention fraction must be in (0, 1].", error_code="invalid_retention_fraction")

    from .tools import _pair_result, _screen_direction, _temperature_grid

    temperatures = _temperature_grid(lower, upper, 5.0, bool(strict_maximum))
    if not temperatures:
        return tool_error(tool, "No temperatures remain inside the requested bound.", error_code="empty_temperature_grid")
    remaining = list(polymers)
    stages: list[dict[str, Any]] = []
    source_families: list[str] = []
    for index, supplied in enumerate(route_steps, 1):
        if not isinstance(supplied, dict):
            return tool_error(tool, "Every route step must be an object.", error_code="invalid_route_step")
        target = thermo.resolve_polymer(str(supplied.get("dissolved_polymer") or supplied.get("polymer") or ""))
        solvent_key = thermo.resolve_solvent(str(supplied.get("solvent") or ""))
        operating = _number(supplied.get("temperature_c"))
        if target not in remaining or solvent_key is None or operating is None:
            return tool_error(tool, f"Invalid route step {index}.", error_code="invalid_route_step")
        retained = [polymer for polymer in remaining if polymer != target]
        target_row = _pair_result(target, solvent_key, operating)
        off_target_rows = [_pair_result(polymer, solvent_key, operating) for polymer in retained]
        if target_row is None or any(row is None for row in off_target_rows):
            return tool_error(tool, f"Route step {index} lacks thermodynamic evidence.", error_code="missing_stage_evidence")
        off_targets = {
            polymer: float(row["solubility_pct"])
            for polymer, row in zip(retained, off_target_rows) if row is not None
        }
        maximum = max(off_targets.values(), default=0.0)
        safety_row = _row(build_safety_profile(
            thermo.canonical_solvent_name(solvent_key), operating, bool(include_pubchem)
        ))
        sources = _model_source_families(safety_row)
        safety_row["source_families"] = sources
        source_families.extend(sources)
        stages.append({
            "stage": index, "dissolved_polymer": target,
            "retained_polymers": retained,
            "solvent": thermo.canonical_solvent_name(solvent_key),
            "solvent_data_key": solvent_key, "temperature_c": operating,
            "target_solubility_pct": float(target_row["solubility_pct"]),
            "max_off_target_solubility_pct": maximum,
            "selectivity_pct": float(target_row["solubility_pct"]) - maximum,
            "solubility_method_by_polymer": {
                target: target_row["method"],
                **{
                    polymer: row["method"]
                    for polymer, row in zip(retained, off_target_rows)
                    if row is not None
                },
            },
            "safety": _decision_safety(safety_row),
        })
        remaining.remove(target)
    worst = max(stages, key=lambda item: _safety_priority(item["safety"]))
    candidates, screened, excluded, _ = _screen_direction(
        worst["dissolved_polymer"], worst["retained_polymers"], temperatures,
        bool(require_atmospheric), 12,
    )
    current_key = str(worst["solvent_data_key"])
    minimum_selectivity = float(worst["selectivity_pct"]) * retention
    substitutions: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_key = thermo.resolve_solvent(str(candidate.get("solvent") or ""))
        if candidate_key == current_key or float(candidate.get("selectivity_pct") or -math.inf) < minimum_selectivity:
            continue
        safety_row = _row(build_safety_profile(
            str(candidate["solvent"]), float(candidate["temperature_c"]), bool(include_pubchem)
        ))
        sources = _model_source_families(safety_row)
        safety_row["source_families"] = sources
        source_families.extend(sources)
        current_g = _number(worst["safety"].get("g_score"))
        candidate_g = _number(safety_row.get("g_score"))
        g_change = None if current_g is None or candidate_g is None else candidate_g - current_g
        candidate_selectivity = float(candidate["selectivity_pct"])
        substitutions.append({
            **{key: candidate.get(key) for key in (
                "solvent", "temperature_c", "target_solubility_pct",
                "max_off_target_solubility_pct", "off_target_solubilities_pct",
                "limiting_off_target_polymer", "selectivity_pct",
                "solubility_method_by_polymer",
                "boiling_point_c", "boiling_point_margin_c", "atmospheric_feasible",
            )},
            "selectivity_loss_points": float(worst["selectivity_pct"]) - candidate_selectivity,
            "selectivity_retained_fraction": (
                None if not worst["selectivity_pct"]
                else candidate_selectivity / float(worst["selectivity_pct"])
            ),
            "g_score_change": g_change,
            "higher_g_score": bool(g_change is not None and g_change > 0.0),
            "heating_risk_improved": (
                _RISK_PRIORITY.get(str(safety_row.get("heating_risk_level")), 0)
                < _RISK_PRIORITY.get(str(worst["safety"].get("heating_risk_level")), 0)
            ),
            "flash_point_change_c": (
                None if safety_row.get("flash_point_c") is None or worst["safety"].get("flash_point_c") is None
                else float(safety_row["flash_point_c"]) - float(worst["safety"]["flash_point_c"])
            ),
            "flash_point_decrease_c": (
                None if safety_row.get("flash_point_c") is None or worst["safety"].get("flash_point_c") is None
                else float(worst["safety"]["flash_point_c"]) - float(safety_row["flash_point_c"])
            ),
            "safety": _decision_safety(safety_row),
        })
    substitutions.sort(key=lambda item: (
        not item["higher_g_score"],
        -float(item["g_score_change"] or -math.inf),
        float(item["selectivity_loss_points"]),
        str(item.get("solvent") or ""),
    ))
    recommended = next((item for item in substitutions if item["higher_g_score"]), None)
    updated_route = None
    if recommended:
        updated_steps = []
        for stage in stages:
            selected = recommended if stage["stage"] == worst["stage"] else stage
            updated_steps.append({
                "dissolved_polymer": stage["dissolved_polymer"],
                "solvent": selected["solvent"],
                "temperature_c": selected["temperature_c"],
            })
        final_residue = remaining[0] if len(remaining) == 1 else None
        updated_route = {
            "complete": final_residue is not None,
            "best_sequence": [item["dissolved_polymer"] for item in updated_steps]
            + ([final_residue] if final_residue else []),
            "steps": updated_steps, "final_residue": final_residue,
            "unresolved_polymers": [] if final_residue else list(remaining),
        }
    current_display = {**worst, "safety": worst["safety"]}
    display = _format_substitution_comparison(current_display, recommended, retention)
    comparison_rows = [worst["safety"]] + ([recommended["safety"]] if recommended else [])
    return tool_success(
        tool, display=display, analysis_type="route_solvent_substitution_screen",
        polymers=polymers, temperature_min_c=lower, temperature_max_c=upper,
        evaluated_temperature_max_c=max(temperatures), strict_maximum=bool(strict_maximum),
        require_atmospheric=bool(require_atmospheric),
        min_selectivity_retention_fraction=retention,
        minimum_comparable_selectivity_points=minimum_selectivity,
        route_stage_assessments=stages, worst_stage=worst,
        worst_stage_basis={
            "method": "lexicographic sourced safety-screen priority",
            "priority": [
                "heating risk", "critical thermal flags", "above flash point",
                "GHS Danger", "lower G-score", "pictogram count",
            ],
        },
        candidate_substitutions=substitutions,
        recommended_substitution=recommended,
        no_higher_g_score_comparable_substitution=recommended is None,
        recommended_route=updated_route,
        route_update_status=("model_screened_substitution" if updated_route else "unchanged"),
        comparison_rows=comparison_rows,
        candidate_conditions=[
            {"solvent_name": row["solvent"], "operating_temp_c": row["temperature_c"]}
            for row in [worst, *substitutions]
        ],
        screened_conditions=screened, excluded_for_boiling_point=excluded,
        selection_basis=(
            "higher sourced G-score among atmospheric candidates retaining at least "
            f"{retention:.0%} of the current modeled selectivity"
        ),
        provenance={"source_families": list(dict.fromkeys(source_families))},
        artifact={"kind": "route_solvent_substitution", "format": "text", "title": "Route solvent substitution"},
        warnings=[
            "G-score is a screening proxy, not a complete life-cycle or safety assessment.",
            "A higher G-score does not imply safer heated operation; compare flash point and heating risk separately.",
            "Selectivity is a modeled percentage-point difference, not recovery or purity.",
            "The substituted route remains model-screened and requires experimental validation.",
        ],
    )


