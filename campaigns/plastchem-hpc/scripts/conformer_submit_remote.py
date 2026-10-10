"""Exploratory conformer study, Euler-only (copied from submit_coverage_remote.py): one guarded submission per chunk.
The job is named confstudy-<chunk> (never contam-*, so it does not count toward the campaign's Milan cap), runs only
on admitted pools (every alternative of the constraint must be admitted), with no --nice by default (see below).

    python3 ~/plastchem-euler/conformers-v1/<chunk>/conformer_submit_remote.py <chunk> <throttle> [constraint] [nice]"""
import hashlib
import json
import re
import subprocess
import sys
import time
from pathlib import Path

root = Path.home() / "plastchem-euler/conformers-v1"
group = sys.argv[1]
throttle = int(sys.argv[2])
constraint = sys.argv[3] if len(sys.argv) > 3 else "rome|naples"
# no nice by default: this account's fairshare factor is 0, so a fresh job's priority is its age alone (about 10) and any
# nice above that floors it at 1 (1000 and then 50 left k01 at priority 1 with nothing started); the campaign's tasks
# waiting for these pools are older, so their age already puts them first
nice = int(sys.argv[4]) if len(sys.argv) > 4 else 0
assert re.fullmatch(r"k\d\d", group) and nice >= 0
p = root / group
m = json.loads((p / "manifest.json").read_text())
name = m["name"]
assert name.startswith("confstudy-")


def run(args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


queue = run(["squeue", "-u", "aaltamimi2", "--name=" + name, "-h", "-o", "%i|%j|%T|%N"])
accounting = run(["sacct", "-u", "aaltamimi2", "--starttime=2026-10-10", "--name=" + name, "--format=JobID,JobName%40,State%32", "-nP"])
r = {"utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "group": group, "name": name, "squeue": queue, "sacct": accounting}
if queue.strip() or accounting.strip():
    print(json.dumps(dict(r, decision="existing_job_found_no_resubmit")))
    raise SystemExit(0)
if (p / "submission.started").exists():
    print(json.dumps(dict(r, decision="prior_attempt_unconfirmed_no_resubmit")))
    raise SystemExit(2)
indices = json.loads((p / "submission-indices.json").read_text())
assert indices == list(range(len(m["molecules"])))
for mol in m["molecules"]:
    key = mol["inchikey"]
    prep = json.loads((root / "prepared" / key / "preparation.json").read_text())
    assert prep["status"] == "prepared" and prep["input"]["inchikey"] == key
    assert hashlib.sha256((root / "prepared" / key / "input.xyz").read_bytes()).hexdigest() == prep["xyz_sha256"]
    assert not (root / "runs" / key / "attempt.lock").exists()
for line in (p / "staging.sha256").read_text().splitlines():
    sha, relative = line.split()
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == sha, relative
admitted = {a["constraint"] for a in m["admitted"]}
assert all(part in admitted for part in constraint.split("|")), "every pool must be an admitted CPU type"
assert 0 < throttle <= 200
with (p / "submission.started").open("x") as f:
    f.write(r["utc"] + "\n")
result = subprocess.run(["sbatch", "--parsable", f"--array=0-{len(indices) - 1}%{throttle}", "--constraint=" + constraint,
                         f"--nice={nice}", "--job-name=" + name, "--mem=" + m["mem"], "--time=" + m["walltime"],
                         "--chdir=" + str(root), "--output=" + str(root / "logs/%A_%a.out"), "--error=" + str(root / "logs/%A_%a.err"),
                         str(p / "conformer.sbatch"), group], capture_output=True, text=True)
r.update(returncode=result.returncode, stdout=result.stdout, stderr=result.stderr, tasks=len(indices), throttle=throttle,
         constraint=constraint, nice=nice, decision="submitted" if result.returncode == 0 else "submission_failed_or_unconfirmed")
tmp = p / "submission.tmp"
tmp.write_text(json.dumps(r, indent=2) + "\n")
tmp.replace(p / "submission.json")
print(json.dumps({k: v for k, v in r.items() if k not in ("squeue", "sacct")}))
raise SystemExit(result.returncode)
