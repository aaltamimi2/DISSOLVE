"""A-13 (2026-10-09): finish the D-IDENT verification of returns held as awaiting verification because RDKit's
DetermineBonds did not finish in the collector (its time limit, geometry_identity.RDKIT_SECONDS, or its process died)
and no other engine matched. The same perception (same code, same engines, same decision rule) runs here without the short limit
(--hours, 12 by default); the record then becomes converged or a D-IDENT rejection exactly as the collector would
have made it. The long perception runs outside the collector lock; only the record and ledger writes take it.

    python3 scripts/verify_pending_identity.py [--hours 12] [KEY ...]"""
import argparse
import fcntl
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import geometry_identity  # noqa: E402
from identity_campaign import verify  # noqa: E402

P = Path(__file__).resolve().parents[1] / "state/coverage-v1"


def write(path, obj):
    if str(path).startswith("/mnt/r/"):
        path.write_text(json.dumps(obj, indent=2) + "\n")
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=12)
    parser.add_argument("keys", nargs="*")
    args = parser.parse_args()
    geometry_identity.RDKIT_SECONDS = args.hours * 3600
    held = {p.stem: json.loads(p.read_text()) for p in (P / "records").glob("*.json")}
    held = {k: r for k, r in held.items() if r.get("identity_pending") == "rdkit_not_finished"
            and r.get("status") == "converged_identity_pending" and (not args.keys or k in args.keys)}
    for key, r in sorted(held.items()):
        folder = Path(r["archive_path"])
        start = time.time()
        result = verify(folder / "optimized.xyz", key, r["input"]["smiles"])
        seconds = round(time.time() - start, 1)
        with (P / "collector.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            r = json.loads((P / "records" / f"{key}.json").read_text())
            assert r.get("identity_pending") == "rdkit_not_finished", key  # unchanged while the perception ran
            r.update(result)
            r["identity_pending"] = None
            r["identity_pending_resolved"] = dict(utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                                  rdkit_limit_hours=args.hours, perception_seconds=seconds)
            if r["identity_verified"]:
                r.update(status="converged")
            elif any(o.get("undecided") for o in r["identity_observations"]):
                r.update(identity_pending="rdkit_not_finished")  # still undecided: stays held
            else:
                r.update(status="failed", failure_mode="return_integrity_or_connectivity", identity_verified=False,
                         error="No perception engine produced the required first-block match")
            write(folder / "result.json", r)
            write(P / "records" / f"{key}.json", r)
            ledger = json.loads((P / "retrieved.json").read_text())
            ledger[key] = dict(ledger.get(key, {}), status=r["status"], snapshot_utc=r["identity_pending_resolved"]["utc"])
            write(P / "retrieved.json", ledger)
        print(json.dumps({"key": key, "status": r["status"], "identity_verified": r["identity_verified"],
                          "perceived_keys_by_engine": r.get("perceived_keys_by_engine"), "seconds": seconds}))


if __name__ == "__main__":
    main()
