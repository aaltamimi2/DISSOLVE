"""Reversible pause/resume handoff inside the original allocated CPU.

The original Python process is stopped, never cancelled. Its in-flight unit is
excluded from helpers. Resume requires all helper jobs terminal (no live writer)
and every assigned checkpoint verified. A launch timeout before submission can
resume safely under the same handoff lock. Unexpected states fail closed.
"""
import datetime,fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from phase9_tail_common import process,checked_process,sha,save,partition_ownership,keyname

D=Path(__file__).resolve().parent


def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def call(args):
    p=subprocess.run(args,capture_output=True,text=True,timeout=45)
    assert p.returncode==0,(args,p.stderr)
    return p.stdout


def diagnostic():
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(20)'])
    try:
        identity=process(child.pid);os.kill(child.pid,signal.SIGSTOP)
        for _ in range(50):
            state=checked_process(identity)
            if state['state']=='T':break
            time.sleep(.02)
        checked_process(identity,stopped=True)
        os.kill(child.pid,signal.SIGCONT)
        time.sleep(.05);after=checked_process(identity);assert after['state']!='T'
        return dict(utc=utc(),status='passed',node=os.uname().nodename,allocation=os.environ['SLURM_JOB_ID'],stopped=state,resumed=after,
                    scope='Owned diagnostic child only; production not signalled')
    finally:
        if child.poll() is None:
            os.kill(child.pid,signal.SIGCONT);child.terminate();child.wait()


