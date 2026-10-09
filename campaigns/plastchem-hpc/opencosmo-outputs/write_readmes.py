"""A-11: write the README.md of opencosmo-outputs/ and orca-calculation-files/ for the v2 archives, with every count
read from their MANIFEST.tsv, CLASSIFICATION.tsv, PUBLICATION_SETS.tsv and RUNS.tsv, and every definition from the
classifier and DISSOLVE's own structure search and families.

    python3 scripts/export-v2/write_readmes.py CAMPAIGN_DIR     (campaigns/plastchem-hpc in the DISSOLVE branch)"""
import collections
import csv
import json
import os
import sys
import textwrap
from pathlib import Path

root = Path(sys.argv[1])
src = Path(os.environ.get("DISSOLVE_SRC", root.resolve().parents[1] / "src"))
sys.path.insert(0, str(src))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_contaminants import CLASS_ORDER, GROUP_ORDER, HALOGEN_CLASSES  # noqa: E402
from dissolve.contaminant_search import FUNCTIONAL_GROUPS  # noqa: E402


def table(path):
    return list(csv.DictReader((root / path).open(), delimiter="\t", quoting=csv.QUOTE_NONE))


surfaces = table("opencosmo-outputs/MANIFEST.tsv")
classified = table("opencosmo-outputs/CLASSIFICATION.tsv")
publication = table("opencosmo-outputs/PUBLICATION_SETS.tsv")
runs = table("orca-calculation-files/RUNS.tsv")
families = json.loads((src / "dissolve/data/plastchem_families.json").read_text())["families"]
by_category = collections.Counter(row["category"] for row in surfaces)
contaminants = [row for row in surfaces if row["category"] == "contaminant"]
assert sorted(row["path"] for row in contaminants) == sorted(row["surface"] for row in classified)
tier_files = [row for row in contaminants if row["campaign_source"].startswith("publication-v1")]
released = [row for row in contaminants if not row["campaign_source"].startswith("publication-v1")]
halogen = sum(row["campaign_source"].startswith("halogen-v1") for row in released)
tier2_later = sum(row["campaign_source"].startswith("tier2-v1") for row in released)
coverage = sum(row["campaign_source"].startswith("coverage-v1") for row in released)  # A-13, promotion-v4 onward
v1 = len(released) - halogen - tier2_later - coverage


def archive_sets(folder, stem):
    """Every archive set a SHA256SUMS lists (the v2 base set, then one per release since A-13), in order."""
    names = [line.split()[1] for line in (root / folder / "SHA256SUMS").read_text().splitlines() if line.strip()]
    return [name for name in names if name.startswith(stem) and name.endswith(".tar.xz")]


def reassemble(folder, stem):
    sets = archive_sets(folder, stem)
    lines = [f"cat {name}.part* > {name}" for name in sets] + ["sha256sum -c SHA256SUMS"]
    lines += [f"tar -xJf {name}" for name in sets]
    note = ("" if len(sets) == 1 else
            f"\n\nThe {len(sets)} sets unpack into the same `{stem}/` tree: the first holds the v2 archive (the CHNO campaign, "
            "the halogen tier and the publication sets), each later one the contaminants a release added (amendment A-13; "
            "parts are never rewritten).")
    return "```sh\n" + "\n".join(lines) + "\n```" + note
polymers = sorted({row["name"].split("_")[0] for row in surfaces if row["category"] == "polymer conformer"})  # PETG's three connectivities are one polymer
not_released = [row for row in runs if row["released_surface"] == "not in release"]
by_stage = collections.Counter(row["run_folder"].split("/runs/")[0] for row in not_released)
in_folder = collections.defaultdict(list)
for row in classified:
    in_folder[row["folder"].removeprefix("contaminants/")].append(row)
top = collections.Counter(folder.split("/")[0] for folder in in_folder for _ in in_folder[folder])


