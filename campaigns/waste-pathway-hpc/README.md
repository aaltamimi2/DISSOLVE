# Waste-pathway HPC campaign (TEA/LCA): data, analysis and main figure

First-pass sensitivity campaign for `optimize_waste_pathway` and `tea_tornado`: 641,226 BioSTEAM runs on Euler
(UW-Madison), 2026-10-11/12. This folder holds everything needed to redo the analysis and redraw the manuscript figure
from the tables, and to rerun the campaign after the model changes.

This lives on the branch `tea-lca-campaign-results` (main plus this folder). It is not on `main`: the tables are about
320 MB and the results are a draft under review.

## Layout

| Path | What |
|---|---|
| `data/` | The collected tables (Parquet, zstd). `data/feed_candidates/` is one table in six parts |
| `data/SHA256SUMS.txt`, `data/DATA_INDEX.json` | Hash and row count of every table |
| `manifests/` | `CAMPAIGN.json`, `CAMPAIGN.v2.json` (every range, grid, count and the seed; v2 governs `sobol`, `mc`, `oatfix`), `systems.json` (the 34 wash systems) |
| `bin/` | The campaign scripts: manifests, runners, the warm TEA worker, collection |
| `euler/` | SLURM job scripts, the environment file and the package lists of both environments |
| `analysis/results_numbers.py` | Recomputes every number quoted in the Results text; `results_numbers.txt` is its output on these tables |
| `figures/main-figure/` | One script that draws the manuscript figure from the tables, its assets, a reference copy and a verifier |

## Quick start: numbers and figure from the tables

```bash
pip install duckdb
python campaigns/waste-pathway-hpc/analysis/results_numbers.py            # manuscript basis: resale excluded
python campaigns/waste-pathway-hpc/analysis/results_numbers.py --resale   # with residual resale allowed

cd campaigns/waste-pathway-hpc/figures/main-figure
pip install -r requirements.txt
python make_figure.py --data ../../data --out out
python verify.py --out out        # compares with reference/
```

`figures/main-figure/README.txt` lists the settings (wash system, feed, resale exclusion, axis limit, labels) and what
each panel reads. To redraw after a model update, rebuild the tables (below) and point `--data` at them.

Read a table with DuckDB or pandas. `feed_candidates` (11.6 million rows) should be queried, not loaded:

```python
import duckdb
duckdb.sql("select count(*) from read_parquet('data/feed_candidates/*.parquet')")
```

## Code version

The runs used the trial merge of `waste-pathway-optimization` onto DISSOLVE main (local commit `b1930168`), with the
private `plastics` package under `vendor/plastics`. That commit is not on the remote. Against this branch's base
(`main` at `2cf82c6b`) the modules that produce the numbers are unchanged: `tea.py`, `tea_worker.py`,
`waste_pathway_milp.py` and the stored TEA and solubility data are identical, and `waste_pathway.py`, `tea_tornado.py` and
`waste_pathway_data.py` differ only by the deployment switches added in `2cf82c6b` (a cap on live simulations and a
figures switch, both off unless their environment variables are set). So this branch reproduces the campaign.

A normal cold TEA run and the campaign's warm run agree to about 15 significant digits.

## Blocks

| Block | What was varied | Runs in the tables | Successful |
|---|---|---|---|
| `feeds` | 16 polymer families (every 3-, 4- and 5-polymer subset of LDPE, HDPE, PET, NYLON6, EVOH), 906 compositions on a 10% grid, 4 plants, energy cases C1 to C3, up to 3 washes, 6 solvents per stage, downstream locations A and B: 10,872 scenarios | 197,952 | 194,592 |
| `mc` | The published feed (60/20/10/10 LDPE/PET/NYLON6/EVOH, 8,000 t/yr) and a second feed (40/25/20/15 HDPE/LDPE/PET/EVOH, 12,000 t/yr), dedicated and shared 20,000 t/yr plant, 300 common Latin-hypercube draws of 13 process and economic parameters, a solvent-price factor and five resin prices, plus the base: 1,204 scenarios | 34,314 | 33,110 |
| `surface` | 34 wash systems over target share 5 to 95% (19 values), capacity 1,000 to 200,000 t/yr (8 values), C1 to C3 | 15,504 | 14,744 |
| `oat` + `oatfix` | All 17 tornado parameters low and high, one at a time, at 12 base plants per system and energy case | 43,248 | 39,948 |
| `sobol` | Saltelli design (A, B, AB_i), N = 1,024, 17 factors, the 18 most frequent wash systems, C1 | 350,208 | 350,133 |

Plants: `ded5k`, `ded20k`, `ded50k` (a plant for this feed alone); `shared20k` (8,000 t/yr of this feed in a
20,000 t/yr plant, charged its throughput share of the costs). Downstream keys: `lf` landfill, `we` incineration with
energy recovery, `py` pyrolysis, `gas_er` gasification for energy, `gas_h2` gasification for hydrogen, `gas_h2cc` the
same with carbon capture, `resale` (a single-polymer residual sold as resin). Energy cases: C1 on-site boiler and
turbogenerator, C2 grid electricity with no on-site utilities, C3 grid electricity plus on-site boiler.

## Tables

