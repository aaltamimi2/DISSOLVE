#!/usr/bin/env python3
"""Reproduce A1: line+branch coverage of the four unaudited modules.

Coverage of a line is not an audit of that line. This script is the map.

DuckDB's C extension fails if coverage starts tracing before duckdb is
imported (anaconda 3.11: ``_duckdb._sqltypes``). Load duckdb first, then
start the tracer. No BioSTEAM child.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_MODULES = (
    "analysis",
    "separation",
    "contaminants",
    "safety",
)
_INCLUDED = (
    "tests/test_planner_breadth.py",
    "tests/test_agent_tools.py",
    "tests/test_acceptance.py",
    "tests/test_screen_to_economics_order.py",
    "tests/test_cli.py",
)
_OMIT_REASONS = {
    "tests/test_rank_solvent_maps.py": (
        "13824 LOC TEA-heavy rank fixture; OOM risk on the 15 GB box"
    ),
    "tests/test_evaluate_process.py": "TEA evaluate-process lane; not A1-A7 scope",
    "tests/test_cli_process_sheet.py": "TEA confirmation sheet; not A1-A7 scope",
    "tests/test_tea_preflight.py": "TEA preflight probe; not A1-A7 scope",
    "tests/test_tea_exposed_coefficients.py": "TEA coefficients; not A1-A7 scope",
    "tests/test_tea_polymer_parameters.py": "TEA polymer parameters; not A1-A7 scope",
    "tests/test_tea_design_point.py": "TEA design point; not A1-A7 scope",
    "tests/test_tea_flowsheet_switches.py": "TEA flowsheet switches; not A1-A7 scope",
    "tests/test_sheet_standing_preview.py": "TEA sheet standing; not A1-A7 scope",
    "tests/test_stage1_tea.py": "TEA stage-1; not A1-A7 scope",
    "tests/test_nonfinite_served_tea.py": "TEA served numbers; not A1-A7 scope",
    "tests/test_residual_route.py": "TEA residual route; not A1-A7 scope",
    "tests/test_sensitivity_capacity_labor.py": "TEA sensitivity; not A1-A7 scope",
    "tests/test_sensitivity_handle_inherit.py": "TEA sensitivity inherit; not A1-A7 scope",
    "tests/test_rank_evaluate_handle.py": "TEA rank/evaluate handle; not A1-A7 scope",
    "tests/test_rank_campaign_handle.py": "TEA rank campaign handle; not A1-A7 scope",
    "tests/test_rank_landscape.py": "TEA rank landscape; not A1-A7 scope",
    "tests/test_rank_handle_inherit.py": "TEA rank inherit; not A1-A7 scope",
    "tests/test_economics_handle_inherit.py": "TEA economics inherit; not A1-A7 scope",
    "tests/test_incomplete_process_config.py": "TEA incomplete twelve; not A1-A7 scope",
    "tests/test_lookup_parity.py": "TEA lookup parity; not A1-A7 scope",
    "tests/test_not_applicable_in_mode.py": "TEA mode-gate; not A1-A7 scope",
    "tests/test_screening_handoff.py": "TEA screening handoff; not A1-A7 scope",
    "tests/test_evaluate_safety_standing.py": "TEA standing badge; not safety.py internals",
    "tests/test_campaign_consume.py": "TEA campaign consume; not A1-A7 scope",
    "tests/test_campaign_basis.py": "TEA campaign basis; not A1-A7 scope",
    "tests/test_solvent_scope.py": "thermo solvent roster; not the four modules",
    "tests/test_thermo.py": "frozen thermo.py suite; not the four modules",
}
_EXERCISED_THROUGH = {
    "analysis": [
        "registry import (session/dispatch load)",
        "tests/test_agent_tools.py dispatch lookup_glass_transition, lookup_hansen_parameters schema",
    ],
    "separation": [
        "registry import",
        "tests/test_planner_breadth.py dispatch plan_multistage_separation",
        "tests/test_agent_tools.py dispatch screen_precipitation_order",
    ],
    "contaminants": [
        "registry import",
        "tests/test_agent_tools.py dispatch screen_contaminant_leaching, compare_contaminant_removal_modes",
    ],
    "safety": [
        "tests/test_screen_to_economics_order.py direct import",
        "tests/test_agent_tools.py dispatch get_solvent_safety_card / screen_route_solvent_substitutions",
        "tests/test_acceptance.py dispatch compare_solvent_safety_at_conditions",
        "tests/test_cli.py get_solvent_safety_card rendering",
    ],
}


def _ranges(lines: list[int]) -> list[list[int]]:
    if not lines:
        return []
    ordered = sorted(set(int(n) for n in lines))
    start = prev = ordered[0]
    out: list[list[int]] = []
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append([start, prev] if start != prev else [start, start])
        start = prev = n
    out.append([start, prev] if start != prev else [start, start])
    return out


def main() -> int:
    os.chdir(_ROOT)
    sys.path[:0] = [str(_ROOT), str(_SRC)]
    import duckdb  # noqa: F401  — must precede the coverage tracer
    import coverage
    import pytest

    covfile = _ROOT / ".coverage.a1"
    raw_json = _ROOT / "audit" / "_coverage_raw.json"
    out_json = _ROOT / "audit" / "COVERAGE.v1.json"

    all_tests = sorted(p.name for p in (_ROOT / "tests").glob("test_*.py"))
    omitted = []
    for name in all_tests:
        rel = f"tests/{name}"
        if rel in _INCLUDED:
            continue
        omitted.append({
            "path": rel,
            "reason": _OMIT_REASONS.get(rel, "not on the A1 measured path"),
        })

    cov = coverage.Coverage(
        branch=True,
        source=[f"dissolve.{name}" for name in _MODULES],
        data_file=str(covfile),
    )
    cov.erase()
    cov.start()
    from dissolve import analysis, contaminants, safety, separation
    assert analysis.__file__ and contaminants.__file__
    assert safety.__file__ and separation.__file__
    code = pytest.main([*_INCLUDED, "-q", "--tb=no"])
    cov.stop()
    cov.save()
    if code not in (0, pytest.ExitCode.OK):
        raise SystemExit(f"pytest failed with {code}")
    cov.json_report(outfile=str(raw_json))
    raw = json.loads(raw_json.read_text(encoding="utf-8"))
    files = raw.get("files") or {}
    modules = {}
    for name in _MODULES:
        rel = f"src/dissolve/{name}.py"
        payload = None
        for key, value in files.items():
            if key.endswith(f"dissolve/{name}.py") or key.endswith(f"dissolve\\{name}.py"):
                payload = value
                break
        if payload is None:
            raise SystemExit(f"coverage json missing {rel}")
        summary = payload.get("summary") or {}
        missing = list(payload.get("missing_lines") or [])
        missing_branches = list(payload.get("missing_branches") or [])
        n_branches = summary.get("num_branches") or 0
        modules[f"{name}.py"] = {
            "path": rel,
            "statements": summary.get("num_statements"),
            "covered_statements": summary.get("covered_lines"),
            "percent_statements": summary.get("percent_covered"),
            "branches": n_branches,
            "covered_branches": summary.get("covered_branches"),
            "percent_branches": (
                None if not n_branches
                else 100.0 * float(summary.get("covered_branches") or 0) / float(n_branches)
            ),
            "uncovered_line_ranges": _ranges(missing),
            "uncovered_lines": missing,
            "uncovered_branches": missing_branches,
            "import": (
                "indirect via dissolve.registry except safety "
                "(also imported directly by test_screen_to_economics_order)"
            ),
            "exercised_through": list(_EXERCISED_THROUGH[name]),
        }

    spec = Path("/home/aaltamimi2/dissolve-v12-audit/UNAUDITED_SURFACE_SPEC.v2.md")
    spec_sha = subprocess.check_output(
        ["sha256sum", str(spec)], text=True,
    ).split()[0]
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True,
    ).strip()
    document = {
        "schema": "dissolve.audit-coverage.v1",
        "spec": "UNAUDITED_SURFACE_SPEC.v2",
        "spec_sha256": spec_sha,
        "measured_on_builder_sha": head,
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supersedes": (
            "§1 test-import column. Do not quote that column after this file lands."
        ),
        "does_not_supersede": (
            "unexamined. Coverage of a line is not an audit of that line. "
            "A1 is a map. A2–A7 are the walks."
        ),
        "coverage_version": raw.get("meta", {}).get("version"),
        "branch": True,
        "duckdb_preload": (
            "import duckdb before coverage.Coverage.start(); "
            "coverage run CLI traces duckdb import and breaks _duckdb._sqltypes"
        ),
        "command": "python3 audit/measure_coverage.py",
        "pytest": {
            "exit_code": int(code),
            "included_files": list(_INCLUDED),
        },
        "records": {
            "analysis.py": (
                "indirect only: registry import plus "
                "tests/test_agent_tools.py dispatch lookup_glass_transition "
                "and lookup_hansen_parameters schema"
            ),
            "separation.py": (
                "indirect only: tests/test_planner_breadth.py "
                "plan_multistage_separation; "
                "tests/test_agent_tools.py screen_precipitation_order"
            ),
            "contaminants.py": (
                "indirect only: tests/test_agent_tools.py "
                "screen_contaminant_leaching and "
                "compare_contaminant_removal_modes"
            ),
            "safety.py": (
                "direct import in tests/test_screen_to_economics_order.py; "
                "also dispatch get_solvent_safety_card, "
                "compare_solvent_safety_at_conditions, "
                "screen_route_solvent_substitutions"
            ),
        },
        "suite": {
            "test_files_in_worktree": len(all_tests),
            "included": list(_INCLUDED),
            "omitted": omitted,
        },
        "modules": modules,
    }
    out_json.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    raw_json.unlink(missing_ok=True)
    print(f"wrote {out_json}", flush=True)
    for name, info in modules.items():
        print(
            f"  {name}: {info['covered_statements']}/{info['statements']} lines "
            f"({info['percent_statements']:.1f}%), "
            f"{len(info['uncovered_line_ranges'])} uncovered ranges",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
