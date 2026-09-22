"""Bounded A-6 comparison. No cluster, catalog or product writes. Serial resumable activities."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,json,math,hashlib,datetime,importlib.util,sys,fcntl,time
from pathlib import Path
import numpy as np
from scipy.special import logsumexp
from opencosmorspy import COSMORS,Parameterization
from opencosmorspy.parameterization import openCOSMORS24a
from opencosmorspy.input_parsers import SigmaProfileParser
from thermodynamic_prediction import refresh_volumes
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a6');D.mkdir(parents=True,exist_ok=True)
lock=(R/'state/thermodynamics-v1/worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
def save(p,x):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def csvwrite(p,rows):
 with p.open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)));w.writeheader();w.writerows(rows)
refpath=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/polymer_cosmo.py');spec=importlib.util.spec_from_file_location('pe_reference',refpath);ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
LIB=Path('/home/aaltamimi2/COSMO-POLYMER-ML/results/oligomers/all-cosmotherm-solvents')
models=[m for m in json.loads((R/'state/polymer-v1/body/manifest.json').read_text())['molecules'] if m['polymer']=='pe'];assert len(models)==31
T=298.15;RT=0.00831446261815324*T;EH=2625.4996394799
ensemble=[];surfaces={'A':[],'B':[]};pins={str(refpath):sha(refpath),str(refpath.with_name('cosmo_logp.py')):sha(refpath.with_name('cosmo_logp.py'))}
for m in models:
 key=m['entry_id'];src=Path('/home/aaltamimi2/polymers_cosmo')/m['source_file'];assert sha(src)==m['source_sha256']
 target=D/'converted-A'/(m['conformer_id']+'.cosmo');converted=ref.convert_gaussian_cosmo(src,target);a=ref.split_mcos(src)[0]
 record=json.loads((R/'state/polymer-v1/records'/(key+'.json')).read_text());assert record['status']=='converged'
 b=Path(record['archive_path'])/'surface.orcacosmo';assert sha(b)==record['surface_sha256']
 pa=SigmaProfileParser(str(target));pb=SigmaProfileParser(str(b))
 ensemble.append({'entry_id':key,'A_energy_hartree':a.energy_hartree,'B_energy_hartree':record['cosmo_solute_energy_hartree'],'B_OPT_energy_hartree':float(record['stages']['opt']['final_energies_hartree'][-1]),'A_cavity_cm3_mol':float(pa['volume'])*.602214076,'B_cavity_cm3_mol':float(pb['volume'])*.602214076,'A_surface_sha256':sha(target),'B_surface_sha256':sha(b)})
 surfaces['A'].append(target);surfaces['B'].append(b)
 for p in [src,target,b]:pins[str(p)]=sha(p)
weights={};energy_summary={}
for route in ['A','B','B_OPT']:
 es=np.array([r[route+'_energy_hartree'] for r in ensemble]);de=(es-es.min())*EH;w=np.exp(-de/RT);z=float(w.sum());w/=z;weights[route]=w
 for row,e,weight in zip(ensemble,de,w):row[route+'_relative_energy_kj_mol']=float(e);row[route+'_weight_298K']=float(weight)
 energy_summary[route]={'n90':int(np.searchsorted(np.cumsum(sorted(w,reverse=True)),.9)+1),'partition_sum_relative_to_minimum':z,'energy_definition':{'A':'Gaussian COSMO corrected total energy from source mcos','B':'ORCA COSMORS solute energy from surface','B_OPT':'ORCA gas-phase OPT electronic energy; sensitivity basis'}[route]}
csvwrite(D/'pe-ensemble.csv',ensemble)
registry=json.loads((R/'state/thermodynamics-v1/library-registry.json').read_text());volumes=refresh_volumes();solvents=[];dropped=[]
for row in registry['solvents']:
 if row['status']!='ready':dropped.append({'solvent':row['solvent_key'],'reason':'not in resolved campaign references'});continue
 a=ref.cosmotherm_file_for(row['solvent_key'],solvents_dir=LIB)
 if a is None:dropped.append({'solvent':row['solvent_key'],'reason':'no exact COSMObase mapping'});continue
 b=Path(row['surface']);assert sha(b)==row['surface_sha256'];item={'name':row['solvent_key'],'A':str(a),'B':str(b)}
 for route,p in [('A',a),('B',b)]:
  pins[str(p)]=sha(p)
  if row['solvent_key'] in volumes:item[route+'_volume']=volumes[row['solvent_key']]['molar_volume_cm3_mol'];item[route+'_volume_source']='documented campaign table'
  else:item[route+'_volume']=float(SigmaProfileParser(str(p))['volume'])*.602214076;item[route+'_volume_source']='route-specific COSMO cavity'
 solvents.append(item)
assert len(solvents)==32
solutes={k:{'A':LIB/name,'B':R/'state/opencosmo-verification-v1'/f'{k}-workstation.orcacosmo'} for k,name in [('dep','diethylphthalate_c0.cosmo'),('dbp','dibutylphthalate_c0.cosmo'),('bbp','butylbenzylphthalate_c0.cosmo'),('dehp','di-2-ethylhexylphthalate_c0.cosmo')]}
for paths in solutes.values():
 for p in paths.values():pins[str(p)]=sha(p)
save(D/'inputs.json',{'solvents':solvents,'dropped':dropped,'solutes':{k:{r:str(p) for r,p in v.items()} for k,v in solutes.items()},'pins':pins,'energy_summary':energy_summary,'temperature_K':T})
def activity(route,solute,phase,label):
 path=D/'activities'/f'{route}-{solute}-{label}.json';sig={'route':route,'solute_sha256':sha(solutes[solute][route]),'phase_sha256':sha(phase)}
 if path.exists():
  v=json.loads(path.read_text());assert v['signature']==sig;return v
 start=time.monotonic();v={'signature':sig,'samples':[],'temperature_K':T,'reference_state':'pure_component'}
 try:
  engine=COSMORS(openCOSMORS24a() if route=='B' else Parameterization('default_turbomole'));engine.add_molecule([str(solutes[solute][route])]);engine.add_molecule([str(phase)])
  for x in [1e-5,1e-6,1e-7,1e-8]:
   engine.add_job(x=np.array([x,1-x]),T=T,refst='pure_component');lng=float(engine.calculate()['tot']['lng'][-1][0]);assert math.isfinite(lng);v['samples'].append({'x':x,'ln_gamma':lng})
   if len(v['samples'])>1 and abs(lng-v['samples'][-2]['ln_gamma'])/math.log(10)<=.005:v.update(status='converged',ln_gamma=lng);break
  else:v.update(status='dilution_not_converged')
 except Exception as e:v.update(status='failed',error=str(e))
 v['wall_seconds']=time.monotonic()-start;save(path,v);print(json.dumps({'activity':path.name,'status':v['status']}),flush=True);return v
rows=[];failures=[]
for solute in solutes:
 calculated={}
 for route in ['A','B']:
  acts=[activity(route,solute,p,f'pe-{i:02}') for i,p in enumerate(surfaces[route])]
  complete=all(a['status']=='converged' for a in acts)
  w=weights[route];vp=float(sum(w[i]*ensemble[i][route+'_cavity_cm3_mol'] for i in range(31)))
  if complete:
   gamma=-float(logsumexp(np.log(w)-np.array([a['ln_gamma'] for a in acts])))
   legacy=-float(logsumexp(np.log(w)-np.array([a['samples'][0]['ln_gamma'] for a in acts])))-math.log(energy_summary[route]['partition_sum_relative_to_minimum'])
   optgamma=-float(logsumexp(np.log(weights['B_OPT'])-np.array([a['ln_gamma'] for a in acts]))) if route=='B' else None
  for solvent in solvents:
   a=activity(route,solute,Path(solvent[route]),'solvent-'+solvent['name'].replace('/','_'));key=solvent['name']
   value={'status':'not_available','solvent_volume_cm3_mol':solvent[route+'_volume'],'solvent_volume_source':solvent[route+'_volume_source'],'PE_volume_cm3_mol':vp,'PE_volume_source':'Boltzmann-weighted route-specific COSMO cavity'}
   if complete and a['status']=='converged':
    kx=(gamma-a['ln_gamma'])/math.log(10);corr=math.log10(vp/solvent[route+'_volume']);value.update(status='predicted',logP_x=kx,logP_concentration=kx+corr,legacy_unnormalized_x1e5_logP_concentration=(legacy-a['samples'][0]['ln_gamma'])/math.log(10)+corr)
    if route=='B':value['OPT_weight_sensitivity_logP_concentration']=(optgamma-a['ln_gamma'])/math.log(10)+math.log10(sum(weights['B_OPT'][i]*ensemble[i]['B_cavity_cm3_mol'] for i in range(31))/solvent['B_volume'])
   else:failures.append({'solute':solute,'route':route,'solvent':key,'solvent_activity_status':a['status'],'PE_ensemble_complete':complete})
   calculated[(route,key)]=value
 for solvent in solvents:
  name=solvent['name'];a=calculated['A',name];b=calculated['B',name];row={'solute':solute.upper(),'solvent':name}
  for route,v in [('A',a),('B',b)]:row.update({route+'_'+k:val for k,val in v.items()})
  if a['status']==b['status']=='predicted':row.update(delta_B_minus_A=b['logP_concentration']-a['logP_concentration'],delta_B_minus_A_x=b['logP_x']-a['logP_x'],sign_changed=a['logP_concentration']*b['logP_concentration']<0)
  rows.append(row)
 csvwrite(D/'route-comparison.csv',rows)
metrics={}
for key in ['all','DEP','DBP','BBP','DEHP']:
 rr=[r for r in rows if 'delta_B_minus_A' in r and (key=='all' or r['solute']==key)];x=np.array([r['A_logP_concentration'] for r in rr]);y=np.array([r['B_logP_concentration'] for r in rr]);slope,intercept=np.polyfit(x,y,1);res=y-(slope*x+intercept)
 metrics[key]={'n':len(rr),'slope':float(slope),'intercept':float(intercept),'residual_sd_n_minus_2':float(np.sqrt(np.sum(res**2)/(len(rr)-2))),'sign_changed_pairs':sum(r['sign_changed'] for r in rr),'sign_changed_solvents':sorted({r['solvent'] for r in rr if r['sign_changed']}),'largest_disagreements':[{'solute':r['solute'],'solvent':r['solvent'],'A':r['A_logP_concentration'],'B':r['B_logP_concentration'],'delta':r['delta_B_minus_A']} for r in sorted(rr,key=lambda r:abs(r['delta_B_minus_A']),reverse=True)[:5]]}
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'intersection':len(solvents),'comparison_pairs':len(rows),'paired_predictions':metrics['all']['n'],'failures':failures,'metrics':metrics,'energy_summary':energy_summary,'dropped':dropped,'confound':'Routes differ simultaneously in QC engine, parameterisation and re-optimised geometry. This measures the entire route; no parameterisation-only attribution is possible.'}
save(D/'summary.json',summary)
report='# PE route comparison — A-6\n\n'+summary['confound']+'\n\n31 identical source conformer identities on both routes; all 31 retained, including the five ORCA optimisation merges reported in ../pe-ensemble-20260921/summary.json. Temperature 298.15 K. Four phthalates × 32 exact matched solvents = 128 comparisons. Generic xylene is unresolved and outside the 32 resolved references; no resolved reference was dropped.\n\nPrimary ensemble: normalized Boltzmann weights from each route’s COSMO corrected/solute electronic energies, with ln gamma_PE = −log(sum(w_i exp(−ln gamma_i))). No vibrational free energies or degeneracy correction. Volumes use the documented campaign table when available; otherwise route-specific cavity volume × 0.602214076 cm³/mol per Å³. PE volume is the Boltzmann-weighted cavity volume. Per-phase sources are recorded in the CSV and inputs.json. The concentration basis is logP_x + log10(V_PE/V_solvent).\n\nThe existing product helper uses an **unnormalized** conformer partition sum and x=1e−5. Its exact convention is supplied in the legacy_unnormalized_x1e5 columns, rather than silently equated with the normalized Boltzmann average. Production dilution checks use 1e−5 through 1e−8 with a 0.005-log-unit plateau criterion; raw samples are retained. Route B also supplies gas-phase OPT-energy weighting as a sensitivity column, consistent with the earlier PE ensemble report’s energy basis.\n\n'
for key,m in metrics.items():report+=f"{key}: n={m['n']}; B-on-A slope {m['slope']:.6f}, intercept {m['intercept']:.6f}, residual SD (n−2) {m['residual_sd_n_minus_2']:.6f}; {m['sign_changed_pairs']} sign-changing pairs across {len(m['sign_changed_solvents'])} solvents.\n\n"
report+='Largest disagreements and every sign-changing solvent are named in summary.json. The 31 relative energies and weights are in pe-ensemble.csv; 90%-weight counts are '+json.dumps(energy_summary)+'.\n\nFailures: '+json.dumps(failures)+'.\n\nReproduce with `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/compare_pe_routes_a6.py`. Activity files are resumable and input-hash bound. No catalog writes, threshold, product changes, or empirical recalibration.\n'
(D/'REPORT.md').write_text(report);(D/'compare_pe_routes_a6.py').write_bytes(Path(__file__).read_bytes());print('COMPLETE '+json.dumps(summary),flush=True)
