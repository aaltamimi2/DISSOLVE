"""Failure boundary and checkpoint controls, with no scientific calculations."""
import ast
import copy
import hashlib
import json
import tempfile
from pathlib import Path

import phase9_failure_policy as policy
from audit_phase9_results import check_activity_failure


def main():
    checks = []
    original = {'status': 'single_liquid_phase', 'fixture': object()}
    assert policy.wrap(lambda: original)() is original
    checks.append('Successful result returned unchanged, without copying or recalculation')

    def fail():
        raise ValueError(policy.MESSAGE)

    row = policy.wrap(fail)()
    row['recovery_policy'] = dict(entry_sha256=policy.digest(Path(__file__).with_name('phase9_retry_entry.py')),
                                  failure_policy_sha256=policy.digest(policy.__file__))
    policy.validate_failure(row)
    check_activity_failure(row)
    checks.append('Exact observed error retained with traceback, pinned handler and null predictions/verdicts')
    for error in [ValueError('different numerical error'), MemoryError('memory guard'),
                  OSError('filesystem error'), AssertionError('bad digest')]:
        def other():
            raise error
        try:
            policy.wrap(other)()
        except Exception as exc:
            assert exc is error
        else:
            raise AssertionError('Unexpected error was swallowed')
    checks.append('Unrecognized numerical, memory, filesystem and integrity errors propagate')
    for field, bad in [('solute_mole_fraction_solubility', .5), ('above_15_mol_percent', False),
                       ('grid_checks', [{'grid_intervals': 1000}]), ('failure_policy_sha256', 'untrusted')]:
        altered = copy.deepcopy(row); altered[field] = bad
        try:
            check_activity_failure(altered)
        except AssertionError:
            pass
        else:
            raise AssertionError('Accepted altered failure field: ' + field)
    checks.append('Independent auditor rejects invented predictions, verdicts, grid checks or handler pins')

    # Execute the actual unchanged checkpoint function without importing COSMO.
    source = Path(__file__).with_name('phase9_worker_cpu.py')
    tree = ast.parse(source.read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'checkpoint')
    def save(path, value):
        path.write_text(json.dumps(value))
    env = dict(json=json, sha=policy.digest, save=save)
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(source), 'exec'), env)
    with tempfile.TemporaryDirectory(dir='/mnt/r/plastchem-euler/phase9-v1', prefix='failure-test-') as temp:
        path = Path(temp) / 'system.json'; signature = {'worker_sha256': policy.digest(source)}
        value, reused = env['checkpoint'](path, signature, lambda: row)
        assert not reused and value == row
        before = policy.digest(path)
        def should_not_run():
            raise AssertionError('A sealed checkpoint was recomputed')
        value, reused = env['checkpoint'](path, signature, should_not_run)
        assert reused and value == row and policy.digest(path) == before
    checks.append('Actual frozen checkpoint function seals the failure and reuses it byte-for-byte on restart')
    print(json.dumps(dict(passed=len(checks), checks=checks), indent=2))


if __name__ == '__main__':
    main()
