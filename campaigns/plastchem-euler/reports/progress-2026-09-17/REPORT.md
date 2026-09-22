# Contaminant overview draft — 17 September 2026

## Current snapshot

The draft displays **70,572 existing pure-solvent/water partition predictions** for **5,803 accepted contaminants**, grouped into eight atom-count bins. All 5,803 appear exactly once. There are 70 solvent columns: 69 product common keys plus acetic acid to retain existing panel data. All calculations shown are at **298.15 K (25°C)**. Unavailable cells: **335,638**, shown gray rather than zero. This is a partial computed grid, not a claim of complete coverage or engine integration.

Source counts: 64,769 panel predictions and 5,803 validation-only octanol predictions. The snapshot capture began 2026-09-17T18:57:38.543878+00:00 and was finalized 2026-09-17T19:04:26.134276+00:00; two changing panel records were reconciled to newer hash-verified records and recorded in summary.json. All copied source results have now been read back and checked against their source hashes and extracted CSV values.

![Contaminant data overview](/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1/contaminant-data-overview-draft.png)

The DEP worked example uses 11 ethanol/water mole-fraction compositions from the bounded pilot. It illustrates calculated dilute activity ratios, not experimental data or phase-equilibrium partitioning. Its points are separate from the pure-solvent headline count. Heatmap colours show log10 K on the mole-fraction basis consistently; concentration-based coefficients are retained separately in CSV where available. Gray means no accepted prediction in this snapshot. The PNG gives every contaminant at least one pixel row at 300 dpi; PDF and complete matrix are included. Draft width is 7 inches; a publication-width reduction needs a fresh legibility/layout pass.

## Campaign and validation status

Campaign outcomes: 5,803 accepted / 21 failed / 0 running / 0 not yet run, denominator 5,824. The 21 failed dispositions are 16 integrity/connectivity failures, two geometry nonconvergences, and three preflight failures; they are not 21 DFT execution failures. Nine isotope-labelled structures were excluded upstream from 5,833 CHNO inputs. All 5,803 accepted surfaces have a matching surface audit. Total terminal-attempt CPU time: 3,676.9 h. The roughly 9,000-contaminant count is a future owner planning figure, not this lane's verified running or accepted denominator.

The final octanol cohort completed 1,171/1,171 predictions and passed 1,171/1,171 numerical/provenance audits; combined with prior 4,632, octanol covers all 5,803 accepted contaminants. The broader existing panel pass continues serially. Full 69-solvent precomputation, additional compositions/temperatures and product integration were not launched.

Experimental comparison: **n=1179; MAE=0.6969; RMSE=1.0226; bias=+0.5497; slope=0.9495; intercept=0.6738**. These are log-unit statistics against qualified cited measured values, with no recalibration. The table has 1179 full input keys spanning 1143 connectivity blocks; those are entry-weighted metrics, not 1179 independent connectivity skeletons. Prior released n=21 and n=622 packages are unchanged. Dry-octanol modelling and measured water-saturated-octanol conditions differ; measured high-logKow source uncertainty remains. Named outliers and per-row citations are in `/mnt/r/plastchem-euler/validation-final5803-2026-09-17/`.

## Proposed work, not launched

See [the figure and calculation specification](/home/aaltamimi2/plastchem-euler/reports/progress-2026-09-17/CONTAMINANT_OVERVIEW_AND_CALCULATION_SPEC.md). It defines the 69-solvent readiness audit, missing surfaces, identity caveats, possible pure-solvent/composition/temperature grids, deduplicated counts, validation gates, cache keys and later engine integration. Chlorobenzene and sulfolane lack a verified compatible local surface. The DEP pilot passed 67 solvents and 737 mixture cells; this is not proof of every solvent/contaminant combination. Documented concentration-conversion volumes remain incomplete.

Xylene identity and the didecyl-phthalate measured-reference discrepancy remain owner questions. No new ORCA jobs or future grid calculations were submitted by this figure task.

## Reproduction and files

Snapshot directory: `/mnt/r/plastchem-euler/progress-2026-09-17/contaminant-overview-draft-v1`. Figure: `contaminant-data-overview-draft.png` (300 dpi) and `.pdf`. CSVs: `predictions.csv`, `contaminant-rows.csv`, `solvent-columns.csv`, `size-bin-summary.csv`, `worked-example.csv`, `planned-grid-counts.csv`. Complete numeric matrix: `heatmap-matrix.npz`. Evidence: `summary.json`, `verification.json`, `snapshot/`, `SHA256SUMS` and copied `code/`.

Local PNG: `/home/aaltamimi2/plastchem-euler/contaminant-data-overview-draft-2026-09-17.png`.

Scripts are under `/home/aaltamimi2/plastchem-euler/scripts/`: `build_contaminant_overview_snapshot_20260917.py`, `finalize_contaminant_overview_data_20260917.py`, `render_contaminant_overview_20260917.py`, `seal_contaminant_overview_20260917.py`. Use `/home/aaltamimi2/.venvs/cosmo-logp/bin/python`. The builder refuses an existing snapshot directory; create a new version for later data rather than overwriting this draft. The renderer reads this frozen dataset. No product database writes are part of this workflow.

