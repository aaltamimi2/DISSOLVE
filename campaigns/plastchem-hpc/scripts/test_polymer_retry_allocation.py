"""Check two polymer retries cannot consume an unreserved shared-cap slot."""
import ast
import collections
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).with_name('phase9_throttle_remote_v7.py')
TREE = ast.parse(SOURCE.read_text())
FN = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
CODE = compile(ast.Module(body=[FN], type_ignores=[]), str(SOURCE), 'exec')


def scenario(extension, first_running=False):
    counts = dict(A=0, L=0, T=199, E=extension, R1=1, R2=1)
    running = dict(A=0, L=0, T=37, E=extension, R1=int(first_running), R2=0)
    caps = dict(A=1, L=40, T=37, E=max(1, extension), R1=1, R2=1)
    held = dict(R1=not first_running, R2=True)
    events = []

    def allocation(job):
        return 0 if held.get(job) else max(running[job], min(counts[job], caps[job]))

    def bound():
        return sum(allocation(j) for j in counts)

    def queue():
        return [[f'{j}_{i}', 'RUNNING' if i < running[j] else 'PENDING', '1', j,
                 '(JobHeldUser)' if held.get(j) else '(Resources)']
                for j in counts for i in range(counts[j])]

    def details(job):
        return [dict(JobState='RUNNING' if running[job] else 'PENDING', Dependency='(null)',
                     Reason='JobHeldUser' if held.get(job) else 'Resources')], caps[job]

    def change(job, target, reason):
        caps[job] = target
        assert bound() <= 64
        events.append((job, target))

    def call(args):
        if args[:2] == ['scontrol', 'release']:
            held[args[2]] = False
            assert bound() <= 64
        return ''

    env = dict(CAP=64, PROD='A', TIER='T', LARGE='L', B=None, N=None,
               RETRIES={'E': {'purpose': 'extension_production'},
                        'R1': {'purpose': 'polymer_time_limit_recovery'},
                        'R2': {'purpose': 'polymer_time_limit_recovery'}},
               names={j: j for j in counts}, out={}, queue=queue, details=details,
               change=change, call=call, event=lambda e: events.append(e), collections=collections)
    assert bound() <= 64
    exec(CODE, env)
    env['main']()
    assert bound() <= 64 and env['out']['status'] == 'verified'
    return dict(bound=bound(), held=held, events=events)


def main():
    rows = [scenario(0), scenario(26), scenario(27), scenario(26, True)]
    assert rows[0]['bound'] == 39 and not any(rows[0]['held'].values())
    assert rows[1]['bound'] == 64 and not rows[1]['held']['R1'] and rows[1]['held']['R2']
    assert rows[2]['bound'] == 64 and all(rows[2]['held'].values())
    assert rows[3]['bound'] == 64 and rows[3]['held']['R2']
    queue_fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'queue')
    queue_code = compile(ast.Module(body=[queue_fn], type_ignores=[]), str(SOURCE), 'exec')
    for state, reason, allowed in [('PENDING', '(JobHeldUser)', True),
                                   ('RUNNING', 'euler145', False), ('PENDING', '(Resources)', False)]:
        env = dict(CAP=64, names={'123': 'retry'}, POLYMER_UNREADY={'123'}, out={},
                   call=lambda args, line=f'123_0|{state}|1|retry|{reason}\n': line)
        exec(queue_code, env)
        try:
            env['queue']()
        except AssertionError:
            assert not allowed
        else:
            assert allowed
    spec = importlib.util.spec_from_file_location('baseline', SOURCE.with_name('test_phase10_idle_allocation.py'))
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    baseline.CODE = CODE
    baseline.main()
    print(json.dumps(dict(two_retry_cases=rows, registration_guard_cases=3,
                          existing_idle_allocation_cases=8, status='passed'), indent=2))


if __name__ == '__main__':
    main()
