"""Coverage of PlastChem by the served openCOSMO-RS release (owner, 2026-10-08: "decide what we literally cant (no
structure) or shouldnt simulate at all. then of the ones that can be simulated we want to hit 95% coverage").

Every entry of PlastChem v1.0 (the pinned census export of its "Full database" sheet, 17,932 entries) gets one bucket,
by the first rule that applies, in this order:

served                the release computed it: the release lists its PlastChem ID, or (when PlastChem does not flag it a
                      UVCB, polymer or mixture) its InChIKey or the InChIKey its SMILES gives
cannot                no single molecule: PlastChem flags it a UVCB, polymer or mixture (such entries often carry a
                      monomer's structure), it has no SMILES, its SMILES does not parse, or its SMILES lists several
                      molecules (different organic molecules; copies of one neutral molecule; an organic molecule with
                      a neutral partner that is not a counter-ion, such as hydrogen peroxide or iodine; an organic
                      molecule without nitrogen beside a neutral mineral acid, i.e. an ester written as its reactants)
should not            judged on the species that would be computed: the entry's organic molecule, neutralized (a salt
                      or ion becomes its parent acid or base, as Zhou et al. 2026 modelled their PFAS salts, and water
                      of hydration is dropped). In order:
                      inorganic          PlastChem's inorganic flag, or no organic molecule
                      metal              PlastChem's organometallic flag, a metal bonded in the organic molecule, a
                                         salt of a metal other than Li, Na, K, Rb and Cs (metal soaps such as zinc
                                         stearate: the metal holds the anions in one complex that the parent acid
                                         does not represent), or an organometallic written as ions (butyllithium)
                      no parameters      an element without openCOSMO-RS 24a parameters (B, Ge, As, Se, Sb, Te), in
                                         the molecule or its counter-ion
                      permanent ion      still charged once neutralized (quaternary ammonium and the like)
                      radical            open-shell as written; the recipe computes closed-shell singlets
                      isotope-labelled   owner decision D-ISO: excluded, never mapped to the unlabelled parent
                      large and flexible above 700 g/mol with more than 20 rotatable bonds: one conformer cannot
                                         represent it
served as its parent  a salt, ion or hydrate whose neutral parent the release computed: it needs only an alias
to compute            the rest, by elements (CHNO; C, H, N, O with F, Cl, Br or I; any S, P or Si), with the release's
                      status for structures it lists but has not computed

The census funnel (scripts/build_halogen_inputs.py) defines the flags, the metal set and the radical test; this
triage keeps them and adds the parent of a salt or ion, the S, P and Si organics, and the cut for large, flexible
molecules. openCOSMO-RS 24a parameterizes H, C, N, O, F, Si, P, S, Cl and Br; iodine lacks only the dispersion
constant tau, which enters the solvation free energy and not the activity coefficients behind partitioning and LLE
(census notes 5a), so iodine counts as parameterized.

ORCA cost of the structures to compute: the campaign fit on AMD EPYC 7763 (2026-09-12 pilot), seconds =
exp(-0.687758) * atoms^2.305719 * 1.113048, atoms including hydrogens; the fit was made up to 107 atoms.

    python3 scripts/plastchem_coverage.py ASSET OUT_DIR      (ASSET: the served plastchem_opencosmo.duckdb)
"""
import collections
import csv
import datetime
import hashlib
import json
import math
import sys
from pathlib import Path

import duckdb
from rdkit import Chem, RDLogger
from rdkit.Chem import Descriptors, rdMolDescriptors
from rdkit.Chem.MolStandardize import rdMolStandardize

RDLogger.DisableLog("rdApp.*")
ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "inputs/census/plastchem_db_v1.0_full_database_subset.csv"
CENSUS_SHA256 = "9b1b68e40504011798241502b19c74d2889aa9af0eb611d5f81e41d94c4a51a1"
NON_METALS = {"H", "He", "B", "C", "N", "O", "F", "Ne", "Si", "P", "S", "Cl", "Ar", "Ge", "As", "Se", "Br", "Kr",
              "Sb", "Te", "I", "Xe", "At", "Rn"}  # the census funnel's set
PARAMETERIZED = {"H", "C", "N", "O", "F", "Si", "P", "S", "Cl", "Br", "I"}
ALKALI = {"Li", "Na", "K", "Rb", "Cs"}
HALOGENS = {"F", "Cl", "Br", "I"}

