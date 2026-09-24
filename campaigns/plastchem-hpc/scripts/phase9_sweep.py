"""Euler-only A-9 timing/memory/reuse proof; no production submission."""
import os
for k in ['OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']:
    os.environ[k] = '1'
import datetime
import gc
import hashlib
import json
import math
import resource
import time
from pathlib import Path
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
from phase9_profiles import ProfileCache, engine_from_profiles, infinite_dilution, profile_digest

D = Path(__file__).resolve().parent
BASE = D.parent/'phase8-v1'
OLD = D.parent/'phase83-v1'


def save(p, v):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(v, f, indent=2); f.write('\n'); f.flush(); os.fsync(f.fileno())
    tmp.replace(p)


def rss():
    return int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))


def main():
    assert os.environ.get('SLURM_JOB_ID'), 'Euler allocation required'
    start = time.monotonic()
    cohort = json.loads((OLD/'cohort.json').read_text())
    manifest = json.loads((BASE/'manifest.json').read_text())
    cache = ProfileCache()
    phases = [next(s for s in manifest['solvents'] if s['name'] == 'water')['B'], manifest['polymers']['pe'][0]['B']]
    pp = [cache.get(BASE/p) for p in phases]
    out = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               job=os.environ['SLURM_JOB_ID'], cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')),
               profiles_sha256=hashlib.sha256((D/'phase9_profiles.py').read_bytes()).hexdigest(),
               parsed=[], reuse_checks=[], sweep=[], grid_sweep=[])
    selected = list(dict.fromkeys(cohort['calibration_indices'] + list(range(1000))))[:1000]
    profiles=[]
    for i, idx in enumerate(selected):
        c=cohort['rows'][idx];t=time.monotonic()
        profiles.append(cache.get(OLD/c['B'],c['surface_sha256']))
        out['parsed'].append(dict(index=idx,seconds=time.monotonic()-t,rss_kib=rss(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        if i+1 in [1,10,50,100,300,500,1000]:
            save(D/'sweep-progress.json',out);print('PARSED',i+1,rss(),flush=True)
        if rss()>3000*1024:
            out['stop']='memory_guard_3000MiB';break
    # Validate against ordinary parsing, both orders, and after another engine used the cache.
    for p in profiles[:3]:
        for phase in pp:
            fingerprint=[profile_digest(p),profile_digest(phase)]
            for reverse in [False,True]:
                pair=[p,phase] if not reverse else [phase,p]
                standard=COSMORS(openCOSMORS24a())
                for item in pair:standard.add_molecule([item['path']])
                cached=engine_from_profiles(pair)
                for x in [0.,1e-8,1e-5,.2,.8,1.]:
                    composition=np.array([x,1-x])
                    standard.add_job(x=composition.copy(),T=298.15,refst='pure_component')
                    cached.add_job(x=composition.copy(),T=298.15,refst='pure_component')
                a=standard.calculate()['tot']['lng'];b=cached.calculate()['tot']['lng']
                error=float(np.max(np.abs(a-b)))
                assert error<1e-10,(p['path'],phase['path'],error)
                assert fingerprint==[profile_digest(p),profile_digest(phase)]
                out['reuse_checks'].append(dict(solute=p['sha256'],phase=phase['sha256'],reverse=reverse,max_abs_ln_gamma_error=error))
                del standard,cached;gc.collect()
    for n in [10,25,50,100,200]:
        if n>len(profiles):continue
        for phase in pp:
            t=time.monotonic();before=rss();values=infinite_dilution(profiles[:n],phase)
            out['sweep'].append(dict(n=n,phase=phase['sha256'],seconds=time.monotonic()-t,rss_before_kib=before,rss_after_kib=rss(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,values=list(map(float,values))))
            save(D/'sweep-progress.json',out);print('SWEEP',n,phase['sha256'][:10],out['sweep'][-1]['seconds'],rss(),flush=True)
            gc.collect()
    # Grid call size sweep on the same points, with unmodified numerical engine.
    xs=np.linspace(.001,.999,256)
    baseline=None
    for batch in [8,64,256]:
        engine=engine_from_profiles([profiles[0],pp[0]],threshold=1e-9)
        values=[];t=time.monotonic()
        for off in range(0,len(xs),batch):
            engine.clear_jobs()
            for x in xs[off:off+batch]:engine.add_job(x=np.array([x,1-x]),T=298.15,refst='pure_component')
            values.extend(engine.calculate()['tot']['lng'].tolist())
        values=np.array(values)
        if baseline is None:baseline=values
        error=float(np.max(np.abs(values-baseline)));assert error<1e-10
        out['grid_sweep'].append(dict(batch=batch,points=len(xs),seconds=time.monotonic()-t,max_abs_ln_gamma_error=error,rss_kib=rss(),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss))
        save(D/'sweep-progress.json',out);del engine;gc.collect()
    out.update(status='complete',wall_seconds=time.monotonic()-start,parse_count=cache.parse_count,retained_profile_count=len(profiles)+2,peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    save(D/'sweep-result.json',out)
    print(json.dumps({k:v for k,v in out.items() if k not in ['parsed','sweep','reuse_checks']}),flush=True)


if __name__=='__main__':main()
