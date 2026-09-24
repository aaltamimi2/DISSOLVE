"""Offline synthetic scheduler tests; never connects to Euler or runs science."""
import contextlib, gc, hashlib, io, json, runpy, subprocess, tempfile
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('submit_phase9_production_remote.py').resolve()


def exercise(root):
    D = root/'plastchem-euler/phase9-v1'; D.mkdir(parents=True)
    (D.parent/'phase83-v1').mkdir()
    def put(name, data):
        p=D/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data));return p
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    gate=dict(status='reproduction_passed_cost_pending',errors=[],lle_compared=2240,lle_denominator=2240,
              partition_numerical_passes=17733,documented_finite_dilution_reference_differences=59)
    cost=dict(cost_gate_passes=True,planning_cpu_hours_including_diagnostics=400,lle_systems=6400,lle_denominator=6400,
              production_sized_calibration_comparison={'status':'passed'})
    put('gate-comparison-final.json',gate);put('chunk-cost-final.json',cost)
    probe=put('chunk-probe-results-v1/0000/complete.json',dict(unit_ids=[f'cohort-{i:05d}' for i in range(100)]))
    pp=put('chunk-probe-plan.json',{'synthetic':True})
    units=[dict(id=f'cohort-{i:05d}') for i in range(100,5830)]
    put('production-plan.json',dict(chunks=[units[i:i+100] for i in range(0,len(units),100)],
        reuse_completed_chunk_probe_ids=[f'cohort-{i:05d}' for i in range(100)],complete_chunk_probe_plan_sha256=sha(pp)))
    def clearance():
        put('production-clearance.json',dict(status='passed',limit_cpu_hours=500,
          file_pins={name:sha(D/name) for name in ['gate-comparison-final.json','chunk-cost-final.json','production-plan.json']},
          probe_complete_sha256=sha(probe),entry_resume_test={'status':'completed_chunk_verified_and_skipped'}))
    clearance()
    state=dict(old=True,tiercap=37,production=False,held=False,submissions=0,cancellations=0)
    commands=[]
    def q_prod():
        return ''.join(f"99000_{i}|{'PENDING|(JobHeldUser)' if state['held'] else 'RUNNING|node'}\n" for i in range(58)) if state['production'] else ''
    def queue():
        rows=[]
        if state['old']:rows += [f'68234_{i}|PENDING|1|contam-phase83-production-v1|(JobHeldUser)' for i in range(100)]
        rows += [f'65677_{i}|RUNNING|1|contam-polymer24a-large-v1|node' for i in range(6)]
        rows += [f'63873_{i}|PENDING|1|contam-tier2-chno500700-v1|(Dependency)' for i in range(236)]
        if state['production']:rows += [f"99000_{i}|{'PENDING' if state['held'] else 'RUNNING'}|1|contam-phase9-production-v1|{('(JobHeldUser)' if state['held'] else 'node')}" for i in range(58)]
        return '\n'.join(rows)+'\n'
    def run(args,**kw):
        commands.append(args);out=''
        if args[0]=='squeue':
            out=q_prod() if '--name=contam-phase9-production-v1' in args or '-j' in args else queue()
        elif args[0]=='sacct':out='99000_0|contam-phase9-production-v1|RUNNING|1|node\n' if state['production'] else ''
        elif args[:3]==['scontrol','show','job']:
            if args[3]=='63873':out=f"JobState=PENDING Dependency=afterany:65677_0(unfulfilled) ArrayTaskThrottle={state['tiercap']}\n"
            else:out=f"JobState={'PENDING' if state['held'] else 'RUNNING'} Reason={'JobHeldUser' if state['held'] else 'None'} ArrayTaskThrottle=58\n"
        elif args[:2]==['scontrol','update']:
            assert args[2]=='JobId=63873';state['tiercap']=int(args[3].split('=')[1])
        elif args[0]=='scancel':
            assert args==['scancel','--state=PENDING','68234'];state['old']=False;state['cancellations']+=1
        elif args[0]=='sbatch':
            assert '--hold' in args and '--array=0-57%58' in args and '--mem=4G' in args
            state.update(production=True,held=True);state['submissions']+=1;out='99000\n'
        elif args[:2]==['scontrol','release']:
            assert args[2]=='99000' and state['tiercap']==6;state['held']=False
        else:raise AssertionError(args)
        return subprocess.CompletedProcess(args,0,out,'')
    def invoke():
        with patch.object(Path,'home',return_value=root),patch('subprocess.run',side_effect=run),patch('fcntl.flock'),contextlib.redirect_stdout(io.StringIO()):
            result=runpy.run_path(str(SCRIPT),run_name='__main__')
            result['lock'].close()
            del result
            gc.collect()
    invoke();receipt=json.loads((D/'production-submission.json').read_text())
    assert receipt['running_after']==64 and receipt['launch_utc'] and state['submissions']==1
    invoke();assert state['submissions']==1 and state['cancellations']==1
    for mutation in ['cost','partition','LLE']:
        g=dict(gate);c=dict(cost)
        if mutation=='cost':c['cost_gate_passes']=False;c['planning_cpu_hours_including_diagnostics']=501
        elif mutation=='partition':g['partition_numerical_passes']=17732
        else:g['lle_compared']=2239
        put('gate-comparison-final.json',g);put('chunk-cost-final.json',c);clearance()
        before=len(commands)
        try:invoke()
        except AssertionError:pass
        else:raise AssertionError('Invalid evidence was accepted: '+mutation)
        assert len(commands)==before, 'Invalid gate must fail before any scheduler access'
    return dict(status='passed',scope='Synthetic offline scheduler; no real submission or scientific data',
                checks=['58-slot launch reserves six before release','only pending obsolete tasks cancelled',
                        'lost-confirmation reconciliation does not resubmit','cost above 500 stops before scheduler access',
                        'incomplete partition evidence stops','incomplete LLE evidence stops'])


with tempfile.TemporaryDirectory(prefix='launch-test-',dir=str(SCRIPT.parents[1]/'state/phase9-v1')) as tmp:
    print(json.dumps(exercise(Path(tmp)),indent=2))
