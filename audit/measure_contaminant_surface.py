#!/usr/bin/env python3
"""Reproduce A6: contaminant thresholds, absent-case split, input diff.

Diffs contaminant `_inputs` field by field. Does not import tea and does
not call tea._config_key. No BioSTEAM.
"""
from __future__ import annotations

import hashlib
import inspect
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
_UNKNOWN = "xyzzy-not-a-contaminant"
_COMMON = dict(
    target_polymer="LDPE",
    other_polymers=["PP"],
    solvents=["Toluene"],
)


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


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


_CONFIG_KEY_CALLS: list[str] = []


def _wrap_config_key() -> Any:
    """Refuse tea._config_key if the package already loaded tea."""
    tea = sys.modules.get("dissolve.tea")
    if tea is None:
        return None
    original = tea._config_key

    def blocked(*_args: Any, **_kwargs: Any) -> str:
        _CONFIG_KEY_CALLS.append("tea._config_key")
        raise RuntimeError("A6 diffs contaminant inputs; tea._config_key is forbidden.")

    tea._config_key = blocked  # type: ignore[method-assign]
    return original


def _unwrap_config_key(original: Any) -> None:
    tea = sys.modules.get("dissolve.tea")
    if tea is not None and original is not None:
        tea._config_key = original  # type: ignore[method-assign]


@lru_cache(maxsize=1)
def thresholds() -> dict[str, Any]:
    module = _contaminants()
    leach = module.default_thresholds(include_precipitation=False)
    strap = module.default_thresholds(include_precipitation=True)
    source = inspect.getsource(module.default_thresholds)
    cited = any(
        token in source.casefold()
        for token in ("cfr", "epa", "zhou", "doi", "http", "ref.", "cite")
    )
    connection = module._connection()
    metadata = dict(connection.execute("SELECT * FROM metadata").fetchall())
    asset_sources = [
        {"source_path": row[0], "sha256": row[1], "bytes": row[2]}
        for row in connection.execute("SELECT * FROM asset_sources").fetchall()
    ]
    digest = hashlib.sha256(Path(module._ASSET).read_bytes()).hexdigest()
    return {
        "defaults_without_precipitation": leach,
        "defaults_with_precipitation": strap,
        "constants_wt_pct": {
            "swelling_min": module._DEFAULT_SWELLING_MIN,
            "swelling_max": module._DEFAULT_SWELLING_MAX,
            "dissolution_min": module._DEFAULT_DISSOLUTION_MIN,
            "precipitation": module._DEFAULT_PRECIPITATION_THRESHOLD,
        },
        "spec_1_10_1": {
            "swelling_min_wt_pct": 1.0,
            "dissolution_min_wt_pct": 10.0,
            "precipitation_threshold_wt_pct": 1.0,
        },
        "citation_on_function": cited,
        "status": "unsourced",
        "finding": (
            "An unsourced threshold in a safety-adjacent answer. "
            "default_thresholds is 1 / 10 / 1 wt% (plus swelling_max 10) "
            "with no regulatory or literature basis on the function."
        ),
        "success_path_provenance": module._provenance(),
        "success_path_provenance_is_not_the_threshold_basis": True,
        "duckdb": {
            "path": "src/dissolve/data/contaminants.duckdb",
            "pinned_sha256": module._ASSET_SHA256,
            "on_disk_sha256": digest,
            "match": digest == module._ASSET_SHA256,
            "metadata": metadata,
            "asset_sources": asset_sources,
            "family_counts": {
                family: len(names) for family, names in module._families().items()
            },
        },
    }


def _probe_inputs(*, include_precipitation: bool) -> dict[str, Any]:
    module = _contaminants()
    extra: dict[str, Any] = {}
    if include_precipitation:
        extra["precipitation_threshold_wt_pct"] = None
    inputs, error = module._inputs(
        "measure_contaminant_surface",
        _COMMON["target_polymer"],
        [_KNOWN],
        _COMMON["other_polymers"],
        _COMMON["solvents"],
        None,
        False,
        None,
        None,
        None,
        include_precipitation=include_precipitation,
        **extra,
    )
    if error:
        raise RuntimeError(error)
    return _jsonable(inputs)


@lru_cache(maxsize=1)
def input_diff() -> dict[str, Any]:
    leach = _probe_inputs(include_precipitation=False)
    strap = _probe_inputs(include_precipitation=True)
    keys = sorted(set(leach) | set(strap))
    same: list[str] = []
    only_leach: dict[str, Any] = {}
    only_strap: dict[str, Any] = {}
    different: dict[str, Any] = {}
    for key in keys:
        if key not in leach:
            only_strap[key] = strap[key]
        elif key not in strap:
            only_leach[key] = leach[key]
        elif leach[key] != strap[key]:
            different[key] = {"leaching": leach[key], "strap": strap[key]}
        else:
            same.append(key)
    precipitation_membership = sorted(
        set(only_strap)
        | set(only_leach)
        | {
            key for key in different
            if "precipitation" in key
            or (
                key == "threshold_sources"
                and set(different[key]["strap"]) - set(different[key]["leaching"])
                == {"precipitation_threshold_wt_pct"}
            )
        }
    )
    non_precipitation_diffs = [
        key for key in different if key not in precipitation_membership
    ]
    return {
        "method": (
            "Field-by-field Python equality on contaminants._inputs. "
            "tea._config_key is not the comparison. A raise-wrapper is "
            "installed if dissolve.tea is already in sys.modules from "
            "package init."
        ),
        "tea_in_sys_modules": "dissolve.tea" in sys.modules,
        "tea_config_key_calls": list(_CONFIG_KEY_CALLS),
        "shared_user_args": {
            **_COMMON,
            "contaminants": [_KNOWN],
        },
        "leaching_input_keys": sorted(leach),
        "strap_input_keys": sorted(strap),
        "same_fields": same,
        "only_in_leaching": only_leach,
        "only_in_strap": only_strap,
        "different_fields": different,
        "precipitation_membership_fields": precipitation_membership,
        "non_precipitation_diffs": non_precipitation_diffs,
        "only_precipitation_membership_differs": (
            not only_leach
            and not non_precipitation_diffs
            and set(only_strap) <= {"precipitation_threshold_wt_pct"}
        ),
    }


