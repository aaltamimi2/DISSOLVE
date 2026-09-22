import math,json
from pathlib import Path
from unittest.mock import patch
import numpy as np
import thermodynamic_prediction as t
from thermodynamic_library import registry
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1'
values={k:{'status':'converged','ln_gamma':v,'last_log10_dilution_shift':0,'solvent_surface_sha256':'test'} for k,v in [('water',7),('hexane',2),('methanol',4)]}
ab=t.pair('hexane','water',values);assert abs(ab['log10_K_mole_fraction']-5/math.log(10))<1e-14
assert abs(ab['log10_K_concentration']-(5/math.log(10)+math.log10(18.07/131.6)))<1e-14
assert t.pair('missing','water',values)['status']=='not_available'
values['methanol']['status']='failed';assert t.pair('methanol','water',values)['status']=='not_available';values['methanol']['status']='converged'
v=t.validation(values);assert v['antisymmetry_max_log10']<1e-14 and v['cycle_closure_max_log10']<1e-14
water=next(s for s in registry()['solvents'] if s['solvent_key']=='water');real=t.activity(water,water);assert real['status']=='converged' and abs(real['ln_gamma'])<1e-8
solute_file=ROOT/'state/opencosmo-verification-v1/dbp-workstation.orcacosmo'
import hashlib
solute={'surface':str(solute_file),'surface_sha256':hashlib.sha256(solute_file.read_bytes()).hexdigest()}
f=ROOT/'state/opencosmo-verification-v1/solvents/water.orcacosmo';solvent={'solvent_key':'water','surface':str(f),'surface_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
replay=t.activity(solute,solvent);gold=json.loads((ROOT/'state/opencosmo-verification-v1/DBP-golden.json').read_text());assert abs(replay['samples'][0]['ln_gamma']-gold['ln_gamma_inf']['water'])<1e-10
class Nonconverging:
 def __init__(self,*a):self.n=0
 def add_molecule(self,*a):pass
 def add_job(self,*a,**kw):self.n+=1
 def calculate(self):return {'tot':{'lng':np.array([[float(self.n)]])}}
with patch.object(t,'COSMORS',Nonconverging):bad=t.activity(water,water)
assert bad['status']=='dilution_not_converged' and 'ln_gamma' not in bad
class SolverFailure(Nonconverging):
 def calculate(self):raise ValueError('COSMOspace did not converge')
with patch.object(t,'COSMORS',SolverFailure):badsolver=t.activity(water,water)
assert badsolver['status']=='failed' and 'ln_gamma' not in badsolver
result={'analytic_sign_and_volume_conversion':True,'failed_or_missing_activity_cannot_produce_partition':True,'antisymmetry_and_cycle_closure':True,'actual_Milan_water_self_ln_gamma':real['ln_gamma'],'actual_historical_DBP_water_replay_error':replay['samples'][0]['ln_gamma']-gold['ln_gamma_inf']['water'],'dilution_failure_withholds_final_value':True,'solver_failure_withholds_final_value':True}
(P/'prediction-checks.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
