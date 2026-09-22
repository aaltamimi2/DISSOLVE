"""A-5 deterministic reconciliation, held arrays, PE-only initial release."""
import json,hashlib,subprocess,datetime,re
from pathlib import Path
R=Path.home()/'plastchem-euler/polymer-v1'
def run(a):return subprocess.run(a,capture_output=True,text=True,check=True).stdout
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
for line in (R/'staging.sha256').read_text().splitlines():
 h,p=line.split();assert hashlib.sha256((R/p).read_bytes()).hexdigest()==h,p
queue=run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%R'])
assert not any(x.split('|')[1].startswith('contam-') and x.split('|')[2] in ['RUNNING','COMPLETING'] for x in queue.splitlines()),'Another lane campaign occupies shared cap'
save(R/'preflight-queue.json',{'queue':queue,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
tier=[x.split('|') for x in queue.splitlines() if x.split('|')[1]=='contam-tier2-chno500700-v1'];assert len(tier)==236 and all(x[2]=='PENDING' and x[3].strip('()')=='JobHeldUser' for x in tier),'Tier2 remaining must be held'
ids={};receipts={}
for group in ['body','large']:
 p=R/group;m=json.loads((p/'manifest.json').read_text());name=m['name']
 q=run(['squeue','-u','aaltamimi2','--name='+name,'-h','-o','%i|%j|%T'])
 a=run(['sacct','-u','aaltamimi2','--starttime=2026-09-21','--name='+name,'--format=JobID,JobName%40,State,Elapsed,NodeList','-nP'])
 r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'group':group,'squeue':q,'sacct':a};save(p/'reconciliation.json',r)
 found={re.match(r'\d+',line).group() for line in (q+'\n'+a).splitlines() if re.match(r'\d+',line)}
 if found:
  assert len(found)==1;job=next(iter(found));r.update(decision='existing_no_resubmit',job_id=job)
 else:
  assert not (p/'submission.started').exists(),'Unconfirmed prior attempt; stop'
  (p/'submission.started').write_text(r['utc'])
  args=['sbatch','--parsable','--hold','--array=0-'+str(len(m['molecules'])-1)+'%64','--chdir='+str(R),'--output='+str(R/'logs/%A_%a.out'),'--error='+str(R/'logs/%A_%a.err')]
  if group=='large':args+=['--dependency=afterany:'+ids['body']]
  proc=subprocess.run(args+[str(R/(group+'.sbatch'))],capture_output=True,text=True);r.update(returncode=proc.returncode,stdout=proc.stdout,stderr=proc.stderr);save(p/'submission-attempt.json',r);assert proc.returncode==0,r
  job=proc.stdout.strip().split(';')[0];assert job.isdigit();r.update(decision='submitted',job_id=job)
 ids[group]=job;receipts[group]=r;save(p/'submission.json',r)
m=json.loads((R/'body/manifest.json').read_text());assert all(x['polymer']=='pe' and x['atoms']==38 for x in m['molecules'][:31]);released=[]
for i in range(31):
 detail=run(['scontrol','show','job',ids['body']+'_'+str(i),'-o'])
 if 'JobState=PENDING' in detail and 'Reason=JobHeldUser' in detail:
  run(['scontrol','release',ids['body']+'_'+str(i)]);released.append(i)
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'arrays':ids,'receipts':receipts,'submitted':284,'bands':{'body':244,'large':40},'initial_pe_released':31,'released_this_call':released,'remaining_held':253,'shared_cap':64,'tier2_held':236,'squeue_after':run(['squeue','-u','aaltamimi2','-h','-r','-o','%i|%j|%T|%R'])};save(R/'submission-accounting.json',r);print(json.dumps(r))
