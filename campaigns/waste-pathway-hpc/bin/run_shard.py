"""Run a shard of wash configurations through DISSOLVE's own TEA path with a warm fork server.

    run_shard.py MANIFEST.jsonl OUT.runs.jsonl.gz [--limit N]

Each manifest line is {"id", "config": <public process_config>, ...}. Each output record is {"id", "ok", "row" | "error",
"wall_s"} (gzip, one member per record); ids already in OUT are skipped, so a killed shard resumes.
"""
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import campaign_common as common

args = [a for a in sys.argv[1:] if not a.startswith("--")]
manifest, out_path = args[0], args[1]
limit = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
server = common.start()
from dissolve import waste_pathway_data as data_layer  # noqa: E402

done = common.done_ids(out_path)
count = 0
with open(manifest) as source:
    for line in source:
        item = json.loads(line)
        if item["id"] in done:
            continue
        if limit is not None and count >= limit:
            break
        started = time.monotonic()
        record = {"id": item["id"]}
        try:
            record.update(ok=True, row=data_layer.run_process_config(item["config"], confirm_live_tea=True))
        except data_layer.StageUnavailable as error:
            record.update(ok=False, error=[str(a) for a in error.args])
        except Exception as error:  # a configuration the engine refuses outright is a result, not a crash of the shard
            record.update(ok=False, error=[type(error).__name__, str(error)[:500]])
        record["wall_s"] = round(time.monotonic() - started, 3)
        data_layer.clear_stage_rows()
        common.append_gz(out_path, record)
        count += 1
common.stop(server)
print(f"ran {count}, skipped {len(done)}")
