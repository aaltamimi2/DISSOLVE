"""A-13 item 5d: one retry round of earlier tiers' failed rows, run through the coverage pipeline (state/coverage-v1/rNN).
Only rows interrupted by the scheduler or a time limit (scheduler_signal_*, walltime_censored, slurm_timeout,
slurm_preempted) are retried, once, with the unchanged recipe from the same prepared geometry; D-IDENT rejections and
mmff_parameters_unavailable are final, and ValueError, geometry_nonconvergence and abnormal COSMO terminations were
inspected (2026-10-08: deterministic embedding failures and SCF non-convergence) and are final too. The walltime is a
recorded resource choice: these rows already ran 7-23 h and were cut.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r01 [--walltime 96:00:00]

--oom (A-13, 2026-10-09): a round of the coverage runs SLURM killed OUT_OF_MEMORY (all on Milan-X so far), each from its own
prepared geometry with the unchanged recipe and the memory raised to 8G, the per-row raise A-13 allows after an
out-of-memory kill. The round keeps its own run folders (retry_folders: coverage-v1/retries/rNN/...), since the runner
never reruns a folder that holds an earlier attempt; the walltime is the chunks' rule for its largest molecule and is
lowered per task after submission (coverage_task_walltime.py).

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r02 --oom

--time-limit (A-13, 2026-10-10): a round of the coverage runs that reached their per-task time limit (6 x the cost fit;
the runner's guard or the scheduler's signal ended them), unchanged recipe and memory, own run folders, with four times
the first limit (24 x the fit for the round's largest molecule). The per-task lowering is not applied to this round.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r05 --time-limit

--embedding (A-13, owner decision 2026-10-12: "if justifiable, fix, otherwise accept as failed"): a round of the rows whose
frozen preparation embedded no conformer at all (ETKDGv3 from the metric matrix returns nothing for these 129-235 atom
molecules; recorded as ValueError "Bad Conformer Id"). They are prepared again with one change, useRandomCoords=True
(distance geometry started from random coordinates, RDKit's documented setting for large molecules); everything else is
the frozen recipe: ETKDGv3, seed 12345, 300 embeddings, pruneRmsThresh 0.5, MMFF94 maxIters 2000, the lowest-MMFF-energy
conformer to DFT, then the unchanged ORCA recipe and D-IDENT. The change touches only the starting coordinates of the
DFT optimization, is recorded in each preparation.json and manifest row, and the failed frozen preparation is kept
beside it. A molecule that still embeds nothing (two embeddings are tried first) stays a final failure.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r09 --embedding

--cosmo-restart (A-13, owner decision 2026-10-12): a round of the rows whose optimization converged and whose COSMO stage
then stopped at ORCA's 125-iteration SCF limit (large conjugated dyes in the diffuse def2-TZVPD basis; "the SCF has not
converged" in cosmo.out, checked on Euler for every row). COSMORS(Water) does not pass an SCF setting on to the two
calculations it writes (tested: %scf MaxIter 500 in cosmo.inp leaves their MaxIter at 125), so nothing in the input can
change. The round reruns the byte-identical cosmo.inp in its own folder with the two orbital files the stopped stage
left, which ORCA's AutoStart reads as the starting guess; the optimization is reused, not repeated (identical input,
output copied). Method, basis, convergence criteria and the driver that writes the surface are unchanged; only the
starting orbitals of the SCF differ. Tested on one row first (coverage-v1/tests/scf-restart-MVIFQPPFCHUSIH). Once per
row: a second SCF failure is final.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_retry.py r10 --cosmo-restart"""
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
    parser.add_argument("--oom", action="store_true", help="the coverage runs killed OUT_OF_MEMORY, with 8G")
    parser.add_argument("--time-limit", action="store_true", help="the coverage runs that reached their time limit, 4x longer")
    parser.add_argument("--cosmo-restart", action="store_true", help="rows whose COSMO SCF stopped at 125 iterations, from their orbitals")
    parser.add_argument("--embedding", action="store_true", help="rows the frozen embedding gave no conformer, with useRandomCoords")
    args = parser.parse_args()
    assert re.fullmatch(r"r\d\d", args.round)
    out = P / args.round / "manifest.json"
    assert not out.exists(), f"{out} exists; a retry round is built once"
    if args.oom:
        return out_of_memory_round(args.round, out)
    if args.time_limit:
        return out_of_memory_round(args.round, out, kind="time_limit")
    if args.embedding:
        return embedding_round(args.round, out)
    if args.cosmo_restart:
        return cosmo_restart_round(args.round, out)
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


