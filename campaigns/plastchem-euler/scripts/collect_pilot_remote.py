"""Read-only snapshot of this pilot's state, including explicit scheduler failures."""
import json,subprocess,time
from pathlib import Path
root=Path.home()/'plastchem-euler/pilot-v1'
submission=json.loads((root/'submission.json').read_text())
array=submission['stdout'].strip().split(';')[0]
assert array.isdigit()
def run(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
fields='JobID,JobName%40,Partition,State%32,ExitCode,ElapsedRaw,AllocCPUS,MaxRSS,NodeList,Start,End'
r={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'array_job_id':array,'squeue':run(['squeue','-u','aaltamimi2','--name=contam-p1-milan-v1','-h','-o','%i|%j|%T|%M|%N|%R']),'sacct':run(['sacct','-j',array,'--units=K','--format='+fields,'-nP']),'results':{},'logs':{}}
for p in (root/'runs').glob('*/result.json'):
 try:r['results'][p.parent.name]=json.loads(p.read_text())
 except (OSError,json.JSONDecodeError) as e:r.setdefault('read_errors',{})[str(p)]=str(e)
# Tail only this lane's failed jobs; full logs remain on Euler.
for key,record in r['results'].items():
 if record['status']=='failed':
  r['logs'][key]={p.name:'\n'.join(p.read_text(errors='replace').splitlines()[-60:]) for p in (root/'runs'/key).glob('*.out')}
print(json.dumps(r,indent=2))
