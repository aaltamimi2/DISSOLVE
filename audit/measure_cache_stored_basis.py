#!/usr/bin/env python3
"""Reproduce Finding 30: cache records store 12 fields; the serve key is 44/46.

No live BioSTEAM. Does not rewrite tea_cache.json.gz.
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


def _tea():
    _sys_path()
    from dissolve import tea
    return tea


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


@lru_cache(maxsize=1)
def census() -> dict[str, Any]:
    module = _tea()
    rows = []
    for record in module._records():
        config = dict(record.get("config") or {})
        basis = module.cache_record_stored_basis(config)
        rows.append({
            "label": record.get("label"),
            "energy_case": config.get("energy_case"),
            **basis,
        })
    return {
        "record_count": len(rows),
        "all_incomplete": all(
            row["status"] == "incomplete_stored_basis" for row in rows
        ),
        "stored_field_counts": sorted({row["stored_field_count"] for row in rows}),
        "serve_key_field_counts": sorted({
            row["serve_key_field_count"] for row in rows
        }),
        "rows": rows,
    }


@lru_cache(maxsize=1)
def lookup_stamp() -> dict[str, Any]:
    module = _tea()
    payload = _data(module.lookup_admitted_process_records(
        target_polymer="LDPE", solvent="Dodecane",
    ))
    records = payload.get("records") or []
    first = (records[0].get("cache_stored_basis") if records else {}) or {}
    return {
        "success": payload.get("success"),
        "cache_stored_basis_status": payload.get("cache_stored_basis_status"),
        "records_with_incomplete_stored_basis": payload.get(
            "records_with_incomplete_stored_basis"
        ),
        "record_count": payload.get("record_count"),
        "first_status": first.get("status"),
        "first_stored_field_count": first.get("stored_field_count"),
        "first_serve_key_field_count": first.get("serve_key_field_count"),
        "first_reconstructed_defaults_are_not_stored": first.get(
            "reconstructed_defaults_are_not_stored"
        ),
        "warning_names_twelve": any(
            "D-8 twelve" in str(item) for item in (payload.get("warnings") or [])
        ),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-cache-stored-basis.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "finding_30",
        "residual_taken": (
            "Finding 30: cache records store the D-8 twelve; serve key is 44/46"
        ),
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_cache_stored_basis.py",
        "live_biosteam": False,
        "cache_file_rewritten": False,
        "census": census(),
        "lookup": lookup_stamp(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "CACHE_STORED_BASIS.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    census_doc = document["census"]
    print(f"wrote {out}", flush=True)
    print(
        "n", census_doc["record_count"],
        "all_incomplete", census_doc["all_incomplete"],
        "stored", census_doc["stored_field_counts"],
        "key", census_doc["serve_key_field_counts"],
        "lookup", document["lookup"]["cache_stored_basis_status"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
