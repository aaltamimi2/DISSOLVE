import json
from pathlib import Path
root=Path(__file__).resolve().parents[1];p=root/'state/pilot-v1';s=json.loads((p/'latest-snapshot.json').read_text());records=list(s['results'].values())
if (p/'final-status.json').exists():
 final=json.loads((p/'final-status.json').read_text());a=json.loads((root/'reports/pilot-v1/analysis.json').read_text())
 budget=a['all_attempt_budget'];band=budget['bootstrap_cpu_hours_p05_p50_p95'];archive=a['fit']['archive_bytes_bootstrap_p05_p50_p95']
 text=f'''Phase 1 COMPLETE — reported and stopped. Array 54154; final scheduler snapshot {s['utc']}.

One research array, milan&cpu, euler09/euler10 excluded, cap 16, 1 CPU, 4G, 12h. All 55 executed tasks ran on euler145, AMD EPYC 7763 (Milan), completed with exit 0:0, and produced COSMORS surfaces. Name reconciliation found exactly this array and 55 completed tasks; squeue is empty. The monitor exited. No retry or Phase 2 submission.

Disposition / 56 selected: 46 converged and identity-verified; 10 failed; 0 pending; 0 running. DFT coverage: 55/56 executed, 55/55 executions converged, 1/56 never executed. Failures are 9 unresolved stereo identities (including DEHP) and 1 isotope preflight failure. The strict exact_full_inchikey policy remains in force; no clarification approving a relaxed policy arrived.

Milan-only one-attempt projection for 5,833 structures: {budget['campaign_cpu_hours']:,.0f} CPU-hours, conditional bootstrap sensitivity band {band[0]:,.0f}–{band[2]:,.0f}. This is not a guaranteed confidence interval or a retry budget. Maximum Slurm MaxRSS 1,953.2 MiB, measured for 55/55 executions. Projected five-file archive: {archive[1]/1e9:.2f} GB ({archive[0]/1e9:.2f}–{archive[2]/1e9:.2f} GB conditional band).

All four historical anchors are Intel Core i7-13700 (Raptor Lake), per C-1. EPYC 7763/i7-13700 ORCA wall ratios: DEP 3.729, DBP 1.954, BBP 1.942, DEHP 4.181. Initial conformers and optimisation cycle counts differ; these are workload comparisons, not pure CPU benchmarks. The revised estimate uses only Milan timings.

Final report: [reports/pilot-v1/REPORT.md](reports/pilot-v1/REPORT.md).
Every molecule and residual: [reports/pilot-v1/molecules.csv](reports/pilot-v1/molecules.csv).
Exportable plot: [reports/pilot-v1/wall-and-residuals.pdf](reports/pilot-v1/wall-and-residuals.pdf).
Machine state: state/pilot-v1/final-status.json, final-validation.json, verified-state.json; state/campaign-status.json.
Returned bundles: /mnt/r/plastchem-euler/results/<InChIKey>/.

Before any owner-cleared Phase 2: resolve identity handling for source-undeclared stereo; preserve isotope identities for 9/5,833 corpus entries; resolve MMFF-unavailable carbon monoxide. These entries have not been removed or substituted. Phase 2 still requires explicit owner clearance. No other lane or campaign was operated on.
'''
 (root/'PROGRESS.md').write_text(text);raise SystemExit(0)
normal=sum(r['status']=='converged_identity_pending' for r in records);running=sum(r['status'].startswith('running_') for r in records);prefail=sum(r['status']=='failed' and r.get('dft_ran') is False for r in records);dfail=sum(r['status']=='failed' and r.get('dft_ran') is not False for r in records);pending=56-normal-running-prefail-dfail
policy=p/'identity-policy.json';mode=json.loads(policy.read_text())['mode'] if policy.exists() else 'exact_full_inchikey (unchanged; clarification pending)'
text=f'''Phase 1 pilot RUNNING — array {s['array_job_id']}; snapshot {s['utc']}.

Research, milan&cpu, excludes euler09/euler10; one array 0-55%16, 1 CPU, 4G, 12h per task. All measured compute nodes so far: AMD EPYC 7763 (Milan). No resubmission or Phase 2.

Compute dispositions / 56 selected: {normal} normally completed OPT+COSMORS; {dfail} DFT failures; {running} running; {pending} pending; {prefail} preflight failure. Completion is not chemical-identity acceptance. Task 42, benz[a]anthracene-d12, was cancelled while pending because plain XYZ lost isotope labels; no replacement. Its Euler attempt lock prevents a blind later run.

DECISION REQUEST — source stereo policy: the pinned DEHP key and several others leave stereo unspecified. First completed molecule MEXURWLRXYTWGT-UHFFFAOYSA-N produced MEXURWLRXYTWGT-NVNMMGJXSA-N, agreed by RDKit and Open Babel; connectivity matches. Strict full-key validation rejects it. May verification preserve the full observed key and accept connectivity plus every source-declared stereocentre/bond stereo, leaving only undeclared stereo unconstrained? Known-answer checks reject positional isomers, inversions of declared stereo, and isotope loss. Current policy: {mode}. No relaxed acceptance has been applied without clearance. An appended charter clarification is sufficient.

C-1 resolved hardware provenance: historical anchors ran on Intel Core i7-13700 (Raptor Lake); only Euler LOGIN is Xeon E5-2640 v4 (Broadwell). Revised estimates use Milan pilot data alone; the old 2,628 CPU-hour hypothesis is not propagated. Anchor ratios will name both CPUs.

Reconciliation before submission found the deterministic pilot name absent from squeue and sacct; receipt 54154 is pinned. Monitor uses the prescribed multiplexed connection, 120-second polling, and failure backoff; it cannot submit or cancel jobs. Further charter amendments are checked locally.

Evidence: state/pilot-v1/latest-snapshot.json, verified-state.json, monitor-history.jsonl, workstation-anchors.json; state/campaign-status.json. Returned artifacts: /mnt/r/plastchem-euler/results/<InChIKey>/. A-1 report: reports/PHASE0-A1.md. Full Phase 1 report follows all terminal dispositions, verification and analysis. Phase 2 still needs the owner.
'''
if policy.exists():
 info=json.loads(policy.read_text());start=text.index('DECISION REQUEST');end=text.index('\n\nC-1 resolved',start)
 text=text[:start]+f"Identity policy confirmed: {mode}. Authority: {info.get('authority','owner/orchestrator clarification recorded in identity-policy.json')}. Full observed InChIKeys and both perception observations are retained. No DFT rerun is needed for a validation-policy update."+text[end:]
(root/'PROGRESS.md').write_text(text)
