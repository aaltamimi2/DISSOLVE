"""Observe A-10 without taking transport priority from the original collector."""
import datetime
import fcntl
import json
import os
import time
from pathlib import Path

from euler_transport import _run

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')


def save(p,row):
    temp=p.with_suffix('.tmp');temp.write_text(json.dumps(row,indent=2)+'\n');temp.replace(p)


def once():
    with (R/'state/ssh-transport.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return dict(status='deferred_primary_transport_busy')
        code=(R/'scripts/observe_phase10_production_remote.py').read_text()
        wrapped='import json,traceback\ntry:\n exec(compile('+repr(code)+",'phase10-status','exec'),{'__name__':'__main__'})\nexcept Exception:\n print(json.dumps(dict(status='observer_error',traceback=traceback.format_exc())))\n"
        p=_run('ssh',['euler','python3 -'],input=wrapped,capture_output=True,text=True,timeout=180)
        assert p.returncode==0,p.stderr
    out=json.loads(p.stdout);save(D/'production-observation.json',out)
    keys=['utc','status','job_id','completed_chunks','partition_molecules_remote','LLE_systems_remote',
          'allocated_CPU_h','research_running_CPUs','running_by_array','failures','potential_stragglers']
    return {k:out[k] for k in keys if k in out}


def main():
    lock=(R/'state/phase10-production-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    save(D/'production-watch-process.json',dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    while True:
        try:out=once()
        except Exception as exc:out=dict(status='transport_or_observer_exception',error=repr(exc))
        out.setdefault('utc',datetime.datetime.now(datetime.timezone.utc).isoformat())
        save(D/'production-watch-status.json',out);print(json.dumps(out),flush=True)
        if out['status'] in ['inspection_required','observer_error','computation_complete_collection_pending']:return
        until=time.monotonic()+(60 if out['status']=='deferred_primary_transport_busy' else 300)
        while time.monotonic()<until:time.sleep(min(45,until-time.monotonic()))


if __name__=='__main__':main()
