"""Deterministic, reconciled bounded Phase8 submission; run on Euler login only."""
import sys,json,subprocess,datetime,re,hashlib
from pathlib import Path
D=Path.home()/'plastchem-euler/phase8-v1'
def run(args):return subprocess.run(args,check=True,capture_output=True,text=True).stdout
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
mode=sys.argv[1];config={'81':('contam-phase81-v1','0-39%24','phase81.sbatch'),'82pilot':('contam-phase82-lle-pilot-v1','0-1%2','phase82pilot.sbatch'),'82partition':('contam-phase82-partition-v1','0-7%8','phase82partition.sbatch'),'82lle':('contam-phase82-lle-v1','0-511%24','phase82lle.sbatch')}
name,array,script=config[mode];now=datetime.datetime.now(datetime.timezone.utc).isoformat()
if mode in ['82pilot','82lle']:
 units=json.loads((D/'validation-inputs.json').read_text())['lle_units'];pilot=[i for i,u in enumerate(units) if u['solute']=='DEP' and u['regime']=='RT' and u['solvent'] in ['water','dichloromethane']];assert len(pilot)==2
 if mode=='82pilot':array=','.join(map(str,pilot))+'%2'
 else:array=','.join(str(i) for i in range(len(units)) if i not in pilot)+'%16'
q=run(['squeue','-h','-r','-u','aaltamimi2','--name='+name,'-o','%i|%j|%T'])
a=run(['sacct','-nP','-u','aaltamimi2','--starttime=2026-09-23','--name='+name,'--format=JobID,JobName%40,State,Elapsed,NodeList'])
r={'utc':now,'mode':mode,'squeue':q,'sacct':a,'shared_cap':64,'throttle':int(array.split('%')[1])};save(D/(mode+'-reconciliation.json'),r)
found={line.split('|')[0].split('_')[0].split('.')[0] for line in (q+'\n'+a).splitlines() if re.match(r'^\d',line)}
if found:
 assert len(found)==1,found;r.update(decision='existing_no_resubmission',job_id=next(iter(found)))
else:
 queue=run(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C']);r['queue_before']=queue
 # Budget other arrays using their maxima, never merely their current running count.
 ids={line.split('|')[0].split('_')[0] for line in queue.splitlines() if line.strip()};bounds={};details={}
 for job in ids:
  lines=[l.split('|') for l in queue.splitlines() if l.split('|')[0].split('_')[0]==job]
  detail=run(['scontrol','show','job',lines[0][0],'-o']);details[job]=detail;caps=re.findall(r'ArrayTaskThrottle=(\d+)',detail)
  bounds[job]=min(len(lines),int(caps[0])) if caps else sum(int(l[3]) for l in lines)
 reserve=sum(bounds.values());r['other_array_upper_bounds']=bounds
 if '63873' in bounds and '65677' in bounds:
  assert 'Dependency=afterany:65677_*(unfulfilled)' in details['63873']
  assert all(l.split('|')[2]=='PENDING' for l in queue.splitlines() if l.split('|')[0].split('_')[0]=='63873')
  reserve-=min(bounds['63873'],bounds['65677']);r['nonoverlap_evidence']=details['63873']
 r['other_maximum_simultaneous']=reserve
 assert reserve+r['throttle']<=64,(reserve,r['throttle'],bounds)
 args=['sbatch','--parsable','--job-name='+name,'--array='+array,'--chdir='+str(D),'--output='+str(D/'logs/%A_%a.out'),'--error='+str(D/'logs/%A_%a.err')]
 if mode=='81':args+=['--hold']
 args+=[str(D/script)]
 r['commands']=[args];save(D/(mode+'-submission-unconfirmed.json'),r)
 p=subprocess.run(args,capture_output=True,text=True);r.update(returncode=p.returncode,stdout=p.stdout,stderr=p.stderr);save(D/(mode+'-submission-attempt.json'),r);assert p.returncode==0,r
 job=p.stdout.strip().split(';')[0];assert job.isdigit();r.update(decision='submitted',job_id=job)
 if mode=='81':
  release=['scontrol','release',job+'_0'];run(release);r['commands'].append(release);r['initial_validation_unit_released']=0;r['other_units_held']=39
r['queue_after']=run(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C|%R'])
save(D/(mode+'-submission.json'),r);print(json.dumps(r))
