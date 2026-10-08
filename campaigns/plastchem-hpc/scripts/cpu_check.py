"""A-13 owner NOTE (2026-10-08 17:10 UTC, "USE WHATEVER COMPUTE THE ACCOUNT CAN GET"): the reproduction check that admits a
CPU type and partition for ORCA. Each check reruns the reference set, structures already converged on the campaign's
Milan nodes (AMD EPYC 7763, research, milan&cpu), from their identical prepared input.xyz with the unchanged recipe on the
candidate type, and compares:

  D-IDENT        the same verdict (and the perceived key is reported)
  energies       every FINAL SINGLE POINT ENERGY of both stages and the COSMO solute energy within 1e-6 Eh
  surface        the same segment count; cavity area and volume within 1e-3 relative
  openCOSMO-RS   logP within 0.01: octanol/water, and toluene, ethanol and acetone against the PE ensemble (normalized
                 convention, 298.15 K, x = 0), with the production functions (phase9_profiles, phase9_worker_cpu.ensemble)

The reference set spans C/H/N/O, halogens (Br, Cl and iodine's ECP), S, P and Si, and one structure above 60 atoms (DEHP).
P and Si were never in a campaign tier before A-13: their references are chunk c01's own Milan runs.

    python3 scripts/cpu_check.py stage TAG CONSTRAINT [--time 24:00:00]   upload and submit the check array
    python3 scripts/cpu_check.py collect TAG                               copy returns to R:, verify, D-IDENT
    python3 scripts/cpu_check.py compare TAG [TAG ...]                      the verdict, state/coverage-v1/cpu-checks/TAG/
    python3 scripts/cpu_check.py admitted                                   the allow-list of types that passed

Records: state/coverage-v1/cpu-checks/<TAG>/ (stage, submission, records, comparison) and admitted.json; returned files:
/mnt/r/plastchem-euler/coverage-v1/cpu-checks/<TAG>/<InChIKey>/."""
import argparse
import datetime
import hashlib
import json
import math
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from euler_transport import run  # noqa: E402

