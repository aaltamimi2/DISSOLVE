#!/usr/bin/env python3
"""Reproduce A4: planner characterisation, greedy/window, precipitation/getter.

Pass/fail weight is items 2, 3, 4 — not the paper Table 2 match.
Mismatch with Sánchez-Rivera Table 2 is science, not a product FAIL.
No BioSTEAM. include_pubchem=False on precipitation.
"""
from __future__ import annotations

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

# Grid polymers that exist and overlap the paper's mix. ABS is identity-only.
# PA66/6 is not in POLYMER_IDENTITIES.
_GRID_FEED = ["LDPE", "PP", "PS", "PET", "NYLON66"]
_PAPER_TABLE2 = {
    "citation": "Sánchez-Rivera et al., Waste Management 194 (2025) Table 2",
    "kind": "human-chosen experimental protocol, not an engine input",
    "sequence": [
        {"solvent": "toluene", "temperature_c": 35},
        {"solvent": "THF", "temperature_c": 67},
        {"solvent": "o-xylene", "temperature_c": [80, 95, 115]},
        {"solvent": "DMSO/water", "temperature_c": 95},
        {"solvent": "1,2-PDO", "temperature_c": 125},
        {"solvent": "GVL", "temperature_c": 160},
        {"solvent": "DMSO", "temperature_c": 145},
        {"solvent": "formic acid", "temperature_c": 90},
    ],
    "residue": "PA66",
    "table1": "COSMO-RS at those already-chosen conditions",
}
_PRECIP_WARNING = (
    "The capacity threshold is a grade/MW-dependent loading proxy, not a "
    "measured cloud point or validated precipitation recovery, and does not "
    "predict cloud-point ordering."
)
_SCORE_KEYS = (
    "complete",
    "resolved_count",
    "bottleneck_selectivity_pct",
    "neg_cumulative_off_target_burden",
    "bottleneck_target_solubility_pct",
    "neg_peak_temperature_c",
)


def _sys_path() -> None:
    for path in (str(_ROOT), str(_SRC)):
        if path not in sys.path:
            sys.path.insert(0, path)


def _separation():
    _sys_path()
    from dissolve import separation
    return separation


def _thermo():
    _sys_path()
    from dissolve import thermodynamics as thermo
    return thermo


def _data(raw: str) -> dict[str, Any]:
    from dissolve.contracts import parse_tool_result
    return parse_tool_result(raw)["data"]