def examples(folder, n=3):
    """The shortest names in a folder: usually its plainest members."""
    names = sorted((row["name"] for row in in_folder[folder]), key=lambda name: (len(name), name.casefold()))
    return "; ".join(names[:n])


def count(folder):
    return len(in_folder.get(folder, []))


# Publication sets
pub_lines = []
for set_name, title in (("PFAS", "PFAS (ESI Table S7)"), ("BFR", "Brominated flame retardants (Case Study 1)"),
                        ("Phthalates", "Phthalates (ESI Table S5)")):
    items = [item for item in publication if item["set"] == set_name]
    have = sum(bool(item["surfaces"]) for item in items)
    pub_lines += [f"### `publication-sets/{set_name}/`: {title}, {count(f'publication-sets/{set_name}')} files for "
                  f"{have} of its {len(items)} compounds", "",
                  "| Compound | CAS | Species, computed in | File, or why there is none |", "|---|---|---|---|"]
    for item in items:
        files = [path.rsplit("/", 1)[1] for path in item["surfaces"].split("; ") if path]
        shown = ", ".join(f"`{name}`" for name in files)
        where = "A-12" if item["computed_in"].startswith("publication") else "release"
        pub_lines.append(f"| {item['label']}: {item['name']} | {item['cas'].replace(';', ', ')} | "
                         f"{item['species']}, {where} | {shown or item['why_no_surface']} |")
    pub_lines.append("")

# Agent families
fam_lines = ["| Folder | Files here | Family members computed | Family (DISSOLVE's definition) |", "|---|---:|---:|---|"]
with_surface = {row["inchikey"] for row in classified}
# a family defined after its members were archived files none of them: archived surfaces keep their folder (A-13)
empty_families = []
for family in sorted(families, key=lambda f: f["name"]):
    folder = "agent-families/" + family["name"].replace(" ", "_")
    members_with_surface = len(with_surface & set(family["members"]))
    if members_with_surface and not count(folder):
        empty_families.append(family["name"])
    fam_lines.append(f"| `{folder.split('/')[1]}/` | {count(folder)} | {members_with_surface} | "
                     f"{family['description']}; {family['basis']} |")

empty_text = ("" if not empty_families else "\n\n" + textwrap.fill(
    f"The {' and '.join(empty_families)} {'family' if len(empty_families) == 1 else 'families'} came after "
    f"{'its' if len(empty_families) == 1 else 'their'} members were "
    "archived: a contaminant keeps the folder of the set that first archived it (parts are never rewritten), so "
    f"{'its folder is' if len(empty_families) == 1 else 'their folders are'} empty.", 118))

# PlastChem groups
group_lines = ["| Folder | Files | Shortest names in it |", "|---|---:|---|"]
for group in GROUP_ORDER:
    if count(f"plastchem-groups/{group}"):
        group_lines.append(f"| `{group}/` | {count(f'plastchem-groups/{group}')} | {examples(f'plastchem-groups/{group}')} |")

# Structure classes
what = {"polybrominated_diphenyl_ethers": "PBDE congeners: C12H(10-n)Br(n)O, two benzene rings joined by an ether "
                                          "oxygen, with nothing but bromine on them",
        "fluorinated": "carries fluorine and no other halogen", "chlorinated": "carries chlorine and no other halogen",
        "brominated": "carries bromine and no other halogen", "iodinated": "carries iodine and no other halogen",
        "several_halogens": "carries two or more of F, Cl, Br and I",
        "aliphatic_hydrocarbons": "carbon and hydrogen only, and none of the classes above (no aromatic ring, no "
                                  "seven-CH2 chain)"}
what.update({name.replace(" ", "_"): meaning for name, (_smarts, meaning) in FUNCTIONAL_GROUPS.items()})
order = ["polybrominated_diphenyl_ethers", *HALOGEN_CLASSES.values(), "several_halogens", *(name.replace(" ", "_") for name in CLASS_ORDER),
         "aliphatic_hydrocarbons"]
