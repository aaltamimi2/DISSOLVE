"""One polymer/solute unit. Fresh process, atomic durable outputs, bounded memory."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['MKL_NUM_THREADS']='1'
import sys,json,csv,math,hashlib,time,gc,ctypes,fcntl
sys.dont_write_bytecode=True
from pathlib import Path
import numpy as np
from scipy.special import logsumexp
from opencosmorspy import COSMORS,Parameterization
from opencosmorspy.parameterization import openCOSMORS24a
R=Path('/home/aaltamimi2/plastchem-euler');BASE=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7');A6=BASE.parent/'route-comparison-a6'
polymer,solute=sys.argv[1:];D=BASE/polymer;U=D/'units'/solute;U.mkdir(parents=True,exist_ok=True)
lock=(R/'state/thermodynamics-v1/worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):
 p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_text(json.dumps(v,indent=2)+'\n');tmp.replace(p)
def csvsave(p,rows):
 tmp=p.with_suffix('.tmp')
 with tmp.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in rows for k in row)));w.writeheader();w.writerows(rows);f.flush();os.fsync(f.fileno())
 tmp.replace(p)
def guard():
 mem={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()};rss=int(next(l.split()[1] for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('VmRSS:')))
 if rss>1800*1024 or mem['MemAvailable']<int(2.5*1024*1024):
  save(U/'memory-pause.json',{'rss_kib':rss,'available_kib':mem['MemAvailable'],'polymer':polymer,'solute':solute});raise SystemExit(75)
guard();inputs=json.loads((D/'inputs.json').read_text());ensemble=list(csv.DictReader((D/'pe-ensemble.csv').open()));n=len(ensemble);solutes={solute:{k:Path(v) for k,v in inputs['solutes'][solute].items()}}
if (U/'complete.json').exists():raise SystemExit(0)
T=298.15;EH=2625.4996394799;RT=.00831446261815324*T
weights={};z={}
for route in ['A','B','B_OPT']:
 energies=np.array([float(r[route+'_energy_hartree']) for r in ensemble]);w=np.exp(-(energies-energies.min())*EH/RT);z[route]=float(w.sum());weights[route]=w/w.sum()
def activity(route,phase,label):
 guard();path=D/'activities'/f'{route}-{solute}-{label}.json';signature={'route':route,'solute_sha256':sha(solutes[solute][route]),'phase_sha256':sha(phase)}
 cached=path
 if not cached.exists() and label.startswith('solvent-'):cached=A6/'activities'/path.name
 if not cached.exists() and polymer=='pe':cached=A6/'activities'/path.name
 if cached.exists():
  v=json.loads(cached.read_text());assert v['signature']==signature;return v
 v={'signature':signature,'samples':[],'temperature_K':T,'reference_state':'pure_component'};engine=None;start=time.monotonic()
 try:
  engine=COSMORS(openCOSMORS24a() if route=='B' else Parameterization('default_turbomole'));engine.add_molecule([str(solutes[solute][route])]);engine.add_molecule([str(phase)])
  for x in [1e-5,1e-6,1e-7,1e-8]:
   guard();engine.add_job(x=np.array([x,1-x]),T=T,refst='pure_component');lng=float(engine.calculate()['tot']['lng'][-1][0]);assert math.isfinite(lng);v['samples'].append({'x':x,'ln_gamma':lng})
   if len(v['samples'])>1 and abs(lng-v['samples'][-2]['ln_gamma'])/math.log(10)<=.005:v.update(status='converged',ln_gamma=lng);break
  else:v['status']='dilution_not_converged'
 except MemoryError:raise SystemExit(75)
 except Exception as exc:v.update(status='failed',error=str(exc))
 finally:
  del engine;gc.collect()
  try:ctypes.CDLL('libc.so.6').malloc_trim(0)
  except OSError:pass
 v['wall_seconds']=time.monotonic()-start;save(path,v);guard();return v
legacy_vol={r['solvent']:r for r in csv.DictReader((A6/'existing-convention-comparison.csv').open()) if r['solute']==solute.upper()}
calculated={}
for route in ['A','B']:
 acts=[]
 for i,row in enumerate(ensemble):
  phase=D/'converted-A'/(row['entry_id'].split('__',1)[1]+'.cosmo') if route=='A' else BASE.parent/'results'/row['entry_id']/'surface.orcacosmo'
  a=activity(route,phase,f'pe-{i:02}');assert a['status']=='converged',(polymer,solute,route,i,a);acts.append(a)
 w=weights[route];vp=sum(float(r[route+'_cavity_cm3_mol'])*wi for r,wi in zip(ensemble,w));gamma=-float(logsumexp(np.log(w)-np.array([a['ln_gamma'] for a in acts])))
 # Existing convention uses the exact constants from the pinned A-6 implementation.
 HARTREE_TO_KCAL=627.509474;GAS=0.00198720425864083
 # Read numeric constants from the previously pinned reference module (no COSMO objects).
 import importlib.util
 spec=importlib.util.spec_from_file_location('a7_reference',Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/cosmo_logp.py'));ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
 es=[float(r[route+'_energy_hartree']) for r in ensemble];relative=[(e-min(es))*ref.HARTREE_TO_KCAL for e in es];rw=ref.boltzmann_weights(relative);legacygamma=ref.boltzmann_combine([a['samples'][0]['ln_gamma'] for a in acts],relative);legacyvp=sum(float(r[route+'_cavity_cm3_mol'])*wi for r,wi in zip(ensemble,rw))
 optgamma=-float(logsumexp(np.log(weights['B_OPT'])-np.array([a['ln_gamma'] for a in acts]))) if route=='B' else None
 for solvent in inputs['solvents']:
  name=solvent['name'];a=activity(route,Path(solvent[route]),'solvent-'+name);assert a['status']=='converged';kx=(gamma-a['ln_gamma'])/math.log(10);conc=kx+math.log10(vp/solvent[route+'_volume']);lv=legacy_vol[name];legacyvs=float(lv[route+'_solvent_volume_cm3_mol']);lx=(legacygamma-a['samples'][0]['ln_gamma'])/math.log(10)
  row={'status':'predicted','logP_x':kx,'logP_concentration':conc,'solvent_volume_cm3_mol':solvent[route+'_volume'],'solvent_volume_source':solvent[route+'_volume_source'],'PE_volume_cm3_mol':vp,'PE_volume_source':'Boltzmann-weighted route-specific COSMO cavity','legacy_logP_x':lx,'legacy_logP_concentration':lx+math.log10(legacyvp/legacyvs),'legacy_solvent_volume':legacyvs,'legacy_volume_source':lv[route+'_volume_source'],'legacy_polymer_volume':legacyvp}
  if route=='B':row['OPT_weight_sensitivity_logP_concentration']=(optgamma-a['ln_gamma'])/math.log(10)+math.log10(sum(weights['B_OPT'][i]*float(ensemble[i]['B_cavity_cm3_mol']) for i in range(n))/solvent['B_volume'])
  calculated[route,name]=row
 del acts,ref;gc.collect();guard()
normalized=[];existing=[]
for solvent in inputs['solvents']:
 name=solvent['name'];a=calculated['A',name];b=calculated['B',name];row={'solute':solute.upper(),'solvent':name}
 for route,value in [('A',a),('B',b)]:row.update({route+'_'+k:v for k,v in value.items()})
 row.update(delta_B_minus_A=b['logP_concentration']-a['logP_concentration'],delta_B_minus_A_x=b['logP_x']-a['logP_x'],sign_changed=a['logP_concentration']*b['logP_concentration']<0);normalized.append(row)
 existing.append({'solute':solute.upper(),'solvent':name,'A_existing_product_logP_concentration':a['legacy_logP_concentration'],'B_same_convention_logP_concentration':b['legacy_logP_concentration'],'delta_B_minus_A':b['legacy_logP_concentration']-a['legacy_logP_concentration'],'A_logP_x':a['legacy_logP_x'],'B_logP_x':b['legacy_logP_x'],'sign_changed':a['legacy_logP_concentration']*b['legacy_logP_concentration']<0,'A_solvent_volume_cm3_mol':a['legacy_solvent_volume'],'B_solvent_volume_cm3_mol':b['legacy_solvent_volume'],'A_volume_source':a['legacy_volume_source'],'B_volume_source':b['legacy_volume_source'],'A_PE_volume_cm3_mol':a['legacy_polymer_volume'],'B_PE_volume_cm3_mol':b['legacy_polymer_volume']})
csvsave(U/'normalized.csv',normalized);csvsave(U/'existing.csv',existing);save(U/'complete.json',{'polymer':polymer,'solute':solute,'rows':32,'normalized_sha256':sha(U/'normalized.csv'),'existing_sha256':sha(U/'existing.csv')});print('UNIT_COMPLETE',polymer,solute,flush=True)
