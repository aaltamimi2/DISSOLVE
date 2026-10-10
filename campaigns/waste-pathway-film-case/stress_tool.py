"""Stress test of optimize_waste_pathway on feeds and inputs it was not built around.

    .venv/bin/python campaigns/waste-pathway-film-case/stress_tool.py [--fast]

Each case calls the tool directly and records whether it returned an answer, a stated refusal, or crashed (a crash is a bug).
--fast runs only the cases that need no simulation. Results go to stress_tool.json beside this file.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

from dissolve import waste_pathway as tool

HERE = Path(__file__).resolve().parent
FILM = {"LDPE": 0.6, "PET": 0.2, "NYLON6": 0.1, "EVOH": 0.1}

# (name, kwargs, needs_simulation)
CASES = [
    ("bad_fraction_sum", dict(feed_mass_fractions={"LDPE": 0.5, "EVOH": 0.3}, feed_tonnes_per_year=1000), False),
    ("unknown_polymer", dict(feed_mass_fractions={"unobtainium": 1.0}, feed_tonnes_per_year=1000), False),
    ("zero_tonnes", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=0), False),
    ("negative_tonnes", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=-5), False),
    ("negative_fraction", dict(feed_mass_fractions={"LDPE": 1.2, "EVOH": -0.2}, feed_tonnes_per_year=1000), False),
    ("empty_feed", dict(feed_mass_fractions={}, feed_tonnes_per_year=1000), False),
    ("string_numbers", dict(feed_mass_fractions={"LDPE": "0.6", "EVOH": "0.4"}, feed_tonnes_per_year="2000"), False),
    ("nonnumeric_fraction", dict(feed_mass_fractions={"LDPE": "lots", "EVOH": 0.4}, feed_tonnes_per_year=1000), False),
    ("duplicate_labels", dict(feed_mass_fractions={"PE": 0.5, "LDPE": 0.2, "EVOH": 0.3}, feed_tonnes_per_year=1000), False),
    ("generic_name_polyethylene", dict(feed_mass_fractions={"polyethylene": 0.7, "EVOH": 0.3}, feed_tonnes_per_year=1000), False),
    ("max_washes_zero_is_downstream_only", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, max_washes=0), False),
    ("max_washes_nine", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, max_washes=9), False),
    ("shared_plant_too_small", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, shared_plant_capacity_mt_per_yr=1000), False),
    ("price_for_absent_polymer", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, polymer_prices_usd_per_t={"PVC": 900}), False),
    ("unknown_distance_site", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, downstream_distances_mile={"airport": 5}), False),
    ("no_confirmation_asked_for", dict(feed_mass_fractions={"LDPE": 0.5, "EVOH": 0.5}, feed_tonnes_per_year=3000), False),
    ("single_polymer_feed", dict(feed_mass_fractions={"LDPE": 1.0}, feed_tonnes_per_year=5000, confirm_live_tea=True), False),
    ("no_costable_solvent_named", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, solvents=["Tetrachloroethylene"]), False),
    # these need live simulations
    ("binary_ldpe_evoh", dict(feed_mass_fractions={"LDPE": 0.7, "EVOH": 0.3}, feed_tonnes_per_year=5000, confirm_live_tea=True), True),
    ("pet_rich", dict(feed_mass_fractions={"PET": 0.7, "LDPE": 0.3}, feed_tonnes_per_year=8000, confirm_live_tea=True), True),
    ("polycarbonate_mix", dict(feed_mass_fractions={"PC": 0.4, "PET": 0.3, "EVOH": 0.3}, feed_tonnes_per_year=6000, confirm_live_tea=True), True),
    ("hdpe_ldpe_only", dict(feed_mass_fractions={"HDPE": 0.5, "LDPE": 0.5}, feed_tonnes_per_year=4000, confirm_live_tea=True), True),
    ("five_polymers_with_unsupported_ps", dict(feed_mass_fractions={"LDPE": 0.4, "HDPE": 0.2, "PET": 0.15, "EVOH": 0.15, "PS": 0.1},
                                               feed_tonnes_per_year=10000, confirm_live_tea=True), True),
    ("percent_input_and_names", dict(feed_mass_fractions={"ldpe": 60, "pet": 20, "nylon 6": 10, "evoh": 10},
                                     feed_tonnes_per_year=8000, confirm_live_tea=True), True),
    ("tiny_feed", dict(feed_mass_fractions={"LDPE": 0.7, "EVOH": 0.3}, feed_tonnes_per_year=100, confirm_live_tea=True), True),
    ("huge_feed", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=200000, confirm_live_tea=True), True),
    ("one_wash_only", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, max_washes=1, confirm_live_tea=True), True),
    ("three_washes_allowed", dict(feed_mass_fractions={"LDPE": 0.4, "HDPE": 0.2, "PET": 0.2, "EVOH": 0.2}, feed_tonnes_per_year=8000,
                                  max_washes=3, confirm_live_tea=True), True),
    ("energy_case_c2", dict(feed_mass_fractions={"LDPE": 0.7, "EVOH": 0.3}, feed_tonnes_per_year=5000, energy_case="C2", confirm_live_tea=True), True),
    ("restricted_solvents", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, solvents=["Toluene", "Ethylene glycol"],
                                 confirm_live_tea=True), True),
    ("stricter_thermo_limits", dict(feed_mass_fractions=FILM, feed_tonnes_per_year=8000, min_solubility_pct=25, max_retained_pct=0.5,
                                    confirm_live_tea=True), True),
    ("price_override_unpriced_polymer", dict(feed_mass_fractions={"PC": 0.4, "PET": 0.3, "EVOH": 0.3}, feed_tonnes_per_year=6000,
                                             polymer_prices_usd_per_t={"PC": 2500}, confirm_live_tea=True), True),
    ("circularity_objective_pareto", dict(feed_mass_fractions={"LDPE": 0.7, "EVOH": 0.3}, feed_tonnes_per_year=5000,
                                          pareto="circularity_vs_profit", confirm_live_tea=True), True),
]


def summarize(payload: dict) -> dict:
    data = payload.get("data") or {}
    out = {"success": data.get("success"), "error_code": data.get("error_code"), "error": str(data.get("error") or "")[:240]}
    if data.get("success"):
        sel = data.get("selected")
        out.update({
            "analysis": data.get("analysis_type"), "basis": data.get("basis"), "candidates": data.get("candidates_considered"),
            "washes_costed": data.get("washes_costed"), "no_admissible_wash": data.get("polymers_with_no_admissible_wash"),
            "failed_washes": [f"{f.get('polymer')}:{f.get('reason')}" for f in data.get("washes_that_failed") or []][:6],
            "warnings": data.get("warnings"),
        })
        if sel:
            out["selected"] = {"pathway": sel["pathway"], "profit": round(sel["profit_usd_per_yr"]), "emissions_t": round(sel["emissions_t_co2e_per_yr"], 1),
                               "circularity": round(sel["circularity_index"], 3)}
            out["circularity_in_0_1"] = all(0.0 <= v <= 1.0 for v in sel["circularity_scores"].values()) and 0.0 <= sel["circularity_index"] <= 1.0
            out["milp_agrees"] = data.get("milp_agrees_with_enumeration")
        if data.get("frontier_points"):
            out["front_points"] = len(data["frontier_points"])
    elif data.get("pending_stages"):
        out["pending_stages"] = len(data["pending_stages"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()
    results = {}
    for name, kwargs, sims in CASES:
        if args.fast and sims:
            continue
        started = time.monotonic()
        try:
            payload = json.loads(tool.optimize_waste_pathway(**kwargs))
            row = summarize(payload)
            row["crashed"] = False
        except Exception as error:  # a crash is a bug: record it with its trace
            row = {"crashed": True, "exception": f"{type(error).__name__}: {error}", "trace": traceback.format_exc()[-900:]}
        row["seconds"] = round(time.monotonic() - started, 1)
        results[name] = row
        verdict = "CRASH" if row["crashed"] else ("ok" if row.get("success") else f"refused[{row.get('error_code')}]")
        print(f"{name:36s} {verdict:34s} {row['seconds']:6.1f}s  {json.dumps({k: v for k, v in row.items() if k in ('selected', 'no_admissible_wash', 'failed_washes', 'warnings', 'error', 'exception', 'candidates')})[:420]}", flush=True)
        (HERE / "stress_tool.json").write_text(json.dumps(results, indent=1, default=str))


if __name__ == "__main__":
    main()
