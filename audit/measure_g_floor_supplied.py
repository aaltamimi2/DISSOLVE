#!/usr/bin/env python3
"""Reproduce R3: explicit minimum_g_score=6.0 is caller-supplied, not default.

4c32e43 residual. No citation invented. No BioSTEAM.
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


def _view(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": payload.get("success"),
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
def supplied_vs_omitted() -> dict[str, Any]:
    module = _safety()
    omitted = _data(module.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE", limit=2,
    ))
    explicit = _data(module.screen_green_solvent_candidates(
        feed_polymers=["LDPE", "PP"], target_polymer="LDPE",
        minimum_g_score=6.0, limit=2,
    ))
    return {
        "why": (
            "4c32e43 residual: explicit 6.0 was labelled default because "
            "disclosure compared the number, not whether the caller supplied it."
        ),
        "omitted": _view(omitted),
        "explicit_six": _view(explicit),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-g-floor-supplied.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "R3",
        "residual_taken": "explicit minimum_g_score=6.0 was indistinguishable from default",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_g_floor_supplied.py",
        "supplied_vs_omitted": supplied_vs_omitted(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "G_FLOOR_SUPPLIED.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pair = document["supplied_vs_omitted"]
    print(f"wrote {out}", flush=True)
    print(
        "omitted", pair["omitted"]["minimum_g_score_source"],
        "explicit", pair["explicit_six"]["minimum_g_score_source"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
