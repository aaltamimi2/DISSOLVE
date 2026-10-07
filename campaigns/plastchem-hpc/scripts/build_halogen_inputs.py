"""Halogen tier input (owner 2026-10-06): PlastChem organics of C, H, N, O plus any of F, Cl, Br, I.

The census funnel is re-run on the pinned workbook export and must reproduce every pinned census file exactly before
anything is written (a mismatch is a stop). Then the halogen tier is the funnel's eligible structures with
elements within {C, H, N, O, F, Cl, Br, I}, at least one halogen, molecular weight at most 700 g/mol (the CHNO
tiers' limit), no isotope label (owner decision D-ISO), and none already in a tier.

Run with the campaign venv:  ~/.venvs/cosmo-logp/bin/python scripts/build_halogen_inputs.py [--write]"""
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
INPUTS = ROOT / "inputs"
EXPORT = INPUTS / "census/plastchem_db_v1.0_full_database_subset.csv"
KEEP = {"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Se", "Br", "I"}  # census keep-set
NON_METALS = {"H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar", "Ge", "As", "Se", "Br", "Kr",
              "Sb", "Te", "I", "Xe", "At", "Rn"}
HALOGENS = {"F", "Cl", "Br", "I"}
HALOGEN_TIER = {"C", "H", "N", "O"} | HALOGENS
PINNED = {  # file: (sha256, rows, unique InChIKeys)
    "plastchem_organics_orca_opencosmo_firstpass_audited_8065.csv":
        ("60f56f9faeb845a73ae3de89cb1b75eb1c9dcae7bb9f7f7da2bcbf635a05f8ad", 8065, 7792),
    "plastchem_organics_orca_opencosmo_firstpass.csv":
        ("a8e1ae169bbf8a5831a24bd8a83ba4a0e36cf69f3ce59e774fc4bd21aff92828", 6064, 5833),
    "plastchem_organics_orca_opencosmo_flagged_heteroatoms.csv":
        ("0270fc64a02c3d6ff426c67dab077ddbcf46feb52086f91505e8608a0741fc81", 1762, 1721),
    "plastchem_organics_orca_opencosmo_flagged_si_b.csv":
        ("050d79f70f81f1dd30aaf9c4a594ed2c033dc0022d9ab9588668a1bd39af3a7a", 239, 238),
    "plastchem_organics_orca_opencosmo_tier2_chno_mw500_700.csv":
        ("f9b513c9df4e0d9ca49c6870b958b805e0c84b77368098afb63c75422540ebfb", 270, 270),
}
OUT_CSV = INPUTS / "plastchem_organics_orca_opencosmo_halogen_mw700.csv"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def funnel(rows):
    """Eligible rows with their parsed molecule, and the drop count of each step, in census order."""
    drops = collections.Counter()
    eligible = []
    for row in rows:
        flag = lambda name: (row[name] or "").strip() == "1"  # noqa: E731
        if flag("inorganic_compounds") or flag("organometallics"):
            drops["inorganic_or_organometallic"] += 1
            continue
        if flag("UVCBs") or flag("polymers") or flag("mixtures"):
            drops["uvcb_polymer_mixture"] += 1
            continue
        smiles = (row["isomeric_smiles"] or "").strip() or (row["canonical_smiles"] or "").strip()
        if not smiles or smiles == "nan":
            drops["no_smiles"] += 1
            continue
        raw = Chem.MolFromSmiles(smiles, sanitize=False)
        symbols = {atom.GetSymbol() for atom in raw.GetAtoms()} if raw is not None else set()
        if symbols - NON_METALS:
            drops["metal_atom"] += 1
            continue
        if "." in smiles:
            drops["multi_fragment"] += 1
            continue
        if raw is not None and sum(atom.GetFormalCharge() for atom in raw.GetAtoms()) != 0:
            drops["net_charge"] += 1
            continue
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None and any(atom.GetNumRadicalElectrons() for atom in mol.GetAtoms()):
            drops["radical"] += 1
            continue
        if symbols - KEEP:
            drops["element_outside_keep_set"] += 1
            continue
        if mol is None:
            drops["unparsed"] += 1
            continue
        if "C" not in symbols:
            drops["no_carbon"] += 1
            continue
        name = next((row[k].strip() for k in ("pubchem_name", "iupac_name", "cas") if (row[k] or "").strip()
                     and row[k].strip() != "nan"), "")
        eligible.append({"row": row, "mol": mol, "smiles": smiles, "name": name, "elements": symbols,
                         "inchikey": row["inchikey"], "mw": float(row["molecular_weight"])})
    return eligible, drops


