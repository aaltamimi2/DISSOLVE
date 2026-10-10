"""Run final supplements serially after both delivery and full raw validation.

No submission, transport, release mutation, or automatic retry after failure.
An agent must inspect the completed artifacts and rendered figures afterward.
"""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase10-v1')
PY = '/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
STEPS = [
    ('reconcile_phase10_final_outcomes.py', 'final-outcome-reconciliation'),
    ('validate_phase10_experimental.py', 'experimental-validation-v1'),
]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def status(name, **kwargs):
    value = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status=name, **kwargs)
    save(D / 'final-validation-watch-status.json', value)
    print(json.dumps(value), flush=True)


def readiness(delivery, raw):
    for value in [delivery, raw]:
        assert value.get('status') != 'inspection_required', value
    return (delivery.get('status') == 'complete_delivery_verified_agent_review_required'
            and raw.get('status') == 'complete_raw_audit_agent_review_required')


def main():
    lock = (R / 'state/phase10-final-validation-watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    pins = {name: sha(R / 'scripts' / name) for name in [Path(__file__).name, *[s[0] for s in STEPS]]}
    charter = sha(R / 'CHARTER.txt')
    save(D / 'final-validation-watch-process.json', dict(pid=os.getpid(),
        utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), interpreter=PY,
        script_pins=pins, charter_sha256=charter))

    def check_pins():
        assert sha(R / 'CHARTER.txt') == charter, 'Charter changed; inspect scope'
        for name, pin in pins.items():
            assert sha(R / 'scripts' / name) == pin, ('Pinned code changed', name)

    while True:
        check_pins()
        # These watcher status files are atomically replaced. Terminal statuses
        # are published only after the child has exited and receipts are durable.
        delivery = json.loads((D / 'delivery-watch-status.json').read_text())
        raw = json.loads((D / 'raw-audit-watch-status.json').read_text())
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        if readiness(delivery, raw) and available >= 2.5 * 1024**3:
            break
        status('waiting_for_verified_delivery_and_raw_audit', delivery=delivery['status'],
               raw=raw['status'], available_bytes=available)
        time.sleep(45)

    for script, folder in STEPS:
        check_pins()
        assert not (D / folder).exists(), ('Existing supplement requires reconciliation', folder)
        available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                             if line.startswith('MemAvailable:'))) * 1024
        while available < 2.5 * 1024**3:
            status('memory_guard', next_step=script, available_bytes=available)
            time.sleep(45)
            check_pins()
            available = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                                 if line.startswith('MemAvailable:'))) * 1024
        with (D / 'final-validation-children.log').open('a') as log:
            child = subprocess.Popen([PY, '-u', str(R / 'scripts' / script)], cwd=R,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        status('running_supplement', script=script, child_pid=child.pid)
        while child.poll() is None:
            time.sleep(5)
        assert child.returncode == 0, (script, child.returncode, 'Inspect retained log; no automatic retry')
        check_pins()
    outcome = json.loads((D / 'final-outcome-reconciliation/summary.json').read_text())
    assert outcome['status'] == 'final_outcomes_reconciled'
    stats = json.loads((D / 'experimental-validation-v1/statistics.json').read_text())
    assert stats['cohort'] == 5830 and stats['reference_entries'] == 1179
    assert stats['no_recalibration'] and stats['all_prior_reference_choices_preserved']
    status('supplements_complete_agent_and_figure_review_required',
           reconciliation_sha256=sha(D / 'final-outcome-reconciliation/summary.json'),
           statistics_sha256=sha(D / 'experimental-validation-v1/statistics.json'),
           statistics=stats['statistics'])


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        status('inspection_required', error=repr(exc))
        raise
