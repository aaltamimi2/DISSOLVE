"""Launch A-10 only after the measured <=510 CPU-hour gate; reconcile first."""
import datetime
import fcntl
import hashlib
import json
import re
import subprocess
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase10-v1'
NAME = 'contam-phase10-production-v1'
lock = (D.parent / 'phase83-v1/throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
receipt = dict(name=NAME, purpose='extension_production',
    utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), operations=[])


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    receipt['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


gate_path = D / 'calibration-clearance.json'
gate = json.loads(gate_path.read_text())
assert gate['status'] == 'passed' and gate['projected_CPU_h'] <= gate['limit_CPU_h'] == 510
assert gate['calibration_molecules'] == 40 and gate['LLE_systems'] == 1560
assert gate['exact_batch_control_comparisons'] == 80 and gate['max_control_difference'] <= 1e-9
for name, digest in gate['file_pins'].items(): assert sha(D/name) == digest, name
plan = json.loads((D/'production-plan.json').read_text())
cal = json.loads((D/'calibration-plan-v2.json').read_text())
assert plan['calibration_plan_sha256'] == sha(D/'calibration-plan-v2.json')
units = [u for chunk in plan['chunks'] for u in chunk]
reused = {u['id'] for c in cal['chunks'] for u in c}
assert len(units) == len({u['id'] for u in units}) == 5790 and len(reused) == 40
assert {u['id'] for u in units}.isdisjoint(reused)
assert {u['id'] for u in units} | reused == {f'cohort-{i:05d}' for i in range(5830)}
assert len(plan['chunks']) == 58 and plan['constraint'] == '(milan|genoa)&cpu'
for i, chunk in enumerate(cal['chunks']):
    folder = D/cal['output']/f'{i:04d}'
    footer = json.loads((folder/'complete.json').read_text())
    assert footer['signature']['plan_sha256'] == sha(D/'calibration-plan-v2.json')
    assert footer['unit_ids'] == [u['id'] for u in chunk]
    assert len(footer['lle_statuses']) == 39*len(chunk)
    for s in ['water', 'hexane']:
        r = json.loads((folder/f'CONTROL__{s}-comparison.json').read_text())
        assert r['passed'] and r['max_abs_ln_gamma'] <= 1e-9 and r['n'] == len(chunk)
active = json.loads((D.parent/'phase9-v1/active-throttle-controller.json').read_text())
assert active['filename'] == 'phase9_throttle_remote_v5.py'
assert sha(D.parent/'phase9-v1'/active['filename']) == active['sha256']
queue = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '--name='+NAME, '-o', '%i|%T|%R'])
account = call(['sacct', '-nP', '-u', 'aaltamimi2', '--starttime=2026-09-24', '--name='+NAME,
                '--format=JobID,JobName%60,State,Elapsed,NodeList'])
receipt.update(squeue_reconciliation=queue, sacct_reconciliation=account,
    gate_sha256=sha(gate_path), plan_sha256=sha(D/'production-plan.json'))
found = {s.split('|')[0].split('_')[0].split('.')[0] for s in (queue+'\n'+account).splitlines()
         if re.match(r'^\d', s)}
prior = D/'production-submission.json'
marker = D/'production-submission-unconfirmed.json'
if found:
    assert len(found) == 1
    job = next(iter(found))
    if prior.exists():
        old = json.loads(prior.read_text())
        assert old['job_id'] == job and old['gate_sha256'] == receipt['gate_sha256']
        receipt.update(original_submission=old, submitted_utc=old.get('submitted_utc'))
    receipt.update(job_id=job, decision='existing_no_resubmission')
else:
    assert not prior.exists() and not marker.exists(), 'Prior or unconfirmed submission requires reconciliation'
    # This throttle leaves 37 slots for tier 2 even after its dependency clears.
    # The shared allocator separately reserves every running allocation before
    # it releases this held array; submission alone cannot cause starts.
    command = ['sbatch', '--parsable', '--hold', '--job-name='+NAME,
        '--partition=research', '--constraint=(milan|genoa)&cpu', '--exclude=euler09,euler10',
        '--nodes=1', '--ntasks=1', '--cpus-per-task=1', '--mem=4G', '--time=08:00:00',
        '--no-requeue', '--array=0-57%27', '--chdir='+str(D),
        '--output='+str(D/'logs/production-%A_%a.out'), '--error='+str(D/'logs/production-%A_%a.err'),
        '--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; '
        '../phase8-v1/venv/bin/python -u phase10_worker_v2.py production-plan.json "$SLURM_ARRAY_TASK_ID"']
    receipt['command'] = command
    marker.write_text(json.dumps(receipt, indent=2)+'\n')
    job = call(command).strip().split(';')[0]
    assert job.isdigit()
    receipt.update(job_id=job, initial_throttle=27, decision='submitted_held_for_shared_allocator',
                   submitted_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
temp = prior.with_suffix('.tmp')
temp.write_text(json.dumps(receipt, indent=2)+'\n'); temp.replace(prior)
print(json.dumps(receipt))
