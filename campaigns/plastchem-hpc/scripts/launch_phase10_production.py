"""Stage and submit the authorized extension after its measured cost gate.

No waiting on the primary transport lock, no cap change, and no blind retries.
The remote launcher submits held; the shared allocator owns its release.
"""
import datetime
import fcntl
import hashlib
import io
import json
import tarfile
from pathlib import Path

from euler_transport import _run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase10-v1')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    gate = json.loads((D/'calibration-clearance.json').read_text())
    assert gate['status'] == 'passed' and gate['projected_CPU_h'] <= 510
    assert gate['charter_sha256'] == sha(R/'CHARTER.txt'), 'Review changed authorization'
    for name, digest in gate['file_pins'].items():
        assert sha(D/name) == digest, name
    release = json.loads((D.parent/'phase9-v1/release-watch-status.json').read_text())
    if release['status'] in ['running_full_audit_and_build', 'running_independent_delivery_verification']:
        print(json.dumps(dict(status='deferred_primary_release_busy')))
        return
    files = {name: D/name for name in ['production-plan.json', 'calibration-clearance.json']}
    files['submit_phase10_production_remote.py'] = R/'scripts/submit_phase10_production_remote.py'
    pins = {name: sha(path) for name, path in files.items()}
    token = hashlib.sha256(json.dumps(pins, sort_keys=True).encode()).hexdigest()
    archive = D/('production-launch-'+token[:16]+'.tar.gz')
    if not archive.exists():
        with tarfile.open(archive, 'w:gz') as stream:
            raw = json.dumps(pins, sort_keys=True).encode()
            entry = tarfile.TarInfo('pins.json'); entry.size = len(raw)
            stream.addfile(entry, io.BytesIO(raw))
            for name, path in files.items(): stream.add(path, arcname=name, recursive=False)
    with (R/'state/ssh-transport.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps(dict(status='deferred_primary_transport_busy')))
            return
        result = _run('scp', [str(archive), 'euler:plastchem-euler/phase10-v1/'+archive.name],
                      capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr
        # The stage is immutable: every existing destination must already match.
        # Application failures are JSON, distinct from transport failures.
        source = '''import fcntl,hashlib,json,tarfile,traceback
from pathlib import Path
try:
 D=Path.home()/'plastchem-euler/phase10-v1'
 lock=(D/'production-stage.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX)
 archive=D/ARCHIVE_NAME
 assert hashlib.sha256(archive.read_bytes()).hexdigest()==ARCHIVE_SHA
 with tarfile.open(archive,'r:gz') as t:
  names=[m.name for m in t.getmembers()]
  assert len(names)==len(set(names)) and set(names)==set(PINS)|{'pins.json'}
  assert json.load(t.extractfile('pins.json'))==PINS
  for name,digest in PINS.items():
   m=t.getmember(name);assert m.isfile() and '/' not in name
   raw=t.extractfile(m).read();assert hashlib.sha256(raw).hexdigest()==digest
   p=D/name
   if p.exists():assert hashlib.sha256(p.read_bytes()).hexdigest()==digest,name
   else:
    tmp=p.with_suffix(p.suffix+'.stage-tmp');tmp.write_bytes(raw);tmp.replace(p)
 path=D/'submit_phase10_production_remote.py'
 exec(compile(path.read_text(),str(path),'exec'),{'__name__':'__main__','__file__':str(path)})
except Exception:
 print(json.dumps(dict(status='launch_error_requires_reconciliation',traceback=traceback.format_exc())))
'''
        source = 'ARCHIVE_NAME='+repr(archive.name)+'\nARCHIVE_SHA='+repr(sha(archive))+'\nPINS='+repr(pins)+'\n'+source
        result = _run('ssh', ['euler', 'python3 -'], input=source,
                      capture_output=True, text=True, timeout=180)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    evidence = dict(utc=stamp, pins=pins, archive=str(archive), archive_sha256=sha(archive),
                    returncode=result.returncode, stdout=result.stdout, stderr=result.stderr)
    (D/('production-launch-'+stamp+'.json')).write_text(json.dumps(evidence, indent=2)+'\n')
    assert result.returncode == 0, 'Transport failed: reconcile before any retry'
    receipt = json.loads(result.stdout)
    assert receipt.get('job_id') and receipt.get('decision') in [
        'submitted_held_for_shared_allocator', 'existing_no_resubmission'], receipt
    (D/'production-submission.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(dict(status='submitted_held_allocator_release_pending',
                         job_id=receipt['job_id'], decision=receipt['decision'],
                         submitted_utc=receipt.get('submitted_utc'), projected_CPU_h=gate['projected_CPU_h'])))


if __name__ == '__main__': main()