class_lines = ["| Folder | Files | What it holds | Shortest names in it |", "|---|---:|---|---|"]
for name in order:
    if count(f"structure-classes/{name}"):
        class_lines.append(f"| `{name}/` | {count(f'structure-classes/{name}')} | {what[name]} | "
                           f"{examples(f'structure-classes/{name}')} |")

# Coverage of PlastChem: the latest report of scripts/plastchem_coverage.py
coverage_json = sorted((root / "reports").glob("plastchem-coverage-*/summary.json"))[-1]
cov = json.loads(coverage_json.read_text())
cov_dir = "../" + str(coverage_json.parent.relative_to(root))
why = cov["reasons"]
out = {"cannot": sum(n for k, n in why.items() if k.startswith("cannot:")),
       "should not": sum(n for k, n in why.items() if k.startswith("should not:"))}
parents = cov["buckets"]["served as its parent"]
new_orca = cov["orca_new_structures"]
route = cov["cheapest_route_to_95_percent"]
cannot_rows = [
    ("no single structure", "PlastChem flags it a UVCB, polymer or mixture; a SMILES it carries is usually a monomer or one "
                            "component (PlastChem's own authors count these entries as not assessable as one structure)"),
    ("no structure", "no SMILES"),
    ("several molecules", "its SMILES lists different organic molecules (reaction products, adducts, salts of two organic "
                          "ions), copies of one neutral molecule, or an organic molecule beside a neutral partner that is "
                          "not a counter-ion (an ester written as alcohol and acid)"),
    ("unparsable", "its SMILES does not parse"),
]
should_rows = [
    ("inorganic", "PlastChem's inorganic flag, or no organic molecule"),
    ("metal", "PlastChem's organometallic flag, a metal bonded in the molecule, a salt of a metal other than Li, Na, K, Rb "
              "and Cs (metal soaps such as zinc stearate), or an organometallic written as ions. openCOSMO-RS 24a has no "
              "metal parameters, and a higher level of theory would not supply them: its parameters are fitted to "
              "BP86/def2-TZVPD surfaces"),
    ("permanent ion", "still charged once neutralized (quaternary ammonium and the like)"),
    ("large and flexible", "above 700 g/mol with more than 20 rotatable bonds: one conformer cannot represent it"),
    ("no parameters", "an element without openCOSMO-RS 24a parameters: B, Ge, As, Se, Sb or Te"),
    ("radical", "open-shell as written: nitroxide stabilizers such as Tempol, and hydrosilanes whose SMILES lacks the "
                "hydrogen on silicon; the recipe computes closed-shell molecules"),
    ("isotope-labelled", "owner decision D-ISO: excluded, never mapped to the unlabelled compound"),
]
coverage_lines = ["| Entries | Count | Rule |", "|---|---:|---|",
                  f"| Served | {cov['buckets']['served']:,} | the release computed it and lists its PlastChem ID (since "
                  f"promotion-v4 also a salt, ion or hydrate, listed with its neutral parent), or its InChIKey if "
                  f"PlastChem does not flag it a UVCB, polymer or mixture |",
                  f"| Served as its parent | {parents:,} | a salt, ion or hydrate whose neutral parent the release "
                  f"computed but does not list it yet: it needs only an alias |",
                  f"| To compute | {cov['buckets']['to compute']:,} | simulable, not yet computed: "
                  f"{cov['structures_to_compute']:,} structures |",
                  f"| Should not be computed | {out['should not']:,} | below |",
                  f"| Cannot be computed | {out['cannot']:,} | below |",
                  "", "| Cannot: no single molecule | Entries | Why |", "|---|---:|---|"]
