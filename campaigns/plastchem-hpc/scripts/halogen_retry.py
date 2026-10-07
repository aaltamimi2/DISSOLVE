"""A-11 preemption retries. A halogen run stopped by the scheduler (PREEMPTED: euler142's "critical" partition outranks
research and our jobs carry --no-requeue) did no chemistry wrong; it reruns from the same prepared geometry under the
same frozen recipe, in its own round so the interrupted attempt stays on record (the tier-2 time-limit pattern).

    python3 scripts/halogen_retry.py launch    stage and submit a round for every preempted structure not yet retried
    python3 scripts/halogen_retry.py collect   copy finished retry returns, verify identity, update the records

A converged retry replaces the structure's record (status converged, retry_round set, the interrupted attempt kept under
previous_attempts); a retry preempted again is eligible for the next round; any other failure is final."""
import hashlib
import json
import shutil
import sys
import tarfile
import time
from pathlib import Path

from euler_transport import run
from identity_campaign import verify

R = Path(__file__).resolve().parents[1]
P = R / "state/halogen-v1"
ROUNDS = P / "retries"
REMOTE = "plastchem-euler/halogen-v1/retries"
DEST = Path("/mnt/r/plastchem-euler/halogen-v1/retries")
PREEMPTED = {"scheduler_signal_15", "slurm_preempted"}
THROTTLE = 8


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).startswith("/mnt/r/"):
        path.write_text(json.dumps(value, indent=2) + "\n")
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def check(result):
    if result.returncode:
        sys.exit(f"remote step failed: {(result.stderr or '')[:400]}")
    return result


def preempted(record):
    state = (record.get("slurm_accounting") or {}).get("state", "")
    return record.get("status") == "failed" and (record.get("failure_mode") in PREEMPTED or state.startswith("PREEMPTED"))


def in_flight():
    """Structures in a submitted round that has not finished collecting."""
    keys = set()
    for meta in ROUNDS.glob("r*/round.json"):
        info = json.loads(meta.read_text())
        if not (meta.parent / "collected.json").exists():
            keys |= set(info["keys"])
    return keys


