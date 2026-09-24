"""Serial, resumable extension collection/export, below the original release.

No cluster submissions or scientific compute. Stops on any unexplained failure.
"""
import datetime
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

from audit_phase9_results import sha
from collect_phase10 import require_primary, PrimaryBusy

R=Path(__file__).resolve().parents[1]
D=Path('/mnt/r/plastchem-euler/phase10-v1')
PY='/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
SCRIPTS=['collect_phase10.py','collect_phase9.py','build_phase10_primary_reference.py','audit_phase10_results.py',
    'phase10_lle_audit.py','build_phase10_release.py','verify_phase10_delivery.py','watch_phase10_delivery.py',
    'audit_phase9_results.py','build_phase9_release.py','build_phase84_release.py','verify_phase9_delivery.py']


def status(name,**kwargs):
    x=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status=name,**kwargs)
    p=D/'delivery-watch-status.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(x,indent=2)+'\n');tmp.replace(p)
    print(json.dumps(x),flush=True)


def pause(seconds=45):time.sleep(seconds)


def main():
    lock=(R/'state/phase10-delivery-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    pins={name:sha(R/'scripts'/name) for name in SCRIPTS};charter=sha(R/'CHARTER.txt')
    def check_pins():
        assert sha(R/'CHARTER.txt')==charter,'Charter changed; inspect before continuing'
        for name,digest in pins.items():assert sha(R/'scripts'/name)==digest,('Pinned code changed',name)
    (D/'delivery-watch-process.json').write_text(json.dumps(dict(pid=os.getpid(),utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        charter_sha256=charter,script_pins=pins),indent=2)+'\n')
    def run(name):
        check_pins();require_primary()
        status('running_'+name,command=[PY,'-u',str(R/'scripts'/name)])
        with (D/'delivery-watch-children.log').open('a') as log:
            child=subprocess.Popen([PY,'-u',str(R/'scripts'/name)],cwd=R,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
        while child.poll() is None:pause(5)
        assert child.returncode==0,(name,child.returncode,'Inspect delivery-watch-children.log')
        check_pins()
    while True:
        check_pins()
        try:require_primary()
        except PrimaryBusy as exc:status('waiting',reason=str(exc));pause();continue
        if not (D/'primary-polymer-reference-receipt.json').exists():
            assert not (D/'primary-polymer-reference.json.gz').exists(),'Unconfirmed reference extraction; reconcile receipt'
            run('build_phase10_primary_reference.py')
        registry_path=D/'collection.json';counts=dict(partition_molecules=0,LLE_systems=0,complete_chunks=0)
        if registry_path.exists():
            registry=json.loads(registry_path.read_text())
            for p in registry['files']:
                if p.endswith('.sha256.json'):continue
                if '/partition/' in p:counts['partition_molecules']+=1
                if '/lle/' in p:counts['LLE_systems']+=1
                if p.endswith('/complete.json'):counts['complete_chunks']+=1
            del registry
        status('collected_checkpoint_coverage',**counts,denominator=5830,LLE_denominator=227370)
        assert counts['partition_molecules']<=5830 and counts['LLE_systems']<=227370 and counts['complete_chunks']<=67
        if counts==dict(partition_molecules=5830,LLE_systems=227370,complete_chunks=67):
            target=D.parent/'promotion-ext39-v1'
            if not target.exists():run('build_phase10_release.py')
            if not (D/'delivery-verification.json').exists():run('verify_phase10_delivery.py')
            verification=json.loads((D/'delivery-verification.json').read_text())
            assert verification['status']=='complete_delivery_verified' and verification['manifest_sha256']==sha(target/'manifest.json')
            status('complete_delivery_verified_agent_review_required',verification=verification)
            return
        latest=D/'collection-status.json'
        before=sha(latest) if latest.exists() else None
        run('collect_phase10.py')
        # No artificial multi-minute idle while sealed returns remain. The
        # shared transport itself enforces connection backoff; no parallel pass.
        if latest.exists() and sha(latest)!=before and json.loads(latest.read_text()).get('scan_more'):continue
        pause()


if __name__=='__main__':
    try:main()
    except Exception as exc:status('inspection_required',error=repr(exc));raise
