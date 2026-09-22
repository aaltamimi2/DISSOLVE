# PE route comparison — A-6

Routes differ simultaneously in QC engine, parameterisation and re-optimised geometry. This measures the entire route; no parameterisation-only attribution is possible.

## Direct comparison with the existing implementation

Start with [existing-convention-comparison.csv](existing-convention-comparison.csv): 128/128 paired predictions, four anchors × 32 matched solvents, no calculation failures. It preserves the existing product’s unnormalized ensemble and five-entry volume table/cavity fallback on both routes. The separate [normalized comparison](route-comparison.csv) uses normalized Boltzmann weights, converged dilution samples and the broader documented campaign volume table. These are explicitly different aggregation/volume conventions, not a recalibration. Both CSVs include the mole-fraction basis.

| Solute | n | Slope B on A | Intercept | Residual SD (log units, n−2) | Sign changes |
|---|---:|---:|---:|---:|---:|
| all | 128 | 0.984767 | -0.246916 | 0.437995 | 9 |
| DEP | 32 | 0.830036 | -0.089954 | 0.387258 | 2 |
| DBP | 32 | 0.928793 | -0.133580 | 0.375231 | 1 |
| BBP | 32 | 0.950782 | -0.236790 | 0.442973 | 1 |
| DEHP | 32 | 1.063725 | -0.406128 | 0.476781 | 5 |

Largest existing-convention differences (B − A, log units):

- DEHP / water: -2.235871 (A=-8.202146, B=-10.438016).
- BBP / chloroform: -1.922346 (A=4.194047, B=2.271701).
- DEP / chloroform: -1.857604 (A=3.892421, B=2.034817).
- DEHP / chloroform: -1.815934 (A=4.353305, B=2.537371).
- DBP / chloroform: -1.619150 (A=3.985167, B=2.366017).

Sign-changing solvents: 1,2-propanediol, acetylacetone, cyclohexane, cyclohexanol, dimethyl sulfoxide, heptane, hexane, methanol, tert-butanol. Per-solute membership is recorded in existing-convention-summary.json.

Ensemble: Route A needs 7/31 conformers for 90% of the weight; Route B needs 6/31. Five re-optimisation merges leave 26 representatives, but **all 31 members retain their source identity and participate in both comparisons**. The CSV `pe-ensemble.csv` reports both surface-energy weighting and the gas-phase OPT sensitivity. `pe-opt-energies-and-merges.csv` and `pe-merge-summary.json` supply each merged-into reference, RMSD and energy difference.

31 identical source conformer identities on both routes; all 31 retained, including the five ORCA optimisation merges reported in ../pe-ensemble-20260921/summary.json. Temperature 298.15 K. Four phthalates × 32 exact matched solvents = 128 comparisons. Generic xylene is unresolved and outside the 32 resolved references; no resolved reference was dropped.

Primary ensemble: normalized Boltzmann weights from each route’s COSMO corrected/solute electronic energies, with ln gamma_PE = −log(sum(w_i exp(−ln gamma_i))). No vibrational free energies or degeneracy correction. Volumes use the documented campaign table when available; otherwise route-specific cavity volume × 0.602214076 cm³/mol per Å³. PE volume is the Boltzmann-weighted cavity volume. Per-phase sources are recorded in the CSV and inputs.json. The concentration basis is logP_x + log10(V_PE/V_solvent).

The existing product helper uses an **unnormalized** conformer partition sum and x=1e−5. Its exact convention is supplied in the legacy_unnormalized_x1e5 columns, rather than silently equated with the normalized Boltzmann average. Production dilution checks use 1e−5 through 1e−8 with a 0.005-log-unit plateau criterion; raw samples are retained. Route B also supplies gas-phase OPT-energy weighting as a sensitivity column, consistent with the earlier PE ensemble report’s energy basis.

all: n=128; B-on-A slope 0.980629, intercept -0.269386, residual SD (n−2) 0.435220; 5 sign-changing pairs across 5 solvents.

DEP: n=32; B-on-A slope 0.820341, intercept -0.046619, residual SD (n−2) 0.380391; 0 sign-changing pairs across 0 solvents.

DBP: n=32; B-on-A slope 0.923711, intercept -0.134046, residual SD (n−2) 0.371197; 1 sign-changing pairs across 1 solvents.

BBP: n=32; B-on-A slope 0.945382, intercept -0.243697, residual SD (n−2) 0.439143; 0 sign-changing pairs across 0 solvents.

DEHP: n=32; B-on-A slope 1.061892, intercept -0.461645, residual SD (n−2) 0.475394; 4 sign-changing pairs across 4 solvents.

Largest disagreements and every sign-changing solvent are named in summary.json. The 31 relative energies and weights are in pe-ensemble.csv; 90%-weight counts are {"A": {"n90": 7, "partition_sum_relative_to_minimum": 3.0928969669380826, "energy_definition": "Gaussian COSMO corrected total energy from source mcos"}, "B": {"n90": 6, "partition_sum_relative_to_minimum": 2.967276370143377, "energy_definition": "ORCA COSMORS solute energy from surface"}, "B_OPT": {"n90": 6, "partition_sum_relative_to_minimum": 3.0255279295202393, "energy_definition": "ORCA gas-phase OPT electronic energy; sensitivity basis"}}.

Failures: [].

Reproduce with `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/compare_pe_routes_a6.py`. Activity files are resumable and input-hash bound. No catalog writes, threshold, product changes, or empirical recalibration.

## Exact existing-product convention

`existing-convention-comparison.csv` uses the unmodified read-only product `compute_log10_p_solvent_over_polymer` for route A, supplied with the calculated x=1e−5 activity coefficients. Its five-entry volume table and cavity fallback are preserved. Route B applies the same aggregation and volume-selection convention to the ORCA surfaces. This is the direct comparison to the existing implementation; `route-comparison.csv` separately provides normalized weights, dilution-checked activities and the larger documented campaign volume table. Their numbers must not be silently interchanged.

all: n=128, slope=0.984767, intercept=-0.246916, residual SD=0.437995; sign changes=9 pairs across 9 solvents.

DEP: n=32, slope=0.830036, intercept=-0.089954, residual SD=0.387258; sign changes=2 pairs across 2 solvents.

DBP: n=32, slope=0.928793, intercept=-0.133580, residual SD=0.375231; sign changes=1 pairs across 1 solvents.

BBP: n=32, slope=0.950782, intercept=-0.236790, residual SD=0.442973; sign changes=1 pairs across 1 solvents.

DEHP: n=32, slope=1.063725, intercept=-0.406128, residual SD=0.476781; sign changes=5 pairs across 5 solvents.

Named differences and sign changes are in existing-convention-summary.json. Reproduce with `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/pe_a6_existing_convention.py` after the main comparison.
