"""Offline coverage for multiple isolated, independently registered retries."""
import ast
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

SOURCE = Path(__file__).with_name('phase9_throttle_remote_v10.py')
tree = ast.parse(SOURCE.read_text())
stop = next(i for i, node in enumerate(tree.body)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == 'out' for t in node.targets))
code = compile(ast.Module(body=tree.body[:stop], type_ignores=[]), str(SOURCE), 'exec')


def case(ready, bad_job=False, bad_name=False):
    with tempfile.TemporaryDirectory(dir='/mnt/r/plastchem-euler/tier2-v1', prefix='registry-control-') as temp:
        home = Path(temp)
        root = home / 'plastchem-euler'
        (root / 'phase83-v1').mkdir(parents=True)
        (root / 'phase9-v1').mkdir()
        (root / 'phase9-v1/production-submission.json').write_text('{"job_id":"100"}')
        for index, job in [(1, '101'), (2, '102')]:
            path = root / f'tier2-v1/time-limit-retry{index}'
            path.mkdir(parents=True)
            receipt = dict(job_id=job, group='tier2', purpose='tier2_time_limit_recovery',
                           name=f'contam-tier2-timelimit-retry{index}')
            if index == 2 and bad_name:
                receipt['name'] = 'unrecognized-name'
            (path / 'tier2-submission.json').write_text(json.dumps(receipt))
            if index == 1 or ready:
                marker = dict(original_archives_verified=True, registered_entries=1,
                              jobs={'tier2': '999' if index == 2 and bad_job else job})
                (path / 'ready-for-release.json').write_text(json.dumps(marker))
        env = {}
        rejected = False
        try:
            with patch('pathlib.Path.home', return_value=home):
                exec(code, env)
        except AssertionError:
            rejected = True
        finally:
            if 'lock' in env:
                env['lock'].close()
        assert rejected == (bad_job or bad_name)
        if not rejected:
            assert env['names']['101'] == 'contam-tier2-timelimit-retry1'
            assert env['names']['102'] == 'contam-tier2-timelimit-retry2'
            assert set(env['RETRIES']) == ({'101', '102'} if ready else {'101'})
            assert env['POLYMER_UNREADY'] == (set() if ready else {'102'})
        return dict(ready=ready, bad_job=bad_job, bad_name=bad_name, rejected=rejected)


if __name__ == '__main__':
    cases = [case(False), case(True), case(True, bad_job=True), case(True, bad_name=True)]
    print(json.dumps(dict(status='passed', cases=cases,
                         controller_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest())))
