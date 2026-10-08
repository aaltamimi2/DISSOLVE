"""A-13: keep the coverage campaign's Milan-pool arrays inside the cap of 64 running jobs (D-CONC; since the owner NOTE of
2026-10-08 17:10 UTC the cap covers the Milan pool only) and hand freed slots to the next chunk in work-list order. Each
array's throttle becomes min(its unfinished tasks, what the earlier arrays leave of 64). Changes go through scontrol
update ArrayTaskThrottle on this campaign's own arrays only, and are logged (UTC, array, from, to, why) in
state/coverage-v1/throttle-log.jsonl.

    python3 scripts/coverage_throttle.py [--dry-run]"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402

R = Path(__file__).resolve().parents[1]
P = R / "state/coverage-v1"
CAP = 64
REMOTE = """import json,re,subprocess
from pathlib import Path
root=Path.home()/'plastchem-euler/coverage-v1'
out=[]
for folder in sorted(root.glob('[cr][0-9][0-9]'),key=lambda f:(f.name[0]!='c',f.name)):
    meta=folder/'submission.json'
    if not meta.exists():continue
    r=json.loads(meta.read_text())
    if r.get('returncode')!=0 or r.get('constraint','milan&cpu')!='milan&cpu':continue
    job=r['stdout'].strip().split(';')[0]
    q=subprocess.run(['squeue','-h','-r','-j',job,'-o','%T'],capture_output=True,text=True).stdout.split()
    d=subprocess.run(['scontrol','show','job',job,'-o'],capture_output=True,text=True).stdout
    m=re.search(r'ArrayTaskThrottle=(\\d+)',d)
    out.append(dict(chunk=folder.name,array=job,running=q.count('RUNNING'),pending=q.count('PENDING'),throttle=int(m.group(1)) if m else None))
print(json.dumps(out))
"""


def main(dry_run):
    result = run("ssh", ["euler", "python3 -"], input=REMOTE, capture_output=True, text=True)
    if result.returncode:
        sys.exit(f"queue read failed: {result.stderr[:300]}")
    arrays = json.loads(result.stdout)  # the Milan-pool arrays, chunks in work-list order, then retry rounds
    left, plan = CAP, []
    for s in arrays:
        chunk, array = s["chunk"], s["array"]
        unfinished = s["running"] + s["pending"]
        if not unfinished:
            continue
        want = max(1, min(unfinished, left)) if left > 0 else 1
        # never below what already runs: lowering a throttle does not stop a running task (it would only look capped)
        want = max(want, s["running"]) if left >= s["running"] else s["running"]
        plan.append(dict(chunk=chunk, array=array, running=s["running"], pending=s["pending"], throttle=s["throttle"], to=want))
        left -= want
    changes = [p for p in plan if p["throttle"] != p["to"]]
    total = sum(p["to"] for p in plan)
    log = P / "throttle-log.jsonl"
    for change in changes:
        if dry_run:
            continue
        done = run("ssh", ["euler", f"scontrol update JobId={change['array']} ArrayTaskThrottle={change['to']}; "
                                    f"scontrol show job {change['array']} -o | grep -o 'ArrayTaskThrottle=[0-9]*' | head -1; true"],
                   capture_output=True, text=True)
        change["readback"] = done.stdout.strip()
        with log.open("a") as handle:
            handle.write(json.dumps(dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), chunk=change["chunk"],
                                         array=change["array"], from_throttle=change["throttle"], to=change["to"],
                                         readback=change["readback"], running=change["running"], pending=change["pending"],
                                         why=f"Milan pool cap {CAP}: earlier chunks keep their unfinished tasks, the next "
                                             "chunk takes what is left")) + "\n")
    print(json.dumps(dict(cap=CAP, planned_total=total, plan=plan, changed=len(changes), dry_run=dry_run)))


if __name__ == "__main__":
    main("--dry-run" in sys.argv)
