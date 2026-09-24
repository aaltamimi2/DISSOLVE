"""Reconcile and queue three disjoint helpers, held for the shared controller."""
import datetime,fcntl,json,re,subprocess,sys
from pathlib import Path
from phase10_tail_common import sha,save
D=Path(__file__).resolve().parent;chunk=int(sys.argv[1]);assert 0<=chunk<58
root=D/f'tail-{chunk:04d}-v1'
name=f'contam-phase10-retry-tail{chunk:04d}'
receipt_path=D/f'production-retry-tail{chunk:04d}-submission.json';pending=D/f'production-retry-tail{chunk:04d}-unconfirmed.json'
lock=(D.parent/'phase83-v1/throttle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
launchlock=(root/'launch.lock').open('a');fcntl.flock(launchlock,fcntl.LOCK_EX)
receipt=dict(name=name,utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),purpose='extension_recovery',indices=[0,1,2],operations=[])

def call(args):
    p=subprocess.run(args,capture_output=True,text=True)
    receipt['operations'].append(dict(command=args,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr))
    assert p.returncode==0,(args,p.stderr)
    return p.stdout

activation=json.loads((D.parent/'phase9-v1/active-throttle-controller.json').read_text())
assert activation['filename'] in ['phase9_throttle_remote_v5.py','phase9_throttle_remote_v6.py'] and sha(D.parent/'phase9-v1'/activation['filename'])==activation['sha256']
for name_,digest in json.loads((D/'tail-code-pins.json').read_text()).items():assert sha(D/name_)==digest,name_
gate=json.loads((D/'calibration-clearance.json').read_text());assert gate['status']=='passed' and gate['exact_batch_control_comparisons']==80 and gate['max_control_difference']<=1e-9
assignment=json.loads((root/'assignment.json').read_text());receipt['assignment_sha256']=sha(root/'assignment.json')
q=call(['squeue','-h','-r','-u','aaltamimi2','--name='+name,'-o','%i|%j|%T'])
a=call(['sacct','-nP','-u','aaltamimi2','--starttime=2026-09-24','--name='+name,'--format=JobID%40,JobName%60,State,Elapsed,NodeList'])
found={s.split('|')[0].split('_')[0].split('.')[0] for s in (q+'\n'+a).splitlines() if re.match(r'^\d',s)}
receipt['reconciliation']=dict(squeue=q,sacct=a)
if found:
    assert len(found)==1
    previous=json.loads((receipt_path if receipt_path.exists() else pending).read_text())
    assert previous['assignment_sha256']==receipt['assignment_sha256'] and previous['name']==name
    job=next(iter(found));assert 'job_id' not in previous or previous['job_id']==job
    receipt.update(previous,job_id=job,decision='existing_no_resubmission',reconciliation=receipt['reconciliation'])
else:
    assert not receipt_path.exists() and not pending.exists(),'Unconfirmed attempt requires reconciliation'
    state=json.loads((root/'handoff-state.json').read_text())
    import time
    assert state['status']=='paused_helpers_permitted' and time.time()-state['heartbeat_epoch']<90
    assert state['assignment_sha256']==receipt['assignment_sha256']
    assert all(assignment['groups']) and len(assignment['groups'])==3
    cmd=['sbatch','--parsable','--hold','--job-name='+name,'--partition=research','--constraint=(milan|genoa)&cpu',
         '--exclude=euler09,euler10','--nodes=1','--ntasks=1','--cpus-per-task=1','--mem=4G','--time=08:00:00','--no-requeue',
         '--array=0-2%3','--chdir='+str(D),'--output='+str(D/'logs/tail-%A_%a.out'),'--error='+str(D/'logs/tail-%A_%a.err'),
         '--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; '
         f'../phase8-v1/venv/bin/python -u phase10_tail_helper.py tail-{chunk:04d}-v1/assignment.json "$SLURM_ARRAY_TASK_ID"']
    receipt['command']=cmd;save(pending,receipt)
    job=call(cmd).strip().split(';')[0];assert job.isdigit()
    receipt.update(job_id=job,decision='submitted_held_for_shared_cap_controller')
receipt['scheduler_readback']=call(['scontrol','show','job',job,'-o']);save(receipt_path,receipt)
print(json.dumps(receipt))
