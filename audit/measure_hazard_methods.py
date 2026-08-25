#!/usr/bin/env python3
"""HAZARD_METHODS_SPEC.v1 docs SHA: published Methods vs sealed duckdb.

Does not edit safety.py or safety.duckdb. No live PubChem. No BioSTEAM.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_PUBLISHED = _ROOT / "audit" / "HAZARD_METHODS.published.v1.md"
_SPEC = Path("/home/aaltamimi2/dissolve-v12-audit/HAZARD_METHODS_SPEC.v1.md")
_FORBIDDEN = (
    "562 screenable",
    "102 of the 562",
    "covers 452",
    "8 have neither",
    "100 solvents where both",
    "differ by 0.28",
)
_GSK_ONLY = (
    ("1,2-dimethoxyethane", "110-71-4"),
    ("cis-decalin", "493-01-6"),
)
_ROUTE = [{
    "dissolved_polymer": "LDPE",
    "solvent": "Toluene",
    "temperature_c": 80,
}]


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _safety():
    _sys_path()
    from dissolve import safety
    return safety


def _thermo():
    _sys_path()
    from dissolve import thermodynamics as thermo
    return thermo


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def published_text() -> str:
    return _PUBLISHED.read_text(encoding="utf-8")


def _gsk_hit(safety: Any, name: str, cas: str) -> float | None:
    query = (
        "SELECT g_score FROM gsk_safety WHERE lower(common_name)=lower(?)"
    )
    parameters: list[str] = [name]
    if cas:
        query += " OR cas_number=?"
        parameters.append(cas)
    row = safety._connection().execute(
        query + " LIMIT 1", parameters,
    ).fetchone()
    return None if row is None else _finite(row[0])


def _green_hit(safety: Any, name: str, cas: str) -> float | None:
    query = (
        "SELECT g_score FROM green_solvent WHERE lower(name)=lower(?)"
    )
    parameters: list[str] = [name]
    if cas:
        query += " OR cas_number=?"
        parameters.append(cas)
    row = safety._connection().execute(
        query + " LIMIT 1", parameters,
    ).fetchone()
    return None if row is None else _finite(row[0])


@lru_cache(maxsize=1)
def roster_census() -> dict[str, Any]:
    safety = _safety()
    thermo = _thermo()
    scope, origin = thermo.resolve_solvent_scope()
    roster = sorted(thermo.get_available_solvents())
    served_gsk = 0
    served_green = 0
    served_neither = 0
    both = 0
    absdiffs: list[float] = []
    signed: list[float] = []
    gsk_only: list[dict[str, Any]] = []
    for key in roster:
        local = safety._local_properties(key)
        name = str(local.get("name") or key)
        cas = str(local.get("cas_number") or "")
        preferred = safety._gscore(name, cas)
        source = preferred.get("source")
        score = _finite(preferred.get("g_score"))
        if source == "GSK_dataset.csv" and score is not None:
            served_gsk += 1
        elif source == "GreenSolventDB_10k.csv" and score is not None:
            served_green += 1
        else:
            served_neither += 1
        gsk = _gsk_hit(safety, name, cas)
        green = _green_hit(safety, name, cas)
        if gsk is not None and green is not None:
            both += 1
            absdiffs.append(abs(gsk - green))
            signed.append(gsk - green)
        elif gsk is not None:
            gsk_only.append({"interp_key": key, "name": name, "cas": cas, "g_score": gsk})
    mae = sum(absdiffs) / len(absdiffs) if absdiffs else None
    signed_mean = sum(signed) / len(signed) if signed else None
    duckdb_sha = hashlib.sha256(Path(safety._ASSET).read_bytes()).hexdigest()
    return {
        "enumerating_function": "get_available_solvents",
        "scope_token": scope,
        "scope_origin": origin,
        "n": len(roster),
        "served_gsk": served_gsk,
        "served_green": served_green,
        "served_neither": served_neither,
        "both_table_hits": both,
        "gsk_only": gsk_only,
        "mae": mae,
        "mae_2dp": None if mae is None else round(mae, 2),
        "signed_mean": signed_mean,
        "signed_mean_2dp": None if signed_mean is None else round(signed_mean, 2),
        "duckdb_sha256": duckdb_sha,
        "duckdb_pin": safety._ASSET_SHA256,
        "default_minimum_g_score": safety._DEFAULT_MINIMUM_G_SCORE,
    }


def gscore_sql_shape() -> dict[str, Any]:
    safety = _safety()
    source = inspect.getsource(safety._gscore)
    return {
        "has_limit_1": "LIMIT 1" in source,
        "has_order_by": "ORDER BY" in source,
        "gsk_first": source.index("FROM gsk_safety") < source.index("FROM green_solvent"),
    }


def published_bindings() -> dict[str, Any]:
    text = published_text()
    census = roster_census()
    leftover_keys = {row["interp_key"] for row in census["gsk_only"]}
    leftover_cas = {row["cas"] for row in census["gsk_only"]}
    return {
        "path": str(_PUBLISHED.relative_to(_ROOT)),
        "names_enumerating_function": "get_available_solvents" in text,
        "names_scope_all": "`all`" in text or "scope `all`" in text or "built-in scope `all`" in text,
        "names_gscore": "`_gscore`" in text,
        "names_limit_1": "`LIMIT 1`" in text,
        "names_no_order_by": "no `ORDER BY`" in text or "with no `ORDER BY`" in text,
        "names_mae": "mean absolute difference" in text,
        "names_signed_mean": "signed mean" in text,
        "names_green_screen": "screen_green_solvent_candidates" in text,
        "names_route_screen": "screen_route_solvent_substitutions" in text,
        "names_include_pubchem": "include_pubchem" in text,
        "names_unsourced_floor": "unsourced" in text and "6.0" in text,
        "names_failed_headings": "pubchem_failed_headings" in text,
        "forbidden_claim_hits": [item for item in _FORBIDDEN if item in text],
        "leftover_keys_in_text": all(key in text for key, _cas in _GSK_ONLY),
        "leftover_cas_in_text": all(cas in text for _key, cas in _GSK_ONLY),
        "census_leftover_keys": sorted(leftover_keys),
        "census_leftover_cas": sorted(leftover_cas),
        "expected_leftover_keys": [key for key, _cas in _GSK_ONLY],
    }


class _PubchemRaised(RuntimeError):
    """Marker that a test patched `_pubchem` and it was reached."""


def _raise_pubchem(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise _PubchemRaised("PUBCHEM_FETCH_RAISED")


def network_by_tool() -> dict[str, Any]:
    safety = _safety()
    original = safety._pubchem
    green_ok = False
    route_default_raised = False
    route_default_ok = False
    route_offline_ok = False
    try:
        safety._pubchem = _raise_pubchem  # type: ignore[method-assign]
        green = _data(safety.screen_green_solvent_candidates(
            feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
        ))
        green_ok = bool(green.get("success")) and green.get("screen_executed") is not False
        try:
            route = _data(safety.screen_route_solvent_substitutions(
                feed_polymers=["LDPE", "PP"], route_steps=_ROUTE,
            ))
            route_default_ok = bool(route.get("success"))
        except _PubchemRaised:
            route_default_raised = True
        offline = _data(safety.screen_route_solvent_substitutions(
            feed_polymers=["LDPE", "PP"], route_steps=_ROUTE,
            include_pubchem=False,
        ))
        route_offline_ok = bool(offline.get("success"))
    finally:
        safety._pubchem = original  # type: ignore[method-assign]
    return {
        "green_screen_completes_with_pubchem_raising": green_ok,
        "route_default_raises": route_default_raised,
        "route_default_completes": route_default_ok,
        "route_include_pubchem_false_completes": route_offline_ok,
    }


def served_floor() -> dict[str, Any]:
    safety = _safety()
    payload = _data(safety.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
    ))
    return {
        "minimum_g_score": payload.get("minimum_g_score"),
        "minimum_g_score_source": payload.get("minimum_g_score_source"),
        "minimum_g_score_citation_status": payload.get(
            "minimum_g_score_citation_status"
        ),
    }


def build_document() -> dict[str, Any]:
    spec_sha = hashlib.sha256(_SPEC.read_bytes()).hexdigest() if _SPEC.exists() else None
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-hazard-methods-published.v1",
        "spec": "HAZARD_METHODS_SPEC.v1.md",
        "spec_sha256": spec_sha,
        "published": str(_PUBLISHED.relative_to(_ROOT)),
        "measured_on_builder_sha": tree,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_hazard_methods.py",
        "census": roster_census(),
        "gscore_sql_shape": gscore_sql_shape(),
        "published_bindings": published_bindings(),
        "network_by_tool": network_by_tool(),
        "served_floor": served_floor(),
        "keep_outs": (
            "No safety.py edit. No safety.duckdb edit. No live PubChem. "
            "No BioSTEAM. No v12 merge. No G-floor citation invented."
        ),
    }


def main() -> None:
    document = build_document()
    out = _ROOT / "audit" / "HAZARD_METHODS.v1.json"
    out.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
