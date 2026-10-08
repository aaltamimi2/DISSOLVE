"""A-12: the structures behind the publication sets of Zhou et al., Green Chem. 2026 (d5gc06059a), so that every
compound of the paper has its own ORCA/openCOSMO surface (owner, 2026-10-07: "make the numbers match exactly. i am
pretty sure the salts didnt have the counter cation (just treated the PFAS as negatively charged)").

- PFAS (ESI Table S7, 26): each as its anion, no counter-cation. The paper's own values show the species: ammonium
  GenX (25) equals the free acid TFPA (8) to 1e-8 and the two ADONA salts (24, 26) are identical, so acids and salts
  were one species, the anion. Two anions are therefore shared: 8 with 25, 24 with 26 (24 distinct structures).
- BFR (Case Study 1, 4): tri-PBDE (BDE-28) and gamma-HBCD come from the release; DECA and TBBPA-dbP (over the
  campaign's 700 g/mol limit) are computed here, neutral.
- Phthalates (ESI Table S5, 8): all in the release, neutral.

Structures come from PubChem by CAS number (cached in inputs/publication_pubchem_zhou2026.json; PubChem is asked only
when the cache lacks a CAS) and are checked against PlastChem's structure where PlastChem has the compound.

    ~/.venvs/cosmo-logp/bin/python scripts/build_publication_inputs.py      (writes the manifest and the table)"""
import csv
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

from rdkit import Chem
from rdkit.Chem.Descriptors import MolWt
from rdkit.Chem.rdMolDescriptors import CalcMolFormula

ROOT = Path(__file__).resolve().parents[1]
SETS = ROOT / "inputs/publication_sets_zhou2026.json"
CACHE = ROOT / "inputs/publication_pubchem_zhou2026.json"
TABLE = ROOT / "inputs/publication_structures_zhou2026.csv"
WORKBOOK = ROOT / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"
STATE = ROOT / "state/publication-v1"
# The paper reports one HBCD value and does not name the stereoisomer; gamma-HBCD is the main component of technical
# HBCD. The release computed it (and nine other stereoisomers).
HBCD = "DEIGXXQKDWULML-MOCCIAMBSA-N"
PUBCHEM = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/{}/property/SMILES,MolecularFormula,Charge/JSON"


