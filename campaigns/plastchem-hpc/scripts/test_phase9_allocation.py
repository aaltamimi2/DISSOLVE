"""Offline boundary tests of the scheduler policy; performs no cluster actions."""
import ast, json, sys
from pathlib import Path

SOURCE = Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name('phase9_throttle_remote.py')
tree = ast.parse(SOURCE.read_text())
function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
code = compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), 'exec')


def scenario(a_count, a_running, large, tier_running, b_count, b_running, b_held, a_cap, tier_cap, b_cap=4):
    caps = {'A': a_cap, 'T': tier_cap, 'L': 40, 'B': b_cap}
    counts = {'A': a_count, 'T': 236, 'L': large, 'B': b_count}
    runs = {'A': a_running, 'T': tier_running, 'L': large, 'B': b_running}
    held = {'B': b_held}
    changes = []
    def bound():
        ab = max(runs['A'], min(counts['A'], caps['A']))
        tb = max(runs['T'], min(counts['T'], caps['T']))
        bb = 0 if held['B'] else max(runs['B'], min(counts['B'], caps['B']))
        return ab + max(large, tb) + bb  # tier/large are dependency-exclusive
    def queue():
        return [[f'{j}_{i}', 'RUNNING' if i < runs[j] else 'PENDING', '1', j,
                 '(JobHeldUser)' if j == 'B' and held['B'] else '(Resources)']
                for j in counts for i in range(counts[j])]
    def details(j):
        return [dict(Dependency='afterany:65677_0(unfulfilled)' if j == 'T' and large else '(null)',
                     JobState='PENDING' if not runs[j] else 'RUNNING', Reason='JobHeldUser' if held.get(j) else 'Resources')], caps[j]
    def change(j, target, reason):
        caps[j] = target; changes.append([j, target]); assert bound() <= 64, (changes, bound())
    def call(args):
        if args[:2] == ['scontrol', 'release']:
            held['B'] = False; assert bound() <= 64
        return ''
    import collections
    env = dict(CAP=64, PROD='A', TIER='T', LARGE='L', B='B' if b_count else None, N=None, RETRIES={},
               names={j: j for j in counts}, out={}, queue=queue, details=details,
               change=change, call=call, event=lambda value: changes.append(value), collections=collections)
    exec(code, env); env['main']()
    return dict(initial=[a_count,a_running,large,tier_running,b_count,b_running,b_held,a_cap,tier_cap],
                final_bound=bound(), caps=caps, changes=changes)


cases = [
    (58,58,6,0,69,0,True,58,6),
    (40,40,6,0,69,0,True,58,6),
    (40,40,0,6,69,4,False,54,6),
    (23,23,0,20,60,4,False,23,20),
    (58,58,0,6,69,0,True,58,6),
    (0,0,0,37,69,0,True,27,37),
    (10,10,6,0,0,0,True,58,6),
]
def nitro_case(earlier, ready, held=True, marker_job='N'):
    import collections
    state={'held':held};operations=[]
    class Marker:
        def __truediv__(self,name):return self
        def exists(self):return ready
        def read_text(self):return json.dumps(dict(job_id=marker_job,original_archives_verified=True,registered_entries=7))
    def queue():
        rows=[['N_'+str(i),'PENDING' if state['held'] else 'RUNNING','1','N','(JobHeldUser)' if state['held'] else '(Resources)'] for i in range(7)]
        if earlier:rows.append(['A_0','RUNNING','1','A','(Resources)'])
        assert sum(r[1]=='RUNNING' for r in rows)<=64
        return rows
    def call(args):
        operations.append(args)
        if args[:2]==['scontrol','release']:state['held']=False
        return ''
    env=dict(CAP=64,PROD='A',TIER='T',LARGE='L',B=None,N='N',RETRIES={},nitro_root=Marker(),
             names={j:j for j in ['A','T','L','N']},out={},queue=queue,
             details=lambda j:([dict(Dependency='(null)',JobState='PENDING' if j=='N' and state['held'] else 'RUNNING')],7 if j=='N' else 64),
             change=lambda *args:None,call=call,event=lambda value:None,collections=collections,json=json)
    exec(code,env)
    error=None
    try:env['main']()
    except AssertionError as exc:error=str(exc)
    return dict(earlier=earlier,ready=ready,held_after=state['held'],error=error,operations=operations)

results = [scenario(*case) for case in cases]
nitro=[nitro_case(True,True),nitro_case(False,False),nitro_case(False,True),nitro_case(True,True,held=False),nitro_case(False,True,marker_job='wrong')]
assert nitro[0]['held_after'] and not nitro[0]['error']
assert nitro[1]['held_after'] and not nitro[1]['error']
assert not nitro[2]['held_after'] and not nitro[2]['error']
assert nitro[3]['error'] is not None and nitro[4]['error'] is not None
report = dict(status='passed', cases=len(results)+len(nitro), scope='Mock scheduler transitions; not a live scheduling measurement', results=results,nitro_last_priority_cases=nitro)
print(json.dumps(report, indent=2))
