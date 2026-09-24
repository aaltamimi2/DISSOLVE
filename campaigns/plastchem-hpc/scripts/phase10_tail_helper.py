"""Disjoint RT LLE helper; existing original chunk stays verifiably paused.

Calls exactly the frozen worker's guarded solver and canonical checkpoint API.
The original resumes to validate all seals and write its completion footer.
"""
import os
for name in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[name]='1'
import fcntl
import json
import sys
import time
from pathlib import Path

import phase10_worker_v2 as w
from phase10_tail_common import sha, save, keyname


def main(assignment_path,group):
    ap=Path(assignment_path);a=json.loads(ap.read_text());root=ap.parent;digest=sha(ap);group=int(group)
    assert os.environ['SLURM_JOB_PARTITION']=='research'
    assert all(os.environ[k]=='1' for k in ['SLURM_CPUS_PER_TASK','SLURM_NTASKS','SLURM_JOB_NUM_NODES'])
    lock=(root/f'helper-{group}.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    for name,pin in a['code_pins'].items():assert sha(w.D/name)==pin,name
    plan=json.loads((w.D/'production-plan.json').read_text());assert sha(w.D/'production-plan.json')==a['signature']['plan_sha256']
    manifest=json.loads((w.D/'manifest.json').read_text());assert sha(w.D/'manifest.json')==a['signature']['manifest_sha256']
    assert sha(w.D/plan['worker_pins_file'])==a['signature']['worker_pins_sha256']
    for name,pin in json.loads((w.D/plan['worker_pins_file']).read_text()).items():assert sha(w.D/name)==pin
    assert sha(w.BASE/'package-pins.json')==a['signature']['package_pins_sha256']
    for name,pin in json.loads((w.BASE/'package-pins.json').read_text()).items():assert sha(w.BASE/name)==pin
    units={u['id']:u for u in plan['chunks'][a['chunk']]};assigned=a['groups'][group]
    assert a['retained_unit'] not in assigned
    out=w.D/plan['output']/f"{a['chunk']:04d}"
    execution=dict(job_id=os.environ['SLURM_JOB_ID'],array_job_id=os.environ.get('SLURM_ARRAY_JOB_ID'),
        array_task_id=os.environ.get('SLURM_ARRAY_TASK_ID'),node=os.uname().nodename,
        cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')))
    assert 'EPYC 7763' in execution['cpu_model'] or 'EPYC 9' in execution['cpu_model']
    evidence=dict(assignment_sha256=digest,helper_sha256=sha(__file__),group=group,
        purpose='Disjoint RT-only tail helper; original paused; retained in-flight unit excluded')
    def guard():
        state=json.loads((root/'handoff-state.json').read_text())
        assert state['status']=='paused_helpers_permitted' and state['assignment_sha256']==digest
        assert time.time()-state['heartbeat_epoch']<120,'Pause heartbeat stale; stop before any write'
        w.guard()
    start=time.monotonic();cache=w.ProfileCache();counts={'computed':0,'reused':0};digests={}
    for uid in assigned:
        u=units[uid];solute=cache.get(w.D/u['surface'],u['surface_sha256'])
        for s in manifest['solvents']:
            guard();profile=cache.get(w.D/s['surface'],s['surface_sha256'])
            path=out/'lle'/keyname((uid,s['name'],'RT'))
            sig=dict(a['signature'],unit=uid,solute_sha256=u['surface_sha256'],solvent_sha256=s['surface_sha256'],temperature_K=298.15)
            def calculate():
                began=time.monotonic()
                value=w.safe_lle(solute,profile,298.15,u['molecular_weight_g_mol'],s['molecular_weight_g_mol'],plan['grid_batch'])
                guard()
                if value['status']=='activity_nonconvergence':w.validate_failure(value)
                value.update(unit=uid,inchikey=u['inchikey'],solvent=s['name'],regime='RT',temperature_K=298.15,
                    wall_seconds=time.monotonic()-began,execution=execution,signature=sig,tail_helper=evidence,
                    value_validated=value['status'] in ['single_liquid_phase','two_liquid_phases'])
                return value
            value,reused=w.checkpoint(path,sig,calculate);counts['reused' if reused else 'computed']+=1
            digests[path.name]=json.loads(path.with_suffix('.json.sha256.json').read_text())['sha256']
        save(root/f'helper-{group}-progress.json',dict(execution=execution,counts=counts,finished_unit=uid))
    assert counts['computed']<=a['helper_systems'][group]
    save(root/f'helper-{group}-complete.json',dict(assignment_sha256=digest,execution=execution,units=assigned,
        counts=counts,files=digests,parse_count=cache.parse_count,wall_seconds=time.monotonic()-start))


if __name__=='__main__':main(sys.argv[1],sys.argv[2])