def pubchem(cas, cache):
    if cas not in cache:
        with urllib.request.urlopen(PUBCHEM.format(cas), timeout=30) as response:
            props = json.load(response)["PropertyTable"]["Properties"]
        cache[cas] = {"cid": props[0]["CID"], "smiles": props[0].get("SMILES") or props[0]["IsomericSMILES"],
                      "formula": props[0]["MolecularFormula"], "charge": props[0]["Charge"],
                      "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        time.sleep(0.3)  # PubChem asks for at most five requests a second
    return cache[cas]


def anion(smiles):
    """The PFAS part of a salt or acid, deprotonated: counter-ions dropped, the carboxylic or sulfonic acid OH made
    O-. Returns (SMILES, how)."""
    fragments = Chem.GetMolFrags(Chem.MolFromSmiles(smiles), asMols=True)
    mol = max(fragments, key=lambda m: sum(a.GetSymbol() == "F" for a in m.GetAtoms()))
    if Chem.GetFormalCharge(mol) == -1:
        return Chem.MolToSmiles(mol), "anion as registered" if len(fragments) == 1 else "counter-ion dropped"
    acid = Chem.MolFromSmarts("[OX2H1][$([CX3]=O),$([SX4](=O)=O)]")
    hits = mol.GetSubstructMatches(acid)
    assert len(hits) == 1, (smiles, len(hits))
    editable = Chem.RWMol(mol)
    oxygen = editable.GetAtomWithIdx(hits[0][0])
    oxygen.SetFormalCharge(-1)
    oxygen.SetNumExplicitHs(0)
    oxygen.SetNoImplicit(True)
    out = editable.GetMol()
    Chem.SanitizeMol(out)
    return Chem.MolToSmiles(out), "acid deprotonated"


def acid(smiles):
    """The PFAS part of a salt or anion as its neutral parent acid: counter-ions dropped, the anionic O protonated.
    Returns (SMILES, how)."""
    fragments = Chem.GetMolFrags(Chem.MolFromSmiles(smiles), asMols=True)
    mol = max(fragments, key=lambda m: sum(a.GetSymbol() == "F" for a in m.GetAtoms()))
    if Chem.GetFormalCharge(mol) == 0:
        return Chem.MolToSmiles(mol), "acid as registered" if len(fragments) == 1 else "counter-ion dropped"
    editable = Chem.RWMol(mol)
    oxygens = [a for a in editable.GetAtoms() if a.GetSymbol() == "O" and a.GetFormalCharge() == -1]
    assert len(oxygens) == 1, smiles
    oxygens[0].SetFormalCharge(0)
    oxygens[0].SetNumExplicitHs(1)
    out = editable.GetMol()
    Chem.SanitizeMol(out)
    return Chem.MolToSmiles(out), "anion protonated" + ("" if len(fragments) == 1 else ", counter-ion dropped")


def release_keys():
    """InChIKeys the release computed (their neutral surfaces and thermodynamics exist)."""
    import gzip
    with gzip.open("/mnt/r/plastchem-euler/promotion-v2/contaminants.csv.gz", "rt") as handle:
        return {r["input_inchikey"] for r in csv.DictReader(handle) if r["campaign_status_at_snapshot"] == "converged"
                and r["partition_predicted_rows"] not in ("", "0")}


def main_neutral():
    """A-12 correction: the paper's PFAS as neutral acids (its values match the neutral acids, r = 0.96, and not the
    anions). Writes state/publication-v1/neutral/manifest.json with every distinct neutral PFAS acid the release
    has not computed; salts map to their parent acids."""
    sets = json.loads(SETS.read_text())["sets"]
    cache = json.loads(CACHE.read_text())
    computed = release_keys()
    molecules, rows = {}, []
    for label, name, cas, *_ in sets["PFAS"]:
        record = pubchem(cas.split(";")[0], cache)
        smiles, how = acid(record["smiles"])
        mol = Chem.MolFromSmiles(smiles)
        key = Chem.MolToInchiKey(mol)
        assert Chem.GetFormalCharge(mol) == 0
        where = "release (promotion-v2)" if key in computed else "publication tier (A-12, neutral)"
        rows.append({"set": "PFAS", "label": label, "name": name, "cas": cas, "species": "neutral acid", "charge": 0,
                     "smiles": smiles, "inchikey": key, "formula": CalcMolFormula(mol),
                     "molecular_weight_g_mol": round(MolWt(mol), 2), "computed_by": where,
                     "source": f"PubChem CID {record['cid']} ({how})"})
        if key in computed:
            continue
        entry = molecules.setdefault(key, {
            "name": name, "smiles": smiles, "cas": cas, "inchikey": key, "charge": 0,
            "molecular_weight_g_mol": f"{MolWt(mol):.2f}", "atoms": Chem.AddHs(mol).GetNumAtoms(), "labels": [],
            "set": "PFAS", "species": "neutral", "group": "neutral", "tier": "PUBLICATION_ZHOU2026_NEUTRAL",
            "plastchem_id": ""})
        entry["labels"].append(label)
    ordered = sorted(molecules.values(), key=lambda m: m["atoms"])
    for index, m in enumerate(ordered):
        m["array_index"] = index
    manifest = {
        "campaign": "contam-publication-neutral-milan-v1", "group": "neutral", "name": "contam-publication-n1",
        "concurrency": len(ordered), "walltime": "72:00:00",
        "policy": {"mode": "connectivity_first_block_with_computed_stereochemistry",
                   "authority": "Owner decision A-3 D-IDENT", "charge": "from the manifest (A-12)",
                   "tier_authority": "Amendment A-12 (neutral correction)", "campaign_concurrency_limit": 64},
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "input": {"path": str(SETS.relative_to(ROOT)), "sha256": hashlib.sha256(SETS.read_bytes()).hexdigest(),
                  "structures": str(CACHE.relative_to(ROOT))},
        "molecules": ordered,
    }
    (STATE / "neutral").mkdir(parents=True, exist_ok=True)
    (STATE / "neutral/manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    out = ROOT / "inputs/publication_structures_zhou2026_neutral.csv"
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"26 PFAS as neutral acids: {sum(r['computed_by'].startswith('release') for r in rows)} from the release, "
          f"{len(ordered)} new structures to compute")
    for m in ordered:
        print(f"  {m['array_index']:2} {'/'.join(m['labels']):18} {m['atoms']:3} atoms {m['molecular_weight_g_mol']:>7} {m['smiles']}")


def main():
    sets = json.loads(SETS.read_text())["sets"]
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    workbook = list(csv.DictReader(WORKBOOK.open()))
    rows, molecules = [], {}
    for set_name in ("PFAS", "BFR", "Phthalates"):
        for item in sets[set_name]:
            label, name, cas = item[:3]
            first_cas = cas.split(";")[0]
            if set_name == "PFAS":
                record = pubchem(first_cas, cache)
                smiles, how = anion(record["smiles"])
                species, charge = "anion", -1
                plastchem = [w for w in workbook if first_cas in w["cas"].split(";")]
                if plastchem:  # the same compound as PlastChem's, apart from protonation and counter-ions
                    ours = Chem.MolToInchiKey(Chem.MolFromSmiles(smiles)).split("-")[0]
                    theirs = Chem.MolToInchiKey(Chem.MolFromSmiles(anion(
                        plastchem[0]["isomeric_smiles"] or plastchem[0]["canonical_smiles"])[0])).split("-")[0]
                    assert ours == theirs, (label, ours, theirs)
                source = f"PubChem CID {record['cid']} ({how})"
            elif label in ("DECA", "TBBPA-dbp"):
                record = pubchem(first_cas, cache)
                smiles, species, charge = Chem.MolToSmiles(Chem.MolFromSmiles(record["smiles"])), "neutral", 0
                source = f"PubChem CID {record['cid']}"
            else:
                smiles = species = charge = source = None  # taken from the release (see below)
            row = {"set": set_name, "label": label, "name": name, "cas": cas, "species": species, "charge": charge,
                   "smiles": smiles, "source": source}
            if smiles:
                mol = Chem.MolFromSmiles(smiles)
                key = Chem.MolToInchiKey(mol)
                assert Chem.GetFormalCharge(mol) == charge
                row.update(inchikey=key, formula=CalcMolFormula(mol), molecular_weight_g_mol=round(MolWt(mol), 2),
                           computed_by="publication-v1")
                entry = molecules.setdefault(key, {
                    "name": name, "smiles": smiles, "cas": cas, "inchikey": key, "charge": charge,
                    "molecular_weight_g_mol": f"{MolWt(mol):.2f}", "atoms": Chem.AddHs(mol).GetNumAtoms(),
                    "labels": [], "set": set_name, "species": species, "group": "publication",
                    "tier": "PUBLICATION_ZHOU2026", "plastchem_id": ""})
                entry["labels"].append(label)
            rows.append(row)
    for row in rows:  # the paper's compounds the release already computed (neutral BFRs and phthalates)
        if row["smiles"] is None:
            row.update(species="neutral", charge=0, computed_by="release (promotion-v2)",
                       source="PlastChem via the release" + (" (gamma-HBCD, see the header)" if row["label"] == "HBCD"
                                                              else ""))
    CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n")
    ordered = sorted(molecules.values(), key=lambda m: (m["set"] != "PFAS", m["atoms"]))
    for index, m in enumerate(ordered):
        m["array_index"] = index
    manifest = {
        "campaign": "contam-publication-milan-v1", "group": "publication", "name": "contam-publication-v1",
        "concurrency": 26, "walltime": "72:00:00",
        "policy": {"mode": "connectivity_first_block_with_computed_stereochemistry",
                   "authority": "Owner decision A-3 D-IDENT", "charge": "from the manifest (A-12)",
                   "tier_authority": "Amendment A-12", "campaign_concurrency_limit": 64},
        "recipe": {"n_embed": 300, "seed": 12345, "prune_rms": 0.5, "max_mmff_iters": 2000, "dft_conformers": 1},
        "input": {"path": str(SETS.relative_to(ROOT)), "sha256": hashlib.sha256(SETS.read_bytes()).hexdigest(),
                  "structures": str(CACHE.relative_to(ROOT))},
        "molecules": ordered,
    }
    (STATE / "publication").mkdir(parents=True, exist_ok=True)
    (STATE / "publication/manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    with TABLE.open("w", newline="") as handle:
        fields = ["set", "label", "name", "cas", "species", "charge", "smiles", "inchikey", "formula",
                  "molecular_weight_g_mol", "computed_by", "source"]
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} paper compounds; {len(ordered)} new structures to compute "
          f"({sum(m['charge'] == -1 for m in ordered)} anions, {sum(m['charge'] == 0 for m in ordered)} neutral)")
    for m in ordered:
        print(f"  {m['array_index']:2} {'/'.join(m['labels']):22} {m['charge']:+d} {m['atoms']:3} atoms "
              f"{m['molecular_weight_g_mol']:>7} {m['smiles']}")


if __name__ == "__main__":
    sys.exit(main_neutral() if "--neutral" in sys.argv else main())