def _keys(*smiles):
    return {Chem.MolToInchiKey(Chem.MolFromSmiles(s)) for s in smiles}


WATER, = _keys("O")
#: Carbon that is not organic: carbonate, cyanide, cyanate and thiocyanate (as counter-ions), CO2, CO, elemental carbon.
INORGANIC_CARBON = _keys("OC(=O)O", "C#N", "OC#N", "SC#N", "O=C=O", "[C-]#[O+]", "[C]")
#: Neutral partners that make a salt written in neutral form: ammonia with any organic molecule, a mineral acid with
#: an organic molecule holding nitrogen (an amine hydrochloride, sulfate or phosphate).
AMMONIA, = _keys("N")
MINERAL_ACIDS = _keys("F", "Cl", "Br", "I", "O[N+](=O)[O-]", "ON=O", "OS(=O)(=O)O", "OP(=O)(O)O", "OP(=O)(O)OP(=O)(O)O",
                      "OCl(=O)(=O)=O", "OC(=O)O", "SC#N", "OC#N")
LARGE_FLEXIBLE = (700.0, 20)
BUCKETS = ("served", "served as its parent", "to compute", "should not", "cannot")
UNCHARGER = rdMolStandardize.Uncharger()


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def cpu_hours(atoms):
    return math.exp(-0.687758) * atoms ** 2.305719 * 1.113048 / 3600


def neutral(fragment):
    return UNCHARGER.uncharge(fragment)


def species_of(entry):
    """(reason, detail) when the entry cannot or should not be computed, else (None, facts about its species)."""
    flag = lambda name: (entry[name] or "").strip() == "1"  # noqa: E731
    if flag("UVCBs") or flag("polymers") or flag("mixtures"):
        kinds = [k for k, n in (("UVCB", "UVCBs"), ("polymer", "polymers"), ("mixture", "mixtures")) if flag(n)]
        return "no single structure", f"PlastChem flags it a {' and '.join(kinds)}"
    smiles = next((s for s in (entry["isomeric_smiles"], entry["canonical_smiles"]) if (s or "").strip() not in ("", "nan")), "")
    if not smiles:
        return "no structure", "no SMILES"
    mol = Chem.MolFromSmiles(smiles.strip())
    if mol is None:
        return "unparsable", "its SMILES does not parse"
    if flag("inorganic_compounds"):
        return "inorganic", "PlastChem flags it inorganic"
    if flag("organometallics"):
        return "metal", "PlastChem flags it organometallic"
    organic, counter, partner, hydrate = [], [], [], False
    for fragment in Chem.GetMolFrags(mol, asMols=True):
        symbols = {atom.GetSymbol() for atom in fragment.GetAtoms()}
        key = Chem.MolToInchiKey(neutral(fragment))
        if key == WATER:
            hydrate = True
        elif "C" in symbols and key not in INORGANIC_CARBON:
            organic.append((key, fragment))
        elif (any(atom.GetFormalCharge() for atom in fragment.GetAtoms()) or symbols - NON_METALS
              or key in MINERAL_ACIDS or key == AMMONIA):
            counter.append((key, fragment))
        else:
            partner.append(fragment)
    if not organic:
        return "inorganic", "no organic molecule"
    bonded = {a.GetSymbol() for _, f in organic for a in f.GetAtoms()} - NON_METALS
    if bonded:
        return "metal", f"{', '.join(sorted(bonded))} bonded in its organic molecule"
    around = {a.GetSymbol() for f in [f for _, f in counter] + partner for a in f.GetAtoms()}
    if around - NON_METALS - ALKALI:
        return "metal", f"a salt of {', '.join(sorted(around - NON_METALS - ALKALI))}"
    if around - PARAMETERIZED - ALKALI:
        return "no parameters", f"its counter-ion or partner holds {', '.join(sorted(around - PARAMETERIZED - ALKALI))}"
    molecules = "several molecules"
    if len({key for key, _ in organic}) > 1:
        return molecules, f"{len({key for key, _ in organic})} different organic molecules"
    if partner:
        return molecules, f"an organic molecule with {', '.join(sorted(Chem.MolToSmiles(f) for f in partner))}"
    if len(organic) > 1 and not counter:
        return molecules, f"{len(organic)} copies of one neutral molecule"
    written_neutral = counter and not any(a.GetFormalCharge() for f in [f for _, f in counter] + [organic[0][1]]
                                          for a in f.GetAtoms())
    if (written_neutral and all(key in MINERAL_ACIDS for key, _ in counter)
            and not any(a.GetSymbol() == "N" for a in organic[0][1].GetAtoms())):
        return molecules, "an organic molecule without nitrogen beside a mineral acid (an ester written as its reactants)"
    if any(a.GetSymbol() == "C" and a.GetFormalCharge() for a in organic[0][1].GetAtoms()) and around & ALKALI:
        return "metal", "an organometallic written as ions"
    original = organic[0][1]
    species = neutral(original)
    elements = {atom.GetSymbol() for atom in species.GetAtoms()}
    if elements - PARAMETERIZED:
        return "no parameters", f"holds {', '.join(sorted(elements - PARAMETERIZED))}"
    if sum(atom.GetFormalCharge() for atom in species.GetAtoms()):
        return "permanent ion", "still charged once neutralized"
    if any(atom.GetNumRadicalElectrons() for atom in species.GetAtoms()):
        return "radical", "open-shell"
    if any(atom.GetIsotope() for atom in mol.GetAtoms()):
        return "isotope-labelled", "owner decision D-ISO"
    form = ("salt" if counter else "ion" if any(a.GetFormalCharge() for a in original.GetAtoms()) and
            Chem.MolToInchiKey(original) != Chem.MolToInchiKey(species) else "hydrate" if hydrate else "")
    weight, rotatable = Descriptors.MolWt(species), rdMolDescriptors.CalcNumRotatableBonds(species)
    return None, dict(key=Chem.MolToInchiKey(species), smiles=Chem.MolToSmiles(species), form=form,
                      elements=elements, weight=weight, rotatable=rotatable, atoms=Chem.AddHs(species).GetNumAtoms())


