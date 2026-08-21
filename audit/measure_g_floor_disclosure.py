#!/usr/bin/env python3
"""Reproduce R2: default G-score floor 6.0 is served and labelled unsourced.

Taken from the f535afa residual list (A5 leftover). Not a P-item.
Does not invent a citation. No live PubChem. No BioSTEAM.
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


def _floor_view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": payload.get("success"),
        "analysis_type": payload.get("analysis_type"),
        "minimum_g_score": payload.get("minimum_g_score"),
        "minimum_g_score_source": payload.get("minimum_g_score_source"),
        "minimum_g_score_citation_status": payload.get(
            "minimum_g_score_citation_status"
        ),
        "warning_names_unsourced": any(
            "no regulatory or literature citation" in str(item)
            for item in (payload.get("warnings") or [])
        ),
    }


@lru_cache(maxsize=1)
def served_floors() -> dict[str, Any]:
    module = _safety()
    default = _data(module.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=3,
    ))
    user = _data(module.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE",
        minimum_g_score=8.0, limit=3,
    ))
    gap = _data(module.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PMMA"], target_polymer="LDPE",
    ))
    return {
        "why": (
            "f535afa residual: A5 G-floor 6.0 still unsourced. "
            "The number was already served; this slice labels it unsourced."
        ),
        "default": _floor_view(default),
        "user_requested": _floor_view(user),
        "scope_gap": _floor_view(gap),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-g-floor-disclosure.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "R2",
        "residual_taken": "A5 G-floor 6.0 unsourced on the served green screen",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_g_floor_disclosure.py",
        "served_floors": served_floors(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "G_FLOOR_DISCLOSURE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    floors = document["served_floors"]
    print(f"wrote {out}", flush=True)
    print(
        "default", floors["default"]["minimum_g_score_citation_status"],
        "user", floors["user_requested"]["minimum_g_score_citation_status"],
        "gap", floors["scope_gap"]["minimum_g_score_citation_status"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
