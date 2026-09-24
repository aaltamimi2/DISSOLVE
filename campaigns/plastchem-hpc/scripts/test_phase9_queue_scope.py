"""Only the charter-excluded job 50511 is outside this allocator's control."""
import ast
import json, sys
from pathlib import Path

source=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).with_name('phase9_throttle_remote_v2.py')
tree=ast.parse(source.read_text())
queue=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='queue')
code=compile(ast.Module(body=[queue],type_ignores=[]),str(source),'exec')


def check(external,accepted):
    rows=[f'68503_{i}|RUNNING|1|contam-phase9-production-v1|euler144' for i in range(58)]
    rows += [f'65677_{i}|RUNNING|1|contam-polymer24a-large-v1|euler142' for i in range(3)]
    rows += [f'68561_{i}|RUNNING|1|contam-phase9-common-solvents-v1|euler144' for i in range(3)]
    env=dict(CAP=64,names={'68503':'contam-phase9-production-v1','65677':'contam-polymer24a-large-v1',
             '68561':'contam-phase9-common-solvents-v1'},out={},call=lambda args:'\n'.join(rows+[external]))
    exec(code,env)
    try:
        result=env['queue']()
    except AssertionError:
        assert not accepted
        return dict(external=external,status='correctly_rejected')
    assert accepted and len(result)==64 and len(env['out']['outside_lane_jobs'])==1
    return dict(external=external,status='excluded_without_control',campaign_running=len(result),
                outside_running_cpus=env['out']['outside_lane_running_cpus'])


if __name__=='__main__':
    cases=[('50511|PENDING|8|slab125-mixed-50ns|(BeginTime)',True),
           ('50511|RUNNING|8|slab125-mixed-50ns|euler10',True),
           ('50511|RUNNING|8|unexpected-name|euler10',False),
           ('99999|RUNNING|1|unexpected|euler144',False)]
    print(json.dumps(dict(passed=4,results=[check(*case) for case in cases]),indent=2))
