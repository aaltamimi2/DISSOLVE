# Contaminant progress — 14 September 2026

Report generated 2026-09-15T01:48:53.784515+00:00. ORCA cohort cutoff: **20:30 CDT, 14 September 2026**.
Capture began 2026-09-15T01:30:00.000875+00:00 and ended 2026-09-15T01:34:26.447861+00:00.
Effective local collector cutoff: 2026-09-15T01:30:00.054810+00:00; collector resumed 2026-09-15T01:30:00.117643+00:00.
Cohort snapshot: `a7516b00de024dec5c1dbd3f39b50491643cc5dce172ad87b5044b5a30c25d3e`. Numeric records sealed 2026-09-15T01:40:50.922129+00:00.
This package uses a fixed report cohort; it is not completion of the full campaign.

The scope reconciles as **5,833 pinned structures − 9 excluded isotopologues = 5,824 eligible**.
The nine exclusions and their reasons appear in `excluded-isotopologues.csv`; no parent mapping is made.

| ORCA disposition | Count / eligible denominator |
|---|---:|
| awaiting verification | 0 / 5,824 |
| converged | 648 / 5,824 |
| failed | 4 / 5,824 |
| not yet run | 5,140 / 5,824 |
| running | 32 / 5,824 |

Counts come from frozen locally returned records; the retained scheduler snapshot may precede the cutoff by a monitor interval.
Later ORCA returns belong to the next batch. The six support solvents are a separate denominator: **6/6 converged, 0/6 failed**.
The identity rule is connectivity-first-block matching, with full perceived keys and agreement engines retained in `campaign-dispositions.csv`
and `figures/computed-partition-values.csv`. No missing molecule is interpolated.

## Validation-only octanol/water comparison

The initial **590-molecule rehearsal cohort** has **590/590 octanol/water predictions**,
with 0/590 failed and 0/590 unprocessed.
This additional validation solvent does not change the original 32-pair product panel.
The octanol reference reuses campaign task `54798_3677` (`57269`), completed in 5 min 31 s on Milan.
It ran with the original 24-hour allocation and completed before the later requested 4-hour override could be applied.
No duplicate DFT was submitted. The exact frozen OPT and COSMORS recipe, one CPU and 4 GB were retained.
Main 01 (`54786`) was released from its dependency at throttle 24, then raised to 27 after octanol finished.
The verified bound at that change was one remaining Main 00 task + 27 Main 01 tasks + four tail tasks = 32.
The tail was unchanged. The octanol task's sibling dependency was verified unchanged.
Scheduler before/after records are retained in `state/campaign-v1/main01-overlap-release.json`,
`state/campaign-v1/main01-throttle27.json` and `state/progress-2026-09-14/octanol-release.json`.

PubChem retrieval covered **590/590 InChIKeys**.
There are **29 cited observations for 21 molecules**;
**21 molecules** have a selected best point value.
Raw citations, values, retrieval times and source hashes are retained in
`/mnt/r/plastchem-euler/progress-2026-09-14/sealed-thermodynamics/octanol-validation/experimental-reference-candidates.csv`; the selected values are in `/mnt/r/plastchem-euler/progress-2026-09-14/sealed-thermodynamics/octanol-validation/best-measured-logKow.csv`.
The primary statistics use one selected value per molecule, not every duplicate observation.
HSDB/Hansch literature values and Sangster curated experimental values retain distinct source-class labels;
Sangster may include logD-to-logP adjustment or consensus processing. No XLogP3 or explicitly modelled value is admitted.