| File | Rows | One row is |
|---|---|---|
| `feed_scenarios.parquet` | 21,744 | A feed scenario at one downstream location: the three optima (route, profit, emissions, circularity), front sizes, normalization bounds |
| `feed_candidates/` | 11,550,720 | One candidate route of one scenario-location: washes, destination, profit, emissions, circularity, front membership |
| `mc_scenarios.parquet` | 2,408 | As `feed_scenarios`, plus the draw (`ovr_*`, `solvent_price_factor`, `price_*`; `draw` is null for the base) |
| `mc_candidates.parquet` | 3,230,332 | As `feed_candidates` |
| `washes_<block>.parquet` | see Blocks | One BioSTEAM run: `ok`, `error`, `wall_s`, the configuration sent (`cfg_*`) and the result row (`msp_usd_per_kg`, `gwp_kg_co2e_per_kg`, `tci_usd`, `aoc_usd_per_yr`, energy and solvent make-up columns) |
| `oat_tornado.parquet` | 102,000 | One base plant, parameter and metric: values at low and high, swing |
| `sobol_indices.parquet` | 1,530 | One system, metric and factor: `S1`, `ST` with 95% bootstrap bounds, `n_used`, the factor's range |

## Assumptions that travel with every number

1. **Residual resale.** The model sells a washed single-polymer residual as pure resin although it still holds the
   unrecovered part of the washed polymers. The manuscript excludes those routes (`downstream_key <> 'resale'`); with
   them, 78% of profit optima rest on resale.
2. **Downstream table.** Incineration and gasification have zero operating cost and pyrolysis earns nothing, so every
   profit optimum without resale ends in energy recovery and every scenario has a profitable route.
3. **Emissions are gate to gate**, with no credit for displaced virgin resin: "no wash, landfill" has the lowest
   emissions in all 21,744 scenario-locations. Wash GWP coverage is partial (`lca_status`).
4. **Solvent recovery is 99.9999% per pass.** `solvent_loss_pct = 0.01` is divided by 100 in `tea_worker.py` and again
   in the plant model. This is the intended assumption. The "0.01 to 1%" range of the tornado and Sobol runs is
   therefore 99.9999% to 99.99% recovery.
5. **Polymer loading is the same for every solvent** (3 wt% base). Solvent identity enters the cost only through
   physical properties and the dissolution temperature, which is why prices span only 1.02 to 1.21 USD/kg across the
   washes at one plant.
6. **Selection is by exhaustive enumeration.** Euler had no MILP solver; `milp_agrees` in the scenario tables compares
   enumeration with itself and is not an independent check.
7. **Operating days.** The tool's default tornado range reaches 385 days and the engine refuses more than 366. The
   campaign caps the range at 366 (`gen_manifests.py`, `ENGINE_BOUNDS`).
8. **Failed runs are data**, never zeros: NMP has no thermophysical properties in the plant model and
   hexamethylphosphoramide fails in two energy cases.
9. **Circularity** is a screening index normalized to bounds set per scenario; compare it within a scenario only.
10. Electricity is fixed (0.07 USD/kWh in the plant model's settings) and was not varied; transport distance was varied
    from 0 to 500 km by truck.

## Rerunning the campaign

The raw records (2.7 GB of gzip JSON lines) and the shard manifests (0.9 GB) stay on Euler under
`/srv/home/aaltamimi2/dissolve-hpc/` and are regenerated by the steps below; they are not in git.

1. **Code.** A checkout of this branch (or of the updated model) with `vendor/plastics` in place, at `$ROOT/code`.
2. **Environments** (`euler/env.sh` shows the variables): `envs/main` (Python 3.11, `euler/main-env-freeze.txt`) runs
   DISSOLVE; `envs/tea` (Python 3.12, `euler/tea-env-freeze.txt`) runs BioSTEAM. Install with `PYTHONNOUSERSITE=1`, or
   pip skips packages it finds in `~/.local`. `TEA_REAL_PYTHON` must point at the TEA interpreter.
3. **Wash systems.** `python bin/screen_families.py > systems.json` (the thermodynamic screen over the families).
4. **Manifests.** `python bin/gen_manifests.py systems.json manifests` writes one directory of shards per block and
   `CAMPAIGN.json`. Seed 20261011; the same inputs give the same manifests. `--only sobol,mc --summary NAME.json`
   regenerates chosen blocks.
5. **Run.** One array job per block, one shard per task (`euler/job.sh BLOCK`, 1 CPU, 4 GB):
   `sbatch --array=0-452 job.sh feeds`, `0-75 mc`, `0-6 surface`, `0-16 oat`, `0-140 sobol`. A killed shard resumes
   where it stopped. `run_shard.py` and `run_feeds.py` start a fork server that imports BioSTEAM once and forks per run
   behind the product's own `tea.evaluate_process` (`tea_forkserver.py`, `tea_python_forked`): 1.2 to 3 s per run
   against about 26 s cold.
6. **Collect.** `sbatch euler/collect.sh analysis` (48 GB) runs `bin/collect.py results manifests analysis` and writes
   the tables of `data/`, including the tornado and Sobol index tables.
7. **Numbers and figure.** As in the quick start, with `--data` pointing at the new tables.

`bin/mk_probe.py` and `bin/probe_table.py` are the small checks used before the campaign (a few washes; the stage table
of three feeds). `euler/health.py` samples running shards.