def main(chunk):
    chunk=int(chunk);assert chunk==2
    root=D/'tail-0002-v1';root.mkdir(exist_ok=True)
    controller=(root/'supervisor.lock').open('a');fcntl.flock(controller,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not (root/'assignment.json').exists(),'Do not repeat an existing handoff'
    gate=json.loads((D/'recovery-gate-fresh-passed.json').read_text())
    assert gate['status']=='passed' and gate['systems']==2240 and gate['mismatch_count']==0
    pins=json.loads((D/'tail-code-pins.json').read_text())
    for name,digest in pins.items():assert sha(D/name)==digest,name
    assert gate['signature']['failure_policy_sha256']==sha(D/'phase9_failure_policy.py')
    plan=json.loads((D/'production-plan.json').read_text());units=plan['chunks'][chunk]
    manifest=json.loads((D.parent/'phase8-v1/manifest.json').read_text())
    out=D/plan['output']/f'{chunk:04d}';started=json.loads((out/'started.json').read_text())
    assert os.environ['SLURM_JOB_ID']==started['execution']['job_id']
    assert os.uname().nodename==started['execution']['node']
    candidates=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit():continue
        try:
            if p.stat().st_uid!=os.getuid():continue
            record=process(int(p.name))
        except (FileNotFoundError,PermissionError,ProcessLookupError):continue
        if record['cwd']==str(D) and record['command'].endswith(f'python -u phase9_entry.py production-plan.json {chunk}'):
            candidates.append(record)
    assert len(candidates)==1,candidates
    original=candidates[0];state=dict(utc=utc(),status='pause_intent',original_process=original,supervisor=process(os.getpid()),node=os.uname().nodename)
    save(root/'handoff-state.json',state)
    os.kill(original['pid'],signal.SIGSTOP)
    safe_to_resume=True
    try:
        for _ in range(1200):
            if checked_process(original)['state']=='T':break
            time.sleep(.05)
        checked_process(original,stopped=True)
        state.update(pause_utc=utc(),pause_epoch=time.time())
        save(root/'handoff-state.json',state)
        seals={}
        for path in (out/'lle').glob('*.json.sha256.json'):
            key=tuple(path.name[:-len('.json.sha256.json')].split('__'))
            data=json.loads(path.read_text());payload=path.with_name(path.name[:-len('.sha256.json')])
            assert sha(payload)==data['sha256'];seals[key]=data
        ownership=partition_ownership(units,manifest['solvents'],seals,3)
        assignment=dict(ownership,utc=utc(),chunk=chunk,original_process=original,signature=started['signature'],
                        original_job=started['execution'],code_pins=pins,
                        existing_seals={keyname(k):v for k,v in seals.items()},
                        gate_sha256=sha(D/'recovery-gate-fresh-passed.json'))
        ap=root/'assignment.json';save(ap,assignment)
        state.update(status='paused_helpers_permitted',assignment_sha256=sha(ap),heartbeat_epoch=time.time(),ownership=ownership)
        save(root/'handoff-state.json',state)
        began=time.monotonic()
        while True:
            checked_process(original,stopped=True)
            state.update(heartbeat_epoch=time.time(),utc=utc());save(root/'handoff-state.json',state)
            receipt_path=D/'production-retry-tail0002-submission.json'
            pending=D/'production-retry-tail0002-unconfirmed.json'
            # Serialize launch intent vs the timeout decision; an unconfirmed
            # submission forbids automatic resume until reconciled explicitly.
            launchlock=(root/'launch.lock').open('a')
            with launchlock:
                fcntl.flock(launchlock,fcntl.LOCK_EX)
                exists=receipt_path.exists() or pending.exists()
                if exists:safe_to_resume=False
                if not exists and time.monotonic()-began>600:
                    state.update(status='aborted_before_submission',utc=utc());save(root/'handoff-state.json',state)
                    break
            if receipt_path.exists():
                receipt=json.loads(receipt_path.read_text());job=receipt['job_id']
                assert receipt['assignment_sha256']==sha(ap)
                q=call(['squeue','-h','-r','-j',job,'-o','%i|%T'])
                if not q.strip():
                    a=call(['sacct','-nP','-j',job,'--format=JobID%40,State%30,ExitCode'])
                    states={r.split('|')[0]:r.split('|')[1] for r in a.splitlines() if r}
                    tasks=[job+'_'+str(i) for i in range(3)]
                    if all(states.get(task) in ['COMPLETED','FAILED','TIMEOUT','OUT_OF_MEMORY','NODE_FAIL','CANCELLED'] for task in tasks):
                        state['helper_accounting']=a
                        if all(states[task]=='COMPLETED' for task in tasks):
                            for group in range(3):
                                done=json.loads((root/f'helper-{group}-complete.json').read_text())
                                assert done['assignment_sha256']==sha(ap) and done['units']==assignment['groups'][group]
                                assert len(done['files'])==64*len(done['units'])
                                for name,digest in done['files'].items():
                                    payload=out/'lle'/name;seal=json.loads(payload.with_suffix('.json.sha256.json').read_text())
                                    assert sha(payload)==digest==seal['sha256']
                                    for k,v in assignment['signature'].items():assert seal['signature'][k]==v
                            for name,old in assignment['existing_seals'].items():
                                assert json.loads((out/'lle'/(name+'.sha256.json')).read_text())==old
                                assert sha(out/'lle'/name)==old['sha256']
                            state['outcome']='all_helper_units_verified'
                        else:
                            state['outcome']='helper_failure_terminal_preserved_checkpoints_resume_original'
                        safe_to_resume=True
                        state.update(status='helpers_terminal_resume_ready',utc=utc());save(root/'handoff-state.json',state)
                        break
            time.sleep(20)
    except BaseException as exc:
        state.update(status='supervisor_error',error=repr(exc),utc=utc());save(root/'handoff-state.json',state)
        raise
    finally:
        with (root/'launch.lock').open('a') as final_lock:
            fcntl.flock(final_lock,fcntl.LOCK_EX)
            if state.get('status') not in ['helpers_terminal_resume_ready','aborted_before_submission']:
                if (D/'production-retry-tail0002-submission.json').exists() or (D/'production-retry-tail0002-unconfirmed.json').exists():
                    safe_to_resume=False
        if safe_to_resume:
            checked_process(original);os.kill(original['pid'],signal.SIGCONT)
            time.sleep(.1);state.update(status='original_resumed',resume_utc=utc(),resumed_process=checked_process(original),paused_seconds=time.time()-state.get('pause_epoch',time.time()),original_elapsed_time_includes_pause=True)
            save(root/'handoff-state.json',state)
        else:
            state.update(status='pause_requires_reconciliation',utc=utc());save(root/'handoff-state.json',state)


if __name__=='__main__':
    if sys.argv[1]=='diagnostic':print(json.dumps(diagnostic()))
    else:main(sys.argv[1])
