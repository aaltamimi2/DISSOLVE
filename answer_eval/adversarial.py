"""55 adversarial counter-cases: each named broken implementation must fail its fixture/key.

All 55 rows are active. A row with zero demonstrations is a miss, never a pass.
"""

from __future__ import annotations

from typing import Any

from answer_eval.evaluate import evaluate_fixture
from answer_eval.reproduce import failing_keys
from answer_eval.tables import load_json

CASES: list[dict[str, Any]] = [
    {"id": 1, "mutation": "first_fit", "fixtures": ["F-MATCH-7"], "keys": ["matched"]},
    {"id": 2, "mutation": "post_match_repeats", "fixtures": ["F-COUNT-4", "F-ID-3"], "keys": ["P_graph"]},
    {"id": 3, "mutation": "drop_failures_from_den", "fixtures": ["F-COUNT-8"], "keys": ["M-1_primary"]},
    {"id": 4, "mutation": "truth_as_support", "fixtures": ["F-COUNT-1", "F-COUNT-2", "F-COUNT-3"], "keys": ["M-2"]},
    {"id": 5, "mutation": "descriptive_out_of_R", "fixtures": ["F-M4-2"], "keys": ["M-2"]},
    {"id": 6, "mutation": "signed_zero_canon", "fixtures": ["F-EQ-3"], "keys": ["canonical_equivalence"]},
    {"id": 7, "mutation": "nfkc_before_exp", "fixtures": ["F-EQ-10"], "keys": ["canonical_equivalence"]},
    {"id": 8, "mutation": "drop_negative_kappa", "fixtures": ["F-EMIT-1"], "keys": ["write_results"]},
    {"id": 9, "mutation": "discard_nonnumeric_prose", "fixtures": ["F-TEXT-1"], "keys": ["L1_breakdown"]},
    {"id": 10, "mutation": "omitted_as_refusal", "fixtures": ["F-REF-2"], "keys": ["M-8"]},
    {"id": 11, "mutation": "ambiguity_as_fp", "fixtures": ["F-REF-4"], "keys": ["M-8"]},
    {"id": 12, "mutation": "no_promoted_vertex", "fixtures": ["F-ID-6"], "keys": ["M-1"]},
    {"id": 13, "mutation": "silent_inexact", "fixtures": ["F-EQ-11"], "keys": ["canonical_equivalence"]},
    {"id": 14, "mutation": "partial_as_question_error", "fixtures": ["F-REF-8"], "keys": ["operational_error_rate"]},
    {"id": 15, "mutation": "skip_order", "fixtures": ["F-ID-4"], "keys": ["canonical_envelopes_equal", "content_to_slot_equal"]},
    {"id": 16, "mutation": "overwrite_collision", "fixtures": ["F-ID-9", "F-ID-11"], "keys": ["halt"]},
    {"id": 17, "mutation": "accept_ref_dup", "fixtures": ["F-ID-10"], "keys": ["halt"]},
    {"id": 18, "mutation": "copy_role_from_stub", "fixtures": ["F-COUNT-15"], "keys": ["M-7_subtypes"]},
    {"id": 19, "mutation": "q3_as_vertex", "fixtures": ["F-COUNT-11"], "keys": ["P_graph", "M-2"]},
    {"id": 20, "mutation": "unknown_unconvertible", "fixtures": ["F-EQ-12"], "keys": ["canonical_equivalence"]},
    {"id": 21, "mutation": "canon_unit_rejected", "fixtures": ["F-EQ-13"], "keys": ["canonical_equivalence"]},
    {"id": 22, "mutation": "seed_20260906", "fixtures": ["F-RS-1"], "keys": ["interval_serialized"]},
    {"id": 23, "mutation": "keep_zero_den", "fixtures": ["F-RS-3"], "keys": ["dropped_replicates"]},
    {"id": 24, "mutation": "copy_unknown_key_text", "fixtures": ["F-EMIT-2"], "keys": ["exact_error_log_records", "marker_absent_from_log"]},
    {"id": 25, "mutation": "accept_schema", "fixtures": ["F-EMIT-2"], "keys": ["write_results"]},
    {"id": 26, "mutation": "accept_schema", "fixtures": ["F-EMIT-3"], "keys": ["write_results"]},
    {"id": 27, "mutation": "accept_schema", "fixtures": ["F-EMIT-3"], "keys": ["write_results"]},
    {"id": 28, "mutation": "accept_schema", "fixtures": ["F-EMIT-3"], "keys": ["write_results"]},
    {"id": 29, "mutation": "cutoff_literal_200", "fixtures": ["F-RS-3"], "keys": ["interval_label"]},
    {"id": 30, "mutation": "omit_inner_draw", "fixtures": ["F-RS-4"], "keys": ["interval_serialized"]},
    {"id": 31, "mutation": "basis_from_reference", "fixtures": ["F-TEXT-1"], "keys": ["basis_source"]},
    {"id": 32, "mutation": "unprojected_q4", "fixtures": ["F-ID-1c"], "keys": ["matched"]},
    {"id": 33, "mutation": "drop_or_credit_q3_refusal", "fixtures": ["F-TEXT-3"], "keys": ["state", "M-8"]},
    {"id": 34, "mutation": "copy_unknown_key_text", "fixtures": ["F-EMIT-1"], "keys": ["error_log_records"]},
    {"id": 35, "mutation": "string_interval_endpoints", "fixtures": ["F-RS-1"], "keys": ["public_row_write_result"]},
    {"id": 36, "mutation": "m3t_from_m3", "fixtures": ["F-CB-1"], "keys": ["M-3t"]},
    {"id": 37, "mutation": "additional_folds_m3t", "fixtures": ["F-CB-4"], "keys": ["M-3t"]},
    {"id": 38, "mutation": "negative_in_m3t_den", "fixtures": ["F-CB-5"], "keys": ["M-3t"]},
    {"id": 39, "mutation": "credit_answered_negative", "fixtures": ["F-CB-6"], "keys": ["M-3t"]},
    {"id": 40, "mutation": "majority", "fixtures": ["F-STRAT-5"], "keys": ["closed_book"]},
    {"id": 41, "mutation": "omit_inner_draw", "fixtures": ["F-RS-5"], "keys": ["interval_serialized"]},
    {"id": 42, "mutation": "independent_families", "fixtures": ["F-RS-6"], "keys": ["interval_serialized"]},
    {"id": 43, "mutation": "casefold_excluded", "fixtures": ["F-PERT-1"], "keys": ["transformed_text"]},
    {"id": 44, "mutation": "accept_excluded_replacement", "fixtures": ["F-PERT-6"], "keys": ["invalid"]},
    {"id": 45, "mutation": "accept_schema", "fixtures": ["F-EMIT-4"], "keys": ["write_results"]},
    {"id": 46, "mutation": "count_ineligible_den", "fixtures": ["F-PERT-2", "F-PERT-3"], "keys": ["not_applicable", "in_denominator"]},
    {"id": 47, "mutation": "accept_schema", "fixtures": ["F-EMIT-5"], "keys": ["write_results"]},
    {"id": 48, "mutation": "accept_duplicate_axes", "fixtures": ["F-EMIT-5"], "keys": ["write_results"]},
    {"id": 49, "mutation": "skip_stub_visible", "fixtures": ["F-CB-10"], "keys": ["halt"]},
    {"id": 50, "mutation": "std", "alt_mutation": "run2_minus_run1", "fixtures": ["F-RS-8"], "keys": ["dispersion_serialized"]},
    {"id": 51, "mutation": "accept_schema", "fixtures": ["F-EMIT-6"], "keys": ["write_results"]},
    {"id": 52, "mutation": "accept_schema", "fixtures": ["F-EMIT-6"], "keys": ["write_results"]},
    {"id": 53, "mutation": "accept_schema", "fixtures": ["F-EMIT-6"], "keys": ["write_results"]},
    {"id": 54, "mutation": "accept_schema", "fixtures": ["F-EMIT-6"], "keys": ["write_results"]},
    {"id": 55, "mutation": "accept_schema", "fixtures": ["F-EMIT-7"], "keys": ["write_results"], "require_indices": [1, 3]},
]