R = Path(__file__).resolve().parents[1]
S = R / "state"
C = S / "coverage-v1/cpu-checks"
DEST = Path("/mnt/r/plastchem-euler/coverage-v1/cpu-checks")
REMOTE = "plastchem-euler/coverage-v1/cpu-checks"
BASE_SPEC = Path("/mnt/r/plastchem-euler/coverage-plans/supplement-inputs-v1-20260925T1350/inputs.json")
BASE_SPEC_SHA256 = "aa693c8063929510ef4f7c236220250d2704db4b3c9fa607fbe6b152f4cc0f6c"
REFERENCE_MODEL = "AMD EPYC 7763 64-Core Processor"
TOLERANCE = {"energy_hartree": 1e-6, "area_relative": 1e-3, "volume_relative": 1e-3, "logp": 0.01}
LOGP_SOLVENTS = ("toluene", "ethanol", "acetone")
LOGP_POLYMER = "pe"
#: (InChIKey, what it covers, the tier whose Milan run is the reference, prepared folder relative to state/)
REFERENCE_SET = [
    ("KBPLFHHGFOOTCA-UHFFFAOYSA-N", "C/H/O: 1-octanol", "campaign-v1", "campaign-v1/prepared/KBPLFHHGFOOTCA-UHFFFAOYSA-N"),
    ("BJQHLKABXJIVAM-UHFFFAOYSA-N", "C/H/O above 60 atoms: DEHP (66)", "campaign-v1", "pilot-v1/BJQHLKABXJIVAM-UHFFFAOYSA-N"),
    ("GLUCALKKMFBJEB-UHFFFAOYSA-N", "Br, Cl, N, O: 2-bromo-6-chloro-4-nitroaniline", "halogen-v1", "halogen-v1/prepared/GLUCALKKMFBJEB-UHFFFAOYSA-N"),
    ("VSMDINRNYYEDRN-UHFFFAOYSA-N", "I (def2 ECP): 4-iodophenol", "halogen-v1", "halogen-v1/prepared/VSMDINRNYYEDRN-UHFFFAOYSA-N"),
    ("JGTNAGYHADQMCM-UHFFFAOYSA-N", "S, F: perfluorobutanesulfonic acid", "publication-v1", "publication-v1/prepared/JGTNAGYHADQMCM-UHFFFAOYSA-N"),
    ("YTPLMLYBLZKORZ-UHFFFAOYSA-N", "S, aromatic: thiophene", "coverage-v1", "coverage-v1/prepared/YTPLMLYBLZKORZ-UHFFFAOYSA-N"),
    ("VONWDASPFIQPDY-UHFFFAOYSA-N", "P: dimethyl methylphosphonate", "coverage-v1", "coverage-v1/prepared/VONWDASPFIQPDY-UHFFFAOYSA-N"),
    ("JJQZDUKDJDQPMQ-UHFFFAOYSA-N", "Si: dimethoxydimethylsilane", "coverage-v1", "coverage-v1/prepared/JJQZDUKDJDQPMQ-UHFFFAOYSA-N"),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if str(path).startswith("/mnt/r/"):
        path.write_text(json.dumps(value, indent=2) + "\n")
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def check(result, what):
    if result.returncode:
        sys.exit(f"{what} failed: {(result.stderr or '')[:400]}")
    return result


def reference(key, tier):
    """The Milan run of a reference structure: (record, run result with stages, surface path, smiles)."""
    if tier == "publication-v1":
        record = json.loads((S / tier / "records" / f"{key}.json").read_text())
        back = S / tier / "incoming/returns" / key
        result = json.loads((back / "result.json").read_text())
        identity = record["identity"]
        verdict = dict(identity_verified=identity["identity_verified"], perceived_inchikey=identity["perceived_inchikey"])
        return dict(result, **verdict), back / "surface.orcacosmo", result["input"]["smiles"]
    path = S / tier / "records" / f"{key}.json"
    record = json.loads(path.read_text()) if path.exists() else {}
    if record.get("status") != "converged":
        return None, None, None
    surface = Path(record["archive_path"]) / "surface.orcacosmo"
    assert sha(surface) == record["surface_sha256"], key
    return record, surface, record["input"]["smiles"]


def reference_set():
    out = []
    for key, covers, tier, prepared in REFERENCE_SET:
        record, surface, smiles = reference(key, tier)
        prep = json.loads((S / prepared / "preparation.json").read_text())
        xyz = S / prepared / "input.xyz"
        assert sha(xyz) == prep["xyz_sha256"], key
        ready = record is not None and record.get("cpu_model") == REFERENCE_MODEL
        if record is not None:
            assert record["preparation"]["xyz_sha256"] == prep["xyz_sha256"], f"{key}: the reference ran another input"
        out.append(dict(inchikey=key, covers=covers, tier=tier, prepared=str(S / prepared), smiles=smiles or prep["input"]["smiles"],
                        atoms=len([l for l in xyz.read_text().splitlines()[2:] if len(l.split()) == 4]),
                        name=prep["input"].get("name") or covers, reference_ready=ready,
                        reference_cpu=record.get("cpu_model") if record else None,
                        reference_surface=str(surface) if surface else None))
    return out


def stage(tag, constraint, walltime):
    assert re.fullmatch(r"[a-z0-9-]+", tag)
    folder = C / tag
    assert not (folder / "submission.json").exists(), f"{tag} is submitted; a check is staged once"
    refs = reference_set()
    molecules = [dict(inchikey=r["inchikey"], smiles=r["smiles"], name=r["name"], atoms=r["atoms"], array_index=i,
                      group=tag, tier="cpu-check", covers=r["covers"], reference_tier=r["tier"]) for i, r in enumerate(refs)]
    policy = json.loads((S / "coverage-v1/policy.json").read_text())
    manifest = dict(campaign="contam-coverage-cpu-check", group=tag, name=f"contam-cpucheck-{tag}", constraint=constraint,
                    partition="research", walltime=walltime, mem="4G", policy=policy,
                    recipe={"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
                    authority="A-13 owner NOTE 2026-10-08 17:10 UTC", molecules=molecules)
    folder.mkdir(parents=True, exist_ok=True)
    write(folder / "manifest.json", manifest)
    sbatch = folder / "check.sbatch"
    sbatch.write_text("#!/bin/bash\n"
                      f"#SBATCH --job-name=contam-cpucheck-{tag}\n#SBATCH --partition=research\n#SBATCH --constraint={constraint}\n"
                      "#SBATCH --exclude=euler09,euler10\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n#SBATCH --cpus-per-task=1\n"
                      f"#SBATCH --mem=4G\n#SBATCH --time={walltime}\n#SBATCH --signal=B:USR1@120\n#SBATCH --no-requeue\n"
                      "set -euo pipefail\nsource \"$HOME/plastchem-euler/orca-env.sh\"\n"
                      f"exec python3 \"$HOME/{REMOTE}/{tag}/cpu_check_runner.py\" {tag}\n")
    files = [(folder / "manifest.json", "manifest.json"), (sbatch, "check.sbatch"),
             (R / "scripts/cpu_check_runner.py", "cpu_check_runner.py")]
    for r in refs:
        for name in ("input.xyz", "preparation.json"):
            files.append((Path(r["prepared"]) / name, f"prepared/{r['inchikey']}/{name}"))
    sums = folder / "staging.sha256"
    sums.write_text("".join(f"{sha(p)}  {name}\n" for p, name in files))
    files.append((sums, "staging.sha256"))
    archive = folder / "staging.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for path, name in files:
            tar.add(path, arcname=name, recursive=False)
    check(run("ssh", ["euler", f"mkdir -p ~/{REMOTE}/{tag}/logs ~/{REMOTE}/{tag}/runs ~/{REMOTE}/{tag}/returns"],
              capture_output=True, text=True), "mkdir")
    check(run("scp", ["-q", str(archive), f"euler:{REMOTE}/{tag}/"], capture_output=True, text=True), "scp")
    out = check(run("ssh", ["euler", f"cd ~/{REMOTE}/{tag} && tar -xzf staging.tar.gz && sha256sum -c --quiet staging.sha256 "
                                     f"&& sbatch --parsable --array=0-{len(molecules) - 1} --output=logs/%A_%a.out "
                                     f"--error=logs/%A_%a.err check.sbatch"], capture_output=True, text=True), "submit")
    array = out.stdout.strip().split(";")[0]
    assert array.isdigit(), out.stdout
    write(folder / "submission.json", dict(utc=utc(), tag=tag, constraint=constraint, walltime=walltime, array_job_id=array,
                                           structures=[m["inchikey"] for m in molecules],
                                           references_ready=[r["inchikey"] for r in refs if r["reference_ready"]]))
    print(json.dumps(dict(tag=tag, array=array, constraint=constraint, structures=len(molecules))))


SNAPSHOT = """import json,subprocess
from pathlib import Path
root=Path.home()/'{remote}/{tag}'
print(json.dumps(dict(records={{p.parent.name:json.loads(p.read_text()) for p in (root/'runs').glob('*/result.json')}},
  sacct=subprocess.run(['sacct','-j','{array}','-nP','--format=JobID,State,ElapsedRaw,NodeList,ExitCode'],capture_output=True,text=True).stdout)))
"""


def collect(tag):
    from identity_campaign import verify
    folder = C / tag
    submission = json.loads((folder / "submission.json").read_text())
    snap = json.loads(check(run("ssh", ["euler", "python3 -"], input=SNAPSHOT.format(remote=REMOTE, tag=tag, array=submission["array_job_id"]),
                                capture_output=True, text=True), "snapshot").stdout)
    states = {}
    for line in snap["sacct"].splitlines():
        fields = line.split("|")
        if "_" in fields[0] and fields[0].split("_")[1].isdigit():
            states[int(fields[0].split("_")[1])] = fields
    manifest = json.loads((folder / "manifest.json").read_text())
    out = {}
    for m in manifest["molecules"]:
        key = m["inchikey"]
        local = folder / "records" / f"{key}.json"
        if local.exists():
            out[key] = json.loads(local.read_text())
            continue
        r = snap["records"].get(key)
        acct = states.get(m["array_index"])
        state = acct[1].split()[0] if acct else ""
        if not r or r.get("status") not in ("converged_identity_pending", "failed"):
            if state in ("PREEMPTED", "CANCELLED", "NODE_FAIL", "TIMEOUT", "FAILED", "OUT_OF_MEMORY"):
                out[key] = dict(inchikey=key, status="failed", failure_mode="slurm_" + state.lower(), slurm_state=acct[1])
                write(local, out[key])
            continue
        r["slurm_accounting"] = dict(state=acct[1], elapsed_seconds=int(acct[2]) if acct and acct[2] else None,
                                     node_list=acct[3] if acct else None) if acct else None
        if r["status"] == "converged_identity_pending":
            target = DEST / tag
            target.mkdir(parents=True, exist_ok=True)
            check(run("scp", ["-rq", f"euler:{REMOTE}/{tag}/returns/{key}", str(target)], capture_output=True, text=True), "scp")
            back = target / key
            for name, digest in [("surface.orcacosmo", r["surface_sha256"]),
                                 *[(stage + ".inp", info["input_sha256"]) for stage, info in r["stages"].items()]]:
                assert sha(back / name) == digest, f"{tag} {key} {name} digest mismatch"
            r.update(verify(back / "optimized.xyz", key, m["smiles"]))
            r.update(status="converged" if r["identity_verified"] else "failed",
                     failure_mode=None if r["identity_verified"] else "return_integrity_or_connectivity",
                     archive_path=str(back))
        out[key] = r
        write(local, r)
    print(json.dumps(dict(tag=tag, collected=len(out), of=len(manifest["molecules"]),
                          statuses={k: v.get("status") for k, v in out.items()})))
    return out


def surface_stats(path):
    text = Path(path).read_text()
    get = lambda pattern: float(re.search(pattern, text, re.M).group(1))  # noqa: E731
    return dict(segments=int(get(r"^\s*(\d+)\s+# Number of surface points")), area=get(r"^\s*([0-9.]+)\s+# Area"),
                volume=get(r"^\s*([0-9.]+)\s+# Volume"))


def energies(record):
    """Every final single-point energy a run printed: the optimisation's (the last is the optimised geometry's), the
    COSMO stage's when its driver prints any, and the COSMO solute energy (the surface's own calculation)."""
    out = {f"{stage}_all": [float(x) for x in record["stages"][stage]["final_energies_hartree"]] for stage in ("opt", "cosmo")}
    out["opt_final"] = out["opt_all"][-1]
    out["cosmo_solute"] = float(record["cosmo_solute_energy_hartree"])
    return out


def logp_table(surfaces):
    """{surface path: {octanol_water, toluene_pe, ethanol_pe, acetone_pe}} with the production functions, 298.15 K, x = 0."""
    package = Path("/mnt/r/plastchem-euler/phase8-v1")
    sys.path.insert(0, str(R / "scripts"))
    from phase9_profiles import ProfileCache, infinite_dilution
    from phase9_worker_cpu import ensemble
    assert sha(BASE_SPEC) == BASE_SPEC_SHA256
    spec = json.loads(BASE_SPEC.read_text())
    cache = ProfileCache()
    paths = sorted(set(surfaces))
    profiles = [cache.get(p) for p in paths]
    phases = {name: spec["solvents"][name] for name in ("water", "1-octanol", *LOGP_SOLVENTS)}
    lng = {name: infinite_dilution(profiles, cache.get(s["surface"], s["surface_sha256"])) for name, s in phases.items()}
    conformers = spec["polymers"][LOGP_POLYMER]
    per_conformer = [infinite_dilution(profiles, cache.get(c["surface"], c["surface_sha256"])) for c in conformers]
    table = {}
    for i, path in enumerate(paths):
        polymer = ensemble(conformers, [values[i] for values in per_conformer])["normalized_gamma"]
        row = {"octanol_water": float((lng["water"][i] - lng["1-octanol"][i]) / math.log(10))}
        for name in LOGP_SOLVENTS:
            row[f"{name}_{LOGP_POLYMER}"] = float((polymer - lng[name][i]) / math.log(10))
        table[path] = row
    _ = package
    return table


def compare(tags):
    refs = {r["inchikey"]: r for r in reference_set()}
    surfaces, pairs = [], {}
    for tag in tags:
        for key, ref in refs.items():
            local = C / tag / "records" / f"{key}.json"
            cand = json.loads(local.read_text()) if local.exists() else None
            pairs[(tag, key)] = cand
            if cand and cand.get("status") == "converged" and ref["reference_ready"]:
                surfaces += [ref["reference_surface"], str(Path(cand["archive_path"]) / "surface.orcacosmo")]
    table = logp_table(surfaces) if surfaces else {}
    for tag in tags:
        rows, verdicts = [], []
        cpu_models, partitions = set(), set()
        for key, ref in refs.items():
            cand = pairs[(tag, key)]
            row = dict(inchikey=key, covers=ref["covers"], atoms=ref["atoms"], reference_tier=ref["tier"])
            if not ref["reference_ready"]:
                row.update(verdict="waiting", why="the Milan reference has not converged yet")
            elif cand is None:
                row.update(verdict="waiting", why="the candidate run has not finished")
            elif cand.get("status") != "converged" and (cand.get("failure_mode") or "").startswith(("slurm_preempted", "scheduler_signal_15", "slurm_cancelled", "slurm_node_fail")):
                row.update(verdict="rerun", why=f"interrupted by the scheduler ({cand.get('failure_mode')}); not a result")
            else:
                record, surface, _smiles = reference(key, ref["tier"])
                ref_ok = bool(record.get("identity_verified", record.get("status") == "converged"))
                cand_ok = cand.get("status") == "converged"
                row.update(reference_identity_verified=ref_ok, candidate_identity_verified=cand_ok,
                           reference_perceived_inchikey=record.get("perceived_inchikey"),
                           candidate_perceived_inchikey=cand.get("perceived_inchikey"),
                           candidate_cpu_model=cand.get("cpu_model"), candidate_node=cand.get("node"),
                           candidate_partition=cand.get("partition"), candidate_failure_mode=cand.get("failure_mode"))
                failures = []
                if ref_ok != cand_ok:
                    failures.append("D-IDENT verdict differs")
                if cand_ok:
                    cpu_models.add(cand["cpu_model"])
                    partitions.add(cand["partition"])
                    e_ref, e_cand = energies(record), energies(cand)
                    diffs = [abs(e_ref[k] - e_cand[k]) for k in ("opt_final", "cosmo_solute")]
                    if len(e_ref["cosmo_all"]) == len(e_cand["cosmo_all"]):
                        diffs += [abs(a - b) for a, b in zip(e_ref["cosmo_all"], e_cand["cosmo_all"])]
                    else:
                        failures.append("the COSMO stage printed a different number of energies")
                    s_ref = surface_stats(surface)
                    s_cand = surface_stats(Path(cand["archive_path"]) / "surface.orcacosmo")
                    l_ref = table[str(surface)]
                    l_cand = table[str(Path(cand["archive_path"]) / "surface.orcacosmo")]
                    row.update(max_energy_difference_hartree=max(diffs),
                               opt_cycles=dict(reference=record["stages"]["opt"]["geometry_cycles"],
                                               candidate=cand["stages"]["opt"]["geometry_cycles"]),
                               segments=dict(reference=s_ref["segments"], candidate=s_cand["segments"]),
                               area_relative_difference=abs(s_cand["area"] - s_ref["area"]) / s_ref["area"],
                               volume_relative_difference=abs(s_cand["volume"] - s_ref["volume"]) / s_ref["volume"],
                               logp=dict(reference=l_ref, candidate=l_cand),
                               max_logp_difference=max(abs(l_cand[k] - l_ref[k]) for k in l_ref),
                               wall_seconds=dict(reference=sum(record["stages"][s]["wall_seconds"] for s in ("opt", "cosmo")),
                                                 candidate=sum(cand["stages"][s]["wall_seconds"] for s in ("opt", "cosmo"))),
                               surface_identical=sha(surface) == sha(Path(cand["archive_path"]) / "surface.orcacosmo"))
                    if row["max_energy_difference_hartree"] > TOLERANCE["energy_hartree"]:
                        failures.append("energy")
                    if s_ref["segments"] != s_cand["segments"]:
                        failures.append("segment count")
                    if row["area_relative_difference"] > TOLERANCE["area_relative"]:
                        failures.append("area")
                    if row["volume_relative_difference"] > TOLERANCE["volume_relative"]:
                        failures.append("volume")
                    if row["max_logp_difference"] > TOLERANCE["logp"]:
                        failures.append("logP")
                row.update(verdict="fail" if failures else "pass", failures=failures)
            rows.append(row)
            verdicts.append(row["verdict"])
        if "fail" in verdicts:
            overall = "fail"
        elif all(v == "pass" for v in verdicts) and len(rows) >= 6:
            overall = "pass"
        else:
            overall = "incomplete"
        result = dict(utc=utc(), tag=tag, overall=overall, tolerance=TOLERANCE, reference_cpu_model=REFERENCE_MODEL,
                      reference="Milan runs of the same prepared inputs (research, milan&cpu)",
                      logp_basis=f"normalized convention, 298.15 K, x = 0; octanol/water and {', '.join(LOGP_SOLVENTS)} against {LOGP_POLYMER}",
                      cpu_models=sorted(cpu_models), partitions=sorted(partitions),
                      counts={v: verdicts.count(v) for v in sorted(set(verdicts))}, structures=rows)
        write(C / tag / "comparison.json", result)
        print(json.dumps(dict(tag=tag, overall=overall, counts=result["counts"], cpu_models=result["cpu_models"],
                              max_energy=max((r.get("max_energy_difference_hartree", 0) for r in rows), default=None),
                              max_logp=max((r.get("max_logp_difference", 0) for r in rows), default=None),
                              failures={r["inchikey"][:14]: r["failures"] for r in rows if r.get("failures")})))


def admitted():
    out = [dict(cpu_model=REFERENCE_MODEL, partition="research", constraint="milan&cpu",
                basis="the campaign's reference CPU (A-2); no check needed")]
    for path in sorted(C.glob("*/comparison.json")):
        result = json.loads(path.read_text())
        if result["overall"] == "pass" and len(result["cpu_models"]) == 1 and len(result["partitions"]) == 1:
            stage = json.loads((path.parent / "submission.json").read_text())
            out.append(dict(cpu_model=result["cpu_models"][0], partition=result["partitions"][0],
                            constraint=stage["constraint"], basis=f"reproduction check {result['tag']} passed {result['utc']}",
                            comparison_sha256=sha(path)))
    write(C / "admitted.json", dict(utc=utc(), authority="A-13 owner NOTE 2026-10-08 17:10 UTC", admitted=out))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "collect", "compare", "admitted", "references"])
    parser.add_argument("args", nargs="*")
    parser.add_argument("--time", default="24:00:00")
    a = parser.parse_args()
    if a.action == "stage":
        stage(a.args[0], a.args[1], a.time)
    elif a.action == "collect":
        for tag in a.args:
            collect(tag)
    elif a.action == "compare":
        compare(a.args)
    elif a.action == "admitted":
        admitted()
    else:
        print(json.dumps(reference_set(), indent=1))
