"""A-13 item 5d: one retry round of earlier tiers' failed rows, run through the coverage pipeline (state/coverage-v1/rNN).
Only rows interrupted by the scheduler or a time limit (scheduler_signal_*, walltime_censored, slurm_timeout,
slurm_preempted) are retried, once, with the unchanged recipe from the same prepared geometry; D-IDENT rejections and
mmff_parameters_unavailable are final, and ValueError, geometry_nonconvergence and abnormal COSMO terminations were
inspected (2026-10-08: deterministic embedding failures and SCF non-convergence) and are final too. The walltime is a
recorded resource choice: these rows already ran 7-23 h and were cut.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r01 [--walltime 96:00:00]"""
import argparse
import csv
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "state/coverage-v1"
WORKLIST = ROOT / "inputs/plastchem_coverage_a13_worklist.csv"
WORKLIST_SHA256 = "40b4ca4ef3237e7947dfd77beab339a0acca5beb4fb732b135b0f8ce228683e2"
TIERS = ("campaign-v1", "tier2-v1", "halogen-v1")
RETRYABLE = re.compile(r"^(scheduler_signal_\d+|walltime_censored|slurm_timeout|slurm_preempted)$")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("round", help="rNN")
    parser.add_argument("--walltime", default="96:00:00")
    args = parser.parse_args()
    assert re.fullmatch(r"r\d\d", args.round)
    out = P / args.round / "manifest.json"
    assert not out.exists(), f"{out} exists; a retry round is built once"
    assert sha(WORKLIST) == WORKLIST_SHA256
    done = {m["inchikey"] for path in P.glob("r[0-9][0-9]/manifest.json") for m in json.loads(path.read_text())["molecules"]}
    molecules = []
    for r in csv.DictReader(WORKLIST.open()):
        if r["release_status"] != "failed" or r["inchikey"] in done:
            continue
        tier = next((t for t in TIERS if (ROOT / "state" / t / "records" / f"{r['inchikey']}.json").exists()), None)
        if tier is None:
            continue
        record = json.loads((ROOT / "state" / tier / "records" / f"{r['inchikey']}.json").read_text())
        if not RETRYABLE.match(record.get("failure_mode") or ""):
            continue
        source = ROOT / "state" / tier / "prepared" / r["inchikey"]
        prep = json.loads((source / "preparation.json").read_text())
        assert prep["status"] == "prepared" and sha(source / "input.xyz") == prep["xyz_sha256"] == record["preparation"]["xyz_sha256"]
        target = P / "prepared" / r["inchikey"]
        if target.exists():  # the same structure prepared before: it must be the identical geometry
            assert sha(target / "input.xyz") == prep["xyz_sha256"], r["inchikey"]
        else:
            target.mkdir(parents=True)
            for name in ("input.xyz", "preparation.json"):
                shutil.copyfile(source / name, target / name)
        inp = record["input"]
        molecules.append({
            "name": inp["name"], "name_source": f"{tier} (its earlier tier's identity)", "smiles": inp["smiles"],
            "plastchem_id": inp.get("plastchem_id", ""), "cas": inp.get("cas", ""), "inchikey": r["inchikey"],
            "molecular_weight_g_mol": inp["molecular_weight_g_mol"], "n_atoms_with_H": str(inp["atoms"]),
            "array_index": len(molecules), "atoms": int(inp["atoms"]), "group": args.round, "tier": inp.get("tier", tier),
            "worklist_order": int(r["order"]), "kind": r["kind"], "elements": r["elements"],
            "served_entries_direct": r["served_entries_direct"], "served_entries_as_parent": r["served_entries_as_parent"],
            "entry_names": r["entry_names"], "plastchem_groups": r["plastchem_groups"],
            "families_existing": r["families_existing"], "families_proposed": r["families_proposed"],
            "retry_of": dict(tier=tier, failure_mode=record.get("failure_mode"), array_job_id=record.get("array_job_id"),
                             array_task_id=record.get("array_task_id"), node=record.get("node"),
                             cpu_model=record.get("cpu_model"), elapsed_seconds=record.get("elapsed_seconds"),
                             slurm_accounting=record.get("slurm_accounting"),
                             record_sha256=sha(ROOT / "state" / tier / "records" / f"{r['inchikey']}.json"))})
    assert molecules, "nothing to retry"
    manifest = {
        "campaign": "contam-coverage-milan-v1", "group": args.round, "name": f"contam-coverage-{args.round}",
        "walltime": args.walltime, "mem": "4G", "policy": json.loads((P / "policy.json").read_text()),
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "input": {"path": str(WORKLIST.relative_to(ROOT)), "sha256": WORKLIST_SHA256},
        "chunk_rule": "A-13 5d: earlier tiers' rows interrupted by the scheduler or a time limit, retried once, unchanged",
        "molecules": molecules,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"round": args.round, "molecules": len(molecules), "walltime": args.walltime,
                      "orders": [m["worklist_order"] for m in molecules], "manifest_sha256": sha(out)}))


if __name__ == "__main__":
    main()
