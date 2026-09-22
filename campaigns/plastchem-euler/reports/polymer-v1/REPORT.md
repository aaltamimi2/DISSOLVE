# Polymer conformer campaign — A-5

Submitted 2026-09-22T02:30:50.576584+00:00 (21:30 CDT September 21). Corrected input SHA-256 `72e4fca5694615722cb6134a42d2126333dfe3a8fc56c9f969e41edbc911a3ae`; all 284 XYZ digests, atom counts, formulas and connectivity blocks independently verified. 14 polymers, 16 species, no omitted conformers.

- Array 65676: 244 conformers, 38–104 atoms.
- Array 65677: 40 conformers, 155–167 atoms; afterany:65676.
- PE orders 1–31 released; remaining 253 held for first-cohort cost review and ordered release.
- Tier 2 remaining 236 stay held. Shared cap 64; research, milan&cpu, euler09/10 excluded; one CPU, 4 GB, 72-hour walltime.

Recipe and stage validation reused unchanged from the contaminant runner. Supplied coordinates used directly; no conformer search or local minimization. Entries remain separate by species and conformer. No polymer-contaminant partition coefficients are authorised or calculated.

The first submission attempt stopped before sbatch on parentheses around the scheduler hold reason. After mandatory backoff, both deterministic names were absent from squeue and sacct and were submitted once. Evidence: `/home/aaltamimi2/plastchem-euler/state/polymer-v1/submission-accounting.json`.

PE deliverables pending: accepted/31, final and relative energies, 298.15 K Boltzmann weights, cavity volumes, explicit merges with RMSD and energy differences, and surface hashes. Do not average PETG species together.

## Body release, 2026-09-22T02:39:16Z

Per explicit orchestrator steer, released the entire 244-task body array 65676 using `scontrol release 65676`; cap remains 64. Initial readback confirms all 244 body tasks pending with reason None; all 40 large tasks in 65677 and all 236 tier-2 tasks remain JobHeldUser. This supersedes the initial PE-only release plan. The initial task-specific releases did not produce running tasks; full-array release is now confirmed. Evidence: `state/polymer-v1/body-release-owner-steer.json`.

## Walltime correction — 2026-09-21 22:02 CDT

Updated pending array 65676 in place to 28 hours, then indices 0–30 to 3 hours. Scheduler readback confirms 31 PE tasks at 3 hours and 213 other body tasks at 28 hours, shared throttle 64, 4 GB unchanged. No cancellation or resubmission. Large array 65677 (40 tasks) and tier 2 (236 tasks) remain held. All body tasks were still pending Priority in the immediate readback; shorter limits do not guarantee an immediate allocation. Evidence: `state/polymer-v1/body-walltime-rightsize.json` and `body-walltime-verified.json`.

First confirmed PE start: **2026-09-21 22:02:35 CDT**, tasks **65676_0–16** on **euler144**. Readback at 22:03:40 confirms 17 PE running and 227 body pending. Evidence: `state/polymer-v1/post-walltime-start-check.json`.

## Pending-only size-band limits — 2026-09-21 22:20 CDT

Manifest-derived body ranges: 31–81 (44 atoms) 3h, 82–92 (48) 3h, 93–117 (62) 5h, 118–165 (84) 9h, 166–172 (88) 10h, 173–194 (91) 11h, 195–214 (98) 12h, 215–243 (104) 14h. Only pending indices were changed: 47–243, because 31–46 had already started; PE was already 3h and had no pending tasks. Large 65677 indices 0–39 are now 30h and remain held. Updated 237 pending tasks in place, using temporary body holds to prevent start races, then releasing those holds; no cancel/resubmit. Running task limits verified unchanged. Readback: 19 running, cap64 unchanged; tier2 236 held and untouched. Evidence: `state/polymer-v1/pending-band-walltime-update.json` and `pending-band-walltime-verified.json`.

Any TIMEOUT is an execution-limit event retained as an attempt and counted as retry_pending, not a chemistry failure. Longer-limit resubmission is authorised after terminal/queue/accounting reconciliation, preserving attempt history and the shared cap. No TIMEOUT has yet been observed. Tier2 limits will use its own measurements, never these polymer limits.

## Owner queue release and PC override — 2026-09-22

Measured refit: 216 accepted polymer body timings (38–104 atoms), ln(hours) = −12.584617 + 3.063892 ln(atoms), residual smearing 1.122337. The 40 large conformers project 848.3 CPU-h (conditional bootstrap 95% 728.6–973.8); limits are 47h for polyurethane155, 50h for PC158, 59h for PETG167. These are extrapolations: the completed measurements do not support a uniform fourfold speedup or shortening all large jobs below 30h.

Tier2 uses only its own 27 accepted timings: ln(hours) = −6.653028 + 1.841998 ln(atoms), smearing1.070672; 266 prepared structures project1818.5 CPU-h (1538.4–2122.1). Atom bands ≤60/70/80/90/100/110/120/130/140 have limits6/8/10/12/15/18/21/24/28h respectively. Time limits are ceil(max(2×mean,1.25×95th-percentile residual factor×geometric prediction)); no polymer speedup was used for tier2.

All276 held tasks were queued using in-place updates and releases. The later owner override made PC immediately eligible: cleared dependencies and released65677_14 through65677_32 first. Then released indices0–13 and33–39 with an after-start dependency on all19 PC jobs, and tier2 afterany:65677. Body had27 running and no pending; large/tier2 throttles37 enforce total≤64 because tier2 cannot overlap large and body running can only decline. PC started at08:53:19–20CDT on euler142/euler143; 57 total running at08:55. Exact commands/readbacks: state/polymer-v1/pc-priority-release.json and pc-first-start-readback.json. An initial attempt to change the body throttle returned an already-finished-task error; no body throttle change was needed because it has no pending tasks.

Nitrocellulose166–172 is left running. Time-limit outcomes remain retry_pending, never chemistry failures. On timeout each original directory will be checked for a last usable geometry/trajectory/GBW; retries will preserve attempt provenance, use48h, and queue after tier2. This polymer is an outlier not represented by the atom-count fit. No running job was cancelled or extended.


Nitrocellulose owner instruction, 2026-09-22: original tasks 65676_166–172 remain untouched. A persistent supervisor waits for natural terminal outcomes, retains successes, and stages confirmed time-limit retries from the last usable saved geometry after a connectivity check. Retry uses the unchanged recipe, 48 hours, 4 GB and 1 CPU; dependency afterany:65676:65677:63873 puts it after PC, other large polymers and tier 2. TIMEOUT/USR1 walltime interruption remains a time-limit outcome, not a chemistry failure; all seven identities remain in the 284 denominator. Original records and geometry provenance are preserved under /mnt/r/plastchem-euler/polymer-v1/nitro-retry1/. Status and any submission errors are in /home/aaltamimi2/plastchem-euler/logs/nitro-timeout-retries.jsonl. No retry submission has yet occurred at this entry.

A-7 is complete: /mnt/r/plastchem-euler/polymer-v1/route-comparison-a7/REPORT.md. 896 pairs per convention; offsets polymer-specific, normalized sign changes 56/896, existing-convention 88/896. Both top changes 32/128 (DEHP, PVDF to PVC for all 32 solvents). Tier-2 serial panel processing resumed after the phased calculations finished.
