#!/usr/bin/env python3
"""Identity join + §7 facts. Unspecified-only STRAP refuse is in contaminants.py.

Does not embed washes. Does not call tea._config_key. No BioSTEAM.
"""
from __future__ import annotations

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
_OBJECTED_V2 = Path("/home/aaltamimi2/dissolve-v12-audit/CONTAMINANT_MODE_SPEC.v2.md")
_PFOA = "Perfluorooctanoic Acid"
_DEHP_CATALOG = "di-(2-ethylhexyl) phthalate (DEHP)"


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _contaminants():
    _sys_path()
    from dissolve import contaminants
    return contaminants


def _thermo():
    _sys_path()
    from dissolve import thermodynamics
    return thermodynamics


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@lru_cache(maxsize=1)
def identity() -> dict[str, Any]:
    module = _contaminants()
    thermo = _thermo()
    rows = module._connection().execute(
        "SELECT DISTINCT solvent_raw, solvent_key, solvent_normalized FROM logd"
        " ORDER BY solvent_key"
    ).fetchall()
    mismatches = []
    for raw, key, normalized in rows:
        resolved = thermo.resolve_solvent(key)
        mismatches.append({
            "solvent_raw": raw,
            "solvent_key": key,
            "solvent_normalized": normalized,
            "resolved": resolved,
            "key_equals_resolved": key == resolved,
        })
    key_ne_resolved = [item for item in mismatches if not item["key_equals_resolved"]]
    pfoa = _PFOA
    butanone = module._logd("butanone", pfoa)
    two_butanone = module._logd("2-butanone", pfoa)
    aceticacid = module._logd("aceticacid", pfoa)
    acetic_acid = module._logd("acetic acid", pfoa)
    o_xylene_pfoa = module._logd("o-xylene", pfoa)
    xylene_pfoa = module._logd("xylene", pfoa)
    o_xylene_dehp = module._logd("o-xylene", _DEHP_CATALOG)
    aliases = {}
    for token in (
        "DEHP", "BBP", "DiNP", "DEP", "DBP", "DiDP", "DnHP", "DnOP",
        "2-ethylhexyl", "heptafluoropropoxy",
    ):
        supported, unsupported, families, _uncovered = module._expand([token])
        aliases[token] = {
            "supported": supported,
            "unsupported": unsupported,
            "families": families,
        }
    return {
        "logd_distinct_solvents": len(rows),
        "key_not_equal_resolved_count": len(key_ne_resolved),
        "key_not_equal_resolved": key_ne_resolved,
        "constructed_hits": {
            "butanone_equals_2_butanone": (
                butanone is not None and butanone == two_butanone
            ),
            "butanone_logd": butanone,
            "2_butanone_logd": two_butanone,
            "aceticacid_equals_acetic_acid": (
                aceticacid is not None and aceticacid == acetic_acid
            ),
            "aceticacid_logd": aceticacid,
            "acetic_acid_logd": acetic_acid,
        },
        "o_xylene_is_not_catalog_xylene": {
            "pfoa_o_xylene": o_xylene_pfoa,
            "pfoa_xylene": xylene_pfoa,
            "different_identities": (
                o_xylene_pfoa is None and xylene_pfoa is not None
            ),
            "dehp_o_xylene_hits": o_xylene_dehp is not None,
        },
        "parenthetical_aliases": aliases,
        "solvent_raw_is_queried": "solvent_raw" in (
            module._solvent_where(("acetone",))[0]
        ),
    }


