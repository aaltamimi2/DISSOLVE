"""Finish the authorized A-8.4 handoff after full A-9 collection.

Local only: no scheduler mutation, new scientific calculation, product write,
git push or goal-completion claim. The agent reviews/commits the final evidence.
"""
import datetime
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
import traceback
from pathlib import Path

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase9-v1')
S = R / 'state/phase9-v1'
TARGET = D.parent / 'promotion-v1'
PYTHON = '/home/aaltamimi2/.venvs/cosmo-logp/bin/python'
CHECKS = {'denominator': 5830, 'partition_molecules': 5830,
          'partition_rows': 3731200, 'partition_row_denominator': 3731200,
          'LLE_systems': 373120, 'LLE_denominator': 373120,
          'fully_evaluated_molecules': 5830}


def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): digest.update(block)
    return digest.hexdigest()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def status(name, **details):
    value = dict(utc=utc(), status=name, **details)
    save(D / 'release-watch-status.json', value)
    print(json.dumps(value), flush=True)


def ready_counts(summary, terminal):
    return all(summary.get(key) == value and terminal.get(key) == value
               for key, value in CHECKS.items())


def require_pins(pins):
    for name, digest in pins.items():
        assert sha(R / name) == digest, 'Charter/release code changed; review before continuing: ' + name


def expected_footers(plans):
    return {f'{root}/{index:04d}/complete.json'
            for root, plan in plans.items() for index in range(len(plan['chunks']))}


def require_footers(registry, plans):
    missing = expected_footers(plans) - set(registry['files'])
    assert not missing, 'Terminal collector missing completion footers: ' + repr(sorted(missing))


def no_active_activity_jobs(recovery, job_ids):
    # The recovery observer includes all lane jobs. Only this frozen activity
    # production and its checkpoint recoveries/helpers gate the release here.
    return not any(row.split('|')[0].split('_')[0] in job_ids
                   for row in recovery['queue'].splitlines() if row)


def available_memory():
    return int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                    if line.startswith('MemAvailable:'))) * 1024


def run_check(script, arguments, log):
    with log.open('a') as stream:
        result = subprocess.run([PYTHON, str(R / 'scripts' / script), *arguments],
                                cwd=R, stdout=stream, stderr=subprocess.STDOUT)
    assert result.returncode == 0, f'{script} exited {result.returncode}; inspect {log}'


def main():
    lock = (S / 'release-watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    names = ['CHARTER.txt', 'scripts/watch_phase9_release.py',
             'scripts/audit_phase9_results.py', 'scripts/build_phase9_release.py',
             'scripts/build_phase84_release.py', 'scripts/verify_phase9_delivery.py']
    pins = {name: sha(R / name) for name in names}
    save(S / 'release-watch-process.json', dict(pid=os.getpid(), utc=utc(), pins=pins))
    save(D / 'release-watch-start.json', dict(pid=os.getpid(), utc=utc(), pins=pins,
         scope='Full audit/build/independent verification only after full frozen collection. No product writes; final agent review/commit required.'))
    last = None
    while True:
        try:
            require_pins(pins)
            marker = S / 'production-collection-terminal.json'
            summary = json.loads((D / 'production-summary.json').read_text())
            reason = 'waiting_for_complete_collection'
            terminal = json.loads(marker.read_text()) if marker.exists() else {}
            ready = ready_counts(summary, terminal)
            if ready:
                recovery = json.loads((D / 'recovery-watch-latest.json').read_text())
                jobs = {str(json.loads(p.read_text())['job_id']) for p in
                        [D / 'production-submission.json', *D.glob('production-retry-*-submission.json')]}
                age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(recovery['utc'])).total_seconds()
                ready = age <= 900 and not recovery['unhandled'] and no_active_activity_jobs(recovery, jobs)
                reason = 'waiting_for_terminal_activity_scheduler_readback'
            if ready and available_memory() < 2.5 * 1024**3:
                ready = False
                reason = 'waiting_for_local_memory'
            if not ready:
                current = (reason, summary['fully_evaluated_molecules'], summary['LLE_systems'])
                if current != last:
                    status(reason, fully_evaluated_molecules=current[1], LLE_systems=current[2], denominator=5830)
                    last = current
                time.sleep(30)
                continue
            # Collection has reported terminal. Still take its actual lock,
            # rather than relying on a PID file or timestamp alone.
            with (S / 'collect.lock').open('a') as collection_lock:
                try: fcntl.flock(collection_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    status('waiting_for_active_collector_to_exit')
                    time.sleep(30)
                    continue
                plans = {root: json.loads((D / name).read_text()) for root, name in
                         [('chunk-probe-results-v1', 'chunk-probe-plan.json'),
                          ('production-results-v1', 'production-plan.json')]}
                registry = json.loads((D / 'collection.json').read_text())
                require_footers(registry, plans)
                require_pins(pins)
                if not TARGET.exists():
                    status('running_full_audit_and_build', collection_terminal_utc=terminal['utc'])
                    run_check('build_phase9_release.py', [], D / 'release-final-build.log')
                require_pins(pins)
                audit = json.loads((TARGET / 'provenance/results-audit.json').read_text())
                assert audit['status'] == 'complete' and audit['fully_evaluated_molecules'] == 5830
                while available_memory() < 2.5 * 1024**3:
                    status('waiting_for_memory_before_independent_verification')
                    time.sleep(30)
                    require_pins(pins)
                status('running_independent_delivery_verification', path=str(TARGET))
                output = D / 'delivery-verification.json'
                run_check('verify_phase9_delivery.py', [str(TARGET), '--output', str(output)],
                          D / 'release-final-verification.log')
                require_pins(pins)
                checked = json.loads(output.read_text())
                seal = json.loads((D / 'release-seal.json').read_text())
                assert checked['status'] == 'complete_delivery_verified'
                assert checked['manifest_sha256'] == seal['manifest_sha256'] == sha(TARGET / 'manifest.json')
                assert checked['counts'] == {'partition_rows': 3731200, 'lle_rows': 373120}
                report = R / 'reports/phase84-release'
                report.mkdir(exist_ok=True)
                shutil.copyfile(output, report / 'delivery-verification.json')
                final = json.loads((TARGET / 'summary.json').read_text())
                with (report / 'REPORT.md').open('a') as stream:
                    stream.write(f"\nIndependent post-export verification passed at {checked['utc']}: all payload hashes, frozen inputs, identities, unique keys and full quantity coverage checked. Fully evaluated: 5,830/5,830; fully qualified across every quantity: {final['fully_qualified_contaminants']}/5,830. LLE outcomes remain explicit: `{json.dumps(checked['statuses']['lle_rows'], sort_keys=True)}`. No unresolved output is silently promoted. This verifies computation/provenance and the agreed reference comparisons, not experimental accuracy.\n\nFinal agent completion review and milestone commit remain required. No product write or push was performed.\n")
                status('complete_delivery_verified_agent_review_required', path=str(TARGET),
                       release_id=seal['release_id'], verification=str(output),
                       report=str(report / 'REPORT.md'), commit_required=True)
                return
        except Exception as error:
            status('inspection_required', error=repr(error), traceback=traceback.format_exc())
            return 1  # Never blindly rebuild/overwrite a sealed artifact after a failure.


if __name__ == '__main__': raise SystemExit(main())
