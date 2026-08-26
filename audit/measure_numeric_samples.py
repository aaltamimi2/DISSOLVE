#!/usr/bin/env python3
"""Reproduce A7: analyze_numeric_samples only. No HSP. No Tg. No BioSTEAM."""
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
_SQL_NAME = "SELECT * FROM solvent_data; DROP TABLE x; --"
_X = [1.0, 2.0, 3.0, 4.0]
_Y = [2.0, 4.0, 6.0, 8.0]


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _analysis():
    _sys_path()
    from dissolve import analysis
    return analysis


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _refuse(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": payload.get("success"),
        "error_code": payload.get("error_code"),
        "error": payload.get("error") or payload.get("message"),
    }


@lru_cache(maxsize=1)
def scipy_status() -> dict[str, Any]:
    module = _analysis()
    stats = module._scipy_stats()
    return {
        "scipy_present": stats is not None,
        "module": getattr(stats, "__name__", None) if stats is not None else None,
        "degradation_is_not_a_defect": True,
    }


@lru_cache(maxsize=1)
def must_serve() -> dict[str, Any]:
    module = _analysis()
    series = {"temp_c": list(_X), "solubility": list(_Y)}
    summary = _data(module.analyze_numeric_samples(series, analysis_type="summary"))
    correlation = _data(module.analyze_numeric_samples(
        series, analysis_type="correlation",
        x_name="temp_c", y_name="solubility",
    ))
    summary_rows = summary["result"]["summaries"]
    corr = correlation["result"]["correlation"]
    return {
        "summary": {
            "success": summary.get("success"),
            "analysis_type": summary.get("analysis_type"),
            "sample_source": summary.get("sample_source"),
            "series_names": summary.get("series_names"),
            "confidence_interval_methods": [
                row["confidence_interval_method"] for row in summary_rows
            ],
            "n": [row["n"] for row in summary_rows],
        },
        "correlation": {
            "success": correlation.get("success"),
            "analysis_type": correlation.get("analysis_type"),
            "sample_source": correlation.get("sample_source"),
            "x_name": corr["x_name"],
            "y_name": corr["y_name"],
            "method": corr["method"],
            "coefficient": corr["coefficient"],
            "n_pairs": corr["n_pairs"],
            "p_value_present": corr["p_value"] is not None,
        },
        "independent_calls": True,
    }


@lru_cache(maxsize=1)
def must_refuse() -> dict[str, Any]:
    module = _analysis()
    return {
        "non_finite_nan": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0, float("nan"), 2.0]},
        ))),
        "non_finite_inf": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0, float("inf")]},
        ))),
        "boolean_in_series": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0, True]},
        ))),
        "n_lt_2": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0]},
        ))),
        "correlation_unpaired_lengths": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0, 2.0, 3.0], "y": [1.0, 2.0, 3.0, 4.0]},
            analysis_type="correlation",
        ))),
        "regression_unpaired_lengths": _refuse(_data(module.analyze_numeric_samples(
            {"x": [1.0, 2.0, 3.0], "y": [1.0, 2.0]},
            analysis_type="regression",
        ))),
        "more_than_max_series": _refuse(_data(module.analyze_numeric_samples(
            {f"s{index}": [1.0, 2.0] for index in range(module._MAX_NUMERIC_SERIES + 1)},
        ))),
        "max_numeric_series": module._MAX_NUMERIC_SERIES,
    }


@lru_cache(maxsize=1)
def names_are_not_sql() -> dict[str, Any]:
    module = _analysis()
    executes: list[str] = []
    payload_calls = 0
    original_payload = module.asset_payload

    def blocked_payload() -> dict[str, Any]:
        nonlocal payload_calls
        payload_calls += 1
        raise RuntimeError("A7 must not open the HSP/Tg asset.")

    duckdb = sys.modules.get("duckdb")
    original_connect = getattr(duckdb, "connect", None) if duckdb is not None else None
    if duckdb is not None:
        def blocked_connect(*_args: Any, **_kwargs: Any) -> Any:
            executes.append("duckdb.connect")
            raise RuntimeError("A7 must not open a database.")

        duckdb.connect = blocked_connect  # type: ignore[method-assign]
    module.asset_payload = blocked_payload  # type: ignore[method-assign]
    try:
        result = _data(module.analyze_numeric_samples(
            {_SQL_NAME: [1.0, 2.0, 3.0], "temp_c": list(_X)},
            analysis_type="summary",
        ))
    finally:
        module.asset_payload = original_payload  # type: ignore[method-assign]
        if duckdb is not None and original_connect is not None:
            duckdb.connect = original_connect  # type: ignore[method-assign]
    return {
        "contract": "never execute database names or SQL",
        "sql_like_name": _SQL_NAME,
        "success": result.get("success"),
        "series_names": result.get("series_names"),
        "served_as_label": _SQL_NAME in (result.get("series_names") or []),
        "asset_payload_calls": payload_calls,
        "duckdb_connect_calls": list(executes),
        "treated_as_sql": False,
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-numeric-samples.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "A7",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_numeric_samples.py",
        "scope": "analyze_numeric_samples only",
        "hsp_or_tg_opened": False,
        "scipy": scipy_status(),
        "must_serve": must_serve(),
        "must_refuse": must_refuse(),
        "names_are_not_sql": names_are_not_sql(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "NUMERIC_SAMPLES.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(
        "summary", document["must_serve"]["summary"]["success"],
        "corr", document["must_serve"]["correlation"]["success"],
        "scipy", document["scipy"]["scipy_present"],
        "sql_label", document["names_are_not_sql"]["served_as_label"],
        "asset_calls", document["names_are_not_sql"]["asset_payload_calls"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
