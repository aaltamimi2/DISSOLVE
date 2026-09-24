"""Disjoint LLE units; original chunk is verifiably stopped for the entire handoff.

Uses the frozen numerical worker, original plan and canonical checkpoint paths.
Does not recalculate partitioning or modify existing seals. No global completion
footer is written: the original process resumes to validate all checkpoints.
"""
import os
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
import fcntl,json,sys,time
from pathlib import Path
import phase9_worker_cpu as w
import phase9_failure_policy as policy
from phase9_tail_common import sha,save,keyname


def main(assignment_path,group):
    ap=Path(assignment_path);assignment=json.loads(ap.read_text());root=ap.parent;assignment_digest=sha(ap)
    identity=assignment['original_process'];group=int(group)
    assert os.environ['SLURM_CPUS_PER_TASK']=='1' and os.environ['SLURM_NTASKS']=='1'
    lock=(root/f'helper-{group}.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for name,digest in assignment['code_pins'].items():assert sha(w.D/name)==digest,name
    planpath=w.D/'production-plan.json';plan=json.loads(planpath.read_text())
    manifest=json.loads((w.BASE/'manifest.json').read_text());validation=json.loads((w.BASE/'validation-inputs.json').read_text())
    for name,digest in json.loads((w.D/'production-clearance.json').read_text())['file_pins'].items():assert sha(w.D/name)==digest,name
    for name,digest in json.loads((w.BASE/'package-pins.json').read_text()).items():assert sha(w.BASE/name)==digest,name
    assert sha(planpath)==assignment['signature']['plan_sha256']
    units={u['id']:u for u in plan['chunks'][assignment['chunk']]}
    assigned=assignment['groups'][group];assert assignment['retained_unit'] not in assigned
    out=w.D/plan['output']/f"{assignment['chunk']:04d}";signature=assignment['signature']
    execution=dict(job_id=os.environ['SLURM_JOB_ID'],array_task_id=os.environ.get('SLURM_ARRAY_TASK_ID'),node=os.uname().nodename,
                   cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')))
    assert 'EPYC 7763' in execution['cpu_model'] or 'EPYC 9' in execution['cpu_model']
    evidence=dict(entry_sha256=sha(__file__),failure_policy_sha256=sha(policy.__file__),
                  assignment_sha256=assignment_digest,purpose='Disjoint tail helper; original process paused and retained in-flight unit excluded')
    calculate=policy.wrap(w.lle_result);counts={'computed':0,'reused':0};digests={}
    def guard():
        state=json.loads((root/'handoff-state.json').read_text())
        assert state['status']=='paused_helpers_permitted' and state['assignment_sha256']==assignment_digest,state
        assert time.time()-state['heartbeat_epoch']<120,'Original process pause heartbeat stale; preserve checkpoints and stop'
        w.guard()
    start=time.monotonic();cache=w.ProfileCache()
    for uid in assigned:
        unit=units[uid];solute=cache.get(w.D/unit['surface'],unit['surface_sha256'])
        for solvent in manifest['solvents']:
            profile=cache.get(w.BASE/solvent['B'])
            high=next(x for x in validation['lle_units'] if x['solvent']==solvent['name'] and x['regime']=='high')
            for regime,T in [('RT',298.15),('high',high['temperature_K'])]:
                guard();path=out/'lle'/keyname((uid,solvent['name'],regime))
                sig=dict(signature,unit=uid,solute_sha256=unit['surface_sha256'],solvent_sha256=profile['sha256'],temperature_K=T)
                def calc():
                    began=time.monotonic();value=calculate(solute,profile,T,unit['molecular_weight_g_mol'],validation['solvent_identities'][solvent['name']]['molecular_weight_g_mol'],plan['grid_batch'])
                    guard()
                    value.update(unit=uid,inchikey=unit['inchikey'],solvent=solvent['name'],regime=regime,temperature_K=T,
                                 wall_seconds=time.monotonic()-began,execution=execution,signature=sig,recovery_policy=evidence)
                    return value
                value,reused=w.checkpoint(path,sig,calc);counts['reused' if reused else 'computed']+=1
                digests[path.name]=json.loads(path.with_suffix('.json.sha256.json').read_text())['sha256']
        del solute,profile
        import gc;gc.collect()
        save(root/f'helper-{group}-progress.json',dict(execution=execution,counts=counts,finished_unit=uid))
    assert counts['computed']<=assignment['helper_systems'][group]
    save(root/f'helper-{group}-complete.json',dict(assignment_sha256=assignment_digest,execution=execution,
         units=assigned,counts=counts,files=digests,parse_count=cache.parse_count,wall_seconds=time.monotonic()-start))


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