def launch():
    manifest = json.loads((P / "halogen/manifest.json").read_text())
    by_key = {m["inchikey"]: m for m in manifest["molecules"]}
    busy = in_flight()
    keys = sorted(k for k, p in ((p.stem, p) for p in (P / "records").glob("*.json"))
                  if k not in busy and preempted(json.loads(p.read_text())))
    if not keys:
        print(json.dumps({"launched": 0}))
        return
    name = f"r{len(list(ROUNDS.glob('r*/round.json'))) + 1:02d}"
    stage = ROUNDS / name / "staging"
    if stage.exists():
        shutil.rmtree(stage)  # an earlier attempt that never reached Euler
    molecules = []
    for index, key in enumerate(keys):
        molecule = dict(by_key[key], array_index=index, retry_round=name,
                        retry_reason="preempted by a higher-priority partition; rerun from the same prepared geometry")
        molecules.append(molecule)
        target = stage / "prepared" / key
        target.mkdir(parents=True)
        for item in ("input.xyz", "preparation.json"):
            shutil.copyfile(P / "prepared" / key / item, target / item)
    round_manifest = dict(manifest, molecules=molecules, name=f"contam-halogen-{name}", concurrency=THROTTLE,
                          retry_of="contam-halogen-v1", retry_round=name)
    (stage / "halogen").mkdir()
    (stage / "halogen/manifest.json").write_text(json.dumps(round_manifest, indent=1) + "\n")
    runner = (R / "scripts/halogen_runner.py").read_text()
    needle = "ROOT=Path.home()/'plastchem-euler/halogen-v1'"
    assert runner.count(needle) == 1
    (stage / "halogen_runner.py").write_text(runner.replace(needle, f"ROOT=Path.home()/'{REMOTE}/{name}'"))
    sbatch = (R / "scripts/halogen.sbatch").read_text().replace("contam-halogen-v1", f"contam-halogen-{name}")
    sbatch = sbatch.replace("plastchem-euler/halogen-v1/halogen_runner.py", f"{REMOTE}/{name}/halogen_runner.py")
    assert f"{REMOTE}/{name}/halogen_runner.py" in sbatch
    (stage / "halogen.sbatch").write_text(sbatch)
    archive = ROUNDS / name / "staging.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=str(path.relative_to(stage)), recursive=False)
    check(run("ssh", ["euler", f"mkdir -p ~/{REMOTE}/{name}"], capture_output=True, text=True))
    check(run("scp", ["-q", str(archive), f"euler:{REMOTE}/{name}/"], capture_output=True, text=True))
    result = check(run("ssh", ["euler", f"cd ~/{REMOTE}/{name} && tar -xzf staging.tar.gz && mkdir -p logs runs returns && "
                                        f"sbatch --parsable --array=0-{len(keys) - 1}%{THROTTLE} --chdir=$HOME/{REMOTE}/{name} "
                                        f"--output=$HOME/{REMOTE}/{name}/logs/%A_%a.out --error=$HOME/{REMOTE}/{name}/logs/%A_%a.err "
                                        "halogen.sbatch"], capture_output=True, text=True))
    array = result.stdout.strip().split(";")[0]
    write(ROUNDS / name / "round.json", dict(round=name, keys=keys, array_job_id=array, throttle=THROTTLE,
                                            utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    print(json.dumps({"launched": len(keys), "round": name, "array": array}))


REMOTE_SNAPSHOT = """import json,subprocess,datetime
from pathlib import Path
root=Path.home()/'{remote}'
print(json.dumps(dict(records={{p.parent.name:json.loads(p.read_text()) for p in (root/'runs').glob('*/result.json')}},
  sacct=subprocess.run(['sacct','-j','{array}','-nP','--units=K','--format=JobID,State,ElapsedRaw,MaxRSS,NodeList,ExitCode'],capture_output=True,text=True).stdout)))
"""


def collect():
    for meta in sorted(ROUNDS.glob("r*/round.json")):
        info = json.loads(meta.read_text())
        name, folder = info["round"], meta.parent
        if (folder / "collected.json").exists():
            continue
        response = check(run("ssh", ["euler", "python3 -"], input=REMOTE_SNAPSHOT.format(remote=f"{REMOTE}/{name}", array=info["array_job_id"]),
                             capture_output=True, text=True))
        snapshot = json.loads(response.stdout)
        accounting = {}
        for line in snapshot["sacct"].splitlines():
            fields = line.split("|")
            index = fields[0].split("_")[1] if "_" in fields[0] else ""
            if index.isdigit():  # a pending array prints as 76518_[0-6%8]; only started tasks have an index
                accounting[int(index)] = fields
        done_path = folder / "retrieved.json"
        done = json.loads(done_path.read_text()) if done_path.exists() else {}
        terminal = 0
        for index, key in enumerate(info["keys"]):
            acct = accounting.get(index)
            state = acct[1].split()[0] if acct else ""
            finished = state in {"COMPLETED", "FAILED", "TIMEOUT", "OUT_OF_MEMORY", "CANCELLED", "NODE_FAIL", "PREEMPTED"}
            terminal += finished
            if key in done or not finished:
                continue
            r = snapshot["records"].get(key) or {"inchikey": key, "status": "failed", "failure_mode": "slurm_" + state.lower()}
            if state == "PREEMPTED" and r.get("status") != "converged_identity_pending":
                r.update(status="failed", failure_mode="slurm_preempted")
            r["slurm_accounting"] = {"state": acct[1], "elapsed_seconds": int(acct[2]) if acct[2] else None, "node_list": acct[4]}
            r["retry_round"] = name
            target = DEST / name / "results" / key
            if r.get("status") == "converged_identity_pending":
                target.parent.mkdir(parents=True, exist_ok=True)
                check(run("scp", ["-rq", f"euler:{REMOTE}/{name}/returns/{key}", str(target.parent)], capture_output=True, text=True))
                try:
                    for item, digest in [("surface.orcacosmo", r["surface_sha256"]),
                                         *[(stage + ".inp", step["input_sha256"]) for stage, step in r["stages"].items()]]:
                        assert sha(target / item) == digest, item + " digest mismatch"
                    r.update(verify(target / "optimized.xyz", key, r["input"]["smiles"]))
                    r["identity_authority"] = json.loads((P / "policy.json").read_text())
                    if not r["identity_verified"]:
                        raise ValueError("No perception engine produced the required first-block match")
                    r.update(status="converged", dft_status="converged", archive_path=str(target))
                except Exception as exc:
                    r.update(status="failed", failure_mode="return_integrity_or_connectivity", error=str(exc), identity_verified=False)
            record_path = P / "records" / f"{key}.json"
            previous = json.loads(record_path.read_text())
            r["previous_attempts"] = previous.get("previous_attempts", []) + [
                {k: previous.get(k) for k in ("status", "failure_mode", "array_job_id", "array_task_id", "node",
                                               "elapsed_seconds", "slurm_accounting", "retry_round")}]
            write(record_path, r)
            done[key] = {"status": r["status"], "failure_mode": r.get("failure_mode")}
            write(done_path, done)
        if terminal == len(info["keys"]) and len(done) == len(info["keys"]):
            write(folder / "collected.json", dict(round=name, outcomes=done, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        print(json.dumps({"round": name, "terminal": terminal, "of": len(info["keys"]), "collected": len(done)}))


if __name__ == "__main__":
    {"launch": launch, "collect": collect}[sys.argv[1]]()
