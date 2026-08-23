# COSMO-RS partition coefficients — running the pipeline from a clone

Reproduces the per-solvent logD our contaminant corpus reports, from SMILES.
Library: `src/dissolve/cosmo_logp.py`. Tests: `tests/test_cosmo_logp.py`.

The two external programs are **not** in this repository — ORCA is a ~17 GB
licensed download and openCOSMO-RS is a source build. Everything else needed to
drive them is here.

## What you must install yourself

**ORCA ≥ 6.0** — <https://orcaforum.kofo.mpg.de>, free for academic use,
registration and EULA required, so it cannot be scripted.
**6.0 is a hard floor: 5.x has no `COSMORS` keyword.**

```bash
tar -xf orca_6_*_linux_x86-64_shared_openmpi418_avx2.tar.xz -C ~/software
export ORCA_BIN=~/software/orca_6_1_1_linux_x86-64_shared_openmpi418_avx2/orca
export LD_LIBRARY_PATH="$(dirname "$ORCA_BIN"):$LD_LIBRARY_PATH"
"$ORCA_BIN" --version
```

**openCOSMO-RS (Python)** — no PyPI package; install from source into an
**isolated** environment:

```bash
python3 -m venv ~/.venvs/cosmo-logp
~/.venvs/cosmo-logp/bin/pip install numpy scipy pandas plotly matplotlib duckdb rdkit
~/.venvs/cosmo-logp/bin/pip install git+https://github.com/TUHH-TVT/opencosmorspy.git
```

Isolated on purpose: this pipeline's NumPy and RDKit requirements conflict with
the main environment, which carries a `numpy==1.26.4` pin. The driver scripts
load `cosmo_logp` **by file path**, not via `import dissolve`, so they run in
that minimal venv without the package's langchain dependencies.

**Open Babel** (`obabel`) for the identity check. `apt install openbabel` or conda.

## Two known environment traps

**MPI.** The `shared_openmpi418` ORCA build needs `libmpi.so.40`. If your system
MPI is MPICH it is absent and `%pal` dies in *Startup* with an error that never
mentions MPI. These scripts run **serial** and parallelise across molecules
instead; the molecules are small enough that this costs little.

**`%maxcore` is per process, not per job.** `1500` with 4 processes is ~6 GB.

## Running it

```bash
REPO=$(git rev-parse --show-toplevel); cd "$REPO"
PY=~/.venvs/cosmo-logp/bin/python

# 1. conformers — every one is identity-checked against the target InChIKey
$PY scripts/cosmo/make_conformers.py --tag dep --outdir ~/cosmo-work/conf

# 2. DFT + COSMO surface, one call per molecule -> .orcacosmo
for tag in dep water methanol dcm hexane cyclohexanol; do
  $PY scripts/cosmo/run_orca_stage.py --xyz ~/cosmo-work/xyz/$tag.xyz \
      --tag $tag --outdir ~/cosmo-work/stage1
done

# 3. delta logD, scored against the anchor pairs   (add --conformer-dir for the ensemble)
$PY scripts/cosmo/compute_delta_logd.py --artifacts ~/cosmo-work/stage1 \
    --solute dep --out ~/cosmo-work/stage1.json

# 4. the full-table regression and its figure
$PY scripts/cosmo/regress_all_solvents.py --cosmo-dir <COSMObase dir> --out fits.json
$PY scripts/cosmo/plot_fits.py --fits fits.json --out fits.png
```

Step 4 uses COSMObase Turbomole `.cosmo` files, which already cover 31 solvents;
the ORCA route needs one DFT run per solvent. Lower accuracy, far wider coverage
— that is the trade, and it is why the two routes are separate scripts.

`compute_delta_logd.py` **exits 2** when the accept test fails, so it can gate a
pipeline rather than only print.

## Expected output

Against the rescued artifacts in `~/cosmo-artifacts` (see its README):

| | DCM−water | cyclohexanol−water | hexane−water | DCM−methanol |
|---|---:|---:|---:|---:|
| single conformer | 5.31 | 3.79 | 3.36 | 1.45 |
| 6-conformer ensemble | 5.34 | 3.86 | 3.46 | 1.44 |
| our table | 4.81 | 2.92 | 2.41 | 1.79 |

Full-table regression, 31 solvents: slope **0.774** [0.655, 0.893]; dropping
chloroform **0.928** [0.819, 1.037]; also dropping water **0.946** [0.778, 1.114].

**Slope 1 is what a single constant reference phase predicts.** The all-31 fit
excludes it; both chloroform-free fits contain it. One solvent is the entire
difference between "these are different quantities" and "the same quantity plus
a constant".

## Three things that are easy to get wrong

**Level of theory is not a free choice.** openCOSMO-RS 24a is fit to
BP86/def2-TZVP(-f)//def2-TZVPD. A different functional or basis produces a
perfectly valid σ-profile the parameterisation never saw, **nothing raises**,
and the number is wrong by an unbounded amount.

**Identity must bind on InChIKey, never formula or mass.** Diethyl phthalate,
isophthalate and terephthalate are all C12H14O4 at 222.24 g/mol. Check again
*after* optimisation — an optimiser that closes a ring has changed the molecule.

**Do not tune settings until the number matches.** Conformer count, energy
window, functional, cavity radii and the anchor set are frozen before the first
ORCA job. Adjusting them after seeing a residual is fitting to the answer.