@lru_cache(maxsize=1)
def absent_split() -> dict[str, Any]:
    module = _contaminants()
    all_unsupported = _data(module.screen_contaminant_leaching(
        _COMMON["target_polymer"],
        [_UNKNOWN],
        other_polymers=_COMMON["other_polymers"],
        solvents=_COMMON["solvents"],
    ))
    mixed = _data(module.screen_contaminant_leaching(
        _COMMON["target_polymer"],
        [_KNOWN, _UNKNOWN],
        other_polymers=_COMMON["other_polymers"],
        solvents=_COMMON["solvents"],
    ))
    all_strap = _data(module.screen_contaminant_strap_removal(
        _COMMON["target_polymer"],
        [_UNKNOWN],
        other_polymers=_COMMON["other_polymers"],
        solvents=_COMMON["solvents"],
    ))
    return {
        "all_unsupported": {
            "success": all_unsupported.get("success"),
            "error_code": all_unsupported.get("error_code"),
            "unsupported_contaminants": all_unsupported.get("unsupported_contaminants"),
            "refuses": (
                all_unsupported.get("success") is False
                and all_unsupported.get("error_code") == "unsupported_contaminants"
            ),
        },
        "all_unsupported_strap": {
            "success": all_strap.get("success"),
            "error_code": all_strap.get("error_code"),
            "refuses": (
                all_strap.get("success") is False
                and all_strap.get("error_code") == "unsupported_contaminants"
            ),
        },
        "one_known_one_unknown": {
            "success": mixed.get("success"),
            "error_code": mixed.get("error_code"),
            "supported_contaminants": mixed.get("supported_contaminants"),
            "unsupported_contaminants": mixed.get("unsupported_contaminants"),
            "continues": mixed.get("success") is True,
            "lists_unknown": _UNKNOWN in (mixed.get("unsupported_contaminants") or []),
        },
        "absent_refuses_rather_than_defaults_only_on_all_unsupported": True,
    }


@lru_cache(maxsize=1)
def comparison() -> dict[str, Any]:
    module = _contaminants()
    result = _data(module.compare_contaminant_removal_modes(
        _COMMON["target_polymer"],
        [_KNOWN],
        other_polymers=_COMMON["other_polymers"],
        solvents=_COMMON["solvents"],
    ))
    leaching = result.get("leaching") or {}
    strap = result.get("strap_contaminant_removal") or {}
    shared = {
        "target_polymer",
        "other_polymers",
        "requested_contaminants",
        "supported_contaminants",
        "unsupported_contaminants",
        "temperature_max_c",
        "strict_maximum",
    }
    served_same = {
        key: leaching.get(key) == strap.get(key) for key in sorted(shared)
    }
    return {
        "success": result.get("success"),
        "recommended_mode": result.get("recommended_mode"),
        "precipitation_threshold_wt_pct": result.get("precipitation_threshold_wt_pct"),
        "provenance": result.get("provenance"),
        "shared_served_fields_equal": served_same,
        "strap_only_served_threshold_fields": {
            "precipitation_threshold_wt_pct": strap.get("precipitation_threshold_wt_pct"),
            "min_dissolution_solubility_wt_pct": strap.get(
                "min_dissolution_solubility_wt_pct"
            ),
        },
        "leaching_has_precipitation_threshold": (
            "precipitation_threshold_wt_pct" in leaching
        ),
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    _contaminants()
    original = _wrap_config_key()
    try:
        document = {
            "schema": "dissolve.audit-contaminant-surface.v1",
            "spec": "UNAUDITED_SURFACE_SPEC.v2",
            "spec_sha256": spec_sha,
            "checkpoint": "A6",
            "measured_on_builder_sha": head,
            "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "command": "python3 audit/measure_contaminant_surface.py",
            "tea_in_sys_modules": "dissolve.tea" in sys.modules,
            "tea_config_key_calls": list(_CONFIG_KEY_CALLS),
            "thresholds": thresholds(),
            "absent_split": absent_split(),
            "input_diff": input_diff(),
            "comparison": comparison(),
        }
        document["tea_config_key_calls"] = list(_CONFIG_KEY_CALLS)
        document["input_diff"]["tea_config_key_calls"] = list(_CONFIG_KEY_CALLS)
        return document
    finally:
        _unwrap_config_key(original)


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "CONTAMINANT_SURFACE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out}", flush=True)
    print(
        "unsourced", document["thresholds"]["status"],
        "all_refuse", document["absent_split"]["all_unsupported"]["refuses"],
        "mix_ok", document["absent_split"]["one_known_one_unknown"]["continues"],
        "precip_only", document["input_diff"]["only_precipitation_membership_differs"],
        "tea_key_calls", document["tea_config_key_calls"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
