# Throughput steer: preparation and chained submission

**Eight preparation workers are active. Main-body blocks submit immediately when ready, at cap 28 with an afterany chain; the existing tail array remains at cap 4. Maximum campaign concurrency stays 32.**

Timing matters: the steer arrived after all 89 tail structures were prepared and array **54733** was submitted. Its receipt records no prior matching job in either squeue or sacct. Four tail tasks are running on AMD EPYC 7763 (Milan). No tail work was cancelled, repeated, or moved into a main block. All remaining preparation is main-body work.

## Measured preparation throughput

The prior implementation already used **four process workers**, each with one RDKit thread. Individual molecule durations were not serial batch wall time. The four-worker dispatcher was paused, its bounded queue drained, and all in-flight records were confirmed complete before its workers were stopped. No interrupted preparation or duplicate canonical attempt was introduced.

| Measurement | Four workers | Eight workers |
| --- | ---: | ---: |
| Completed molecules in measured window | 105 main | 60 main |
| Window wall time | 17.63 min | 5.63 min |
| Main throughput | 357.3/hour | 639.4/hour |

The complete earlier mixed main/tail window produced 194 molecules in 37.87 minutes (307.4/hour). Its summed per-molecule wall time was 2.51 hours, confirming overlapping execution. Main-only throughput increased by an observed factor of 1.79; these sequential windows contain different molecules and are not a controlled scaling benchmark. The earlier main preparations averaged 39.09 seconds each.

Eight-worker parent-plus-worker peak RSS was **632.4 MiB**; minimum observed system available memory **3011.9 MiB**. Dispatch is bounded to eight tasks and pauses replenishment if available memory falls below 700 MiB. Threads remain one per worker.

## Reproducibility

Four already-prepared molecules (two main and two tail) were re-prepared concurrently in an isolated lane-owned validation directory. **4/4 XYZ files are byte-identical; 4/4 retain seed 12345, the identical complete recipe and selected conformer ID.** Canonical preparation files were untouched. This checks the requested handful; it does not claim an all-structure rerun.

| InChIKey | Atoms | XYZ byte-identical | Seed |
| --- | ---: | --- | ---: |
| `AKXFYSSXNQQBNT-UHFFFAOYSA-N` | 80 | yes | 12345 |
| `YAOSHAWMYFELSJ-UHFFFAOYSA-N` | 80 | yes | 12345 |
| `VHQQPFLOGSTQPC-UHFFFAOYSA-N` | 107 | yes | 12345 |
| `DFKBFBPHOGVNGQ-QPLCGJKRSA-N` | 90 | yes | 12345 |

## Submission plan

At the observed eight-worker rate, remaining main preparation projects to **8.6 hours**, above the approximately three-hour trigger. This is a conservative scheduling projection from the current large-molecule window, not a prediction for every smaller molecule. Chunking is therefore implemented.

- 5,833 pinned − 9 visibly excluded isotopologues = **5,824 eligible**. No isotope-parent mapping is carried.
- Main ≤80 atoms: **5,735 eligible = 55 reused pilot results + 5,680 new target dispositions**. Twelve disjoint blocks contain eleven times 500 and a final 180.
- Carbon monoxide is the known MMFF preparation failure; it remains in the eligible denominator and receives no job. If no further preparation fails, **5,679 new main jobs** will be submitted.
- Each ready block submits immediately after staging/digest validation and deterministic-name reconciliation against both squeue and sacct. The replaced full-main job name is also checked for conflicts.
- First main block: cap 28, no dependency on the tail. Every subsequent main block: cap 28 and `--dependency=afterany:<preceding-main-array-id>`.
- The existing 89-target tail array retains cap 4. At most one main block can run, so **28 + 4 = 32**. No independent 32-task arrays or concurrency increase.
- Research, `milan&cpu`, euler09/euler10 excluded; 1 CPU, 4 GB, main 24 hours and tail 48 hours.
- Identical original global task indices, input XYZs, ORCA runner, recipe and D-IDENT policy. No silent retry; multiplexed SSH/backoff and scp-only returns continue.

Slurm documents that an afterany dependency on an array ID is satisfied after all array tasks complete, regardless of success: [official job-array documentation](https://slurm.schedmd.com/job_array.html). Local mocked-scheduler checks passed for the dependency argument, cap, existing-job reconciliation, refusal after an unconfirmed attempt, full-main conflict rejection, and the exact disjoint partition of 5,680 indices. No cluster jobs were used for these checks.

Live preparation measurements: `state/campaign-v1/throughput-steer/parallel-stats.json`. Reproducibility evidence: `state/campaign-v1/throughput-steer/reproducibility.json`. Plan: `state/campaign-v1/chunk-plan.json`. Recipe pins are preserved; throughput additions are pinned in `THROUGHPUT-EXECUTION-PROVENANCE.sha256`.

The chunk launcher, eight-worker preparation pool and updated campaign monitor are running. The main blocks have not yet been submitted at this report snapshot; each will launch once its own preparations finish, without waiting for other blocks or the tail.
