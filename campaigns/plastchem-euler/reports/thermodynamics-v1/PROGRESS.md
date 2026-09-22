# Contaminant thermodynamic campaign — active

Updated 2026-09-14T05:13:56Z. **Not complete.** The goal includes final thermodynamic partitioning and validation, beyond ORCA completion. Numbers below are dated snapshots; machine records continue updating.

## Current coverage

| Measure | Verified snapshot |
| --- | --- |
| Eligible campaign | 5,833 pinned − 9 explicit isotope exclusions = 5,824 |
| ORCA dispositions | 341 converged; 3 preparation failures; 32 running; 5448 not yet run / 5,824 |
| Main / tail successes | 320/5,735 main; 21/89 above 80 atoms |
| Production activities | 341 contaminants, water only; 0 failed activities at 2026-09-14T05:13:14Z |
| Full production partitioning | 0 contaminants; 0 non-water predictions |
| Homogeneous solvent surfaces | 1/33 ready |
| Documented volume corrections | 31/33 |

Twelve main arrays are chained afterany, capped at 28; separate tail array 54733 is capped at four. Total concurrency remains 32, research/milan&cpu with euler09/euler10 excluded, one CPU and 4 GB each. No automatic retries or concurrency increase. The latest capacity samples show other research users waiting. All returned campaign timings identify AMD EPYC 7763 (Milan); workstation anchors identify Intel Core i7-13700.

The three preparation failures are CO (MMFF parameters unavailable), [2.2]paracyclophane and cubenene (BadConformerId). They were not replaced, retried or interpolated. All 55 eligible pilot results are reused. The nine isotope exclusions remain explicit, with no campaign parent mapping. Input and full perceived keys, connectivity-match basis and agreeing perception engines remain in molecule records.

## Solvent library and results

Twenty-five solvent identities will reuse verified campaign returns. Six missing solvent calculations—dihydropyran, chloroform, dichloromethane, DMSO, isopropylamine and tetrahydropyran—are array **56079**, cap four, initially dependent on both final main array 54802 and tail 54733. At 2026-09-14T05:46:28.409466+00:00, the live dependency was changed and verified to **afterany:54733 only**. The support array inherits the tail’s four slots after all tail tasks terminate, alongside at most one 28-slot main chunk; total concurrency remains at most 32. All six remain pending the tail dependency and are separate from the 5,824-contaminant denominator.

Water reuses the verified Milan A-1 diagnostic with exact recipe provenance. Its diagnostic preparation differs from campaign ranking; reuse is explicit because water has no conformational torsions. Other solvents require the frozen conformer ranking. Legacy COSMObase 2002 files establish identities only and never enter the 24a production calculation.

The separate **xylene** identity remains undefined; it must not silently become o-xylene. Clarification was requested. The 32 other source identities are resolved.

One serial local worker processes new verified returns, pauses new calculations below 1,000 MiB available memory, checks result writes on R:, and retains committed numerical results. Bulk output is under `/mnt/r/plastchem-euler/thermodynamics-v1/`. The combined `partitioning-current.csv` refreshes after changed passes, with ledger-hash and source-identity checks. Its 186,368 rows equal 5,824 × 32 requested water-reference pairs; pending or failed values stay blank. Row count does not imply prediction coverage. No product database is modified.

## Numerical meaning and validation

Production uses openCOSMORS24a at 298.15 K and a pure-component liquid reference, for neutral solutes. Mole-fraction partitioning is `(ln gamma_B − ln gamma_A)/ln(10)`; concentration partitioning adds `log10(V_B/V_A)`. These are solvent/solvent estimates, not pH-dependent logD or an absolute unidentified polymer-reference quantity.

Each activity must pass successive dilution checks from 1e-5 down to at most 1e-8. A final activity is withheld unless the last shift is at most 0.005 log units; solver failures also withhold values. The summed pair dilution shift is a sensitivity measure, not a physical error bound. Algebraic reversal and cycle closure check implementation consistency, not chemical accuracy.