Gray-cell reasons are tabulated in `cell-status-counts.csv` and `missingness-summary.json`: 208,908 future-grid cells not launched; 115,063 cells awaiting existing panel work; 11,606 without compatible solvent surfaces; 61 dilution failures. Presentation metadata was refreshed after the final footer wording correction; numeric data and the passed readback checks were unchanged.

## Complete octanol distribution

The refreshed [distribution plot](/home/aaltamimi2/plastchem-euler/2026-09-17-complete-octanol-distribution-5803.png) includes all 5,803 audited dry-octanol predictions at 25°C, with the full numerical range retained. The 300 dpi PNG, PDF, predictions CSV, histogram bins and hash manifest are in `/mnt/r/plastchem-euler/progress-2026-09-17/octanol-distribution-5803/`. This companion plot leaves the sealed overview snapshot and earlier releases unchanged.

## Final resolved-panel audit gate

`/home/aaltamimi2/plastchem-euler/scripts/audit_final_resolved_panel.py --check-only` checks the complete accepted cohort before allowing a final numerical/provenance snapshot. The first check correctly refused certification while 639 entries lacked panel records and 4,811 had only partial reference coverage. This is an expected incomplete-processing outcome, not a scientific failure. Once ready, run with the COSMO virtualenv Python and `--output /mnt/r/plastchem-euler/audits/final-resolved-panel-<timestamp>`; the directory must be new. The command seals all 5,803 records, checks reference membership, source hashes, documented volume bindings and stored numerical identities, and refuses certification if any record changes during capture. Unresolved xylene and experimental-accuracy limitations remain separate; this command does not certify them.

## Expanded-panel interim audit at 15:12 CDT

A further 389 complete available-panel records passed numerical/provenance and bounded-dilution failure-semantics audits, with zero failures or changed records. The sealed package is `/mnt/r/plastchem-euler/audits/resolved32-increment-20260917T2008/`; its SHA256SUMS digest is `d52f07f4078fcdeb2ae27fcca73e72ca497a45409d34f0784ebb7464f65ef76e`. Combined with the first 112-record audit, this covers 501 records at the 32-reference scope, not the complete 5,803-entry cohort. The serial worker continues. Final certification remains pending.

## Expanded-panel interim audit at 16:49 CDT

A further 468 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings the audited 32-reference cohort to 969; it is not full-cohort certification. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260917T2143`; SHA256SUMS digest: `53cf78b967e2745540e09789755cc5666fbe283f4050f519cdee0f91e21d55d5`. The serial processor continues.

## Expanded-panel interim audit at 18:21 CDT

A further 457 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 1,426 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260917T2315`; SHA256SUMS digest: `f3571d23e3ce21350183b11a2b36086401764643e024aedacca0cb9d81c0144e`. The serial processor continues.


## 20:06 CDT — additional 470 numerical records sealed

A further 470 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 1,896 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0056`. SHA256SUMS digest: `b7dc7ae5d5774e40311bb5be259d2630208713d80b70050f252f5f5d77939b02`. The serial processor continues.


## 21:10 CDT — additional 335 numerical records sealed

A further 335 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 2,231 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0203`. SHA256SUMS digest: `d861707a36532f4fbd8f626626778bb35b3aa2dd1a2b9c1b81706da25709144c`. The serial processor continues.


## 22:09 CDT — additional 293 numerical records sealed

A further 293 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 2,524 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0303`. SHA256SUMS digest: `d89f19f330a4b17316c9f4c8cd47c87d003d58f4a9eb0863b5eca1d548bde427`. The serial processor continues.


## 23:01 CDT — additional 268 numerical records sealed

A further 268 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 2,792 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0354`. SHA256SUMS digest: `d63365a5bcce92c38c80142c577a62ee4ee1cba3f2578d9352d2bb3516974686`. The serial processor continues.


## 23:49 CDT — additional 250 numerical records sealed

A further 250 records passed numerical/provenance and bounded-dilution failure-semantics checks, with zero audit failures or changed records. This disjoint increment brings audited 32-reference coverage to 3,042 records; full-cohort certification remains pending. Sealed package: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0442`. SHA256SUMS digest: `5d842fb9cc91cb90055e5fa8f7bde38bb6891acbb64bd2c90f9bb54a23573172`. The serial processor continues.

Incremental numerical audit sealed 2026-09-18T05:58:07.939253+00:00: 243 additional 32-reference records passed, zero failures; disjoint sealed coverage now 3,285 records. Artifact: `/mnt/r/plastchem-euler/audits/resolved32-increment-20260918T0549`; manifest SHA-256 `396496ca7093958eff10c9a6c960c61c9324b7944b09a52b3798d617ff3678bd`. This is numerical/provenance consistency, not new experimental validation or full campaign completion.
