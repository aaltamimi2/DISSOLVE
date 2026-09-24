"""Reconciled phase83 submission with a conservative shared-cap bound."""
import json,subprocess,datetime,re,sys,hashlib
from pathlib import Path
D=Path.home()/'plastchem-euler/phase83-v1'
def run(args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
mode=sys.argv[1];assert mode in ['calibration','production']
cohort=json.loads((D/'cohort.json').read_text());indices=cohort['calibration_indices'];throttle=16
assert hashlib.sha256((D/'phase8_lle.py').read_bytes()).hexdigest()=='1fee9216925b91877eaf79840e010330801b93adf41b0b116c774358df0296f9'
if mode=='production':
 gate=json.loads((D/'cost-gate.json').read_text());assert gate['authorized_cpu_hours']<=6000 and gate['decision']=='continue'
 assert gate['cohort_sha256']==hashlib.sha256((D/'cohort.json').read_bytes()).hexdigest()
 verified=json.loads((D/'all-surfaces-verified.json').read_text());assert verified['cohort_sha256']==gate['cohort_sha256'] and verified['surface_count']==len(cohort['rows'])
 indices=[i for i in range(len(cohort['rows'])) if i not in indices]
 throttle=27
name='contam-phase83-'+mode+'-v1'
q=run(['squeue','-h','-r','-u','aaltamimi2','--name='+name,'-o','%i|%j|%T'])
a=run(['sacct','-nP','-u','aaltamimi2','--starttime=2026-09-23','--name='+name,'--format=JobID,JobName%60,State,Elapsed,NodeList'])
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'squeue':q,'sacct':a,'mode':mode,'throttle':throttle,'shared_cap':64}
save(D/(mode+'-reconciliation.json'),r)
found={l.split('|')[0].split('_')[0].split('.')[0] for l in (q+'\n'+a).splitlines() if re.match(r'^\d',l)}
if found:
 assert len(found)==1,found;r.update(decision='existing_no_resubmission',job_id=next(iter(found)))
else:
 queue=run(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C']);r['queue_before']=queue
 lines=[l.split('|') for l in queue.splitlines() if l.strip()];bounds={};details={}
 for job in {l[0].split('_')[0] for l in lines}:
  group=[l for l in lines if l[0].split('_')[0]==job];detail=run(['scontrol','show','job',group[0][0],'-o']);details[job]=detail
  caps=re.findall(r'ArrayTaskThrottle=(\d+)',detail);bounds[job]=min(len(group),int(caps[0])) if caps else sum(int(l[3]) for l in group)
 reserve=sum(bounds.values())
 if '63873' in bounds and '65677' in bounds:
  assert 'Dependency=afterany:65677_*(unfulfilled)' in details['63873']
  assert all(l[2]=='PENDING' for l in lines if l[0].split('_')[0]=='63873')
  reserve-=min(bounds['63873'],bounds['65677']);r['nonoverlap_evidence']=details['63873']
 r.update(other_array_upper_bounds=bounds,other_maximum_simultaneous=reserve)
 assert reserve+throttle<=64,(reserve,throttle,bounds)
 config=run(['scontrol','show','config']);max_array=int(re.search(r'MaxArraySize\s*=\s*(\d+)',config).group(1));assert len(indices)<=max_array
 mapping=D/(mode+'-map.json');save(mapping,indices)
 args=['sbatch','--parsable','--job-name='+name,'--array=0-'+str(len(indices)-1)+'%'+str(throttle),'--chdir='+str(D),'--output='+str(D/'logs/%A_%a.out'),'--error='+str(D/'logs/%A_%a.err'),str(D/'phase83.sbatch'),str(mapping)]
 r['command']=args;save(D/(mode+'-submission-unconfirmed.json'),r)
 p=subprocess.run(args,capture_output=True,text=True);r.update(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr);save(D/(mode+'-submission-attempt.json'),r);assert p.returncode==0,r
 job=p.stdout.strip().split(';')[0];assert job.isdigit();r.update(decision='submitted',job_id=job,task_count=len(indices))
r['queue_after']=run(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C|%R'])
save(D/(mode+'-submission.json'),r)
print(json.dumps({k:v for k,v in r.items() if k not in ['squeue','sacct','queue_before','queue_after','nonoverlap_evidence']},indent=2))
