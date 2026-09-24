# PlastChem ORCA calculation files

Each molecule's ORCA/openCOSMO calculation files from the PlastChem contaminant campaign. The archive holds the run
folder behind every surface in `../opencosmo-outputs/`, plus the 34 runs whose surface was not released.

## Layout inside the archive

Each folder has the same path as its surface, without the extension. For example, `contaminants/<InChIKey>.orcacosmo`
in the surfaces archive matches `contaminants/<InChIKey>/` here.

- `orca-calculation-files/contaminants/<InChIKey>/`: 5,830 folders, one for each released contaminant surface.
  - 26 contaminants reuse a solvent's surface: toluene, acetone, ethanol and 23 more.
  - For those, the folder holds the files of the solvent run that produced the surface.
- `orca-calculation-files/polymers/<polymer>/<conformer>/`: 274 conformer folders.
- `orca-calculation-files/solvents/panel-32/<name>/`: 32 folders.
- `orca-calculation-files/solvents/common-69/<name>/`: 69 folders.
- `<category>/not-in-release/<stage>/<run>/`: 34 run folders whose surface was not released.
  - These are 23 contaminants, 10 polymer conformers and 1 solvent.
  - 15 did not finish: 9 polymer conformers and 5 contaminants failed, and 1 polymer conformer stopped during its COSMO
    step.
  - 19 converged, but their surface is not in the release. 18 are contaminants. The other is the phase-9
    γ-valerolactone run; the release uses the campaign's own surface for that solvent.
- `MANIFEST.tsv`: every file, with its category, identity, name, campaign run folder, released surface, run status,
  size and SHA-256.
- `RUNS.tsv`: one row per folder, with the files left out or replaced. It is also committed here.

A run is matched to a released surface when the SHA-256 of its `cosmo.solute.orcacosmo` equals the released file's.
The status column is the run record's own status when the run finished. Identities were checked afterwards, in the
cohort freeze (`../calculation-files/phase83-v1/cohort.json`).

## What each folder holds

- `opt.inp`: the geometry optimisation input (`OPT BP86 def2-TZVP(-f) TightSCF`), with the starting geometry.
- `optimized.xyz`: the optimised geometry.
- `cosmo.inp`: the `COSMORS(Water)` step on the optimised geometry.
- `cosmo.solute_vac.inp`, `cosmo.solute_cpcm.inp` and `cosmo.solvent_cpcm.inp`: the inputs ORCA generates for that
  step. The solute CPCM step writes the surface.
- `cosmo.out`: the COSMO step's ORCA log.
- `result.json`: the run record, with the input identity, the exact inputs, energies, timings and CPU.
- `cosmo.json` and `cosmo_out.json`: the COSMO step's records.

## Starting geometries from licensed files

Some runs started from geometries read from licensed files:

- The 284 polymer conformers started from COSMOtherm conformer files (`.mcos` or `.cosmo`).
- 68 of the 69 common solvents started from COSMObase.

For those 352 runs:

- `opt.inp` is left out.
- Where `result.json` quoted the starting geometry (343 runs), it is replaced by `result.redacted.json`. In that file,
  the quoted geometry is replaced by a note.
- Everything ORCA computed is kept.

`verify_orca_archive.py` checks two things: that no packed file from these runs shares more than 10% of its starting
coordinates, and that every released surface has its folder.

## Not included

- The surfaces themselves. They are in `../opencosmo-outputs/`.
- Binary intermediates: wavefunctions (`.gbw`), densities and CPCM files.
- The optimisation log (`opt.out`) and trajectory.

## Reassemble

```sh
cat orca-calculation-files.tar.xz.part* > orca-calculation-files.tar.xz
sha256sum -c SHA256SUMS
tar -xJf orca-calculation-files.tar.xz
```

## Rebuild

`run_pack.sh` runs on Euler against the campaign folder `~/plastchem-euler`. It does four things:

1. Runs `pack_orca_files.py plan`.
2. Runs `pack_orca_files.py pack`, piped through `xz -T2 -6` and `split -b 95000000`.
3. Writes `SHA256SUMS`.
4. Lists the archive.

`verify_orca_archive.py` then checks the result. `../opencosmo-outputs/build_export.py` assembled the surfaces archive.
