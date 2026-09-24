"""Adversarial possible-start bounds for original and recovery arrays together."""
import ast
import collections
import json
from pathlib import Path


SOURCE = Path(__file__).with_name('phase9_throttle_remote_v3.py')
fn = next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
CODE = compile(ast.Module(body=[fn], type_ignores=[]), str(SOURCE), 'exec')


def scenario(a, large, tier, b, recovery, held):
    counts = dict(A=a, L=large, T=236, B=69, R=recovery)
    runs = dict(A=a, L=large, T=tier, B=b, R=0 if held else recovery)
    caps = dict(A=max(a, 1), L=40, T=large if large else tier, B=b, R=recovery)
    holding = dict(R=held, B=False)
    changes = []

    def allocation(job):
        return 0 if holding.get(job) else max(runs[job], min(counts[job], caps[job]))

    def bound():
        return allocation('A') + max(large, allocation('T')) + allocation('B') + allocation('R')

    def queue():
        return [[f'{j}_{i}', 'RUNNING' if i < runs[j] else 'PENDING', '1', j,
                 '(JobHeldUser)' if holding.get(j) else '(Resources)']
                for j in counts for i in range(counts[j])]

    def details(job):
        return [dict(Dependency='afterany:65677_0(unfulfilled)' if job == 'T' and large else '(null)',
                     JobState='RUNNING' if runs[job] else 'PENDING',
                     Reason='JobHeldUser' if holding.get(job) else 'Resources')], caps[job]

    def change(job, target, reason):
        caps[job] = target
        changes.append([job, target])
        assert bound() <= 64, (changes, bound())

    def call(args):
        if args[:2] == ['scontrol', 'release']:
            holding[args[2]] = False
            assert bound() <= 64
        return ''

    env = dict(CAP=64, PROD='A', TIER='T', LARGE='L', B='B', N=None,
               RETRIES={'R': {}}, names={j:j for j in counts}, out={},
               queue=queue, details=details, change=change, call=call,
               event=lambda e: changes.append(e), collections=collections)
    assert bound() <= 64
    exec(CODE, env); env['main']()
    return dict(initial=[a, large, tier, b, recovery, held],
                final_bound=bound(), recovery_held=holding['R'], changes=changes)


if __name__ == '__main__':
    cases = [(57,3,0,4,1,True), (56,3,0,4,1,True), (58,3,0,3,1,True),
             (56,3,0,4,1,False), (20,0,37,4,3,False),
             (0,0,37,4,23,True), (0,0,37,4,23,False)]
    results = [scenario(*case) for case in cases]
    assert results[0]['recovery_held'] and results[2]['recovery_held']
    assert not results[1]['recovery_held'] and not results[5]['recovery_held']
    print(json.dumps(dict(passed=len(results), results=results), indent=2))