def out_of_memory_round(round_name, out, kind="out_of_memory"):
    import math
    listed = {m["inchikey"] for path in P.glob("r[0-9][0-9]/manifest.json")
              if json.loads(path.read_text()).get("retry_kind") == kind
              for m in json.loads(path.read_text())["molecules"]}
    molecules = []
    for path in sorted((P / "records").glob("*.json")):
        record = json.loads(path.read_text())
        key = record.get("inchikey") or path.stem
        oom = str((record.get("slurm_accounting") or {}).get("state", "")).startswith("OUT_OF_MEMORY")
        timed = not oom and record.get("status") == "failed" and (record.get("execution_outcome") == "time_limit" or record.get(
            "failure_mode") in ("slurm_timeout", "walltime_censored", "scheduler_signal_10"))
        if key in listed or not (oom if kind == "out_of_memory" else timed):
            continue
        prep = json.loads((P / "prepared" / key / "preparation.json").read_text())
        assert prep["status"] == "prepared" and sha(P / "prepared" / key / "input.xyz") == prep["xyz_sha256"], key
        molecules.append(dict(record["input"], group=round_name, array_index=len(molecules), retry_of=dict(
            group=record.get("group"), failure_mode=record.get("failure_mode"), array_job_id=record.get("array_job_id"),
            array_task_id=record.get("array_task_id"), node=record.get("node"), cpu_model=record.get("cpu_model"),
            elapsed_seconds=record.get("elapsed_seconds"), slurm_accounting=record.get("slurm_accounting"),
            record_sha256=sha(path))))
    assert molecules, f"no {kind} run to retry"
    largest = max(m["atoms"] for m in molecules)
    factor = 6 if kind == "out_of_memory" else 24
    hours = min(384, max(3 if kind == "out_of_memory" else 12, math.ceil(factor * math.exp(-0.687758) * largest ** 2.305719 * 1.113048 / 3600)))
    manifest = {
        "campaign": "contam-coverage-milan-v1", "group": round_name, "name": f"contam-coverage-{round_name}",
        "walltime": f"{hours}:00:00", "mem": "8G" if kind == "out_of_memory" else "4G", "retry_folders": True, "retry_kind": kind,
        "policy": json.loads((P / "policy.json").read_text()),
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "input": {"path": str(WORKLIST.relative_to(ROOT)), "sha256": WORKLIST_SHA256},
        "chunk_rule": ("A-13: coverage runs killed OUT_OF_MEMORY at 4G, retried once from the same prepared geometry with "
                       "8G and their own run folders") if kind == "out_of_memory" else
                      ("A-13: coverage runs that reached their time limit (6 x the cost fit), retried once from the same "
                       "prepared geometry with 24 x the fit and their own run folders"),
        "molecules": molecules,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"round": round_name, "molecules": len(molecules), "walltime": manifest["walltime"], "mem": manifest["mem"], "kind": kind,
                      "from": sorted({m["retry_of"]["group"] for m in molecules}), "manifest_sha256": sha(out)}))


