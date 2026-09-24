"""Workbook agreement, raw LLE and both ambiguous layouts; no source repair."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['MPLBACKEND']='Agg'
import csv,json,math,datetime,hashlib
from pathlib import Path
import numpy as np
from scipy.stats import linregress
D=Path('/mnt/r/plastchem-euler/phase8-v1');O=D/'reports/phase82';O.mkdir(parents=True,exist_ok=True);v=json.loads((D/'validation-inputs.json').read_text())
identities={}
for name,s in v['solutes'].items():
 key=s['input']['inchikey'];p=Path('/home/aaltamimi2/plastchem-euler/state/campaign-v1/records')/(key+'.json');assert hashlib.sha256(p.read_bytes()).hexdigest()==s['record_sha256'];r=json.loads(p.read_text());perceived=r.get('perceived_inchikey',r.get('inchikey_after_optimization'));assert perceived and key.split('-')[0]==perceived.split('-')[0]
 identities[name]={'input_inchikey':key,'perceived_inchikey':perceived,'identity_match_basis':'connectivity_first_block','perception_engines_agreeing':';'.join(r.get('perception_engines_agreeing_on_perceived_key',[])),'name':s['input']['name'],'smiles':s['input']['smiles'],'cas':s['input']['cas']}
def write(name,rows):
 with (O/name).open('w',newline='') as h:w=csv.DictWriter(h,fieldnames=list(dict.fromkeys(k for r in rows for k in r)) or ['status']);w.writeheader();w.writerows(rows)
def metrics(x,y):
 x=np.array(x);y=np.array(y)
 if len(x)<3:return {'n':len(x),'status':'insufficient_pairs'}
 fit=linregress(x,y);d=y-x
 return {'n':len(x),'MAE':float(np.mean(abs(d))),'RMSE':float(np.sqrt(np.mean(d*d))),'bias':float(np.mean(d)),'slope':float(fit.slope),'intercept':float(fit.intercept)}
def confusion(rows,truth,pred):
 valid=[r for r in rows if r.get(pred) is not None];tp=sum(r[truth] and r[pred] for r in valid);tn=sum(not r[truth] and not r[pred] for r in valid);fp=sum(not r[truth] and r[pred] for r in valid);fn=sum(r[truth] and not r[pred] for r in valid)
 return {'denominator':len(rows),'scored':len(valid),'unscored':len(rows)-len(valid),'true_positive':tp,'true_negative':tn,'false_positive':fp,'false_negative':fn,'agreement':(tp+tn)/len(valid) if valid else None}
raw=[]
for name in v['solutes']:
 p=D/'phase82/partition'/name/'predictions.csv'
 if p.exists():raw.extend(csv.DictReader(p.open()))
lookup={(r['solute'].lower(),r['solvent'],r['convention']):r for r in raw};paired=[];stats={};cms=[]
for c in ['normalized','existing']:
 for ref in v['logP_reference']:
  r=dict(ref,convention=c,reference_type='commercial COSMOtherm19 computed workbook value',reference_pass=ref['value']>0,predicted_pass=None,status='missing')
  p=lookup.get((ref['solute'].lower(),ref['solvent'],c))
  if p and p['status']=='predicted':r.update(status='predicted',predicted=float(p['logP_concentration']),logP_x=float(p['logP_x']),residual=float(p['logP_concentration'])-ref['value'],predicted_pass=float(p['logP_concentration'])>0,solute_surface_sha256=p['solute_surface_sha256'],solvent_surface_sha256=p['solvent_surface_sha256'])
  elif p:r['status']=p['status']
  r.update(identities[ref['solute']]);paired.append(r)
 rows=[r for r in paired if r['convention']==c];good=[r for r in rows if r['status']=='predicted'];stats[c]=metrics([r['value'] for r in good],[r['predicted'] for r in good]);cms.append(dict(quantity='logP_above_zero',convention=c,**confusion(rows,'reference_pass','predicted_pass')))
write('logP-workbook-parity.csv',paired);write('logP-worst-cases.csv',sorted([r for r in paired if 'residual' in r],key=lambda r:abs(r['residual']),reverse=True)[:40])
lle=[];by={};aggregate=json.loads((D/'phase82/lle-results.json').read_text()) if (D/'phase82/lle-results.json').exists() else {};failure_aggregate=json.loads((D/'phase82/lle-failures.json').read_text()) if (D/'phase82/lle-failures.json').exists() else {}
for idx,u in enumerate(v['lle_units']):
 p=D/'phase82/lle'/f'{idx:03d}'/'result.json';r=aggregate.get(f'{idx:03d}') or (json.loads(p.read_text()) if p.exists() else dict(u,status='failed' if f'{idx:03d}' in failure_aggregate else 'missing'))
 flat={k:value for k,value in r.items() if not isinstance(value,(dict,list))};flat['array_index']=idx;flat.update(identities[u['solute']]);lle.append(flat);by[u['solute'],u['solvent'],u['regime']]=r
write('lle-raw-solubilities.csv',lle);verdicts=[]
for ref in v['miscibility_reference']:
 p=by[ref['solute'],ref['solvent'],ref['regime']];literal=str(ref['literal_verdict']).strip().lower();assert literal in ['yes','no']
 r=dict(ref,reference_pass=literal=='yes',calculation_status=p['status'],predicted_15_mol_percent=p.get('above_15_mol_percent'),predicted_15_wt_percent=p.get('above_15_wt_percent'),solute_mole_fraction_solubility=p.get('solute_mole_fraction_solubility'),solute_wt_percent_solubility=p.get('solute_wt_percent_solubility'))
 verdicts.append(r)
for layout in ['blocked','paired']:
 for basis in ['mol','wt']:
  for regime in ['all','RT','high']:
   rows=[r for r in verdicts if r['layout']==layout and (regime=='all' or r['regime']==regime)];cms.append(dict(quantity='miscibility_above_15_percent',layout=layout,basis=basis,regime=regime,**confusion(rows,'reference_pass','predicted_15_'+basis+'_percent')))
write('miscibility-both-layouts-both-bases.csv',verdicts);write('verdict-confusion-matrices.csv',cms)
disagreements=[]
for r in verdicts:
 for basis in ['mol','wt']:
  pred=r['predicted_15_'+basis+'_percent']
  if pred is not None and pred!=r['reference_pass']:disagreements.append(dict(r,basis=basis,error_type='false_positive' if pred else 'false_negative'))
write('miscibility-disagreements.csv',disagreements)
# Literal header/regime keys have duplicate cells. Retain both values and expose
# the different assignments made by each layout instead of silently relabelling.
groups={}
for r in v['miscibility_reference']:
 if r['layout']=='blocked':groups.setdefault((r['solvent'],r['literal_header'],r['literal_regime_header']),[]).append(r)
conflicts=[]
for key,rs in groups.items():
 if len({str(r['literal_verdict']).lower() for r in rs})<2:continue
 row={'solvent':key[0],'literal_compound_header':key[1],'literal_regime_header':key[2],'literal_cells_values':json.dumps([{k:r[k] for k in ['cell','literal_verdict']} for r in rs])}
 for layout in ['blocked','paired']:
  cells={r['cell'] for r in rs};assigned=[r for r in verdicts if r['layout']==layout and r['solvent']==key[0] and r['cell'] in cells];row[layout+'_computed_assignments']=json.dumps([{k:r[k] for k in ['cell','solute','regime','temperature_K','literal_verdict','calculation_status','solute_mole_fraction_solubility','solute_wt_percent_solubility','predicted_15_mol_percent','predicted_15_wt_percent']} for r in assigned])
 conflicts.append(row)
assert len(conflicts)==14,len(conflicts);write('fourteen-literal-conflicts.csv',conflicts)
dehp=[]
for c in ['normalized','existing']:
 rows={r['solvent']:r for r in paired if r['solute']=='DEHP' and r['convention']==c and r['status']=='predicted'}
 for a,b in [('dichloromethane','water'),('cyclohexanol','water'),('hexane','water'),('dichloromethane','methanol')]:
  if a in rows and b in rows:dehp.append({'convention':c,'pair':a+'/'+b,'new_openCOSMO_logP':rows[a]['predicted']-rows[b]['predicted'],'workbook_logP_difference':rows[a]['value']-rows[b]['value'],'difference':(rows[a]['predicted']-rows[b]['predicted'])-(rows[a]['value']-rows[b]['value'])})
write('DEHP-water-pair-discrepancy.csv',dehp)
from collections import Counter
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'logP_metrics':stats,'confusion_matrices':cms,'lle_status_counts':dict(Counter(r['status'] for r in lle)),'lle_denominator':512,'literal_conflicts':14,'workbook_sha256':v['workbook_sha256']};(O/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
report='# Phase 8.2 — workbook validation\n\nThis is agreement with **computed commercial COSMOtherm workbook references**, not validation against measured partition coefficients. New calculations use neutral openCOSMO-RS 24a, all eight phthalates, PVC at 298.15 K, and 32 solvents. QC engine, parameterisation, geometry and ensemble treatment differ from the workbook route; no empirical correction is applied. DiNP and DiDP use the pinned PlastChem representative structures, not an experimentally resolved commercial isomer mixture.\n\n'
report+=f"Source workbook: `{v['workbook']}`; methods inventory: `/home/aaltamimi2/plastchem-euler/reports/phase8-0-inventory/REPORT.md`. The authors used Gaussian 16 BVP86/TZVP/DGA1, CPCM-water optimisation then infinite-dielectric single point, with commercial COSMOtherm19/BP_TZVP_19 for pure-solvent thermodynamics. The present surfaces retain the authorized ORCA BP86/def2-TZVP(-f) OPT then BP86/def2-TZVPD COSMORS(Water) route; the OPT deck has no CPCM keyword. These are not identical implicit-water optimisations. No new DFT was run in this phase.\n\n"
if summary['lle_status_counts'].get('missing',0):report='**IN PROGRESS — LLE results are still pending; this is not the Phase 8.2 gate report.**\n\n'+report
report+='## Neutral solvent/PVC partitioning\n\nHeadline verdict is strict logP > 0. Confusion-matrix truth is the workbook; predicted is the new route. Both conventions and both mole-fraction/concentration bases are retained, but workbook comparison uses concentration logP.\n\n| Convention | n/256 | MAE | RMSE | Bias | Slope | Intercept | TP | TN | FP | FN | Unscored |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
for c,st in stats.items():
 cm=next(r for r in cms if r.get('convention')==c);report+='| '+c+' | '+str(st['n'])+' | '+' | '.join(f"{st.get(k,float('nan')):.4f}" for k in ['MAE','RMSE','bias','slope','intercept'])+' | '+' | '.join(str(cm[k]) for k in ['true_positive','true_negative','false_positive','false_negative','unscored'])+' |\n'
report+='\nThe verdict comparison is primary: normalized and existing-convention TP/TN/FP/FN are all shown above against the full 256 denominator; aggregate numerical agreement does not remove near-zero screening disagreements.\n'
report+='\nWorst cases are named in logP-worst-cases.csv; every observation, source cell, surface hash and residual is in logP-workbook-parity.csv.\n\n'
for c in ['normalized','existing']:
 report+=c.title()+' largest absolute residuals (new minus workbook):\n\n'
 for r in sorted([r for r in paired if r['convention']==c and 'residual' in r],key=lambda r:abs(r['residual']),reverse=True)[:5]:report+=f"- {r['solute']} / {r['solvent']}: {r['residual']:+.4f} log units (workbook cell {r['cell']}).\n"
 report+='\n'
report+='## Binary miscibility\n\nRaw liquid-liquid solubility is reported at 298.15 K and each **literal workbook high temperature**, without substituting the paper’s temperature cap. The 24a physical parameters are unchanged; the COSMOspace iterative tolerance is tightened to 1e−9 to resolve chemical-potential equality. Lower Gibbs convex hulls at 1000 and 2000 grid intervals seed tie-line refinement, both-component chemical potentials must agree within 1e−7 RT, sampled tangent stability must hold, and solubility must change by ≤0.01 percentage point on refinement. Single-liquid-phase results mean complete mixing in this binary LLE model. This is not a fusion-corrected solid-liquid solubility calculation. Values within 0.01 percentage point of 15 are indeterminate. Grid tests do not mathematically exclude arbitrarily narrow unresolved phase gaps.\n\n'
report+=f"Statuses against 512 systems: {summary['lle_status_counts']}. Failed or unresolved calculations remain in the denominator and have no forced verdict. The workbook supplies only Yes/No, so numerical solubility MAE/RMSE/bias/regression are **not defined**; the appropriate validation is the confusion matrix.\n\n"
report+='| Layout | Basis | Regime | Scored/denominator | TP | TN | FP | FN | Agreement |\n|---|---|---|---:|---:|---:|---:|---:|---:|\n'
for cm in cms:
 if cm['quantity'].startswith('miscibility'):report+='| '+' | '.join([cm['layout'],cm['basis'],cm['regime'],f"{cm['scored']}/{cm['denominator']}",*[str(cm[k]) for k in ['true_positive','true_negative','false_positive','false_negative']],f"{cm['agreement']:.3f}" if cm['agreement'] is not None else 'NA'])+' |\n'
report+='\nBlocked layout assigns D:K to RT and L:S to high temperature, eight compounds in workbook order. Paired layout assigns adjacent RT/high columns to each compound. Both are scored under 15 mol% and 15 wt%; **neither layout nor basis is chosen**. The 14 conflicting literal keys, both cell values and corresponding computed assignments under both layouts are enumerated in fourteen-literal-conflicts.csv. No source rows are repaired.\n\n'
report+='Higher agreement under one interpretation does not establish the original sheet semantics. The layout and concentration basis remain owner questions. Input and perceived full InChIKeys and agreeing perception engines are carried in the parity and solubility tables; identity acceptance is explicitly on connectivity, per D-IDENT.\n\n'
report+='Every false positive and false negative is named, with layout, basis, temperature and source cell, in [miscibility-disagreements.csv](miscibility-disagreements.csv). The source conflicts are separately enumerated in [fourteen-literal-conflicts.csv](fourteen-literal-conflicts.csv).\n\n'
report+='## DEHP discrepancy and boundaries\n\nDEHP-water-pair-discrepancy.csv carries the fresh solvent/water differences. The earlier diagnostic found approximately +2.1 log-unit differences for DCM/water, cyclohexanol/water and hexane/water, versus close agreement for DCM/methanol; that historical finding has not been discarded or calibrated away. The new table uses Milan contaminant surfaces; Phase 8.1 retains the explicitly required workstation anchor surfaces.\n\nPFAS is excluded by the clearance note; no PFAS calculation or proposal is made. No new 24a polymer-solubility calculation is performed. **The stored S(T) grid remains LEGACY data for the polymer side of leaching and STRAP**, and is not validated by the present binary-contaminant LLE exercise. Xylene identity, didecyl-phthalate provenance and the workbook basis/layout remain owner questions; none is silently resolved here.\n\n'
report+=f"## Reproduction and gate\n\nInputs: `{D}/validation-inputs.json`, workbook sha256 `{v['workbook_sha256']}`. Scripts: `/home/aaltamimi2/plastchem-euler/scripts/prepare_phase82.py`, `phase82_worker.py`, `phase8_lle.py`, `analyze_phase82.py`. Euler scripts in ~/plastchem-euler/phase8-v1; all jobs research, milan&cpu, euler09/10 excluded, 1 CPU, 4 GB, shared cap ≤64. Raw coefficients: `{D}/phase82/lle/<index>/activity-grid.json`; results include temperatures, identities, residuals and CPU/job provenance. Collect with scp, never rsync. Phase 8.3 and promotion remain held for review; no product or catalog writes.\n"
(O/'REPORT.md').write_text(report);print(json.dumps(summary,indent=2))
