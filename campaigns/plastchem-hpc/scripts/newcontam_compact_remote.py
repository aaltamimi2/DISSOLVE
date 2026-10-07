"""Runs on Euler inside one A-11 thermodynamics batch. For every sealed plan, verify each payload against its seal and
write results-compact/<plan>.jsonl.gz: partition rows without the 31-entry conformer digest lists, and LLE systems
without their activity grids (the raw sealed files stay on Euler). One line per row or system, plus one header line
carrying the plan's CPU models and job ids."""
import gzip
import hashlib
import json
import sys
from pathlib import Path

batch = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
out = batch / "results-compact"
out.mkdir(exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sealed(path):
    seal = json.loads(path.with_name(path.name + ".sha256.json").read_text())
    assert sha(path) == seal["sha256"], f"{path} does not match its seal"
    return json.loads(path.read_text())


done = 0
for plan_dir in sorted((batch / "results").iterdir()):
    complete = plan_dir / "complete.json"
    target = out / f"{plan_dir.name}.jsonl.gz"
    if not complete.exists() or target.exists():
        continue
    header = {"plan": plan_dir.name, "complete": json.loads(complete.read_text()), "cpu_models": [], "job_ids": []}
    lines = []
    if plan_dir.name.startswith("partition-"):
        for path in sorted((plan_dir / "activities").glob("*.json")):
            if path.name.endswith(".sha256.json"):
                continue
            execution = sealed(path)["execution"]
            header["cpu_models"].append(execution["cpu_model"])
            header["job_ids"].append(str(execution["job_id"]))
        for path in sorted((plan_dir / "partition").rglob("*.json")):
            if path.name.endswith(".sha256.json"):
                continue
            for row in sealed(path):
                row.pop("polymer_surface_sha256", None)
                lines.append(row)
    else:
        for path in sorted((plan_dir / "systems").glob("*.json")):
            if path.name.endswith(".sha256.json"):
                continue
            value = sealed(path)
            value.pop("activities", None)
            value["raw_sha256"] = sha(path)
            header["cpu_models"].append(value["execution"]["cpu_model"])
            header["job_ids"].append(str(value["execution"]["job_id"]))
            lines.append(value)
    header["cpu_models"] = sorted(set(header["cpu_models"]))
    header["job_ids"] = sorted(set(header["job_ids"]))
    tmp = target.with_suffix(".tmp")
    with gzip.open(tmp, "wt") as handle:
        handle.write(json.dumps(header) + "\n")
        for line in lines:
            handle.write(json.dumps(line, separators=(",", ":")) + "\n")
    tmp.replace(target)
    done += 1
print(json.dumps({"compacted_now": done, "compact_files": len(list(out.glob("*.jsonl.gz")))}))