def cosmo_restart_round(round_name, out):
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from euler_transport import run
    retry_folders = {p.parent.name: bool(json.loads(p.read_text()).get("retry_folders")) for p in P.glob("[cr][0-9][0-9]/manifest.json")}
    listed = {m["inchikey"] for path in P.glob("r[0-9][0-9]/manifest.json")
              if json.loads(path.read_text()).get("retry_kind") == "cosmo_restart"
              for m in json.loads(path.read_text())["molecules"]}
    rows = []
    for path in sorted((P / "records").glob("*.json")):
        record = json.loads(path.read_text())
        stages = record.get("stages") or {}
        if record.get("status") == "failed" and record.get("failure_mode") in ("orca_nonzero_or_abnormal_termination", "scf_nonconvergence") \
                and str(record.get("error", "")).startswith("cosmo exit") and stages.get("opt", {}).get("exit_code") == 0 \
                and "cosmo" in stages and record["inchikey"] not in listed:
            folder = ("retries/" + record["group"] + "/" if retry_folders[record["group"]] else "") + "runs/" + record["inchikey"]
            rows.append((path, record, folder))
    assert rows, "no COSMO-stage SCF failure to restart"
    check = "cd ~/plastchem-euler/coverage-v1; for d in " + " ".join(f for _, _, f in rows) + \
        "; do echo \"$d $(grep -c 'SCF has not converged' $d/cosmo.out) $(ls $d/result.json $d/opt.out $d/cosmo.solute_vac.gbw $d/cosmo.solute_cpcm.gbw 2>/dev/null | wc -l)\"; done; true"
    result = run("ssh", ["euler", check], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-300:]
    seen = {line.split()[0]: (int(line.split()[1]), int(line.split()[2])) for line in result.stdout.splitlines() if len(line.split()) == 3}
    molecules, not_eligible = [], []
    for path, record, folder in rows:
        messages, files = seen[folder]
        if messages < 1 or files != 4:
            not_eligible.append(dict(inchikey=record["inchikey"], folder=folder, scf_messages=messages, files_present=files))
            continue
        molecules.append(dict(record["input"], group=round_name, array_index=len(molecules), cosmo_restart=dict(run_folder=folder),
                              retry_of=dict(group=record.get("group"), failure_mode=record.get("failure_mode"), error=record.get("error"),
                                            array_job_id=record.get("array_job_id"), array_task_id=record.get("array_task_id"),
                                            node=record.get("node"), cpu_model=record.get("cpu_model"),
                                            scf_not_converged_messages=messages, record_sha256=sha(path))))
    assert molecules, f"none eligible: {not_eligible}"
    manifest = {
        "campaign": "contam-coverage-milan-v1", "group": round_name, "name": f"contam-coverage-{round_name}",
        "walltime": "72:00:00", "mem": "4G", "retry_folders": True, "retry_kind": "cosmo_restart",
        "policy": json.loads((P / "policy.json").read_text()),
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "recipe_exception": dict(change="COSMO stage rerun from the stopped stage's orbitals (ORCA AutoStart); identical inputs",
                                 authority="Owner 2026-10-12: fix if justifiable, otherwise accept as failed."),
        "input": {"path": str(WORKLIST.relative_to(ROOT)), "sha256": WORKLIST_SHA256},
        "chunk_rule": ("A-13, owner 2026-10-12: rows whose COSMO SCF stopped at 125 iterations, the identical cosmo.inp rerun once "
                       "from the stopped stage's orbitals, the converged optimization reused; own run folders"),
        "not_eligible": not_eligible,
        "molecules": molecules,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"round": round_name, "molecules": len(molecules), "walltime": manifest["walltime"], "kind": "cosmo_restart",
                      "not_eligible": not_eligible, "manifest_sha256": sha(out)}))


EMBEDDING_AUTHORITY = ("Owner 2026-10-12: fix if justifiable, otherwise accept as failed. Only useRandomCoords differs "
                       "from the frozen preparation; it changes the starting coordinates of the DFT optimization only.")


def prepare_random_coordinates(m):
    """prepare_campaign.prepare with useRandomCoords=True; the failed frozen preparation is kept beside the new one."""
    import os
    import time
    from rdkit import Chem
    from rdkit.Chem import AllChem
    path = P / "prepared" / m["inchikey"]
    record, kept = path / "preparation.json", path / "preparation.frozen-recipe-failure.json"
    old = json.loads(record.read_text())
    if old.get("recipe", {}).get("use_random_coords"):
        return old
    assert old["status"] == "preparation_failed" and old["error"] == "Bad Conformer Id" and not (path / "input.xyz").exists(), m["inchikey"]
    start = time.monotonic()
    r = {"input": m, "status": "preparing", "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000,
                                                       "dft_conformers": 1, "use_random_coords": True},
         "recipe_exception": dict(change="ETKDGv3 useRandomCoords=True", authority=EMBEDDING_AUTHORITY,
                                  frozen_preparation_sha256=sha(record), frozen_failure=old["error"])}
    mol = Chem.MolFromSmiles(m["smiles"])
    assert mol is not None and Chem.MolToInchiKey(mol) == m["inchikey"]
    assert not any(a.GetIsotope() for a in mol.GetAtoms())
    h = Chem.AddHs(mol)
    assert AllChem.MMFFHasAllMoleculeParams(h), m["inchikey"]
    params = AllChem.ETKDGv3()
    params.randomSeed = 12345
    params.pruneRmsThresh = 0.5
    params.numThreads = 1
    params.useRandomCoords = True
    # two embeddings first: a cage that random coordinates cannot embed either (the fullerenes) fails here in seconds
    # to minutes, where 300 failing embeddings would take hours; it is reported as still failed, never substituted
    if not list(AllChem.EmbedMultipleConfs(Chem.Mol(h), numConfs=2, params=params)):
        return dict(old, random_coordinates_attempt=dict(embedded_count=0, embeddings_tried=2, wall_seconds=time.monotonic() - start))
    ids = list(AllChem.EmbedMultipleConfs(h, numConfs=300, params=params))
    if not ids:
        return dict(old, random_coordinates_attempt=dict(embedded_count=0, embeddings_tried=300, wall_seconds=time.monotonic() - start))
    scores = AllChem.MMFFOptimizeMoleculeConfs(h, numThreads=1, maxIters=2000)
    ranked = sorted((energy, cid) for cid, (status, energy) in zip(ids, scores) if status == 0)
    if not ranked:
        return dict(old, random_coordinates_attempt=dict(embedded_count=len(ids), mmff_converged_count=0,
                                                         wall_seconds=time.monotonic() - start))
    energy, cid = ranked[0]
    xyz = Chem.MolToXYZBlock(h, confId=cid)
    r.update(status="prepared", embedded_count=len(ids), mmff_converged_count=len(ranked), selected_conformer_id=cid,
             selected_mmff_energy_kcal=energy, dft_conformer_count=1, xyz_sha256=hashlib.sha256(xyz.encode()).hexdigest(),
             wall_seconds=time.monotonic() - start, pid=os.getpid())
    shutil.copyfile(record, kept)
    (path / "input.xyz").write_text(xyz)
    tmp = record.with_suffix(".tmp")
    tmp.write_text(json.dumps(r, indent=2) + "\n")
    tmp.replace(record)
    return r


