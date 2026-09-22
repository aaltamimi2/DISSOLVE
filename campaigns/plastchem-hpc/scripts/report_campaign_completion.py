"""Final A-3 report. Refuses completion while any eligible target lacks a disposition."""
import collections,csv,hashlib,json,statistics,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1';R=ROOT/'reports/campaign-v1'
s=json.loads((P/'summary.json').read_text());snap=json.loads((P/'latest-snapshot.json').read_text());rows=list(csv.DictReader((R/'results.csv').open()))
assert len(rows)==5824 and not snap['squeue'].strip()
assert (P/'submission-complete.json').exists() and len(snap['groups'])==len(json.loads((P/'chunk-plan.json').read_text())['main_chunks'])+1
assert all(s['counts'][k]==0 for k in ['running','not_yet_run','awaiting_verification'])
assert s['counts']['converged']+s['counts']['failed']==5824
records={p.stem:json.loads(p.read_text()) for p in (P/'records').glob('*.json')}
completed=[r for r in records.values() if r.get('dft_status')=='converged' or r.get('status')=='converged']
executed=[r for r in records.values() if r.get('dft_ran',bool(r.get('stages')))]
models=collections.Counter(r.get('cpu_model','unrecorded') for r in executed)
rss=[float(r['maxrss_kib']) for r in rows if r['maxrss_kib']]
byte_sizes=[int(r['returned_bytes']) for r in rows if r['returned_bytes']]
lines=['# Phase 2 campaign completion — A-3 owner decisions','',
       '**5,833 pinned structures − 9 explicitly excluded deuterated entries = 5,824 campaign targets.** No isotope-to-parent mapping was applied. The parked heteroatom and Si/B tiers remain outside this campaign.','',
       f"Final eligible dispositions: **{s['counts']['converged']:,}/5,824 converged and connectivity-verified; {s['counts']['failed']:,}/5,824 failed; 0/5,824 awaiting execution or final disposition**. Execution coverage, reported separately: **{len(executed):,}/5,824 executed**, **{5824-len(executed):,}/5,824 never executed**. Of executed targets, **{len(completed):,}/{len(executed):,}** completed OPT + COSMORS normally. Exclusions are separate: **9/5,833** pinned identities. Never-executed preparation failures remain failures in the eligible denominator.",'',
       'D-IDENT acceptance compares the first InChIKey block only. The [results table](results.csv) records the input key, full perceived key, explicit connectivity match basis, input stereo specification, agreeing perception engines and each engine’s key. A match does not silently assert stereo-layer equality. Exact geometry/surface provenance remains in each result bundle.','',
       f"Reused reference-recipe pilot results: **55**. New Slurm jobs submitted: **{s['new_jobs_submitted']:,}**. Arrays: `{s['arrays']}`. Main-body arrays of roughly 500 targets initially used afterany dependencies and a 28-slot cap alongside the separately submitted 4-slot tail (32 total). As chunks drained, the controller released the next dependency and adjusted both throttles within the authorised total. The owner subsequently authorised 64 concurrent jobs while retaining 4G per job; the first recorded increase took effect on 2026-09-16 at 20:35:30 UTC (15:35:30 CDT). The authority and scheduler readbacks are recorded in state/campaign-v1/concurrency64-authority-audit.json and state/campaign-v1/resource-increase-2026-09-16.json; the exact chat-message timestamp is not independently available. All jobs used research / milan&cpu with euler09/euler10 excluded, one CPU and 4G per job, main 24-hour and tail 48-hour limits. No automatic retry was used.",'',
       '## Main body and separately measured extrapolated tail','',
       '| Group | Targets | Converged | Failed | Measured terminal attempt-hours | OPT + COSMORS measured hours* |',
       '| --- | ---: | ---: | ---: | ---: | ---: |']
for group in ['main_le80','tail_gt80']:
    g=s['groups'][group]
    lines.append(f"| {group} | {g['denominator']:,} | {g['converged']:,} | {g['failed']:,} | {g['measured_terminal_attempt_hours']:,.2f} | {g['measured_orca_hours']:,.2f} |")
