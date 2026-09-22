# Seven-polymer route comparison — A-7

**The routes differ simultaneously in QC engine, parameterisation and re-optimised geometry. This measures the entire route, not a parameterisation-only effect.** No catalog writes, thresholds, product changes or empirical correction.

166 matched source conformers, seven polymers × four anchors × 32 solvent references = **896 paired predictions per convention**, 298.15 K. All 166 rows retained, including optimization merges. **Excluded:** PET, PS, polyethersulfone, nitrocellulose, polyurethane, PC and PETG; their absence is not a negative result. The scope is frozen to the A-7 list even if other polymers finish during processing. Generic xylene remains unresolved, outside the 32-reference intersection.

## Design and conventions

Route A: source Gaussian COSMO converted to Turbomole shape, 2002 default_turbomole; COSMObase solutes/solvents. Route B: ORCA 24a polymer surfaces, four workstation anchor surfaces and campaign solvent references. Each polymer has identical source-conformer identities across routes. Surface corrected/solute electronic energies define normalized Boltzmann weights; the gas-phase OPT weighting is supplied as sensitivity. Both mole-fraction and concentration bases are in the per-polymer CSVs and paired-predictions.csv. logP_conc = (ln gamma_polymer − ln gamma_solvent)/ln(10) + log10(V_polymer/V_solvent).

Normalized convention uses −log(sum(w_i exp(−ln gamma_i))), a bounded dilution check, and the documented campaign molar-volume table with cavity fallback. Existing convention preserves the product’s unnormalized conformer partition sum, x=1e−5, and its five-entry molar-volume table with cavity fallback. Volumes and their sources appear in each polymer’s input and comparison tables. COSMO cavity volume is converted using 0.602214076 cm³/mol per Å³. These conventions must not be silently interchanged.

## Existing convention

| Polymer | n | Slope | Intercept ± OLS SE | Residual SD | Sign changes |
|---|---:|---:|---:|---:|---:|
| evoh | 128 | 0.9778 | -0.3381 ± 0.0445 | 0.4294 | 8 |
| nylon6 | 128 | 0.9775 | -0.1510 ± 0.0424 | 0.4298 | 3 |
| nylon66 | 128 | 0.9783 | -0.0708 ± 0.0431 | 0.4291 | 6 |
| pe | 128 | 0.9848 | -0.2469 ± 0.0409 | 0.4380 | 9 |
| pp | 128 | 0.9824 | -0.2180 ± 0.0391 | 0.4339 | 8 |
| pvc | 128 | 0.9785 | -0.6869 ± 0.0386 | 0.4351 | 44 |
| pvdf | 128 | 0.9781 | +0.4805 ± 0.0521 | 0.4293 | 10 |
| pooled | 896 | 0.9219 | -0.1602 ± 0.0180 | 0.5306 | 88 |

**Offset conclusion: polymer-specific.** 19/21 paired bootstrap intercept contrasts exclude zero; nominal common-intercept F=58.106, p=1.271e-60. See summary.json for every contrast. A single polymer-independent intercept correction is not supported when the polymer-specific conclusion holds. No recalibration was applied. OLS SE/F use nominal independent-residual assumptions. Paired bootstrap preserves matching and anchor strata but this is four anchors, not a representative contaminant sample; no empirical correction applied.

Sign changes: **88/896**. By polymer: {"evoh": 8, "nylon6": 3, "nylon66": 6, "pe": 9, "pp": 8, "pvc": 44, "pvdf": 10}. Both route values within ±0.5 log units: 62; at least one within ±0.5: 88. Solvent concentrations: {"cyclohexane": 6, "cyclohexanol": 4, "dodecane": 1, "hexane": 4, "heptane": 4, "acetic acid": 5, "methanol": 3, "1,2-propanediol": 5, "acetylacetone": 5, "dimethyl sulfoxide": 5, "tert-butanol": 3, "1-propanol": 1, "isopropanol": 1, "2,3-dihydropyran": 2, "2-butanone": 4, "acetone": 3, "benzene": 2, "ethyl acetate": 4, "isopropylamine": 3, "methylacetate": 3, "n,n-dimethylformamide": 3, "o-xylene": 4, "tetrahydropyran": 1, "toluene": 4, "diphenyl ether": 3, "triethylamine": 4, "tetrahydrofuran": 1}. Every triple and both values are in sign-changes.csv.

