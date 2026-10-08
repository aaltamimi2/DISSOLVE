"""Collect only the registered tier-2 time-limit recovery; preserve original attempts."""
import os
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
import datetime
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time

from bounded_geometry_identity import verify
from euler_transport import run

R = Path(__file__).resolve().parents[1]
P = R / 'state/tier2-v1'
D = Path('/mnt/r/plastchem-euler/tier2-v1/time-limit-retry2')
S = P / 'time-limit-retry2'
S.mkdir(exist_ok=True)
lock = (S / 'collector.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
receipts = json.loads((D / 'submission.json').read_text())['receipts']
jobs = {g: r['job_id'] for g, r in receipts.items()}
assert set(jobs) == {'tier2'} and len(set(jobs.values())) == 1
models = {g: json.loads((D / 'staging' / g / 'manifest.json').read_text())['molecules'][0] for g in jobs}
config = json.loads((S / 'active-retries.json').read_text())
assert config['jobs'] == jobs
done_path = S / 'retrieved.json'
done = json.loads(done_path.read_text()) if done_path.exists() else {}
destination = D / 'results'
destination.mkdir(exist_ok=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


write(S / 'collector-process.json', dict(pid=os.getpid(), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                                        script_sha256=sha(Path(__file__)), jobs=jobs))
while len(done) < 1:
    try:
        code = """from pathlib import Path
import json,subprocess,datetime
R=Path.home()/'plastchem-euler/tier2-v1/time-limit-retry2'
print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
records={p.parent.name:json.loads(p.read_text()) for p in (R/'runs').glob('*/result.json')},
sacct=subprocess.check_output(['sacct','-j',JOBS,'-nP','--units=K','--format=JobID,State,ElapsedRaw,MaxRSS,NodeList,ExitCode'],text=True))))
""".replace('JOBS', repr(','.join(jobs.values())))
        response = run('ssh', ['euler', 'python3 -'], input=code, capture_output=True, text=True, timeout=90)
        assert response.returncode == 0, response.stderr
        snapshot = json.loads(response.stdout)
        write(S / 'latest-snapshot.json', snapshot)
        account = {v[0]: v for line in snapshot['sacct'].splitlines() if len(v := line.split('|')) >= 6}
        for group, molecule in models.items():
            key = molecule['inchikey']
            if key in done:
                continue
            task = jobs[group] + '_0'
            a = account.get(task)
            terminal = bool(a and a[1].split()[0] in ['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED', 'OUT_OF_MEMORY', 'NODE_FAIL', 'PREEMPTED'])
            original = config['entries'][key]
            r = snapshot['records'].get(key)
            if not r and not terminal:
                continue
            r = dict(r or dict(input=molecule, inchikey=key, status='failed',
                              failure_mode='completed_without_result' if a[1] == 'COMPLETED' else 'slurm_' + a[1].lower()))
            r.update(active_attempt='time-limit-retry2', previous_attempt=original,
                     restart_provenance=molecule['restart_provenance'])
            if a:
                r['slurm_accounting'] = dict(state=a[1], elapsed_seconds=int(a[2]), node_list=a[4], exit_code=a[5])
                batch = account.get(task + '.batch')
                if batch and batch[3]:
                    r['slurm_accounting']['maxrss_kib'] = float(batch[3].rstrip('K'))
                    r['slurm_accounting']['maxrss_source'] = 'sacct batch step'
            if (a and a[1] == 'TIMEOUT') or r.get('failure_mode') in ['scheduler_signal_10', 'walltime_censored']:
                r.update(status='failed', execution_outcome='time_limit', retry_required=True)
                if a and a[1] == 'TIMEOUT':
                    r['failure_mode'] = 'slurm_timeout'
            if terminal:
                if r['status'] not in ['failed', 'converged_identity_pending']:
                    r.update(status='failed', failure_mode='terminal_without_normal_result')
                target = destination / key
                if key in snapshot['records']:
                    fetched = run('scp', ['-rq', 'euler:plastchem-euler/tier2-v1/time-limit-retry2/returns/' + key, str(destination)],
                                  capture_output=True, text=True, timeout=240)
                    assert fetched.returncode == 0, fetched.stderr
                target.mkdir(exist_ok=True)
                if r['status'] == 'converged_identity_pending':
                    r['dft_status'] = 'converged'
                    try:
                        assert r['cpu_model'] == 'AMD EPYC 7763 64-Core Processor'
                        assert r['runner_sha256'] == sha(D / 'staging/tier2_runner.py')
                        assert r['manifest_sha256'] == receipts[group]['manifest_sha256']
                        assert sha(target / 'surface.orcacosmo') == r['surface_sha256']
                        for stage, info in r['stages'].items():
                            assert sha(target / (stage + '.inp')) == info['input_sha256']
                        identity = verify(target / 'optimized.xyz', molecule['inchikey'], seconds=30)
                        assert identity['identity_verified'], identity
                        r.update(identity, status='converged', dft_status='converged', archive_path=str(target),
                                 identity_authority=json.loads((P / 'policy.json').read_text()))
                    except Exception as exc:
                        r.update(status='failed', failure_mode='return_integrity_or_connectivity', error=str(exc))
                write(target / 'result.json', r)
                r['returned_bytes'] = sum(p.stat().st_size for p in target.iterdir() if p.is_file())
                write(P / 'records' / (key + '.json'), r)
                done[key] = dict(status=r['status'], record_sha256=sha(target / 'result.json'), task=task)
                write(done_path, done)
            else:
                write(P / 'records' / (key + '.json'), r)
        subprocess.run([os.sys.executable, str(R / 'scripts/summarize_tier2.py')], check=True, stdout=subprocess.DEVNULL)
        print(json.dumps(dict(utc=snapshot['utc'], terminal=len(done), denominator=1, jobs=jobs)), flush=True)
    except Exception as exc:
        print(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), error=repr(exc))), flush=True)
    if len(done) < 1:
        time.sleep(300)
