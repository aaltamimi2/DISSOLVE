"""Offline scheduler controls: exact retries, lost acknowledgments and no duplicates."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import patch

SOURCE = Path(__file__).with_name('submit_tier2_time_limit_retry_remote.py')
STAGE = Path('/mnt/r/plastchem-euler/tier2-v1/time-limit-retry1/staging')


def scenario(lost=False, hidden=False, running=0):
    with tempfile.TemporaryDirectory(dir=STAGE.parent, prefix='submission-control-') as temp:
        home = Path(temp)
        root = home / 'plastchem-euler'
        dest = root / 'tier2-v1/time-limit-retry1'
        dest.mkdir(parents=True)
        for source in STAGE.rglob('*'):
            target = dest / source.relative_to(STAGE)
            if source.is_dir():
                target.mkdir(exist_ok=True)
            elif source.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
        shutil.copyfile(STAGE.parent / 'preparation-summary.json', dest / 'preparation-summary.json')
        (root / 'phase83-v1').mkdir()
        (root / 'phase9-v1').mkdir()
        controller = root / 'phase9-v1/phase9_throttle_remote_v9.py'
        controller.write_bytes(SOURCE.with_name(controller.name).read_bytes())
        (controller.parent / 'active-throttle-controller.json').write_text(json.dumps(dict(
            filename=controller.name, sha256=hashlib.sha256(controller.read_bytes()).hexdigest())))
        jobs = {}
        submitted = []
        lose_once = lost

        def fake(args, **kwargs):
            nonlocal lose_once
            output = ''
            if args[:4] == ['scontrol', 'show', 'partition', 'research']:
                output = 'PartitionName=research MaxTime=16-16:00:00'
            elif args[:3] == ['scontrol', 'show', 'job']:
                output = f'JobId={args[3]} JobState=PENDING Reason=JobHeldUser ArrayTaskThrottle=1'
            elif args[0] in ['squeue', 'sacct'] and any(x.startswith('--name=') for x in args):
                name = next(x.split('=', 1)[1] for x in args if x.startswith('--name='))
                if name in jobs and not hidden:
                    output = f'{jobs[name]}|{name}|PENDING\n'
            elif args[0] == 'sacct' and '-j' in args:
                task = args[args.index('-j') + 1]
                output = f'{task}|FAILED\n'
            elif args[0] == 'squeue':
                output = ''.join(f'900_{i}|RUNNING|1|tier2\n' for i in range(running))
                output += ''.join(f'{job}_0|PENDING|1|{name}\n' for name, job in jobs.items())
            elif args[0] == 'sbatch':
                assert '--hold' in args and '--array=0%1' in args
                assert '--cpus-per-task=1' in args and '--mem=4G' in args
                assert '--constraint=milan&cpu' in args and '--exclude=euler09,euler10' in args
                group = Path(args[-1]).stem
                name = 'contam-tier2-timelimit-retry1'
                assert name not in jobs, 'Duplicate submission'
                jobs[name] = str(73000 + len(jobs))
                submitted.append(name)
                output = jobs[name] + '\n'
                if lose_once:
                    lose_once = False
                    raise subprocess.TimeoutExpired(args, 1)
            else:
                raise AssertionError(('Unexpected scheduler operation', args))
            return subprocess.CompletedProcess(args, 0, output, '')

        def invoke():
            env = dict(__name__='offline_control', __file__=str(SOURCE))
            try:
                with patch('pathlib.Path.home', return_value=home), patch('subprocess.run', side_effect=fake), patch('builtins.print'):
                    exec(compile(SOURCE.read_text(), str(SOURCE), 'exec'), env)
            finally:
                if 'lock' in env:
                    env['lock'].close()

        if running > 64:
            try:
                invoke()
            except AssertionError:
                assert not submitted
            else:
                raise AssertionError('Over-cap queue not rejected')
        elif lost:
            try:
                invoke()
            except subprocess.TimeoutExpired:
                assert len(submitted) == 1
            else:
                raise AssertionError('Expected lost acknowledgement')
            if hidden:
                try:
                    invoke()
                except AssertionError as exc:
                    assert 'Unconfirmed previous attempt' in str(exc)
                    assert len(submitted) == 1
                else:
                    raise AssertionError('Blind resubmission permitted')
            else:
                invoke()
                assert len(submitted) == 1
        else:
            invoke()
            invoke()
            assert len(submitted) == 1
        return dict(lost=lost, hidden=hidden, running=running, submissions=len(submitted))


if __name__ == '__main__':
    results = [scenario(), scenario(lost=True), scenario(lost=True, hidden=True), scenario(running=65)]
    print(json.dumps(dict(status='passed', cases=results), indent=2))
