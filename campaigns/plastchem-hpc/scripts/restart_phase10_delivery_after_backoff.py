"""Reconcile the six verified archives after the September 24 backoff stop."""
import datetime
import fcntl
import hashlib
import json
import subprocess
from pathlib import Path

R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase10-v1')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    old=json.loads((D/'delivery-watch-process.json').read_text())
    assert sha(R/'CHARTER.txt')==old['charter_sha256']
    assert json.loads((D/'delivery-watch-status.json').read_text())['status']=='inspection_required'
    assert not Path('/proc/'+str(old['pid'])+'/cmdline').exists(),'Existing watcher still live'
    assert not (D/'collection-pending.json').exists()
    with (D/'collect.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        reg=json.loads((D/'collection.json').read_text())
        assert len(reg['archives'])==6 and len(reg['files'])==10980
        assert {p.name for p in (D/'returns').glob('*.tar.gz')}=={Path(a['path']).name for a in reg['archives']}
        for a in reg['archives']:assert sha(Path(a['path']))==a['sha256']
        for b in reg['compact_bundles']:assert sha(Path(b['path']))==b['sha256']
        receipt=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='stopped_collector_reconciled_before_restart',
            archives=6,verified_files=10980,pending_transfer=False,old_watcher_pid=old['pid'],
            charter_sha256=old['charter_sha256'],collector_sha256=sha(R/'scripts/collect_phase10.py'))
        (D/'collection-restart-reconciliation.json').write_text(json.dumps(receipt,indent=2)+'\n')
    with (D/'delivery-watch.log').open('a') as log:
        child=subprocess.Popen(['/home/aaltamimi2/.venvs/cosmo-logp/bin/python','-u','scripts/watch_phase10_delivery.py'],
            cwd=R,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    receipt['new_watcher_pid']=child.pid
    (D/'delivery-restart.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
