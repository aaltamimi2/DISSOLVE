"""Reconcile and submit only the authorized A-10 small batch, initially held.

The shared allocator releases it after reserving all possible competing starts.
A missing receipt after an ambiguous submission never triggers blind resubmit.
"""
import datetime
import fcntl
import hashlib
import json
import re
import subprocess
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase10-v1'
NAME = 'contam-phase10-calibration-v2'
lock = (D.parent / 'phase83-v1/throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
receipt = dict(name=NAME, purpose='extension_calibration',
               utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), operations=[])


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    receipt['operations'].append(dict(command=args, returncode=p.returncode,
                                     stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def save():
    p = D / 'calibration-v2-submission.json'
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(receipt, indent=2) + '\n')
    temp.replace(p)


plan = json.loads((D / 'calibration-plan-v2.json').read_text())
assert plan['cost_limit_CPU_h'] == 510 and len(plan['chunks']) == 9
assert plan['manifest_sha256'] == sha(D / 'manifest.json')
manifest = json.loads((D / 'manifest.json').read_text())
assert len(manifest['solvents']) == 39 and manifest['denominator'] == 5830
assert plan['constraint'] == '(milan|genoa)&cpu'
for name, digest in json.loads((D / 'staging-pins-v2.json').read_text()).items():
    assert sha(D / name) == digest, name
for s in manifest['solvents']:
    assert sha(D / s['surface']) == s['surface_sha256'], s['name']
for u in sum(plan['chunks'], []):
    assert sha(D / u['surface']) == u['surface_sha256'], u['id']
    source = D / u['primary_partition']
    seal = json.loads(source.with_suffix('.json.sha256.json').read_text())
    assert sha(source) == seal['sha256'] and seal['signature']['plan_sha256'] == u['primary_plan_sha256']
active = json.loads((D.parent / 'phase9-v1/active-throttle-controller.json').read_text())
assert active['filename'] == 'phase9_throttle_remote_v5.py'
assert sha(D.parent / 'phase9-v1' / active['filename']) == active['sha256']

previous_queue = call(['squeue', '-h', '-r', '-u', 'aaltamimi2',
                       '--name=contam-phase10-calibration-v1', '-o', '%i|%T'])
assert not previous_queue.strip(), 'Previous attempt still active'
previous_accounting = call(['sacct', '-nP', '-j', '68833', '--format=JobID,State,ElapsedRaw,ExitCode'])
previous = {r.split('|')[0]:r.split('|')[1] for r in previous_accounting.splitlines() if r and '.' not in r.split('|')[0]}
assert previous == {'68833_'+str(i):'FAILED' for i in range(9)}
receipt['previous_attempt_accounting'] = previous_accounting
receipt['previous_attempt_outcome'] = 'Control reproduction failure before extension activities/LLE; preserved separately'
queue = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '--name=' + NAME, '-o', '%i|%T|%R'])
account = call(['sacct', '-nP', '-u', 'aaltamimi2', '--starttime=2026-09-24', '--name=' + NAME,
                '--format=JobID,JobName%60,State,Elapsed,NodeList'])
receipt.update(squeue_reconciliation=queue, sacct_reconciliation=account,
               plan_sha256=sha(D / 'calibration-plan-v2.json'))
found = {s.split('|')[0].split('_')[0].split('.')[0] for s in (queue + '\n' + account).splitlines()
         if re.match(r'^\d', s)}
if found:
    assert len(found) == 1
    job = next(iter(found))
    prior = D / 'calibration-v2-submission.json'
    if prior.exists():
        old = json.loads(prior.read_text())
        assert old['job_id'] == job and old['plan_sha256'] == receipt['plan_sha256']
        receipt['original_submission'] = old
        receipt['submitted_utc'] = old.get('submitted_utc')
    receipt.update(job_id=job, decision='existing_no_resubmission')
else:
    assert not (D / 'calibration-v2-submission-unconfirmed.json').exists(), 'Unconfirmed attempt requires reconciliation'
    assert not (D / 'calibration-v2-submission.json').exists(), 'Receipt exists but scheduler history absent'
    command = ['sbatch', '--parsable', '--hold', '--job-name=' + NAME, '--partition=research',
        '--constraint=(milan|genoa)&cpu', '--exclude=euler09,euler10', '--nodes=1', '--ntasks=1',
        '--cpus-per-task=1', '--mem=4G', '--time=04:00:00', '--no-requeue', '--array=0-8%9',
        '--chdir=' + str(D), '--output=' + str(D / 'logs/calibration-v2-%A_%a.out'),
        '--error=' + str(D / 'logs/calibration-v2-%A_%a.err'),
        '--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; '
        '../phase8-v1/venv/bin/python -u phase10_worker_v2.py calibration-plan-v2.json "$SLURM_ARRAY_TASK_ID"']
    receipt['command'] = command
    (D / 'calibration-v2-submission-unconfirmed.json').write_text(json.dumps(receipt, indent=2) + '\n')
    job = call(command).strip().split(';')[0]
    assert job.isdigit()
    receipt.update(job_id=job, decision='submitted_held_for_shared_allocator', initial_throttle=9,
                   submitted_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
save()
print(json.dumps(receipt))
