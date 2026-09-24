"""Reconcile a measured A-10 straggler and hand off disjoint units under cap 64.

Never auto-retries an ambiguous mutation. Transport queues on the shared lock
after checking original release priority. The science worker is unchanged.
"""
import datetime
import fcntl
import json
import shutil
import sys
from pathlib import Path

from audit_phase9_results import sha
from euler_transport import _run

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')


def transport(kind,args,**kwargs):
    primary=json.loads((D.parent/'phase9-v1/release-watch-status.json').read_text())
    assert primary['status'] not in ['running_full_audit_and_build','running_independent_delivery_verification'],'Defer to original release'
    with (R/'state/ssh-transport.lock').open('a') as lock:
        # Do not abandon a confirmed pause merely because the collector owns
        # the next transport turn. Still serialize every call and obey backoff.
        fcntl.flock(lock,fcntl.LOCK_EX)
        p=_run(kind,args,text=True,capture_output=True,timeout=240,**kwargs)
        assert p.returncode==0,p.stderr
    return p.stdout


def remote(code):
    wrapped='import json,traceback\ntry:\n exec(compile('+repr(code)+",'phase10-tail-launch','exec'),{})\nexcept Exception:\n print(json.dumps(dict(status='application_error',traceback=traceback.format_exc())))\n"
    x=json.loads(transport('ssh',['euler','python3 -'],input=wrapped))
    assert x.get('status')!='application_error',x
    return x


def save(path,x):path.write_text(json.dumps(x,indent=2)+'\n')


def main(chunk,observation_path=None):
    chunk=int(chunk);assert 0<=chunk<58
    lock=(R/'state/phase10-tail-launch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    observation_path=Path(observation_path) if observation_path else D/'production-observation.json'
    assert observation_path.resolve().parent==D.resolve()
    observation=json.loads(observation_path.read_text())
    age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(observation['utc'])).total_seconds()
    assert 0<=age<900 and not observation['failures']
    assert chunk in {c['chunk'] for c in observation['potential_stragglers']}
    measured=next(c for c in observation['chunks'] if c['chunk']==chunk)
    assert measured['scheduler_state']=='RUNNING' and measured['elapsed_seconds']>=3600 and not measured['complete']
    local=D/f'tail-{chunk:04d}-v1';local.mkdir(exist_ok=True)
    assert not (local/'launch-intent.json').exists(),'Existing attempt must be reconciled, never repeated blindly'
    names=['phase9_tail_common.py','phase10_tail_common.py','phase10_tail_supervisor.py','phase10_tail_helper.py','submit_phase10_tail_remote.py']
    pins={}
    for name in names:shutil.copyfile(R/'scripts'/name,D/name);pins[name]=sha(D/name)
    pins.update(json.loads((D/'worker-pins-v2.json').read_text()))
    pinpath=D/'tail-code-pins.json'
    if pinpath.exists():assert json.loads(pinpath.read_text())==pins,'Existing helper code pins must not change'
    else:save(pinpath,pins)
    stage_pins={name:sha(D/name) for name in names+[pinpath.name]}
    stage=remote("import json,hashlib\nfrom pathlib import Path\nD=Path.home()/'plastchem-euler/phase10-v1'\npins="+repr(stage_pins)+"\nmissing=[]\nfor name,pin in pins.items():\n p=D/name\n if p.exists():assert hashlib.sha256(p.read_bytes()).hexdigest()==pin,('Different existing code; do not overwrite',name)\n else:missing.append(name)\nprint(json.dumps(dict(missing=missing)))\n")
    if stage['missing']:
        transport('scp',[*[str(D/name) for name in stage['missing']],'euler:plastchem-euler/phase10-v1/'])
    intent=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),chunk=chunk,measured=measured,
        straggler=next(c for c in observation['potential_stragglers'] if c['chunk']==chunk),code_pins=pins,
        observation_path=str(observation_path),observation_sha256=sha(observation_path),
        authority='A-10 applies A-9 straggler splitting; shared cap remains 64')
    save(local/'launch-intent.json',intent)
    code=r'''
import fcntl,json,subprocess,datetime,hashlib
from pathlib import Path
D=Path.home()/'plastchem-euler/phase10-v1';chunk=CHUNK
root=D/f'tail-{chunk:04d}-v1';root.mkdir(exist_ok=True)
with (root/'start.lock').open('a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX)
 for name,pin in json.loads((D/'tail-code-pins.json').read_text()).items():assert hashlib.sha256((D/name).read_bytes()).hexdigest()==pin,name
 receipt=root/'supervisor-start.json'
 if receipt.exists():print(receipt.read_text())
 else:
  job=json.loads((D/'production-submission.json').read_text())['job_id']
  q=subprocess.check_output(['squeue','-h','-j',f'{job}_{chunk}','-o','%i|%T|%N'],text=True).strip()
  assert q.split('|')[:2]==[f'{job}_{chunk}','RUNNING'],q
  plan=json.loads((D/'production-plan.json').read_text());folder=D/plan['output']/f'{chunk:04d}'
  starts=list(folder.glob('started-*.json'));assert len(starts)==1
  started=json.loads(starts[0].read_text());assert not (folder/'complete.json').exists()
  args=['srun','--overlap','--jobid='+started['execution']['job_id'],'--nodes=1','--ntasks=1','--cpus-per-task=1','--immediate=30','python3',str(D/'phase10_tail_supervisor.py'),str(chunk)]
  log=(D/'logs'/f'tail-{chunk:04d}-supervisor.log').open('a')
  child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
  x=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),pid=child.pid,command=args,squeue_before=q)
  receipt.write_text(json.dumps(x,indent=2)+'\n');print(json.dumps(x))
'''.replace('CHUNK',str(chunk))
    started=remote(code);save(local/'supervisor-start.json',started)
    code=r'''
import json,time
from pathlib import Path
D=Path.home()/'plastchem-euler/phase10-v1';root=D/'TAIL'
# Hash verification of a large existing prefix can exceed one minute on NFS.
# This only lengthens observation of the same supervisor; it never restarts it.
for _ in range(180):
 p=root/'handoff-state.json'
 if p.exists():
  s=json.loads(p.read_text())
  if s['status']=='paused_helpers_permitted':
   print(json.dumps(dict(state=s,assignment=json.loads((root/'assignment.json').read_text()))));break
  assert s['status']=='pause_intent',s
 time.sleep(1)
else:raise RuntimeError('Pause not yet confirmed; reconcile supervisor; no submission')
'''.replace('TAIL',f'tail-{chunk:04d}-v1')
    ready=remote(code)
    for name,value in [('handoff-state.json',ready['state']),('assignment.json',ready['assignment'])]:save(local/name,value)
    assert sha(local/'assignment.json')==ready['state']['assignment_sha256']
    code="import runpy,sys\nfrom pathlib import Path\np=Path.home()/'plastchem-euler/phase10-v1/submit_phase10_tail_remote.py'\nsys.path.insert(0,str(p.parent));sys.argv=[str(p),"+repr(str(chunk))+"]\nrunpy.run_path(str(p),run_name='__main__')\n"
    receipt=remote(code);save(D/f'production-retry-tail{chunk:04d}-submission.json',receipt)
    print(json.dumps(dict(job_id=receipt['job_id'],ownership=ready['state']['ownership'],
        status='submitted_held_shared_allocator_will_release',cap=64)))


if __name__=='__main__':main(sys.argv[1],sys.argv[2] if len(sys.argv)>2 else None)
