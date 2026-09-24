"""A-9 chunk worker: parse once, independent engines, phase-bound checkpoints.

No empirical correction. LLE uses the pinned 8.2 solver unmodified. Its convex
hull screen runs on both validated grids; a coarse grid is never sufficient to
declare a single phase. Missing or corrupt checkpoints are never silently used.
"""
import os
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:
    os.environ[k]='1'
import datetime
import gc
import hashlib
import json
import math
import resource
import sys
import time
import traceback
from pathlib import Path
import numpy as np
from scipy.special import logsumexp
from phase9_profiles import ProfileCache, engine_from_profiles, infinite_dilution, sha
from phase9_grid import binary_grid
from phase8_lle import solve_lle

D=Path(__file__).resolve().parent
BASE=D.parent/'phase8-v1'


def save(p, value):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    with tmp.open('w') as f:
        json.dump(value,f,separators=(',',':'));f.write('\n');f.flush();os.fsync(f.fileno())
    tmp.replace(p)


def guard():
    rss=int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))
    if rss>3000*1024:raise MemoryError('A-9 guard: RSS above 3000 MiB; completed phases retained')


def checkpoint(path, signature, operation):
    seal=path.with_suffix(path.suffix+'.sha256.json')
    if seal.exists():
        s=json.loads(seal.read_text());assert s['signature']==signature
        assert sha(path)==s['sha256'],str(path)
        return json.loads(path.read_text()),True
    # A payload without its seal is an interrupted phase and is recomputed.
    value=operation();save(path,value)
    save(seal,dict(signature=signature,sha256=sha(path)))
    return value,False


def ensemble(rows, activities):
    es=np.array([r['B_energy_hartree'] for r in rows]);de=es-es.min()
    norm=-de*2625.4996394799/(.00831446261815324*298.15);norm-=logsumexp(norm)
    old=-de*627.5094740631/(.0019872041*298.15)
    vols=np.array([r['B_cavity_cm3_mol'] for r in rows])
    vals=np.array(activities)
    return dict(normalized_gamma=-float(logsumexp(norm-vals)),existing_gamma=-float(logsumexp(old-vals)),
                normalized_volume=float(np.exp(norm)@vols),existing_volume=float(np.exp(old-logsumexp(old))@vols))


def lle_result(solute, solvent, temperature, mw_s, mw_v, batch_size):
    engine=engine_from_profiles([solute,solvent],threshold=1e-9)
    cache={};calls=0
    def evaluate(xs):
        nonlocal calls
        keys=[format(float(x),'.17g') for x in xs]
        missing=list(dict.fromkeys(k for k in keys if k not in cache))
        for off in range(0,len(missing),batch_size):
            guard();chunk=missing[off:off+batch_size];engine.clear_jobs()
            for k in chunk:
                x=float(k);engine.add_job(x=np.array([x,1-x]),T=temperature,refst='pure_component')
            values=engine.calculate()['tot']['lng'];assert np.isfinite(values).all()
            for k,v in zip(chunk,values):cache[k]=list(map(float,v))
            calls+=1
        return np.array([cache[k] for k in keys])
    # Reuse all points shared by the two grids, including their logarithmic tails.
    # The unmodified hull screen/refiner consumes this cache and only refines gaps.
    xs=np.unique(np.concatenate([np.unique(np.r_[np.arange(1,n)/n,np.geomspace(1e-14,1e-3,45),1-np.geomspace(1e-14,1e-3,45)]) for n in [1000,2000]]))
    for off in range(0,len(xs),batch_size):
        guard();chunk=xs[off:off+batch_size]
        values=binary_grid(engine,chunk,temperature)
        for x,v in zip(chunk,values):cache[format(float(x),'.17g')]=list(map(float,v))
        calls+=1
    result=solve_lle(evaluate,mw_s,mw_v)
    result.update(n_activity_points=len(cache),calculate_calls=calls,screen='exact 8.2 two-grid Gibbs lower hull; no coarse-only single-phase acceptance',activities=cache)
    del engine;gc.collect()
    return result