Ranks put **lowest solvent/polymer logP first**, meaning greatest polymer retention. Across 128 pairs: mean Spearman 0.8661, median 0.8750, minimum 0.7857; mean Kendall 0.7619; top changes **32/128**. Every ranking and top gap is in rank-stability.csv; every changed pair is named in top-polymer-changes.csv. Because a solvent term is shared across polymers, ranks can repeat across solvents for the same anchor: 128 pairs are not 128 independent chemical probes.

Top changes, grouped without omitting any pair:

- DEHP: pvdf → pvc: 1,2-propanediol, 1-propanol, 2,3-dihydropyran, 2-butanone, acetic acid, acetone, acetylacetone, benzene, chloroform, cyclohexane, cyclohexanol, dichloromethane, dimethyl sulfoxide, diphenyl ether, dodecane, ethanol, ethyl acetate, ethylene glycol, heptane, hexane, isopropanol, isopropylamine, methanol, methylacetate, n,n-dimethylformamide, o-xylene, tert-butanol, tetrahydrofuran, tetrahydropyran, toluene, triethylamine, water.

Largest disagreements (B−A, log units):

- pvc / DEHP / water: -2.6587.
- pvc / BBP / chloroform: -2.3332.
- pvc / DEP / chloroform: -2.2761.
- evoh / DEHP / water: -2.2505.
- pvc / DEHP / chloroform: -2.2387.
- pe / DEHP / water: -2.2359.
- pp / DEHP / water: -2.1529.
- pvc / DBP / chloroform: -2.1058.
- nylon6 / DEHP / water: -2.0618.
- evoh / BBP / chloroform: -2.0163.
## Normalized convention

| Polymer | n | Slope | Intercept ± OLS SE | Residual SD | Sign changes |
|---|---:|---:|---:|---:|---:|
| evoh | 128 | 0.9735 | -0.1953 ± 0.0455 | 0.4264 | 4 |
| nylon6 | 128 | 0.9733 | -0.2203 ± 0.0485 | 0.4268 | 3 |
| nylon66 | 128 | 0.9741 | -0.2007 ± 0.0498 | 0.4262 | 0 |
| pe | 128 | 0.9806 | -0.2694 ± 0.0452 | 0.4352 | 5 |
| pp | 128 | 0.9782 | -0.2124 ± 0.0457 | 0.4310 | 4 |
| pvc | 128 | 0.9741 | -0.0804 ± 0.0384 | 0.4322 | 7 |
| pvdf | 128 | 0.9739 | +0.4040 ± 0.0392 | 0.4263 | 33 |
| pooled | 896 | 0.9286 | -0.0777 ± 0.0174 | 0.4729 | 56 |

**Offset conclusion: polymer-specific.** 12/21 paired bootstrap intercept contrasts exclude zero; nominal common-intercept F=32.884, p=7.221e-36. See summary.json for every contrast. A single polymer-independent intercept correction is not supported when the polymer-specific conclusion holds. No recalibration was applied. OLS SE/F use nominal independent-residual assumptions. Paired bootstrap preserves matching and anchor strata but this is four anchors, not a representative contaminant sample; no empirical correction applied.

Sign changes: **56/896**. By polymer: {"evoh": 4, "nylon6": 3, "pe": 5, "pp": 4, "pvc": 7, "pvdf": 33}. Both route values within ±0.5 log units: 53; at least one within ±0.5: 56. Solvent concentrations: {"1,2-propanediol": 4, "dodecane": 5, "acetic acid": 1, "ethylene glycol": 1, "heptane": 2, "acetylacetone": 4, "dimethyl sulfoxide": 6, "ethanol": 3, "isopropanol": 3, "1-propanol": 2, "tert-butanol": 2, "2,3-dihydropyran": 1, "2-butanone": 1, "acetone": 1, "benzene": 1, "ethyl acetate": 2, "isopropylamine": 3, "methylacetate": 2, "n,n-dimethylformamide": 1, "tetrahydrofuran": 1, "tetrahydropyran": 1, "toluene": 3, "diphenyl ether": 2, "o-xylene": 2, "triethylamine": 1, "cyclohexanol": 1}. Every triple and both values are in sign-changes.csv.

