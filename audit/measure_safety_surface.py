#!/usr/bin/env python3
"""Reproduce A5: safety provenance, four registry tools, no live PubChem.

A5.1 inventories the twelve non-underscore functions.
A5.2–A5.4 exercise only the four registry tools.
include_pubchem=False unless the held PubChem pin is injected by CID.
No BioSTEAM.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_SPEC = Path("/home/aaltamimi2/dissolve-v12-audit/UNAUDITED_SURFACE_SPEC.v2.md")
_HELD = _SRC / "dissolve" / "data" / "held_solvent_pubchem_enrichment.json"
_TWELVE = (
    "build_safety_profile",
    "condition_operability",
    "typed_solvent_safety_evidence",
    "attach_lower_hazard_disclosure",
    "merge_condition_operability",
    "recommended_condition_operability",
    "format_safety_card",
    "format_safety_comparison",
    "get_solvent_safety_card",
    "compare_solvent_safety_at_conditions",
    "screen_green_solvent_candidates",
    "screen_route_solvent_substitutions",
)
_REGISTRY = (
    "get_solvent_safety_card",
    "compare_solvent_safety_at_conditions",
    "screen_green_solvent_candidates",
    "screen_route_solvent_substitutions",
)


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _safety():
    _sys_path()
    from dissolve import safety
    return safety


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _held() -> dict[str, Any]:
    return json.loads(_HELD.read_text(encoding="utf-8"))


def _held_by_cid() -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    for record in _held()["records"]:
        cid = (record.get("cid_resolution") or {}).get("cid")
        pubchem = record.get("pubchem")
        if cid is not None and isinstance(pubchem, dict):
            out[int(cid)] = pubchem
    return out


@lru_cache(maxsize=1)
def provenance_inventory() -> dict[str, Any]:
    safety = _safety()
    digest = hashlib.sha256(Path(safety._ASSET).read_bytes()).hexdigest()
    connection = safety._connection()
    sources = [
        {
            "path": row[0],
            "sha256": row[1],
            "bytes": row[2],
            "source_commit": row[3],
        }
        for row in connection.execute("SELECT * FROM asset_sources").fetchall()
    ]
    metadata = dict(connection.execute("SELECT * FROM safety_metadata").fetchall())
    gsk_n = connection.execute("SELECT COUNT(*) FROM gsk_safety").fetchone()[0]
    green_n = connection.execute("SELECT COUNT(*) FROM green_solvent").fetchone()[0]
    curated = [
        {
            "canonical_name": row[0],
            "source_label": row[1],
            "source_url": row[2],
        }
        for row in connection.execute(
            "SELECT canonical_name, source_label, source_url FROM curated_safety"
        ).fetchall()
    ]
    gsk = safety._gscore("Toluene")
    ml = safety._gscore("Dodecane")
    return {
        "twelve_non_underscore_functions": list(_TWELVE),
        "four_registry_tools": list(_REGISTRY),
        "safety_duckdb": {
            "path": "src/dissolve/data/safety.duckdb",
            "pinned_sha256": safety._ASSET_SHA256,
            "on_disk_sha256": digest,
            "match": digest == safety._ASSET_SHA256,
            "gsk_safety_rows": int(gsk_n),
            "green_solvent_rows": int(green_n),
            "asset_sources": sources,
            "safety_metadata": metadata,
            "v11_inheritance": (
                "safety_metadata.source_commit is 4b7b513. The four CSV "
                "sources are pinned to that commit. No v12 re-verification "
                "is recorded in the database. Labelled v11-inherited."
            ),
        },
        "g_score": {
            "gsk_example": {
                "query": "Toluene",
                "source": gsk.get("source"),
                "ml_predicted": gsk.get("ml_predicted"),
                "g_score": gsk.get("g_score"),
                "classification": gsk.get("classification"),
                "table": "gsk_safety",
            },
            "green_example": {
                "query": "Dodecane",
                "source": ml.get("source"),
                "ml_predicted": ml.get("ml_predicted"),
                "g_score": ml.get("g_score"),
                "data_quality": ml.get("data_quality"),
                "table": "green_solvent",
            },
            "lookup_order": "gsk_safety first (ml_predicted=False), then green_solvent (ml_predicted=True)",
        },
        "hazard_fields": {
            "boiling_point_c": {
                "source": "thermodynamics.duckdb:solvent_data (V12-0 Solvent_Data.csv snapshot)",
                "vintage": "v12 solvent snapshot",
            },
            "flash_point_c": {
                "source": "PubChem heading Flash Point via _pubchem, or absent when include_pubchem=False",
                "vintage": "live unless held pin is injected; not in safety.duckdb",
            },
            "autoignition_c": {
                "source": "PubChem heading Autoignition Temperature",
                "vintage": "same as flash",
            },
            "ghs": {
                "source": "PubChem heading GHS Classification when include_pubchem=True; else empty",
                "vintage": "same as flash",
            },
            "peroxide_former_class": {
                "source": "curated_safety in safety.duckdb",
                "vintage": "v11-inherited; five rows, VUMC list / local review",
                "rows": curated,
            },
            "g_score": {
                "source": "gsk_safety (GSK_dataset.csv) or green_solvent (GreenSolventDB_10k.csv)",
                "vintage": "source_commit 4b7b513, v11-inherited",
            },
        },
        "minimum_g_score_floor": {
            "default": 6.0,
            "cited_in_function": False,
            "status": "unsourced",
            "served_warning": (
                "G-score >= 6 is an EHS screening cutoff, not proof of low process hazard."
            ),
        },
        "held_pubchem_pin": {
            "path": "src/dissolve/data/held_solvent_pubchem_enrichment.json",
            "sha256": hashlib.sha256(_HELD.read_bytes()).hexdigest(),
            "fetched_at": _held()["fetched_at"],
            "record_count": _held()["record_count"],
            "wired_into_safety_py": False,
            "note": (
                "The pin exists on disk and is not imported by safety.py. "
                "A5.4 injects it by CID so flash-known tests do not hit the network."
            ),
        },
        "function_set_split": (
            "A5.1 may name all twelve. A5.2–A5.4 served-answer tests use only "
            "the four registry tools."
        ),
    }


@lru_cache(maxsize=1)
def absent_solvent() -> dict[str, Any]:
    safety = _safety()
    query = "xyzzy-not-a-solvent"
    local = safety._local_properties(query)
    card = _data(safety.get_solvent_safety_card(query, include_pubchem=False))
    green = _data(safety.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
    ))
    return {
        "query": query,
        "local_properties": local,
        "card": {
            "success": card.get("success"),
            "solvent_name": card.get("solvent_name"),
            "data_gaps": card["safety_profile"]["data_gaps"],
            "gscore": card["safety_profile"]["gscore"],
            "must_refuse": False,
            "note": (
                "Observation only. The card succeeds with data_gaps. "
                "Do not treat success as a defect."
            ),
        },
        "green_screen": {
            "success": green.get("success"),
            "minimum_g_score": green.get("minimum_g_score"),
            "excluded_missing_g_score_count": green.get("excluded_missing_g_score_count"),
            "excluded_below_g_score_count": green.get("excluded_below_g_score_count"),
            "ranked": [
                {
                    "solvent": row["solvent"],
                    "g_score": row["g_score"],
                    "g_score_source": row["g_score_source"],
                    "g_score_is_ml_predicted": row["g_score_is_ml_predicted"],
                }
                for row in green.get("ranked_candidates") or []
            ],
            "provenance": green.get("provenance"),
            "warning0": (green.get("warnings") or [None])[0],
        },
        "three_behaviours": [
            "get_solvent_safety_card succeeds with data_gaps",
            "_local_properties falls back to {name: query}",
            "screen_green_solvent_candidates skips missing G-score into missing_g",
        ],
    }


@lru_cache(maxsize=1)
def lower_hazard_bind() -> dict[str, Any]:
    safety = _safety()
    recommendation = {
        "solvent": "Toluene",
        "ghs_signal_word": "Danger",
        "safety_source": "local",
        "ordering_window_c": 40.0,
    }
    qualifying = [
        recommendation,
        {
            "solvent": "Isobutyl Isobutyrate",
            "ghs_signal_word": "Warning",
            "ordering_window_c": 38.0,
        },
        {
            "solvent": "Octane",
            "ghs_signal_word": "Warning",
            "ordering_window_c": 30.0,
        },
    ]
    attached = safety.attach_lower_hazard_disclosure(
        dict(recommendation),
        qualifying,
        top_k=1,
        decision_metric="ordering_window_c",
        decision_value_key="ordering_window_c",
        decision_unit="C",
    )
    alternatives = list((attached or {}).get("lower_hazard_alternatives") or [])
    return {
        "recommendation_solvent": attached.get("solvent"),
        "typed_evidence_names_recommendation": any(
            item.get("value") == "Danger"
            for item in (attached.get("typed_safety_evidence") or [])
        ),
        "alternative_solvents": [row["solvent"] for row in alternatives],
        "alternatives_are_warning_only": all(
            row["ghs_signal_word"] == "Warning" for row in alternatives
        ),
        "recommendation_not_in_alternatives": "Toluene" not in {
            row["solvent"] for row in alternatives
        },
        "binds": (
            attached.get("solvent") == "Toluene"
            and {row["solvent"] for row in alternatives}
            == {"Isobutyl Isobutyrate", "Octane"}
        ),
    }


@lru_cache(maxsize=1)
def flash_condition() -> dict[str, Any]:
    safety = _safety()
    offline = _data(safety.compare_solvent_safety_at_conditions(
        candidates=[{"solvent_name": "Toluene", "operating_temp_c": 80}],
        include_pubchem=False,
    ))
    offline_row = offline["comparison_rows"][0]
    held = _held_by_cid()
    original = safety._pubchem

    def pinned(cid: int) -> dict[str, Any]:
        return dict(held.get(int(cid)) or {})

    try:
        safety._pubchem = pinned  # type: ignore[method-assign]
        pinned_compare = _data(safety.compare_solvent_safety_at_conditions(
            candidates=[{"solvent_name": "1-Pentene", "operating_temp_c": 25}],
            include_pubchem=True,
        ))
    finally:
        safety._pubchem = original  # type: ignore[method-assign]
    pinned_row = pinned_compare["comparison_rows"][0]
    return {
        "offline_missing_flash": {
            "include_pubchem": False,
            "solvent": "Toluene",
            "operating_temp_c": 80,
            "flash_point_c": offline_row.get("flash_point_c"),
            "operating_at_or_above_flash_point": offline_row.get(
                "operating_at_or_above_flash_point"
            ),
            "heating_risk_level": offline_row.get("heating_risk_level"),
            "all_at_or_above_flash_point": offline.get("all_at_or_above_flash_point"),
            "silent_pass": False,
        },
        "held_pin_known_flash": {
            "hook": (
                "safety._pubchem replaced with held_solvent_pubchem_enrichment.json "
                "lookup by CID. No network."
            ),
            "solvent": "1-Pentene",
            "operating_temp_c": 25,
            "flash_point_c": pinned_row.get("flash_point_c"),
            "operating_at_or_above_flash_point": pinned_row.get(
                "operating_at_or_above_flash_point"
            ),
            "heating_risk_level": pinned_row.get("heating_risk_level"),
            "says_above_flash_when_known": (
                pinned_row.get("operating_at_or_above_flash_point") is True
            ),
        },
    }


@lru_cache(maxsize=1)
def registry_offline() -> dict[str, Any]:
    safety = _safety()
    card = _data(safety.get_solvent_safety_card(
        "Toluene", operating_temp_c=80, include_pubchem=False,
    ))
    substitutions = _data(safety.screen_route_solvent_substitutions(
        feed_polymers=["LDPE", "PP"],
        route_steps=[{
            "dissolved_polymer": "LDPE",
            "solvent": "Toluene",
            "temperature_c": 80,
        }],
        include_pubchem=False,
    ))
    return {
        "get_solvent_safety_card": {
            "success": card.get("success"),
            "include_pubchem": False,
            "g_score_source": card["safety_profile"]["provenance"].get("g_score"),
            "g_score_is_ml_predicted": card["comparison_rows"][0].get(
                "g_score_is_ml_predicted"
            ),
            "flash_point_c": card["safety_profile"]["physical_properties"]["flash_point_c"],
        },
        "screen_route_solvent_substitutions": {
            "success": substitutions.get("success"),
            "error_code": substitutions.get("error_code"),
            "include_pubchem": False,
        },
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-safety-surface.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "A5",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_safety_surface.py",
        "live_pubchem": False,
        "inventory": provenance_inventory(),
        "absent_solvent": absent_solvent(),
        "lower_hazard_disclosure": lower_hazard_bind(),
        "flash_condition": flash_condition(),
        "registry_offline": registry_offline(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "SAFETY_SURFACE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(
        "gsk", document["inventory"]["g_score"]["gsk_example"]["ml_predicted"],
        "green", document["inventory"]["g_score"]["green_example"]["ml_predicted"],
        "floor", document["inventory"]["minimum_g_score_floor"]["status"],
        "absent_ok", document["absent_solvent"]["card"]["success"],
        "missing_g", document["absent_solvent"]["green_screen"]["excluded_missing_g_score_count"],
        "bind", document["lower_hazard_disclosure"]["binds"],
        "offline_flash", document["flash_condition"]["offline_missing_flash"]["heating_risk_level"],
        "held_above", document["flash_condition"]["held_pin_known_flash"]["says_above_flash_when_known"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
