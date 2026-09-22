# First-day campaign checkpoint

Snapshot: 2026-09-14T03:41:09Z, approximately 24 hours after the first campaign task started.

There are 314 converged eligible structures: 259 newly completed tasks plus 55 reused pilot results. Three preparation failures remain explicit, 32 tasks are running, and 5,475 structures have not yet run, against 5,824 eligible structures. No new DFT execution failure is recorded.

New completed tasks comprise 239 main-body and 20 tail tasks. Their summed Slurm elapsed time is 677.46 CPU-hours at one CPU per task; this excludes elapsed time of unfinished tasks and excludes reused pilot work. Maximum reported campaign MaxRSS is 3610.0 MiB within the 4 GiB allocation. The tasks ran on the Milan campaign pool; workstation timings are not used here.

The 2026-09-14T03:34:25Z queue sample still shows 18 other research pending records. Concurrency remains 28 main plus four tail, with no increase. Chained main arrays and separate tail array remain active; the six supplemental solvent tasks remain dependent on the campaign arrays.

Production has 314 water-activity records and zero failed activity calculations. No final non-water partition predictions are yet available. The 314-record numerical audit passed; the most recent full surface audit covers 310 returns, and the four later returns have passed stage-exit, archived-hash and recorded-connectivity checks. These scopes remain distinct.

This is measured throughput, not a revised campaign forecast. Early ordered batches, staged startup, and unfinished jobs prevent treating 259 completions per day as an unbiased rate for the entire set. The final estimate and accounting must retain those distinctions.

Machine evidence: `state/campaign-v1/first-day-checkpoint.json`; latest scheduler snapshot, capacity history, and thermodynamic audit records remain in their respective state directories.
