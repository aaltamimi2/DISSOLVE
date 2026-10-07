"""A-11: write the README.md of opencosmo-outputs/ and orca-calculation-files/ for the v2 archives, with every count
read from their MANIFEST.tsv, CLASSIFICATION.tsv, PUBLICATION_SETS.tsv and RUNS.tsv, and every definition from the
classifier and DISSOLVE's own structure search and families.

    python3 scripts/export-v2/write_readmes.py CAMPAIGN_DIR     (campaigns/plastchem-hpc in the DISSOLVE branch)"""
import collections
import csv
import json
import os
import sys
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
halogen = sum(row["campaign_source"].startswith("halogen-v1") for row in contaminants)
tier2_later = sum(row["campaign_source"].startswith("tier2-v1") for row in contaminants)
v1 = len(contaminants) - halogen - tier2_later
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
                  "| Compound | CAS | File(s), or why there is none |", "|---|---|---|"]
    for item in items:
        files = [path.rsplit("/", 1)[1] for path in item["surfaces"].split("; ") if path]
        shown = ", ".join(f"`{name}`" for name in files) if len(files) <= 2 else f"{len(files)} files: " + ", ".join(
            f"`{name}`" for name in files)
        pub_lines.append(f"| {item['label']}: {item['name']} | {item['cas'].replace(';', ', ')} | "
                         f"{shown or item['why_no_surface']} |")
    pub_lines.append("")

# Agent families
fam_lines = ["| Folder | Files here | Family members computed | Family (DISSOLVE's definition) |", "|---|---:|---:|---|"]
with_surface = {row["inchikey"] for row in classified}
for family in sorted(families, key=lambda f: f["name"]):
    folder = "agent-families/" + family["name"].replace(" ", "_")
    fam_lines.append(f"| `{folder.split('/')[1]}/` | {count(folder)} | {len(with_surface & set(family['members']))} | "
                     f"{family['description']}; {family['basis']} |")

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

hbcd_isomer = next((row for row in classified if row["inchikey"].startswith("SHRRVNVEOIKVSG")), None)
example = next(row for row in classified if row["inchikey"] == "BJQHLKABXJIVAM-UHFFFAOYSA-N")

(root / "opencosmo-outputs/README.md").write_text(f"""# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign and the halogen tier of amendment A-11 (2026-10-06/07). {len(contaminants):,} contaminants:
{v1:,} from the promotion-v1 cohort, {tier2_later} tier-2 CHNO structures (500-700 g/mol) that converged after it and
{halogen:,} from the halogen tier (C, H, N and O with F, Cl, Br or I, up to 700 g/mol).

## Layout

- `contaminants/<folder>/<name>.orcacosmo`: one surface per contaminant, in exactly one folder: the first of the five
  below that applies to it.
  1. `publication-sets/` ({top['publication-sets']}): the PFAS, brominated flame retardants and phthalates of Zhou et
     al., *Solvent-Mediated Contaminant Removal from Plastic Waste Using Thermodynamic Modeling*, Green Chem. 2026
     (d5gc06059a), from its ESI, and nothing else, so these folders can be used to validate against the paper.
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

Matched by CAS number, and by InChIKey where PlastChem files a compound under another CAS (DiNP, DnHP, DnOP). HBCD is
matched by the connectivity of 1,2,5,6,9,10-hexabromocyclododecane, so every computed stereoisomer is in `BFR/`;
{"PlastChem files the generic HBCD CAS 25637-99-4 under 1,1,2,2,3,3-hexabromocyclododecane, a" + chr(10) +
 f"different compound, which is in `{hbcd_isomer['folder'].removeprefix('contaminants/')}/` instead." if hbcd_isomer else ""} The
campaign holds neutral molecules of C, H, N, O, F, Cl, Br and I up to 700 g/mol taken from PlastChem, which is why the
sulfonic acids, the salts and the heaviest compounds have no file.

{chr(10).join(pub_lines)}
## 2. Agent families

DISSOLVE's definitions, from `src/dissolve/data/plastchem_families.json`. "Family members computed" counts every
member with a surface, including those filed in a publication set or a smaller family.

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

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD); def2 basis sets put an ECP on iodine.
- Conformers: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes to DFT.
- Identity: the optimised geometry's connectivity must match the input (D-IDENT); structures whose hydrogen moved
  (azo pigments becoming hydrazones) were rejected and have no surface here.
- Thermodynamics: openCOSMO-RS 24a at 298.15 K. The installed 24a class has no iodine dispersion parameter
  (tau_53); it enters only dG_solv, not the activity coefficients used for partitioning and LLE.
- Folders: `classify_contaminants.py` (inputs: `../inputs/publication_sets_zhou2026.json`,
  `../inputs/census/plastchem_db_v1.0_groups_functions.csv` and DISSOLVE's families and structure search).

## Reassemble

```sh
cat opencosmo-outputs.tar.xz.part* > opencosmo-outputs.tar.xz
sha256sum -c SHA256SUMS
tar -xJf opencosmo-outputs.tar.xz
```
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

```sh
cat orca-calculation-files.tar.xz.part* > orca-calculation-files.tar.xz
sha256sum -c SHA256SUMS
tar -xJf orca-calculation-files.tar.xz
```

## Rebuild

`run_export_v2.sh` runs on Euler in `~/opencosmo-export-v2`: `build_export_v2.py` assembles the surfaces from
`contaminants-v2.tsv` (written with `CLASSIFICATION.tsv` and `PUBLICATION_SETS.tsv` by `make_contaminants_tsv.py` from
the promotion-v2 release), then `run_pack.sh` packs these files and `verify_orca_archive.py` checks that every released
surface has its folder and that no licensed starting geometry leaked.
""")
print("README files written:", len(contaminants), "contaminants;", len(not_released), "runs not in release;",
      dict(top))
