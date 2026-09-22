# Phase 0 / Amendment A-1 — diagnostics passed; report and stop

2026-09-12. The compute-node AVX2 gate is closed for the tested research/Milan target. Exactly two diagnostic jobs were submitted, both completed successfully. No campaign array, campaign job, or Phase 1 pilot was run.

Both jobs ran on **euler145.engr.wisc.edu**, **AMD EPYC 7763 64-Core Processor (Milan)**. The full `/proc/cpuinfo` flags and `lscpu` output were captured. The `avx2` flag was checked before ORCA; the actual ORCA 6.1.1 AVX2 binary then executed both frozen-recipe stages and produced surfaces. Slurm also identifies this node with `amd,epyc,milan,ssd,cpu`.

| Molecule / job | Atoms incl. H | OPT wall (s) | COSMORS wall (s) | Slurm elapsed (s) | Slurm peak RSS (MiB) | Surface bytes | Five-file bundle bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Water / 54150 | 3 | 26.525 | 32.285 | 60 | 508.91 | 48,442 | 53,364 |
| Ethanol / 54151 | 9 | 57.806 | 40.985 | 100 | 474.11 | 113,666 | 120,166 |

All timings in the table were measured on the AMD EPYC 7763 Milan CPU above; stage wall includes process startup and I/O. Peak RSS is the Slurm batch-step measurement, not the requested allocation. Both jobs have `COMPLETED`, exit `0:0`.

Each job requested **research**, constraint **milan&cpu**, one node, one task, **1 CPU**, **4G memory**, **00:15:00**. Nodes euler09 and euler10 were explicitly excluded. Job 50511 remained running on vanlehn/euler09 at the final queue check; it was not modified. No account argument was needed, confirming the prior entitlement assessment through actual submission.

Water was regenerated using one ETKDGv3 + MMFF94 conformer. Ethanol was selected from the digest-verified CHNO submission set: CAS 64-17-5, PlastChem ID 11245, SMILES CCO. Both inputs use seed 20260912. Exactly one conformer per molecule was passed to DFT. The frozen decks were OPT BP86/def2-TZVP(-f)/TightSCF and COSMORS(Water), neutral singlet, serial, `%maxcore 1500`, no `%pal`. The generated COSMORS solute calculation explicitly used BP86/def2-TZVPD. All parent runs terminated normally, optimisation converged, and ORCA reported **6.1.1 / GIT 487d211c**.

Post-optimisation identities were perceived from the returned XYZ with RDKit and matched the expected InChIKeys. Returned surface and input-deck digests were independently verified locally:

- `XLYOFNOQVPJJNP-UHFFFAOYSA-N` — surface SHA-256 `da09de46b52463d51ad9534346ff47eeda0d2e24803a67a00503ff65cd77f750`.
- `LFQSCWFLJHTTHZ-UHFFFAOYSA-N` — surface SHA-256 `453d03ba8f34e7f7b0e4ec1a127c96b8b8659f226473bc964dae090f65523a97`.

**Diagnostics: 2/2 converged, 0/2 failed, 0/2 not yet run.** The ethanol result is scoped as an A-1 diagnostic, not silently credited as a completed campaign job. Campaign execution remains 0/5,833; its state remains 0 converged, 0 failed, 5,833 not yet run.

The accepted return path was exercised end to end. Each verified bundle contains exactly `surface.orcacosmo`, `result.json`, `opt.inp`, `cosmo.inp`, and `optimized.xyz` under `/mnt/r/plastchem-euler/results/<InChIKey>/`. Full diagnostic stdout, COSMORS subsidiary outputs, CPU flags, and lscpu evidence are under `/mnt/r/plastchem-euler/diagnostics-a1/evidence/<InChIKey>/`. Scratch remains in the user’s Euler home at `~/plastchem-euler/diagnostics-a1/runs/<InChIKey>/`. Verified local identities/accounting were copied back as `verified-result.json`, preserving the original compute-stage record. Transfers used scp. Local root was checked before retrieval (2.4 GB free); bulk retrieval went directly to R:.

The diagnostic return sizes are measurements, not representative estimates of the 5,833-structure set. The earlier approximately 3.1 GB projection remains only an anchor-based planning hypothesis. Report a new population projection after the cleared pilot provides real sizes.

**Calibration constraint:** use research with the Milan CPU generation for both pilot and campaign if this target is retained; record the exact CPU model for every result. Per Correction C-1, the four workstation anchors used Intel Core i7-13700; the previous login smoke used Xeon E5-2640 v4 (Broadwell); their times are not interchangeable with Milan, and Genoa is a separate calibration target. If the campaign target changes generation or partition, rerun the pilot calibration on that target. This diagnostic does not establish throughput or safe campaign concurrency.

Reproducible scripts: `scripts/prepare_a1.py`, `scripts/a1_diagnostic.py`, `scripts/a1_diagnostic.sbatch`, `scripts/submit_a1.sh`, `scripts/verify_a1.py`. Input/script provenance: `state/A1-PROVENANCE.sha256`. Records: `state/a1-status.json` and the two `state/a1-<InChIKey>.json` files. Scheduler evidence: `logs/a1-job-allocations.txt`, `logs/a1-final-accounting.txt`. Submission markers and exclusive per-structure attempt locks prevent accidental duplicate submissions or silent reruns.

**Phase 0 complete after A-1. Stopped.** No remaining diagnostic blocker. Phase 1 still requires orchestrator/owner clearance; Phase 2 still requires explicit owner approval.

Post-steer reconciliation (before preparing Phase 1): `squeue` has no live `contam-a1-diag` jobs; `sacct --starttime=today --name=contam-a1-diag` returns exactly 54150 and 54151, both COMPLETED. No duplicate diagnostics or retry submission were needed. The initial failed transport sequence had created no remote diagnostic directory; that absence was checked before the successful staging/submission. This is a clarification of A-2's wording about a lost confirmation: the observed record is the two completed jobs above, not an additional previously lost diagnostic. Evidence: `logs/pilot-initial-reconciliation.txt`.

Correction C-1 resolves workstation provenance: the four historical anchor runs used the Intel Core i7-13700. The Euler login node alone is Xeon E5-2640 v4 (Broadwell). The earlier workstation/Broadwell attribution has been retracted; exact timings and corrected CPU metadata are in `state/pilot-v1/workstation-anchors.json`.
