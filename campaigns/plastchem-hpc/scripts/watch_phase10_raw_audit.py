"""Run the supplemental raw-cache audit after both releases are verified.

One child only, no cluster work, no retry after an unexplained audit failure.
"""
import datetime
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from audit_phase9_results import sha

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')
PY='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'


def save(p,x):
    tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(p)


def status(name,**kwargs):
    x=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=name,**kwargs)
    save(D/'raw-audit-watch-status.json',x);print(json.dumps(x),flush=True)


def main():
    lock=(R/'state/phase10-raw-audit-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    pins={n:sha(R/'scripts'/n) for n in ['watch_phase10_raw_audit.py','audit_phase10_raw_lle.py','audit_phase9_raw_lle.py']}
    save(D/'raw-audit-watch-process.json',dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),script_pins=pins))
    while True:
        for name,pin in pins.items():assert sha(R/'scripts'/name)==pin
        reasons=[]
        for folder,target in [('phase9-v1','promotion-v1'),('phase10-v1','promotion-ext39-v1')]:
            p=D.parent/folder/'delivery-verification.json'
            if not p.exists():reasons.append(folder+'_delivery_pending');continue
            v=json.loads(p.read_text());assert v['status']=='complete_delivery_verified'
            assert v['manifest_sha256']==sha(D.parent/target/'manifest.json')
        p=D.parent/'phase9-v1/raw-lle-audit-v2/summary.json'
        if not p.exists() or json.loads(p.read_text())['status']!='complete':reasons.append('primary_raw_audit_pending')
        available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
        if available<2.5*1024**3:reasons.append('memory_guard')
        if reasons:status('waiting',reasons=reasons);time.sleep(45);continue
        break
    with (D/'raw-lle-audit-v1.log').open('a') as log:
        child=subprocess.Popen([PY,'-u',str(R/'scripts/audit_phase10_raw_lle.py')],cwd=R,
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    status('auditing_all_collected_archives',child_pid=child.pid)
    while child.poll() is None:time.sleep(45)
    assert child.returncode==0,'Raw cache audit failed; inspect retained log; no automatic restart'
    for name,pin in pins.items():assert sha(R/'scripts'/name)==pin
    summary=json.loads((D/'raw-lle-audit-v1/summary.json').read_text())
    release=json.loads((D.parent/'promotion-ext39-v1/summary.json').read_text())
    raw_counts={'lle_'+k:v for k,v in summary['statuses'].items()}
    assert summary['status']=='complete' and summary['systems']==227370 and summary['fully_evaluated_contaminants']==5830
    assert all(release['counts'][k]==v for k,v in raw_counts.items())
    status('complete_raw_audit_agent_review_required',systems=227370,
        summary_sha256=sha(D/'raw-lle-audit-v1/summary.json'),
        release_manifest_sha256=sha(D.parent/'promotion-ext39-v1/manifest.json'))


if __name__=='__main__':
    try:main()
    except Exception as exc:status('inspection_required',error=repr(exc));raise
