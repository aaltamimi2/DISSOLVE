"""Reconcile a bounded, dependency-bound staging recovery, never blind retry."""
import json,subprocess,datetime,re
from pathlib import Path
D=Path.home()/'plastchem-euler/phase83-v1'
def call(a):return subprocess.check_output(a,text=True)
name='contam-phase83-staging-repair-v1'
q=call(['squeue','-h','-r','-u','aaltamimi2','--name='+name,'-o','%i|%T'])
a=call(['sacct','-nP','-u','aaltamimi2','--starttime=2026-09-23','--name='+name,'--format=JobID,JobName%60,State,Elapsed,NodeList'])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'squeue':q,'sacct':a,'name':name}
found={l.split('|')[0].split('_')[0].split('.')[0] for l in (q+'\n'+a).splitlines() if re.match(r'^\d',l)}
if found:
 assert len(found)==1;r.update(job_id=next(iter(found)),decision='already_exists_no_resubmission')
else:
 failures=[]
 for p in (D/'phase82/lle').glob('*/failure.json'):
  e=json.loads(p.read_text())
  if 'FileNotFoundError' in e['error'] and 'phase83-v1/phase8_lle.py' in e['error']:failures.append(int(p.parent.name))
 indices=sorted({n//64 for n in failures});assert indices
 # The new array cannot overlap 68169; no mutation of any other array.
 queue=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%T|%C']);lines=[l.split('|') for l in queue.splitlines()];bounds={};details={}
 for job in {l[0].split('_')[0] for l in lines if not l[0].startswith('68169_')}:
  group=[l for l in lines if l[0].split('_')[0]==job];detail=call(['scontrol','show','job',group[0][0],'-o']);details[job]=detail;m=re.search(r'ArrayTaskThrottle=(\d+)',detail);bounds[job]=min(len(group),int(m.group(1))) if m else sum(int(l[2]) for l in group)
 reserve=sum(bounds.values())
 if '63873' in bounds and '65677' in bounds:
  assert 'Dependency=afterany:65677_*(unfulfilled)' in details['63873'];assert all(l[1]=='PENDING' for l in lines if l[0].startswith('63873_'));reserve-=min(bounds['63873'],bounds['65677'])
 assert reserve+max(27,min(16,len(indices)))<=64
 (D/'calibration-repair-map.json').write_text(json.dumps(indices)+'\n')
 script=D/'repair.sbatch';script.write_text((D/'phase83.sbatch').read_text().replace('04:00:00','00:30:00').replace('phase83_worker.py "$SLURM_ARRAY_TASK_ID" "$1"','repair_phase83_staging.py "$SLURM_ARRAY_TASK_ID"'))
 command=['sbatch','--parsable','--job-name='+name,'--array=0-'+str(len(indices)-1)+'%16','--dependency=afterany:68169','--chdir='+str(D),'--output='+str(D/'logs/%A_%a.out'),'--error='+str(D/'logs/%A_%a.err'),str(script)]
 r.update(command=command,indices=indices,affected_binary_systems=failures,other_maximum=reserve,shared_cap=64,dependency='afterany:68169')
 (D/'calibration-repair-submission-unconfirmed.json').write_text(json.dumps(r,indent=2)+'\n')
 response=call(command).strip();job=response.split(';')[0];assert job.isdigit();r.update(job_id=job,decision='submitted')
r['scheduler_readback']=call(['scontrol','show','job',r['job_id'],'-o'])
(D/'calibration-repair-submission.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r))
