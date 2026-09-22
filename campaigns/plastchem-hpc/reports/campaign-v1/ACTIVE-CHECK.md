# Active campaign verification — 2026-09-13T14:45:21Z

All 13 arrays reconciled by deterministic name against squeue and sacct: exactly one array ID per name. Live scheduler settings confirm research, milan&cpu, euler09/euler10 excluded, 1 CPU and 4 GB. All eleven later main arrays have the expected afterany predecessor. Main cap 28 plus tail cap 4 remains 32. No resubmission, retry, cancellation or cap increase was performed.

5,833 pinned − 9 excluded = 5,824 eligible. Submission accounting: 55 reused pilot results + 5,766 newly submitted jobs + 3 preparation failures = 5,824. The new jobs comprise 5,677 main and 89 tail tasks.

Current counts / 5,824: {'converged': 159, 'failed': 3, 'running': 32, 'awaiting_verification': 0, 'not_yet_run': 5630, 'denominator': 5824}.

Eight-worker preparation is complete: 5,574 remaining targets disposed in 85.25 minutes, 3923.2/hour over the complete run. Peak parent-plus-worker RSS 634.3 MiB. The earlier 8.6-hour estimate from large molecules was an overestimate; smaller later molecules prepared much faster. The already-authorised chunks remain chained as submitted.

Preparation failures (3/5,824; none executed DFT):
- `OOLUVSIJOMLOCB-UHFFFAOYSA-N` — [2.2]Paracyclophane: ValueError: Bad Conformer Id.
- `UGFAIRIUMAVXCW-UHFFFAOYSA-N` — Carbon Monoxide: mmff_parameters_unavailable: No MMFF parameters; frozen recipe permits no force-field substitution.
- `KWFAQPWLROZBAY-UHFFFAOYSA-N` — Cubenene: ValueError: Bad Conformer Id.

The monitor continues read-only scheduler checks, scp retrieval to R:, digest and connectivity verification, and failure accounting. Root has 2.2 GB free. No additional preparation workers remain active.

OpenCOSMO compatibility evidence: [verification report](../opencosmo-verification-v1/REPORT.md), 12/12 selected campaign surfaces predicted successfully. Coverage and DEHP disagreement remain explicitly qualified; this is not full-panel or homogeneous-Milan production validation.
