# Contaminant resolved-panel results — validation in progress

Original campaign: **5,803 accepted / 21 failed / 0 running / 0 not run, denominator 5,824**. The nine isotope-labelled inputs remain excluded from the original 5,833. All 5,803 accepted molecules have now been evaluated against the 32 resolved solvent references at 298.15 K under openCOSMO-RS 24a. This includes water, giving 31 available non-self solvent comparisons. Generic xylene is a separate unresolved requested identity, so the full requested panel remains incomplete.

## Results and missingness

| Export state | Rows |
|---|---:|
| Predicted solvent/water coefficients | 179,752 |
| Generic xylene identity unresolved | 5,803 |
| Numerical dilution-convergence failures | 141 |
| ORCA/preparation failure rows (21 × 32) | 672 |
| Total requested rows (5,824 × 32) | 186,368 |

Of the 179,752 predicted mole-fraction partition coefficients, 173,958 have documented molar-volume corrections for concentration-based coefficients. The other 5,794 are diphenyl-ether predictions without a documented volume correction; that field remains blank. No unavailable molecule/solvent result was interpolated. The 141 activity failures affect 26 contaminants; all are bounded dilution nonconvergence, retained in [activity-failures.csv](activity-failures.csv). Activity-attempt denominator: 5,803 × 32 = 185,696.

The aggregate CSV is `/mnt/r/plastchem-euler/thermodynamics-v1/partitioning-current.csv`. Its verified SHA-256 is `9f2a77825ee4ecdd077bf3c6be146728bd6fea4f9c0996d9a935860faa0bf486`. The independent aggregate check verified every target/pair exactly once, finite predicted values, blank unavailable coefficients, temperature/parameterization, and the arithmetic sign of the concentration correction. Evidence: `/home/aaltamimi2/plastchem-euler/state/final-panel-csv-verification-20260921.json`.

## Validation status

All 5,803 accepted ORCA surfaces have prior surface-integrity audit coverage. All 5,803 validation-only octanol predictions were separately audited. The full current resolved-panel numerical/provenance seal is RUNNING at `/mnt/r/plastchem-euler/audits/final-resolved32-20260921`; no final seal or completion is claimed until its receipt exists and passes.

Independent experimental comparison remains the previously verified 1,179-molecule set: MAE 0.696913, RMSE 1.022563, bias +0.549662 log units; predicted-on-measured slope 0.949545 and intercept 0.673839. Its per-row sources, predictions, outliers and 300-dpi parity plot remain at `/mnt/r/plastchem-euler/validation-final5803-2026-09-17/`. This measured comparison is separate from the four workstation-anchor implementation check. No recalibration was applied. Dry-octanol predictions, experimental water-saturated octanol, and uncertain high-logKow references remain qualifications. Earlier released packages are untouched.

## Separate ongoing work

Tier 2 denominator is 270, never added silently to the original 5,824. Its first 30 executed tasks yielded 27 accepted, one completed-DFT connectivity failure and two abnormal COSMORS terminations; four additional preparation failures did not run ORCA. The other 236 tasks remain held under A-5. All 27 accepted returns have completed serial panel processing: 837 non-self predictions, zero activity failures, with xylene explicitly unresolved. Results are under `/mnt/r/plastchem-euler/tier2-v1/thermodynamics/`. The numerical seal passed 27/27, its 61 artifacts were independently rehashed, and the separate surface/geometry audit passed 27/27. Receipts: `state/tier2-first27-panel-audit.json`, `state/tier2-first27-panel-independent-rehash.json`, and `state/tier2-first27-surface-audit.json` in the workspace. These are subset results, not completion of all 270 tier-2 entries.

A-5 polymer arrays 65676 (244 body conformers) and 65677 (40 large conformers) are separate from contaminant counts. The body was released at 2026-09-22T02:39:16Z, with cap 64. The large array and remaining tier-2 tasks stay held. At 22:02 CDT the body limits were reduced in place to 3 hours for PE and 28 hours for the other body conformers, with 4 GB unchanged. The first 17 PE tasks started on euler144 at 22:02:35 CDT; the scheduler readback is `state/polymer-v1/post-walltime-start-check.json`. No polymer-contaminant partition coefficients or product served-number changes are authorised.

## Remaining items

- Finish and verify the all-5,803 panel audit and immutable result seal; regenerate final overview figures from that exact snapshot.
- Remaining 236 tier-2 tasks are held by A-5; their results do not exist. The accepted 27-molecule subset is exported and audited separately.
- Owner questions remain generic xylene identity and didecyl-phthalate experimental source. No unilateral choice was made.
- The 69-solvent/composition/temperature grid and engine integration remain a specification, not a launched full-grid calculation.

## Reproduction

Original calculation: `/home/aaltamimi2/plastchem-euler/scripts/watch_thermodynamics.py` (completed pass, serial slot handed to tier 2).

Aggregate export: `/home/aaltamimi2/plastchem-euler/scripts/export_thermodynamic_table.py`.

Full audit: `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/audit_final_resolved_panel.py --output /mnt/r/plastchem-euler/audits/final-resolved32-20260921` (already running; do not launch a duplicate into the same directory).

Tier-2 processing/export: `/home/aaltamimi2/plastchem-euler/scripts/watch_tier2_thermodynamics.py` and `/home/aaltamimi2/plastchem-euler/scripts/export_tier2_thermodynamic_table.py`.

## Updated overview figure

[300-dpi PNG](contaminant-panel-overview.png) · [PDF](contaminant-panel-overview.pdf) · [row identities](overview-contaminant-rows.csv) · [matrix CSV](overview-matrix.csv). Every accepted original-campaign molecule appears exactly once, grouped by atom count including H; DEP is shown above. Colour denotes log10 Kx, not concentration-based logKow. 179,752 predicted cells; 5,944 unavailable cells (5,803 unresolved xylene + 141 dilution failures). All text black, uniform 12 pt; spacing visually checked. Workspace PNG: `/home/aaltamimi2/plastchem-euler/contaminant-panel-overview-2026-09-21.png`.