def classify(entry, served_ids, served_keys, statuses):
    """A row of PLASTCHEM_COVERAGE.tsv for one PlastChem entry."""
    row = dict(plastchem_id=entry["plastchem_ID"], cas=(entry["cas_fixed"] or entry["cas"] or "").strip("'"),
               name=next((entry[k].strip() for k in ("pubchem_name", "iupac_name", "cas")
                          if (entry[k] or "").strip() not in ("", "nan")), ""),
               bucket="", reason="", detail="", species_inchikey="", species_smiles="", species_g_mol="",
               atoms_with_h="", release_status="")
    flagged = any((entry[k] or "").strip() == "1" for k in ("UVCBs", "polymers", "mixtures"))
    if row["plastchem_id"] in served_ids or (entry["inchikey"] in served_keys and not flagged):
        return dict(row, bucket="served", reason="computed", detail="computed in the served release",
                    species_inchikey=entry["inchikey"])
    reason, facts = species_of(entry)
    if reason:
        bucket = "cannot" if reason in ("no single structure", "no structure", "unparsable", "several molecules") else "should not"
        return dict(row, bucket=bucket, reason=reason, detail=facts)
    row.update(species_inchikey=facts["key"], species_smiles=facts["smiles"], species_g_mol=f"{facts['weight']:.1f}",
               atoms_with_h=str(facts["atoms"]))
    if facts["key"] in served_keys:
        if facts["form"]:
            return dict(row, bucket="served as its parent", reason=f"parent of {'an' if facts['form'] == 'ion' else 'a'} {facts['form']}",
                        detail="its neutral parent is computed: it needs only an alias")
        return dict(row, bucket="served", reason="computed, read from its SMILES",
                    detail="its SMILES gives the InChIKey of a computed contaminant")
    if facts["weight"] > LARGE_FLEXIBLE[0] and facts["rotatable"] > LARGE_FLEXIBLE[1]:
        return dict(row, bucket="should not", reason="large and flexible",
                    detail=f"{facts['weight']:.0f} g/mol with {facts['rotatable']} rotatable bonds")
    elements = facts["elements"]
    kind = ("CHNO" if elements <= {"C", "H", "N", "O"} else
            "halogen" if elements <= {"C", "H", "N", "O"} | HALOGENS else "S, P or Si")
    detail = "; ".join(filter(None, [f"parent of {'an' if facts['form'] == 'ion' else 'a'} {facts['form']}" if facts["form"] else "",
                                     "above 700 g/mol" if facts["weight"] > 700 else ""]))
    return dict(row, bucket="to compute", reason=kind, detail=detail, release_status=statuses.get(facts["key"], ""))


