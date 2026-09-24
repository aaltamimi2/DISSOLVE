"""Independent algebra, identity, grid and coverage checks on verified returns.

This checks arithmetic/provenance, not experimental accuracy. No COSMO calculation
or scheduler mutation is performed. Results are streamed one contaminant at a time.
"""
import collections,datetime,gzip,hashlib,json,math
from pathlib import Path
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase83-v1');B=D.parent/'phase8-v1'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
 cohort=json.loads((D/'cohort.json').read_text());manifest=json.loads((B/'manifest.json').read_text());validation=json.loads((B/'validation-inputs.json').read_text());cohort_sha=sha(D/'cohort.json')
 collection=json.loads((R/'state/phase83-v1/collection.json').read_text())['collected']
 expected={(p,s['name'],c) for p in manifest['polymers'] for s in manifest['solvents'] for c in ['normalized','existing']}
 temperatures={(u['solvent'],u['regime']):u['temperature_K'] for u in validation['lle_units']}
 solvent_sha={s['name']:sha(B/s['B']) for s in manifest['solvents']}
 counts=collections.Counter();maxima=collections.defaultdict(float);cpu=collections.Counter();issues=[]
 for key,record in sorted(collection.items()):
  p=D/'collected'/(key+'.json.gz');assert sha(p)==record['metadata_sha256'],p
  data=json.load(gzip.open(p,'rt'));c=cohort['rows'][int(key)];footer=data['complete']
  assert footer['signature']['cohort_sha256']==cohort_sha
  assert footer['inchikey']==c['inchikey'] and footer['index']==int(key)
  model=footer['execution']['cpu_model'];assert model=='AMD EPYC 7763 64-Core Processor',model;cpu[model]+=1
  assert len(data['partition'])==len(expected)==640
  assert {(r['polymer'],r['solvent'],r['convention']) for r in data['partition']}==expected
  for row in data['partition']:
   assert row['inchikey']==c['inchikey'] and row['solute_surface_sha256']==c['surface_sha256']
   assert row['solvent_surface_sha256']==solvent_sha[row['solvent']]
   assert row['polymer_ensemble_manifest_sha256']==cohort['phase81_manifest_sha256'] and float(row['temperature_K'])==298.15
   counts['partition_'+row['status']]+=1
   if row['status']!='predicted':
    assert row.get('failure_mode');issues.append({'index':int(key),'quantity':'partition','polymer':row['polymer'],'solvent':row['solvent'],'convention':row['convention'],'status':row['status'],'reason':row['failure_mode']});continue
   lp=float(row['logP_x']);lc=float(row['logP_concentration']);gp=float(row['ln_gamma_polymer']);gs=float(row['ln_gamma_solvent']);vp=float(row['polymer_volume_cm3_mol']);vs=float(row['solvent_volume_cm3_mol'])
   assert all(math.isfinite(v) for v in [lp,lc,gp,gs,vp,vs]) and min(vp,vs)>0
   gamma_error=abs(lp-(gp-gs)/math.log(10));volume_error=abs(lc-lp-math.log10(vp/vs))
   assert max(gamma_error,volume_error)<1e-11
   maxima['partition_activity_equation_error_log10']=max(maxima['partition_activity_equation_error_log10'],gamma_error)
   maxima['partition_volume_equation_error_log10']=max(maxima['partition_volume_equation_error_log10'],volume_error)
  assert len(data['lle'])==len(temperatures)==64
  assert {(r['solvent'],r['regime']) for r in data['lle'].values()}==set(temperatures)
  for unit,row in data['lle'].items():
   assert int(key)*64<=int(unit)<(int(key)+1)*64
   assert row['solute']==c['inchikey'] and row['temperature_K']==temperatures[(row['solvent'],row['regime'])]
   assert footer['lle_statuses'][unit]==row['status'];counts['lle_'+row['status']]+=1
   if row['status'] not in ['single_liquid_phase','two_liquid_phases']:
    issues.append({'index':int(key),'quantity':'LLE','solvent':row['solvent'],'temperature_K':row['temperature_K'],'status':row['status'],'reason':row.get('error',row.get('issues',row['status']))});continue
   sig=row['signature'];assert sig['solute_sha256']==c['surface_sha256'] and sig['solvent_sha256']==solvent_sha[row['solvent']]
   assert row['solver_sha256']==footer['signature']['solver_sha256']
   x=row['solute_mole_fraction_solubility'];wt=row['solute_wt_percent_solubility'];mw=c['molecular_weight_g_mol'];ms=validation['solvent_identities'][row['solvent']]['molecular_weight_g_mol']
   assert -1e-12<=x<=1+1e-12 and -1e-10<=wt<=100+1e-10
   mass_error=abs(wt-100*x*mw/(x*mw+(1-x)*ms));assert mass_error<1e-10
   maxima['LLE_mass_conversion_error_percentage_points']=max(maxima['LLE_mass_conversion_error_percentage_points'],mass_error)
   grids=row['grid_checks'];assert [g['grid_intervals'] for g in grids]==[1000,2000]
   assert all(g['status']==row['status'] for g in grids)
   dx=100*abs(grids[0]['solute_mole_fraction_solubility']-grids[1]['solute_mole_fraction_solubility']);dw=abs(grids[0]['solute_wt_percent_solubility']-grids[1]['solute_wt_percent_solubility'])
   assert max(dx,dw)<=.01 and abs(dx-row['grid_change_mol_percentage_points'])<1e-10 and abs(dw-row['grid_change_wt_percentage_points'])<1e-10
   for g in grids:
    for tie in g['tie_lines']:
     assert 0<tie['x_solvent_rich']<tie['x_solute_rich']<1
     assert tie['chemical_potential_residual']<=1e-7 and tie['minimum_tangent_distance_RT']>=-1e-7
   for basis,value in [('mol',100*x),('wt',wt)]:assert row['above_15_'+basis+'_percent']==(None if abs(value-15)<=.01 else value>15)
   if row['status']=='single_liquid_phase':assert not row['tie_lines'] and abs(x-1)<1e-12
   else:assert row['tie_lines'] and abs(x-row['tie_lines'][0]['x_solvent_rich'])<1e-12
 result={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cohort_sha256':sha(D/'cohort.json'),'auditor_sha256':sha(Path(__file__)),
  'audited_contaminants':len(collection),'denominator':len(cohort['rows']),'status':'passed_for_collected_subset','counts':dict(counts),'maximum_arithmetic_errors':dict(maxima),'cpu_models':dict(cpu),
  'scope':'Checks complete keys, input/surface identity, CPU generation, partition sign and volume equations, LLE mass conversion, both grid checks, tie-line residual/stability checks and threshold arithmetic. This is not experimental validation or an independent rerun of activity coefficients.',
  'scientific_unresolved_or_failed_rows':issues}
 out=D/'collected-audit.json';tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps(result,indent=2)+'\n');tmp.replace(out)
 print(json.dumps({k:v for k,v in result.items() if k!='scientific_unresolved_or_failed_rows'},indent=2))
 return result
if __name__=='__main__':main()