**Matched n = 21; MAE = 1.526, RMSE = 1.879,
bias (predicted minus measured) = +1.451 log units.**
These are descriptive database comparisons; sample size, phase conditions and source heterogeneity limit generalisation.
The model uses neutral pure-component references at 298.15 K. Experimental wet phases, pH and temperatures may differ.
The [stearyl-acrylate source](https://doi.org/10.1039/A908863F) reports concentration-dependent partitioning,
and the [bile-acid source](https://pubmed.ncbi.nlm.nih.gov/2280184/) distinguishes protonated and ionized species;
the species assignment of its HSDB value was not independently recovered. These caveats remain in the observation rows.

![Octanol/water experimental parity](/mnt/r/plastchem-euler/progress-2026-09-14/sealed-thermodynamics/octanol-validation/validation/predicted-vs-experimental.png)

n = 21 molecules; MAE = 1.526, RMSE = 1.879, bias (predicted minus measured) = +1.451 log units. Black line: 1:1. One best cited measured value per molecule; phase/temperature differences remain.

| Outlier (absolute residual >1) | Measured | Predicted | Residual |
|---|---:|---:|---:|
| Benzyl butyl phthalate | 4.730 | 6.008 | +1.278 |
| Dioctyl phthalate | 8.100 | 10.434 | +2.334 |
| Dibutyl Phthalate | 4.500 | 5.649 | +1.149 |
| Didecyl phthalate | 9.050 | 12.566 | +3.516 |
| Bis(2-ethylhexyl) phthalate | 7.600 | 9.964 | +2.364 |
| Deoxycholic Acid | 3.500 | 6.517 | +3.017 |
| Acetyl tributyl citrate | 4.920 | 8.470 | +3.550 |
| Bis(2-ethylhexyl) adipate | 8.100 | 9.191 | +1.091 |
| 2-Ethylhexyl laurate | 8.030 | 10.203 | +2.173 |
| Methyl arachidate | 9.300 | 10.404 | +1.104 |
| Methyl erucate | 9.320 | 11.100 | +1.780 |
| Stearyl acrylate | 7.100 | 10.599 | +3.499 |

All predicted values and identity/provenance fields are in `/mnt/r/plastchem-euler/progress-2026-09-14/sealed-thermodynamics/octanol-validation/validation/all-predicted-logKow.csv`;
the matched table is `/mnt/r/plastchem-euler/progress-2026-09-14/sealed-thermodynamics/octanol-validation/validation/best-measured-parity.csv`.
The audit checks each result's surface hashes, connectivity, dilution plateau, finite output and conversion arithmetic.
Water activities are preserved from the verified rehearsal records. This is not an independent rerun of the water solver.
Octanol's molar volume uses a [measured density at 298.15 K](https://trc.nist.gov/ThermoML/10.1021/je100170v.html),
with the source, uncertainty and second experimental density check retained in `provenance.json`.

The original product-panel experimental comparison below is separate from this octanol validation.

## Original product-panel partitioning and validation

The sealed values contain **3,888 solvent/water predictions for 648 molecules**
out of 648 frozen ORCA completions. The requested panel is 32 solvent/water pairs per molecule;
only pairs with available, verified solvent references are reported. Neutral pure-component reference states at 298.15 K
are used. These are concentration-ratio partition predictions, not pH-dependent logD or polymer partition coefficients.
Each solvent activity is calculated separately for solute plus that solvent. The multi-solvent panel is not a
calculation of a mixed-solvent solution or of liquid–liquid phase coexistence. Its transfer ratios alone do not
establish whether two solvent phases coexist, or give partitioning between mutually saturated phases.
The panel follows the existing product solvent keys, but this CHNO campaign does not validate PFAS chemistry or ionisation treatment.
Missing pairs remain absent; per-molecule available counts are explicit in `campaign-dispositions.csv`.
The full `partitioning-frozen.csv` contains all **186,368 requested molecule/pair rows**,
including unavailable pairs with explicit statuses and blank numerical values. It records identities, CPU,
source hashes, volume corrections and phase notes. The smaller plotted-values CSV contains only available values.
The full-table audit checked **186,368/186,368 rows**, including pair uniqueness, explicit missingness,
identity provenance, phase notes and exact numerical agreement with the plotted subset.

Experimental comparison covers **1 molecules / 1 observations**. MAE **1.836**, RMSE **1.836**, bias (predicted minus observed) **+1.836 log units**.
Reference coverage before same-solvent matching: 12 molecules / 24 candidate observations.
Of these, 11 molecules have point-value references; 1 observations are censored bounds and excluded from point-error statistics.
Outliers with absolute residual greater than one log unit: Diethyl Phthalate (chloroform: +1.836)

The sample is small and does not establish accuracy across the campaign. Experimental values are kept separate from
screening proxies and modelled database values. Octanol is absent from the authorised panel: octanol/water references
are retained as unmatched rather than compared with another solvent. Row-level sources, retrieval times and conditions
are in `sealed-thermodynamics/experimental-reference-candidates.csv` and `experimental-validation/reference-match-dispositions.csv`.
DEP's chloroform/water reference is at 298.15 K but uses mutually saturated phases, unlike the pure-solvent calculation.

Sources: [Liang supporting data, observed column only](https://doi.org/10.1021/acs.est.7b01737.s001),
[EPA measured slow-stir phthalates](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=30003VNC.TXT),
[Sprunger chloroform reference conditions](https://digital.library.unt.edu/ark:/67531/metadc155630/m2/1/high_res_d/Man-Pub-488.pdf).
The separate [EPA phenolic benzotriazoles table](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=P1013BCS.TXT)
supplies a measured lower bound, retained with its inequality rather than converted into a point value.
[Perylene's HSDB entry through PubChem](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/9142/JSON?heading=LogP)
cites [Andersson and Schrader's direct partition method](https://doi.org/10.1021/ac9902291);
the entry's temperature and primary numerical table were not independently retrieved.
PlastChem inputs and the inspected product schema provide no qualified experimental partition field.
Targeted local searches found no further qualified experimental dataset; web search failures and rejected modelled values are recorded.

The frozen surface audit passed **648/648** contaminants and **6/6** support solvents.
Checks cover digests, geometry, surface area, finite data, CPU, frozen ORCA version and identity provenance.
Stored numerical/provenance checks passed **648/648** sealed results,
including dilution convergence and conversion between mole-fraction and concentration ratios. These are not experimental validation.

Four workstation anchors are reported separately in `sealed-thermodynamics/workstation-anchor-agreement.csv`: Intel Core i7-13700 workstation
solute surfaces versus AMD EPYC 7763 Milan solute surfaces, holding historical solvent references fixed. Sixteen pair
comparisons have maximum absolute difference 0.359691 log units; preparation/geometry and machine effects are confounded.
Historical DBP/BBP/DEHP golden outputs agree in 12/12 saved comparisons; no DEP golden JSON was available.
The unmodified reference `delta_log_d` function and water-cache preservation audits are documented in the accompanying
`sealed-thermodynamics/reference-math-and-water-audit.json`; these check implementation and preservation, not independent chemical accuracy.
Its timestamp and coverage identify the audit checkpoint; it does not imply a new independent solver replay.

The current homogeneous-Milan DCM/water outputs are additionally compared for **4/4 anchors** in
`production-anchor-agreement.csv`. Maximum absolute difference from the historical workstation is
**0.189156 log units**; the difference from the earlier Milan-solute/historical-solvent
comparison is at most **0.003663 log units**. Solvent geometry and dilution stopping
both differ in the latter comparison, so it is not an isolated CPU-effect measurement.

## Figures and timing

All figures use 300 dpi PNG, uniform 14-point black text and adjacent CSV data.

![Experimental parity](/mnt/r/plastchem-euler/progress-2026-09-14/experimental-validation/predicted-vs-experimental.png)

Experimental comparison covers **1 molecules / 1 observations**. MAE **1.836**, RMSE **1.836**, bias (predicted minus observed) **+1.836 log units**. Black line: 1:1. Phase conditions are qualified above; n counts molecules separately from observations.

![Value distributions](/mnt/r/plastchem-euler/progress-2026-09-14/figures/computed-value-distribution.png)

Distributions are separated by solvent, with sample counts; they are computed values, not measurements.

![Campaign progress](/mnt/r/plastchem-euler/progress-2026-09-14/figures/campaign-progress.png)

![Wall time and atom count](/mnt/r/plastchem-euler/progress-2026-09-14/figures/walltime-vs-atoms.png)

Successful measured OPT+COSMORS cost: main body **1193.92 CPU-hours**, >80-atom tail **172.60 CPU-hours**.
Failed records with measured stages add **11.48 CPU-hours**; unfinished runtime is not included in those sums.
Slurm batch-step RAM high-water across 649 records with reported RSS is **4089.9 MiB**, against 4,096 MiB requested.
This is the maximum in the captured accounting records, not a bound on unfinished jobs or the remaining campaign.
All campaign timing measurements are from AMD EPYC 7763 Milan. The overlay is the original Milan pilot mean fit,
drawn only over its sampled 15–80 atom range. Successful timings alone cannot estimate the cost of failures or unfinished jobs.

| Failed molecule | Input key | Failure mode |
|---|---|---|
| Undec-10-ynoic acid, dodecyl ester | `CPUMHCZZUYHHHY-UHFFFAOYSA-N` | geometry_nonconvergence |
| Cubenene | `KWFAQPWLROZBAY-UHFFFAOYSA-N` | ValueError |
| [2.2]Paracyclophane | `OOLUVSIJOMLOCB-UHFFFAOYSA-N` | ValueError |
| Carbon Monoxide | `UGFAIRIUMAVXCW-UHFFFAOYSA-N` | mmff_parameters_unavailable |

The failures are retained with no substitute or automatic retry. Full dispositions and measured times are in CSV.

## Execution, pending work and reproduction

At 15:27 CDT, owner authority removed array 56079's remaining `afterany:54733` dependency.
Readback confirmed no dependency. The transient configured maximum was main 28 + tail 4 + support 4 = 36;
main and tail throttles were unchanged. The six support jobs completed and their monitor exited successfully.
Campaign and serial thermodynamic processing continue, subject to the existing memory guard, multiplexed SSH and backoff.

Xylene identity remains an owner question: the product maps the PFAS label to p-xylene, but approval to adopt that
interpretation is pending. Missing solvent references, unfinished ORCA jobs and later returns are next-batch work.

From `/home/aaltamimi2/plastchem-euler`, after the report cohort has been captured:

```bash
unset PLASTCHEM_PROGRESS_ROOT
python3 scripts/seal_progress_thermodynamics.py
/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_completed_surfaces.py --frozen
/home/aaltamimi2/.venvs/cosmo-logp/bin/python scripts/audit_completed_surfaces.py --frozen --support-solvents
python3 scripts/audit_thermodynamic_records.py --frozen
python3 scripts/plot_progress_20260914.py --frozen
python3 scripts/compare_progress_experiments.py --frozen
python3 scripts/compare_production_anchors.py --frozen
python3 scripts/export_thermodynamic_table.py --frozen
python3 scripts/audit_progress_table.py --frozen
python3 scripts/build_frozen_progress_report.py
```

The seal is a one-time operation that refuses overwrite. Subsequent commands rerun against the pinned snapshot.
After visually inspecting the four final PNGs and reviewing this report, record their SHA-256 hashes and
review outcomes in `final-visual-review.json`, then run `python3 scripts/seal_progress_release.py`.
Verify the released files with `python3 scripts/seal_progress_release.py --verify`.
The resulting `release-manifest.json` identifies the final package and excludes provisional preview outputs.
`sealed-thermodynamics/software-provenance.json` records the Python/dependency versions and source hashes;
`sealed-thermodynamics/software-sources/` preserves the installed scientific code captured during processing.
The installed openCOSMO-RS development package reports version 0.0.1; source hashes distinguish this code revision.
Bulk artifacts are under `/mnt/r/plastchem-euler/progress-2026-09-14`; source data and retrieved documents are in `sealed-thermodynamics/reference-sources/`.
Final report path: `/home/aaltamimi2/plastchem-euler/reports/progress-2026-09-14/REPORT.md`.

## Octanol sign and offset review

No sign or molar-volume unit error was found. No numerical code change, solver rerun or empirical correction was applied.

For DEP, ln(gamma_water) = 10.798508105548, ln(gamma_octanol) = 1.091587995802.
logKx = (ln(gamma_water) − ln(gamma_octanol))/ln(10) = 4.215661839938.
log10(V_water/V_octanol) = log10(18.07/158.5283018867925) = -0.943148655040.
logKconc = 3.272513184898; stored and reference-function results agree to 1e-12.

Derivation: at equilibrium x_octanol/x_water = gamma_water/gamma_octanol; c = x/V_m, so c_octanol/c_water = (x_octanol/x_water)(V_water/V_octanol). Both molar volumes are in cm³/mol, so their ratio is dimensionless. The stored activities are natural logarithms, divided by ln(10) once.

All 590/590 stored predictions agree with independent 40-digit decimal arithmetic and the unmodified reference delta_log_d function (A = octanol, B = water).
Reference SHA-256: `49ad08d13ae0bc0368188192f87312598a229ec35feeed5f011b9d963569e766`.

Predicted = 1.102869 × measured + 0.823937; residual = 0.102869 × measured + 0.823937 (n = 21).
MAE 1.525749; RMSE 1.879302; bias +1.450602; centered residual RMS 1.194792. Bias squared accounts for 59.6% of MSE. This is not a scatter-free constant offset; no regression correction was applied.

| Anchor | Name | Measured logKow | Predicted logKow | Residual | Citation |
|---|---|---:|---:|---:|---|
| DEP | Diethyl Phthalate | 2.470 | 3.273 | +0.803 | [Hansch, C., Leo, A., D. Hoekman. Exploring QSAR - Hydrophobic, Electronic, and Steric Constants. Washington, DC: American Chemical Society., 1995., p. 101](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/6781/JSON?heading=LogP) |
| DBP | Dibutyl Phthalate | 4.500 | 5.649 | +1.149 | [Ellington JJ, Floyd TL; Octanol/water partition coefficients for eight phthalate esters. EPA/600/S-96/006, Sept. 1996. Athens, GA: US Environ Prot Agency, National Exposure Research Lab (1996)](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/3026/JSON?heading=LogP) |
| BBP | Benzyl butyl phthalate | 4.730 | 6.008 | +1.278 | [Ellington JJ, Floyd TL; Octanol/water partition coefficients for eight phthalate esters.  EPA/600/S-96/006, Sept. 1996; Athens, GA: USEPA, National Exposure Research Lab (1996)](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/2347/JSON?heading=LogP) |
| DEHP | Bis(2-ethylhexyl) phthalate | 7.600 | 9.964 | +2.364 | [Debruijin J et al; J Environ Toxicol Chem 8: 499-512 (1989)](https://pubchem.ncbi.nlm.nih.gov/rest/pug_view/data/compound/8343/JSON?heading=LogP) |

The full parity CSV now names and flags all four anchors; source URLs, raw citations and retrieval times remain in every row. The original published comparison and its hashes are preserved in the octanol-validation directory.

Reproduce: `python3 scripts/check_octanol_sign_20260914.py`. Artifacts: `/mnt/r/plastchem-euler/progress-2026-09-14/octanol-sign-review`.

## Hydrophobicity-dependent residuals and limits

No recalibration was applied. For the 21 matched molecules, predicted logKow = **1.103 × measured + 0.824**; residual = **0.103 × measured + 0.824**. The slope standard error is 0.099, so the small heterogeneous sample does not establish that the population slope exceeds one. This fitted trend is descriptive, not a correction or an attribution of cause.

The ten molecules with measured logKow >7 all have positive residuals, averaging **+1.898**. The six below 4 have bias **+0.912**, MAE **0.950**, and only **2/6** within ±0.3. Low-logKow agreement is better on average than the high group, but is not uniformly good: DEP is +0.803 and deoxycholic acid is +3.017. The latter retains its pH/speciation qualification; it is not removed from the primary n=21 statistics. The point estimates do not imply a monotonic increase for every molecule.

Two potential contributors remain unresolved. First, the model uses **dry, pure-component octanol and water references**, whereas standard experimental logKow uses **mutually saturated phases**. Second, high-logKow measurement is difficult: octanol microdroplets in sampled water can increase apparent aqueous concentration and lower apparent logKow in shake-flask measurements. Slow stirring addresses this artifact; high measured values should not be dismissed automatically. OECD reports successful slow-stir determinations up to logKow 8.2. We have not calculated the wet-phase correction or determined which contributor dominates. [OECD Test Guideline 123](https://doi.org/10.1787/9789264015845-en)

The EPA phthalate report documents a broad historical DEHP literature range (5.11–9.61), alongside its own measured slow-stir value 7.27. That heterogeneous range is not a universal ±1 uncertainty, nor proof of a particular slow-stir versus generator-column discrepancy for every row. The selected PubChem DEHP value remains 7.60 from its cited 1989 source, with the EPA value retained separately. Every parity row retains its source URL and raw citation; method, phase and speciation are not inferred where missing. [EPA phthalate measurements](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=30003VNC.TXT)

Reproduce with `python3 scripts/review_octanol_hydrophobicity.py`. Group membership, individual residuals and unchanged source citations are in `residuals-by-measured-range.csv` beside this review. The four named anchors remain in the sign-review parity tables.

A source discrepancy remains for didecyl phthalate: PubChem lists 9.05, while the cited [primary abstract](https://doi.org/10.1021/je990149u) reports slow-stir 8.83 ±0.05. Full-text access failed, so the difference is unresolved. The primary n=21 comparison is preserved. Substituting only the abstract's measured value as an explicit sensitivity analysis gives MAE 1.536, RMSE 1.899, bias +1.461; predictions are unchanged. This is a source-selection sensitivity, not recalibration.
