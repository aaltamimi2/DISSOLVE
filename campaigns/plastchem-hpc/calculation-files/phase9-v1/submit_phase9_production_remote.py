"""Euler-side reconciled launch, gated by complete correctness and timing evidence."""
import datetime, fcntl, hashlib, json, re, subprocess
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase9-v1'
OLD = D.parent / 'phase83-v1'
name = 'contam-phase9-production-v1'
lock = (OLD / 'throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
receipt = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), name=name, operations=[])


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    receipt['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def save():
    tmp = D / 'production-submission.tmp'
    tmp.write_text(json.dumps(receipt, indent=2) + '\n')
    tmp.replace(D / 'production-submission.json')


decision = json.loads((D / 'production-clearance.json').read_text())
assert decision['status'] == 'passed' and decision['limit_cpu_hours'] == 500
for relative, digest in decision['file_pins'].items():
    assert sha(D / relative) == digest, relative
gate = json.loads((D / 'gate-comparison-final.json').read_text())
cost = json.loads((D / 'chunk-cost-final.json').read_text())
assert gate['status'] == 'reproduction_passed_cost_pending' and not gate['errors']
assert gate['lle_compared'] == gate['lle_denominator'] == 2240
assert gate['partition_numerical_passes'] == 17733
assert gate['documented_finite_dilution_reference_differences'] == 59
assert cost['cost_gate_passes'] and cost['planning_cpu_hours_including_diagnostics'] <= 500
assert cost['lle_systems'] == cost['lle_denominator'] == 6400
assert cost['production_sized_calibration_comparison']['status'] == 'passed'
probe = json.loads((D / 'chunk-probe-results-v1/0000/complete.json').read_text())
assert sha(D / 'chunk-probe-results-v1/0000/complete.json') == decision['probe_complete_sha256']
plan = json.loads((D / 'production-plan.json').read_text())
constraint=plan.get('constraint','milan&cpu')
assert constraint in ['milan&cpu','(milan|genoa)&cpu']
if constraint!='milan&cpu':
    genoagate=json.loads((D/'genoa-comparison.json').read_text())
    assert genoagate['status']=='passed' and not genoagate['errors']
    assert genoagate['worker_cpu_sha256']==sha(D/'phase9_worker_cpu.py')
    assert plan['worker_module']=='phase9_worker_cpu'
units = sum(plan['chunks'], [])
ids = [u['id'] for u in units] + probe['unit_ids']
assert len(ids) == len(set(ids)) == 5830
assert set(ids) == {f'cohort-{i:05d}' for i in range(5830)}
assert set(plan['reuse_completed_chunk_probe_ids']) == set(probe['unit_ids'])
assert plan['complete_chunk_probe_plan_sha256'] == sha(D / 'chunk-probe-plan.json')
assert decision['entry_resume_test']['status'] == 'completed_chunk_verified_and_skipped'

q = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '--name=' + name, '-o', '%i|%T|%R'])
a = call(['sacct', '-nP', '-u', 'aaltamimi2', '--starttime=2026-09-23', '--name=' + name,
          '--format=JobID,JobName%60,State,Elapsed,NodeList'])
receipt.update(squeue_reconciliation=q, sacct_reconciliation=a, clearance_sha256=sha(D / 'production-clearance.json'))
found = {s.split('|')[0].split('_')[0].split('.')[0] for s in (q + '\n' + a).splitlines() if re.match(r'^\d', s)}
if found:
    assert len(found) == 1
    job = next(iter(found))
    prior = D / 'production-submission.json'
    if prior.exists():
        saved = json.loads(prior.read_text()); assert saved['job_id'] == job
        receipt['original_submission'] = saved
        receipt['launch_utc'] = saved.get('launch_utc')
    receipt.update(job_id=job, decision='existing_no_resubmission')
