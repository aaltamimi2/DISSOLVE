"""Queue the 69 common-solvent DFT tasks only after A's confirmed launch.

Submitted held so the shared-cap controller can safely allocate spare capacity.
All licensed-derived inputs stay in the Euler lane and untracked bulk storage.
"""
import datetime, fcntl, hashlib, json, re, subprocess
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase9-solvent-library-v1'
A = D.parent / 'phase9-v1'
lock = (D.parent / 'phase83-v1/throttle.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX)
name = 'contam-phase9-common-solvents-v1'
r = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), name=name, operations=[])


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    r['operations'].append(dict(command=args, returncode=p.returncode, stdout=p.stdout, stderr=p.stderr))
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


launch = json.loads((A / 'production-submission.json').read_text())
assert launch['launch_utc'], 'A has not launched'
assert 'Reason=JobHeldUser' not in launch['scheduler_readback']
r['A_launch_utc'] = launch['launch_utc']; r['A_job_id'] = launch['job_id']
manifest = json.loads((D / 'solvent_library/manifest.json').read_text())
assert len(manifest['molecules']) == manifest['denominator'] == 69
assert len({m['inchikey'] for m in manifest['molecules']}) == 69
for path, digest in json.loads((D / 'staging-pins.json').read_text()).items():
    assert hashlib.sha256((D / path).read_bytes()).hexdigest() == digest, path
for mol in manifest['molecules']:
    prep = json.loads((D / 'prepared' / mol['inchikey'] / 'preparation.json').read_text())
    assert prep['status'] == 'prepared'
    assert hashlib.sha256((D / 'prepared' / mol['inchikey'] / 'input.xyz').read_bytes()).hexdigest() == prep['xyz_sha256']
q = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '--name=' + name, '-o', '%i|%T|%R'])
a = call(['sacct', '-nP', '-u', 'aaltamimi2', '--starttime=2026-09-23', '--name=' + name,
          '--format=JobID,JobName%60,State,Elapsed,NodeList'])
r.update(squeue_reconciliation=q, sacct_reconciliation=a)
ids = {s.split('|')[0].split('_')[0].split('.')[0] for s in (q + '\n' + a).splitlines() if re.match(r'^\d', s)}
if ids:
    assert len(ids) == 1
    r.update(job_id=next(iter(ids)), decision='existing_no_resubmission')
else:
    assert not (D / 'submission-unconfirmed.json').exists(), 'Unconfirmed attempt requires reconciliation'
    cmd = ['sbatch', '--parsable', '--hold', '--job-name=' + name, '--partition=research',
           '--constraint=milan&cpu', '--exclude=euler09,euler10', '--nodes=1', '--ntasks=1',
           '--cpus-per-task=1', '--mem=4G', '--time=08:00:00', '--no-requeue', '--signal=B:USR1@120',
           '--array=0-68%4', '--chdir=' + str(D), '--output=' + str(D / 'logs/%A_%a.out'),
           '--error=' + str(D / 'logs/%A_%a.err'),
           '--wrap=source "$HOME/plastchem-euler/orca-env.sh"; exec python3 phase9_solvent_runner.py solvent_library']
    # --wrap uses /bin/sh on some clusters; use POSIX dot for the existing env.
    cmd[-1] = cmd[-1].replace('source ', '. ', 1)
    (D / 'submission-unconfirmed.json').write_text(json.dumps(dict(r, command=cmd), indent=2) + '\n')
    job = call(cmd).strip().split(';')[0]; assert job.isdigit()
    r.update(job_id=job, decision='submitted_held_for_A_priority', submitted_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), command=cmd)
r['scheduler_readback'] = call(['scontrol', 'show', 'job', r['job_id'], '-o'])
temp = D / 'submission.tmp'; temp.write_text(json.dumps(r, indent=2) + '\n'); temp.replace(D / 'submission.json')
print(json.dumps(r))
