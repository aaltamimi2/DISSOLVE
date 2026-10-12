"""Run a shard of feed scenarios through the product's optimize_waste_pathway tool.

    run_feeds.py SCENARIOS.jsonl OUT_PREFIX [--limit N]

A scenario: {"id", "fractions", "tonnes", "energy_case", "shared_plant", "max_washes", "max_solvents", "locations",
"overrides" (process fields applied to every wash), "prices" (resin prices)}. Writes OUT_PREFIX.scenarios.jsonl.gz (one
line per scenario: per location the tool's answer and its saved candidates and exact fronts) and OUT_PREFIX.washes.jsonl.gz
(every wash simulated, with its full process configuration and result row). Both are gzip, one member per record.
"""
import gzip, json, os, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_common as common

args = [a for a in sys.argv[1:] if not a.startswith("--")]
source, prefix = Path(args[0]), args[1]
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
os.environ["DISSOLVE_PATHWAY_RESULTS_DIR"] = tempfile.mkdtemp(prefix="pathway-")
server = common.start(prefix + ".washes.jsonl.gz")
from dissolve import waste_pathway, waste_pathway_data as data_layer  # noqa: E402

base_process_config = data_layer.process_config
out_path = prefix + ".scenarios.jsonl.gz"
done = common.done_ids(out_path)
count = 0
with open(source) as scenarios:
    for line in scenarios:
        scenario = json.loads(line)
        if scenario["id"] in done:
            continue
        if limit is not None and count >= limit:
            break
        started = time.monotonic()
        overrides = dict(scenario.get("overrides") or {})
        price_factor = float(scenario.get("solvent_price_factor") or 1.0)

        def process_config(spec, *, energy_case="C1", overrides=None, _extra=overrides, _factor=price_factor):
            config = base_process_config(spec, energy_case=energy_case, overrides={**_extra, **dict(overrides or {})})
            if _factor != 1.0:
                config["solvent_price_usd_per_kg"] = float(config["solvent_price_usd_per_kg"]) * _factor
            return config

        data_layer.process_config = process_config
        data_layer.clear_stage_rows()
        record = {"id": scenario["id"], "scenario": scenario, "locations": {}}
        for location in scenario.get("locations") or ["A", "B"]:
            try:
                payload = json.loads(waste_pathway.optimize_waste_pathway(
                    scenario["fractions"], scenario["tonnes"], objective="max_profit",
                    max_washes=scenario.get("max_washes", 3), max_solvents_per_stage=scenario.get("max_solvents", 6),
                    location_scenario=location, energy_case=scenario.get("energy_case", "C1"), confirm_live_tea=True,
                    polymer_prices_usd_per_t=scenario.get("prices"),
                    shared_plant_capacity_mt_per_yr=scenario.get("shared_plant")))
                answer = payload.get("data") or payload
                figure_file = answer.get("figure_data_file")
                figure = json.loads(Path(figure_file).read_text()) if figure_file and Path(figure_file).exists() else None
                if figure_file and Path(figure_file).exists():
                    Path(figure_file).unlink()
                record["locations"][location] = {"ok": bool(payload.get("ok", True)), "answer": answer, "figure_data": figure}
            except Exception as error:
                record["locations"][location] = {"ok": False, "error": [type(error).__name__, str(error)[:800]]}
        record["wall_s"] = round(time.monotonic() - started, 2)
        common.append_gz(out_path, record)
        count += 1
common.stop(server)
print(f"ran {count}, skipped {len(done)}")