@lru_cache(maxsize=1)
def screens() -> dict[str, Any]:
    module = _contaminants()
    args = dict(
        target_polymer="LDPE",
        contaminants=[_PFOA],
        other_polymers=["PET", "EVOH"],
        solvents=["2,3-dihydropyran", "acetone", "2-butanone", "1-propanol"],
    )
    leach = _data(module.screen_contaminant_leaching(**args))
    strap = _data(module.screen_contaminant_strap_removal(**args))

    def view(payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload.get("candidate_solvents") or []
        return {
            "success": payload.get("success"),
            "mode": payload.get("mode"),
            "decision_basis": payload.get("decision_basis"),
            "recommended_solvents": payload.get("recommended_solvents"),
            "passing_count": sum(1 for row in rows if row.get("passes")),
            "target_statuses": [
                {
                    "solvent": row.get("solvent"),
                    "passes": row.get("passes"),
                    "target_polymer_status": row.get("target_polymer_status"),
                    "operating_temperature_c": row.get("operating_temperature_c"),
                    "precipitation_temperature_c": row.get(
                        "precipitation_temperature_c"
                    ),
                }
                for row in rows
            ],
        }

    return {
        "same_inputs": args,
        "leaching": view(leach),
        "strap": view(strap),
        "inverted_polymer_requirement": {
            "leaching_wants_target_intact": True,
            "strap_wants_dissolve_then_precipitate": True,
            "pass_sets_differ": (
                set(leach.get("recommended_solvents") or [])
                != set(strap.get("recommended_solvents") or [])
            ),
        },
    }


@lru_cache(maxsize=1)
def thresholds() -> dict[str, Any]:
    module = _contaminants()
    served = _data(module.screen_contaminant_strap_removal(
        "LDPE", [_DEHP_CATALOG], other_polymers=["EVOH"], solvents=["Toluene"],
    ))
    return {
        "defaults": module.default_thresholds(include_precipitation=True),
        "served_threshold_sources": served.get("threshold_sources"),
        "served_threshold_citations": served.get("threshold_citations"),
        "served_threshold_citation_status": served.get("threshold_citation_status"),
        "served_still_labels_block_unsourced": (
            served.get("threshold_citation_status") == "unsourced"
        ),
        "split": {
            "precipitation_threshold_wt_pct": {
                "default": 1.0,
                "literature_basis": (
                    "Zhou Green Chem. 2026, 28, 9061: "
                    "'we set a threshold of 1 wt%'"
                ),
                "status": "paper_sourced",
                "product_still_labelled": (
                    served.get("threshold_sources") or {}
                ).get("precipitation_threshold_wt_pct"),
            },
            "swelling_min_wt_pct": {"default": 1.0, "status": "unsourced"},
            "swelling_max_wt_pct": {"default": 10.0, "status": "unsourced"},
            "dissolution_min_wt_pct": {
                "default": 10.0,
                "status": "unsourced",
                "rejects_paper_thp": (
                    "LDPE in THP is 6.49 wt% at 85 C; paper 7.3 wt% at 87 C"
                ),
            },
        },
        "no_citation_invented": True,
    }


@lru_cache(maxsize=1)
def absence() -> dict[str, Any]:
    module = _contaminants()
    bfr = _data(module.screen_contaminant_leaching("LDPE", ["BFR"]))
    unknown = _data(module.screen_contaminant_leaching(
        "LDPE", ["xyzzy-not-a-contaminant"],
    ))
    mixed = _data(module.screen_contaminant_leaching(
        "LDPE", [_PFOA, "HBCD"], solvents=["Toluene"],
    ))
    bad_solvent = _data(module.screen_contaminant_leaching(
        "LDPE", [_PFOA], solvents=["not-a-real-solvent-xyz"],
    ))
    con = module._connection()
    regimes = con.execute(
        "SELECT family, temperature_regime, COUNT(*) "
        "FROM miscibility GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    toluene = module._miscibility("toluene", _PFOA, "rt")
    return {
        "all_unknown": {
            "success": unknown.get("success"),
            "error_code": unknown.get("error_code"),
            "unsupported_contaminants": unknown.get("unsupported_contaminants"),
            "refuses": unknown.get("success") is False,
        },
        "uncovered_class_BFR": {
            "success": bfr.get("success"),
            "error_code": bfr.get("error_code"),
            "unsupported_contaminants": bfr.get("unsupported_contaminants"),
            "supported_families": bfr.get("supported_families"),
            "empty_is_not_clean": bfr.get("success") is False,
            "distinct_family_code": bfr.get("error_code")
            == "unsupported_contaminant_family",
        },
        "mixed_known_unknown_continues": {
            "success": mixed.get("success"),
            "unsupported_contaminants": mixed.get("unsupported_contaminants"),
            "n_supported": mixed.get("n_supported_contaminants"),
        },
        "unknown_solvent": {
            "success": bad_solvent.get("success"),
            "error_code": bad_solvent.get("error_code"),
            "unsupported_solvents": bad_solvent.get("unsupported_solvents"),
            "refuses": bad_solvent.get("success") is False,
            "today_is_success_with_failing_candidate": False,
        },
        "unspecified_fallback": {
            "regimes_by_family": [
                {"family": family, "temperature_regime": regime, "n": n}
                for family, regime, n in regimes
            ],
            "all_unspecified_are_pfas": all(
                family == "PFAS"
                for family, regime, _n in regimes
                if regime == "unspecified"
            ),
            "rt_and_t_higher_are_phthalates": all(
                family == "Phthalates"
                for family, regime, _n in regimes
                if regime in {"rt", "t_higher"}
            ),
            "toluene_pfoa_asked_rt": toluene,
            "unspecified_not_a_strap_basis_not_implemented": False,
        },
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    v2_sha = subprocess.check_output(
        ["sha256sum", str(_OBJECTED_V2)], text=True,
    ).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-contaminant-identity.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "objected_v2": "CONTAMINANT_MODE_SPEC.v2.md",
        "objected_v2_sha256": v2_sha,
        "checkpoint": "identity-join + section-7 facts independent of test 2",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_contaminant_identity.py",
        "tea_config_key_calls": [],
        "identity": _jsonable(identity()),
        "screens": _jsonable(screens()),
        "thresholds": _jsonable(thresholds()),
        "absence": _jsonable(absence()),
        "not_this_slice": [
            "wash embedding",
            "accept test 2 planner bind",
            "tea._config_key",
        ],
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "CONTAMINANT_IDENTITY.v1.json"
    out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    ident = document["identity"]
    print(f"wrote {out}", flush=True)
    print(
        "key!=resolved", ident["key_not_equal_resolved_count"],
        "butanone", ident["constructed_hits"]["butanone_equals_2_butanone"],
        "DEHP", ident["parenthetical_aliases"]["DEHP"]["supported"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
