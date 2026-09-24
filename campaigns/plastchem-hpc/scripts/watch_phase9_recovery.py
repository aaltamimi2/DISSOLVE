"""Low-memory recovery reconciliation; collectors and scientific jobs stay intact."""
import datetime,fcntl,hashlib,json,os,time
from pathlib import Path
from euler_transport import run
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase9-v1')

def main():
    lock=(R/'state/phase9-v1/recovery-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    (R/'state/phase9-v1/recovery-watch-process.json').write_text(json.dumps(dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))+'\n')
    while True:
        began=time.monotonic()
        try:
            verified=D/'delivery-verification.json'
            if verified.exists():
                verification=json.loads(verified.read_text())
                assert verification['status']=='complete_delivery_verified'
                manifest=D.parent/'promotion-v1/manifest.json'
                assert hashlib.sha256(manifest.read_bytes()).hexdigest()==verification['manifest_sha256']
                assert verification['counts']==dict(partition_rows=3731200,lle_rows=373120)
                line=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='retired_after_verified_primary_release',release_id=verification['manifest_sha256'])
                (D/'recovery-watch-terminal.json').write_text(json.dumps(line,indent=2)+'\n')
                print(json.dumps(line),flush=True);return
            gate=json.loads((D/'recovery-gate-fresh-passed.json').read_text())
            assert gate['status']=='passed' and gate['systems']==2240
            p=run('ssh',['euler','python3 ~/plastchem-euler/phase9-v1/reconcile_phase9_failures_remote.py'],capture_output=True,text=True,timeout=180)
            assert p.returncode==0,p.stderr
            result=json.loads(p.stdout);(D/'recovery-watch-latest.json').write_text(json.dumps(result,indent=2)+'\n')
            for receipt in result['submitted']:
                (D/(receipt['name'].replace('contam-phase9-retry-','production-retry-')+'-submission.json')).write_text(json.dumps(receipt,indent=2)+'\n')
            code="import json; from pathlib import Path; r=Path.home()/'plastchem-euler/phase9-v1/tail-0002-v1'; print(json.dumps({p.name:json.loads(p.read_text()) for p in [r/'handoff-state.json',*r.glob('helper-*-progress.json'),*r.glob('helper-*-complete.json')]}))"
            tail=run('ssh',['euler','python3 -'],input=code,capture_output=True,text=True,timeout=180)
            assert tail.returncode==0,tail.stderr
            tail_files=json.loads(tail.stdout);handoff=tail_files['handoff-state.json']
            for name,value in tail_files.items():(D/'tail-0002-v1'/name).write_text(json.dumps(value,indent=2)+'\n')
            if handoff['status'] in ['pause_requires_reconciliation','supervisor_error']:
                result['unhandled'].append(dict(tail_handoff=handoff['status'],reason='Original process remains paused; reconcile all helper states before any resume'))
            if handoff['status']=='paused_helpers_permitted' and time.time()-handoff['heartbeat_epoch']>120:
                result['unhandled'].append(dict(tail_handoff='stale_heartbeat',reason='Supervisor heartbeat missing; do not resume blindly'))
            line=dict(utc=result['utc'],submitted=[r['job_id'] for r in result['submitted']],unhandled=result['unhandled'],tail_status=handoff['status'],running=result['controller']['running_after'])
            print(json.dumps(line),flush=True)
            with (D/'recovery-watch-history.jsonl').open('a') as f:f.write(json.dumps(line)+'\n')
        except Exception as exc:print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),error=repr(exc))),flush=True)
        while time.monotonic()-began<300:time.sleep(min(50,300-(time.monotonic()-began)))

if __name__=='__main__':main()
