"""Audit octanol predictions and compare one best cited measured value per molecule."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,datetime as dt,hashlib,json,math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/progress-2026-09-14');D=B/'octanol-validation';O=D/'validation';O.mkdir(exist_ok=True)
cohort=json.loads((B/'rehearsal/freeze/manifest.json').read_text())['cohort'];provenance=json.loads((D/'provenance.json').read_text());best=list(csv.DictReader((B/'pubchem-measured-validation/best-measured-logKow.csv').open()));observations=list(csv.DictReader((B/'pubchem-measured-validation/experimental-reference-candidates.csv').open()));values={};failures=[];all_predictions=[]
for m in cohort:
 p=D/(m['inchikey']+'.json')
 if not p.exists():continue
 r=json.loads(p.read_text());pred=r['prediction'];assert r['solute_surface_sha256']==m['surface_sha256'] and r['octanol_surface_sha256']==provenance['octanol_surface_sha256']
 assert r['input_inchikey'].split('-')[0]==r['perceived_inchikey'].split('-')[0] and r['identity_match_basis']=='connectivity_first_block'
 assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
 if pred['status']!='predicted':failures.append({'key':m['inchikey'],'name':r['name'],'failure':r['octanol_activity']});continue
 a,w=r['octanol_activity'],r['water_activity'];assert a['status']==w['status']=='converged'
 for activity in [a,w]:
  samples=activity['samples'];assert len(samples)>=2
  assert abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)<=.005
 x=(w['ln_gamma']-a['ln_gamma'])/math.log(10);c=x+math.log10(pred['molar_volume_reference_cm3_mol']/pred['molar_volume_solvent_cm3_mol'])
 assert abs(x-pred['log10_K_mole_fraction'])<1e-12 and abs(c-pred['log10_K_concentration'])<1e-12 and math.isfinite(c)
 values[m['inchikey']]=r
 all_predictions.append({'input_inchikey':m['inchikey'],'perceived_inchikey':r['perceived_inchikey'],'identity_match_basis':r['identity_match_basis'],'name':r['name'],'cpu_model':r['cpu_model'],'predicted_logKow':c,'temperature_K':298.15,'phase_basis':'pure_component','solute_surface_sha256':m['surface_sha256'],'octanol_surface_sha256':r['octanol_surface_sha256'],'water_source_result_sha256':r['water_source_result_sha256'],'result_sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
matched=[]
for ref in best:
 key=ref['input_inchikey']
 if key not in values:continue
 r=values[key];prediction=r['prediction']['log10_K_concentration'];observed=float(ref['measured_logKow']);matched.append({**ref,'predicted_logKow':prediction,'residual_predicted_minus_measured':prediction-observed})
for name,rows in [('all-predicted-logKow.csv',all_predictions),('best-measured-parity.csv',matched)]:
 if rows:
  with (O/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
residuals=[r['residual_predicted_minus_measured'] for r in matched];n=len(matched)
summary={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'cohort_denominator':590,'predicted_molecules':len(values),'failed_molecules':len(failures),'unprocessed_molecules':590-len(values)-len(failures),'measured_reference_molecules':len(best),'matched_molecules':n,'MAE':sum(abs(x) for x in residuals)/n if n else None,'RMSE':math.sqrt(sum(x*x for x in residuals)/n) if n else None,'bias_predicted_minus_measured':sum(residuals)/n if n else None,'outliers_abs_residual_gt_1':[{'name':r['name'],'inchikey':r['input_inchikey'],'predicted':r['predicted_logKow'],'measured':float(r['measured_logKow']),'residual':r['residual_predicted_minus_measured']} for r in matched if abs(r['residual_predicted_minus_measured'])>1],'failures':failures,'interpretation':'One best cited measured point value per molecule; no double counting of multiple source observations. Model uses neutral pure solvent references; experiment may use wet phases and differing/unspecified temperature. Numerical audit is separate from experimental accuracy.'}
(O/'statistics.json').write_text(json.dumps(summary,indent=2)+'\n')
if n:
 plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,'xtick.labelsize':14,'ytick.labelsize':14,'legend.fontsize':14,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
 fig,ax=plt.subplots(figsize=(9,9),constrained_layout=True);observed=[float(r['measured_logKow']) for r in matched];predicted=[r['predicted_logKow'] for r in matched];lo=min(observed+predicted)-.5;hi=max(observed+predicted)+.5
 ax.plot([lo,hi],[lo,hi],color='black',label='1:1');ax.scatter(observed,predicted,s=65,color='#327ba8');ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Measured octanol/water logKow',ylabel='Predicted octanol/water logKow',title=f'Octanol/water validation (n = {n})');ax.legend();fig.savefig(O/'predicted-vs-experimental.png',dpi=300);plt.close(fig)
 caption=f"n = {n} molecules; MAE = {summary['MAE']:.3f}, RMSE = {summary['RMSE']:.3f}, bias (predicted minus measured) = {summary['bias_predicted_minus_measured']:+.3f} log units. Black line: 1:1. One best cited measured value per molecule; phase/temperature differences remain."
 (O/'parity-caption.txt').write_text(caption+'\n')
print(json.dumps(summary))
