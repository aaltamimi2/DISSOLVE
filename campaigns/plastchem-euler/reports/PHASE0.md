# Phase 0 — ORCA installed and smoke-tested; compute AVX2 gate unresolved

2026-09-12. Lane: codex-astra-CONTAM. No sbatch, salloc, or srun invoked; no jobs submitted. Phase 1 has not started. Phase 0 is **not fully cleared**, because compute-node AVX2 flags could not be read without an allocation or working compute-node SSH.

## Inputs

All five SHA-256 digests match `state/INPUTS.sha256`. Exact row-multiset equality was checked on all six common columns, not merely counts; structure sets are pairwise disjoint.

| Tier | Rows | Unique InChIKeys |
| --- | ---: | ---: |
| CHNO submission set | 6,064 | 5,833 |
| Parked heteroatoms | 1,762 | 1,721 |
| Parked Si/B | 239 | 238 |
| Audited superset | 8,065 | 7,792 |

CHNO + heteroatoms also exactly equals the no-Si/B input: 7,826 rows / 7,554 unique structures. All 8,065 SMILES parse, have net charge zero, and contain one fragment. Maximum RDKit MW is 499.778 g/mol (the charter's 499.77 is the stated input mass); maximum stated-versus-RDKit difference is 0.062 g/mol. The CHNO input contains only CHNO. DEP, DBP, BBP and DEHP each occur once by CAS in the audited superset. Evidence: `state/phase0-input-verification.json`; reproducible check: `scripts/verify_inputs.py`.

## Euler entitlement, limits and other work

Passwordless SSH verified as aaltamimi2, uid 18803. Group membership includes `euler-research` and `euler-vanlehn`. `research` and `interactive` allow all groups/accounts; `vanlehn` allows `euler-vanlehn,euler-admin`, so this user passes that restriction. `AccountingStorageEnforce=none`; `sacctmgr show assoc user=aaltamimi2` returns no association. Current work has `Account=(null), QOS=normal`; there is no discovered account name to supply. `JobSubmitPlugins=(null)`.

- `MaxArraySize=10001`; `MaxJobCount=10000` is cluster-wide, not a personal allowance.
- No `MaxSubmitJobs` configuration entry; normal QoS has blank MaxJobsPU, MaxSubmitJobsPU, MaxTRESPU and MaxWall, and no user association imposes a visible limit. This establishes the exposed configuration, not an untested promise of unlimited submission.
- Partitions have maximum walltime 16-16:00:00; default 04:00:00; default memory 2,000 MB/CPU; no explicit partition memory maximum. Explicit partition selection is necessary: default `none` has zero nodes.
- Queue snapshot: research 9 running jobs / 134 requested CPUs and 32 pending one-CPU jobs; vanlehn 5 running / 48 CPUs. Other visible queues: chrysoslab 2 running / 192 CPUs, pdelab 2 running / 32 CPUs, none 1 pending / 2 CPUs. This is a transient snapshot.
- This login already has running job **50511, slab125-mixed-50ns**, vanlehn/euler09, 8 CPUs, 48 GiB, one GPU, eight-hour time limit. It is a different campaign; no files or job state were modified. No other job under this login was visible at the first snapshot.

Proposed later target, subject to phase clearance: the research CPU pool (for example euler142, currently idle with 256 logical CPUs and 500,000 MB), rather than adding contention on the vanlehn nodes. A pilot must use a modest explicit concurrency cap and recheck shared-login work; no full-campaign resource commitment is made here. Evidence: `logs/euler-access-retry.txt`, `logs/euler-limits-avx2.txt`, `logs/euler-compute-check.txt`, `logs/target-nodes.txt`.

## AVX2 — unresolved compute-node gate

`lscpu` on **euler-login-1.engr.wisc.edu** explicitly reports `avx2` (Intel Xeon E5-2640 v4). The installed AVX2 ORCA binary successfully executed there.

Compute-node authentication was denied on `euler142` and `euler09`, including a local-key connection through the login host. Slurm advertises `amd,epyc,milan,ssd,cpu` for euler142 and `amd,epyc,genoa,...` for euler09, but no explicit `avx2` feature. CPU-family labels are not being represented as an observed CPU flag. I did not invoke srun or borrow the existing job to bypass the no-job gate. Evidence: `logs/euler142-avx2.txt`, `logs/euler09-avx2.txt`.

To clear this specific gate, obtain a read-only CPU-flag output from the intended compute pool through a permitted access method, or explicitly amend the no-job instruction to permit a tiny diagnostic allocation. A runtime AVX2 preflight is also required on each later target node.

## ORCA installation and end-to-end smoke test

Installed from the user's existing Windows-side archive, transferred with **scp** directly to Euler. No local extraction or new download. Source and remote tarball SHA-256 both:

`5eaf676f9711a38835d609264321a30266b487b65477547802dedee982bc82d5`

Euler installation (17 GB):
`/srv/home/aaltamimi2/plastchem-euler/software/orca_6_1_1_linux_x86-64_shared_openmpi418_avx2`

Activate in a shell with `source ~/plastchem-euler/orca-env.sh`; the binary can also be invoked by absolute path. Shared dependencies resolved successfully. The archive remains in `~/plastchem-euler/distributions/`.

One water conformer was generated locally using RDKit ETKDGv3 (seed 20260912) and MMFF. The smoke ran directly on the login node, without a scheduler submission, at the charter's frozen recipe: OPT BP86/def2-TZVP(-f)/TightSCF, then COSMORS(Water), neutral singlet, serial, no `%pal`, `%maxcore 1500`. The generated COSMORS solute deck explicitly reports BP86/def2-TZVPD. Both parent outputs terminated normally; optimisation reported convergence. Both parent outputs identify **Program Version 6.1.1 / GIT 487d211c**.

| Smoke measurement | Result |
| --- | --- |
| Node | euler-login-1.engr.wisc.edu |
| Geometry optimisation elapsed | 23.929 s |
| COSMORS elapsed | 29.392 s |
| Optimised-geometry InChIKey | XLYOFNOQVPJJNP-UHFFFAOYSA-N |
| Identity verification | RDKit bond perception from optimised XYZ; matches water |
| Solute surface | 48,454 bytes |
| Surface SHA-256 | 9bc1cec34e95f38dd9d713f405459b9c51f79730bdbc007851376e1ec031239b |

Smoke: **1/1 converged, 0/1 failed**. This demonstrates installation and COSMORS functionality on Euler's login node; it is not a compute-node timing calibration. Campaign: **0/5,833 converged, 0/5,833 failed, 5,833/5,833 not yet run**.

Exact decks, outputs, COSMORS subsidiary outputs, optimised XYZ, surface, and verified JSON are archived at `/mnt/r/plastchem-euler/phase0/`. Euler raw files remain at `~/plastchem-euler/phase0/water/`; `verified-result.json` records the local post-optimisation identity check. Local provenance: `state/phase0-smoke.json`, `state/RECIPE-SOURCES.sha256`, `state/ORCA-DISTRIBUTION.sha256`. Scripts: `scripts/prepare_smoke.py`, `scripts/smoke_orca.py`, `scripts/verify_smoke.py`.

Licensing flag requested by the charter: this is the same user's existing ORCA distribution installed in that user's cluster home, not redistributed. The bundled `EULA_ORCA_2025.pdf` is retained. The charter says the registered user must have accepted the EULA; this run does not independently establish that acceptance.

## Results return shape and storage

Per successful campaign InChIKey, return `surface.orcacosmo`, `result.json`, `opt.inp`, `cosmo.inp`, and `optimized.xyz` to `/mnt/r/plastchem-euler/results/<InChIKey>/` using scp. JSON will carry source identities, post-optimisation identity, status, ORCA version/GIT, exact deck hashes, node, timings, properties and surface digest. Keep full scratch and complete calculation logs on Euler; return compact diagnostic records for failures. openCOSMO-RS remains local in `~/.venvs/cosmo-logp`.

Measured existing local phthalate surface sizes: DEP 359,739; DBP 483,539; BBP 508,283; DEHP 693,015 bytes. Their mean is 511,144 bytes. With a provisional 20 KB allowance for JSON/decks/XYZ, the anchor-based estimate is **0.53 MB/molecule, 3.10 GB for 5,833**; extrapolating the smallest/largest anchors gives **2.22–4.16 GB**. This is not a population size measurement; the pilot must refine it. Plan 10 GB for returned successful artifacts and headroom. Evidence: `state/phase0-return-sizing.json`.

Created `/mnt/r/plastchem-euler/phase0/`; R: has about 1.1 PB available. Local root was checked before retrieval and still had about 2.4 GB available; bulk retrieval went directly to R:. No product databases or other-lane files were modified.

**Stopped at Phase 0.** ORCA-not-installed blocker resolved. Compute-node AVX2 confirmation remains blocked by access. No Phase 1 or campaign execution is authorised by this report.
