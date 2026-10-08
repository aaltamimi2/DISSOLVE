"""A-13 coverage campaign: the manifest of one ORCA chunk of the pinned work list (owner, 2026-10-08: "go from lowest to
highest MW"). The A-11 builder's counterpart (build_halogen_inputs.py): it re-checks the pinned input and writes
state/coverage-v1/<chunk>/manifest.json in the halogen manifest's shape.

Chunk cNN is the NN-th run of 100 work-list rows that need ORCA (release_status empty: never run), in work-list order
(molecular weight, then atoms, then InChIKey). Rows the release already lists are not ORCA work: thermodynamics_pending
and running rows go to the harvest (A-13 5a), failed rows to the one-time retry review (A-13 5d).

Names (A-11 rule, A-13 7b): the work list's PlastChem name; a neutral parent with no PlastChem entry of its own takes
PubChem's name for its InChIKey (its Title, or its IUPAC name when the Title is only a registry number), cached in
inputs/coverage_pubchem_names_a13.json like A-12's PubChem cache. PubChem is asked only when the cache lacks a key.

    ~/.venvs/cosmo-logp/bin/python scripts/build_coverage_chunk.py c01 [--walltime 72:00:00] [--mem 4G]"""
import argparse
import csv
import hashlib
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")
ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "inputs/plastchem_coverage_a13_worklist.csv"
WORKLIST_SHA256 = "40b4ca4ef3237e7947dfd77beab339a0acca5beb4fb732b135b0f8ce228683e2"
NAMES = ROOT / "inputs/coverage_pubchem_names_a13.json"
P = ROOT / "state/coverage-v1"
CHUNK = 100
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/inchikey/{}/property/Title,IUPACName/JSON"
#: a name that is only a registry number or a database placeholder is not a chemical name
PLACEHOLDER = re.compile(r"^(\d{2,7}-\d{2}-\d|CID \d+|EINECS \d{3}-\d{3}-\d|SCHEMBL\d+|DTXSID\d+|CHEMBL\d+|AKOS\d+|NSC\d+|UNII-\w+)$",
                         re.IGNORECASE)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def never_run():
    assert sha256(WORKLIST) == WORKLIST_SHA256, "the pinned work list changed"
    rows = list(csv.DictReader(WORKLIST.open()))
    assert [int(r["order"]) for r in rows] == list(range(1, len(rows) + 1))
    return [r for r in rows if not r["release_status"]]


def pubchem_name(key, cache):
    """(name, source) for an InChIKey from PubChem, cached; (None, reason) when PubChem has no record."""
    if key not in cache:
        try:
            with urllib.request.urlopen(PUBCHEM.format(urllib.parse.quote(key)), timeout=30) as response:
                props = json.load(response)["PropertyTable"]["Properties"]
            cache[key] = {"cid": props[0]["CID"], "title": props[0].get("Title"), "iupac": props[0].get("IUPACName"),
                          "records": len(props), "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise
            cache[key] = {"cid": None, "title": None, "iupac": None, "records": 0, "http": 404,
                          "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        time.sleep(0.3)  # PubChem asks for at most five requests a second
    entry = cache[key]
    if entry["title"] and not PLACEHOLDER.match(entry["title"].strip()):
        return entry["title"].strip(), f"PubChem CID {entry['cid']} title"
    if entry["iupac"]:
        return entry["iupac"].strip(), f"PubChem CID {entry['cid']} IUPAC name"
    return None, "no PubChem record for the InChIKey"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chunk", help="cNN")
    parser.add_argument("--walltime", default="auto",
                        help="HH:MM:SS, or auto: 6 x the campaign fit for the chunk's largest molecule, 3 h to 16 days")
    parser.add_argument("--mem", default="4G")
    args = parser.parse_args()
    assert re.fullmatch(r"c\d\d", args.chunk), args.chunk
    number = int(args.chunk[1:])
    out = P / args.chunk / "manifest.json"
    assert not out.exists(), f"{out} exists; a chunk manifest is never rebuilt"
    rows = never_run()[(number - 1) * CHUNK:number * CHUNK]
    assert rows, f"{args.chunk}: no work-list rows left"
    policy = json.loads((P / "policy.json").read_text())
    cache = json.loads(NAMES.read_text()) if NAMES.exists() else {}
    molecules = []
    try:
        for index, r in enumerate(rows):
            mol = Chem.MolFromSmiles(r["smiles"])
            assert mol is not None and Chem.MolToInchiKey(mol) == r["inchikey"], r["inchikey"]
            assert not any(a.GetIsotope() for a in mol.GetAtoms()), r["inchikey"]  # D-ISO
            assert Chem.GetFormalCharge(mol) == 0, r["inchikey"]  # neutral singlet, "* xyz 0 1"
            atoms = Chem.AddHs(mol).GetNumAtoms()
            assert atoms == int(r["atoms_with_h"]), r["inchikey"]
            if r["name"]:
                name, source = r["name"], "PlastChem (work list)"
            else:
                name, source = pubchem_name(r["inchikey"], cache)
                if name is None:  # stays visible: the release step must resolve it before the row is named
                    name, source = r["inchikey"], "unresolved: " + source
            ids = [i for i in (r["served_entries_direct"].split(";") + r["served_entries_as_parent"].split(";")) if i]
            molecules.append({
                "name": name, "name_source": source, "smiles": r["smiles"], "plastchem_id": ";".join(ids),
                "cas": r["cas"], "inchikey": r["inchikey"], "molecular_weight_g_mol": r["g_mol"],
                "n_atoms_with_H": r["atoms_with_h"], "array_index": index, "atoms": atoms, "group": args.chunk,
                "tier": "coverage", "worklist_order": int(r["order"]), "kind": r["kind"], "elements": r["elements"],
                "served_entries_direct": r["served_entries_direct"], "served_entries_as_parent": r["served_entries_as_parent"],
                "entry_names": r["entry_names"], "plastchem_groups": r["plastchem_groups"],
                "families_existing": r["families_existing"], "families_proposed": r["families_proposed"]})
    finally:
        NAMES.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    walltime = args.walltime
    if walltime == "auto":  # a resource choice, recorded: short limits backfill; a task reaching it is resubmitted longer
        largest = max(m["atoms"] for m in molecules)
        hours = min(384, max(3, math.ceil(6 * math.exp(-0.687758) * largest ** 2.305719 * 1.113048 / 3600)))
        walltime = f"{hours}:00:00"
    manifest = {
        "campaign": "contam-coverage-milan-v1", "group": args.chunk, "name": f"contam-coverage-{args.chunk}",
        "walltime": walltime, "mem": args.mem, "policy": policy,
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "input": {"path": str(WORKLIST.relative_to(ROOT)), "sha256": WORKLIST_SHA256},
        "chunk_rule": f"never-run work-list rows {(number - 1) * CHUNK + 1}-{(number - 1) * CHUNK + len(rows)} in work-list "
                      f"order (orders {rows[0]['order']}-{rows[-1]['order']})",
        "molecules": molecules,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=1) + "\n")
    print(json.dumps({"chunk": args.chunk, "molecules": len(molecules), "orders": [rows[0]["order"], rows[-1]["order"]],
                      "g_mol": [rows[0]["g_mol"], rows[-1]["g_mol"]], "atoms_max": max(m["atoms"] for m in molecules),
                      "names_from_pubchem": sum(m["name_source"].startswith("PubChem") for m in molecules),
                      "names_unresolved": [m["inchikey"] for m in molecules if m["name_source"].startswith("unresolved")],
                      "manifest_sha256": sha256(out)}))


if __name__ == "__main__":
    sys.exit(main())
