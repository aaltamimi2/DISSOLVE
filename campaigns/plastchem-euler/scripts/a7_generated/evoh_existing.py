"""Reproduce the read-only product formula with cached A-6 activities; no extra calculations."""
from pathlib import Path
import csv,json,math,importlib.util,sys,functools
import numpy as np
from scipy.special import logsumexp
R=Path('/home/aaltamimi2/plastchem-euler');D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7/evoh')
assert (D/'summary.json').exists(),'Main comparison must finish first'
p=Path('/home/aaltamimi2/dissolve-v12-builder-1/src/dissolve/polymer_cosmo.py');spec=importlib.util.spec_from_file_location('pe_existing',p);ref=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ref;spec.loader.exec_module(ref)
# Memoize pure reads, retaining the reference function and its numerical implementation.
ref._as_turbomole_polymer=functools.lru_cache(None)(ref._as_turbomole_polymer)
ref._cavity_molar_volume_cm3=functools.lru_cache(None)(ref._cavity_molar_volume_cm3)
inputs=json.loads((D/'inputs.json').read_text());ensemble=list(csv.DictReader((D/'pe-ensemble.csv').open()));models=[m for m in json.loads((R/'state/polymer-v1/body/manifest.json').read_text())['molecules'] if m['polymer']=='evoh'];polymers=[D/'converted-A'/(m['conformer_id']+'.cosmo') for m in models]
rows=[]
for solute in inputs['solutes']:
 cached={}
 for i,p in enumerate(polymers):cached[str(p)]=json.loads((D/'activities'/f'A-{solute}-pe-{i:02}.json').read_text())['samples'][0]['ln_gamma']
 def gamma(solute_path,phase,**kwargs):return cached[str(phase)]
 bacts=[json.loads((D/'activities'/f'B-{solute}-pe-{i:02}.json').read_text()) for i in range(11)]
 bes=[float(r['B_energy_hartree']) for r in ensemble];rel=[(e-min(bes))*ref.HARTREE_TO_KCAL for e in bes];bw=ref._cl.boltzmann_weights(rel);blng=ref.boltzmann_combine([a['samples'][0]['ln_gamma'] for a in bacts],rel);bvp=sum(w*float(r['B_cavity_cm3_mol']) for w,r in zip(bw,ensemble))
 for solvent in inputs['solvents']:
  name=solvent['name'];activity=json.loads((D/'activities'/f'A-{solute}-solvent-{name}.json').read_text());cached[solvent['A']]=activity['samples'][0]['ln_gamma']
  a=ref.compute_log10_p_solvent_over_polymer(inputs['solutes'][solute]['A'],solvent['A'],polymers,polymer_name='evoh',solvent_key=name,ln_gamma=gamma,energies_hartree=[float(r['A_energy_hartree']) for r in ensemble])
  bsol=json.loads((D/'activities'/f'B-{solute}-solvent-{name}.json').read_text())['samples'][0]['ln_gamma']
  if name in ref.MOLAR_VOLUMES_CM3:bvs=ref.MOLAR_VOLUMES_CM3[name];bsource='existing product table'
  else:
   from opencosmorspy.input_parsers import SigmaProfileParser
   bvs=float(SigmaProfileParser(solvent['B'])['volume'])*.602214076;bsource='route B COSMO cavity'
  bx=(blng-bsol)/math.log(10);bv=bx+math.log10(bvp/bvs);av=a['log10_p_solvent_over_polymer']
  rows.append({'solute':solute.upper(),'solvent':name,'A_existing_product_logP_concentration':av,'B_same_convention_logP_concentration':bv,'delta_B_minus_A':bv-av,'A_logP_x':(a['ln_gamma_polymer']-a['ln_gamma_solvent'])/math.log(10),'B_logP_x':bx,'sign_changed':av*bv<0,'A_solvent_volume_cm3_mol':a['volume_solvent_cm3'],'B_solvent_volume_cm3_mol':bvs,'A_volume_source':a['volume_source_solvent'],'B_volume_source':bsource,'A_PE_volume_cm3_mol':a['volume_polymer_cm3'],'B_PE_volume_cm3_mol':bvp})
with (D/'existing-convention-comparison.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
metrics={}
for solute in ['all','DEP','DBP','BBP','DEHP']:
 rr=[r for r in rows if solute=='all' or r['solute']==solute];x=np.array([r['A_existing_product_logP_concentration'] for r in rr]);y=np.array([r['B_same_convention_logP_concentration'] for r in rr]);slope,intercept=np.polyfit(x,y,1);res=y-slope*x-intercept
 metrics[solute]={'n':len(rr),'slope':float(slope),'intercept':float(intercept),'residual_sd_n_minus_2':float(np.sqrt(sum(res*res)/(len(rr)-2))),'sign_changed_pairs':sum(r['sign_changed'] for r in rr),'sign_changed_solvents':sorted({r['solvent'] for r in rr if r['sign_changed']}),'largest_disagreements':sorted(rr,key=lambda r:abs(r['delta_B_minus_A']),reverse=True)[:5]}
(D/'existing-convention-summary.json').write_text(json.dumps(metrics,indent=2)+'\n')
with (D/'REPORT.md').open('a') as f:
 f.write('\n## Exact existing-product convention\n\n`existing-convention-comparison.csv` uses the unmodified read-only product `compute_log10_p_solvent_over_polymer` for route A, supplied with the calculated x=1e−5 activity coefficients. Its five-entry volume table and cavity fallback are preserved. Route B applies the same aggregation and volume-selection convention to the ORCA surfaces. This is the direct comparison to the existing implementation; `route-comparison.csv` separately provides normalized weights, dilution-checked activities and the larger documented campaign volume table. Their numbers must not be silently interchanged.\n\n')
 for name,m in metrics.items():f.write(f"{name}: n={m['n']}, slope={m['slope']:.6f}, intercept={m['intercept']:.6f}, residual SD={m['residual_sd_n_minus_2']:.6f}; sign changes={m['sign_changed_pairs']} pairs across {len(m['sign_changed_solvents'])} solvents.\n\n")
 f.write('Named differences and sign changes are in existing-convention-summary.json. Reproduce with `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/pe_a6_existing_convention.py` after the main comparison.\n')
print(json.dumps(metrics['all']),flush=True)