def _by_id() -> dict[str, dict[str, Any]]:
    data = load_json("fixtures/FIXTURES.eval.v9.json")
    return {fx["fixture_id"]: fx for fx in data["fixtures"]}


def _key_failed(expected: dict, observed: dict, key: str) -> bool:
    fails = failing_keys(expected, observed)
    if key in fails:
        return True
    return any(f == key or f.startswith(key + ".") for f in fails)


def _write_index_flipped(expected: dict, observed: dict, indices: list[int]) -> bool:
    exp = expected.get("write_results") or []
    got = observed.get("write_results") or []
    if len(got) < max(indices) + 1 or len(exp) < max(indices) + 1:
        return False
    return all(exp[i] != got[i] for i in indices)


def run_case(case: dict[str, Any], fixtures: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    fixtures = fixtures or _by_id()
    mutations = [case["mutation"]]
    if case.get("alt_mutation"):
        mutations.append(case["alt_mutation"])
    rows = []
    for mut in mutations:
        for fid in case["fixtures"]:
            fx = fixtures[fid]
            try:
                got = evaluate_fixture(fx, mutation=mut)
            except Exception as exc:
                rows.append({"fixture_id": fid, "mutation": mut, "ok": False, "failed_keys": [type(exc).__name__], "halt": None})
                continue
            keys = case["keys"]
            if case.get("require_indices") is not None:
                ok = _write_index_flipped(fx["expected"], got, case["require_indices"])
                failed = ["write_results"] if ok else []
            else:
                failed = [k for k in keys if _key_failed(fx["expected"], got, k)]
                ok = all(_key_failed(fx["expected"], got, k) for k in keys)
            rows.append({"fixture_id": fid, "mutation": mut, "ok": ok, "failed_keys": failed, "halt": got.get("halt")})
    ok = all(r["ok"] for r in rows) and len(rows) > 0
    return {"id": case["id"], "ok": ok, "demonstrations": len(rows), "rows": rows}


def run_adversarial() -> dict[str, Any]:
    fixtures = _by_id()
    results = [run_case(c, fixtures) for c in CASES]
    n = len(CASES)
    zero_demo = [r["id"] for r in results if r["demonstrations"] < 1]
    n_ok = sum(1 for r in results if r["ok"] and r["demonstrations"] >= 1)
    return {
        "n": n,
        "n_ok": n_ok,
        "n_active": n,
        "n_active_ok": n_ok,
        "zero_demonstration_ids": zero_demo,
        "rows": results,
    }
