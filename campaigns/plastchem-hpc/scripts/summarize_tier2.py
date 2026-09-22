"""Separate A-4 accounting; never changes the 5,824-entry campaign."""
import collections,csv,json,datetime
from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'state/tier2-v1'
manifest=json.loads((P/'tier2/manifest.json').read_text())
records={p.stem:json.loads(p.read_text()) for p in (P/'records').glob('*.json')}
counts=dict(converged=0,failed=0,running=0,awaiting_verification=0,retry_pending=0,not_yet_run=0,denominator=270)
rows=[]
for m in manifest['molecules']:
 r=records.get(m['inchikey'],{});status=r.get('status','not_yet_run')
 category='converged' if status=='converged' else 'failed' if status=='failed' else 'awaiting_verification' if status=='converged_identity_pending' else 'not_yet_run' if status=='not_yet_run' else 'running'
 if r.get('execution_outcome')=='time_limit' or r.get('failure_mode') in ['slurm_timeout','walltime_censored','scheduler_signal_10']:category='retry_pending'
 counts[category]+=1
 stages=r.get('stages',{});walls=[stages.get(s,{}).get('wall_seconds') for s in ['opt','cosmo']]
 rows.append(dict(input_inchikey=m['inchikey'],name=m['name'],smiles=m['smiles'],cas=m['cas'],tier='CHNO_500_700',atoms=m['atoms'],status=category,cpu_model=r.get('cpu_model'),perceived_inchikey=r.get('perceived_inchikey'),identity_match_basis=r.get('identity_match_basis'),perceived_keys_by_engine=json.dumps(r.get('perceived_keys_by_engine',{})),perception_engines_agreeing_on_perceived_key=json.dumps(r.get('perception_engines_agreeing_on_perceived_key',[])),failure_mode=r.get('failure_mode'),elapsed_seconds=r.get('elapsed_seconds'),orca_wall_seconds=sum(walls) if all(x is not None for x in walls) else None,surface_sha256=r.get('surface_sha256'),archive_path=r.get('archive_path')))
assert sum(v for k,v in counts.items() if k!='denominator')==270
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':counts,'groups':{'tier2':counts},'excluded_count':0,'pinned_unique':270,'failures_by_mode':dict(collections.Counter(r['failure_mode'] for r in rows if r['status']=='failed')),'measured_orca_hours':sum(r['orca_wall_seconds'] or 0 for r in rows)/3600,'projection_cpu_hours':1770,'review_threshold_cpu_hours':3540}
tmp=P/'summary.tmp';tmp.write_text(json.dumps(summary,indent=2)+'\n');tmp.replace(P/'summary.json')
out=Path('/mnt/r/plastchem-euler/tier2-v1');out.mkdir(exist_ok=True,parents=True)
with (out/'results.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps(summary))
