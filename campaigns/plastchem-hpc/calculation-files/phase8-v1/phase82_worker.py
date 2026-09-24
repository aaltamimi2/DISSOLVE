"""Euler validation: PVC partitioning and explicit binary LLE, no product writes."""
import os,sys,json,time,traceback,math
from pathlib import Path
import phase8_worker as w
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
from phase8_lle import solve_lle
D=Path(__file__).resolve().parent
def lle_unit(manifest,validation,idx):
 u=validation['lle_units'][idx];out=D/'phase82/lle'/f'{idx:03d}';out.mkdir(parents=True,exist_ok=True)
 if (out/'complete.json').exists():return
 start=time.monotonic();s=validation['solutes'][u['solute']];v=next(v for v in manifest['solvents'] if v['name']==u['solvent']);cachefile=out/'activity-grid.json';sig={'solute_sha256':w.sha(D/s['B']),'solvent_sha256':w.sha(D/v['B']),'temperature_K':u['temperature_K'],'cosmospace_conv_thresh':1e-9}
 cache={}
 if cachefile.exists():
  old=json.loads(cachefile.read_text());assert old['signature']==sig;cache=old['activities']
 par=openCOSMORS24a();par.cosmospace_conv_thresh=1e-9;engine=COSMORS(par);engine.add_molecule([str(D/s['B'])]);engine.add_molecule([str(D/v['B'])]);count=0
 def flush():w.save(cachefile,{'signature':sig,'activities':cache})
 def evaluate(xs):
  nonlocal count
  keys=[format(float(x),'.17g') for x in xs];missing=list(dict.fromkeys(k for k in keys if k not in cache))
  for off in range(0,len(missing),8):
   w.guard();chunk=missing[off:off+8];engine.clear_jobs()
   for k in chunk:
    x=float(k);engine.add_job(x=np.array([x,1-x]),T=u['temperature_K'],refst='pure_component')
   values=engine.calculate()['tot']['lng'];assert np.isfinite(values).all()
   for k,values_k in zip(chunk,values):cache[k]=list(map(float,values_k))
   count+=len(chunk)
   if count%64<8:flush();print('LLE_GRID',idx,count,round(time.monotonic()-start,2),flush=True)
  return np.array([cache[k] for k in keys])
 try:
  result=solve_lle(evaluate,s['molecular_weight_g_mol'],validation['solvent_identities'][u['solvent']]['molecular_weight_g_mol']);flush()
  result.update(u,signature=sig,solute_input=s['input'],solvent_identity=validation['solvent_identities'][u['solvent']],wall_seconds=time.monotonic()-start,n_activity_points=len(cache),execution=w.provenance(),validation_inputs_sha256=w.sha(D/'validation-inputs.json'),driver_sha256=w.sha(__file__),solver_sha256=w.sha(D/'phase8_lle.py'),numerical_note='24a physical parameters unchanged; COSMOspace convergence tightened to 1e-9 for tie-line residual test 1e-7; 1000 and 2000 interval grids, three initial guesses per gap; no empirical correction')
  w.save(out/'result.json',result);w.save(out/'complete.json',dict(w.provenance(),status=result['status'],result_sha256=w.sha(out/'result.json')))
 finally:del engine;w.cleanup()
if __name__=='__main__':
 if not os.environ.get('SLURM_JOB_ID'):raise SystemExit('Euler allocation required')
 manifest=json.loads((D/'manifest.json').read_text());validation=json.loads((D/'validation-inputs.json').read_text());mode=sys.argv[1];idx=int(sys.argv[2])
 out=D/'phase82'/mode/(f'{idx:03d}' if mode=='lle' else list(validation['solutes'])[idx])
 try:
  if mode=='partition':
   name=list(validation['solutes'])[idx];stamp=D/'phase82/partition'/name/'complete.json'
   if stamp.exists():assert json.loads(stamp.read_text())['validation_inputs_sha256']==w.sha(D/'validation-inputs.json')
   w.partition_unit(manifest,'pvc',name,validation['solutes'][name],D/'phase82/partition'/name,routes=('B',));c=json.loads(stamp.read_text());c.update(validation_inputs_sha256=w.sha(D/'validation-inputs.json'),driver_sha256=w.sha(__file__));w.save(stamp,c)
  elif mode=='lle':lle_unit(manifest,validation,idx)
  else:raise ValueError(mode)
 except Exception as e:w.save(out/'failure.json',dict(w.provenance(),error=str(e),traceback=traceback.format_exc()));raise
