"""Durable A-3 campaign table, explicitly separate exclusions and the extrapolated tail."""
import collections,csv,json,fcntl
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'
summary_lock=(P/'summary.lock').open('a');fcntl.flock(summary_lock,fcntl.LOCK_EX)
eligible=json.loads((P/'eligible.json').read_text());excluded=json.loads((P/'exclusions.json').read_text())
records={p.stem:json.loads(p.read_text()) for p in (P/'records').glob('*.json')}
counts={'converged':0,'failed':0,'running':0,'awaiting_verification':0,'not_yet_run':0,'denominator':5824}
groups={g:dict(counts,denominator=sum(m['group']==g for m in eligible)) for g in ['main_le80','tail_gt80']}
rows=[]
for mol in eligible:
    key=mol['inchikey'];r=records.get(key,{});status=r.get('status','not_yet_run')
    category='converged' if status=='converged' else 'failed' if status=='failed' else 'awaiting_verification' if status=='converged_identity_pending' else 'not_yet_run' if status=='not_yet_run' else 'running'
    counts[category]+=1;groups[mol['group']][category]+=1
    stages=r.get('stages',{});walls=[stages.get(stage,{}).get('wall_seconds') for stage in ['opt','cosmo']]
    rows.append({'input_inchikey':key,'name':mol['name'],'smiles':mol['smiles'],'atoms':mol['atoms'],'group':mol['group'],'status':category,'input_stereo_specified':mol['input_stereo_specified'],'perceived_inchikey':r.get('perceived_inchikey'),'identity_match_basis':r.get('identity_match_basis'),'connectivity_match':r.get('connectivity_match'),'perception_engines_agreeing_on_perceived_key':json.dumps(r.get('perception_engines_agreeing_on_perceived_key',[])),'perceived_keys_by_engine':json.dumps(r.get('perceived_keys_by_engine',{}),sort_keys=True),'cpu_model':r.get('cpu_model'),'node':r.get('node'),'job_id':r.get('job_id'),'reused_from':r.get('reused_from'),'failure_mode':r.get('failure_mode'),'error':r.get('error'),'dft_ran':r.get('dft_ran',bool(r.get('stages'))),'opt_seconds':walls[0],'cosmo_seconds':walls[1],'total_orca_wall_seconds':sum(walls) if all(x is not None for x in walls) else None,'elapsed_seconds':r.get('elapsed_seconds',r.get('wall_seconds_total')),'maxrss_kib':r.get('slurm_accounting',{}).get('maxrss_kib'),'surface_bytes':r.get('surface_bytes'),'returned_bytes':r.get('returned_bytes'),'surface_sha256':r.get('surface_sha256')})
assert sum(v for k,v in counts.items() if k!='denominator')==5824
receipts={p.parent.name:json.loads(p.read_text()) for p in P.glob('*/submission-receipt.json')}
submitted=sum(r['submission_tasks'] for r in receipts.values())
summary={'counts':counts,'groups':groups,'excluded_count':9,'pinned_unique':5833,'reconciliation':'5833 - 9 = 5824','identity_policy':json.loads((P/'policy.json').read_text()),'failures_by_mode':dict(collections.Counter(r['failure_mode'] for r in rows if r['status']=='failed')),'concurrency_total':json.loads((P/'policy.json').read_text())['campaign_concurrency_limit'],'new_jobs_submitted':submitted,'arrays':{g:r['array_job_id'] for g,r in receipts.items()},'reused_pilot_results':55}
for group in groups:
    observed=[r for r in rows if r['group']==group and r['total_orca_wall_seconds'] is not None]
    summary['groups'][group]['completed_timing_count']=len(observed)
    summary['groups'][group]['measured_orca_hours']=sum(r['total_orca_wall_seconds'] for r in observed)/3600
    attempts=[r for r in records.values() if r.get('input',{}).get('group')==group and r.get('status') in ['converged','failed']]
    summary['groups'][group]['measured_terminal_attempt_hours']=sum(r.get('elapsed_seconds') or r.get('slurm_accounting',{}).get('elapsed_seconds') or 0 for r in attempts)/3600
