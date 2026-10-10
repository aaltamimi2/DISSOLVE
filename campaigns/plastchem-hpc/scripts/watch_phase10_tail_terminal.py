"""Retain terminal handoff proof serially; never resubmit or signal any job."""
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(name,value):
    path=D/name;tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)
def status(name,**values):
    value=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=name,**values)
    save('tail-terminal-watch-status.json',value);print(json.dumps(value),flush=True)

def main():
    lock=(R/'state/phase10-tail-terminal-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    pins={name:sha(R/'scripts'/name) for name in ['watch_phase10_tail_terminal.py','collect_phase10_tail_terminal.py']}
    save('tail-terminal-watch-process.json',dict(pid=os.getpid(),pins=pins,utc=datetime.datetime.now(datetime.timezone.utc).isoformat()))
    while True:
        for name,pin in pins.items():assert sha(R/'scripts'/name)==pin
        primary=json.loads((D.parent/'phase9-v1/delivery-verification.json').read_text())
        assert primary['status']=='complete_delivery_verified'
        assert primary['manifest_sha256']==sha(D.parent/'promotion-v1/manifest.json')
        with (D/'tail-terminal-capture.log').open('a') as log:
            child=subprocess.Popen(['/usr/bin/python3','-u',str(R/'scripts/collect_phase10_tail_terminal.py')],
                cwd=R,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        status('capturing_terminal_controls',child_pid=child.pid)
        while child.poll() is None:time.sleep(5)
        assert child.returncode==0,'Inspect retained capture log; no automatic retry after an unexplained error'
        rows=json.loads((D/'tail-terminal-evidence-latest.json').read_text())['results']
        assert rows
        waiting=[r['chunk'] for r in rows if r['status']=='waiting']
        assert all(r['status'] in ['waiting','already_verified','terminal_handoff_verified'] for r in rows)
        if not waiting:
            status('all_known_terminal_handoffs_verified',chunks=[r['chunk'] for r in rows]);return
        status('waiting_for_original_resumes',waiting=waiting,verified=len(rows)-len(waiting))
        until=time.monotonic()+300
        while time.monotonic()<until:time.sleep(min(45,until-time.monotonic()))

if __name__=='__main__':
    try:main()
    except Exception as exc:status('inspection_required',error=repr(exc));raise
