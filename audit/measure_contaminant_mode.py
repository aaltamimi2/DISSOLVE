#!/usr/bin/env python3
"""Reproduce v3 §7: record the audit and the unspecified-only STRAP refuse.

Authorized product edit is unspecified_not_a_strap_basis only.
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
_SPEC = Path("/home/aaltamimi2/dissolve-v12-audit/CONTAMINANT_MODE_SPEC.v3.md")
_PFOA = "Perfluorooctanoic Acid"
_DEHP = "di-(2-ethylhexyl) phthalate (DEHP)"


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


def _candidate(payload: dict[str, Any], solvent: str | None = None) -> dict[str, Any]:
    rows = payload.get("candidate_solvents") or []
    if solvent is None:
        return rows[0] if rows else {}
    wanted = solvent.casefold()
    for row in rows:
        if str(row.get("solvent") or "").casefold() == wanted:
            return row
    return {}


def _regimes(row: dict[str, Any]) -> list[str]:
    hot = [item.get("miscibility_regime") for item in (row.get("contaminants") or [])]
    cold = [
        item.get("miscibility_regime")
        for item in (row.get("precipitation_regime_contaminants") or [])
    ]
    return [str(item) for item in hot + cold if item is not None]


@lru_cache(maxsize=1)
def inverted_screens() -> dict[str, Any]:
    module = _contaminants()
    args = dict(
        target_polymer="LDPE",
        contaminants=[_PFOA],
        other_polymers=["PET", "EVOH"],
        solvents=["2,3-dihydropyran", "acetone", "2-butanone", "1-propanol"],
    )
    leach = _data(module.screen_contaminant_leaching(**args))
    strap = _data(module.screen_contaminant_strap_removal(**args))
    return {
        "same_inputs": args,
        "leaching_success": leach.get("success"),
        "strap_success": strap.get("success"),
        "leaching_mode": leach.get("mode"),
        "strap_mode": strap.get("mode"),
        "leaching_recommended": leach.get("recommended_solvents"),
        "strap_recommended": strap.get("recommended_solvents"),
        "leaching_decision_basis": leach.get("decision_basis"),
        "strap_decision_basis": strap.get("decision_basis"),
        "pass_sets_differ": (
            set(leach.get("recommended_solvents") or [])
            != set(strap.get("recommended_solvents") or [])
        ),
    }


@lru_cache(maxsize=1)
def constructed_b_pair() -> dict[str, Any]:
    module = _contaminants()
    fail = _data(module.screen_contaminant_strap_removal(
        "LDPE", [_DEHP], other_polymers=["PET", "EVOH"], solvents=["toluene"],
    ))
    passed = _data(module.screen_contaminant_strap_removal(
        "LDPE", [_DEHP], other_polymers=["EVOH"], solvents=["toluene"],
    ))
    fail_row = _candidate(fail, "toluene")
    pass_row = _candidate(passed, "toluene")
    return {
        "fail_pet_evoh": {
            "success": fail.get("success"),
            "passes": fail_row.get("passes"),
            "target_polymer_status": fail_row.get("target_polymer_status"),
            "unspecified_not_a_strap_basis": fail_row.get(
                "unspecified_not_a_strap_basis"
            ),
        },
        "pass_evoh": {
            "success": passed.get("success"),
            "passes": pass_row.get("passes"),
            "operating_temperature_c": pass_row.get("operating_temperature_c"),
            "precipitation_temperature_c": pass_row.get(
                "precipitation_temperature_c"
            ),
            "contaminant_logd_min": pass_row.get("contaminant_logd_min"),
            "miscibility_regimes": _regimes(pass_row),
            "unspecified_not_a_strap_basis": pass_row.get(
                "unspecified_not_a_strap_basis"
            ),
        },
    }


@lru_cache(maxsize=1)
def withdrawn_pfoa_case() -> dict[str, Any]:
    module = _contaminants()
    payload = _data(module.screen_contaminant_strap_removal(
        "LDPE", [_PFOA], other_polymers=["PET"], solvents=["cyclohexanol"],
    ))
    asked = module._miscibility("toluene", _PFOA, "rt")
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "refuses": (
            payload.get("success") is False
            and payload.get("error_code") == "unspecified_not_a_strap_basis"
        ),
        "toluene_pfoa_asked_rt": asked,
    }


@lru_cache(maxsize=1)
def regimes() -> dict[str, Any]:
    module = _contaminants()
    con = module._connection()
    rows = con.execute(
        "SELECT family, temperature_regime, COUNT(*) "
        "FROM miscibility GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall()
    pfas_specified = con.execute(
        """SELECT COUNT(*) FROM miscibility
           WHERE temperature_regime != 'unspecified'
             AND contaminant_key IN (
               SELECT contaminant_key FROM contaminants WHERE family='PFAS'
             )"""
    ).fetchone()[0]
    toluene_dehp = module._miscibility("toluene", _DEHP, "t_higher")
    return {
        "regimes_by_family": [
            {"family": family, "temperature_regime": regime, "n": n}
            for family, regime, n in rows
        ],
        "histogram_256_256_832": (
            {(family, regime): n for family, regime, n in rows}
            == {
                ("PFAS", "unspecified"): 832,
                ("Phthalates", "rt"): 256,
                ("Phthalates", "t_higher"): 256,
            }
        ),
        "pfas_specified_rows": int(pfas_specified),
        "toluene_dehp_asked_t_higher": toluene_dehp,
        "asked_t_higher_returns_rt": (
            (toluene_dehp or {}).get("temperature_regime") == "rt"
        ),
    }


@lru_cache(maxsize=1)
def thresholds() -> dict[str, Any]:
    module = _contaminants()
    served = _data(module.screen_contaminant_strap_removal(
        "LDPE", [_DEHP], other_polymers=["EVOH"], solvents=["toluene"],
    ))
    return {
        "threshold_sources": served.get("threshold_sources"),
        "threshold_citations": served.get("threshold_citations"),
        "threshold_citation_status": served.get("threshold_citation_status"),
        "precipitation_threshold_wt_pct": served.get(
            "precipitation_threshold_wt_pct"
        ),
        "precipitation_is_paper": (
            (served.get("threshold_sources") or {}).get(
                "precipitation_threshold_wt_pct"
            ) == "paper"
        ),
        "swell_dissolve_unsourced": (
            (served.get("threshold_citations") or {}).get("swelling_min_wt_pct")
            == "unsourced"
            and (served.get("threshold_citations") or {}).get(
                "dissolution_min_wt_pct"
            ) == "unsourced"
        ),
    }


@lru_cache(maxsize=1)
def thp() -> dict[str, Any]:
    thermo = _thermo()
    at_85 = thermo.get_solubility_result("LDPE", "tetrahydropyran", 85.0)
    at_87 = thermo.get_solubility_result("LDPE", "tetrahydropyran", 87.0)
    value = at_85.get("solubility_pct")
    return {
        "ldpe_thp_85c": {
            "solubility_pct": value,
            "unavailable_reason": at_85.get("unavailable_reason"),
        },
        "ldpe_thp_87c": {
            "solubility_pct": at_87.get("solubility_pct"),
            "unavailable_reason": at_87.get("unavailable_reason"),
        },
        "paper_7_3_wt_pct_at_87c": 7.3,
        "both_below_default_dissolution_min": (
            value is not None and float(value) < 10.0 and 7.3 < 10.0
        ),
    }


@lru_cache(maxsize=1)
def identity_and_refusals() -> dict[str, Any]:
    module = _contaminants()
    dehp = module._expand(["DEHP"])
    inner = module._expand(["2-ethylhexyl"])
    bfr = _data(module.screen_contaminant_leaching("LDPE", ["BFR"]))
    junk = _data(module.screen_contaminant_leaching(
        "LDPE", ["xyzzy-not-a-contaminant"],
    ))
    unknown_solvent = _data(module.screen_contaminant_leaching(
        "LDPE", [_PFOA], solvents=["not-a-real-solvent-xyz"],
    ))
    return {
        "dehp_supported": dehp[0],
        "ethylhexyl_unsupported": inner[1],
        "bfr_error_code": bfr.get("error_code"),
        "junk_error_code": junk.get("error_code"),
        "unknown_solvent_error_code": unknown_solvent.get("error_code"),
        "codes_are_distinct": len({
            bfr.get("error_code"),
            junk.get("error_code"),
            unknown_solvent.get("error_code"),
        }) == 3,
    }


@lru_cache(maxsize=1)
def paper_disagreements() -> dict[str, Any]:
    return {
        "logd_has_no_polymer_column": True,
        "pfas_miscibility_is_unspecified_only": True,
        "dissolution_10_wt_pct_is_unsourced": True,
        "precipitation_1_wt_pct_is_paper": True,
        "zero_bfr_rows": True,
        "thp_rejected_by_10_wt_pct": True,
        "pfoa_cyclohexanol_pet_is_unspecified_fallback": True,
        "dehp_toluene_evoh_is_specified_rt": True,
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-contaminant-mode.v1",
        "spec": "CONTAMINANT_MODE_SPEC.v3",
        "spec_sha256": spec_sha,
        "checkpoint": "v3-section-7",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_contaminant_mode.py",
        "tea_config_key_calls": [],
        "inverted_screens": inverted_screens(),
        "constructed_b_pair": constructed_b_pair(),
        "withdrawn_pfoa_case": withdrawn_pfoa_case(),
        "regimes": regimes(),
        "thresholds": thresholds(),
        "thp": thp(),
        "identity_and_refusals": identity_and_refusals(),
        "paper_disagreements": paper_disagreements(),
        "not_this_slice": [
            "wash embedding",
            "cli.py",
            "separation.py",
            "accept test 2 planner bind",
            "tea._config_key",
        ],
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "CONTAMINANT_MODE.v1.json"
    out.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    pair = document["constructed_b_pair"]
    withdrawn = document["withdrawn_pfoa_case"]
    print(f"wrote {out}", flush=True)
    print(
        "b_fail", pair["fail_pet_evoh"]["passes"],
        "b_pass", pair["pass_evoh"]["passes"],
        pair["pass_evoh"]["miscibility_regimes"],
        "pfoa", withdrawn["error_code"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
