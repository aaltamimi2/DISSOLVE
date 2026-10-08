"""A-13 coverage campaign input (owner, 2026-10-08: hit 95%+ of the simulable PlastChem entries, lowest to highest
molecular weight). Writes the pinned, read-only work list and the parent-alias list from the coverage triage.

inputs/plastchem_coverage_a13_worklist.csv     one row per structure to compute, in the owner's order (molecular
                                               weight ascending, then atoms, then InChIKey), with the PlastChem
                                               entries it serves, the release status of structures the release
                                               lists, and the families it would join
inputs/plastchem_coverage_a13_parent_aliases.csv  one row per PlastChem entry served through its computed neutral
                                               parent (salts, ions, hydrates): it needs only an alias

Families: DISSOLVE's own family specs (src/dissolve/plastchem_release.py, _FAMILY_SPECS and _SMARTS), applied to the
molecule computed (the neutral parent for a salt) and to the PlastChem group flags of every entry it serves, plus the
group-based families A-13 proposes for compounds the campaign never held (PROPOSED below).

    python3 scripts/build_coverage_worklist.py COVERAGE_TSV [--write]
"""
import argparse
import collections
import csv
import hashlib
import json
import sys
from pathlib import Path

from rdkit import Chem, RDLogger

RDLogger.DisableLog("rdApp.*")
ROOT = Path(__file__).resolve().parents[1]
DISSOLVE_SRC = Path.home() / "dissolve-main-cleanup/src"
sys.path.insert(0, str(DISSOLVE_SRC))
from dissolve.plastchem_release import _FAMILY_SPECS, _SMARTS  # noqa: E402

