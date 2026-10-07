"""Collaborator layout of the v2 archives (owner, 2026-10-07): "reorganize the orca/cosmo contaminants into the PFAS
publication set/ BFR publication set, Phthalate publication set and then organize the plastchem into their families
(that they get categorized into on the actual agent engine)"; for compounds in no family, "one no family folder or try
to come up with a family for them".

Every computed contaminant goes in exactly one folder under contaminants/, the first of these that applies:

1. publication-sets/<set>: the PFAS (ESI Table S7), phthalates (Table S5) and brominated flame retardants (Case
   Study 1) of Zhou et al., Green Chem. 2026 (inputs/publication_sets_zhou2026.json), matched by CAS number or by
   InChIKey (a full key, or a first block for every stereoisomer of HBCD).
2. agent-families/<family>: the families DISSOLVE's agent uses (src/dissolve/data/plastchem_families.json). A compound
   in several goes in the smallest of them (the most specific: a benzophenone UV absorber under Benzophenones).
3. plastchem-groups/<group>: PlastChem's own structural groups (workbook columns), most specific first (GROUP_ORDER).
4. structure-classes/<class>: polybrominated_diphenyl_ethers for the PBDE congeners (C12H(10-n)Br(n)O diphenyl
   ethers: PlastChem marks them as grouped but sets no group column for them, and its PBDEs column flags
   brominated biphenyls), else the halogen it carries (fluorinated, chlorinated, brominated, iodinated, several
   halogens), else the first matching functional group of DISSOLVE's structure search (CLASS_ORDER), else
   aliphatic_hydrocarbons for a compound of carbon and hydrogen only.
5. no-family: none of the above.

CLASSIFICATION.tsv lists every membership of every compound, not only the folder it went to.

    python3 scripts/export-v2/classify_contaminants.py /mnt/r/plastchem-euler/promotion-v2   (dry run: counts)"""
import collections
import csv
import gzip
import json
import os
import re
import sys
from pathlib import Path

LANE = Path(__file__).resolve().parents[2]
DISSOLVE = Path(os.environ.get("DISSOLVE_SRC", Path.home() / "dissolve-contaminant-always-on/src"))
PUBLICATION_SETS = LANE / "inputs/publication_sets_zhou2026.json"
FAMILIES = DISSOLVE / "dissolve/data/plastchem_families.json"
GROUPS = LANE / "inputs/census/plastchem_db_v1.0_groups_functions.csv"
WORKBOOK = LANE / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"

# PlastChem's groups, most specific first; the substance-type flags are not families.
GROUP_ORDER = [
    "PFASs", "PBDEs", "PCBs", "polychlorinated_naphthalenes", "PBDD_PBDF_PCDD_PCDF", "dioxins", "DDT_DDE_DDD",
    "chlorinated_paraffins", "organophosphates", "azodyes", "diazo_amino_hydroxyl_naphthalenedisulfonic_acid_dyes",
    "benzotriazoles", "benzothiazole", "phenolic_antioxidants", "acetophenones_benzophenones", "orthophthalates",
    "isophthalates_terephthalates_trimellitates", "bisphenols", "alkylphenols", "parabens", "salicylate_esters",
    "salicyclic_acid", "aromatic_amines", "aromatic_ethers", "aralkyladehydes", "dibenzoyl_peroxide_derivatives",
    "dihydropurinediones", "pyrazoles", "silanes_siloxanes_silicones", "aliphatic_primary_amides", "alkyl_nitrates",
    "carboxylic_acids_salts", "ethanediols", "cyclic_acetals", "cyclic_ethers", "dialiphatic_ethers_excluding_unsatured",
    "alkane_ethers", "aliphatic_ketones", "ketones_simple", "aldehydes_simple", "aromatic_hydrocarbons", "alkynes",
    "alkenes", "alkanes",
]
NOT_FAMILIES = {"UVCBs", "polymers", "mixtures", "inorganic_compounds", "organometallics"}
HALOGEN_CLASSES = {"F": "fluorinated", "Cl": "chlorinated", "Br": "brominated", "I": "iodinated"}
# DISSOLVE structure-search groups: reactive groups, then nitrogen and carbonyl derivatives, then phenols and amines,
# then acids, esters, aldehydes, ketones, alcohols and ethers, then the carbon skeleton.
CLASS_ORDER = [
    "isocyanate", "epoxide", "acrylate", "anhydride", "azo", "nitro", "nitrile", "quinone", "benzotriazole",
    "benzophenone", "imide", "urea or carbamate", "carbonate ester", "lactone", "phthalate ester", "hindered phenol",
    "phenol", "aromatic amine", "amide", "amine", "carboxylic acid", "ester", "aldehyde", "ketone", "alcohol", "ether",
    "fused aromatic rings", "aromatic ring", "long alkyl chain",
]
FIELDS = ["inchikey", "name", "path", "assigned_by", "publication_set", "agent_families", "plastchem_groups",
          "plastchem_functions", "halogens", "functional_groups", "plastchem_ids", "cas"]


