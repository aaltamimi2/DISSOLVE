"""Owner-authorized Main 01 overlap; reconcile/readback, never submit a job."""
import datetime as dt,json
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1]
receipt=ROOT/'state/campaign-v1/main01-overlap-release.json'
code=r'''import subprocess,json,datetime,pathlib
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'job_id':'54786','authority':'Orchestrator 2026-09-14 17:45 CDT','operations':[]}
def call(args):
 r=subprocess.run(args,capture_output=True,text=True);v={'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr};out['operations'].append(v);return r
def save():
 p=pathlib.Path.home()/'plastchem-euler/campaign-v1/main01-overlap-release.json';p.write_text(json.dumps(out,indent=2)+'\n')
before=call(['scontrol','show','job','54786','-o']);assert before.returncode==0
assert 'JobName=contam-p2-main-c001-v1 ' in before.stdout and 'Partition=research ' in before.stdout
q=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C'])
assert q.returncode==0
rows=[s.split('|') for s in q.stdout.splitlines()]
old=[r for r in rows if r[1]=='contam-p2-main-c000-v1'];assert len(old)<=2
assert all(r[2]=='RUNNING' and r[3]=='1' for r in old)
others=[r for r in rows if r[2]!='PENDING' and r[1]!='contam-p2-main-c001-v1']
assert sum(int(r[3]) for r in others)+24+1<=32,'Insufficient room including reserved octanol slot'
out['active_other_cpus_before']=sum(int(r[3]) for r in others);out['reserved_octanol_cpu']=1
save()
r=call(['scontrol','update','JobId=54786','ArrayTaskThrottle=24']);save();assert r.returncode==0
check=call(['scontrol','show','job','54786','-o']);save();assert check.returncode==0 and 'ArrayTaskThrottle=24 ' in check.stdout
r=call(['scontrol','update','JobId=54786','Dependency=']);save();assert r.returncode==0
check=call(['scontrol','show','job','54786','-o']);save();assert check.returncode==0
assert 'Dependency=(null)' in check.stdout and 'ArrayTaskThrottle=24 ' in check.stdout
q=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C']);save();assert q.returncode==0
out['verified']=True;out['tail_modified']=False;out['maximum_with_current_main00_tail_and_reserved_octanol']=out['active_other_cpus_before']+24+1;save();print(json.dumps(out))
'''
start={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'status':'change_started_confirmation_pending','job_id':'54786'}
receipt.write_text(json.dumps(start,indent=2)+'\n')
try:
 r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True,timeout=60)
 result={**start,'status':'verified' if r.returncode==0 else 'change_unconfirmed','returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
except Exception as e:result={**start,'status':'change_unconfirmed','error':str(e)}
receipt.write_text(json.dumps(result,indent=2)+'\n')
with (ROOT/'logs/campaign-changes.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['stdout']}))
if result.get('returncode')==0:
 d=json.loads(result['stdout']);print(json.dumps({k:v for k,v in d.items() if k!='operations'}))
