"""A-13 thermodynamics for the coverage campaign (copied from newcontam_thermo.py): the 32-solvent panel x the 10 converged
polymer ensembles (both conventions) and binary LLE (RT and high), with the supplement workers whose reproduction matched
promotion-v1 to 1e-15, on genoa&cpu (A-9 note, A-11 note) inside the shared cap (partition 3 + LLE 5 = 8 of 64).

Sources:
  harvest    halogen structures whose ORCA converged but that no batch has staged (A-13 5a: the A-11 freeze's
             thermodynamics_pending rows and the "running" rows that converged since)
  coverage   coverage-tier structures accepted by watch_coverage.py (state/coverage-v1/records)

Batches tNN live in /mnt/r/plastchem-euler/coverage-thermo-v1 and ~/plastchem-euler/coverage-thermo-v1 on Euler. A
structure is staged once: every key in a newcontam-thermo-v1 or coverage-thermo-v1 batch, and every structure
promotion-v1 serves, is excluded. The worker code staged must be byte-identical to the code the A-11/A-12 batches pinned.

    python3 scripts/coverage_thermo.py stage BATCH {harvest|coverage} [--limit N] [--keys KEY ...] [--dry-run]
    python3 scripts/coverage_thermo.py submit BATCH
    python3 scripts/coverage_thermo.py status BATCH
    python3 scripts/coverage_thermo.py collect BATCH [--partial]

Nothing here writes a release or a product asset."""
import argparse
import csv
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path

from euler_transport import run

R = Path(__file__).resolve().parents[1]
B = Path("/mnt/r/plastchem-euler")
OUT = B / "coverage-thermo-v1"
EARLIER = B / "newcontam-thermo-v1"
REMOTE = "/srv/home/aaltamimi2/plastchem-euler/coverage-thermo-v1"
PACKAGE = "/srv/home/aaltamimi2/plastchem-euler/phase8-v1"
BASE_SPEC = B / "coverage-plans/supplement-inputs-v1-20260925T1350/inputs.json"
BASE_SPEC_SHA256 = "aa693c8063929510ef4f7c236220250d2704db4b3c9fa607fbe6b152f4cc0f6c"
POLYMERS = ["evoh", "nylon6", "nylon66", "pc", "pe", "pet", "pp", "ps", "pvc", "pvdf"]
#: the worker code every A-11/A-12 batch pinned (g00 reproduced promotion-v1 with it); staging refuses anything else
CODE_PINS = {
    "supplement_partition_worker.py": "2cd04d6e93854d349a7088d4ffce1beeb871365a01d9ce4c425ca5f0dedf7ec7",
    "supplement_lle_worker.py": "408bdee03a53d8d6233ecbc5c4f059b1472868da9173bbdcebf69d620fd9e9d8",
    "phase9_profiles.py": "0113145c2aa1fe9e2581678741734bcae6651a2303d53d78344798da4ac0bb77",
    "phase9_worker_cpu.py": "1c2e5910904db8d8068fd964278f92188e31c988cb3d5711426b1d0361bf3f16",
    "phase9_grid.py": "e4c68346047b1a348c4fddae39826ba37c213cb2bb042f6f29f8bd747670844b",
    "phase9_failure_policy.py": "e77434846fa4db3fc033d4f79c3293a0ba7d7f8fbc972d06fe7b652c589db321",
}
LLE_SOLVER_SHA256 = "1fee9216925b91877eaf79840e010330801b93adf41b0b116c774358df0296f9"
CODE = list(CODE_PINS)
PARTITION_KEYS, LLE_SYSTEMS = 50, 1000
THROTTLE = {"partition": 20, "lle": 40}  # Genoa, outside the Milan cap since the owner NOTE of 2026-10-08 17:10; bounded arrays


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def panel():
    import duckdb
    rows = duckdb.connect().execute("SELECT DISTINCT product_solvent_key FROM read_parquet(?) ORDER BY 1",
                                    [str(B / "promotion-v1/partition.parquet")]).fetchall()
    return [r[0] for r in rows]


def served_v1():
    with gzip.open(B / "promotion-v1/contaminants.csv.gz", "rt") as handle:
        return {r["input_inchikey"] for r in csv.DictReader(handle) if r["campaign_status_at_snapshot"] == "converged"}


def staged_anywhere():
    return {key for root in (EARLIER, OUT) for meta in root.glob("*/batch.json") for key in json.loads(meta.read_text())["keys"]}


