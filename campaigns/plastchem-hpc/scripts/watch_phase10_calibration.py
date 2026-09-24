"""Low-priority status/timing collection; defer to the original collector lock.

No job submissions, cap change or scientific calculation. Stop for failures or
once measurements are ready for review. Leave all original processes running.
"""
import datetime
import fcntl
import json
import os
import time
from pathlib import Path
from euler_transport import _run

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase10-v1')


def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def save(p, value):
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(p)


def once():
    with (R / 'state/ssh-transport.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(utc=utc(), status='deferred_primary_transport_busy')
        source = (R / 'scripts/observe_phase10_calibration_remote.py').read_text()
        # Encode application failure in JSON so a scientific/readback assertion
        # is not mistaken for a network failure by the shared SSH backoff.
        code = 'import json,traceback\ntry:\n exec(compile(' + repr(source) + ", 'phase10-observer', 'exec'), {'__name__':'__main__'})\nexcept Exception:\n print(json.dumps(dict(status='observer_error',traceback=traceback.format_exc())))\n"
        p = _run('ssh', ['euler', 'python3 -'], input=code, capture_output=True,
                 text=True, timeout=180)
        assert p.returncode == 0, p.stderr
    result = json.loads(p.stdout)
    save(D / 'calibration-v2-observation.json', result)
    return dict(utc=result.get('utc', utc()), status=result['status'],
                completed_chunks=result.get('completed_chunks'), failures=result.get('failures', []),
                LLE_seals=sum(r['LLE_seals'] for r in result.get('chunks', [])),
                control_comparisons=sum(len(r['control_comparisons']) for r in result.get('chunks', [])),
                projection=result.get('projection'))


def main():
    lock = (R / 'state/phase10-calibration-watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    save(D / 'calibration-watch-process.json', dict(pid=os.getpid(), utc=utc()))
    while True:
        try:
            result = once()
        except Exception as exc:
            result = dict(utc=utc(), status='transport_or_observer_exception', error=repr(exc))
        print(json.dumps(result), flush=True)
        save(D / 'calibration-watch-status.json', result)
        if result['status'] in ['inspection_required', 'observer_error', 'measurement_complete_review_required']:
            return
        delay = 60 if result['status'] == 'deferred_primary_transport_busy' else 300
        until = time.monotonic()+delay
        while time.monotonic() < until:
            time.sleep(min(45, until-time.monotonic()))


if __name__ == '__main__': main()
