"""Shared-cap A-9 allocator, run on Euler under the original controller lock.

Production gets available slots first. Tier 2's dependency transition is guarded
before borrowing its reservation. Solvent DFT starts only in capacity left by A.
No cancellation, resubmission, or global cap increase occurs here.
"""
import collections, datetime, fcntl, json, re, subprocess
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase9-v1'
OLD = D.parent / 'phase83-v1'
lock = (OLD / 'throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
CAP = 64
PROD = str(json.loads((D / 'production-submission.json').read_text())['job_id'])
TIER, LARGE = '63873', '65677'
solvent_receipt = D.parent / 'phase9-solvent-library-v1/submission.json'
B = str(json.loads(solvent_receipt.read_text())['job_id']) if solvent_receipt.exists() else None
names = {PROD: 'contam-phase9-production-v1', TIER: 'contam-tier2-chno500700-v1',
         LARGE: 'contam-polymer24a-large-v1'}
if B: names[B] = 'contam-phase9-common-solvents-v1'
nitro_root = D.parent / 'polymer-v1/nitro-retry1'
nitro_receipt = nitro_root / 'submission.json'
N = str(json.loads(nitro_receipt.read_text())['job_id']) if nitro_receipt.exists() else None
if N: names[N] = 'contam-polymer24a-nitro-retry1'
out = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), cap=CAP,
           changes=[], operations=[])


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    out['operations'].append(dict(command=args, returncode=p.returncode,
                                  stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def queue():
    rows = [s.split('|') for s in call(['squeue', '-h', '-r', '-u', 'aaltamimi2',
             '-p', 'research', '-o', '%i|%T|%C|%j|%R']).splitlines() if s]
    assert all(r[0].split('_')[0] in names and r[3] == names[r[0].split('_')[0]]
               and int(r[2]) == 1 for r in rows), 'Unexpected research allocation'
    assert sum(r[1] != 'PENDING' for r in rows) <= CAP
    return rows


def details(job):
    lines = call(['scontrol', 'show', 'job', job, '-o']).splitlines()
    rows = [dict(re.findall(r'(\S+?)=(\S+)', s)) for s in lines if s]
    rows = [r for r in rows if r['JobState'] in ['PENDING', 'RUNNING', 'COMPLETING']]
    assert rows and all(r['JobName'] == names[job] and r['Partition'] == 'research' for r in rows)
    limits = {int(r['ArrayTaskThrottle']) for r in rows}
    assert len(limits) == 1
    return rows, limits.pop()


def event(value):
    value['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for p in [OLD / 'throttle-changes.jsonl', D / 'throttle-changes.jsonl']:
        with p.open('a') as f: f.write(json.dumps(value) + '\n')
    out['changes'].append(value)


def change(job, target, reason):
    rows, old = details(job)
    if old == target: return
    assert 1 <= target <= CAP
    cmd = ['scontrol', 'update', f'JobId={job}', f'ArrayTaskThrottle={target}']
    # An array can retain completed expanded records. SLURM may report those
    # while successfully updating the remaining records; readback is decisive.
    p = subprocess.run(cmd, capture_output=True, text=True)
    out['operations'].append(dict(command=cmd, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0 or (p.stderr.strip() and all(
        s.endswith(': Job has already finished') for s in p.stderr.strip().splitlines())), p.stderr
    _, actual = details(job)
    assert actual == target
    event(dict(job_id=job, **{'from': old, 'to': target}, reason=reason,
               readback_throttle=actual, command=cmd))


def main():
    rows = queue()
    by = {j: [r for r in rows if r[0].split('_')[0] == j] for j in names}
    running = lambda j: sum(r[1] != 'PENDING' for r in by.get(j, []))
    out['running_before'] = {j: running(j) for j in names}
    # Recovered time-limit retries are held until every higher-priority array
    # has drained and the original-attempt archive/registration is durable.
    # They therefore never borrow an uncounted slot from production or tier 2.
    if N and by[N]:
        nr, nc = details(N)
        others = any(by[j] for j in names if j != N)
        held = all(r[1] == 'PENDING' and r[4] == '(JobHeldUser)' for r in by[N])
        ready = nitro_root / 'ready-for-release.json'
        if others:
            assert held, 'Nitro retries must remain held behind every earlier array'
        else:
            assert nc <= 7 and len(by[N]) <= 7
            if held and ready.exists():
                marker = json.loads(ready.read_text())
                assert str(marker['job_id']) == N and marker['original_archives_verified']
                assert marker['registered_entries'] == 7
                command = ['scontrol', 'release', N]
                call(command)
                readback = call(['scontrol', 'show', 'job', N, '-o'])
                assert 'Reason=JobHeldUser' not in readback
                event(dict(job_id=N, **{'from': 'held', 'to': 'released'},
                           reason='All earlier arrays drained; seven archived and registered time-limit retries run last',
                           command=command, scheduler_readback=readback))
            elif not held:
                assert ready.exists(), 'Nitro retry started before archive registration'
            after = queue()
            out['running_after'] = dict(collections.Counter(r[0].split('_')[0] for r in after if r[1] != 'PENDING'))
            out['status'] = 'verified'
            return
    # A partially failed submission/release is recoverable, but not by lending
    # slots while its state is unknown. The launch routine handles that case.
    if by[PROD]:
        pr, pc = details(PROD)
        assert all(r['Dependency'] == '(null)' for r in pr)
        assert not any(r[1] == 'PENDING' and r[4] == '(JobHeldUser)' for r in by[PROD])
    b_bound = 0
    if B and by[B]:
        br, bc = details(B)
        b_held = all(r[1] == 'PENDING' and r[4] == '(JobHeldUser)' for r in by[B])
        b_bound = 0 if b_held else max(running(B), min(len(by[B]), bc))
    # All surviving large tasks are reserved. Tier 2 may borrow that same
    # reservation only while its explicit afterany dependency excludes overlap.
    large_bound = len(by[LARGE])
    tier_target = min(37, len(by[TIER]))
    if large_bound and by[TIER]:
        tr, tc = details(TIER)
        assert all(r['JobState'] == 'PENDING' and 'afterany:65677_' in r['Dependency']
                   and '(unfulfilled)' in r['Dependency'] for r in tr)
        tier_target = min(tier_target, large_bound)
        change(TIER, max(1, tier_target), 'Guard tier-2 dependency transition before allocating A-9 production slots')
    other_target = max(large_bound, tier_target) if large_bound else tier_target
    a_target = CAP - other_target - b_bound
    assert a_target >= 1
    if by[PROD]:
        change(PROD, a_target, 'A-9 priority under the shared 64 cap; reserve polymer/tier-2 and released solvent capacity')
    a_bound = max(running(PROD), min(len(by[PROD]), a_target))
    if by[TIER] and not large_bound:
        budget = CAP - a_bound - b_bound
        assert budget >= running(TIER) and budget >= 1
        change(TIER, min(tier_target, budget), 'Restore tier-2 capacity only as existing production tasks release slots')
        other_target = min(tier_target, budget)
    # B was submitted held, after A. Release only when A leaves real capacity,
    # accounting for pending as well as already-running A tasks.
    if B and by[B]:
        available = CAP - a_bound - other_target
        if available > 0:
            target = min(4, available)
            assert target >= running(B)
            change(B, target, 'Common-solvent DFT uses capacity left by A; shared cap unchanged')
            if b_held:
                command = ['scontrol', 'release', B]
                call(command)
                readback = call(['scontrol', 'show', 'job', B, '-o'])
                assert 'Reason=JobHeldUser' not in readback
                event(dict(job_id=B, **{'from': 'held', 'to': 'released'},
                           reason='A-9 launched and sufficient shared-cap capacity is reserved',
                           command=command, scheduler_readback=readback))
    after = queue()
    out['running_after'] = dict(collections.Counter(r[0].split('_')[0] for r in after if r[1] != 'PENDING'))
    out['status'] = 'verified'


try:
    main()
except Exception as exc:
    out.update(status='failed', error=repr(exc))
for p in [OLD / 'throttle-latest.json', D / 'throttle-latest.json']:
    p.write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(out))