else:
    # A previous ambiguous attempt is never retried automatically even if
    # accounting has not yet caught up with a disconnected sbatch process.
    assert not (D / 'production-submission-unconfirmed.json').exists(), 'Unconfirmed attempt requires reconciliation'
    raw = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '-p', 'research', '-o', '%i|%T|%C|%j|%R'])
    rows = [s.split('|') for s in raw.splitlines() if s]
    receipt['queue_before'] = raw
    assert all(r[0].split('_')[0] in ['68234', '63873', '65677'] and r[2] == '1' for r in rows)
    old = [r for r in rows if r[0].startswith('68234_')]
    assert all(r[1] == 'PENDING' and r[4] == '(JobHeldUser)' for r in old), 'Original production not completely held/drained'
    large = [r for r in rows if r[0].startswith('65677_')]
    tier = [r for r in rows if r[0].startswith('63873_')]
    tier_cap = min(37, len(tier))
    if tier:
        detail = call(['scontrol', 'show', 'job', '63873', '-o'])
        active = [dict(re.findall(r'(\S+?)=(\S+)', s)) for s in detail.splitlines() if s]
        active = [r for r in active if r['JobState'] in ['PENDING', 'RUNNING', 'COMPLETING']]
        old_caps = {int(r['ArrayTaskThrottle']) for r in active}; assert len(old_caps) == 1
        old_cap = old_caps.pop()
        if large:
            assert all(r['JobState'] == 'PENDING' and 'afterany:65677_' in r['Dependency']
                       and '(unfulfilled)' in r['Dependency'] for r in active)
            tier_cap = min(len(large), tier_cap)
        else:
            assert sum(r[1] != 'PENDING' for r in tier) <= tier_cap
        if old_cap != tier_cap:
            cmd = ['scontrol', 'update', 'JobId=63873', f'ArrayTaskThrottle={tier_cap}']
            p = subprocess.run(cmd, capture_output=True, text=True)
            receipt['operations'].append(dict(command=cmd, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
            assert p.returncode == 0 or (p.stderr.strip() and all(s.endswith(': Job has already finished') for s in p.stderr.strip().splitlines())), p.stderr
            readback = call(['scontrol', 'show', 'job', '63873', '-o'])
            active_after = [dict(re.findall(r'(\S+?)=(\S+)', s)) for s in readback.splitlines() if s]
            assert {int(r['ArrayTaskThrottle']) for r in active_after if r['JobState'] in ['PENDING', 'RUNNING', 'COMPLETING']} == {tier_cap}
            event = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), job_id='63873',
                         **{'from': old_cap, 'to': tier_cap}, readback_throttle=tier_cap,
                         reason='A-9 launch: guard tier-2 transition under the shared 64 cap')
            for p in [D / 'throttle-changes.jsonl', OLD / 'throttle-changes.jsonl']:
                with p.open('a') as f: f.write(json.dumps(event) + '\n')
    reserve = max(len(large), tier_cap) if large else tier_cap
    throttle = min(64 - reserve, len(plan['chunks']))
    assert throttle >= 1
    # Only the held obsolete tasks are cancelled; completed raw outputs remain.
    if old: call(['scancel', '--state=PENDING', '68234'])
    cmd = ['sbatch', '--parsable', '--hold', '--job-name=' + name, '--partition=research',
           '--constraint='+constraint, '--exclude=euler09,euler10', '--nodes=1', '--ntasks=1', '--cpus-per-task=1', '--mem=4G',
           '--time=08:00:00', '--no-requeue', f'--array=0-{len(plan["chunks"])-1}%{throttle}',
           '--chdir=' + str(D), '--output=' + str(D / 'logs/production-%A_%a.out'),
           '--error=' + str(D / 'logs/production-%A_%a.err'),
           '--wrap=export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 PYTHONPATH=../phase8-v1; '
           '../phase8-v1/venv/bin/python -u phase9_entry.py production-plan.json "$SLURM_ARRAY_TASK_ID"']
    receipt['command'] = cmd
    (D / 'production-submission-unconfirmed.json').write_text(json.dumps(receipt, indent=2) + '\n')
    job = call(cmd).strip().split(';')[0]; assert job.isdigit()
    receipt.update(job_id=job, decision='submitted_held_then_release', initial_throttle=throttle,
                   reserved_other_slots=reserve, submitted_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save()
# Reconcile a lost confirmation without ever submitting a second array.
current = call(['squeue', '-h', '-r', '-j', job, '-o', '%i|%T|%R'])
held = [s for s in current.splitlines() if '|PENDING|(JobHeldUser)' in s]
if held:
    assert len(held) == len(current.splitlines()), 'Partial held array requires explicit reconciliation'
    receipt['launch_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    receipt['launch_timestamp_basis'] = 'UTC immediately before issuing scontrol release; scheduler readback follows'
    save()
    call(['scontrol', 'release', job])
receipt['scheduler_readback'] = call(['scontrol', 'show', 'job', job, '-o'])
assert 'Reason=JobHeldUser' not in receipt['scheduler_readback']
receipt['queue_after'] = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '-p', 'research', '-o', '%i|%T|%C|%j|%R'])
receipt['running_after'] = sum('|RUNNING|' in s or '|COMPLETING|' in s for s in receipt['queue_after'].splitlines())
assert receipt['running_after'] <= 64
save()
print(json.dumps(receipt))