def candidates(source):
    """Accepted surfaces no batch has staged, with their record and returned surface on R:."""
    tier = {"harvest": "halogen-v1", "coverage": "coverage-v1"}[source]
    records = R / f"state/{tier}/records"
    results = B / f"{tier}/results"
    done = served_v1() | staged_anywhere()
    out = {}
    for path in sorted(records.glob("*.json")):
        r = json.loads(path.read_text())
        key = r.get("inchikey") or path.stem
        if r.get("status") != "converged" or key in done:
            continue
        # Retried runs return to their own folder; the record names it.
        surface = (Path(r["archive_path"]) if r.get("archive_path") else results / key) / "surface.orcacosmo"
        assert sha(surface) == r["surface_sha256"], key
        m = r["input"]
        out[key] = dict(inchikey=key, name=m["name"], surface=str(surface), surface_sha256=r["surface_sha256"],
                        atoms=m["atoms"], molecular_weight_g_mol=float(m["molecular_weight_g_mol"]),
                        tier=m.get("tier", "halogen"), source=f"{source}_accepted")
    return out


def stage(batch, source, limit, dry_run=False, only=None):
    out = OUT / batch
    if out.exists() and not (out / "batch.json").exists():
        shutil.rmtree(out)  # an earlier staging that never finished uploading; nothing of it was submitted
    assert not out.exists(), f"{out} exists; batches are never rebuilt"
    assert sha(BASE_SPEC) == BASE_SPEC_SHA256
    for name, digest in CODE_PINS.items():
        assert sha(R / "scripts" / name) == digest, f"{name} is not the version the A-11/A-12 batches pinned"
    assert sha(B / "phase8-v1/phase8_lle.py") == LLE_SOLVER_SHA256
    base = json.loads(BASE_SPEC.read_text())
    units = candidates(source)
    if only:  # named structures only: an owner request ahead of the work-list order (A-13 NOTE 2026-10-08 20:48 UTC)
        missing = sorted(set(only) - set(units))
        assert not missing, f"not accepted, or staged already: {missing}"
        units = {key: units[key] for key in only}
    keys = sorted(units)[:limit] if limit else sorted(units)
    assert keys, "nothing to stage"
    if dry_run:
        print(json.dumps({"batch": batch, "source": source, "keys": len(keys),
                          "atoms_max": max(units[k]["atoms"] for k in keys), "names": [units[k]["name"] for k in keys]}))
        return
    out.mkdir(parents=True)
    (out / "surfaces").mkdir()
    pins = {}

    def staged(path, digest):
        target = out / "surfaces" / f"{digest}.orcacosmo"
        if not target.exists():
            shutil.copyfile(path, target)
        assert sha(target) == digest
        pins[target.name] = digest
        return f"{REMOTE}/{batch}/surfaces/{target.name}"

    solvents = panel()
    assert len(solvents) == 32
    spec = {k: base[k] for k in ("temperature_K", "solute_mole_fraction", "reference_state", "parameterization",
                                  "package_pins", "policy")}
    spec.update(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), status="A-13 production batch",
                parent_inputs_sha256=BASE_SPEC_SHA256, scope=f"A-13 {source} batch {batch}", frozen={},
                package_root=PACKAGE, solvent_cache="", solvent_cache_sha256="")
    spec["solvents"] = {name: dict(base["solvents"][name], surface=staged(base["solvents"][name]["surface"],
                                   base["solvents"][name]["surface_sha256"])) for name in solvents}
    spec["polymers"] = {p: [dict(r, surface=staged(r["surface"], r["surface_sha256"])) for r in base["polymers"][p]]
                        for p in POLYMERS}
    spec["later"] = {k: dict(units[k], surface=staged(units[k]["surface"], units[k]["surface_sha256"])) for k in keys}
    for name, digest in spec["package_pins"].items():
        assert sha(B / "phase8-v1" / name) == digest, name
    write(out / "inputs.json", spec)
    for name in CODE:
        shutil.copyfile(R / "scripts" / name, out / name)
    code_pins = {f"{REMOTE}/{batch}/{name}": sha(out / name) for name in CODE}
    code_pins[f"{PACKAGE}/phase8_lle.py"] = sha(B / "phase8-v1/phase8_lle.py")
    remote_inputs = f"{REMOTE}/{batch}/inputs.json"
    (out / "plans").mkdir()
    partition = [keys[i:i + PARTITION_KEYS] for i in range(0, len(keys), PARTITION_KEYS)]
    for index, chunk in enumerate(partition):
        write(out / "plans" / f"partition-{index:04d}.json", dict(
            purpose="A-13 production", inputs=remote_inputs, inputs_sha256=sha(out / "inputs.json"), keys=chunk,
            polymers=POLYMERS, solvents=solvents, solvent_mode="fresh", direct_solvent_controls=[],
            subbatch=PARTITION_KEYS, output=f"{REMOTE}/{batch}/results/partition-{index:04d}", code_pins=code_pins))
    # every coverage and harvest structure is neutral, so every one gets binary LLE
    systems = [dict(inchikey=k, solvent=s, regime=g) for k in keys if units[k].get("charge", 0) == 0
               for s in solvents for g in ("RT", "high")]
    lle = [systems[i:i + LLE_SYSTEMS] for i in range(0, len(systems), LLE_SYSTEMS)]
    for index, chunk in enumerate(lle):
        write(out / "plans" / f"lle-{index:04d}.json", dict(
            purpose="A-13 production", inputs=remote_inputs, inputs_sha256=sha(out / "inputs.json"), systems=chunk,
            grid_batch=256, output=f"{REMOTE}/{batch}/results/lle-{index:04d}", code_pins=code_pins))
    python = f"{PACKAGE}/venv/bin/python"
    for kind, count in (("partition", len(partition)), ("lle", len(lle))):
        worker = "supplement_partition_worker.py" if kind == "partition" else "supplement_lle_worker.py"
        (out / f"{kind}.sbatch").write_text(
            "#!/bin/bash\n"
            f"#SBATCH --job-name=contam-coverage-{kind}-{batch}\n#SBATCH --partition=research\n#SBATCH --constraint=genoa&cpu\n"
            "#SBATCH --exclude=euler09,euler10\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n#SBATCH --cpus-per-task=1\n"
            # 6 h (A-13, 2026-10-09): plans take <= ~2 h, and 12 h requests waited on Priority while Genoa had idle cores
            "#SBATCH --mem=4G\n#SBATCH --time=6:00:00\n#SBATCH --no-requeue\n"
            f"#SBATCH --output={REMOTE}/{batch}/logs/{kind}-%A_%a.out\n#SBATCH --error={REMOTE}/{batch}/logs/{kind}-%A_%a.err\n"
            "set -euo pipefail\nexport OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1\n"
            "export MALLOC_ARENA_MAX=1 MALLOC_TRIM_THRESHOLD_=65536\n"
            f"cd {REMOTE}/{batch}\n"
            f"exec {python} -u {worker} --plan plans/{kind}-$(printf %04d $SLURM_ARRAY_TASK_ID).json\n")
    meta = dict(batch=batch, source=source, keys=keys, partition_plans=len(partition), lle_plans=len(lle),
                lle_systems=len(systems), solvents=solvents, polymers=POLYMERS, surfaces=len(pins),
                inputs_sha256=sha(out / "inputs.json"), code_pins=code_pins, throttle=THROTTLE,
                script_sha256=sha(__file__), utc=spec["utc"])
    archive = OUT / f"{batch}-staging.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path in sorted(out.rglob("*")):
            if path.is_file():
                tar.add(path, arcname=f"{batch}/{path.relative_to(out)}", recursive=False)
    check(run("ssh", ["euler", f"mkdir -p {REMOTE}"], capture_output=True, text=True))
    check(run("scp", ["-q", str(archive), f"euler:{REMOTE}/"], capture_output=True, text=True))
    check(run("ssh", ["euler", f"cd {REMOTE} && tar -xzf {archive.name} && mkdir -p {batch}/logs {batch}/results "
                               f"&& cd {batch} && sha256sum inputs.json | cut -c1-64"], capture_output=True, text=True),
          expect=meta["inputs_sha256"])
    write(out / "batch.json", meta)  # last: a batch exists only once it is on Euler
    print(json.dumps({k: v for k, v in meta.items() if k not in ("keys", "code_pins", "solvents")}))