CENSUS = ROOT / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"
GROUPS = ROOT / "inputs/census/plastchem_db_v1.0_groups_functions.csv"
WORKLIST = ROOT / "inputs/plastchem_coverage_a13_worklist.csv"
ALIASES = ROOT / "inputs/plastchem_coverage_a13_parent_aliases.csv"
#: Families A-13 proposes, each a PlastChem group (the basis most existing families use). Add one to _FAMILY_SPECS only
#: once three of its members are computed and serve as its examples (the family builder refuses other examples).
PROPOSED = {"Organophosphates": "organophosphates", "Siloxanes and silanes": "silanes_siloxanes_silicones",
            "Benzothiazoles": "benzothiazole", "Azo dyes": "azodyes", "PFAS": "PFASs"}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def families_of(mol, flags):
    """The existing families (DISSOLVE's specs) and the proposed ones a structure would join."""
    patterns = {name: Chem.MolFromSmarts(smarts) for name, smarts in _SMARTS.items()}
    hits = lambda names: [mol.HasSubstructMatch(patterns[name]) for name in names]  # noqa: E731
    existing = []
    for spec in _FAMILY_SPECS:
        if "group" in spec and spec["group"] not in flags and not any(hits(spec.get("or_any", ()))):
            continue
        if "any" in spec and not any(hits(spec["any"])):
            continue
        if "all" in spec and not all(hits(spec["all"])):
            continue
        if "none" in spec and any(hits(spec["none"])):
            continue
        if "min_carbons" in spec and sum(a.GetSymbol() == "C" for a in mol.GetAtoms()) < spec["min_carbons"]:
            continue
        existing.append(spec["name"])
    proposed = [name for name, group in PROPOSED.items() if group in flags]
    return existing, proposed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_tsv")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    census = {r["plastchem_ID"]: r for r in csv.DictReader(CENSUS.open())}
    group_columns = None
    flags = {}
    for row in csv.DictReader(GROUPS.open()):
        if group_columns is None:
            group_columns = [c for c in row if c not in ("plastchem_ID", "inchikey", "cas", "pubchem_name", "iupac_name",
                                                         "Harmonized_functions", "UVCBs", "polymers", "mixtures",
                                                         "inorganic_compounds", "organometallics")]
        flags[row["plastchem_ID"]] = {c for c in group_columns if (row[c] or "").strip() not in ("", "0")}
    rows = list(csv.DictReader(open(args.coverage_tsv), delimiter="\t"))
    structures = collections.OrderedDict()
    aliases = []
    for r in rows:
        if r["bucket"] == "served as its parent":
            aliases.append({"plastchem_id": r["plastchem_id"], "name": r["name"], "cas": r["cas"],
                            "form": r["reason"].removeprefix("parent of a ").removeprefix("parent of an "),
                            "parent_inchikey": r["species_inchikey"], "parent_smiles": r["species_smiles"]})
        if r["bucket"] != "to compute":
            continue
        s = structures.setdefault(r["species_inchikey"], {"rows": [], "smiles": r["species_smiles"],
                                                          "g_mol": float(r["species_g_mol"]),
                                                          "atoms": int(r["atoms_with_h"]), "kind": r["reason"],
                                                          "status": r["release_status"]})
        s["rows"].append(r)
    out = []
    for key, s in structures.items():
        mol = Chem.MolFromSmiles(s["smiles"])
        assert Chem.MolToInchiKey(mol) == key, key
        assert not any(a.GetFormalCharge() for a in mol.GetAtoms()) or sum(a.GetFormalCharge() for a in mol.GetAtoms()) == 0
        group_flags = set().union(*(flags.get(r["plastchem_id"], set()) for r in s["rows"]))
        existing, proposed = families_of(mol, group_flags)
        direct = [r for r in s["rows"] if not r["detail"].startswith("parent of")]
        salts = [r for r in s["rows"] if r["detail"].startswith("parent of")]
        name = direct[0]["name"] if direct else ""
        out.append({
            "inchikey": key, "smiles": s["smiles"], "g_mol": f"{s['g_mol']:.2f}", "atoms_with_h": s["atoms"],
            "elements": "".join(sorted({a.GetSymbol() for a in Chem.AddHs(mol).GetAtoms()},
                                       key=lambda e: ("CHNOFClBrISPSi".find(e), e))),
            "kind": s["kind"], "above_700_g_mol": "yes" if s["g_mol"] > 700 else "",
            "release_status": s["status"], "entries": len(s["rows"]),
            "name": name, "name_needed": "" if name else "yes: the neutral parent's PubChem name by InChIKey",
            "served_entries_direct": ";".join(r["plastchem_id"] for r in direct),
            "served_entries_as_parent": ";".join(r["plastchem_id"] for r in salts),
            "entry_names": " | ".join(r["name"] for r in s["rows"][:6]),
            "cas": ";".join(sorted({r["cas"] for r in s["rows"] if r["cas"] and r["cas"] != "nan"})),
            "plastchem_groups": ";".join(sorted(group_flags)),
            "families_existing": ";".join(existing), "families_proposed": ";".join(proposed),
        })
    out.sort(key=lambda r: (float(r["g_mol"]), int(r["atoms_with_h"]), r["inchikey"]))
    for i, r in enumerate(out, 1):
        r["order"] = i
    columns = ["order", *[c for c in out[0] if c != "order"]]
    summary = {
        "structures": len(out), "entries": sum(r["entries"] for r in out), "parent_aliases": len(aliases),
        "by_kind": dict(collections.Counter(r["kind"] for r in out).most_common()),
        "by_release_status": dict(collections.Counter(r["release_status"] or "never run" for r in out).most_common()),
        "names_needed": sum(bool(r["name_needed"]) for r in out),
        "families_existing": dict(collections.Counter(f for r in out for f in r["families_existing"].split(";") if f).most_common()),
        "families_proposed": dict(collections.Counter(f for r in out for f in r["families_proposed"].split(";") if f).most_common()),
        "no_family": sum(not r["families_existing"] and not r["families_proposed"] for r in out),
        "g_mol_range": [out[0]["g_mol"], out[-1]["g_mol"]],
    }
    print(json.dumps(summary, indent=1))
    if args.write:
        for path, data, fields in ((WORKLIST, out, columns), (ALIASES, aliases, list(aliases[0]))):
            assert not path.exists(), f"{path} is pinned; never overwrite it"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(data)
            path.chmod(0o444)
            print(path, sha256(path))


if __name__ == "__main__":
    main()