tmp=P/'summary.tmp';tmp.write_text(json.dumps(summary,indent=2)+'\n');tmp.replace(P/'summary.json')
out=ROOT/'reports/campaign-v1/results.csv'
with out.with_suffix('.tmp').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
out.with_suffix('.tmp').replace(out)
with (ROOT/'reports/campaign-v1/exclusions.csv').open('w',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=['input_inchikey','name','status','reason']);writer.writeheader();writer.writerows({k:r[k] for k in writer.fieldnames} for r in excluded['records'])
old=json.loads((ROOT/'state/campaign-status.json').read_text())
for row in rows:
    entry=old['structures'][row['input_inchikey']]
    entry.update(status=row['status'],phase=2,group=row['group'],perceived_inchikey=row['perceived_inchikey'],identity_match_basis=row['identity_match_basis'],identity_verified=bool(row['connectivity_match']))
    if row['failure_mode']:entry['failure_mode']=row['failure_mode']
    else:entry.pop('failure_mode',None)
for r in excluded['records']:old['structures'][r['input_inchikey']].update(status='excluded',phase=2,exclusion_reason=r['reason'])
old.update(phase=2,phase_status='active',phase2_authorized=True,counts=counts,pinned_unique=5833,excluded_count=9,campaign_unique=5824,group_counts=groups,full_campaign_jobs_submitted=submitted,phase2_jobs_submitted=submitted)
(ROOT/'state/campaign-status.json').write_text(json.dumps(old,indent=2)+'\n')
(ROOT/'PROGRESS.md').write_text(f'''Phase 2 AUTHORISED and ACTIVE — A-3 owner decisions implemented.

Reconciliation: 5,833 pinned − 9 explicitly excluded deuterated structures = 5,824 eligible; main ≤80 atoms 5,735, tail >80 atoms 89. No isotope-to-parent mapping is carried in campaign records.

Current eligible counts / 5,824: {counts}. Group counts and separately measured costs: {groups}. New jobs submitted: {submitted}; arrays: {summary['arrays']}.

D-IDENT: first-block connectivity match, full perceived key and agreeing engines recorded. No stereo-layer equality requirement. All 55 reference-recipe pilot results are reused, including the nine prior policy failures; historical Phase 1 evidence remains unchanged.

D-CONC (owner updated 2026-09-16): {summary['concurrency_total']} total across overlapping main chunks and remaining tail tasks, research/milan&cpu, euler09/euler10 excluded; one CPU, 4G, main 24h, tail 48h. No automatic retry or increase above the owner-authorized total cap.

Local preparation uses eight serial workers (increased from four after byte-identical reproducibility checks) and the frozen 300-candidate ETKDG/MMFF ranking. Preparation failures remain explicit and receive no substituted force field or geometry. Main-body blocks of approximately 500 targets submit as soon as prepared, originally chained with afterany dependencies at cap 28; the guarded release controller now shares the current total cap across active chunks; the already-submitted tail retains cap 4. New submissions occur only after preparation and staging digest checks, with deterministic-name squeue/sacct reconciliation and multiplexed transport/backoff. Results go to /mnt/r/plastchem-euler/results/ by scp.

Throughput steer report: reports/campaign-v1/THROUGHPUT-STEER.md.

Results table: reports/campaign-v1/results.csv. Explicit exclusions: reports/campaign-v1/exclusions.csv. Machine summary: state/campaign-v1/summary.json. Historical pilot: reports/pilot-v1/REPORT.md. ORCA completion report will include each failure, denominator, and the >80-atom tail's measured cost separately. The active goal additionally requires final thermodynamic partitioning and its audit; ORCA completion alone does not close it. Thermodynamic scope and solvent inventory: state/thermodynamics-v1/.
''')
print(json.dumps(summary))