def main(asset, out_dir):
    out_dir = Path(out_dir)
    if sha256(CENSUS) != CENSUS_SHA256:
        raise SystemExit(f"{CENSUS} is not the pinned census export")
    con = duckdb.connect(str(asset), read_only=True)
    served_ids = {str(p) for (ids,) in con.execute(
        "SELECT plastchem_id FROM contaminants WHERE computed AND plastchem_id IS NOT NULL").fetchall()
                  for p in str(ids).split(";")}
    served_keys = {k for (k,) in con.execute("SELECT inchikey FROM contaminants WHERE computed").fetchall()}
    statuses = dict(con.execute("SELECT inchikey, status FROM contaminants WHERE NOT computed").fetchall())
    meta = dict(con.execute("SELECT key, value FROM metadata").fetchall())
    entries = list(csv.DictReader(CENSUS.open()))
    rows = [classify(e, served_ids, served_keys, statuses) for e in entries]
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "PLASTCHEM_COVERAGE.tsv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    buckets = collections.Counter(r["bucket"] for r in rows)
    reasons = collections.Counter((r["bucket"], r["reason"]) for r in rows)
    simulable = buckets["served"] + buckets["served as its parent"] + buckets["to compute"]
    structures = {}
    for r in rows:
        if r["bucket"] == "to compute":
            structures.setdefault(r["species_inchikey"], r)
    new = [r for r in structures.values() if not r["release_status"]]
    entries_of = collections.Counter(r["species_inchikey"] for r in rows if r["bucket"] == "to compute")
    orca = lambda r: cpu_hours(int(r["atoms_with_h"])) if r["release_status"] in ("", "failed") else 0.0  # noqa: E731
    need, covered, hours, used = math.ceil(0.95 * (buckets["served"] + buckets["served as its parent"] + buckets["to compute"])) - buckets["served"] - buckets["served as its parent"], 0, 0.0, 0
    for r in sorted(structures.values(), key=lambda r: orca(r) / entries_of[r["species_inchikey"]]):
        if covered >= need:
            break
        covered, hours, used = covered + entries_of[r["species_inchikey"]], hours + orca(r), used + 1
    summary = dict(
        made_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        inputs=dict(census=str(CENSUS.relative_to(ROOT)), census_sha256=CENSUS_SHA256,
                    release=json.loads(meta["releases"])[0]["release"] if "releases" in meta else None,
                    release_computed=len(served_keys)),
        entries=len(rows),
        buckets={b: buckets[b] for b in BUCKETS},
        reasons={f"{b}: {r}": n for (b, r), n in sorted(reasons.items(), key=lambda kv: (BUCKETS.index(kv[0][0]), -kv[1]))},
        simulable=simulable,
        served_share=round(buckets["served"] / simulable, 4),
        served_with_parents_share=round((buckets["served"] + buckets["served as its parent"]) / simulable, 4),
        entries_short_of_95_percent=max(0, math.ceil(0.95 * simulable) - buckets["served"] - buckets["served as its parent"]),
        structures_to_compute=len(structures),
        structures_by_elements=dict(collections.Counter(r["reason"] for r in structures.values()).most_common()),
        structures_as_parents=sum(r["detail"].startswith("parent") for r in structures.values()),
        structures_above_700_g_mol=sum("above 700" in r["detail"] for r in structures.values()),
        structures_by_release_status=dict(collections.Counter(r["release_status"] or "not in the release"
                                                              for r in structures.values()).most_common()),
        orca_new_structures=len(new),
        orca_new_cpu_hours=round(sum(cpu_hours(int(r["atoms_with_h"])) for r in new)),
        orca_new_atoms_median=sorted(int(r["atoms_with_h"]) for r in new)[len(new) // 2] if new else None,
        orca_new_above_107_atoms=sum(int(r["atoms_with_h"]) > 107 for r in new),
        cheapest_route_to_95_percent=dict(structures=used, entries=covered, orca_cpu_hours=round(hours),
                                          note="aliases need no calculation; structures ordered by ORCA CPU-hours per "
                                               "entry, thermodynamics-only structures (pending, running) first, failed "
                                               "ones at the fit's cost"),
        criteria=dict(parameterized=sorted(PARAMETERIZED), salt_metals_allowed=sorted(ALKALI),
                      large_flexible=dict(g_mol=LARGE_FLEXIBLE[0], rotatable_bonds=LARGE_FLEXIBLE[1])),
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
