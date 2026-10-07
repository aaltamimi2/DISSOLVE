# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) behind the partition and miscibility data DISSOLVE serves: the
2026-09-12/24 CHNO campaign and the halogen tier of amendment A-11 (2026-10-06/07).

## Layout

- `contaminants/<name>.orcacosmo`: 7,160 contaminant surfaces, one per structure, named by the
  contaminant's own name (contaminants/Bis(2-ethylhexyl)_phthalate.orcacosmo is Bis(2-ethylhexyl) phthalate). 5,830 are the promotion-v1 cohort, 219
  are tier-2 CHNO structures (500-700 g/mol) that converged after it, and 1,111 are the halogen tier (C, H, N
  and O with F, Cl, Br or I, up to 700 g/mol).
  - A name is kept as written except where a file system needs otherwise: `/ \ : * ? " < > |` become `-`, spaces
    become `_`, and names longer than 100 characters are cut at a word boundary. Names equal apart from case carry
    `_[first InChIKey block]` so every file is unique on case-insensitive systems.
  - `MANIFEST.tsv` gives each file's InChIKey (column `identity`), name, SHA-256 and campaign source.
- `polymers/<polymer>/<polymer>__<conformer>.orcacosmo`: 274 oligomer conformer surfaces for
  13 polymers. The 10 complete ensembles are used in the data: EVOH, nylon 6, nylon 6,6, PC, PE, PET, PP,
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
