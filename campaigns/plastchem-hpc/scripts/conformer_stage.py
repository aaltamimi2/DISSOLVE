"""Exploratory conformer study (owner request 2026-10-10; not a release input): one chunk (k01) of every prepared
conformer, uploaded to Euler (~/plastchem-euler/conformers-v1) and checked there file by file (copied from
stage_coverage.py). Tasks are ordered longest first (atoms with hydrogens, descending) so the long ones start first; the
chunk walltime is the campaign rule for its largest molecule (6 x the cost fit, 3 h at least) and coverage_task_walltime.py
--tier conformers-v1 lowers each pending task to its own. Memory 4G as the campaign; the pools are the admitted Rome and
Naples nodes (campaign tasks on them start first: the submission carries a positive --nice).

    python3 scripts/conformer_stage.py k01"""
import hashlib
import json
import math
import re
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "state/conformers-v1"
REMOTE = "plastchem-euler/conformers-v1"
POOLS = ("rome", "naples")


def fit_hours(atoms):
    return max(3, math.ceil(6 * math.exp(-0.687758) * atoms ** 2.305719 * 1.113048 / 3600))


def check(result, what):
    if result.returncode:
        raise SystemExit(f"{what} failed: {(result.stderr or '')[:400]}")
    return result


def main():
    group = sys.argv[1]
    assert re.fullmatch(r"k\d\d", group), group
    assert not (P / group / "staging-summary.json").exists(), f"{group} is staged; a chunk is staged once"
    preps = [json.loads(p.read_text()) for p in sorted((P / "prepared").glob("*/preparation.json"))]
    assert preps and all(p["status"] == "prepared" for p in preps)
    staged = {m["inchikey"] for d in P.glob("k[0-9][0-9]") if (d / "manifest.json").exists() and d.name != group
              for m in json.loads((d / "manifest.json").read_text())["molecules"]}
    preps = [p for p in preps if p["input"]["inchikey"] not in staged]
    preps.sort(key=lambda p: (-p["input"]["atoms"], p["input"]["inchikey"]))
    molecules = [dict(p["input"], array_index=i) for i, p in enumerate(preps)]
    admitted = [{k: a[k] for k in ("cpu_model", "partition", "constraint", "basis")}
                for a in json.loads((ROOT / "state/coverage-v1/cpu-checks/admitted.json").read_text())["admitted"]
                if a["constraint"] in POOLS]
    assert {a["constraint"] for a in admitted} == set(POOLS), admitted
    manifest = dict(campaign="conformer-study-v1", group=group, name=f"confstudy-{group}",
                    walltime=f"{max(fit_hours(m['atoms']) for m in molecules)}:00:00", mem="4G",
                    policy=json.loads((ROOT / "state/coverage-v1/policy.json").read_text()), admitted=admitted,
                    recipe=json.loads(json.dumps(preps[0]["recipe"])), molecules=molecules,
                    note="exploratory, owner request 2026-10-10; not a release input")
    (P / group).mkdir(parents=True, exist_ok=True)
    (P / group / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    (P / group / "submission-indices.json").write_text(json.dumps([m["array_index"] for m in molecules]) + "\n")
    files = [(ROOT / "scripts/conformer_runner.py", f"{group}/conformer_runner.py"),
             (ROOT / "scripts/conformer.sbatch", f"{group}/conformer.sbatch"),
             (ROOT / "scripts/conformer_submit_remote.py", f"{group}/conformer_submit_remote.py"),
             (P / group / "manifest.json", f"{group}/manifest.json"),
             (P / group / "submission-indices.json", f"{group}/submission-indices.json")]
    for m in molecules:
        for name in ("input.xyz", "preparation.json"):
            files.append((P / "prepared" / m["inchikey"] / name, f"prepared/{m['inchikey']}/{name}"))
    sha_path = P / group / "staging.sha256"
    sha_path.write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {name}\n" for p, name in files))
    files.append((sha_path, f"{group}/staging.sha256"))
    archive = P / f"{group}-staging.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path, name in files:
            tar.add(path, arcname=name, recursive=False)
    check(run("ssh", ["euler", f"mkdir -p ~/{REMOTE}/logs ~/{REMOTE}/runs ~/{REMOTE}/returns ~/{REMOTE}/prepared"],
              capture_output=True, text=True), "mkdir")
    check(run("scp", ["-q", str(archive), f"euler:{REMOTE}/"]), "scp")
    remote = check(run("ssh", ["euler", f"cd ~/{REMOTE} && tar -xzf {archive.name} && sha256sum -c --quiet "
                                        f"{group}/staging.sha256 && echo STAGED_OK"], capture_output=True, text=True),
                   "extract/verify")
    assert "STAGED_OK" in remote.stdout, remote.stdout[-400:]
    record = dict(group=group, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), tasks=len(molecules),
                  compounds=len({m["parent_inchikey"] for m in molecules}), walltime=manifest["walltime"],
                  archive=str(archive), archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), remote_verified=True)
    (P / group / "staging-summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    main()
