"""A-13 owner request (orchestrator relay, 2026-10-08): work-list structures computed ahead of their order, with the same
frozen recipe and their identical prepared input (state/coverage-v1/prepared/<InChIKey>/, made with their chunk). They
run as group c00, which sorts before every chunk, so coverage_throttle.py gives it its Milan slots first. The chunk that
lists a structure skips it when staged (stage_coverage.py: a structure is staged once), and summarize_coverage.py and
build_promotion_coverage.py take the group that ran it, so it is counted and released once.

    python3 scripts/build_coverage_request.py c00 INCHIKEY [INCHIKEY ...] --reason "..." """
import argparse
import datetime
import json
import math
from pathlib import Path

R = Path(__file__).resolve().parents[1]
P = R / "state/coverage-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group")
    parser.add_argument("keys", nargs="+")
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    assert args.group == "c00", "owner requests run as group c00, which sorts before every chunk"
    target = P / args.group / "manifest.json"
    assert not target.exists(), f"{target} exists; a group is built once"
    found = {}
    for path in sorted(P.glob("c[0-9][0-9]/manifest.json")):
        manifest = json.loads(path.read_text())
        for mol in manifest["molecules"]:
            if mol["inchikey"] in args.keys and mol["inchikey"] not in found:
                found[mol["inchikey"]] = (path.parent.name, manifest, mol)
    missing = sorted(set(args.keys) - set(found))
    assert not missing, f"not in any built chunk: {missing}"
    for key, (chunk, _, _) in found.items():
        assert not (P / chunk / "staging-summary.json").exists(), f"{key}: {chunk} is staged already"
        assert not (P / "records" / f"{key}.json").exists(), f"{key} has a record already"
        prep = json.loads((P / "prepared" / key / "preparation.json").read_text())
        assert prep["status"] == "prepared", f"{key}: preparation {prep['status']}"
    utc = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    molecules = []
    for index, key in enumerate(args.keys):
        chunk, _, mol = found[key]
        molecules.append(dict(mol, group=args.group, array_index=index,
                              requested=dict(reason=args.reason, utc=utc, listed_by=f"{chunk} index {mol['array_index']}")))
    source = found[args.keys[0]][1]
    largest = max(m["atoms"] for m in molecules)
    # the chunks' rule (build_coverage_chunk.py --walltime auto): 6 x the campaign fit for the largest molecule, 3 h to 16 d
    hours = min(384, max(3, math.ceil(6 * math.exp(-0.687758) * largest ** 2.305719 * 1.113048 / 3600)))
    manifest = {
        "campaign": source["campaign"], "group": args.group, "name": f"contam-coverage-{args.group}",
        "walltime": f"{hours}:00:00", "mem": source["mem"], "policy": source["policy"], "recipe": source["recipe"],
        "input": source["input"],
        "chunk_rule": f"owner request ahead of the work-list order ({args.reason}): "
                      + "; ".join(f"order {found[k][2]['worklist_order']}, {found[k][0]} index {found[k][2]['array_index']}"
                                  for k in args.keys),
        "molecules": molecules,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"group": args.group, "walltime": manifest["walltime"], "chunk_rule": manifest["chunk_rule"],
                      "molecules": [(m["name"], m["inchikey"], m["atoms"]) for m in molecules]}))


if __name__ == "__main__":
    main()
