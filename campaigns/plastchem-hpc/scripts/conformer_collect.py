"""Exploratory conformer study (owner request 2026-10-10; not a release input): collect the conformer runs from Euler
(copied in spirit from watch_coverage.py, one pass). Each finished run's returns are copied to
/mnt/r/plastchem-euler/conformers-v1/results/<key>, their digests checked, and the optimized geometry verified with the
campaign's identity policy (D-IDENT: connectivity first block of the parent InChIKey, RDKit with Open Babel as reference,
an unfinished RDKit perception held, never rejected). A run the scheduler ended without a result is recorded with its
SLURM state. Records: state/conformers-v1/records/<key>.json; counts: state/conformers-v1/summary.json.

    python3 scripts/conformer_collect.py     (anaconda RDKit 2023.09.2, the campaign's identity environment)"""
import collections
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402
from identity_campaign import verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "state/conformers-v1"
DEST = Path("/mnt/r/plastchem-euler/conformers-v1/results")
REMOTE = "plastchem-euler/conformers-v1"
TERMINAL = {"COMPLETED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED", "NODE_FAIL", "PREEMPTED", "BOOT_FAIL", "DEADLINE"}
SNAPSHOT = r'''
import json, subprocess
from pathlib import Path
root = Path.home() / "plastchem-euler/conformers-v1"
groups = {}
for d in sorted(root.glob("k[0-9][0-9]")):
    s = d / "submission.json"
    if s.exists():
        r = json.loads(s.read_text())
        if r.get("returncode") == 0:
            groups[d.name] = r["stdout"].strip().split(";")[0]
records = {}
for p in (root / "runs").glob("*/result.json"):
    try:
        records[p.parent.name] = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        pass
sacct = ""
if groups:
    sacct = subprocess.run(["sacct", "-j", ",".join(groups.values()), "--starttime=2026-10-10", "--units=K",
                            "--format=JobID,State%32,ExitCode,ElapsedRaw,MaxRSS,NodeList", "-nP"],
                           capture_output=True, text=True).stdout
print(json.dumps(dict(groups=groups, records=records, sacct=sacct)))
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def main():
    response = run("ssh", ["euler", "python3 -"], input=SNAPSHOT, capture_output=True, text=True)
    if response.returncode:
        raise SystemExit(response.stderr[-500:])
    snap = json.loads(response.stdout)
    accounting = {}
    for line in snap["sacct"].splitlines():
        f = line.split("|")
        if len(f) >= 6:
            accounting[f[0]] = f
    ledger_path = P / "retrieved.json"
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    DEST.mkdir(parents=True, exist_ok=True)
    fresh = []
    for group, array in snap["groups"].items():
        manifest = json.loads((P / group / "manifest.json").read_text())
        for mol in manifest["molecules"]:
            key = mol["inchikey"]
            if key in ledger:
                continue
            r = snap["records"].get(key)
            acct = accounting.get(f"{array}_{mol['array_index']}")
            state = acct[1].split()[0] if acct else None
            if r is None and state not in TERMINAL:
                continue
            r = dict(r or dict(input=mol, inchikey=key, status="failed", dft_ran=False))
            if acct:
                r["slurm_accounting"] = dict(state=acct[1], exit_code=acct[2], elapsed_seconds=int(acct[3]) if acct[3] else None, node_list=acct[5])
                batch = accounting.get(f"{array}_{mol['array_index']}.batch")
                if batch and batch[4]:
                    r["slurm_accounting"]["maxrss_kib"] = float(batch[4].rstrip("K"))
            if state in TERMINAL and r.get("status") not in ("converged_identity_pending", "failed"):
                r.update(status="failed", failure_mode="slurm_" + state.lower(), error="Scheduler ended the run without a final record")
            if r.get("status") in ("converged_identity_pending", "failed"):
                fresh.append((key, r))
            else:
                write(P / "records" / f"{key}.json", r)  # still running: progress only
    fetch = [key for key, r in fresh if r.get("status") == "converged_identity_pending"]
    for start in range(0, len(fetch), 50):
        result = run("scp", ["-rq", *[f"euler:{REMOTE}/returns/{k}" for k in fetch[start:start + 50]], str(DEST)], capture_output=True, text=True)
        if result.returncode:
            raise SystemExit("scp failed: " + result.stderr[:500])
    for key, r in fresh:
        folder = DEST / key
        if r.get("status") == "converged_identity_pending":
            try:
                for name, digest in [("surface.orcacosmo", r["surface_sha256"]), *[(s + ".inp", i["input_sha256"]) for s, i in r["stages"].items()]]:
                    assert sha(folder / name) == digest, name + " digest mismatch"
                parent = r["input"]["parent_inchikey"]
                r.update(verify(folder / "optimized.xyz", parent, r["input"]["smiles"]))
                if not r["identity_verified"] and any(o.get("undecided") for o in r["identity_observations"]):
                    r.update(identity_pending="rdkit_not_finished", archive_path=str(folder))  # held, not rejected
                elif not r["identity_verified"]:
                    r.update(status="failed", failure_mode="return_integrity_or_connectivity", archive_path=str(folder),
                             error="No perception engine produced the parent's first-block match")
                else:
                    r.update(status="converged", archive_path=str(folder))
            except Exception as exc:
                r.update(status="failed", failure_mode="return_integrity_or_connectivity", error=str(exc), identity_verified=False)
        write(P / "records" / f"{key}.json", r)
        if not r.get("identity_pending"):
            ledger[key] = dict(status=r["status"], failure_mode=r.get("failure_mode"), utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            write(ledger_path, ledger)
    by_compound = collections.defaultdict(collections.Counter)
    for path in (P / "records").glob("*.json"):
        r = json.loads(path.read_text())
        status = r.get("status", "?")
        if r.get("failure_mode") == "return_integrity_or_connectivity":
            status = "rejected"
        by_compound[r["input"]["parent_inchikey"]][status] += 1
    total = collections.Counter()
    for c in by_compound.values():
        total.update(c)
    summary = dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), counts=dict(total), new_this_pass=len(fresh),
                   by_compound={k: dict(v) for k, v in sorted(by_compound.items())})
    write(P / "summary.json", summary)
    print(json.dumps(dict(utc=summary["utc"], counts=summary["counts"], new_this_pass=len(fresh))))


if __name__ == "__main__":
    main()
