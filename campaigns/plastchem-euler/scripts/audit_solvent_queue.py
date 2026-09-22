"""Read-only reconciliation of missing solvent references to submitted campaign tasks."""
import csv, datetime, hashlib, json, collections, os
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
D=Path(os.environ.get('PLASTCHEM_SOLVENT_AUDIT_ROOT', '/mnt/r/plastchem-euler/audits/solvent-queue-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H%M%SZ')));D.mkdir(parents=True,exist_ok=False)
def read(p):return json.loads(p.read_text())
snap_path=ROOT/'state/campaign-v1/latest-snapshot.json';snap_bytes=snap_path.read_bytes();snap=json.loads(snap_bytes)
registry_path=ROOT/'state/thermodynamics-v1/library-registry.json';registry_bytes=registry_path.read_bytes();registry=json.loads(registry_bytes)
(D/'scheduler-snapshot.json').write_bytes(snap_bytes)
(D/'library-registry.json').write_bytes(registry_bytes)
molecules={}
for group in ['main_le80','tail_gt80']:
 for m in read(ROOT/f'state/campaign-v1/{group}/manifest.json')['molecules']:molecules[m['inchikey']]=m
accounting={line.split('|')[0]:line.split('|') for line in snap['sacct'].splitlines()}
rows=[]
for s in registry['solvents']:
 key=s.get('source_identity');m=molecules.get(key);task='';chunk='';state='';reason='';queue_confirmed=False
 if s['status']=='pending' and m:
  for group,indices in snap['array_indices'].items():
   if (group=='tail_gt80') != (m['group']=='tail_gt80'):continue
   if m['array_index'] in indices:
    assert not task,'Multiple submitted arrays for solvent task'
    chunk=group;task=f"{snap['groups'][group]}_{m['array_index']}"
  if task in accounting:state=accounting[task][3]
  for line in snap['squeue'].splitlines():
   cols=line.split('|')
   if task and cols[0].split('_')[0]==task.split('_')[0] and cols[2]=='PENDING':
    spec=cols[0].split('_',1)[1].strip('[]').split('%')[0]
    for token in spec.split(','):
     ends=token.split('-');low=int(ends[0]);high=int(ends[-1])
     if low<=m['array_index']<=high:reason=cols[-1];queue_confirmed=True
 rows.append({'solvent':s['solvent_key'],'library_status':s['status'],'input_inchikey':key or '',
              'campaign_chunk':chunk,'logical_task':task,'sacct_state':state,'squeue_task_pending_confirmed':queue_confirmed,'array_pending_reason':reason,
              'scheduler_snapshot_utc':snap['utc'],'surface_sha256':s.get('surface_sha256','')})
pending=[r for r in rows if r['library_status']=='pending']
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scheduler_snapshot_utc':snap['utc'],
 'registry_sha256':hashlib.sha256(registry_bytes).hexdigest(),
 'scheduler_snapshot_sha256':hashlib.sha256(snap_bytes).hexdigest(),
 'requested':len(rows),'status_counts':dict(collections.Counter(r['library_status'] for r in rows)),
 'pending_with_submitted_task':sum(bool(r['logical_task']) for r in pending),
 'pending_explicitly_in_squeue_ranges':sum(r['squeue_task_pending_confirmed'] for r in pending),
 'pending_accounting_states':dict(collections.Counter(r['sacct_state'] for r in pending)),
 'pending_without_submitted_task':[r['solvent'] for r in pending if not r['logical_task']],
 'scope':'Read-only scheduling reconciliation; no job or dependency changes and no solvent identity decision.'}
with (D/'solvent-queue.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(D/'audit_solvent_queue.py').write_bytes(Path(__file__).read_bytes())
(D/'REPORT.md').write_text('# Solvent scheduling reconciliation\n\n'
 f"Snapshot: {snap['utc']}. Requested references: {len(rows)}. "
 f"Library states: {summary['status_counts']}.\n\n"
 f"Pending references matched to submitted tasks: {summary['pending_with_submitted_task']}/{len(pending)}; "
 f"explicitly present in pending queue ranges: {summary['pending_explicitly_in_squeue_ranges']}/{len(pending)}.\n\n"
 'This is scheduling evidence, not chemical or numerical validation. Xylene identity remains an owner question. '
 'The campaign snapshot and library registry are captured separately; their saved bytes are pinned. '
 'No job, dependency, recipe, or identity policy was changed.\n\n'
 f'Reproduce with `{ROOT}/scripts/audit_solvent_queue.py`; each run creates a new dated audit directory.\n')
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file()))
print(json.dumps(summary,indent=2))
print(str(D))