def embedding_round(round_name, out):
    import concurrent.futures
    import math
    listed = {m["inchikey"] for path in P.glob("r[0-9][0-9]/manifest.json")
              if json.loads(path.read_text()).get("retry_kind") == "embedding"
              for m in json.loads(path.read_text())["molecules"]}
    rows = []
    for path in sorted((P / "records").glob("*.json")):
        record = json.loads(path.read_text())
        if record.get("failure_mode") == "ValueError" and record.get("error") == "Bad Conformer Id" and not record.get("dft_ran") \
                and record["inchikey"] not in listed:
            rows.append((path, record))
    assert rows, "no embedding failure to retry"
    with concurrent.futures.ProcessPoolExecutor(max_workers=4) as pool:
        preps = list(pool.map(prepare_random_coordinates, [r["input"] for _, r in rows]))
    molecules, still_failed = [], []
    for (path, record), prep in zip(rows, preps):
        if prep["status"] != "prepared":
            still_failed.append(dict(inchikey=record["inchikey"], name=record["input"]["name"], attempt=prep.get("random_coordinates_attempt")))
            continue
        molecules.append(dict(record["input"], group=round_name, array_index=len(molecules),
                              recipe_exception="ETKDGv3 useRandomCoords=True (preparation.json)", retry_of=dict(
            group=record.get("group"), failure_mode=record.get("failure_mode"), error=record.get("error"), record_sha256=sha(path))))
    (P / f"{round_name}-embedding-attempt.json").write_text(json.dumps(dict(prepared=[m["inchikey"] for m in molecules],
                                                                              still_failed=still_failed), indent=1) + "\n")
    assert molecules, "random coordinates embedded none of them"
    largest = max(m["atoms"] for m in molecules)
    hours = min(384, max(3, math.ceil(6 * math.exp(-0.687758) * largest ** 2.305719 * 1.113048 / 3600)))
    manifest = {
        "campaign": "contam-coverage-milan-v1", "group": round_name, "name": f"contam-coverage-{round_name}",
        "walltime": f"{hours}:00:00", "mem": "4G", "retry_folders": True, "retry_kind": "embedding",
        "policy": json.loads((P / "policy.json").read_text()),
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1, "use_random_coords": True},
        "recipe_exception": dict(change="ETKDGv3 useRandomCoords=True", authority=EMBEDDING_AUTHORITY),
        "input": {"path": str(WORKLIST.relative_to(ROOT)), "sha256": WORKLIST_SHA256},
        "chunk_rule": ("A-13, owner 2026-10-12: rows the frozen embedding gave no conformer, prepared with useRandomCoords=True "
                       "and otherwise the frozen recipe; own run folders; walltime 6 x the cost fit, lowered per task"),
        "molecules": molecules,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"round": round_name, "molecules": len(molecules), "walltime": manifest["walltime"], "mem": "4G", "kind": "embedding",
                      "still_failed": [f["name"][:40] for f in still_failed], "manifest_sha256": sha(out)}))


if __name__ == "__main__":
    main()
