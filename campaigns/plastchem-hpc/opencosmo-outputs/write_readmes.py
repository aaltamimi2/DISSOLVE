"""A-11: write the README.md of opencosmo-outputs/ and orca-calculation-files/ for the v2 archives, with every count
read from their MANIFEST.tsv and RUNS.tsv.

    python3 scripts/export-v2/write_readmes.py CAMPAIGN_DIR     (campaigns/plastchem-hpc in the DISSOLVE branch)"""
import collections
import csv
import sys
from pathlib import Path

root = Path(sys.argv[1])
surfaces = list(csv.DictReader((root / "opencosmo-outputs/MANIFEST.tsv").open(), delimiter="\t"))
runs = list(csv.DictReader((root / "orca-calculation-files/RUNS.tsv").open(), delimiter="\t"))
by_category = collections.Counter(row["category"] for row in surfaces)
contaminants = [row for row in surfaces if row["category"] == "contaminant"]
halogen = sum(row["campaign_source"].startswith("halogen-v1") for row in contaminants)
tier2_later = sum(row["campaign_source"].startswith("tier2-v1") for row in contaminants)
v1 = len(contaminants) - halogen - tier2_later
polymers = sorted({row["name"].split("_")[0] for row in surfaces if row["category"] == "polymer conformer"})  # PETG's three connectivities are one polymer
not_released = [row for row in runs if row["released_surface"] == "not in release"]
by_stage = collections.Counter(row["run_folder"].split("/runs/")[0] for row in not_released)
example = next(row for row in contaminants if row["identity"] == "BJQHLKABXJIVAM-UHFFFAOYSA-N")

(root / "opencosmo-outputs/README.md").write_text(f"""# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign and the halogen tier of amendment A-11 (2026-10-06/07).

## Layout

- `contaminants/<name>.orcacosmo`: {len(contaminants):,} contaminant surfaces, one per structure, named by the
  contaminant's own name ({example['path']} is {example['name']}). {v1:,} are the promotion-v1 cohort, {tier2_later}
  are tier-2 CHNO structures (500-700 g/mol) that converged after it, and {halogen:,} are the halogen tier (C, H, N
  and O with F, Cl, Br or I, up to 700 g/mol).
  - A name is kept as written except where a file system needs otherwise: `/ \\ : * ? " < > |` become `-`, spaces
    become `_`, and names longer than 100 characters are cut at a word boundary. Names equal apart from case carry
    `_[first InChIKey block]` so every file is unique on case-insensitive systems.
  - `MANIFEST.tsv` gives each file's InChIKey (column `identity`), name, SHA-256 and campaign source.
- `polymers/<polymer>/<polymer>__<conformer>.orcacosmo`: {by_category['polymer conformer']} oligomer conformer surfaces for
  {len(polymers)} polymers. The 10 complete ensembles are used in the data: EVOH, nylon 6, nylon 6,6, PC, PE, PET, PP,
  PS, PVC, PVDF.
- `solvents/panel-32/<name>.orcacosmo`: the 32 solvent surfaces used for partitioning and miscibility.
- `solvents/common-69/<name>.orcacosmo`: DISSOLVE's 69 common solvents, computed separately.

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD); def2 basis sets put an ECP on iodine.
- Conformers: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes to DFT.
- Identity: the optimised geometry's connectivity must match the input (D-IDENT); structures whose hydrogen moved
  (azo pigments becoming hydrazones) were rejected and have no surface here.
- Thermodynamics: openCOSMO-RS 24a at 298.15 K. The installed 24a class has no iodine dispersion parameter
  (tau_53); it enters only dG_solv, not the activity coefficients used for partitioning and LLE.

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

Each folder has the same path as its surface, without the extension: `contaminants/<name>.orcacosmo` in the surfaces
archive matches `contaminants/<name>/` here, named the same way (see that README).

- `orca-calculation-files/contaminants/<name>/`: one folder for each released contaminant surface.
- `orca-calculation-files/polymers/<polymer>/<conformer>/` and `orca-calculation-files/solvents/.../<name>/`.
- `<category>/not-in-release/<campaign path>/<InChIKey>/`: {len(not_released)} run folders whose surface was not
  released (failed, rejected for identity, interrupted, or replaced by a retry), by campaign:
  {", ".join(f"{stage} {count}" for stage, count in sorted(by_stage.items()))}.
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
`contaminants-v2.tsv` (written by `make_contaminants_tsv.py` from the promotion-v2 release), then `run_pack.sh` packs
these files and `verify_orca_archive.py` checks that every released surface has its folder and that no licensed
starting geometry leaked.
""")
print("README files written:", len(contaminants), "contaminants;", len(not_released), "runs not in release")
