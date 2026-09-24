"""Gate on complete fresh reproduction, then stage and reconcile the tail handoff.

Original allocation stays running and counted, with its Python process stopped
only while disjoint helpers own the other units. No sbatch is retried blindly.
"""
import datetime,fcntl,hashlib,json,shutil,time
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1')

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def transport(kind,args,**kwargs):
    p=run(kind,args,capture_output=True,text=True,timeout=180,**kwargs)
    assert p.returncode==0,p.stderr
    return p.stdout

def main():
    lock=(R/'state/phase9-v1/tail-launch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    gate=json.loads((D/'recovery-gate-fresh-local/summary.json').read_text())
    assert gate['status']=='passed' and gate['systems']==2240 and gate['mismatch_count']==0
    shutil.copyfile(D/'recovery-gate-fresh-local/summary.json',D/'recovery-gate-fresh-passed.json')
    names=['phase9_tail_common.py','phase9_tail_helper.py','phase9_tail_supervisor.py','submit_phase9_tail_remote.py','phase9_throttle_remote_v4.py','submit_phase9_recovery_v2_remote.py','reconcile_phase9_failures_remote.py']
    pins={}
    for name in names:shutil.copyfile(R/'scripts'/name,D/name);pins[name]=sha(D/name)
    pins.update({name:sha(D/name) for name in ['phase9_failure_policy.py','phase9_retry_entry.py']})
    (D/'tail-code-pins.json').write_text(json.dumps(pins,indent=2)+'\n')
    transport('scp',[*[str(D/name) for name in names+['tail-code-pins.json','recovery-gate-fresh-passed.json']],'euler:plastchem-euler/phase9-v1/'])
    transport('scp',[str(R/'scripts/phase83_throttle_remote.py'),'euler:plastchem-euler/phase83-v1/phase83_throttle_remote.py'])
    code=r'''
import datetime,fcntl,hashlib,json,subprocess
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1'
lock=(D.parent/'phase83-v1/throttle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
for name,digest in json.loads((D/'tail-code-pins.json').read_text()).items():assert hashlib.sha256((D/name).read_bytes()).hexdigest()==digest,name
active=D/'active-throttle-controller.json';old=json.loads(active.read_text())
backup=D/'active-throttle-controller-v3.json'
if not backup.exists():backup.write_bytes(active.read_bytes())
new=dict(filename='phase9_throttle_remote_v4.py',sha256=hashlib.sha256((D/'phase9_throttle_remote_v4.py').read_bytes()).hexdigest(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),authority='A-9 item 4: tail has production priority; shared cap remains 64')
tmp=active.with_suffix('.tmp');tmp.write_text(json.dumps(new,indent=2)+'\n');tmp.replace(active)
print(json.dumps(new))
'''
    actual=json.loads(transport('ssh',['euler','python3 -'],input=code))
    (D/'active-throttle-controller.json').write_text(json.dumps(actual,indent=2)+'\n')
    code=r'''
import datetime,fcntl,json,os,subprocess
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1';root=D/'tail-0002-v1';root.mkdir(exist_ok=True)
lock=(root/'start.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
receipt=root/'supervisor-start.json'
if receipt.exists():
 print(receipt.read_text())
else:
 q=subprocess.run(['squeue','-h','-j','68503_2','-o','%i|%T|%N'],text=True,capture_output=True,check=True).stdout
 assert q.strip()=='68503_2|RUNNING|euler143',q
 log=(D/'logs/tail-0002-supervisor.log').open('a')
 args=['srun','--overlap','--jobid=68506','--nodes=1','--ntasks=1','--cpus-per-task=1','--immediate=30','python3',str(D/'phase9_tail_supervisor.py'),'2']
 child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
 value=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),pid=child.pid,command=args,squeue_before=q)
 receipt.write_text(json.dumps(value,indent=2)+'\n');print(json.dumps(value))
'''
    started=json.loads(transport('ssh',['euler','python3 -'],input=code))
    local=D/'tail-0002-v1';local.mkdir(exist_ok=True)
    (local/'supervisor-start.json').write_text(json.dumps(started,indent=2)+'\n')
    # A single bounded wait for readiness; no production submission on failure.
    code=r'''
import json,time
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1';root=D/'tail-0002-v1'
for _ in range(55):
 p=root/'handoff-state.json'
 if p.exists():
  state=json.loads(p.read_text())
  if state['status']=='paused_helpers_permitted':
   print(json.dumps(dict(state=state,assignment=json.loads((root/'assignment.json').read_text()))));break
  assert state['status']=='pause_intent',state
 time.sleep(1)
else:raise RuntimeError('Tail pause not yet confirmed; reconcile supervisor before continuing')
'''
    ready=json.loads(transport('ssh',['euler','python3 -'],input=code))
    for name,value in [('handoff-state.json',ready['state']),('assignment.json',ready['assignment'])]:
        # Exact formatting is pinned by the remote canonical writer.
        (local/name).write_text(json.dumps(value,indent=2)+'\n')
    assert sha(local/'assignment.json')==ready['state']['assignment_sha256']
    receipt=json.loads(transport('ssh',['euler','python3 ~/plastchem-euler/phase9-v1/submit_phase9_tail_remote.py']))
    (D/'production-retry-tail0002-submission.json').write_text(json.dumps(receipt,indent=2)+'\n')
    result=json.loads(transport('ssh',['euler','python3 ~/plastchem-euler/phase9-v1/phase9_throttle_remote_v4.py']))
    (D/'tail-first-controller-readback.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(job_id=receipt['job_id'],ownership=ready['state']['ownership'],controller={k:v for k,v in result.items() if k!='operations'})),flush=True)
    assert result['status']=='verified',result.get('error')


if __name__=='__main__':main()