- Surface audit: 340/340 passed, 0 failures at 2026-09-14T05:10:34Z. Checks cover surface/deck hashes, ORCA version and CPU, connectivity/perception provenance, element ordering, optimized-XYZ agreement and finite positive surface geometry. Later returns still require this audit.
- Independent stored-record audit: 341/341 passed, 0 failures at 2026-09-14T05:13:56Z. It checks hashes, source/configuration binding, dilution history, arithmetic and coverage flags. Later records remain outside that snapshot.
- Analytic sign/volume checks, real water self-activity, historical DBP replay and forced solver/dilution failure withholding passed.
- Combined-table audits verified all 5,824 identities, row counts, blank missing values and phase notes. Current hashes and audit timestamps are in state files; an older audit is not proof for a newer export.

The [historical compatibility report](../opencosmo-verification-v1/REPORT.md) demonstrated 12/12 selected completed contaminants in five workstation-library solvents, with 48 pair predictions. Historical DBP/BBP/DEHP replay reproduced 12/12 stored pair values within 3.55e-15 log units. DEP, DBP and BBP passed all four existing ±1.5-log comparisons; DEHP passed one of four, with roughly +2.0 to +2.1 log residuals for three water pairs, already present in workstation results. This discrepancy is retained. Those mixed-origin diagnostics are separate from homogeneous Milan production and do not validate PFAS ionization or every PFAS table row.

## Volume and phase evidence

The 31 documented volumes comprise five unchanged reference values, eight primary-article XML additions, 17 NIST/TRC experimental records and one explicitly labelled supplier-literature value. Every addition retains source identity, temperature, pressure, units, record location and hash. Author-reported, compiler-evaluated, standard, expanded and instrument-reading uncertainty statements are distinguished; none is called total model uncertainty. Isopropylamine has only one qualifying density source, with no independent cross-article agreement claim.

Missing corrections: **diphenyl ether and xylene**. No 20 °C or mixture density was substituted for a pure-liquid 25 °C value. The inspected diphenyl-ether article's pure density block begins at 303.15 K; no exact 298.15 K row was found, despite its abstract's range. The early interpretation of the abstract was corrected in the retained history.

The NIST archive (189,433,115 bytes, stored on R:) matches published SHA-256 `231161b5e443dc1ae0e5da8429d86a88474cb722016e5b790817bb31c58d7ec2`. A scan of 11,923 XML records found 404 candidates for nine then-missing identities. Selected direct measurements were reviewed; outliers were not averaged into corrections.

The inventory's propanediol target key was corrected by rebuilding from its stereo-unspecified SMILES; its original coordinate-perceived stereo key is preserved. All 32 defined SMILES/key checks pass, with no campaign target or DFT change. A separate earlier parser-unit issue for small legacy solvents was corrected locally using explicit Turbomole bohr conversion; product helper code was not edited.

Table rows explicitly identify the liquid-reference basis and phase qualifications for diphenyl ether, tert-butanol and cyclohexanol. These near/below-melting calculations are not evidence of solid-solvent partitioning or a stable pure liquid. Source links are recorded in `solvent-phase-review.json`; this is targeted phase review, not an exhaustive phase audit.

## Remaining completion gates

Complete and reconcile every campaign disposition; verify all successful returns; obtain and validate the homogeneous solvent library; resolve xylene; finish volume/phase coverage; calculate the full requested panel for every completed contaminant; expand production reference comparisons; and perform the final coverage/provenance/numerical audit. No narrower milestone closes the goal.

Machine evidence is under `state/thermodynamics-v1/`: `completion-requirements.json`, `processing-summary.json`, `processing-ledger.json`, `library-registry.json`, `density-coverage.json`, `additional-molar-volumes.json`, audit JSONs and `ENGINE-PROVENANCE.sha256`. Main campaign evidence is under `state/campaign-v1/` and `reports/campaign-v1/`.

[Retained detailed progress history](history/20260914T025825Z-progress.md).

Update 2026-09-14T03:02:17Z: the campaign has 308 verified ORCA completions. All 12 returns added since the 296-surface full audit passed a lightweight audit of surface/deck SHA-256 and recorded CPU, ORCA version, connectivity and perception provenance. This is not a fresh geometry/surface-numerics audit; that distinction is recorded in `new-return-integrity-audit.json`. The numerical worker remains live under its memory guard, with 304 water-activity records and no failed calculations.

