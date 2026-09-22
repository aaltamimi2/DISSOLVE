# Phase 2 campaign completion — A-3 owner decisions

**5,833 pinned structures − 9 explicitly excluded deuterated entries = 5,824 campaign targets.** No isotope-to-parent mapping was applied. The parked heteroatom and Si/B tiers remain outside this campaign.

Final eligible dispositions: **5,803/5,824 converged and connectivity-verified; 21/5,824 failed; 0/5,824 awaiting execution or final disposition**. Execution coverage, reported separately: **5,821/5,824 executed**, **3/5,824 never executed**. Of executed targets, **5,819/5,821** completed OPT + COSMORS normally. Exclusions are separate: **9/5,833** pinned identities. Never-executed preparation failures remain failures in the eligible denominator.

D-IDENT acceptance compares the first InChIKey block only. The [results table](results.csv) records the input key, full perceived key, explicit connectivity match basis, input stereo specification, agreeing perception engines and each engine’s key. A match does not silently assert stereo-layer equality. Exact geometry/surface provenance remains in each result bundle.

Reused reference-recipe pilot results: **55**. New Slurm jobs submitted: **5,766**. Arrays: `{'main_chunk_000': '54755', 'main_chunk_010': '54801', 'main_chunk_004': '54795', 'main_chunk_005': '54796', 'main_chunk_011': '54802', 'main_chunk_008': '54799', 'main_chunk_003': '54789', 'main_chunk_001': '54786', 'main_chunk_006': '54797', 'main_chunk_002': '54787', 'main_chunk_007': '54798', 'main_chunk_009': '54800', 'tail_gt80': '54733'}`. Main-body arrays of roughly 500 targets initially used afterany dependencies and a 28-slot cap alongside the separately submitted 4-slot tail (32 total). As chunks drained, the controller released the next dependency and adjusted both throttles within the authorised total. The owner subsequently authorised 64 concurrent jobs while retaining 4G per job; the first recorded increase took effect on 2026-09-16 at 20:35:30 UTC (15:35:30 CDT). The authority and scheduler readbacks are recorded in state/campaign-v1/concurrency64-authority-audit.json and state/campaign-v1/resource-increase-2026-09-16.json; the exact chat-message timestamp is not independently available. All jobs used research / milan&cpu with euler09/euler10 excluded, one CPU and 4G per job, main 24-hour and tail 48-hour limits. No automatic retry was used.

## Main body and separately measured extrapolated tail

| Group | Targets | Converged | Failed | Measured terminal attempt-hours | OPT + COSMORS measured hours* |
| --- | ---: | ---: | ---: | ---: | ---: |
| main_le80 | 5,735 | 5,715 | 20 | 3,313.01 | 3,300.72 |
| tail_gt80 | 89 | 88 | 1 | 363.88 | 341.44 |

*Stage totals include records with measurements of both stages; the terminal-attempt budget also retains time consumed by incomplete/failed attempts. The main-body total includes the 55 reused pilot measurements. Tail measurements are reported separately and were not assumed from the pilot atom-count extrapolation.

Executed CPU models and record counts: `{'AMD EPYC 7763 64-Core Processor': 5821}`. Every recorded timing belongs to the named compute model, not the Intel Core i7-13700 workstation or Broadwell login node.

Largest measured Slurm MaxRSS: **4,188,104 KiB (4,089.9 MiB)**; available for 5,821/5,821 executed targets. Missing measurements remain blank in the table.

Returned bundles and diagnostic records total **2,381,032,960 bytes (2.381 GB)**. Per-target sizes are in the CSV; median 371,626 bytes, range 18,135–1,061,136. Bulk data are under `/mnt/r/plastchem-euler/results/<InChIKey>/`; transfers used scp with multiplexing and persistent failure backoff. Full ORCA scratch remains in the lane’s Euler home.

## Failures and exclusions

Failure modes: `{'return_integrity_or_connectivity': 16, 'geometry_nonconvergence': 2, 'ValueError': 2, 'mmff_parameters_unavailable': 1}`. Every failed target is listed below and in the machine-readable results table. No failed or never-run result was interpolated or substituted.