def main(planpath,idx):
    assert os.environ.get('SLURM_JOB_ID'),'Euler allocation required'
    assert int(os.environ['SLURM_JOB_NUM_NODES'])==1
    assert int(os.environ['SLURM_NTASKS'])==1
    assert int(os.environ['SLURM_CPUS_PER_TASK'])==1
    planpath=Path(planpath);plan=json.loads(planpath.read_text());units=plan['chunks'][idx]
    manifest=json.loads((BASE/'manifest.json').read_text());validation=json.loads((BASE/'validation-inputs.json').read_text())
    assert sha(BASE/'phase8_lle.py')=='1fee9216925b91877eaf79840e010330801b93adf41b0b116c774358df0296f9'
    assert sha(BASE/'manifest.json')==plan['manifest_sha256']
    assert sha(BASE/'validation-inputs.json')==plan['validation_sha256']
    for path,digest in json.loads((BASE/'package-pins.json').read_text()).items():
        assert sha(BASE/path)==digest,path
    start=time.monotonic();cache=ProfileCache();out=D/plan['output']/f'{idx:04d}'
    signature=dict(plan_sha256=sha(planpath),worker_sha256=sha(__file__),profiles_sha256=sha(D/'phase9_profiles.py'),grid_sha256=sha(D/'phase9_grid.py'),solver_sha256=sha(BASE/'phase8_lle.py'),package_pins_sha256=sha(BASE/'package-pins.json'))
    execution=dict(job_id=os.environ['SLURM_JOB_ID'],array_task_id=os.environ.get('SLURM_ARRAY_TASK_ID'),node=os.uname().nodename,cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')))
    assert ('EPYC 7763' in execution['cpu_model'] or 'EPYC 9' in execution['cpu_model']),execution
    # Cache only compact parsed profiles, and release large parser temporaries.
    profiles=[]
    for u in units:
        profiles.append(cache.get(D/u['surface'],u['surface_sha256']));guard()
    phases=[dict(id='solvent-'+s['name'],surface=s['B']) for s in manifest['solvents']]
    phases += [dict(id='polymer-'+r['entry_id'],surface=r['B']) for rs in manifest['polymers'].values() for r in rs]
    for phase in phases:
        cache.get(BASE/phase['surface']);guard()
    parse_seconds=time.monotonic()-start
    save(out/'started.json',dict(signature=signature,execution=execution,parse_seconds=parse_seconds,parse_count=cache.parse_count,units=[u['id'] for u in units]))
    phase_outputs={}
    for phase in phases:
        t=time.monotonic();p=cache.get(BASE/phase['surface'])
        sig=dict(signature,phase_sha256=p['sha256'],unit_ids=[u['id'] for u in units],temperature_K=298.15)
        def calc():
            values=[]
            for off in range(0,len(units),plan['subbatch']):
                guard();values.extend(map(float,infinite_dilution(profiles[off:off+plan['subbatch']],p)));gc.collect()
            return dict(phase=phase['id'],phase_sha256=p['sha256'],values=values,solute_x=0.,reference_state='pure_component',wall_seconds=time.monotonic()-t,execution=execution)
        value,reused=checkpoint(out/'activities'/(phase['id']+'.json'),sig,calc)
        phase_outputs[phase['id']]=value
        print('PHASE',idx,phase['id'],'reused' if reused else 'computed',round(time.monotonic()-t,3),flush=True)
    partition_seconds=time.monotonic()-start-parse_seconds
    for i,u in enumerate(units):
        def partition():
            rows=[]
            for polymer,rs in manifest['polymers'].items():
                e=ensemble(rs,[phase_outputs['polymer-'+r['entry_id']]['values'][i] for r in rs])
                for s in manifest['solvents']:
                    lng=phase_outputs['solvent-'+s['name']]['values'][i]
                    for convention in ['normalized','existing']:
                        vp=e[convention+'_volume'];vs=s['B_volume' if convention=='normalized' else 'B_legacy_volume'];kx=(e[convention+'_gamma']-lng)/math.log(10)
                        rows.append(dict(unit=u['id'],inchikey=u['inchikey'],polymer=polymer,solvent=s['name'],convention=convention,temperature_K=298.15,status='predicted',logP_x=kx,logP_concentration=kx+math.log10(vp/vs),ln_gamma_polymer=e[convention+'_gamma'],ln_gamma_solvent=lng,polymer_volume_cm3_mol=vp,solvent_volume_cm3_mol=vs,solute_surface_sha256=u['surface_sha256'],solvent_surface_sha256=phase_outputs['solvent-'+s['name']]['phase_sha256']))
            return rows
        checkpoint(out/'partition'/(u['id']+'.json'),dict(signature,unit=u['id']),partition)
    lle_start=time.monotonic();statuses={}
    for s in manifest['solvents']:
        p=cache.get(BASE/s['B'])
        high=next(x for x in validation['lle_units'] if x['solvent']==s['name'] and x['regime']=='high')
        for i,u in enumerate(units):
            for regime,T in [('RT',298.15),('high',high['temperature_K'])]:
                key=u['id']+'__'+s['name']+'__'+regime
                sig=dict(signature,unit=u['id'],solute_sha256=u['surface_sha256'],solvent_sha256=p['sha256'],temperature_K=T)
                def calc_lle():
                    t=time.monotonic()
                    r=lle_result(profiles[i],p,T,u['molecular_weight_g_mol'],validation['solvent_identities'][s['name']]['molecular_weight_g_mol'],plan['grid_batch'])
                    r.update(unit=u['id'],inchikey=u['inchikey'],solvent=s['name'],regime=regime,temperature_K=T,wall_seconds=time.monotonic()-t,execution=execution,signature=sig)
                    return r
                value,reused=checkpoint(out/'lle'/(key+'.json'),sig,calc_lle)
                statuses[key]=value['status'];print('LLE',idx,key,value['status'],'reused' if reused else 'computed',flush=True)
        # A phase seal is written only after all its independent systems are sealed.
        phasefiles=sorted((out/'lle').glob('*__'+s['name']+'__*.json'))
        save(out/'lle-phase-seals'/(s['name']+'.json'),dict(signature=signature,files={p.name:sha(p) for p in phasefiles}))
    save(out/'complete.json',dict(signature=signature,execution=execution,parse_seconds=parse_seconds,partition_seconds=partition_seconds,lle_seconds=time.monotonic()-lle_start,wall_seconds=time.monotonic()-start,parse_count=cache.parse_count,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,unit_ids=[u['id'] for u in units],lle_statuses=statuses))


if __name__=='__main__':
    try:main(sys.argv[1],int(sys.argv[2]))
    except Exception:
        traceback.print_exc();raise