def slug(text):
    return re.sub(r"\s+", "_", text.strip())


def split(value):
    return [part.strip() for part in re.split(r"[;,]", value or "") if part.strip()]


def plastchem_ids(row):
    return [i.split(".")[0] for i in split(row["plastchem_id"])]


def load_inputs():
    sys.path.insert(0, str(DISSOLVE))
    from dissolve.contaminant_search import FUNCTIONAL_GROUPS, structure_of  # the engine's own definitions

    assert set(CLASS_ORDER) == set(FUNCTIONAL_GROUPS), set(CLASS_ORDER) ^ set(FUNCTIONAL_GROUPS)
    groups = list(csv.DictReader(GROUPS.open()))
    columns = list(groups[0])[6:]
    assert set(columns) == set(GROUP_ORDER) | NOT_FAMILIES, set(columns) ^ (set(GROUP_ORDER) | NOT_FAMILIES)
    families = json.loads(FAMILIES.read_text())["families"]
    return structure_of, groups, families, json.loads(PUBLICATION_SETS.read_text())


def publication_matches(rows, sets):
    """{inchikey: (set, label, name, cas)} over the given release rows, and per item the rows it matched."""
    found, per_item = {}, {}
    for set_name, items in sets.items():
        for item in items:
            label, name, cas = item[:3]
            key = item[3] if len(item) > 3 else None
            wanted = set(split(cas))
            hits = [r for r in rows if wanted & set(split(r["cas"])) or key in (r["input_inchikey"],
                                                                                r["input_inchikey"].split("-")[0])]
            per_item[(set_name, label)] = hits
            for r in hits:
                assert r["input_inchikey"] not in found, (r["input_inchikey"], label)
                found[r["input_inchikey"]] = (set_name, label, name, cas)
    return found, per_item


