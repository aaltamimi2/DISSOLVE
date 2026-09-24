"""Continue the existing raw LLE audit over later collected archives, serially.

Never restart a live auditor or restart after an unexplained error. Original
release build/verification has priority over starting another supplemental pass.
"""
import datetime
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase9-v1')
OUT=D/'raw-lle-audit-v2'
SCRIPT=R/'scripts/audit_phase9_raw_lle.py'
PYTHON='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def identity(pid):
    root=Path('/proc')/str(pid)
    try:
        fields=(root/'stat').read_text().rsplit(')',1)[1].split()
        return dict(pid=pid,start_ticks=fields[19],state=fields[0],
                    command=(root/'cmdline').read_bytes().replace(b'\0',b' ').decode().strip())
    except FileNotFoundError:return None


def alive(expected):
    actual=identity(expected['pid'])
    return bool(actual and actual['start_ticks']==expected['start_ticks']
                and actual['command']==expected['command'] and actual['state']!='Z')


def status(name,**details):
    value=dict(utc=utc(),status=name,**details)
    path=D/'raw-lle-audit-watch-status.json';temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)
    print(json.dumps(value),flush=True)


def main(pid):
    lock=(R/'state/phase9-v1/raw-audit-watch.lock').open('a')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    expected=identity(pid)
    assert expected and 'scripts/audit_phase9_raw_lle.py' in expected['command']
    code_sha=sha(SCRIPT)
    assert code_sha==(OUT/'script.sha256').read_text().strip()
    (D/'raw-lle-audit-watch-process.json').write_text(json.dumps(dict(pid=os.getpid(),utc=utc(),attached=expected,script_sha256=code_sha),indent=2)+'\n')
    summary_path=OUT/'summary.json'
    prior_summary_sha=sha(summary_path) if summary_path.exists() else None
    status('observing_existing_auditor',process=expected)
    while alive(expected):
        assert sha(SCRIPT)==code_sha
        time.sleep(45)
    assert summary_path.exists() and sha(summary_path)!=prior_summary_sha, 'Auditor exited without a new successful summary; inspect log'
    while True:
        summary=json.loads(summary_path.read_text())
        assert summary['auditor_sha256']==code_sha
        if summary['status']=='complete':
            assert summary['systems']==373120 and summary['fully_evaluated_contaminants']==5830
            status('complete_raw_LLE_audit',systems=373120,summary_sha256=sha(summary_path))
            return
        assert summary['status']=='passed_for_collected_subset'
        status('successful_subset_waiting_for_new_archives',systems=summary['systems'])
        while True:
            assert sha(SCRIPT)==code_sha
            release=json.loads((D/'release-watch-status.json').read_text())
            if release['status'] in ['running_full_audit_and_build','running_independent_delivery_verification']:
                time.sleep(45);continue
            available=int(next(l.split()[1] for l in Path('/proc/meminfo').read_text().splitlines() if l.startswith('MemAvailable:')))*1024
            if available<2.5*1024**3:
                time.sleep(45);continue
            registry=json.loads((D/'collection.json').read_text())
            new=[r for r in registry['archives'] if Path(r['path']).name+'.json' not in summary['archive_audit_receipts']]
            del registry
            if new:break
            time.sleep(45)
        with (D/'raw-lle-audit-v2.log').open('a') as log:
            child=subprocess.Popen([PYTHON,'-u',str(SCRIPT)],cwd=R,stdin=subprocess.DEVNULL,
                                   stdout=log,stderr=subprocess.STDOUT)
        status('auditing_new_archives',child_pid=child.pid,new_archives=len(new),previous_systems=summary['systems'])
        while child.poll() is None:
            assert sha(SCRIPT)==code_sha
            time.sleep(45)
        assert child.returncode==0,'Raw audit failed; inspect original log; no automatic retry'


if __name__=='__main__':
    try:main(int(sys.argv[1]))
    except Exception as exc:
        status('inspection_required',error=repr(exc));raise
