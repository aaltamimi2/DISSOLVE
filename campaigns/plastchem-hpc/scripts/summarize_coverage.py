"""A-13: separate coverage accounting (copied from summarize_halogen.py), over every chunk manifest; never changes the
earlier tiers. Writes state/coverage-v1/summary.json and /mnt/r/plastchem-euler/coverage-v1/results.csv. A structure
two groups list (c00 holds the owner's requests ahead of the work-list order) counts once: in the group that ran it,
else the first that lists it.

Cost: measured ORCA wall (opt + COSMO stages, AMD EPYC 7763) per converged structure against the campaign fit
seconds = exp(-0.687758) * atoms^2.305719 * 1.113048 (atoms with H; fitted up to 107 atoms)."""
import collections,csv,json,datetime,math
from pathlib import Path
R=Path(__file__).resolve().parents[1];P=R/'state/coverage-v1'
def fit_seconds(atoms):return math.exp(-0.687758)*atoms**2.305719*1.113048
chunks={d.name:json.loads((d/'manifest.json').read_text()) for d in sorted(P.glob('[cr][0-9][0-9]')) if (d/'manifest.json').exists()}
records={p.stem:json.loads(p.read_text()) for p in (P/'records').glob('*.json')}
submitted={g for g in chunks if (P/g/'staging-summary.json').exists()}
listed=collections.defaultdict(list)
for group,manifest in chunks.items():
 for m in manifest['molecules']:listed[m['inchikey']].append(group)
counted_in={k:(records.get(k,{}).get('group') if records.get(k,{}).get('group') in groups else groups[0]) for k,groups in listed.items()}
rows=[];by_chunk={}
for group,manifest in chunks.items():
 mine=[m for m in manifest['molecules'] if counted_in[m['inchikey']]==group]
 counts=dict(converged=0,failed=0,rejected=0,running=0,awaiting_verification=0,retry_pending=0,not_yet_run=0,not_staged=0,denominator=len(mine))
 for m in mine:
  r=records.get(m['inchikey'],{});status=r.get('status','not_yet_run')
  category='converged' if status=='converged' else 'failed' if status=='failed' else 'awaiting_verification' if status=='converged_identity_pending' else 'not_yet_run' if status=='not_yet_run' else 'running'
  if category=='failed' and r.get('failure_mode')=='return_integrity_or_connectivity':category='rejected'  # D-IDENT: a result, not a failure
  if r.get('execution_outcome')=='time_limit' or r.get('failure_mode') in ['slurm_timeout','walltime_censored','scheduler_signal_10','scheduler_signal_15','slurm_preempted'] or (r.get('status')=='failed' and str((r.get('slurm_accounting') or {}).get('state','')).startswith('PREEMPTED')):category='retry_pending'
  if category=='not_yet_run' and group not in submitted:category='not_staged'
  counts[category]+=1
  stages=r.get('stages',{});walls=[stages.get(s,{}).get('wall_seconds') for s in ['opt','cosmo']]
  wall=sum(walls) if all(x is not None for x in walls) else None
  rows.append(dict(chunk=group,worklist_order=m['worklist_order'],input_inchikey=m['inchikey'],name=m['name'],smiles=m['smiles'],cas=m['cas'],kind=m['kind'],elements=m['elements'],tier=m['tier'],atoms=m['atoms'],status=category,cpu_model=r.get('cpu_model'),node=r.get('node'),perceived_inchikey=r.get('perceived_inchikey'),identity_match_basis=r.get('identity_match_basis'),perceived_keys_by_engine=json.dumps(r.get('perceived_keys_by_engine',{})),perception_engines_agreeing_on_perceived_key=json.dumps(r.get('perception_engines_agreeing_on_perceived_key',[])),failure_mode=r.get('failure_mode'),elapsed_seconds=r.get('elapsed_seconds'),orca_wall_seconds=wall,fit_seconds=round(fit_seconds(m['atoms']),1),surface_sha256=r.get('surface_sha256'),archive_path=r.get('archive_path')))
 assert sum(v for k,v in counts.items() if k!='denominator')==len(mine)
 by_chunk[group]=counts
counts=collections.Counter()
for c in by_chunk.values():counts.update(c)
measured=[r for r in rows if r['orca_wall_seconds'] is not None]
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'counts':dict(counts),'chunks':by_chunk,
 'failures_by_mode':dict(collections.Counter(r['failure_mode'] for r in rows if r['status'] in ('failed','rejected','retry_pending'))),
 'measured_orca_hours':sum(r['orca_wall_seconds'] for r in measured)/3600,
 'measured_structures':len(measured),'fit_hours_of_measured':sum(r['fit_seconds'] for r in measured)/3600,
 'cpu_models':dict(collections.Counter(r['cpu_model'] for r in rows if r['cpu_model']))}
tmp=P/'summary.tmp';tmp.write_text(json.dumps(summary,indent=2)+'\n');tmp.replace(P/'summary.json')
out=Path('/mnt/r/plastchem-euler/coverage-v1');out.mkdir(exist_ok=True,parents=True)
if rows:
 with (out/'results.csv').open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
print(json.dumps(summary))
