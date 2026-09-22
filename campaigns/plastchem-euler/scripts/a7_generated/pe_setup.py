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
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7/pe');D.mkdir(parents=True,exist_ok=True)
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