Update 2026-09-14T03:19:49Z: the live numerical worker resumed above its memory threshold and processed all 310 then-completed contaminants in water, with zero activity failures. The six new numerical records are outside the earlier 304-record independent audit. The dihydropyran supplier density candidate is recorded in `dihydropyran-density-review.json`; its exact identity and 25 °C value were checked, but it has not entered production corrections because the original source-document download timed out and supplier-literature provenance must remain explicit.

Resource review at 2026-09-14T03:20:53Z: campaign Slurm accounting reports a maximum completed-task MaxRSS of 3,610 MiB (`54733_2.batch`), above the pilot high-water but below the 4 GiB request. All 32 running tasks retain more than one hour of their wall allocations; the longest-running main task is at 5.96/24 hours. This is scheduler accounting evidence, not a fresh process-memory measurement. Details: `state/campaign-v1/resource-headroom-review.json`. No resource envelope or concurrency change was made.

Supplier correction update: dihydropyran now uses 0.922 g/cm³ at 298.15 K from the opened Sigma-Aldrich D106208 product page, exact InChIKey matched. The resulting molar volume is 91.2343 cm³/mol. The pinned source is a curated attributed extract, explicitly not original HTML/PDF bytes; direct downloads timed out. This is a supplier literature value with unspecified pressure and uncertainty, not an original experimental measurement. Its source class and limitations propagate into each concentration result. Synthetic conversion/sign/provenance and frozen-reference checks passed (`supplier-volume-check.json`). Earlier notes withholding this correction are superseded by this documented provenance treatment.

Audit checkpoint 2026-09-14 05:30 UTC: all 344 returned surface records passed the full surface/deck/geometry/provenance audit (zero failures). All 343 committed thermodynamic records passed stored numerical and provenance checks at 05:28:30 UTC; these contain water activities, not a complete solvent panel. The next worker cycle will process the newly returned structure. Completion remains unproven.

Solvent scheduling improvement 2026-09-14T05:46:28.409466+00:00: inspected the live throttles and afterany chain for all 12 main arrays, the tail and support array. Changed only the existing support array dependency from afterany:54802:54733 to afterany:54733; verified the live result. No new submission, duplicate work, cap increase or main-array dependency change. Original submission receipt remains historical; current envelope and before/after evidence are in `state/solvent-library-v1/current-scheduling.json`, `dependency-review-before.json` and `dependency-change-receipt.json`. The 25 campaign-derived solvents still await their original campaign tasks, so this change does not establish full thermodynamic completion.

Audit checkpoint 2026-09-14 05:51 UTC: full returned-surface audit 348/348 passed, zero failures; stored thermodynamic record audit 347/347 passed at 05:48:55 UTC. These records still have only water activities; full partitioning remains incomplete.

CSV checkpoint 2026-09-14 05:56:52 UTC: audited all 186,368 rows from the 05:55:43 export against its SHA-256 and unchanged manifest. Exactly 5,824 eligible keys × 32 distinct solvent rows; no duplicates, no numeric values on nonprediction rows, all 350 completed-source identity/hash bindings correct, and all 17,472 targeted phase-note rows populated. This proves table structure and missingness, not completed thermodynamic coverage; there are still zero nonself predictions. Evidence: `state/thermodynamics-v1/current-table-audit.json`.

Audit checkpoint 2026-09-14 06:11 UTC: full surface audit 353/353 passed with zero failures; stored thermodynamic numerical/provenance audit 353/353 passed at 06:09:32 UTC. Water-only activities remain the available production coverage; full solvent-pair completion is not established.

Audit checkpoint 2026-09-14 06:33 UTC: full surface audit 357/357 passed with zero failures; stored thermodynamic numerical/provenance audit 357/357 passed at 06:31:12 UTC. Production still provides water activities only; full solvent-pair completion is not established.

Audit checkpoint 2026-09-14 06:52 UTC: full surface audit 363/363 passed, zero failures; stored thermodynamic numerical/provenance audit 361/361 passed at 06:50:16 UTC. Two newer returns await processing. Full solvent-pair completion remains unproven.

Audit checkpoint 2026-09-14 07:11:59 UTC: full surface audit 370/370 passed, zero failures; stored thermodynamic numerical/provenance audit 369/369 passed at 07:09:50 UTC. The additional return awaits the next processing cycle. Full solvent-pair completion remains unproven.

