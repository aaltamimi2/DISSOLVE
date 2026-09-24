"""Reconcile failed production tasks, then queue a checkpoint resume HELD.

The shared controller alone releases the array after reserving its capacity.
This script neither alters a running task nor changes any frozen numerical file.
"""
import datetime
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase9-v1'
attempt, selection = sys.argv[1:]
assert re.fullmatch(r'\d{2}', attempt)
assert re.fullmatch(r'\d+(,\d+)*', selection)
indices = [int(x) for x in selection.split(',')]
assert indices == sorted(set(indices)) and len(indices) <= 8
name = 'contam-phase9-retry-' + attempt
receipt_path = D / f'production-retry-{attempt}-submission.json'
pending = D / f'production-retry-{attempt}-unconfirmed.json'
lock = (D.parent / 'phase83-v1/throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
receipt = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), name=name,
               indices=indices, operations=[], recovery_policy='Per-system exact binary-grid nonconvergence is recorded with null values; unchanged solver and existing sealed checkpoints reused')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    receipt['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


activation = json.loads((D / 'active-throttle-controller.json').read_text())
assert activation['filename'] == 'phase9_throttle_remote_v3.py'
assert sha(D / activation['filename']) == activation['sha256']
pins = json.loads((D / 'recovery-code-pins.json').read_text())
for file, digest in pins.items():
    assert sha(D / file) == digest, file
receipt['recovery_code_pins'] = pins
clearance = json.loads((D / 'production-clearance.json').read_text())
for file, digest in clearance['file_pins'].items():
    assert sha(D / file) == digest, file
plan = json.loads((D / 'production-plan.json').read_text())
assert all(0 <= i < len(plan['chunks']) for i in indices)
original = str(json.loads((D / 'production-submission.json').read_text())['job_id'])
q = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '--name=' + name, '-o', '%i|%j|%T'])
a = call(['sacct', '-nP', '-u', 'aaltamimi2', '--starttime=today', '--name=' + name,
          '--format=JobID%40,JobName%60,State,Elapsed,NodeList'])
found = {s.split('|')[0].split('_')[0].split('.')[0] for s in (q + '\n' + a).splitlines() if re.match(r'^\d', s)}
receipt['reconciliation'] = dict(squeue=q, sacct=a)
if found:
    assert len(found) == 1
    job = next(iter(found))
    previous = json.loads((receipt_path if receipt_path.exists() else pending).read_text())
    assert previous['indices'] == indices and previous['name'] == name
    if 'job_id' in previous:
        assert previous['job_id'] == job
    receipt.update(previous, job_id=job, reconciliation=receipt['reconciliation'], decision='existing_no_resubmission')
else:
    assert not receipt_path.exists() and not pending.exists(), 'Unconfirmed prior attempt requires explicit reconciliation'
    targets = [f'{original}_{i}' for i in indices]
    original_queue = call(['squeue', '-h', '-r', '-j', original, '-o', '%i|%T'])
    assert not any(s.split('|')[0] in targets for s in original_queue.splitlines())
    accounting = call(['sacct', '-nP', '-j', ','.join(targets), '--format=JobID%40,State%30,ElapsedRaw,ExitCode'])
    states = {s.split('|')[0]: s.split('|')[1] for s in accounting.splitlines() if s}
    receipt['original_accounting'] = accounting
    receipt['original_error_pins'] = {}
    for i, task in zip(indices, targets):
        assert states[task] == 'FAILED', (task, states.get(task))
        assert not (D / plan['output'] / f'{i:04d}' / 'complete.json').exists()
        error = D / 'logs' / f'production-{task}.err'
        assert error.read_text().rstrip().endswith('ValueError: COSMOspace did not converge for binary grid')
        receipt['original_error_pins'][str(error.relative_to(D))] = sha(error)
    cmd = ['sbatch', '--parsable', '--hold', '--job-name=' + name, '--partition=research',
           '--constraint=' + plan['constraint'], '--exclude=euler09,euler10', '--nodes=1', '--ntasks=1',
           '--cpus-per-task=1', '--mem=4G', '--time=08:00:00', '--no-requeue',
           '--array=' + selection + '%' + str(min(8, len(indices))), '--chdir=' + str(D),
           '--output=' + str(D / 'logs/recovery-%A_%a.out'), '--error=' + str(D / 'logs/recovery-%A_%a.err'),
           '--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; '
           '../phase8-v1/venv/bin/python -u phase9_retry_entry.py production-plan.json "$SLURM_ARRAY_TASK_ID"']
    receipt['command'] = cmd
    pending.write_text(json.dumps(receipt, indent=2) + '\n')
    job = call(cmd).strip().split(';')[0]
    assert job.isdigit()
    receipt.update(job_id=job, decision='submitted_held_for_shared_cap_controller')
receipt['scheduler_readback'] = call(['scontrol', 'show', 'job', job, '-o'])
temporary = receipt_path.with_suffix('.tmp')
temporary.write_text(json.dumps(receipt, indent=2) + '\n'); temporary.replace(receipt_path)
print(json.dumps(receipt))
