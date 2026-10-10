"""A-13: write state/coverage-v1/STATUS.json from the lane's own state (the orchestrator reads it). Counts come from
summarize_coverage.py's summary, the harvest from the halogen records and the thermodynamics batches, releases from
state/coverage-v1/releases.json; the coverage figure is only ever the latest rerun of plastchem_coverage.py on the
served asset (state/coverage-v1/coverage-latest.json), never arithmetic on the lane's tallies.

    python3 scripts/coverage_status.py --next "..." [--blocker "..."] [--note "..."]"""
import argparse
import datetime
import json
import math
from pathlib import Path

R = Path(__file__).resolve().parents[1]
P = R / "state/coverage-v1"
B = Path("/mnt/r/plastchem-euler")
SIMULABLE = 9627


def fit_hours(atoms):
    return math.exp(-0.687758) * atoms ** 2.305719 * 1.113048 / 3600


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--next", required=True, dest="next_action")
    parser.add_argument("--blocker", action="append", default=[])
    parser.add_argument("--note", action="append", default=[])
    args = parser.parse_args()
    summary = load(P / "summary.json", {"counts": {}, "chunks": {}})
    counts = summary.get("counts", {})
    batches = {}
    for root in (B / "coverage-thermo-v1",):
        for meta in sorted(root.glob("*/batch.json")):
            m = json.loads(meta.read_text())
            folder = meta.parent
            batches[m["batch"]] = dict(source=m["source"], structures=len(m["keys"]), partition_plans=m["partition_plans"],
                                       lle_plans=m["lle_plans"], submitted=(folder / "submission.json").exists(),
                                       array_ids=load(folder / "submission.json", {}).get("array_ids"),
                                       collected=(folder / "collection.json").exists())
    harvest = load(P / "harvest.json", {})
    releases = load(P / "releases.json", [])
    coverage = load(P / "coverage-latest.json", {})
    import csv
    rows = list(csv.DictReader((R / "inputs/plastchem_coverage_a13_worklist.csv").open()))
    never = [r for r in rows if not r["release_status"]]
    projected = sum(fit_hours(int(r["atoms_with_h"])) for r in never)
    status = {
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "lane": "coverage lane (A-13), tmux claude-contam-coverage",
        "charter_amendment": "A-13",
        "goal": "at least 95% of the 9,627 simulable PlastChem entries served (9,146), then the rest of the work list",
        "coverage": coverage or {"note": "no rerun of plastchem_coverage.py yet; promotion-v3 triage: 7,644 of 9,627 (79.4%) served or served through a parent"},
        "orca": {
            "worklist_structures": len(rows), "worklist_never_run": len(never),
            "chunks": summary.get("chunks", {}), "counts_over_staged_chunks": counts,
            "cpu_models": summary.get("cpu_models", {}),
            "measured_orca_cpu_hours": round(summary.get("measured_orca_hours", 0.0), 2),
            "fit_cpu_hours_of_measured": round(summary.get("fit_hours_of_measured", 0.0), 2),
            "projected_orca_cpu_hours_whole_list_by_fit": round(projected),
            "projection_basis": "campaign fit exp(-0.687758)*atoms^2.305719*1.113048 s (AMD EPYC 7763), never-run rows",
        },
        "harvest": harvest,
        "thermodynamics_batches": batches,
        "gate_G1": load(P / "gate-g1.json", {"status": "pending: waits for the first 100 new rows (chunk c01) to finish"}),
        "releases": releases,
        "last_release": releases[-1] if releases else None,
        "next_action": args.next_action,
        "blockers": args.blocker,
        "notes": args.note,
    }
    tmp = P / "STATUS.tmp"
    tmp.write_text(json.dumps(status, indent=2) + "\n")
    tmp.replace(P / "STATUS.json")
    with (P / "log.jsonl").open("a") as handle:
        handle.write(json.dumps({"utc": status["utc"], "event": "status", "next": args.next_action,
                                 "blockers": args.blocker, "counts": counts}) + "\n")
    print(json.dumps({k: status[k] for k in ("utc", "next_action", "blockers")}))


if __name__ == "__main__":
    main()