coverage_lines += [f"| {name} | {why.get('cannot: ' + name, 0):,} | {text} |" for name, text in cannot_rows]
coverage_lines += ["", "| Should not: outside openCOSMO-RS 24a or the recipe | Entries | Why |", "|---|---:|---|"]
coverage_lines += [f"| {name} | {why.get('should not: ' + name, 0):,} | {text} |" for name, text in should_rows]
by_status = cov["structures_by_release_status"]
by_elements = cov["structures_by_elements"]
unfinished = by_status.get("thermodynamics_pending", 0) + by_status.get("running", 0)
# the release status of the structures still to compute, naming only the states some structure is in
status_parts = [text for n, text in (
    (by_status.get("thermodynamics_pending", 0), f"{by_status.get('thermodynamics_pending', 0):,} already have surfaces "
                                                 "whose partition and miscibility were not finished when the release was frozen"),
    (by_status.get("running", 0), f"{by_status.get('running', 0):,} were still running"),
    (by_status.get("failed", 0), f"{by_status.get('failed', 0):,} failed")) if n]
status_text = (", ".join(status_parts[:-1]) + " and " + status_parts[-1] if len(status_parts) > 1 else
               "".join(status_parts) or "None has been run")
status_text = status_text[0].upper() + status_text[1:]
coverage_text = "\n\n".join(textwrap.fill(paragraph, 118) for paragraph in (
    f"That leaves {cov['simulable']:,} simulable entries. The release serves {cov['buckets']['served']:,} of them "
    f"({cov['buckets']['served'] / cov['simulable']:.1%})"
    + (f" and {parents:,} more through their parent ({(cov['buckets']['served'] + parents) / cov['simulable']:.1%} "
       "together)" if parents else "")
    + f"; 95% needs {cov['entries_short_of_95_percent']:,} more entries. The {cov['structures_to_compute']:,} "
    f"structures still to compute are {by_elements.get('S, P or Si', 0):,} with sulfur, phosphorus or silicon (no tier "
    f"computed them before the coverage campaign), {by_elements.get('halogen', 0):,} halogenated and {by_elements.get('CHNO', 0):,} of "
    f"C, H, N and O only; {cov['structures_as_parents']:,} are the neutral parents of salts and "
    f"{cov['structures_above_700_g_mol']:,} are above 700 g/mol (rigid enough for one conformer). "
    f"{status_text}; the other {new_orca:,} were never run: about "
    f"{round(cov['orca_new_cpu_hours'], -2):,} CPU-hours of ORCA by the campaign's cost fit (median "
    f"{cov['orca_new_atoms_median']} atoms with hydrogens; {cov['orca_new_above_107_atoms']} above the 107 atoms the "
    f"fit was made on). The cheapest {route['structures']:,} structures{', the unfinished ones first,' if unfinished else ''} "
    f"reach 95% for about {round(route['orca_cpu_hours'], -1):,} ORCA CPU-hours.",
    f"`{cov_dir}/PLASTCHEM_COVERAGE.tsv` has one row per PlastChem entry: its bucket and reason, and for the simulable "
    f"ones the molecule that would be computed (InChIKey, SMILES, g/mol, atoms with hydrogens) and the release "
    f"status; `summary.json` has the counts. Both are made by `../scripts/plastchem_coverage.py` from the census "
    f"export of the PlastChem workbook and the served release ({cov['inputs']['release']}).",
))

other_hbcd = [row for row in classified if row["inchikey"].startswith("DEIGXXQKDWULML")
              and "publication-sets" not in row["folder"]]
example = next(row for row in classified if row["inchikey"] == "BJQHLKABXJIVAM-UHFFFAOYSA-N")

