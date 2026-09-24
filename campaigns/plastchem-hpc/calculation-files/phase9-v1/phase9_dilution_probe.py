"""Diagnose gate differences using the unmodified engine; no empirical adjustment."""
import os
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
import json,math,time
from pathlib import Path
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
from phase9_profiles import ProfileCache,infinite_dilution,sha
from phase9_sweep import save
D=Path(__file__).resolve().parent;BASE=D.parent/'phase8-v1';OLD=D.parent/'phase83-v1'


def main():
    assert os.environ.get('SLURM_JOB_ID')
    c=json.loads((OLD/'cohort.json').read_text());m=json.loads((BASE/'manifest.json').read_text());plan=json.loads((D/'gate-plan.json').read_text())
    water=BASE/next(s['B'] for s in m['solvents'] if s['name']=='water');cache=ProfileCache();phase=cache.get(water);rows=[]
    for i in [2055,4324,3620,5742,5827,5829]:
        r=c['rows'][i];path=OLD/r['B'];old=json.loads((OLD/'results'/f'{i:05d}'/'activities/solvent-water.json').read_text())
        engine=COSMORS(openCOSMORS24a());engine.add_molecule([str(path)]);engine.add_molecule([str(water)])
        xs=[0.,1e-8,1e-7,1e-6,1e-5]
        for x in xs:engine.add_job(x=np.array([x,1-x]),T=298.15,refst='pure_component')
        vals=engine.calculate()['tot']['lng'][:,0]
        cached=float(infinite_dilution([cache.get(path)],phase)[0])
        chunk=next(j for j,units in enumerate(plan['chunks']) if any(u['id']==f'cal-{i:05d}' for u in units))
        position=next(j for j,u in enumerate(plan['chunks'][chunk]) if u['id']==f'cal-{i:05d}')
        gate=json.loads((D/'gate-results-v1'/f'{chunk:04d}'/'activities/solvent-water.json').read_text())['values'][position]
        row=dict(index=i,name=r['input']['name'],inchikey=r['inchikey'],solute_surface_sha256=sha(path),water_surface_sha256=sha(water),ordinary_samples=[dict(x=x,ln_gamma=float(y)) for x,y in zip(xs,vals)],old_samples=old['samples'],old_converged_ln_gamma=old['ln_gamma'],cached_x0_ln_gamma=cached,gate_x0_ln_gamma=gate,
                 ordinary_vs_cached_x0_log10=(float(vals[0])-cached)/math.log(10),ordinary_vs_gate_x0_log10=(float(vals[0])-gate)/math.log(10),old_first_vs_ordinary_x1e5_log10=(old['samples'][0]['ln_gamma']-float(vals[-1]))/math.log(10),finite_x1e5_minus_x0_log10=(float(vals[-1])-float(vals[0]))/math.log(10))
        rows.append(row);print(json.dumps(row),flush=True)
    save(D/'dilution-probe-result.json',dict(status='complete',rows=rows,job_id=os.environ['SLURM_JOB_ID'],driver_sha256=sha(__file__),cpu_model=next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name'))))


if __name__=='__main__':main()
