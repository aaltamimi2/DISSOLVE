"""Stage the tested recovery wrapper and submit reconciled task 31 held.

All network mutations use the ordinary serialized/backed-off transport. Waiting
for the active collector does not interrupt it or a scientific allocation.
"""
import datetime
import fcntl
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from euler_transport import run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-v1')


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def transport(kind, args, **kw):
    p = run(kind, args, capture_output=True, text=True, timeout=180, **kw)
    assert p.returncode == 0, p.stderr
    return p.stdout


def main():
    lock = (R/'state/phase9-v1/recovery-launch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    names = ['phase9_failure_policy.py', 'phase9_retry_entry.py',
             'phase9_throttle_remote_v3.py', 'submit_phase9_recovery_remote.py']
    pins = {}
    for name in names:
        shutil.copyfile(R/'scripts'/name, D/name); pins[name] = sha(D/name)
    (D/'recovery-code-pins.json').write_text(json.dumps(pins, indent=2)+'\n')
    activation = dict(filename='phase9_throttle_remote_v3.py', sha256=pins['phase9_throttle_remote_v3.py'],
                      authority='A-9 checkpoint recovery and failures-as-data; unchanged shared cap 64',
                      prepared_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    (D/'recovery-launch-intent.json').write_text(json.dumps(dict(activation=activation, indices=[31],
          status='staging_then_reconcile_then_submit_held'), indent=2)+'\n')
    print('Waiting for serialized transport; current collector left running', flush=True)
    transport('scp', [*[str(D/n) for n in names+['recovery-code-pins.json']], 'euler:plastchem-euler/phase9-v1/'])
    code = 'activation='+repr(activation)+'\n'+r'''
import datetime,fcntl,hashlib,json
from pathlib import Path
D=Path.home()/'plastchem-euler/phase9-v1'
lock=(D.parent/'phase83-v1/throttle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
for name,digest in json.loads((D/'recovery-code-pins.json').read_text()).items():
 assert hashlib.sha256((D/name).read_bytes()).hexdigest()==digest,name
active=D/'active-throttle-controller.json'
old=json.loads(active.read_text())
if old['filename']!=activation['filename']:
 activation['utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 backup=D/'active-throttle-controller-v2.json'
 if not backup.exists():backup.write_bytes(active.read_bytes())
 temp=active.with_suffix('.tmp');temp.write_text(json.dumps(activation,indent=2)+'\n');temp.replace(active)
else:assert old['sha256']==activation['sha256']
print(active.read_text())
'''
    actual = json.loads(transport('ssh', ['euler','python3 -'], input=code))
    prior = D/'active-throttle-controller.json'
    if prior.exists() and json.loads(prior.read_text())['filename']!='phase9_throttle_remote_v3.py':
        shutil.copyfile(prior,D/'active-throttle-controller-v2.json')
    prior.write_text(json.dumps(actual,indent=2)+'\n')
    receipt = json.loads(transport('ssh', ['euler','python3 ~/plastchem-euler/phase9-v1/submit_phase9_recovery_remote.py 01 31']))
    (D/'production-retry-01-submission.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(job_id=receipt['job_id'], decision=receipt['decision'], indices=receipt['indices'])),flush=True)
    result=json.loads(transport('ssh',['euler','python3 ~/plastchem-euler/phase9-v1/phase9_throttle_remote_v3.py']))
    (D/'recovery-first-controller-readback.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='operations'}),flush=True)
    assert result['status']=='verified', result.get('error')


if __name__=='__main__': main()