(root / "opencosmo-outputs/README.md").write_text(f"""# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign, the halogen tier of amendment A-11 (2026-10-06/07) and the coverage campaign of amendment
A-13 (2026-10-08 onward), {len(released):,} contaminants: {v1:,} from the promotion-v1 cohort, {tier2_later} tier-2 CHNO
structures (500-700 g/mol) that converged after it, {halogen:,} from the halogen tier (C, H, N and O with F, Cl, Br or I,
up to 700 g/mol) and {coverage:,} from the coverage campaign (the simulable PlastChem structures not yet served, lightest
first, sulfur, phosphorus and silicon included). Also {len(tier_files)}
surfaces computed for the paper's compounds (amendment A-12, see section 1), which the release has served since
promotion-v3 (2026-10-08). How much of PlastChem this covers, and what cannot or should not be computed, is in
[Coverage of PlastChem](#coverage-of-plastchem).

## Layout

- `contaminants/<folder>/<name>.orcacosmo`: one surface per contaminant, in exactly one folder: the first of the five
  below that applies to it.
  1. `publication-sets/` ({top['publication-sets']}): `PFAS/`, `BFR/` and `Phthalates/` hold exactly the compounds of
     Zhou et al., *Solvent-Mediated Contaminant Removal from Plastic Waste Using Thermodynamic Modeling*, Green Chem.
     2026 (d5gc06059a), one file each and nothing else, so they can be used to validate against the paper: 26 PFAS,
     4 brominated flame retardants and 8 phthalates.
     The other members of the same families are in the folders below (other phthalates in
     `agent-families/Phthalates/`, other PFAS in `plastchem-groups/PFASs/`, other PBDEs in
     `structure-classes/polybrominated_diphenyl_ethers/`).
  2. `agent-families/` ({top['agent-families']}): the contaminant families DISSOLVE's agent uses when a question names
     a family ("phthalates", "antioxidants"). A compound in two families is filed in the smaller one.
  3. `plastchem-groups/` ({top['plastchem-groups']}): the structural groups of the PlastChem database (Wagner et al.
     2024, v1.0), for compounds in no agent family. A compound in several is filed in the most specific (PFAS,
     PBDEs, PCBs and dioxins first; alkanes and alkenes last).
  4. `structure-classes/` ({top['structure-classes']}): the rest, by structure: the PBDE congeners, else the
     halogen a compound carries, else its first functional group in DISSOLVE's structure search (in the order of
     the table below), else aliphatic hydrocarbons.
  5. `no-family/` ({top['no-family']}): none of the above: mostly organic peroxides, nitrosamines, hydrazines and
     five-membered heteroaromatics such as furans and imidazoles.
- `CLASSIFICATION.tsv`: one row per contaminant: InChIKey, name, file, folder, why it is there, and every grouping it
  belongs to (publication set, agent families, PlastChem groups, PlastChem functions, halogens, functional groups),
  not only the one that chose its folder. Filter it to find, say, every flame retardant or every chlorinated phthalate.
- `PUBLICATION_SETS.tsv`: each compound of the three publication sets, with its file(s) or why it has none.
- `MANIFEST.tsv`: every file, with its InChIKey (column `identity`), name, SHA-256 and campaign source.
- Names: a contaminant's own name, kept as written except where a file system needs otherwise: `/ \\ : * ? " < > |`
  become `-`, spaces become `_`, and names longer than 100 characters are cut at a word boundary. Names equal apart
  from case carry `_[first InChIKey block]`, so every file is unique on case-insensitive systems. For example
  `{example['surface']}` is {example['name']}.
- `polymers/<polymer>/<polymer>__<conformer>.orcacosmo`: {by_category['polymer conformer']} oligomer conformer
  surfaces for {len(polymers)} polymers. The 10 complete ensembles are used in the data: EVOH, nylon 6, nylon 6,6, PC,
  PE, PET, PP, PS, PVC, PVDF.
- `solvents/panel-32/<name>.orcacosmo`: the 32 solvent surfaces used for partitioning and miscibility.
- `solvents/common-69/<name>.orcacosmo`: DISSOLVE's 69 common solvents, computed separately.

## 1. Publication sets

One file per compound of the paper's ESI, in the species the paper modelled. The same files are also on the branch
as plain files, with each one's ORCA calculation files and its openCOSMO-RS partition and miscibility tables, in
`../publication-sets/`.

- PFAS (Table S7) are neutral acids, as the paper modelled them: across the paper's 32 solvents its values follow our
  neutral acids (r = 0.95-0.97) and not the anions (|r| below 0.1). A salt is its parent acid: NH4TetraFPt is TFPA's
  surface under its own label, and the two ADONA salts (NaDoDFNt, NH4PFNt) are one acid. Ten acids are the release's
  surfaces; the other 14 (the sulfonic acids, PFNS, the F-53B and ADONA acids, PFTriDA and PFTetraDA) were computed
  for this set (amendment A-12). A file named for a salt or an anion is named as the acid, with the paper's label,
  e.g. `Perfluorononanesulfonic_acid_(PFNS).orcacosmo`.
- Brominated flame retardants (Case Study 1): tri-PBDE is the ESI's 2,4,4'-tribromodiphenyl ether (BDE-28). The
  paper reports one HBCD value without naming the stereoisomer; the set holds gamma-HBCD, the main component of
  technical HBCD (the release's {len(other_hbcd)} other 1,2,5,6,9,10-HBCD stereoisomers are in
  `structure-classes/brominated/`). DECA and TBBPA-dbP are heavier than the release's 700 g/mol limit and were
  computed for this set (A-12).
- Phthalates (Table S5): from the release, by CAS number, and by InChIKey where PlastChem files a phthalate under
  another CAS (DiNP, DnHP, DnOP).

{chr(10).join(pub_lines)}
## 2. Agent families

DISSOLVE's definitions, from `src/dissolve/data/plastchem_families.json`. "Family members computed" counts every
member with a surface, including those filed in a publication set or a smaller family.{empty_text}

{chr(10).join(fam_lines)}

The Phthalates family is PlastChem's `orthophthalates` group plus every ortho-phthalate diester outside it (a
benzene-1,2-dicarboxylate diester whose ring carries nothing else): PlastChem's group alone leaves out diesters such as
bis(2-ethylbutyl) phthalate and butyl cyclohexyl phthalate. Ring-halogenated phthalates (tetrabromo- and
tetrachlorophthalates) are not in it; they are in the halogen folders of `structure-classes/`.

## 3. PlastChem groups

Folder names are PlastChem's column names, and a group holds what PlastChem put in it: `alkenes` includes aromatic
hydrocarbons such as pyrene, `aldehydes_simple` includes simple ketones such as camphor, and `PFASs` includes
compounds with a single CF3 or CF2 group, such as bifenthrin. PlastChem's `PBDEs` column flags brominated
biphenyls ({count('plastchem-groups/PBDEs')} computed here), not diphenyl ethers: PlastChem marks the PBDE congeners as
grouped but sets none of its group columns for them, so they are in
`structure-classes/polybrominated_diphenyl_ethers/` ({count('structure-classes/polybrominated_diphenyl_ethers')}; the
congener of the paper, BDE-28, is in `publication-sets/BFR/`).

{chr(10).join(group_lines)}

## 4. Structure classes

Tried in the order of this table: a compound goes in the first class that matches. The functional groups are
DISSOLVE's structure-search definitions (`src/dissolve/contaminant_search.py`, RDKit SMARTS on each compound's
SMILES); the PBDE class is defined by formula in `classify_contaminants.py`.

{chr(10).join(class_lines)}

## Coverage of PlastChem

Every entry of PlastChem v1.0 ({cov['entries']:,} in its full database) is in exactly one row of the first table, by the
first rule that applies in the order `../scripts/plastchem_coverage.py` tries them. Past the PlastChem flags, the
molecule judged is the one that would be computed: the entry's organic molecule in neutral form, so a salt or ion is
its parent acid or base (as Zhou et al. 2026 modelled their PFAS salts) and water of hydration is dropped. Iodine
counts as parameterized: openCOSMO-RS 24a lacks only its dispersion constant, which enters solvation free energies
and not partitioning or miscibility.

{chr(10).join(coverage_lines)}

{coverage_text}

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD); def2 basis sets put an ECP on iodine.
- Conformers: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes to DFT.
- Identity: the optimised geometry's connectivity must match the input (D-IDENT); structures whose hydrogen moved
  (azo pigments becoming hydrazones) were rejected and have no surface here.
- Thermodynamics: openCOSMO-RS 24a at 298.15 K. The installed 24a class has no iodine dispersion parameter
  (tau_53); it enters only dG_solv, not the activity coefficients used for partitioning and LLE.
- Publication-set surfaces of amendment A-12 (the PFAS acids the release lacked, DECA and TBBPA-dbP): the same
  recipe, structures from PubChem by CAS and checked against PlastChem. The sulfonic acids hold sulfur and four are
  above 700 g/mol, so the campaign's own tiers had left them out. Since promotion-v3 (2026-10-08) DISSOLVE serves
  their partition and miscibility rows with the rest of the release; they are also in `../publication-sets/`, and
  DISSOLVE's workbook screens still hold the paper's own COSMOtherm values for its 26 PFAS and 8 phthalates.
- Folders: `classify_contaminants.py` (inputs: `../inputs/publication_sets_zhou2026.json`,
  `../inputs/census/plastchem_db_v1.0_groups_functions.csv` and DISSOLVE's families and structure search).

## Reassemble

{reassemble("opencosmo-outputs", "opencosmo-outputs")}
""")

