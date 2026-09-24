"""Read-only Euler status snapshot; bounded metadata, no collector changes."""
import json,datetime,fcntl
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1')
code='''import pathlib,json,collections,subprocess,datetime
D=pathlib.Path.home()/'plastchem-euler/phase83-v1'
q=subprocess.check_output(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C|%R'],text=True)
counts=collections.Counter(l.split('|')[1]+'|'+l.split('|')[2] for l in q.splitlines())
completed={p.parent.name:json.loads(p.read_text()) for p in (D/'results').glob('*/complete.json')}
failures={p.parent.name:json.loads(p.read_text()) for p in (D/'results').glob('*/task-failure.json')}
errors={p.name:p.read_text()[-3000:] for p in (D/'logs').glob('*.err') if p.stat().st_size}
repair=json.loads((D/'calibration-repair-submission.json').read_text()) if (D/'calibration-repair-submission.json').exists() else None
jobs=sorted({'68169'}|{json.loads(p.read_text())['job_id'] for p in D.glob('*-submission.json') if 'job_id' in json.loads(p.read_text())})
accounting=subprocess.check_output(['sacct','-nP','-j',','.join(jobs),'--format=JobID%50,State%30,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,TotalCPU,UserCPU,SystemCPU'],text=True)
initial={p.parent.parent.name:json.loads(p.read_text()) for p in (D/'results').glob('*/attempts/initial-complete.json')}
fast=(D/'production-submission.json').exists()
progress={};polymers=set()
if fast:
 for key,row in list(initial.items())+list(completed.items()):
  progress.update(row['lle_statuses'])
  polymers.update((key,name) for name in row['outputs'])
 for line in q.splitlines():
  fields=line.split('|')
  if fields[2]!='RUNNING' or not fields[1].startswith('contam-phase83-'):continue
  log=D/'logs'/(fields[0]+'.out')
  if not log.exists():continue
  for entry in log.read_text().splitlines():
   values=entry.split()
   if len(values)>=3 and values[0]=='LLE_DONE':progress[values[1]]=values[2]
   if len(values)>=3 and values[0]=='POLYMER_DONE':polymers.add((f'{int(values[1]):05d}',values[2]+'.csv'))

print(json.dumps({'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'queue':q,'queue_counts':dict(counts),
'running_cpus':sum(int(l.split('|')[3]) for l in q.splitlines() if l.split('|')[2]=='RUNNING'),
'completed':completed,'initial_attempts':initial,'calibration_repair':repair,'task_failures':failures,'stderr':errors,'accounting':accounting,
'polymer_tables':len(polymers) if fast else len(list((D/'results').glob('*/*.csv'))),
'lle_complete':sum(v!='failed' for v in progress.values()) if fast else len(list((D/'phase82/lle').glob('*/complete.json'))),
'lle_progress_status_counts':dict(collections.Counter(progress.values())) if fast else None,
'lle_historical_failure_records':None if fast else len(list((D/'phase82/lle').glob('*/failure.json'))),
'lle_unresolved_execution_failures':sum(v=='failed' for v in progress.values()) if fast else sum(not (p.parent/'result.json').exists() for p in (D/'phase82/lle').glob('*/failure.json'))}))
'''
lock=(R/'state/phase83-v1/status.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True);assert r.returncode==0,r.stderr
snapshot=json.loads(r.stdout);state=R/'state/phase83-v1';state.mkdir(exist_ok=True)
latest=D/'latest-status.json';tmp=latest.with_suffix('.tmp');tmp.write_text(json.dumps(snapshot,indent=2)+'\n');tmp.replace(latest)
# Keep the growing full snapshot on the bulk volume, not the nearly-full root disk.
link=state/'latest.json'
if link.is_symlink():assert link.resolve()==latest
else:
 if link.exists():link.unlink()
 link.symlink_to(latest)
print(json.dumps({k:v for k,v in snapshot.items() if k not in ['queue','completed','accounting','initial_attempts','calibration_repair']},indent=2))
print('Completed contaminant tasks:',len(snapshot['completed']))
