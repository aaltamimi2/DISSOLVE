"""Read-only reconciliation of every submitted campaign array and live scheduler envelope."""
import json,shlex,time
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'
receipts={p.parent.name:json.loads(p.read_text()) for p in P.glob('*/submission-receipt.json')}
ids=[r['array_job_id'] for r in receipts.values()];assert all(x.isdigit() for x in ids)
names=[r['name'] for r in receipts.values()]
code='''import json,subprocess,re,time
ids=IDS
names=NAMES
def command(args):
 r=subprocess.run(args,capture_output=True,text=True);return {'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
out={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'squeue':command(['squeue','-u','aaltamimi2','--name='+','.join(names),'-h','-o','%i|%j|%T|%N|%R']),'sacct':command(['sacct','-u','aaltamimi2','--starttime=2026-09-12','--name='+','.join(names),'--format=JobID,JobName%40,State%32,Elapsed,NodeList','-nP']),'arrays':{}}
for job in ids:
 r=command(['scontrol','show','job',job,'-o']);fields={k:v for k,v in re.findall(r'(\\w+)=([^ ]*)',r['stdout']) if k in ['JobId','JobName','ArrayJobId','ArrayTaskId','ArrayTaskThrottle','JobState','Partition','Account','NumCPUs','CPUs/Task','MinMemoryNode','TimeLimit','Features','ExcNodeList','Dependency','Reason']};out['arrays'][job]={'returncode':r['returncode'],'fields':fields,'stderr':r['stderr']}
print(json.dumps(out))
'''.replace('IDS',repr(ids)).replace('NAMES',repr(names))
r=run('ssh',['euler','python3 -c '+shlex.quote(code)],capture_output=True,text=True)
if r.returncode:raise RuntimeError(r.stderr)
result=json.loads(r.stdout);result['local_submission_receipts']=receipts
(P/'active-reconciliation.json').write_text(json.dumps(result,indent=2)+'\n')
assert result['squeue']['returncode']==result['sacct']['returncode']==0
for label,receipt in receipts.items():
 job=receipt['array_job_id'];a=result['arrays'][job]
 if a['returncode']:continue
 f=a['fields'];print(label,job,json.dumps(f))
 assert f.get('Partition')=='research',f
 assert f.get('Features')=='milan&cpu',f
 assert f.get('ArrayTaskThrottle')==('4' if label=='tail_gt80' else '28'),f
 assert 'euler' in f.get('ExcNodeList',''),f
print('Reconciliation saved; no submissions or scheduler mutations.')
