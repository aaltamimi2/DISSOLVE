"""Restore the authorized main-body cap after Main 00 drains; no submissions."""
import datetime,json
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1]
receipt=ROOT/'state/campaign-v1/main01-throttle28.json'
code=r'''import subprocess,json
out={'operations':[]}
def call(args):
 r=subprocess.run(args,capture_output=True,text=True)
 out['operations'].append({'command':args,'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
 assert r.returncode==0,out
 return r.stdout
q=call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C'])
rows=[r.split('|') for r in q.splitlines()]
assert not any(r[1]=='contam-p2-main-c000-v1' for r in rows),'Main00 still queued'
call(['sacct','-n','-X','-j','54755','--format=JobID,State,ExitCode','-P'])
others=[r for r in rows if r[2]!='PENDING' and r[1]!='contam-p2-main-c001-v1']
assert all(r[1]=='contam-p2-tail_gt80-v1' and r[3]=='1' for r in others)
assert sum(int(r[3]) for r in others)<=4
before=call(['scontrol','show','job','54786','-o'])
assert 'JobName=contam-p2-main-c001-v1 ' in before and 'Partition=research ' in before
assert 'ArrayTaskThrottle=27 ' in before or 'ArrayTaskThrottle=28 ' in before
call(['scontrol','update','JobId=54786','ArrayTaskThrottle=28'])
after=call(['scontrol','show','job','54786','-o'])
assert 'ArrayTaskThrottle=28 ' in after and 'Dependency=(null)' in after
call(['squeue','-h','-r','-u','aaltamimi2','-p','research','-o','%i|%j|%T|%C'])
out.update(verified=True,maximum_concurrency=32,tail_modified=False)
print(json.dumps(out))
'''
r={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'action':'Main01 throttle27to28 after Main00 terminal','status':'change_unconfirmed'}
receipt.write_text(json.dumps(r,indent=2)+'\n')
try:
 result=run('ssh',['euler','python3 -'],input=code,text=True,capture_output=True,timeout=60)
 r.update(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
 if result.returncode==0 and json.loads(result.stdout)['verified']:r['status']='verified'
except Exception as exc:r['error']=str(exc)
receipt.write_text(json.dumps(r,indent=2)+'\n')
with (ROOT/'logs/campaign-changes.jsonl').open('a') as f:f.write(json.dumps(r)+'\n')
print(json.dumps({k:v for k,v in r.items() if k not in ['stdout']}))
