"""A-13: per-task walltimes for pending coverage tasks. A chunk is submitted with one --time, 6 x the campaign fit for
its largest molecule (build_coverage_chunk.py), so most of its tasks ask for far longer than they need and cannot
backfill on the busy pools (a 38-77 h request waits; a short one starts). This lowers each PENDING task's time limit to
the same rule applied to its own molecule (6 x the fit for its atoms, 3 h at least), never above the chunk's walltime
and never raising one. A task that reaches its limit is resubmitted with a longer one through a retry round (charter
A-13), as before. Running tasks are left alone. Every change is logged (UTC, chunk, array, task, atoms, from, to) in
state/coverage-v1/walltime-log.jsonl; the runner's record keeps the manifest walltime, so the log is the provenance.

    python3 scripts/coverage_task_walltime.py [--dry-run] CHUNK [CHUNK ...]"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402

P = Path(__file__).resolve().parents[1] / "state/coverage-v1"
REMOTE = "/srv/home/aaltamimi2/plastchem-euler/coverage-v1"


def need_hours(atoms):
    return max(3, math.ceil(6 * math.exp(-0.687758) * atoms ** 2.305719 * 1.113048 / 3600))


def hours(walltime):
    h, m, s = (int(x) for x in walltime.split(":"))
    return h + (m > 0 or s > 0)


def main(argv):
    dry = "--dry-run" in argv
    chunks = [a for a in argv if not a.startswith("--")]
    log = []
    for chunk in chunks:
        manifest = json.loads((P / chunk / "manifest.json").read_text())
        submission = run("ssh", ["euler", f"cat {REMOTE}/{chunk}/submission.json"], capture_output=True, text=True)
        array = json.loads(submission.stdout)["stdout"].strip().split(";")[0]
        pending = run("ssh", ["euler", f"squeue -h -r -j {array} -t PD -o %K"], capture_output=True, text=True)
        tasks = sorted(int(t) for t in pending.stdout.split() if t.strip().isdigit())
        limit = hours(manifest["walltime"])
        by_index = {m["array_index"]: m for m in manifest["molecules"]}
        plan = [(t, by_index[t]["atoms"], need_hours(by_index[t]["atoms"])) for t in tasks]
        plan = [(t, atoms, h) for t, atoms, h in plan if h < limit]
        print(json.dumps({"chunk": chunk, "array": array, "chunk_walltime_h": limit, "pending": len(tasks),
                          "lowered": len(plan), "to_hours": sorted({h for _, _, h in plan})}))
        if dry or not plan:
            continue
        script = "\n".join(f"scontrol update JobId={array}_{t} TimeLimit={h}:00:00 && echo ok {t}" for t, _, h in plan)
        result = run("ssh", ["euler", "bash -s"], input=script, capture_output=True, text=True)
        done = {int(line.split()[1]) for line in result.stdout.splitlines() if line.startswith("ok ")}
        utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        log += [dict(utc=utc, chunk=chunk, array=array, task=t, atoms=atoms, from_hours=limit, to_hours=h,
                     applied=t in done) for t, atoms, h in plan]
        print(f"{chunk}: {len(done)} of {len(plan)} applied" + (f"; stderr {result.stderr[:200]}" if result.stderr else ""))
    if log:
        with (P / "walltime-log.jsonl").open("a") as handle:
            for entry in log:
                handle.write(json.dumps(entry) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
