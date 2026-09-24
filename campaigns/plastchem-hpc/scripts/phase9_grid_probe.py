"""Euler independent-grid equivalence and throughput diagnostic for A-9."""
import os
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
import json,time,resource,sys
from pathlib import Path
import numpy as np
from phase9_profiles import ProfileCache,engine_from_profiles,sha
from phase9_grid import binary_grid
from phase9_sweep import save

D=Path(__file__).resolve().parent;BASE=D.parent/'phase8-v1';OLD=D.parent/'phase83-v1'


def main():
    assert os.environ.get('SLURM_JOB_ID')
    v=json.loads((BASE/'validation-inputs.json').read_text());m=json.loads((BASE/'manifest.json').read_text());c=json.loads((OLD/'cohort.json').read_text())
    cases=[(BASE/v['solutes'][name]['B'],BASE/next(s['B'] for s in m['solvents'] if s['name']==solvent),T) for name,solvent,T in [('DEP','water',298.15),('DEHP','water',372.15),('DBP','toluene',298.15),('BBP','methanol',337.15)]]
    cases += [(OLD/c['rows'][i]['B'],BASE/next(s['B'] for s in m['solvents'] if s['name']=='water'),298.15) for i in c['calibration_indices'][:3]]
    cache=ProfileCache();rows=[]
    xs=np.unique(np.r_[np.linspace(.001,.999,256),np.geomspace(1e-14,1e-3,45),1-np.geomspace(1e-14,1e-3,45)])
    for a,b,T in cases:
        engine=engine_from_profiles([cache.get(a),cache.get(b)],threshold=1e-9);old=[];start=time.monotonic()
        for off in range(0,len(xs),256):
            engine.clear_jobs()
            for x in xs[off:off+256]:engine.add_job(x=np.array([x,1-x]),T=T,refst='pure_component')
            old.extend(engine.calculate()['tot']['lng'].tolist())
        old_seconds=time.monotonic()-start;start=time.monotonic();new=binary_grid(engine,xs,T);new_seconds=time.monotonic()-start
        error=float(np.max(np.abs(new-np.array(old))))
        rows.append(dict(solute_sha256=sha(a),phase_sha256=sha(b),temperature_K=T,n=len(xs),old_seconds=old_seconds,new_seconds=new_seconds,max_abs_ln_gamma_error=error))
        save(D/'grid-probe-progress.json',dict(rows=rows));print(json.dumps(rows[-1]),flush=True)
        assert error<1e-8,rows[-1]
    save(D/'grid-probe-result.json',dict(status='passed',rows=rows,driver_sha256=sha(__file__),grid_sha256=sha(D/'phase9_grid.py'),peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,job_id=os.environ['SLURM_JOB_ID']))


if __name__=='__main__':main()
