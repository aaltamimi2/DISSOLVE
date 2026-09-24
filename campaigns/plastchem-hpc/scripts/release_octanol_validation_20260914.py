"""Release the existing octanol campaign task early; no duplicate submission."""
import json,datetime as dt
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/'state/progress-2026-09-14/octanol-release.json'
code=r'''import subprocess,json,datetime,pathlib
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'task':'54798_3677','inchikey':'KBPLFHHGFOOTCA-UHFFFAOYSA-N','operations':[]}
def call(args):
 r=subprocess.run(args,capture_output=True,text=True);out['operations'].append({'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr});return r
def save():
 (pathlib.Path.home()/'plastchem-euler/campaign-v1/octanol-validation-release.json').write_text(json.dumps(out,indent=2)+'\n')
b=call(['scontrol','show','job','54798_3677','-o']);assert b.returncode==0 and 'JobState=PENDING ' in b.stdout and 'ArrayTaskId=3677 ' in b.stdout
assert 'Partition=research ' in b.stdout and 'JobName=contam-p2-main-c007-v1 ' in b.stdout
s=call(['scontrol','show','job','54798_3678','-o']);assert s.returncode==0 and 'Dependency=afterany:54797_' in s.stdout
q=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-t','RUNNING,CONFIGURING,COMPLETING','-o','%i|%C']);assert q.returncode==0
active=sum(int(line.split('|')[1]) for line in q.stdout.splitlines());assert active+1<=32
out['active_cpus_before']=active;save()
r=call(['scontrol','update','JobId=54798_3677','Dependency=']);save();assert r.returncode==0
r=call(['scontrol','show','job','54798_3677','-o']);save();assert r.returncode==0 and 'Dependency=(null)' in r.stdout
s=call(['scontrol','show','job','54798_3678','-o']);save();assert s.returncode==0 and 'Dependency=afterany:54797_' in s.stdout
out.update(verified=True,sibling_dependency_unchanged=True,new_jobs_submitted=0,campaign_denominator=5824);save();print(json.dumps(out))
'''
start={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'status':'change_started_confirmation_pending','task':'54798_3677'};p.write_text(json.dumps(start,indent=2)+'\n')
try:
 r=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True,timeout=60)
 result={**start,'status':'verified' if r.returncode==0 else 'change_unconfirmed','returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
except Exception as e:result={**start,'status':'change_unconfirmed','error':str(e)}
p.write_text(json.dumps(result,indent=2)+'\n')
with (ROOT/'logs/campaign-changes.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='stdout'}))
if result.get('returncode')==0:print(json.dumps({k:v for k,v in json.loads(result['stdout']).items() if k!='operations'}))
