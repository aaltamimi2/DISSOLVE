# PlastChem ORCA calculation files

Each molecule's ORCA/openCOSMO calculation files. The archive holds the run folder behind every surface in
`../opencosmo-outputs/`, plus the 196 runs whose surface was not released.

## Layout inside the archive

Each folder has the same path as its surface, without the extension: `contaminants/<folder>/<name>.orcacosmo` in the
surfaces archive matches `contaminants/<folder>/<name>/` here, so the contaminants are organised the same way:
publication sets, agent families, PlastChem groups, structure classes and no-family (see that README and its
`CLASSIFICATION.tsv`).

- `orca-calculation-files/contaminants/<folder>/<name>/`: one folder for each released contaminant surface.
- `orca-calculation-files/polymers/<polymer>/<conformer>/` and `orca-calculation-files/solvents/.../<name>/`.
- `<category>/not-in-release/<campaign path>/<InChIKey>/`: 196 run folders whose surface was not
  released (failed, rejected for identity, interrupted, or replaced by a retry), by campaign:
  campaign-v1 18, diagnostics-a1 1, halogen-v1 140, halogen-v1/retries/r01 3, phase9-solvent-library-v1 1, pilot-v1 1, polymer-v1 10, tier2-v1 22.
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
cat orca-calculation-files-promotion-v4.tar.xz.part* > orca-calculation-files-promotion-v4.tar.xz
cat orca-calculation-files-promotion-v5.tar.xz.part* > orca-calculation-files-promotion-v5.tar.xz
cat orca-calculation-files-promotion-v6.tar.xz.part* > orca-calculation-files-promotion-v6.tar.xz
cat orca-calculation-files-promotion-v7.tar.xz.part* > orca-calculation-files-promotion-v7.tar.xz
cat orca-calculation-files-promotion-v8.tar.xz.part* > orca-calculation-files-promotion-v8.tar.xz
cat orca-calculation-files-promotion-v9.tar.xz.part* > orca-calculation-files-promotion-v9.tar.xz
cat orca-calculation-files-promotion-v10.tar.xz.part* > orca-calculation-files-promotion-v10.tar.xz
cat orca-calculation-files-promotion-v11.tar.xz.part* > orca-calculation-files-promotion-v11.tar.xz
cat orca-calculation-files-promotion-v12.tar.xz.part* > orca-calculation-files-promotion-v12.tar.xz
sha256sum -c SHA256SUMS
tar -xJf orca-calculation-files.tar.xz
tar -xJf orca-calculation-files-promotion-v4.tar.xz
tar -xJf orca-calculation-files-promotion-v5.tar.xz
tar -xJf orca-calculation-files-promotion-v6.tar.xz
tar -xJf orca-calculation-files-promotion-v7.tar.xz
tar -xJf orca-calculation-files-promotion-v8.tar.xz
tar -xJf orca-calculation-files-promotion-v9.tar.xz
tar -xJf orca-calculation-files-promotion-v10.tar.xz
tar -xJf orca-calculation-files-promotion-v11.tar.xz
tar -xJf orca-calculation-files-promotion-v12.tar.xz
```

The 10 sets unpack into the same `orca-calculation-files/` tree: the first holds the v2 archive (the CHNO campaign, the halogen tier and the publication sets), each later one the contaminants a release added (amendment A-13; parts are never rewritten).

## Rebuild

`run_export_v2.sh` runs on Euler in `~/opencosmo-export-v2`: `build_export_v2.py` assembles the surfaces from
`contaminants-v2.tsv` (written with `CLASSIFICATION.tsv` and `PUBLICATION_SETS.tsv` by `make_contaminants_tsv.py` from
the promotion-v2 release and the publication tier's verified runs, `publication-v1`), then `run_pack.sh` packs these files and `verify_orca_archive.py` checks that every released
surface has its folder and that no licensed starting geometry leaked.
