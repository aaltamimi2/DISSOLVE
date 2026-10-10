"""Per-system supplemental LLE checkpoints using the unchanged A-9 solver."""
import os
for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS'):
    os.environ[name]='1'
import argparse
import datetime
import fcntl
import gc
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--plan',required=True,type=Path)
    ap.add_argument('--local-reproduction',action='store_true')
    args=ap.parse_args();plan=json.loads(args.plan.read_text())
    assert sha(plan['inputs'])==plan['inputs_sha256']
    spec=json.loads(Path(plan['inputs']).read_text())
    assert sha(__file__)==plan['code_pins'][str(Path(__file__).resolve())]
    for path,digest in plan['code_pins'].items():assert sha(path)==digest
    root=Path(spec['package_root'])
    for name,digest in spec['package_pins'].items():assert sha(root/name)==digest
    sys.path.insert(0,str(root))
    from phase9_profiles import ProfileCache
    from phase9_worker_cpu import checkpoint, lle_result, save
    from phase9_failure_policy import wrap, validate_failure
    from supplement_partition_worker import guard
    import opencosmorspy
    assert Path(opencosmorspy.__file__).resolve()==(root/'opencosmorspy/__init__.py').resolve()
    local=args.local_reproduction
    if local:
        assert plan['purpose']=='reproduction_only_not_production' and 0<len(plan['systems'])<=8
    else:
        assert os.environ.get('SLURM_JOB_ID') and os.environ['SLURM_JOB_PARTITION']=='research'
        assert all(int(os.environ[k])==1 for k in ('SLURM_JOB_NUM_NODES','SLURM_NTASKS','SLURM_CPUS_PER_TASK'))
    model=next(x.split(':',1)[1].strip() for x in Path('/proc/cpuinfo').read_text().splitlines() if x.startswith('model name'))
    if not local:assert 'EPYC 7763' in model or 'EPYC 9' in model
    out=Path(plan['output']);assert out.resolve().is_relative_to(Path('/mnt/r/plastchem-euler')) or not local
    out.mkdir(parents=True,exist_ok=True);lock=(out/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signature=dict(plan_sha256=sha(args.plan),inputs_sha256=sha(plan['inputs']),code_pins=plan['code_pins'])
    expected={out/'systems'/(s['inchikey']+'__'+s['solvent']+'__'+s['regime']+'.json') for s in plan['systems']}
    assert len(expected)==len(plan['systems'])
    if (out/'complete.json').exists():
        complete=json.loads((out/'complete.json').read_text());assert complete['signature']==signature
        assert {p.with_name(p.name.removesuffix('.sha256.json')) for p in (out/'systems').glob('*.sha256.json')}==expected
        for p in expected:
            seal=json.loads(p.with_suffix('.json.sha256.json').read_text());assert sha(p)==seal['sha256']
            assert all(seal['signature'].get(k)==v for k,v in signature.items())
        print(json.dumps(dict(status='complete_verified_no_rerun',original_execution=complete['execution'])),flush=True);return
    execution=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),cpu_model=model,
        node=os.uname().nodename,job_id=os.environ.get('SLURM_JOB_ID'),local_reproduction=local)
    cache=ProfileCache();safe=wrap(lle_result);start=time.monotonic();statuses={}
    # Lower the existing solver's periodic guard without changing its numerical path.
    import phase9_worker_cpu
    phase9_worker_cpu.guard=lambda:guard(local)
    for system in plan['systems']:
        key=system['inchikey'];name=system['solvent'];regime=system['regime']
        u=spec['frozen'].get(key) or spec['later'][key];s=spec['solvents'][name]
        temperature=s['lle_regimes'][regime]
        sig=dict(signature,system=system,temperature_K=temperature,solute_surface_sha256=u['surface_sha256'],solvent_surface_sha256=s['surface_sha256'])
        path=out/'systems'/(key+'__'+name+'__'+regime+'.json')
        def calculate():
            guard(local);t=time.monotonic()
            solute=cache.get(u['surface'],u['surface_sha256']);solvent=cache.get(s['surface'],s['surface_sha256']);guard(local)
            value=safe(solute,solvent,temperature,u['molecular_weight_g_mol'],s['molecular_weight_g_mol'],plan['grid_batch'])
            if value['status']=='activity_nonconvergence':validate_failure(value)
            value['value_validated']=value['status'] in ('single_liquid_phase','two_liquid_phases')
            value.update(input_inchikey=key,product_solvent_key=name,regime=regime,temperature_K=temperature,
                solute_surface_sha256=u['surface_sha256'],solvent_surface_sha256=s['surface_sha256'],
                parameterization='openCOSMO-RS 24a',execution=execution,wall_seconds=time.monotonic()-t)
            return value
        value,reused=checkpoint(path,sig,calculate);statuses[value['status']]=statuses.get(value['status'],0)+1
        print(json.dumps(dict(system=system,status=value['status'],reused=reused)),flush=True)
        gc.collect();guard(local)
    save(out/'complete.json',dict(status='lle_systems_complete_including_unresolved',signature=signature,
        execution=execution,systems=len(expected),statuses=statuses,wall_seconds=time.monotonic()-start,
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))

if __name__=='__main__':main()