- `BGVYDWVAGZBEMJ-UHFFFAOYSA-N` — 4-[[4-(Aminocarbonyl)phenyl]azo]-N-(2-ethoxyphenyl)-3-hydroxynaphthalene-2-carboxamide: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `CPUMHCZZUYHHHY-UHFFFAOYSA-N` — Undec-10-ynoic acid, dodecyl ester: **geometry_nonconvergence**; Optimisation did not converge.
- `FZEHABHMMOXCEI-UHFFFAOYSA-N` — 1H-Inden-1-one, 3-hydroxy-2-(3-hydroxy-2-quinolinyl)-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `GZCKCZAMIMLHDK-UHFFFAOYSA-N` — C.I. Disperse Yellow 56: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `HECMZBWRWWVBJI-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, N-(2,3-dihydro-2-oxo-1H-benzimidazol-5-yl)-3-hydroxy-4-[(2-methoxy-4-nitrophenyl)azo]-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `HULNYTPFPARJMG-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, 4-[[4-(aminocarbonyl)phenyl]azo]-3-hydroxy-N-(2-methoxyphenyl)-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `IESSXLDTYTXHDL-UHFFFAOYSA-N` — Benzoic acid, 2-[[3-[[(2,3-dihydro-2-oxo-1H-benzimidazol-5-yl)amino]carbonyl]-2-hydroxy-1-naphthalenyl]azo]-, methyl ester: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `KDLDNQQRGJAGIS-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, 3-hydroxy-4-[(2-methyl-4-nitrophenyl)azo]-N-(2-methylphenyl)-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `KQYMXKWYMPNOTG-UHFFFAOYSA-N` — 1H-Inden-1-one, 6-(2,5-dimethylbenzoyl)-3-hydroxy-2-(3-hydroxy-2-quinolinyl)-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `KWFAQPWLROZBAY-UHFFFAOYSA-N` — Cubenene: **ValueError**; Bad Conformer Id.
- `LFFICOHZNUVGRZ-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, 3-hydroxy-4-[[2-methoxy-5-[(phenylamino)carbonyl]phenyl]azo]-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `NWDMDFAMCIHORB-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, 3-hydroxy-4-[(2-methyl-5-nitrophenyl)azo]-N-(2-methylphenyl)-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `OOLUVSIJOMLOCB-UHFFFAOYSA-N` — [2.2]Paracyclophane: **ValueError**; Bad Conformer Id.
- `SNZMGUPMUOWIDP-LUAWRHEFSA-N` — 2-Cyano-2-[2,3-dihydro-3-(tetrahydro-2,4,6-trioxo-5(2H)-pyrimidinylidene)-1H-isoindol-1-ylidene]-N-methylacetamide: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `SOFRHZUTPGJWAM-UHFFFAOYSA-N` — 1-(2-Methoxy-5-nitrophenylazo)-2-hydroxy-3-(3-nitrophenylcarbamoyl)naphthalene: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `SWVFBTUHIDDXAY-DTQAZKPQSA-N` — 2-Quinazolineacetonitrile, alpha-(2,3-dihydro-3-(tetrahydro-2,4,6-trioxo-5(2H)-pyrimidinylidene)-1H-isoindol-1-ylidene)-1,4-dihydro-4-oxo-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `UCTUWLQBNDCKIQ-UHFFFAOYSA-N` — Undec-10-ynoic acid, heptadecyl ester: **geometry_nonconvergence**; Optimisation did not converge.
- `UGFAIRIUMAVXCW-UHFFFAOYSA-N` — Carbon Monoxide: **mmff_parameters_unavailable**; No MMFF parameters; frozen recipe permits no force-field substitution.
- `VXQNQGCWKZPKET-UHFFFAOYSA-N` — 2,4,6(1H,3H,5H)-Pyrimidinetrione, 5,5'-(1H-isoindole-1,3(2H)-diylidene)bis-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `XYZMEPHFXJHGIX-UHFFFAOYSA-N` — 3-Hydroxy-4-[(2-methyl-5-nitrophenyl)azo]-N-phenylnaphthalene-2-carboxamide: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.
- `YOGMQHMXNSEDLV-UHFFFAOYSA-N` — 2-Naphthalenecarboxamide, 4-[[5-(aminocarbonyl)-2-methylphenyl]azo]-3-hydroxy-N-phenyl-: **return_integrity_or_connectivity**; No perception engine produced the required first-block match.

The [exclusions table](exclusions.csv) retains all nine source identities and the owner’s exclusion reason, without a parent mapping. Carbon monoxide’s unavailable MMFF parameters, if present among failures, did not authorise a different force field or a fabricated geometry.

## Execution and provenance

ORCA 6.1.1 / GIT 487d211c; serial, no `%pal`, `%maxcore 1500`; neutral singlet; OPT BP86/def2-TZVP(-f)/TightSCF followed by COSMORS(Water), whose solute step uses BP86/def2-TZVPD. Exactly one DFT conformer, selected with the pinned 300-candidate ETKDGv3/MMFF ranking. Per-result records retain exact decks, hashes, CPU model/node, stage timing, surface digest and identity observations.

Each deterministic array name was reconciled against squeue and sacct before submission. Submission attempt markers prevented blind resubmission after lost confirmation. The final campaign queue is empty. No other campaign job or product database was modified.

**ORCA campaign portion complete. Thermodynamic partitioning and its completion audit remain pending; the active goal is not complete.**
