"""Neutral-solute thermodynamics; explicit standard states, dilution checks and missing volumes."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import hashlib,json,math,time
from pathlib import Path
import numpy as np
from opencosmorspy import COSMORS
from opencosmorspy.parameterization import openCOSMORS24a
CONFIG={'parameterization':'openCOSMORS24a','temperature_K':298.15,'reference_state':'pure_component','initial_solute_fractions':[1e-5,1e-6],'minimum_solute_fraction':1e-8,'single_activity_log10_dilution_tolerance':0.005,'pair_log10_dilution_tolerance':0.01,'neutral_solute_only':True}
# Frozen values from the existing phthalate implementation; unavailable values remain missing.
VOLUMES={'water':18.07,'methanol':40.7,'dichloromethane':64.0,'hexane':131.6,'cyclohexanol':106.0}
VOLUME_DATA={k:{'molar_volume_cm3_mol':v,'source':'Frozen existing phthalate reference implementation','temperature_note':'Approximate 298 K values; uncertainty not specified'} for k,v in VOLUMES.items()}
def refresh_volumes():
 path=Path(__file__).resolve().parents[1]/'state/thermodynamics-v1/additional-molar-volumes.json'
 if path.exists():
  additions=json.loads(path.read_text())
  assert additions['status']=='verified_primary_density_additions'
  for key,row in additions['entries'].items():
   assert row['temperature_K']==298.15 and row['molar_volume_cm3_mol']>0
   assert hashlib.sha256(Path(row['source_xml_path']).read_bytes()).hexdigest()==row['source_xml_sha256']
   assert key not in ['water','methanol','dichloromethane','hexane','cyclohexanol']
   VOLUMES[key]=row['molar_volume_cm3_mol'];VOLUME_DATA[key]=row
 supplier=path.with_name('supplier-molar-volumes.json')
 if supplier.exists():
  additions=json.loads(supplier.read_text())
  assert additions['status']=='verified_supplier_literature_density_additions'
  inventory={s['solvent_key']:s for s in json.loads(path.with_name('solvent-inventory.json').read_text())['solvents']}
  for key,row in additions['entries'].items():
   assert key not in ['water','methanol','dichloromethane','hexane','cyclohexanol']
   assert key not in VOLUME_DATA or VOLUME_DATA[key].get('source_class')=='supplier_literature_value'
   raw=Path(row['source_evidence_path']).read_bytes()
   assert hashlib.sha256(raw).hexdigest()==row['source_evidence_sha256']
   evidence=json.loads(raw)
   assert row['source_class']==evidence['source_class']=='supplier_literature_value'
   assert row['source_inchikey']==evidence['inchikey']==inventory[key]['connectivity_inchikey']
   assert row['temperature_K']==evidence['temperature_K']==298.15
   assert row['density_g_cm3']==evidence['density_g_cm3']>0
   assert math.isfinite(row['molar_volume_cm3_mol']) and row['molar_volume_cm3_mol']>0
   assert math.isclose(row['molar_volume_cm3_mol'],row['molar_mass_g_mol']/row['density_g_cm3'],rel_tol=1e-12)
   VOLUMES[key]=row['molar_volume_cm3_mol'];VOLUME_DATA[key]=row
 return VOLUME_DATA

def activity(solute,solvent):
 start=time.monotonic();r={'solute_surface_sha256':solute['surface_sha256'],'solvent_surface_sha256':solvent['surface_sha256'],'solvent_key':solvent['solvent_key'],'config':CONFIG,'samples':[]}
 try:
  for item in [solute,solvent]:assert hashlib.sha256(open(item['surface'],'rb').read()).hexdigest()==item['surface_sha256'],'Surface checksum changed'
  engine=COSMORS(openCOSMORS24a());engine.add_molecule([solute['surface']]);engine.add_molecule([solvent['surface']])
  for x in [1e-5,1e-6,1e-7,1e-8]:
   engine.add_job(x=np.array([x,1-x]),T=CONFIG['temperature_K'],refst=CONFIG['reference_state'])
   v=float(engine.calculate()['tot']['lng'][-1][0]);assert math.isfinite(v),'Nonfinite activity coefficient'
   r['samples'].append({'solute_fraction':x,'ln_gamma':v})
   if len(r['samples'])>1:
    shift=abs(v-r['samples'][-2]['ln_gamma'])/math.log(10);r['last_log10_dilution_shift']=shift
    if shift<=CONFIG['single_activity_log10_dilution_tolerance']:
     r.update(status='converged',ln_gamma=v,selected_solute_fraction=x);break
  else:r.update(status='dilution_not_converged',error='No verified infinite-dilution plateau within the bounded dilution grid')
 except Exception as exc:r.update(status='failed',error=str(exc))
 r['wall_seconds']=time.monotonic()-start;return r

def pair(a,b,values):
 av,bv=values.get(a),values.get(b)
 row={'solvent':a,'reference':b,'temperature_K':CONFIG['temperature_K'],'parameterization':CONFIG['parameterization']}
 if not av or not bv or av['status']!='converged' or bv['status']!='converged':return dict(row,status='not_available',reason='Both solvent activity coefficients must pass solver and dilution checks')
 x=(bv['ln_gamma']-av['ln_gamma'])/math.log(10)
 row.update(status='predicted',log10_K_mole_fraction=x,observed_dilution_shift_sum_log10=av['last_log10_dilution_shift']+bv['last_log10_dilution_shift'],solvent_surface_sha256=av['solvent_surface_sha256'],reference_surface_sha256=bv['solvent_surface_sha256'])
 if a in VOLUMES and b in VOLUMES:row.update(log10_K_concentration=x+math.log10(VOLUMES[b]/VOLUMES[a]),volume_correction_log10=math.log10(VOLUMES[b]/VOLUMES[a]),molar_volume_solvent_cm3_mol=VOLUMES[a],molar_volume_reference_cm3_mol=VOLUMES[b],volume_source={'solvent':VOLUME_DATA[a],'reference':VOLUME_DATA[b]})
 else:row.update(log10_K_concentration=None,concentration_basis_status='missing_documented_molar_volume')
 return row

def validation(values):
 names=sorted(k for k,v in values.items() if v['status']=='converged');anti=[];closure=[];conc=[]
 for a in names:
  for b in names:
   ab=pair(a,b,values);ba=pair(b,a,values);anti.append(abs(ab['log10_K_mole_fraction']+ba['log10_K_mole_fraction']))
   if ab['log10_K_concentration'] is not None:conc.append(abs(ab['log10_K_concentration']+ba['log10_K_concentration']))
 for i in range(max(0,len(names)-2)):
  a,b,c=names[0],names[i+1],names[i+2];closure.append(abs(pair(a,b,values)['log10_K_mole_fraction']+pair(b,c,values)['log10_K_mole_fraction']-pair(a,c,values)['log10_K_mole_fraction']))
 return {'activity_solvents_converged':len(names),'antisymmetry_max_log10':max(anti,default=None),'cycle_closure_max_log10':max(closure,default=None),'concentration_antisymmetry_max_log10':max(conc,default=None),'interpretation':'Algebraic/implementation consistency only; not independent chemical accuracy validation'}
