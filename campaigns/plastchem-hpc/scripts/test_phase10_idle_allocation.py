"""Check the possible-start cap after every v6 allocation operation."""
import ast
import collections
import json
from pathlib import Path

SOURCE=Path(__file__).with_name('phase9_throttle_remote_v6.py')
fn=next(n for n in ast.parse(SOURCE.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='main')
CODE=compile(ast.Module(body=[fn],type_ignores=[]),str(SOURCE),'exec')


def scenario(large,extension_remaining,extension_running,extension_cap,tier_running,tier_cap,
             production_remaining=0,production_running=0,production_cap=1):
    counts=dict(A=production_remaining,L=large,T=236,E=extension_remaining)
    runs=dict(A=production_running,L=large,T=tier_running,E=extension_running)
    caps=dict(A=production_cap,L=40,T=tier_cap,E=extension_cap);changes=[]
    def allocation(j):return max(runs[j],min(counts[j],caps[j]))
    def bound():return allocation('A')+max(large,allocation('T'))+allocation('E')
    def queue():
        return [[f'{j}_{i}','RUNNING' if i<runs[j] else 'PENDING','1',j,
                 '(Dependency)' if j=='T' and large else '(Resources)'] for j in counts for i in range(counts[j])]
    def details(j):
        return [dict(JobState='RUNNING' if runs[j] else 'PENDING',
                     Dependency='afterany:65677_0(unfulfilled)' if j=='T' and large else '(null)',
                     Reason='Dependency' if j=='T' and large else 'Resources')],caps[j]
    def change(j,target,reason):
        assert 1<=target<=64;caps[j]=target;changes.append((j,target));assert bound()<=64,(changes,bound())
    env=dict(CAP=64,PROD='A',TIER='T',LARGE='L',B=None,N=None,
             RETRIES={'E':{'purpose':'extension_production'}},names={j:j for j in counts},out={},
             queue=queue,details=details,change=change,call=lambda args:'',event=lambda e:None,collections=collections)
    assert bound()<=64
    exec(CODE,env);env['main']()
    assert env['out']['status']=='verified' and bound()<=64
    return dict(cap_after=caps,bound=bound(),changes=changes)


def main():
    cases=[(1,31,27,27,0,1),(0,31,31,31,1,1),(0,27,27,31,33,33),
           (0,31,27,27,37,37),(3,58,27,27,0,3),(0,58,58,58,3,3),
           (0,10,10,58,6,6),(1,31,27,27,0,1,4,4,4)]
    rows=[scenario(*c) for c in cases]
    assert rows[0]['cap_after']['E']==31
    assert rows[1]['cap_after']['T']==33 and rows[1]['bound']==64
    assert rows[2]['cap_after']['T']==37 and rows[2]['bound']==64
    assert rows[3]['cap_after']['E']==27
    assert rows[4]['cap_after']['E']==58 and rows[4]['bound']==61
    assert rows[5]['cap_after']['T']==6 and rows[5]['bound']==64
    assert rows[6]['cap_after']['T']==37
    assert rows[7]['cap_after']['E']==27,'Original production keeps priority'
    print(json.dumps(dict(passed=len(rows),cases=cases,results=rows),indent=2))


if __name__=='__main__':main()
