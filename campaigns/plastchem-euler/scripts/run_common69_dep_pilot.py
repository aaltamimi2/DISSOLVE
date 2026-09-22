"""Bounded DEP pilot only. Never updates production ledgers or submits ORCA."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,json,hashlib,signal,time,datetime,resource,math,gc
from pathlib import Path
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
from thermodynamic_prediction import activity,refresh_volumes,CONFIG
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/common69-pilot-20260917');pid=1147795
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,o):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(o,indent=2)+'\n')
def emit(o):print(json.dumps(o),flush=True)
def csvsave(p,rows):
 fields=sorted(set().union(*(r.keys() for r in rows)))
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
audit=json.loads((D/'availability.json').read_text());solvents=[r for r in audit['rows'] if r.get('surface_parser_pass')]
assert len(solvents)==67
r=json.loads((R/'state/campaign-v1/records/FLKPEMZONWLCSK-UHFFFAOYSA-N.json').read_text());assert r['status']=='converged'
solute={'surface':str(Path(r['archive_path'])/'surface.orcacosmo'),'surface_sha256':r['surface_sha256']}
water=next(s for s in json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text())['solvents'] if s['solvent_key']=='water');assert water['status']=='ready'
volumes=refresh_volumes();ov=json.loads((R/'state/progress-2026-09-14/octanol-molar-volume.json').read_text());assert sha(ov['source_xml_path'])==ov['source_xml_sha256']
assert b'scripts/watch_thermodynamics.py' in Path(f'/proc/{pid}/cmdline').read_bytes()
start=time.monotonic();paused=False
try:
 os.kill(pid,signal.SIGSTOP);paused=True
 for _ in range(600):
  state=next(x for x in Path(f'/proc/{pid}/status').read_text().splitlines() if x.startswith('State:'))
  if '\tT' in state:break
  time.sleep(.1)
 else:raise RuntimeError('Production worker did not reach stopped state; no pilot calculations launched')
 save(R/'state/common69-pilot-worker-pause.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':pid,'state':state,'purpose':'One serial DEP solvent/composition pilot; production worker will resume in finally','script_sha256':sha(__file__)})
 save(D/'provenance.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'config':CONFIG,'solute':solute,'solute_inchikey':r['inchikey'],'solute_name':r['input']['name'],'audit_sha256':sha(D/'availability.json'),'script_sha256':sha(__file__),'composition_basis':'Solvent mole fraction among solvent+water, excluding DEP; total mole fractions [x_DEP,(1-x_DEP)*f,(1-x_DEP)*(1-f)]','fractions':[i/10 for i in range(11)],'interpretation':'Homogeneous liquid branch feasibility only, no phase stability or liquid-liquid equilibrium calculation; no mixture concentration conversion without mixture molar volume'})
 w=activity(solute,water);save(D/'water-activity.json',w);assert w['status']=='converged'
 pure=[];grid=[]
 for s in solvents:
  key=s['common_key'];solv={'solvent_key':key,'surface':s['surface'],'surface_sha256':s['surface_sha256']}
  a=activity(solute,solv);save(D/'pure'/(key+'.json'),a)
  row={'solvent':key,'status':a['status'],'temperature_K':298.15,'wall_seconds':a['wall_seconds'],'ln_gamma':a.get('ln_gamma'),'dilution_shift_log10':a.get('last_log10_dilution_shift')}
  volume=ov if key=='1-octanol' else volumes.get(s.get('panel_key'))
  if a['status']=='converged':
   row['logKx_water_to_solvent']=(w['ln_gamma']-a['ln_gamma'])/math.log(10)
   if volume:row['logKconc_water_to_solvent']=row['logKx_water_to_solvent']+math.log10(18.07/volume['molar_volume_cm3_mol'])
  row['concentration_basis_status']='documented_volume_available' if volume else 'volume_not_yet_documented_in_pilot'
  pure.append(row);emit({'pure_solvent':key,'status':row['status']})
  for fraction in [i/10 for i in range(11)]:
   t=time.monotonic();g={'solvent':key,'water_cosolvent_fraction':1-fraction,'organic_cosolvent_fraction':fraction,'temperature_K':298.15,'samples':[],'status':'failed'}
   try:
    engine=COSMORS(openCOSMORS24a())
    for p in [solute['surface'],solv['surface'],water['surface']]:engine.add_molecule([p])
    for x in [1e-5,1e-6,1e-7,1e-8]:
     comp=np.array([x,(1-x)*fraction,(1-x)*(1-fraction)]);assert abs(comp.sum()-1)<1e-12
     engine.add_job(x=comp,T=298.15,refst='pure_component');v=engine.calculate()['tot']['lng'][-1];assert np.isfinite(v).all()
     g['samples'].append({'x_DEP':x,'composition':comp.tolist(),'ln_gamma':v.tolist()})
     if len(g['samples'])>1:
      shift=abs(v[0]-g['samples'][-2]['ln_gamma'][0])/math.log(10);g['dilution_shift_log10']=shift
      if shift<=.005:
       g.update(status='converged',ln_gamma_DEP=float(v[0]),selected_x_DEP=x,log_activity_ratio_vs_pure_water=(w['ln_gamma']-float(v[0]))/math.log(10));break
    else:g['status']='dilution_not_converged'
    if fraction in [0,1] and g['status']=='converged':
     ref=w if fraction==0 else a
     if ref['status']=='converged':g['endpoint_delta_log10']=abs(g['ln_gamma_DEP']-ref['ln_gamma'])/math.log(10)
   except Exception as exc:g['error']=str(exc)
   g['wall_seconds']=time.monotonic()-t;save(D/'mixtures'/f'{key}-{fraction:.1f}.json',g);grid.append({k:v for k,v in g.items() if k!='samples'});del engine;gc.collect()
  csvsave(D/'pure-solvents.csv',pure);csvsave(D/'composition-grid.csv',grid)
  emit({'completed_solvents':len(pure),'grid_points':len(grid),'grid_failed':sum(g['status']!='converged' for g in grid),'wall_seconds':time.monotonic()-start})
 save(D/'pilot-summary.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'common_denominator':69,'surface_available':len(solvents),'pure_converged':sum(x['status']=='converged' for x in pure),'pure_failed':sum(x['status']!='converged' for x in pure),'mixture_points':len(grid),'mixture_converged':sum(x['status']=='converged' for x in grid),'mixture_failed':sum(x['status']!='converged' for x in grid),'endpoint_max_delta_log10':max(x.get('endpoint_delta_log10',0) for x in grid),'wall_seconds':time.monotonic()-start,'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
finally:
 if paused:
  os.kill(pid,signal.SIGCONT);save(R/'state/common69-pilot-worker-resume.json',{'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':pid,'signal':'SIGCONT','pilot_wall_seconds':time.monotonic()-start})
