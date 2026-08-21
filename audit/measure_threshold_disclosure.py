#!/usr/bin/env python3
"""Reproduce R1: served contaminant answers name unsourced 1/10/1.

Taken from the A7 residual list (first named leftover). Not a P-item.
Does not invent a citation. No tea._config_key. No BioSTEAM.
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
_KNOWN = "Perfluorooctanoic Acid"


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _contaminants():
    _sys_path()
    from dissolve import contaminants
    return contaminants


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _threshold_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": payload.get("success"),
        "swelling_min_wt_pct": payload.get("swelling_min_wt_pct"),
        "swelling_max_wt_pct": payload.get("swelling_max_wt_pct"),
        "dissolution_min_wt_pct": payload.get("dissolution_min_wt_pct"),
        "precipitation_threshold_wt_pct": payload.get("precipitation_threshold_wt_pct"),
        "threshold_basis": payload.get("threshold_basis"),
        "threshold_sources": payload.get("threshold_sources"),
        "threshold_citation_status": payload.get("threshold_citation_status"),
        "provenance_source_dataset": (payload.get("provenance") or {}).get(
            "source_dataset"
        ),
        "warning_names_unsourced": any(
            "no regulatory or literature citation" in str(item)
            for item in (payload.get("warnings") or [])
        ),
    }


@lru_cache(maxsize=1)
def served_defaults() -> dict[str, Any]:
    module = _contaminants()
    args = dict(
        target_polymer="LDPE",
        contaminants=[_KNOWN],
        other_polymers=["PP"],
        solvents=["Toluene"],
    )
    leaching = _data(module.screen_contaminant_leaching(**args))
    strap = _data(module.screen_contaminant_strap_removal(**args))
    compare = _data(module.compare_contaminant_removal_modes(**args))
    user = _data(module.screen_contaminant_leaching(
        **args, swelling_min_wt_pct=2.0, swelling_max_wt_pct=8.0,
        dissolution_min_wt_pct=12.0,
    ))
    return {
        "why": (
            "A7 residual: unsourced contaminant 1/10/1. "
            "Leaching hid those numbers behind Zhou provenance. "
            "This slice serves the numbers and labels them unsourced."
        ),
        "leaching": _threshold_view(leaching),
        "strap": _threshold_view(strap),
        "compare": _threshold_view(compare),
        "user_requested_leaching": _threshold_view(user),
        "nested_leaching_status": (leaching.get("threshold_citation_status")),
        "nested_compare_leaching_status": (
            (compare.get("leaching") or {}).get("threshold_citation_status")
        ),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-threshold-disclosure.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "R1",
        "residual_taken": "unsourced contaminant 1/10/1 on the served answer",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_threshold_disclosure.py",
        "served_defaults": served_defaults(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "THRESHOLD_DISCLOSURE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    served = document["served_defaults"]
    print(f"wrote {out}", flush=True)
    print(
        "leach", served["leaching"]["threshold_citation_status"],
        served["leaching"]["swelling_min_wt_pct"],
        "strap", served["strap"]["precipitation_threshold_wt_pct"],
        "user", served["user_requested_leaching"]["threshold_citation_status"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