def check(result, expect=None):
    if result.returncode:
        sys.exit(f"remote step failed: {(result.stderr or '')[:500]}")
    if expect is not None and expect not in result.stdout:
        sys.exit(f"remote digest mismatch: {result.stdout[:200]}")
    return result


def submit(batch):
    meta = json.loads((OUT / batch / "batch.json").read_text())
    receipt = OUT / batch / "submission.json"
    assert not receipt.exists(), "already submitted"
    ids = {}
    for kind, count in (("partition", meta["partition_plans"]), ("lle", meta["lle_plans"])):
        if not count:
            continue
        result = check(run("ssh", ["euler", f"cd {REMOTE}/{batch} && sbatch --parsable --array=0-{count - 1}%{THROTTLE[kind]} {kind}.sbatch"],
                           capture_output=True, text=True))
        ids[kind] = result.stdout.strip().split(";")[0]
    write(receipt, dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), array_ids=ids))
    print(json.dumps(ids))


def kick_compaction(batch):
    """Compact sealed plans in the background on Euler (one at a time per batch, under flock), so no call holds the
    shared SSH lock while thousands of raw files are read off the network file system. The A-11 helper, unchanged."""
    check(run("scp", ["-q", str(R / "scripts/newcontam_compact_remote.py"), f"euler:{REMOTE}/{batch}/"],
              capture_output=True, text=True))
    check(run("ssh", ["euler", f"cd {REMOTE}/{batch} && (nohup flock -n compact.lock python3 newcontam_compact_remote.py . "
                               f">> compact.log 2>&1 < /dev/null &) ; true"], capture_output=True, text=True))


