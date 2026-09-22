"""Owner-directed, in-place walltime correction; never submit or cancel jobs."""
import datetime,json,shlex
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1]
remote=r'''
import subprocess,json,datetime
out={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'actions':[]}
def call(args):
 p=subprocess.run(args,capture_output=True,text=True)
 r={'command':args,'returncode':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
 out['actions'].append(r)
 if p.returncode: raise RuntimeError(str(r))
 return p.stdout
try:
 before=call(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T|%R|%l|%j'])
 rows=[x.split('|') for x in before.splitlines()]
 body=[x for x in rows if x[0].startswith('65676_')]
 assert len(body)==244 and all(x[1]=='PENDING' for x in body), 'Body state changed; inspect before mutation'
 call(['scontrol','update','JobId=65676','TimeLimit=1-04:00:00'])
 for i in range(31):
  call(['scontrol','update',f'JobId=65676_{i}','TimeLimit=03:00:00'])
 out['readback']=call(['squeue','-h','-r','-u','aaltamimi2','-o','%i|%T|%R|%l|%j'])
 out['detail']=call(['scontrol','show','job','65676','-o'])
 out['completed']=True
except Exception as e:
 out['completed']=False;out['error']=str(e)
print(json.dumps(out))
'''
r=run('ssh',['aaltamimi2@euler.engr.wisc.edu','python3 -c '+shlex.quote(remote)],capture_output=True,text=True,timeout=180)
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'authority':'Owner/orchestrator right-size body walltime, PE3h remaining28h, no cancellation/resubmission, cap64 and other holds unchanged','transport_returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
if r.returncode==0:
 receipt['remote']=json.loads(r.stdout)
p=ROOT/'state/polymer-v1/body-walltime-rightsize.json';p.write_text(json.dumps(receipt,indent=2)+'\n')
with (ROOT/'logs/campaign-launcher.jsonl').open('a') as f:f.write(json.dumps({'utc':receipt['utc'],'event':'polymer_body_walltime_rightsize','receipt':str(p),'completed':receipt.get('remote',{}).get('completed',False)})+'\n')
print(json.dumps(receipt,indent=2))