(root / "orca-calculation-files/README.md").write_text(f"""# PlastChem ORCA calculation files

Each molecule's ORCA/openCOSMO calculation files. The archive holds the run folder behind every surface in
`../opencosmo-outputs/`, plus the {len(not_released)} runs whose surface was not released.

## Layout inside the archive

Each folder has the same path as its surface, without the extension: `contaminants/<folder>/<name>.orcacosmo` in the
surfaces archive matches `contaminants/<folder>/<name>/` here, so the contaminants are organised the same way:
publication sets, agent families, PlastChem groups, structure classes and no-family (see that README and its
`CLASSIFICATION.tsv`).

- `orca-calculation-files/contaminants/<folder>/<name>/`: one folder for each released contaminant surface.
- `orca-calculation-files/polymers/<polymer>/<conformer>/` and `orca-calculation-files/solvents/.../<name>/`.
- `<category>/not-in-release/<campaign path>/<InChIKey>/`: {len(not_released)} run folders whose surface was not
  released (failed, rejected for identity, interrupted, or replaced by a retry), by campaign:
  {", ".join(f"{stage} {n}" for stage, n in sorted(by_stage.items()))}.
- `MANIFEST.tsv`: every file, with its category, identity (InChIKey), name, campaign run folder, released surface,
  run status, size and SHA-256.
- `RUNS.tsv`: one row per folder, with the files left out or replaced.

## What each folder holds

- `opt.inp`, `optimized.xyz`, `cosmo.inp`, the inputs ORCA generates for the COSMO step, `cosmo.out` (the COSMO step's
  log), `result.json` (the run record: input identity, exact inputs, energies, timings, CPU) and the COSMO records.
- Starting geometries read from licensed COSMObase/COSMOtherm files are never included: for those runs `opt.inp` is
  left out and `result.json` is replaced by `result.redacted.json`.
- Not included: the surfaces (in `../opencosmo-outputs/`), binary intermediates (`.gbw`, densities, CPCM files), the
  optimisation log and trajectory.

## Reassemble

{reassemble("orca-calculation-files", "orca-calculation-files")}

## Rebuild

`run_export_v2.sh` runs on Euler in `~/opencosmo-export-v2`: `build_export_v2.py` assembles the surfaces from
`contaminants-v2.tsv` (written with `CLASSIFICATION.tsv` and `PUBLICATION_SETS.tsv` by `make_contaminants_tsv.py` from
the promotion-v2 release and the publication tier's verified runs, `publication-v1`), then `run_pack.sh` packs these files and `verify_orca_archive.py` checks that every released
surface has its folder and that no licensed starting geometry leaked.
""")
print("README files written:", len(contaminants), "contaminants;", len(not_released), "runs not in release;",
      dict(top))
