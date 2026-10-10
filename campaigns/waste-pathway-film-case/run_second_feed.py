"""Generalization check: the same tool, unchanged, on a different feed (12,000 t/yr of 40% HDPE, 25% LDPE, 20% PET, 15% EVOH).

    .venv/bin/python campaigns/waste-pathway-film-case/run_second_feed.py

Nothing here is specific to the first case study's feed: the polymers, the washes, their solvents and temperatures, and
the pathway all come out of the screen, the simulations and the optimizer. Writes second_feed.json beside this file.
"""
from __future__ import annotations

import json
from pathlib import Path

from dissolve import waste_pathway as tool

HERE = Path(__file__).resolve().parent
FEED = {"HDPE": 0.40, "LDPE": 0.25, "PET": 0.20, "EVOH": 0.15}


def main() -> None:
    out = {"feed": FEED, "tonnes_per_year": 12000.0, "runs": {}}
    for label, kwargs in {"optimum_dedicated": {"objective": "max_profit"},
                          "optimum_shared_20kt": {"objective": "max_profit", "shared_plant_capacity_mt_per_yr": 20000},
                          "pareto_emissions_vs_profit": {"pareto": "emissions_vs_profit"}}.items():
        payload = json.loads(tool.optimize_waste_pathway(feed_mass_fractions=FEED, feed_tonnes_per_year=12000,
                                                         location_scenario="A", confirm_live_tea=True, **kwargs))
        out["runs"][label] = payload["data"]
        data = payload["data"]
        print(label, "success" if data.get("success") else f"FAILED {data.get('error_code')}: {str(data.get('error'))[:150]}")
        if data.get("success") and "optima_by_objective" in data:
            for objective, row in data["optima_by_objective"].items():
                print(f"   {objective:16s} {row['pathway']:75s} ${row['profit_usd_per_yr']:12,.0f} {row['emissions_t_co2e_per_yr']:9,.0f} t  CE {row['circularity_index']:.3f}")
        print("   no admissible wash:", data.get("polymers_with_no_admissible_wash"), "| washes costed:", data.get("washes_costed"),
              "| failed:", [f"{f['polymer']}:{f['reason']}" for f in data.get("washes_that_failed", [])][:4], flush=True)
    (HERE / "second_feed.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