def is_pbde(smiles):
    """A PBDE congener: two benzene rings joined by an open-chain ether oxygen, carrying 1-10 bromines and nothing
    else (C12H(10-n)Br(n)O)."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        return False
    atoms = collections.Counter(atom.GetSymbol() for atom in mol.GetAtoms())
    return (set(atoms) <= {"C", "O", "Br"} and atoms["C"] == 12 and atoms["O"] == 1 and atoms["Br"] >= 1
            and mol.HasSubstructMatch(Chem.MolFromSmarts("c1ccccc1-[OX2;!R]-c1ccccc1")))


def classify(computed, all_rows):
    """One record per computed release row (FIELDS), keyed by InChIKey; 'path' is the folder under contaminants/."""
    structure_of, groups, families, publication = load_inputs()
    by_id = {r["plastchem_ID"]: r for r in groups}
    by_key = collections.defaultdict(list)  # PlastChem lists some structures once per CAS number, as the engine reads
    for entry in groups:
        by_key[entry["inchikey"]].append(entry)
    published, _ = publication_matches(all_rows, publication["sets"])
    sizes = {f["name"]: len(f["members"]) for f in families}
    order = [f["name"] for f in families]
    member_of = collections.defaultdict(list)
    for family in families:
        for key in family["members"]:
            member_of[key].append(family["name"])
    out = {}
    for r in computed:
        key = r["input_inchikey"]
        # every PlastChem entry of this structure (by ID or InChIKey) that is one substance: a UVCB, polymer or
        # mixture entry represented by this structure (poly(chlorotrifluoroethylene) by its monomer) has its own groups
        entries = [e for e in {e["plastchem_ID"]: e for e in [*(by_id[i] for i in plastchem_ids(r) if i in by_id),
                                                              *by_key.get(key, [])]}.values()
                   if all(e[flag] in ("", "0", "nan") for flag in NOT_FAMILIES)]
        in_groups = [g for g in GROUP_ORDER if any(e[g] not in ("", "0", "nan") for e in entries)]
        functions = sorted({f for e in entries for f in split(e["Harmonized_functions"])})
        fams = sorted(member_of.get(key, []), key=lambda name: (sizes[name], order.index(name)))
        elements, matched = structure_of(r["smiles"]) or (frozenset(), frozenset())
        halogens = [h for h in HALOGEN_CLASSES if h in elements]
        matched = [g for g in CLASS_ORDER if g in matched]
        if key in published:
            path, how = f"publication-sets/{published[key][0]}", "publication set (Zhou et al. 2026)"
        elif fams:
            path, how = f"agent-families/{slug(fams[0])}", "DISSOLVE agent family"
        elif in_groups:
            path, how = f"plastchem-groups/{in_groups[0]}", "PlastChem group"
        elif is_pbde(r["smiles"]):
            path, how = "structure-classes/polybrominated_diphenyl_ethers", "PBDE congener (C12H(10-n)Br(n)O)"
        elif halogens:
            path = "structure-classes/" + (HALOGEN_CLASSES[halogens[0]] if len(halogens) == 1 else "several_halogens")
            how = "halogen content"
        elif matched:
            path, how = f"structure-classes/{slug(matched[0])}", "DISSOLVE structure-search group"
        elif elements and elements <= {"C", "H"}:  # left after the aromatic and long-chain classes
            path, how = "structure-classes/aliphatic_hydrocarbons", "DISSOLVE structure-search elements (C and H only)"
        else:
            path, how = "no-family", "none"
        pub = published.get(key)
        out[key] = {
            "inchikey": key, "name": r["name"], "path": path, "assigned_by": how,
            "publication_set": f"{pub[0]}: {pub[1]} ({pub[2]}, CAS {pub[3]})" if pub else "",
            "agent_families": "; ".join(fams), "plastchem_groups": "; ".join(in_groups),
            "plastchem_functions": "; ".join(functions), "halogens": " ".join(halogens),
            "functional_groups": "; ".join(matched), "plastchem_ids": ";".join(plastchem_ids(r)), "cas": r["cas"],
        }
    return out


def publication_table(all_rows, surfaces):
    """One row per publication-set item: its surfaces in the archive (surfaces: inchikey -> path), or why it has
    none (its release status, else its PlastChem entry, else that PlastChem lacks it)."""
    sys.path.insert(0, str(DISSOLVE))
    from dissolve.plastchem_release import _outside_reason
    from rdkit import Chem

    publication = json.loads(PUBLICATION_SETS.read_text())
    _, per_item = publication_matches(all_rows, publication["sets"])
    workbook = list(csv.DictReader(WORKBOOK.open()))
    held = frozenset({"C", "H", "N", "O", "F", "Cl", "Br", "I"})  # the elements promotion-v2 holds
    status = {"thermodynamics_pending": "its ORCA surface converged after the release was frozen; its partition "
                                        "and LLE calculations were not yet run",
              "running": "its ORCA calculation was still running when the release was frozen"}
    out = []
    for set_name, items in publication["sets"].items():
        for item in items:
            label, name, cas = item[:3]
            hits = per_item[(set_name, label)]
            paths = sorted(surfaces[r["input_inchikey"]] for r in hits if r["input_inchikey"] in surfaces)
            reason = ""
            if not paths and hits:
                r = hits[0]
                reason = status.get(r["campaign_status_at_snapshot"]) or r["exclusion_reason"] or r["failure_mode"] \
                    or r["campaign_status_at_snapshot"]
            elif not paths:
                entries = [w for w in workbook if set(split(cas)) & set(split(w["cas"]))]
                if not entries:
                    reason = "not in PlastChem"
                else:
                    w = dict(entries[0])
                    w.update({k: w.get(k) not in (None, "", "0", "nan") for k in
                              ("inorganic_compounds", "organometallics", "UVCBs", "polymers", "mixtures")})
                    w["smiles"] = next((v for v in (w.get("isomeric_smiles"), w.get("canonical_smiles"))
                                        if v and v != "nan"), "")
                    mol = Chem.MolFromSmiles(w["smiles"]) if w["smiles"] and "." not in w["smiles"] else None
                    reason = _outside_reason(w, mol, held)
            out.append({"set": set_name, "label": label, "name": name, "cas": cas, "surfaces": "; ".join(paths),
                        "why_no_surface": reason})
    return out


def release_rows(release):
    with gzip.open(Path(release) / "contaminants.csv.gz", "rt") as handle:
        rows = list(csv.DictReader(handle))
    computed = [r for r in rows if r["campaign_status_at_snapshot"] == "converged"
                and r["partition_predicted_rows"] not in ("", "0")]
    return rows, computed


if __name__ == "__main__":
    rows, computed = release_rows(sys.argv[1])
    records = classify(computed, rows)
    tops = collections.Counter(rec["path"].split("/")[0] for rec in records.values())
    print("computed:", len(records), dict(tops))
    for path, n in sorted(collections.Counter(rec["path"] for rec in records.values()).items()):
        print(f"  {path}: {n}")
    for item in publication_table(rows, {key: rec["path"] for key, rec in records.items()}):
        print(item["set"], item["label"], "|", item["surfaces"].count(";") + 1 if item["surfaces"] else 0, "|",
              item["why_no_surface"])