def status(batch):
    """Sealed and compacted plan counts on Euler and the queue, without copying anything; starts background
    compaction of any sealed plan not yet compacted."""
    meta = json.loads((OUT / batch / "batch.json").read_text())
    result = check(run("ssh", ["euler", f"cd {REMOTE}/{batch} && ls results/*/complete.json 2>/dev/null | wc -l; "
                                        f"ls results/partition-*/complete.json 2>/dev/null | wc -l; "
                                        f"ls results-compact/*.jsonl.gz 2>/dev/null | wc -l; "
                                        f"squeue -u aaltamimi2 -h -r -n contam-coverage-partition-{batch},contam-coverage-lle-{batch} -o %T | sort | uniq -c; true"],
                       capture_output=True, text=True))
    lines = result.stdout.splitlines()
    total, partition, compacted = int(lines[0]), int(lines[1]), int(lines[2])
    state = dict(batch=batch, partition_complete=partition, partition_plans=meta["partition_plans"],
                 lle_complete=total - partition, lle_plans=meta["lle_plans"], compacted=compacted,
                 queue=" ".join(l.strip() for l in lines[3:]))
    if compacted < total:
        kick_compaction(batch)
    print(json.dumps(state))
    return state


def collect(batch, partial=False):
    """Once every plan is sealed and compacted on Euler: copy the compact results back and record the collection.
    partial: copy whatever is compacted now and record it as collection-partial.json (a frozen release takes only the
    structures whose every row is in)."""
    state = status(batch)
    plans = state["partition_plans"] + state["lle_plans"]
    if state["compacted"] < plans and not partial:
        return False
    archive = OUT / batch / "results-compact.tar"
    with archive.open("wb") as handle:
        result = run("ssh", ["euler", f"cd {REMOTE}/{batch} && tar -cf - results-compact"], stdout=handle,
                     stderr=subprocess.PIPE)
    if result.returncode:
        sys.exit(f"collect failed: {result.stderr[:300]}")
    with tarfile.open(archive) as tar:
        tar.extractall(OUT / batch, filter="data")
    got = len(list((OUT / batch / "results-compact").glob("*.jsonl.gz")))
    assert got == plans or (partial and got == state["compacted"]), "compact files missing"
    write(OUT / batch / ("collection-partial.json" if got < plans else "collection.json"),
          dict(state, utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), archive_sha256=sha(archive),
               compact_files=got))
    archive.unlink()
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "submit", "status", "collect"])
    parser.add_argument("batch")
    parser.add_argument("source", nargs="?", choices=["harvest", "coverage"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--keys", nargs="+", help="stage: these accepted structures only")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--partial", action="store_true", help="collect: take the compacted plans now")
    args = parser.parse_args()
    if args.action == "stage":
        stage(args.batch, args.source, args.limit, dry_run=args.dry_run, only=args.keys)
    elif args.action == "submit":
        submit(args.batch)
    elif args.action == "status":
        status(args.batch)
    else:
        sys.exit(0 if collect(args.batch, partial=args.partial) else 3)
