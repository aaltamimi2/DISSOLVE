"""Audit and plot fixed released-plus-batch2 results without modifying either input cohort."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
import csv,datetime as dt,hashlib,json,math
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/batch-2');F=Path('/mnt/r/plastchem-euler/progress-2026-09-14');O=B/'figures';O.mkdir(exist_ok=True)
def read(p):return json.loads(Path(p).read_text())
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,indent=2)+'\n')
def csvout(p,rows):
 if not rows:return
 with Path(p).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
frozen=read(F/'freeze/manifest.json')['cohort'];new=read(B/'manifest.json')['cohort'];assert len(frozen)==648 and len(new)==296
assert not ({r['inchikey'] for r in frozen}&{r['inchikey'] for r in new})
oldledger=read(F/'sealed-thermodynamics/processing-ledger.json');fit=read(R/'reports/pilot-v1/analysis.json')['fit'];parts=[];walls=[];audit=[];octs=[];cohort={};files={}
for batch,members in [('released',frozen),('batch2',new)]:
 for m in members:
  k=m['inchikey'];source=(F/'freeze' if batch=='released' else B/'freeze')/'state/campaign-v1/records'/f'{k}.json';r=read(source);cohort[k]=r
  p=Path(oldledger[k]['result_path']) if batch=='released' else B/'panel'/f'{k}.json';v=read(p);h=sha(p);files[str(p)]=h
  assert v['solute_surface_sha256']==r['surface_sha256']==m['surface_sha256'] and r['connectivity_match']
  assert v['input_inchikey']==k and v['perceived_inchikey'].split('-')[0]==k.split('-')[0] and v['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  assert v['config']['parameterization']=='openCOSMORS24a' and v['config']['temperature_K']==298.15
  if batch=='released':assert h==oldledger[k]['result_sha256']
  for a in v['activities'].values():
   assert a['status']=='converged';samples=a['samples'];assert len(samples)>=2 and abs(samples[-1]['ln_gamma']-samples[-2]['ln_gamma'])/math.log(10)<=.005
  for pair in v['partitions_against_water']:
   if pair['status']!='predicted':continue
   x=(v['activities']['water']['ln_gamma']-v['activities'][pair['solvent']]['ln_gamma'])/math.log(10)
   assert abs(x-pair['log10_K_mole_fraction'])<1e-12
   c=x+math.log10(pair['molar_volume_reference_cm3_mol']/pair['molar_volume_solvent_cm3_mol']);assert abs(c-pair['log10_K_concentration'])<1e-12
   parts.append({'input_inchikey':k,'perceived_inchikey':v['perceived_inchikey'],'identity_match_basis':v['identity_match_basis'],'perceived_keys_by_engine':json.dumps(r['perceived_keys_by_engine'],sort_keys=True),'name':v['name'],'cohort':batch,'solvent':pair['solvent'],'log10_K_concentration':c,'log10_K_mole_fraction':x,'cpu_model':v['cpu_model'],'result_sha256':h})
  seconds=sum(r['stages'][s]['wall_seconds'] for s in ['opt','cosmo']);pred=math.exp(fit['intercept'])*r['input']['atoms']**fit['exponent']*fit['residual_smearing']
  walls.append({'inchikey':k,'name':r['input']['name'],'cohort':batch,'group':r.get('group',r['input'].get('group','main_le80')),'atoms':r['input']['atoms'],'wall_hours':seconds/3600,'pilot_mean_hours':pred/3600,'residual_hours':(seconds-pred)/3600,'maxrss_kib':r.get('slurm_accounting',{}).get('maxrss_kib'),'returned_bytes':r.get('returned_bytes'),'cpu_model':r['cpu_model']})
  audit.append({'inchikey':k,'cohort':batch,'status':'passed','result_sha256':h})
for batch,members in [('released',read(F/'rehearsal/freeze/manifest.json')['cohort']),('batch2',new)]:
 for m in members:
  k=m['inchikey'];p=(F/'octanol-validation'/f'{k}.json') if batch=='released' else B/'octanol'/f'{k}.json';r=read(p);pr=r['prediction'];a=r['octanol_activity'];w=r['water_activity'];assert pr['status']=='predicted' and a['status']==w['status']=='converged'
  assert r['solute_surface_sha256']==cohort[k]['surface_sha256'];assert r['perceived_inchikey'].split('-')[0]==k.split('-')[0]
  for act in [a,w]:assert abs(act['samples'][-1]['ln_gamma']-act['samples'][-2]['ln_gamma'])/math.log(10)<=.005
  calc=(w['ln_gamma']-a['ln_gamma'])/math.log(10)+math.log10(pr['molar_volume_reference_cm3_mol']/pr['molar_volume_solvent_cm3_mol']);assert abs(calc-pr['log10_K_concentration'])<1e-12
  if batch=='batch2':assert sha(B/'panel'/f'{k}.json')==r['water_source_result_sha256']
  octs.append({'input_inchikey':k,'perceived_inchikey':r['perceived_inchikey'],'name':r['name'],'cohort':batch,'predicted_logKow':calc,'result_sha256':sha(p)})
assert len(octs)==886 and len(parts)==5664
csvout(O/'computed-partition-values.csv',parts);csvout(O/'walltime-vs-atoms.csv',walls);csvout(B/'cumulative-octanol.csv',octs)
save(B/'numerical-audit.json',{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'panel_passed':len(audit),'panel_failed':0,'octanol_passed':len(octs),'octanol_failed':0,'rows':audit,'interpretation':'Stored numerical, identity and provenance consistency, not experimental accuracy'})
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(11,7),layout='constrained')
for group,color in [('main_le80','#3274a1'),('tail_gt80','#c47c26')]:
 rows=[r for r in walls if r['group']==group];ax.scatter([r['atoms'] for r in rows],[r['wall_hours'] for r in rows],s=24,alpha=.65,color=color,label='Main ≤80 atoms' if group=='main_le80' else 'Tail >80 atoms')
x=np.linspace(15,80,200);ax.plot(x,np.exp(fit['intercept'])*x**fit['exponent']*fit['residual_smearing']/3600,color='black',label='Milan pilot mean fit');ax.set(xlabel='Atom count (including hydrogen)',ylabel='OPT + COSMORS wall time (hours)',title='Cumulative ORCA timings (n = 944)');ax.legend();fig.savefig(O/'walltime-vs-atoms.png',dpi=300);plt.close(fig)
solvents=sorted({r['solvent'] for r in parts});fig,axes=plt.subplots(3,2,figsize=(12,12),layout='constrained')
for ax,s in zip(axes.flat,solvents):
 vals=[r['log10_K_concentration'] for r in parts if r['solvent']==s];ax.hist(vals,bins=25,color='#3274a1',edgecolor='white');ax.set(title=f'{s}: cumulative n = 944',xlabel='log₁₀ K (solvent/water)',ylabel='Structures')
fig.savefig(O/'computed-value-distribution.png',dpi=300);plt.close(fig)
eligible=read(R/'state/campaign-v1/eligible.json');indices={r['inchikey']:r['array_index'] for r in read(R/'state/campaign-v1/main_le80/manifest.json')['molecules']};groups=defaultdict(Counter);live={p.stem:read(p) for p in (R/'state/campaign-v1/records').glob('*.json')};dispositions=[]
for m in eligible:
 k=m['inchikey'];r=live.get(k,{});status='report_converged' if k in cohort else ('completed_later' if r.get('status')=='converged' else r.get('status','not_yet_run'));status=status if status in ['report_converged','completed_later','failed','running','not_yet_run'] else 'running';chunk='Tail >80' if m['group']=='tail_gt80' else (f'Main {indices[k]//500:02d}' if k in indices else 'Pilot reuse');groups[chunk][status]+=1;dispositions.append({'inchikey':k,'name':m['name'],'chunk':chunk,'status':status,'failure_mode':r.get('failure_mode','')})
cats=['report_converged','completed_later','failed','running','not_yet_run'];rows=[{'chunk':c,**{s:groups[c][s] for s in cats}} for c in sorted(groups)];assert sum(sum(r[s] for s in cats) for r in rows)==5824
csvout(O/'campaign-progress.csv',rows);csvout(B/'campaign-dispositions.csv',dispositions)
fig,ax=plt.subplots(figsize=(12,9),layout='constrained');left=np.zeros(len(rows))
for s,color in zip(cats,['#46906b','#5a9cad','#b95c4b','#dbb45d','#b5bbc3']):
 vals=np.array([r[s] for r in rows]);ax.barh([r['chunk'] for r in rows],vals,left=left,color=color,label=s.replace('_',' '));left+=vals
ax.set(xlabel='Structures',title='Cumulative report cohort and live progress / 5,824');ax.legend(loc='upper center',bbox_to_anchor=(.5,-.1),ncol=3);fig.savefig(O/'campaign-progress.png',dpi=300);plt.close(fig)
save(B/'cumulative-summary.json',{'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'released':648,'batch2':296,'cumulative_panel_molecules':944,'panel_predictions':len(parts),'octanol_molecules':len(octs),'released_without_octanol':58,'live_dispositions':dict(Counter(r['status'] for r in dispositions)),'main_hours':sum(r['wall_hours'] for r in walls if r['group']=='main_le80'),'tail_hours':sum(r['wall_hours'] for r in walls if r['group']=='tail_gt80'),'maxrss_mib':max(r['maxrss_kib'] or 0 for r in walls)/1024,'source_files':files})
print('Audited 944 panel and 886 octanol records; three cumulative figures generated',flush=True)
