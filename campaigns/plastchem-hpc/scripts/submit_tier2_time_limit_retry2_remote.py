"""Reconcile and submit the isolated tier-2 retry held; never release them here."""
import datetime
import fcntl
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path.home() / 'plastchem-euler'
R = ROOT / 'tier2-v1/time-limit-retry2'
lock = (ROOT / 'phase83-v1/throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


active = json.loads((ROOT / 'phase9-v1/active-throttle-controller.json').read_text())
assert active['filename'] == 'phase9_throttle_remote_v10.py'
assert active['sha256'] == sha(ROOT / 'phase9-v1' / active['filename'])
stage = json.loads((R / 'preparation-summary.json').read_text())
for name, pin in stage['file_pins'].items():
    assert sha(R / name) == pin, name
receipts = {}
for group in ['tier2']:
    manifest_path = R / group / 'manifest.json'
    m = json.loads(manifest_path.read_text())
    assert len(m['molecules']) == 1
    mol = m['molecules'][0]
    name = 'contam-tier2-timelimit-retry2'
    assert m['name'] == name and mol['array_index'] == 0
    hours = 24
    assert m['walltime'] == f'{hours}:00:00'
    receipt = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), group=group,
        name=name, purpose='tier2_time_limit_recovery', manifest_sha256=sha(manifest_path),
        original_task=mol['restart_provenance']['original_task'], operations=[])

    def call(args):
        p = subprocess.run(args, capture_output=True, text=True)
        receipt['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
        assert p.returncode == 0, (args, p.stderr)
        return p.stdout

    partition = call(['scontrol', 'show', 'partition', 'research', '-o'])
    limit = re.search(r'\bMaxTime=(\S+)', partition).group(1)
    if limit != 'UNLIMITED':
        days, clock = limit.split('-') if '-' in limit else ('0', limit)
        h, minute, second = map(int, clock.split(':'))
        assert hours * 3600 <= int(days) * 86400 + h * 3600 + minute * 60 + second
    q = call(['squeue', '-u', 'aaltamimi2', '--name=' + name, '-h', '-r', '-o', '%i|%j|%T'])
    a = call(['sacct', '-u', 'aaltamimi2', '--starttime=2026-09-22', '--name=' + name,
              '-nP', '--format=JobID,JobName%60,State,Elapsed,NodeList'])
    ids = {re.match(r'\d+', line).group() for line in (q + '\n' + a).splitlines() if re.match(r'\d+', line)}
    receipt.update(squeue_reconciliation=q, sacct_reconciliation=a)
    path = R / (group + '-submission.json')
    started = R / (group + '-submission.started')
    if ids:
        assert len(ids) == 1 and started.exists(), 'Unrecognized existing retry; inspect before adoption'
        assert json.loads(started.read_text())['manifest_sha256'] == receipt['manifest_sha256']
        job = next(iter(ids))
        if path.exists():
            prior = json.loads(path.read_text())
            assert prior['job_id'] == job and prior['manifest_sha256'] == receipt['manifest_sha256']
        receipt.update(job_id=job, decision='existing_no_resubmit')
    else:
        assert not started.exists() and not path.exists(), 'Unconfirmed previous attempt; never blindly resubmit'
        source = mol['restart_provenance']
        assert source['restart_source'] == 'salvaged_geometry' and source['original_task'] == '63873_54'
        states = call(['sacct', '-j', source['original_task'], '--starttime=2026-09-22', '-nP', '--format=JobID,State'])
        states = {p[0]: p[1].split()[0] for p in (line.split('|') for line in states.splitlines()) if len(p) >= 2}
        assert states.get(source['original_task']) in ['FAILED', 'TIMEOUT', 'CANCELLED']
        assert states[source['original_task']] == 'TIMEOUT' or source['original_failure_mode'] in ['scheduler_signal_10', 'walltime_censored']
        raw = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '-p', 'research', '-o', '%i|%T|%C|%j'])
        rows = [line.split('|') for line in raw.splitlines() if line]
        assert source['original_task'] not in {r[0] for r in rows}
        assert sum(int(r[2]) for r in rows if r[1] != 'PENDING') <= 64
        check = source['geometry_identity']
        assert check['identity_verified'] and check['connectivity_match']
        assert check['input_inchikey'] == mol['inchikey']
        assert sha(R / 'prepared' / mol['inchikey'] / 'input.xyz') == source['xyz_sha256'] == check['geometry_sha256']
        (R / 'logs').mkdir(exist_ok=True)
        args = ['sbatch', '--parsable', '--hold', '--array=0%1', '--nodes=1', '--ntasks=1',
                '--cpus-per-task=1', '--mem=4G', '--time=' + m['walltime'], '--partition=research',
                '--constraint=milan&cpu', '--exclude=euler09,euler10', '--chdir=' + str(R),
                '--output=' + str(R / 'logs/%A_%a.out'), '--error=' + str(R / 'logs/%A_%a.err'),
                str(R / (group + '.sbatch'))]
        receipt['command'] = args
        save(started, receipt)
        p = subprocess.run(args, capture_output=True, text=True)
        receipt.update(returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)
        save(R / (group + '-submission-attempt.json'), receipt)
        assert p.returncode == 0, receipt
        job = p.stdout.strip().split(';')[0]
        assert job.isdigit()
        receipt.update(job_id=job, decision='submitted_held')
        save(path, receipt)
    receipt['readback'] = call(['scontrol', 'show', 'job', job, '-o'])
    if receipt['decision'] == 'submitted_held':
        assert 'JobState=PENDING' in receipt['readback'] and 'Reason=JobHeldUser' in receipt['readback']
        assert 'ArrayTaskThrottle=1' in receipt['readback']
    save(path, receipt)
    receipts[group] = receipt
print(json.dumps(dict(status='held_submissions_reconciled', receipts=receipts)))
