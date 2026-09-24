# PlastChem openCOSMO outputs

Every ORCA/openCOSMO surface (`.orcacosmo`) computed for the PlastChem contaminant campaign, 2026-09-12 to
2026-09-24. These are the surfaces behind the partition and miscibility data DISSOLVE serves.

## Layout

- `contaminants/<InChIKey>.orcacosmo`: 5,830 contaminant surfaces, one per structure. This is the frozen cohort:
  5,803 main-tier and 27 tier-2 structures.
- `polymers/<polymer>/<polymer>__<conformer>.orcacosmo`: 274 oligomer conformer surfaces for 14 polymers.
  - The 10 complete ensembles are used in the data: EVOH, nylon 6, nylon 6,6, PC, PE, PET, PP, PS, PVC, PVDF (236
    conformers).
  - The other four were incomplete and are excluded from the ensembles: nitrocellulose, PETG, polyethersulfone and
    polyurethane.
- `solvents/panel-32/<name>.orcacosmo`: the 32 solvent surfaces used for partitioning and miscibility.
- `solvents/common-69/<name>.orcacosmo`: DISSOLVE's 69 common solvents. 30 of them overlap the panel by identity,
  with separately computed surfaces, so they are kept apart.
- `MANIFEST.tsv`: every file, with category, identity, name, SHA-256 and its path in the campaign.

## How they were made

- Quantum chemistry: ORCA 6.1.1. A gas-phase `OPT BP86 def2-TZVP(-f) TightSCF` optimisation, then `COSMORS(Water)`,
  whose solute step writes the CPCM surface (BP86/def2-TZVPD).
- Conformers:
  - Contaminants: RDKit ETKDGv3 generates up to 300 candidates, MMFF94 minimises them, and the lowest-energy one goes
    to DFT.
  - Polymers: oligomer conformers from COSMOtherm conformer files, then the same two ORCA steps.
- Solvents: the common-69 geometries started from COSMObase structures. The surfaces here are the new ORCA outputs;
  no COSMObase file is included.
- Thermodynamics: openCOSMO-RS with the 24a parameterization at 298.15 K.
- The scripts are in `../scripts/`. The per-stage workers, Slurm files, manifests, pins and the frozen cohort are in
  `../calculation-files/`.
- Each molecule's ORCA inputs, optimised geometry, COSMO-step log and run record are in `../orca-calculation-files/`.
- `build_export.py` assembled this folder's archive.

## Checks

- Contaminant and 32-panel solvent files match the SHA-256 recorded by the campaign (`calculation-files/phase83-v1/
  cohort.json` and `calculation-files/phase8-v1/manifest.json`). The build refused any mismatch.
- Polymer and common-solvent hashes are recorded in `MANIFEST.tsv`.

## Not included

- The licensed COSMObase/COSMOtherm `.cosmo` surfaces used only for route comparisons.
- ORCA inputs, logs and run records. They are in `../orca-calculation-files/`, except the optimisation logs.

## Reassemble

```sh
cat opencosmo-outputs.tar.xz.part* > opencosmo-outputs.tar.xz
sha256sum -c SHA256SUMS
tar -xJf opencosmo-outputs.tar.xz
```