Audit checkpoint 2026-09-14 07:38:12 UTC: full surface audit 375/375 passed, zero failures; stored thermodynamic numerical/provenance audit 372/372 passed at 07:35:58 UTC. Three newer returns await processing. Full solvent-pair completion remains unproven.

2026-09-14 08:03 UTC: Full returned-surface audit passed 382/382 with zero failures (geometry, hashes, identity and ORCA provenance). The thermodynamic worker processed 382 contaminants with zero activity failures, but only water is ready (1/33 solvents), so nonself partition predictions and complete partitioning remain zero. Numerical stored-record audit remains the separate 377/377 snapshot at 07:56:49 UTC.

2026-09-14 08:28 UTC: Full returned-surface audit passed 387/387 with zero failures. This verifies surface geometry, hashes, identity and ORCA provenance for this snapshot; it does not establish complete solvent partitioning. Independent thermodynamic record audit passed 383/383 at 08:16:46 UTC.

2026-09-14 08:54 UTC: Full returned-surface audit passed 393/393 with zero failures (geometry, hashes, identity, ORCA provenance). Water activity processing covers 393 contaminants with zero calculation failures; production solvent coverage remains 1/33, so nonself partition predictions remain zero. Independent stored-record audit remains 390/390 at 08:44:00 UTC.

2026-09-14 09:29 UTC: Full returned-surface audit passed 401/401 with zero failures in geometry, hashes, identity and ORCA provenance. This is surface validation, not complete thermodynamic coverage; remaining solvent surfaces are still required.

2026-09-14 09:52 UTC: Full returned-surface audit passed 408/408 with zero failures in geometry, hashes, identity and ORCA provenance. Complete solvent partitioning remains unproven and pending the remaining solvent surfaces.

2026-09-14 10:21:05 UTC — Full returned-surface audit: 415/415 passed, zero failures. Checks cover archived surface and input-deck SHA256, ORCA 6.1.1/GIT 487d211c, AMD EPYC 7763 provenance, connectivity identity and perception records, atom ordering, optimized-coordinate/surface agreement, and finite positive surface geometry. This is surface validity evidence, not complete solvent partitioning coverage. Evidence: state/thermodynamics-v1/completed-surface-audit.json.

2026-09-14 12:44 UTC: Full return audit passed 453/453, zero integrity/geometry/provenance failures. Scheduler snapshot 12:39:46 UTC: 453 converged, 3 preparation failures, 32 running, 5,336 not yet run / 5,824. Main/tail converged 423/5,735 and 30/89. Closest running task to walltime has 15.42 hours remaining; maximum accounted RSS 3,610.03 MiB of 4,096 requested. Production solvent library remains water only; no final nonself partition predictions yet. Existing monitors and chained arrays continue.


2026-09-14 15:02 UTC checkpoint: surface audit passed 500/500 at 15:00:18 UTC; stored numerical/provenance audit passed 495/495 at 15:01:32 UTC. The processing worker reached 501 contaminants at 15:02:01 UTC, with zero activity failures, one of 33 solvent surfaces ready (water), and zero non-self partition predictions. These are distinct snapshots; neither audit establishes full solvent coverage or experimental accuracy. Campaign accounting is 501 converged + 3 preparation failures + 32 running + 5,288 not yet run = 5,824. All four monitors remain active. Research queue history had one other pending record at 13:34 UTC and zero at 14:04 and 14:34 UTC; concurrency remains 32.


2026-09-14 first DFT failure: Undec-10-ynoic acid, dodecyl ester (CPUMHCZZUYHHHY-UHFFFAOYSA-N), job 54755_273, AMD EPYC 7763, failed geometry convergence after 41,333.849 ORCA seconds (11.4816 CPU-hours) and 201 reported cycles. Raw output confirms maximum optimization cycles, all four final geometry criteria failed, and normal ORCA termination. Runner correctly rejected normal termination without geometry convergence; COSMORS was not run and no surface or prediction is attributed. Slurm FAILED 1:0, not timeout/OOM; batch MaxRSS 2,839,592 KiB. Raw opt.out retained on results drive with hash in state/campaign-v1/first-dft-failure-review.json. No retry. Campaign failures now include three preparation failures and one executed DFT failure; denominator remains 5,824.