def _has_cloud_point_field(payload: dict[str, Any]) -> bool:
    found = []

    def walk(value: Any, prefix: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if key in {"cloud_point", "cloud_point_c", "cloud_points"}:
                    found.append(path)
                walk(item, path)
        elif isinstance(value, list):
            for index, item in enumerate(value[:8]):
                walk(item, f"{prefix}[{index}]")

    walk(payload, "")
    return bool(found)


@lru_cache(maxsize=1)
def identities() -> dict[str, Any]:
    thermo = _thermo()
    separation = _separation()
    hits = [
        key for key in thermo.POLYMER_IDENTITIES
        if "PA66/6" in key or key == "PA66/6"
    ]
    membership = _data(separation.lookup_material_database_membership(
        ["PA66/6", "PA66", "ABS", "PMMA", "LDPE"],
    ))
    return {
        "available_thermodynamic_polymers": sorted(thermo.get_available_polymers()),
        "PA66_6_in_POLYMER_IDENTITIES": bool(hits),
        "PA66_6_expand": list(thermo.expand_polymer_identity("PA66/6")),
        "PA66_6_identity": thermo.resolve_polymer_identity("PA66/6"),
        "PA66_expand": list(thermo.expand_polymer_identity("PA66")),
        "PA66_identity": thermo.resolve_polymer_identity("PA66"),
        "ABS_expand": list(thermo.expand_polymer_identity("ABS")),
        "membership": membership["results"],
        "paper_ten_polymer_feed_as_written": (
            "A feed that includes PA66/6 refuses unknown_polymer before any "
            "plan. That is not the paper's COSMOtherm 'PA66/6 uses the PA66 "
            "model, 90/10' calibration."
        ),
    }


@lru_cache(maxsize=1)
def engine_sequence() -> dict[str, Any]:
    separation = _separation()
    knobs = {
        "feed_polymers": list(_GRID_FEED),
        "branch_rule": None,
        "breadth": None,
        "top_k_routes": None,
        "temperature_step_c": 5.0,
        "min_target_solubility_pct": 5.0,
        "min_selectivity_pct": 5.0,
        "require_atmospheric": None,
    }
    payload = _data(separation.plan_multistage_separation(
        feed_polymers=list(_GRID_FEED),
    ))
    paper_like = _data(separation.plan_multistage_separation(
        feed_polymers=["PE", "PP", "PS", "PET", "PVC", "PC", "PA66/6"],
    ))
    steps = [
        {
            "step": item.get("step"),
            "dissolved_polymer": item.get("dissolved_polymer"),
            "solvent": item.get("solvent"),
            "temperature_c": item.get("temperature_c"),
            "selectivity_pct": item.get("selectivity_pct"),
            "g_score": item.get("g_score"),
        }
        for item in payload.get("steps") or []
    ]
    return {
        "knobs": knobs,
        "defaults_observed": {
            "branch_rule": payload.get("branch_rule"),
            "breadth": payload.get("breadth"),
            "top_k_routes": payload.get("top_k_routes"),
            "breadth_origin": payload.get("breadth_origin"),
            "beam_origin": payload.get("beam_origin"),
            "subset_screens_evaluated": payload.get("subset_screens_evaluated"),
        },
        "best_sequence": payload.get("best_sequence"),
        "final_residue": payload.get("final_residue"),
        "solvent_mapping": payload.get("solvent_mapping"),
        "peak_temperature_c": payload.get("peak_temperature_c"),
        "complete": payload.get("complete"),
        "steps": steps,
        "paper_table2": _PAPER_TABLE2,
        "divergence": (
            "The engine is a recursive beam over the stored grid (default "
            "m=1, B=5). It does not take Table 2 as input. The published "
            "sequence (NYLON66 / PET / PS / LDPE, PP residue) uses "
            "selectivity-greedy solvents, not toluene/THF/o-xylene. "
            "Mismatch is not a product FAIL."
        ),
        "paper_like_feed_with_PA66_6": {
            "success": paper_like.get("success"),
            "error_code": paper_like.get("error_code"),
            "unsupported_polymers": paper_like.get("unsupported_polymers"),
        },
    }


@lru_cache(maxsize=1)
def selection_rule() -> dict[str, Any]:
    separation = _separation()
    pair = ["LDPE", "PP"]
    default = _data(separation.plan_multistage_separation(feed_polymers=pair))
    window = _data(separation.plan_multistage_separation(
        feed_polymers=pair, branch_rule="window", selectivity_window_pct=5.0,
    ))
    all_token = _data(separation.plan_multistage_separation(
        feed_polymers=pair, breadth="all",
    ))
    source = inspect.getsource(separation.plan_multistage_separation)
    score_uses_g = "g_score" in source.split("def score(route", 1)[-1].split(
        "def solve", 1
    )[0]
    index = default.get("ranked_path_index") or []
    return {
        "default": {
            "branch_rule": default.get("branch_rule"),
            "breadth": default.get("breadth"),
            "top_k_routes": default.get("top_k_routes"),
            "stage_branch_token": default.get("stage_branch_token"),
            "best_sequence": default.get("best_sequence"),
            "rule": (
                "greedy in solvent per target (screened_directions "
                "best_candidate when m=1); exhaustive in which polymer is "
                "dissolved next; beam 5"
            ),
        },
        "window": {
            "success": window.get("success"),
            "branch_rule": window.get("branch_rule"),
            "selectivity_window_pct": window.get("selectivity_window_pct"),
            "best_sequence": window.get("best_sequence"),
            "reachable": window.get("success") is True
            and window.get("branch_rule") == "window",
        },
        "all_token": {
            "success": all_token.get("success"),
            "branch_rule": all_token.get("branch_rule"),
            "stage_branch_token": all_token.get("stage_branch_token"),
            "breadth": all_token.get("breadth"),
            "best_sequence": all_token.get("best_sequence"),
        },
        "score_function_keys": list(_SCORE_KEYS),
        "score_uses_g_score": score_uses_g,
        "ranked_path_index_records_min_stage_g_score": bool(
            index and "min_stage_g_score" in index[0]
        ),
        "rerank_by_g_score": False,
        "note": (
            "_ranked_path_index records min_stage_g_score; score() orders by "
            "completeness, resolved count, bottleneck selectivity, off-target "
            "burden, target solubility, and peak temperature. It does not "
            "use g_score."
        ),
    }


@lru_cache(maxsize=1)
def degenerates() -> dict[str, Any]:
    separation = _separation()
    thermo = _thermo()
    empty = _data(separation.plan_multistage_separation([]))
    singleton = _data(separation.plan_multistage_separation(["LDPE"]))
    not_a_list = _data(separation.plan_multistage_separation("LDPE"))  # type: ignore[arg-type]
    absent = _data(separation.plan_multistage_separation(["PMMA", "LDPE"]))
    scope = _data(separation.resolve_polymer_data_scope(["PMMA", "LDPE"]))
    identical = []
    for solvent in ("Dodecane", "Toluene", "Xylene", "Decane", "Octane"):
        key = thermo.resolve_solvent(solvent)
        for temperature in (25.0, 80.0, 105.0, 145.0):
            hdpe = thermo.get_solubility_result("HDPE", key, temperature)
            ldpe = thermo.get_solubility_result("LDPE", key, temperature)
            if hdpe.get("solubility_pct") is None or ldpe.get("solubility_pct") is None:
                continue
            if hdpe["solubility_pct"] == ldpe["solubility_pct"]:
                identical.append({
                    "solvent": solvent,
                    "temperature_c": temperature,
                    "solubility_pct": hdpe["solubility_pct"],
                })
    pair = _data(separation.plan_multistage_separation(["HDPE", "LDPE"]))
    return {
        "empty_feed": {
            "success": empty.get("success"),
            "error_code": empty.get("error_code"),
        },
        "singleton_feed": {
            "success": singleton.get("success"),
            "error_code": singleton.get("error_code"),
        },
        "non_list_feed": {
            "success": not_a_list.get("success"),
            "error_code": not_a_list.get("error_code"),
        },
        "absent_from_grid": {
            "planner": {
                "success": absent.get("success"),
                "error_code": absent.get("error_code"),
                "unsupported_polymers": absent.get("unsupported_polymers"),
            },
            "scope": {
                "supported": scope.get("supported_requested_polymers"),
                "hsp_fallback": scope.get("hsp_fallback_requested_polymers"),
            },
            "note": (
                "Planner refuses unknown polymers. HSP fallback is "
                "resolve_polymer_data_scope only."
            ),
        },
        "identical_solubility": {
            "hdpe_ldpe_equal_cells": identical,
            "planner_on_HDPE_LDPE": {
                "success": pair.get("success"),
                "complete": pair.get("complete"),
                "best_sequence": pair.get("best_sequence"),
                "solvent_mapping": pair.get("solvent_mapping"),
            },
            "note": (
                "HDPE and LDPE share the same stored value at 145 C on several "
                "solvents (often the 100 wt% clip). The planner still emits a "
                "sequence; identical cells are not a refuse."
            ),
        },
    }


@lru_cache(maxsize=1)
def precipitation_and_getter() -> dict[str, Any]:
    separation = _separation()
    precip = _data(separation.screen_precipitation_order(
        feed_polymers=["LDPE", "PP"],
        first_polymer="LDPE",
        second_polymer="PP",
        include_pubchem=False,
    ))
    recommended = precip.get("recommended_condition") or {}
    getter = _data(separation.screen_cool_then_reheat_getter(
        recovered_polymer="LDPE",
        sacrificial_getter_polymer="PP",
        solvent="octane",
    ))
    same = _data(separation.screen_cool_then_reheat_getter(
        recovered_polymer="LDPE",
        sacrificial_getter_polymer="LDPE",
        solvent="octane",
        dissolution_temperature_c=85,
        cool_temperature_c=25,
        reheat_temperature_c=85,
        getter_disposition="deliberately_not_recovered",
    ))
    omitted = _data(separation.screen_cool_then_reheat_getter(
        recovered_polymer="LDPE",
        solvent="octane",
        dissolution_temperature_c=85,
        cool_temperature_c=25,
        reheat_temperature_c=85,
        getter_disposition="deliberately_not_recovered",
    ))
    no_solvent = _data(separation.screen_cool_then_reheat_getter(
        recovered_polymer="LDPE",
        sacrificial_getter_polymer="PP",
    ))
    return {
        "precipitation": {
            "executed": precip.get("success") is True,
            "analysis_type": precip.get("analysis_type"),
            "evaluated_candidate_count": precip.get("evaluated_candidate_count"),
            "requested_direction_found": precip.get("requested_direction_found"),
            "recommended_solvent": recommended.get("solvent"),
            "first_precipitation_proxy_c": recommended.get("first_precipitation_proxy_c"),
            "serves_cloud_point_field": _has_cloud_point_field(precip),
            "warning": (precip.get("warnings") or [None])[0],
            "must_refuse_cloud_point": not _has_cloud_point_field(precip)
            and _PRECIP_WARNING in (precip.get("warnings") or []),
        },
        "getter_named_cycle": {
            "executed": getter.get("success") is True,
            "analysis_type": getter.get("analysis_type"),
            "solvent": getter.get("solvent") or getter.get("operating_solvent"),
            "serves_cloud_point_field": _has_cloud_point_field(getter),
            "warnings": getter.get("warnings"),
        },
        "getter_recovered_equals_getter": {
            "success": same.get("success"),
            "error_code": same.get("error_code"),
            "reason": ((same.get("missing_or_invalid_states") or [{}])[0] or {}).get("reason"),
        },
        "getter_omitted_searches_polymer_catalog": {
            "success": omitted.get("success"),
            "analysis_type": omitted.get("analysis_type"),
            "getter_search_axis": omitted.get("getter_search_axis"),
            "recommended_getters": omitted.get("recommended_getters"),
        },
        "named_cycle_does_not_invent_solvent": {
            "success": no_solvent.get("success"),
            "error_code": no_solvent.get("error_code"),
            "invented_solvent": no_solvent.get("solvent"),
        },
    }


def build_document() -> dict[str, Any]:
    spec_sha = subprocess.check_output(["sha256sum", str(_SPEC)], text=True).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    return {
        "schema": "dissolve.audit-planner-surface.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "checkpoint": "A4",
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "command": "python3 audit/measure_planner_surface.py",
        "pass_fail_weight": "items 2, 3, 4 — not the Table 2 match",
        "identities": identities(),
        "characterization": engine_sequence(),
        "selection_rule": selection_rule(),
        "degenerates": degenerates(),
        "precipitation_and_getter": precipitation_and_getter(),
    }


def main() -> int:
    _sys_path()
    document = build_document()
    out = _ROOT / "audit" / "PLANNER_SURFACE.v1.json"
    out.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    seq = document["characterization"]
    pg = document["precipitation_and_getter"]
    print(f"wrote {out}", flush=True)
    print(
        "seq", seq["best_sequence"],
        "paper_refuse", seq["paper_like_feed_with_PA66_6"]["error_code"],
        "window", document["selection_rule"]["window"]["reachable"],
        "g_rerank", document["selection_rule"]["rerank_by_g_score"],
        "precip", pg["precipitation"]["executed"],
        "getter", pg["getter_named_cycle"]["executed"],
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