def unique(items):
    return len({item["inchikey"] for item in items})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the halogen tier CSV (after every check passes)")
    args = parser.parse_args()
    for name, (digest, rows, keys) in PINNED.items():
        path = INPUTS / name
        data = list(csv.DictReader(path.open()))
        assert sha256(path) == digest, f"{name}: digest changed"
        assert (len(data), len({r['inchikey'] for r in data})) == (rows, keys), name
    rows = list(csv.DictReader(EXPORT.open()))
    eligible, drops = funnel(rows)
    le500 = [e for e in eligible if e["mw"] <= 500]
    chno = [e for e in le500 if e["elements"] <= {"C", "H", "N", "O"}]
    sib = [e for e in le500 if e["elements"] & {"Si", "B"}]
    hetero = [e for e in le500 if not e["elements"] <= {"C", "H", "N", "O"} and not e["elements"] & {"Si", "B"}]
    tier2 = [e for e in eligible if 500 < e["mw"] <= 700 and e["elements"] <= {"C", "H", "N", "O"}]
    checks = {
        "eligible": ((len(eligible), unique(eligible)), (8961, 8661)),
        "mw_le_500": ((len(le500), unique(le500)), (8065, 7792)),
        "chno_le_500": ((len(chno), unique(chno)), (6064, 5833)),
        "heteroatoms_le_500": ((len(hetero), unique(hetero)), (1762, 1721)),
        "si_b_le_500": ((len(sib), unique(sib)), (239, 238)),
        "chno_500_700_unique": ((unique(tier2),), (None,)),
    }
    pinned_keys = {}
    for name in PINNED:
        for r in csv.DictReader((INPUTS / name).open()):
            pinned_keys.setdefault(r["inchikey"], set()).add(name)
    tier2_pinned = {r["inchikey"] for r in csv.DictReader((INPUTS / "plastchem_organics_orca_opencosmo_tier2_chno_mw500_700.csv").open())}
    tier2_new = {e["inchikey"] for e in tier2} - {k for k, files in pinned_keys.items()
                                                    if files - {"plastchem_organics_orca_opencosmo_tier2_chno_mw500_700.csv"}}
    report = {"drops": dict(drops), "checks": {k: {"got": v[0], "census": v[1]} for k, v in checks.items()},
              "tier2_reproduced": tier2_new == tier2_pinned}
    failed = [k for k, (got, want) in checks.items() if want[0] is not None and got != want]
    if failed or not report["tier2_reproduced"]:
        print(json.dumps(report, indent=1))
        sys.exit(f"census not reproduced: {failed or 'tier 2'}")
    halogen = [e for e in eligible if e["mw"] <= 700 and e["elements"] <= HALOGEN_TIER and e["elements"] & HALOGENS]
    isotope = [e for e in halogen if any(atom.GetIsotope() for atom in e["mol"].GetAtoms())]
    halogen = [e for e in halogen if e not in isotope]
    already = [e for e in halogen if e["inchikey"] in pinned_keys and pinned_keys[e["inchikey"]] - {
        "plastchem_organics_orca_opencosmo_firstpass_audited_8065.csv",
        "plastchem_organics_orca_opencosmo_flagged_heteroatoms.csv",
        "plastchem_organics_orca_opencosmo_firstpass_no_sib.csv"}]
    assert not already, f"{len(already)} halogen structures already in a submitted tier"
    by_key: dict[str, list] = {}
    for e in halogen:
        by_key.setdefault(e["inchikey"], []).append(e)
    out = []
    for key, items in sorted(by_key.items(), key=lambda kv: kv[1][0]["name"].casefold()):
        first = items[0]
        assert Chem.MolToInchiKey(first["mol"]) == key, key
        ids = sorted({i["row"]["plastchem_ID"] for i in items}, key=int)
        cas = sorted({(i["row"]["cas_fixed"] or i["row"]["cas"] or "").strip("'") for i in items} - {"", "nan"})
        mol_h = Chem.AddHs(first["mol"])
        out.append({"name": first["name"], "smiles": first["smiles"], "plastchem_id": ";".join(ids),
                    "cas": ";".join(cas), "inchikey": key, "molecular_weight_g_mol": first["row"]["molecular_weight"],
                    "halogens": ";".join(sorted(first["elements"] & HALOGENS)),
                    "n_atoms_with_H": mol_h.GetNumAtoms()})
    report.update(halogen_rows=len(halogen), halogen_unique=len(out), isotope_excluded=[e["name"] for e in isotope],
                  halogen_mw_le_500=sum(float(r["molecular_weight_g_mol"]) <= 500 for r in out),
                  halogen_mw_500_700=sum(float(r["molecular_weight_g_mol"]) > 500 for r in out),
                  by_halogen=dict(collections.Counter(r["halogens"] for r in out)))
    print(json.dumps(report, indent=1))
    if args.write:
        assert not OUT_CSV.exists(), f"{OUT_CSV} is pinned; never overwrite it"
        with OUT_CSV.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(out[0]))
            writer.writeheader()
            writer.writerows(out)
        OUT_CSV.chmod(0o444)
        print(OUT_CSV, sha256(OUT_CSV))


if __name__ == "__main__":
    main()
