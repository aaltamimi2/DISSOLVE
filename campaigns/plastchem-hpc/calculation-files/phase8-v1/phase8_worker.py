"""Euler-only, one polymer/solute per process; durable input-bound raw activities."""
import os
for key in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[key]='1'
import csv,json,math,hashlib,time,gc,ctypes,sys,resource,datetime,traceback
from pathlib import Path
import numpy as np
from scipy.special import logsumexp
from opencosmorspy import COSMORS,Parameterization
from opencosmorspy.parameterization import openCOSMORS24a
D=Path(__file__).resolve().parent
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
 with tmp.open('w') as f:json.dump(v,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 tmp.replace(p)
def csvsave(p,rows):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix('.tmp')
 with tmp.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows);f.flush();os.fsync(f.fileno())
 tmp.replace(p)
def guard():
 rss=int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))
 if rss>1850*1024:raise MemoryError('Unit RSS above 1850 MiB; durable completed activities retained')
def cleanup():
 gc.collect()
 try:ctypes.CDLL('libc.so.6').malloc_trim(0)
 except OSError:pass
def provenance():
 return {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cpu_model':next(l.split(':',1)[1].strip() for l in Path('/proc/cpuinfo').read_text().splitlines() if l.startswith('model name')),'job_id':os.environ.get('SLURM_JOB_ID'),'array_task_id':os.environ.get('SLURM_ARRAY_TASK_ID'),'node':os.uname().nodename,'manifest_sha256':sha(D/'manifest.json'),'worker_sha256':sha(__file__),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
def activity(route,solute,phase,path,T=298.15):
 guard();sig={'route':route,'solute_sha256':sha(D/solute),'phase_sha256':sha(D/phase),'temperature_K':T}
 if path.exists():
  v=json.loads(path.read_text());assert v['signature']==sig
  if v['status']=='converged':return v
 v={'signature':sig,'samples':[],'reference_state':'pure_component'};engine=None;start=time.monotonic()
 try:
  engine=COSMORS(openCOSMORS24a() if route=='B' else Parameterization('default_turbomole'));engine.add_molecule([str(D/solute)]);engine.add_molecule([str(D/phase)])
  for x in [1e-5,1e-6,1e-7,1e-8]:
   guard();engine.add_job(x=np.array([x,1-x]),T=T,refst='pure_component');lng=float(engine.calculate()['tot']['lng'][-1][0]);assert math.isfinite(lng);v['samples'].append({'x':x,'ln_gamma':lng})
   if len(v['samples'])>1 and abs(lng-v['samples'][-2]['ln_gamma'])/math.log(10)<=.005:v.update(status='converged',ln_gamma=lng);break
  else:v['status']='dilution_not_converged'
 except MemoryError:raise
 except Exception as e:v.update(status='failed',error=str(e))
 finally:del engine;cleanup()
 v.update(wall_seconds=time.monotonic()-start,execution=provenance());save(path,v);guard();return v
def ensemble_calc(route,ensemble,acts):
 es=np.array([r[route+'_energy_hartree'] for r in ensemble]);de=es-es.min();lnw=-de*2625.4996394799/(.00831446261815324*298.15);lnw-=logsumexp(lnw)
 old=-de*627.5094740631/(.0019872041*298.15);oldw=np.exp(old-logsumexp(old))
 vals=np.array([a['ln_gamma'] for a in acts]);first=np.array([a['samples'][0]['ln_gamma'] for a in acts]);vols=np.array([r[route+'_cavity_cm3_mol'] for r in ensemble])
 result={'normalized_gamma':-float(logsumexp(lnw-vals)),'existing_gamma':-float(logsumexp(old-first)),'normalized_volume':float(np.exp(lnw)@vols),'existing_volume':float(oldw@vols)}
 if route=='B':
  opt=np.array([r['B_OPT_energy_hartree'] for r in ensemble]);ow=-(opt-opt.min())*2625.4996394799/(.00831446261815324*298.15);ow-=logsumexp(ow);result.update(OPT_gamma=-float(logsumexp(ow-vals)),OPT_volume=float(np.exp(ow)@vols))
 return result
def partition_unit(manifest,polymer,solute,solute_paths,out,routes=('A','B')):
 out.mkdir(parents=True,exist_ok=True)
 if (out/'complete.json').exists():
  stamp=json.loads((out/'complete.json').read_text());assert stamp['manifest_sha256']==sha(D/'manifest.json');return
 start=time.monotonic();rows=[];ensembles={};errors={}
 for route in routes:
  acts=[]
  for r in manifest['polymers'][polymer]:
   a=activity(route,solute_paths[route],r[route],out/'activities'/f"{route}-polymer-{r['entry_id']}.json");acts.append(a)
  if all(a['status']=='converged' for a in acts):ensembles[route]=ensemble_calc(route,manifest['polymers'][polymer],acts)
  else:errors[route]=[a for a in acts if a['status']!='converged']
  del acts;cleanup()
  for solvent in manifest['solvents']:
   a=activity(route,solute_paths[route],solvent[route],out/'activities'/f"{route}-solvent-{solvent['name']}.json")
   for convention in ['normalized','existing']:
    r={'polymer':polymer,'solute':solute.upper(),'solvent':solvent['name'],'route':route,'convention':convention,'temperature_K':298.15,'status':'failed','solute_surface_sha256':sha(D/solute_paths[route]),'solvent_surface_sha256':sha(D/solvent[route])}
    if route in ensembles and a['status']=='converged':
     e=ensembles[route];vp=e[convention+'_volume'];vs=solvent[route+('_volume' if convention=='normalized' else '_legacy_volume')];lngs=a['ln_gamma'] if convention=='normalized' else a['samples'][0]['ln_gamma'];kx=(e[convention+'_gamma']-lngs)/math.log(10)
     r.update(status='predicted',ln_gamma_polymer=e[convention+'_gamma'],ln_gamma_solvent=lngs,logP_x=kx,logP_concentration=kx+math.log10(vp/vs),polymer_volume_cm3_mol=vp,solvent_volume_cm3_mol=vs,solvent_volume_source=solvent[route+('_volume_source' if convention=='normalized' else '_legacy_volume_source')])
     if route=='B' and convention=='normalized':r['OPT_weight_sensitivity_logP_concentration']=(e['OPT_gamma']-lngs)/math.log(10)+math.log10(e['OPT_volume']/vs)
    else:r['failure_mode']='polymer_activity_failed' if route not in ensembles else a['status']
    rows.append(r)
 csvsave(out/'predictions.csv',rows);save(out/'complete.json',dict(provenance(),polymer=polymer,solute=solute,rows=len(rows),failed=sum(r['status']!='predicted' for r in rows),wall_seconds=time.monotonic()-start,output_sha256=sha(out/'predictions.csv'),errors=errors))
if __name__=='__main__':
 if not os.environ.get('SLURM_JOB_ID'):raise SystemExit('Scientific computation must run in a Slurm allocation')
 manifest=json.loads((D/'manifest.json').read_text());mode=sys.argv[1];idx=int(sys.argv[2]);unit=manifest['phase81_units'][idx];out=D/'phase81'/unit['polymer']/unit['solute']
 try:partition_unit(manifest,unit['polymer'],unit['solute'],manifest['solutes'][unit['solute']],out)
 except Exception as e:save(out/'failure.json',dict(provenance(),error=str(e),traceback=traceback.format_exc()));raise
