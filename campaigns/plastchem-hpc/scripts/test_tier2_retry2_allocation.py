"""Check a held tier-2 recovery never borrows a still-running slot."""
import ast
import collections
import importlib.util
import json
from pathlib import Path

SOURCE = Path(__file__).with_name('phase9_throttle_remote_v10.py')
TREE = ast.parse(SOURCE.read_text())
FN = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
CODE = compile(ast.Module(body=[FN], type_ignores=[]), str(SOURCE), 'exec')

def scenario(tier_running, released=False):
    counts = dict(A=0, L=0, T=170, P1=1, P2=1, R=1)
    running = dict(A=0, L=0, T=tier_running, P1=1, P2=1, R=int(released))
    caps = dict(A=1, L=40, T=61 if released else 62, P1=1, P2=1, R=1)
    held = dict(R=not released)
    events = []
    def bound():
        return sum(0 if held.get(j) else max(running[j], min(counts[j], caps[j])) for j in counts)
    def queue():
        return [[f'{j}_{i}', 'RUNNING' if i < running[j] else 'PENDING', '1', j,
                 '(JobHeldUser)' if held.get(j) else '(Resources)'] for j in counts for i in range(counts[j])]
    def details(j):
        return [dict(JobState='RUNNING' if running[j] else 'PENDING', Dependency='(null)',
                     Reason='JobHeldUser' if held.get(j) else 'Resources')], caps[j]
    def change(j, target, reason):
        caps[j] = target
        assert bound() <= 64
        events.append((j, target))
    def call(args):
        if args[:2] == ['scontrol', 'release']:
            held[args[2]] = False
            assert bound() <= 64
        return ''
    env = dict(CAP=64, PROD='A', TIER='T', LARGE='L', B=None, N=None,
        RETRIES={'P1': {'purpose': 'polymer_time_limit_recovery'},
                 'P2': {'purpose': 'polymer_time_limit_recovery'},
                 'R': {'purpose': 'tier2_time_limit_recovery'}},
        names={j: j for j in counts}, out={}, queue=queue, details=details,
        change=change, call=call, event=lambda e: events.append(e), collections=collections)
    assert bound() <= 64
    exec(CODE, env)
    env['main']()
    assert bound() <= 64 and caps['T'] == 61 and env['out']['status'] == 'verified'
    assert held['R'] == (tier_running == 62)
    return dict(tier_running=tier_running, initially_released=released,
                final_tier_cap=caps['T'], retry_held=held['R'], potential_running_bound=bound())

if __name__ == '__main__':
    rows = [scenario(62), scenario(61), scenario(60), scenario(61, True)]
    spec = importlib.util.spec_from_file_location('baseline', SOURCE.with_name('test_tier2_idle_allocation.py'))
    baseline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(baseline)
    baseline.SOURCE, baseline.TREE, baseline.CODE = SOURCE, TREE, CODE
    baseline.main()
    print(json.dumps(dict(status='passed', recovery_transition_cases=rows), indent=2))
