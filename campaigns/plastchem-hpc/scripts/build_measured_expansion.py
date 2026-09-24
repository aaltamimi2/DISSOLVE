"""Pinned 960-cohort measured-source expansion; preserve released and batch-2 artifacts."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,json,hashlib,math,datetime
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
D=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15');B=Path('/mnt/r/plastchem-euler/batch-2');F=Path('/mnt/r/plastchem-euler/progress-2026-09-14')
def read(p):return json.loads(p.read_text())
def rows(p):return list(csv.DictReader(p.open()))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def out(p,rs):
 fields=list(dict.fromkeys(k for r in rs for k in r))
 with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rs)
cohort={r['inchikey']:r for r in read(D/'cohort.json')['records']};assert len(cohort)==960
candidates=sum([rows(p/'pubchem-measured-validation/experimental-reference-candidates.csv') for p in [F,B,D]],[])+rows(D/'opera-experimental-reference-candidates.csv')
if (D/'comptox-experimental-reference-candidates.csv').exists():candidates+=rows(D/'comptox-experimental-reference-candidates.csv')
assert all(r['input_inchikey'] in cohort for r in candidates)
out(D/'experimental-reference-candidates.csv',candidates)
# Keep already published measured selections and anchors; new sources are independent fallback.
best={r['input_inchikey']:r for p in [F,B,D] for r in rows(p/'pubchem-measured-validation/best-measured-logKow.csv')}
for r in sorted(candidates,key=lambda r:(int(r['selection_priority']),r['source_name'],r['raw_reference_string'])):
 if r['observed_operator']=='=':best.setdefault(r['input_inchikey'],r)
out(D/'best-measured-logKow.csv',list(best.values()))
preds={};audit=[]
for k,r in cohort.items():
 p=next((q/f'{k}.json' for q in [D/'octanol-fill/octanol',B/'octanol',F/'octanol-validation'] if (q/f'{k}.json').exists()),None)
 assert p is not None,('missing prediction',k)
 v=read(p);pr=v['prediction'];a=v['octanol_activity'];w=v['water_activity']
 assert pr['status']=='predicted' and a['status']==w['status']=='converged'
 assert v['input_inchikey']==k and v['solute_surface_sha256']==r['surface_sha256']
 assert v['perceived_inchikey'].split('-')[0]==k.split('-')[0]
 assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
 for act in [a,w]:assert abs(act['samples'][-1]['ln_gamma']-act['samples'][-2]['ln_gamma'])/math.log(10)<=.005
 x=(w['ln_gamma']-a['ln_gamma'])/math.log(10);c=x+math.log10(pr['molar_volume_reference_cm3_mol']/pr['molar_volume_solvent_cm3_mol'])
 assert abs(c-pr['log10_K_concentration'])<1e-12
 if p.parent.parent.name=='octanol-fill':assert sha(D/'octanol-fill/panel'/f'{k}.json')==v['water_source_result_sha256']
 preds[k]={'input_inchikey':k,'perceived_inchikey':v['perceived_inchikey'],'identity_match_basis':v['identity_match_basis'],'name':v['name'],'predicted_logKow':c,'cpu_model':r['cpu_model'],'result_path':str(p),'result_sha256':sha(p)}
 audit.append({'input_inchikey':k,'status':'passed','result_sha256':sha(p)})
out(D/'all-predictions.csv',list(preds.values()));save(D/'numerical-audit.json',{'passed':len(audit),'failed':0,'rows':audit,'scope':'Stored dilution, identity, surface provenance and concentration conversion checks; not experimental validation.'})
# Public export can finish during the read-only numerical audit; reload its qualified observations.
candidates=sum([rows(p/'pubchem-measured-validation/experimental-reference-candidates.csv') for p in [F,B,D]],[])+rows(D/'opera-experimental-reference-candidates.csv')
if (D/'comptox-experimental-reference-candidates.csv').exists():candidates+=rows(D/'comptox-experimental-reference-candidates.csv')
assert all(r['input_inchikey'] in cohort for r in candidates)
out(D/'experimental-reference-candidates.csv',candidates)
for r in sorted(candidates,key=lambda r:(int(r['selection_priority']),r['source_name'],r['raw_reference_string'])):
 if r['observed_operator']=='=':best.setdefault(r['input_inchikey'],r)
out(D/'best-measured-logKow.csv',list(best.values()))
anchors={r['input_inchikey']:r['anchor'] for r in rows(B/'validation/four-anchor-parity.csv')}
parity=[]
for k,r in best.items():
 pred=preds[k]['predicted_logKow'];obs=float(r['measured_logKow'])
 parity.append({**r,'predicted_logKow':pred,'residual_predicted_minus_measured':pred-obs,'anchor':anchors.get(k,''),'source_question':'Owner unresolved: PubChem 9.05 versus primary abstract 8.83 +/-0.05' if 'didecyl' in r['name'].lower() else ''})
res=np.array([r['residual_predicted_minus_measured'] for r in parity]);obs=np.array([float(r['measured_logKow']) for r in parity]);pred=np.array([r['predicted_logKow'] for r in parity]);slope,intercept=np.polyfit(obs,pred,1)
stats={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'prediction_denominator':len(preds),'n':len(parity),'MAE':float(abs(res).mean()),'RMSE':float(np.sqrt((res**2).mean())),'bias':float(res.mean()),'slope':float(slope),'intercept':float(intercept),'source_observations':len(candidates),'source_classes':{s:sum(r['source_class']==s for r in parity) for s in sorted({r['source_class'] for r in parity})},'empirical_correction_applied':False,'outliers':sorted([{'name':r['name'],'input_inchikey':r['input_inchikey'],'measured':float(r['measured_logKow']),'predicted':r['predicted_logKow'],'residual':r['residual_predicted_minus_measured'],'source_url':r['source_url']} for r in parity if abs(r['residual_predicted_minus_measured'])>1],key=lambda r:-abs(r['residual']))}
save(D/'statistics.json',stats);out(D/'best-measured-parity.csv',parity);out(D/'four-anchor-parity.csv',[r for r in parity if r['anchor']])
for old in rows(B/'validation/four-anchor-parity.csv'):
 new=next(r for r in parity if r['input_inchikey']==old['input_inchikey']);assert float(old['measured_logKow'])==float(new['measured_logKow']) and float(old['predicted_logKow'])==new['predicted_logKow']
out(D/'per-molecule-best-measured.csv',[{**preds[k],**best.get(k,{}),'reference_status':'qualified_point_value' if k in best else 'no_qualified_point_value'} for k in cohort])
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(10,10),layout='constrained');lo=min(min(obs),min(pred))-.5;hi=max(max(obs),max(pred))+.5;ax.plot([lo,hi],[lo,hi],color='black',label='1:1')
for source,color,label in [('OPERA','#c47c26','OPERA additional measured'),('other','#3274a1','PubChem / other measured')]:
 rs=[r for r in parity if (r['source_class'].startswith('OPERA'))==(source=='OPERA')]
 if rs:ax.scatter([float(r['measured_logKow']) for r in rs],[r['predicted_logKow'] for r in rs],color=color,s=60,label=label)
ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Measured octanol/water logKow',ylabel='Predicted octanol/water logKow',title=f'Expanded measured comparison (n = {len(parity)})');ax.legend();fig.savefig(D/'predicted-vs-experimental.png',dpi=300);plt.close(fig)
print(json.dumps(stats),flush=True)