lines+=['','*Stage totals include records with measurements of both stages; the terminal-attempt budget also retains time consumed by incomplete/failed attempts. The main-body total includes the 55 reused pilot measurements. Tail measurements are reported separately and were not assumed from the pilot atom-count extrapolation.','',
        f"Executed CPU models and record counts: `{dict(models)}`. Every recorded timing belongs to the named compute model, not the Intel Core i7-13700 workstation or Broadwell login node.",'']
if rss:lines.append(f"Largest measured Slurm MaxRSS: **{max(rss):,.0f} KiB ({max(rss)/1024:,.1f} MiB)**; available for {len(rss):,}/{len(executed):,} executed targets. Missing measurements remain blank in the table.")
if byte_sizes:lines+=['',f"Returned bundles and diagnostic records total **{sum(byte_sizes):,} bytes ({sum(byte_sizes)/1e9:.3f} GB)**. Per-target sizes are in the CSV; median {statistics.median(byte_sizes):,.0f} bytes, range {min(byte_sizes):,}–{max(byte_sizes):,}. Bulk data are under `/mnt/r/plastchem-euler/results/<InChIKey>/`; transfers used scp with multiplexing and persistent failure backoff. Full ORCA scratch remains in the lane’s Euler home."]
lines+=['','## Failures and exclusions','',f"Failure modes: `{s['failures_by_mode']}`. Every failed target is listed below and in the machine-readable results table. No failed or never-run result was interpolated or substituted.",'']
for row in rows:
    if row['status']=='failed':lines.append(f"- `{row['input_inchikey']}` — {row['name']}: **{row['failure_mode']}**; {row['error']}.")
lines+=['','The [exclusions table](exclusions.csv) retains all nine source identities and the owner’s exclusion reason, without a parent mapping. Carbon monoxide’s unavailable MMFF parameters, if present among failures, did not authorise a different force field or a fabricated geometry.','',
        '## Execution and provenance','',
        'ORCA 6.1.1 / GIT 487d211c; serial, no `%pal`, `%maxcore 1500`; neutral singlet; OPT BP86/def2-TZVP(-f)/TightSCF followed by COSMORS(Water), whose solute step uses BP86/def2-TZVPD. Exactly one DFT conformer, selected with the pinned 300-candidate ETKDGv3/MMFF ranking. Per-result records retain exact decks, hashes, CPU model/node, stage timing, surface digest and identity observations.','',
        'Each deterministic array name was reconciled against squeue and sacct before submission. Submission attempt markers prevented blind resubmission after lost confirmation. The final campaign queue is empty. No other campaign job or product database was modified.','',
        '**ORCA campaign portion complete. Thermodynamic partitioning and its completion audit remain pending; the active goal is not complete.**']
(R/'REPORT.md').write_text('\n'.join(lines)+'\n')
status={'phase':2,'status':'orca_complete_thermodynamics_pending','utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'counts':s['counts'],'excluded_count':9,'groups':s['groups'],'arrays':s['arrays'],'new_jobs_submitted':s['new_jobs_submitted'],'DFT_executed':len(executed),'DFT_normally_completed':len(completed),'report':'reports/campaign-v1/REPORT.md'}
(P/'final-status.json').write_text(json.dumps(status,indent=2)+'\n')
campaign=json.loads((ROOT/'state/campaign-status.json').read_text());campaign['phase_status']='orca_complete_thermodynamics_pending';(ROOT/'state/campaign-status.json').write_text(json.dumps(campaign,indent=2)+'\n')
(ROOT/'PROGRESS.md').write_text('ORCA campaign portion COMPLETE — thermodynamic partitioning remains active.\n\n'+json.dumps(status,indent=2)+'\n\n[Final report](reports/campaign-v1/REPORT.md) · [Results](reports/campaign-v1/results.csv) · [Exclusions](reports/campaign-v1/exclusions.csv)\n')
files=['reports/campaign-v1/REPORT.md','reports/campaign-v1/results.csv','reports/campaign-v1/exclusions.csv','state/campaign-v1/summary.json','state/campaign-v1/final-status.json']
(P/'FINAL-PROVENANCE.sha256').write_text(''.join(f'{hashlib.sha256((ROOT/name).read_bytes()).hexdigest()}  {name}\n' for name in files))
print(R/'REPORT.md')
