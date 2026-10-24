"""Exploratory conformer study on CHTC (owner request 2026-10-12; not a release input): turn the archives pulled by
conformer_htc_fetch.py into records, as conformer_collect.py does for the Euler study. For each returned conformer the
digests of the surface and of the two ORCA inputs are checked against the run's own record, the ORCA inputs are checked
against the recipe and the prepared geometry, and the optimized geometry is verified with the campaign's identity
policy (D-IDENT: connectivity first block of the parent InChIKey, RDKit with Open Babel as reference; an unfinished
RDKit perception is held, never rejected). A rejection stays a rejection here: the bonded-graph observation the owner
allowed for the coverage campaign is not applied.

Records: state/conformers-htc-v1/records/<key>.json; counts: state/conformers-htc-v1/summary.json. A conformer with a
record is not looked at again.

    python3 scripts/conformer_htc_collect.py [--workers N]     (anaconda RDKit 2023.09.2, the identity environment)"""
import argparse
import collections
import hashlib
import os
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity_campaign import verify  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FETCHED = ROOT / "state/conformers-htc-v1"  # where conformer_htc_fetch.py puts every archive, of either study
# conformers-htc-30-v1: the 30-conformer test of the compounds not settled at 10 (its k01-k10 are the same geometries)
P = ROOT / "state" / os.environ.get("CONFORMER_STUDY", "conformers-htc-v1")
HEAD = {"opt": "! OPT BP86 def2-TZVP(-f) TightSCF\n%maxcore 1500\n* xyz 0 1\n", "cosmo": "! COSMORS(Water)\n%maxcore 1500\n* xyz 0 1\n"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def collect(key):
    folder = FETCHED / "returned" / key
    r = json.loads((folder / "result.json").read_text())
    if r.get("status") != "converged_identity_pending":
        return r
    try:
        prep = json.loads((P / "prepared" / key / "preparation.json").read_text())
        assert r["inchikey"] == key and r["input"] == prep["input"], "the run's input is not the prepared one"
        assert r["preparation"]["xyz_sha256"] == prep["xyz_sha256"], "the run's geometry is not the prepared one"
        for name, digest in [("surface.orcacosmo", r["surface_sha256"]), *[(s + ".inp", i["input_sha256"]) for s, i in r["stages"].items()]]:
            assert sha(folder / name) == digest, name + " digest mismatch"
        for stage, head in HEAD.items():
            assert (folder / (stage + ".inp")).read_text().startswith(head), stage + " input is not the recipe's"
        r.update(verify(folder / "optimized.xyz", r["input"]["parent_inchikey"], r["input"]["smiles"]))
        if not r["identity_verified"] and any(o.get("undecided") for o in r["identity_observations"]):
            r.update(identity_pending="rdkit_not_finished", archive_path=str(folder))  # held, not rejected
        elif not r["identity_verified"]:
            r.update(status="failed", failure_mode="return_integrity_or_connectivity", archive_path=str(folder),
                     error=r["identity_rejection"])
        else:
            r.update(status="converged", archive_path=str(folder))
    except Exception as exc:
        r.update(status="failed", failure_mode="return_integrity_or_connectivity", error=str(exc), identity_verified=False)
    return r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    fetched = json.loads((FETCHED / "fetched.json").read_text())
    todo = sorted(k for k in fetched if (P / "prepared" / k).is_dir() and not (P / "records" / f"{k}.json").exists())
    held = 0
    with ProcessPoolExecutor(args.workers) as pool:
        for key, r in zip(todo, pool.map(collect, todo, chunksize=4)):
            if r.get("identity_pending"):
                held += 1  # no record: looked at again on the next pass
                continue
            write(P / "records" / f"{key}.json", r)
    by_compound = collections.defaultdict(collections.Counter)
    for path in (P / "records").glob("*.json"):
        r = json.loads(path.read_text())
        status = "rejected" if r.get("failure_mode") == "return_integrity_or_connectivity" else r.get("status", "?")
        by_compound[r["input"]["parent_inchikey"]][status] += 1
    total = collections.Counter()
    for c in by_compound.values():
        total.update(c)
    summary = dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), counts=dict(total), new_this_pass=len(todo) - held,
                   identity_held=held, complete_ensembles=sum(1 for k, c in by_compound.items()
                                          if c.get("converged") == json.loads((P / "ensembles" / f"{k}.json").read_text())["kept"]),
                   by_compound={k: dict(v) for k, v in sorted(by_compound.items())})
    write(P / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("utc", "counts", "new_this_pass", "identity_held", "complete_ensembles")}))


if __name__ == "__main__":
    main()