Ranks put **lowest solvent/polymer logP first**, meaning greatest polymer retention. Across 128 pairs: mean Spearman 0.9643, median 0.9821, minimum 0.8929; mean Kendall 0.9286; top changes **32/128**. Every ranking and top gap is in rank-stability.csv; every changed pair is named in top-polymer-changes.csv. Because a solvent term is shared across polymers, ranks can repeat across solvents for the same anchor: 128 pairs are not 128 independent chemical probes.

Top changes, grouped without omitting any pair:

- DEHP: pvdf → pvc: 1,2-propanediol, 1-propanol, 2,3-dihydropyran, 2-butanone, acetic acid, acetone, acetylacetone, benzene, chloroform, cyclohexane, cyclohexanol, dichloromethane, dimethyl sulfoxide, diphenyl ether, dodecane, ethanol, ethyl acetate, ethylene glycol, heptane, hexane, isopropanol, isopropylamine, methanol, methylacetate, n,n-dimethylformamide, o-xylene, tert-butanol, tetrahydrofuran, tetrahydropyran, toluene, triethylamine, water.

Largest disagreements (B−A, log units):

- pe / DEHP / water: -2.2547.
- pp / DEHP / water: -2.1496.
- nylon6 / DEHP / water: -2.1326.
- evoh / DEHP / water: -2.1006.
- nylon66 / DEHP / water: -2.0986.
- pvc / DEHP / water: -2.0410.
- pe / BBP / chloroform: -1.9536.
- pp / BBP / chloroform: -1.9248.
- nylon6 / BBP / chloroform: -1.9245.
- nylon66 / BBP / chloroform: -1.9067.

## Ensemble concentration and merges

| Polymer | Conformers | A n90 | B n90 | OPT n90 | Merges | Representatives |
|---|---:|---:|---:|---:|---:|---:|
| evoh | 11 | 3 | 3 | 3 | 0 | 11 |
| nylon6 | 20 | 5 | 5 | 1 | 0 | 20 |
| nylon66 | 28 | 6 | 6 | 3 | 0 | 28 |
| pe | 31 | 7 | 6 | 6 | 5 | 26 |
| pp | 25 | 7 | 7 | 8 | 2 | 23 |
| pvc | 27 | 4 | 6 | 5 | 5 | 22 |
| pvdf | 24 | 16 | 18 | 6 | 5 | 19 |

Merge criteria match A-6: symmetry-aligned heavy-atom RMSD ≤0.10 Å and absolute OPT electronic-energy difference ≤1e−5 Eh; direct comparison to the lowest-energy representative, no transitive chaining. Every merged row, RMSD and energy difference is in optimization-merges.csv; all energies and weights remain in conformer-energies-weights.csv.

normalized: Spearman correlation of conformer count with mean absolute route difference = 0.0714; B n90 with that difference = 0.4818. Conformer count does not show a consistent association with route disagreement in these seven polymers. Seven polymer types are too few and too confounded with chemistry to claim that ensemble size causes route disagreement.

existing: Spearman correlation of conformer count with mean absolute route difference = -0.1429; B n90 with that difference = 0.1853. Conformer count does not show a consistent association with route disagreement in these seven polymers. Seven polymer types are too few and too confounded with chemistry to claim that ensemble size causes route disagreement.

## Reproduction and files

Run `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/run_a7_phased.py`, then `.../scripts/analyze_polymer_comparison_a7.py`. Calculations run one polymer at a time and one solute per fresh process. Each unit atomically writes and flushes 32 rows in each convention before the next begins; completed units are skipped on resume. COSMO objects are deleted and garbage-collected between activities. Unit guard pauses at 1800 MiB RSS; supervisor stops at 1900 MiB RSS or host MemAvailable below 2.5 GiB, retaining completed units. Calculations are input-hash bound and reuse identical A-6 solvent activity records. The original unphased runner is superseded and must not be used. Each polymer folder contains both full comparison CSVs, raw activities, converted inputs and pins. Fits and uncertainty are in fits.csv and summary.json; enumerated flips and rankings are separate CSVs. No experimental-accuracy claim is made by this route-to-route comparison.

PE repeat agreement with A-6: {"route-comparison.csv": {"rows": 128, "max_absolute_difference": 0.0}, "existing-convention-comparison.csv": {"rows": 128, "max_absolute_difference": 4.505373851770855e-11}}. The phased implementation preserves the A-6 numerical result within floating-point arithmetic.
