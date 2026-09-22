# Tier 2 — CHNO, 500 < MW ≤ 700

Amendment A-4 authorises 270 additional structures, kept separate from the 5,824-entry first campaign. Input SHA256: `f9b513c9df4e0d9ca49c6870b958b805e0c84b77368098afb63c75422540ebfb`. Independent checks passed: 270 rows and unique keys, no isotopes, no overlap with existing tiers, neutral single-fragment CHNO, MW in bounds, 54–134 atoms including H and 211 above 80.

Preparation is running; submission accounting is pending. The frozen preparation function and unchanged ORCA science code are reused. Array envelope: research, milan&cpu, exclude euler09/euler10, 1 CPU, 4 GB, maxcore 1500, 72 hours. Identity requires connectivity plus recorded perceived stereo and agreeing engines. Shared campaign cap is 64; Phase 2 retains priority.

One array will be submitted held. A fixed 30-entry cohort spanning atom counts will be released first; the other tasks remain held for the required cost refit. Report before continuing if the revised projection exceeds 3,540 CPU-hours (twice 1,770). Failures remain in the denominator and no job is retried blindly.

Evidence and scripts: `/home/aaltamimi2/plastchem-euler/state/tier2-v1/`, `/home/aaltamimi2/plastchem-euler/scripts/prepare_tier2.py`, `launch_tier2.py`, `tier2_runner.py`, `watch_tier2.py`, `summarize_tier2.py`. Bulk returns and tables: `/mnt/r/plastchem-euler/tier2-v1/`. Transport uses the existing multiplexed SSH/scp helper with persisted backoff. The existing serial panel process remains active.

## Preparation failure, 2026-09-18T05:57:34.036857+00:00

Trilaurin (`VMPHSYLJUKZBJJ-UHFFFAOYSA-N`, CAS 538-24-9) failed RDKit preparation with `ValueError: Bad Conformer Id`. ORCA was not run. Retained in denominator 270; no retry or substitute. With this failure, no more than 269 tasks can be submitted. Evidence: `/home/aaltamimi2/plastchem-euler/state/tier2-v1/prepared/VMPHSYLJUKZBJJ-UHFFFAOYSA-N/preparation.json`; SHA-256 `a98558109551d80c3038c6296bc6f9d593ca3c0fac18ec31ebdfa039cf39ee8a`.

Preparation failure recorded 2026-09-18T06:06:27.158014+00:00: trimethylolpropane trilaurate (`GSAHAZJWNMHSNI-UHFFFAOYSA-N`, CAS 25268-73-9) failed with `ValueError: Bad Conformer Id`. ORCA not run; no retry/substitution. Two preparation failures remain in denominator 270; maximum submission now 268. Source `/home/aaltamimi2/plastchem-euler/state/tier2-v1/prepared/GSAHAZJWNMHSNI-UHFFFAOYSA-N/preparation.json`, SHA-256 `44fa3f61a670fb5b0848c3bb3646cbc660e865c97f9d2889619637eec490b051`.

Preparation update 2026-09-18T06:08:20.815387+00:00: four preflight failures / 270, all RDKit `ValueError: Bad Conformer Id`. Additional failures: `IBKKMFMBXQARGV-UHFFFAOYSA-N` (CAS 14450-05-6) and `PPKAGMLCLQWXJX-UHFFFAOYSA-N` (CAS 93803-89-5). No ORCA execution, retry or substitution. Hashed evidence: `state/tier2-v1/preflight-failure-summary.json`.

## Submission

Array **63873**, submitted 2026-09-18T06:09:39Z (01:09:39 CDT). 270 pinned, 0 policy exclusions, 4 preparation failures, **266 submitted**. Both squeue and sacct reconciliation were empty for its deterministic name. First 30 fixed size-spanning tasks released; remaining 236 held for cost review. Shared array cap 64, 4 GB/task, 72-hour walltime. Initial scheduler readback: Phase 2 running 0, tier 2 running 0; sum <=64. Evidence: `state/tier2-v1/tier2/submission-receipt.json`, `state/tier2-v1/tier2/initial-release.json`, `state/tier2-v1/submission-accounting.json`.

## First-cohort review, 2026-09-22T02:21:39.577416+00:00

All 30 released tasks are terminal: 27 accepted, one completed DFT with connectivity verification failure, and two abnormal COSMORS terminations. Including four preparation failures: 27 accepted / 7 failed / 0 running / 236 held, denominator 270. The 28 complete DFT timings give a projected 1,839.6 CPU-hours (conditional bootstrap 95% interval 1,561.1–2,164.7), below the 3,540 CPU-hour reporting threshold. Two incomplete DFT timings are omitted from the regression but remain explicit failures; this interval does not bound their additional cost or long stalls. A-5 supersedes release: all 236 remaining tasks stay held for polymer priority. Exact review: `/mnt/r/plastchem-euler/tier2-v1/first30-cost-review.json`.

Serial panel processing of the 27 accepted returns started after the original 5,803-record pass completed; separate outputs `/mnt/r/plastchem-euler/tier2-v1/thermodynamics/`.

## Panel completed and sealed

All 27 accepted structures processed at 298.15 K against 32 resolved references: **837 solvent/water predictions**, including 810 concentration-based coefficients; zero activity failures. 27 diphenyl-ether concentration corrections remain unavailable; generic xylene remains unresolved. Full 270-entry export has 8,640 requested rows, including 7,552 held/not-run rows, 224 failed-ORCA/preparation rows, and 27 unresolved xylene rows. CSV: `/mnt/r/plastchem-euler/tier2-v1/thermodynamics/partitioning-current.csv`.

All 27 passed the numerical/provenance audit; seal `/mnt/r/plastchem-euler/audits/tier2-first27-panel-20260921`, manifest SHA-256 `da267db9cf4416f42c7019b9317b1581c2679dbc8fd01e5a5a73da9908461bac`. This covers the completed cohort, not all 270.

Refined failure diagnosis: Vat brown 1 and the nitro anthra/benzopentaphenedione entry each failed SCF convergence in both subsidiary COSMORS gas-phase and CPCM runs. The wrapper returned exit code zero, but normal-termination checks rejected them correctly. Log evidence: `/mnt/r/plastchem-euler/tier2-v1/failure-diagnostics/cosmors-abnormal-tails.json`, SHA-256 `a467fa1992c254ab621a3dda2a7e5750cb2f6ebf33777422db37e9be625a1416`. Original dispositions retained; no retries.

## Independent verification update — 2026-09-21 22:01 CDT

All 27 accepted tier-2 surfaces passed the existing geometry/provenance auditor: surface and input-deck hashes, ORCA 6.1.1 and Milan provenance, recorded connectivity agreement, optimized-geometry correspondence, positive finite segment areas, total-area consistency and finite surface charge densities. The sealed audit is `/mnt/r/plastchem-euler/audits/tier2-first27-surfaces-20260921`; its 31 artifact hashes were rechecked, with manifest SHA256 `a0b79a6182ade097ed21ee87d86ff18cda391a71eb08ea343acbfc2e006f5852`. This establishes integrity for 27 accepted structures out of tier denominator 270; it is not an experimental-accuracy claim.

Separately, all 61 artifacts in the tier-2 numerical panel seal were independently rehashed with zero mismatches. Receipt: `/home/aaltamimi2/plastchem-euler/state/tier2-first27-panel-independent-rehash.json`.
