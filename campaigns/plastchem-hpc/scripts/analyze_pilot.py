"""Final pilot table, size scaling, residuals, conditional uncertainty, and exportable plots."""
import csv,json,math,re,os
from pathlib import Path
import numpy as np
os.environ['MPLCONFIGDIR']=str(Path(__file__).resolve().parents[1]/'state/matplotlib')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'state/pilot-v1';REPORT=ROOT/'reports/pilot-v1';REPORT.mkdir(parents=True,exist_ok=True)
v=json.loads((STATE/'verified-state.json').read_text());census=json.loads((STATE/'census.json').read_text());snap=json.loads((STATE/'latest-snapshot.json').read_text())
accounting={}
for line in snap['sacct'].splitlines():
 p=line.split('|')
 if len(p)>=11:accounting[p[0]]=p
def orca_seconds(stage):
 lines=stage.get('orca_total_run_time_lines',[])
 if not lines:return None
 match=re.search(r'TOTAL RUN TIME:\s*(\d+) days\s*(\d+) hours\s*(\d+) minutes\s*(\d+) seconds\s*(\d+) msec',lines[-1])
 if not match:return None
 d,h,m,sec,ms=map(int,match.groups());return d*86400+h*3600+m*60+sec+ms/1000
rows=[]
for key,r in v['records'].items():
 m=r['input'];task=str(snap['array_job_id'])+'_'+str(m['array_index']);batch=accounting.get(task+'.batch',[]);job=accounting.get(task,[])
 walls={s:r.get('stages',{}).get(s,{}).get('wall_seconds') for s in ['opt','cosmo']}
 total=sum(walls.values()) if all(x is not None for x in walls.values()) else None
 internal=[orca_seconds(r.get('stages',{}).get(stage,{})) for stage in ['opt','cosmo']]
 internal_total=sum(internal) if all(x is not None for x in internal) else None
 rss=float(batch[7].rstrip('K')) if batch and batch[7] else None
 rows.append({'inchikey':key,'name':m['name'],'cas':m['cas'],'array_index':m['array_index'],'job_id':task,'status':r['status'],'dft_converged':r.get('dft_status')=='converged' or r['status']=='converged','failure_mode':r.get('failure_mode',''),'error':r.get('error',''),'atoms':m['atoms'],'heavy_atoms':m['heavy_atoms'],'rotatable_bonds':m['rotatable_bonds'],'stratum':m['stratum'],'selection_role':m['selection_role'],'node':r.get('node') or '','cpu_model':r.get('cpu_model') or '','cpu_generation':'milan','geometry_cycles':r.get('stages',{}).get('opt',{}).get('geometry_cycles'),'opt_wall_seconds':walls['opt'],'cosmo_wall_seconds':walls['cosmo'],'total_orca_wall_seconds':total,'orca_internal_opt_seconds':internal[0],'orca_internal_cosmo_seconds':internal[1],'orca_internal_total_seconds':internal_total,'elapsed_seconds':r.get('elapsed_seconds'),'slurm_state':job[3] if job else '', 'slurm_elapsed_seconds':int(job[5]) if job and job[5] else None,'maxrss_kib':rss,'surface_bytes':r.get('surface_bytes'),'returned_bytes':r.get('returned_bytes'),'surface_sha256':r.get('surface_sha256'),'inchikey_after_optimization':r.get('inchikey_after_optimization')})
# Calibration uses normally completed DFT timings, including explicitly identity-unresolved outputs.
# This models compute work, not acceptance of a chemical result. Other failures remain explicit.
good=[r for r in rows if r['dft_converged'] and r['total_orca_wall_seconds'] is not None]
summary={'counts':v['counts'],'cpu_models':sorted({r['cpu_model'] for r in rows if r['cpu_model']}),'fit_normally_completed_dft':len(good),'verified_chemical_successes':v['counts']['converged'],'failures':[r for r in rows if r['status']=='failed'],'extrapolation_below_15':sum(m['atoms']<15 for m in census),'extrapolation_above_80':sum(m['atoms']>80 for m in census),'population':len(census)}
if len(good)>=10:
 n=np.array([r['atoms'] for r in good],float);t=np.array([r['total_orca_wall_seconds'] for r in good]);X=np.column_stack([np.ones(len(n)),np.log(n)])
 beta=np.linalg.lstsq(X,np.log(t),rcond=None)[0];res=np.log(t)-X@beta;smear=float(np.mean(np.exp(res)))
 pred=np.exp(X@beta)*smear;alln=np.array([m['atoms'] for m in census],float);popX=np.column_stack([np.ones(len(alln)),np.log(alln)])
 projected=np.exp(popX@beta)*smear
 rng=np.random.default_rng(20260912);strata={s:np.array([i for i,r in enumerate(good) if r['stratum']==s]) for s in sorted({r['stratum'] for r in good})};cpu_totals=[];archive_totals=[]
 sizes=np.array([r['returned_bytes'] for r in good],float)
 for repeat in range(2000):
  ids=np.concatenate([rng.choice(ix,len(ix),replace=True) for ix in strata.values()]);b=np.linalg.lstsq(X[ids],np.log(t[ids]),rcond=None)[0];e=np.log(t[ids])-X[ids]@b
  cpu_totals.append(float(np.sum(np.exp(popX@b)*np.mean(np.exp(e)))/3600))
  # Surface/payload size is approximately linear in atoms; retain fitted intercept.
  Z=np.column_stack([np.ones(len(n)),n]);q=np.linalg.lstsq(Z[ids],sizes[ids],rcond=None)[0]
  archive_totals.append(float(np.sum(np.maximum(0,q[0]+q[1]*alln))))
 summary['fit']={'form':'mean seconds = exp(intercept) * atoms**exponent * residual_smearing','intercept':float(beta[0]),'exponent':float(beta[1]),'residual_smearing':smear,'log_rmse':float(np.sqrt(np.mean(res**2))),'observed_to_predicted_ratio_p10_p50_p90':np.quantile(t/pred,[.1,.5,.9]).tolist(),'campaign_cpu_hours':float(projected.sum()/3600),'bootstrap_cpu_hours_p05_p50_p95':np.quantile(cpu_totals,[.05,.5,.95]).tolist(),'archive_bytes_bootstrap_p05_p50_p95':np.quantile(archive_totals,[.05,.5,.95]).tolist(),'uncertainty_interpretation':'Conditional within-stratum bootstrap sensitivity range, not a coverage-guaranteed confidence interval: selection was deliberate, not random; failures and out-of-range chemistry add uncertainty.'}
 for row,p,e in zip(good,pred,res):row.update(predicted_seconds=float(p),residual_seconds=float(row['total_orca_wall_seconds']-p),observed_predicted_ratio=float(row['total_orca_wall_seconds']/p),log_residual=float(e))
 # Merge fitted fields into all rows by key.
 by={r['inchikey']:r for r in good};rows=[by.get(r['inchikey'],r) for r in rows]
 fig,axes=plt.subplots(2,1,figsize=(9,8),sharex=True,gridspec_kw={'height_ratios':[2,1]})
 axes[0].scatter(n,t/60,color='#286090',label='Normally completed DFT timings')
 line=np.arange(15,81);axes[0].plot(line,np.exp(beta[0])*line**beta[1]*smear/60,color='black',label='Power-law mean fit')
 for r in good:
  if r['selection_role'] in ['DEP','DBP','BBP','DEHP']:axes[0].annotate(r['selection_role'],(r['atoms'],r['total_orca_wall_seconds']/60))
 axes[0].set_ylabel('OPT + COSMORS wall (minutes)');axes[0].set_yscale('log');axes[0].legend();axes[0].set_title('research / Milan: '+', '.join(summary['cpu_models']))
 axes[1].scatter(n,(t-pred)/60);axes[1].axhline(0,color='black');axes[1].set_ylabel('Residual (minutes)');axes[1].set_xlabel('Atom count, including H');fig.tight_layout();fig.savefig(REPORT/'wall-and-residuals.png',dpi=180);fig.savefig(REPORT/'wall-and-residuals.pdf');plt.close(fig)
for row in rows:
 if not row['cpu_model']:row['cpu_generation']=''
 row['attempt_seconds']=row['elapsed_seconds'] if row['elapsed_seconds'] is not None else row['slurm_elapsed_seconds']
attempts=[r for r in rows if r['attempt_seconds'] is not None and r['cpu_model'] and r['status'] in ['converged','failed']]
if len(attempts)>=10:
 an=np.array([r['atoms'] for r in attempts],float);at=np.array([r['attempt_seconds'] for r in attempts],float)
 AX=np.column_stack([np.ones(len(an)),np.log(an)]);alln=np.array([m['atoms'] for m in census],float);PX=np.column_stack([np.ones(len(alln)),np.log(alln)])
 ab=np.linalg.lstsq(AX,np.log(at),rcond=None)[0];ae=np.log(at)-AX@ab
 ast={s:np.array([i for i,r in enumerate(attempts) if r['stratum']==s]) for s in sorted({r['stratum'] for r in attempts})};rng=np.random.default_rng(20260913);totals=[]
 for _ in range(2000):
  ids=np.concatenate([rng.choice(ix,len(ix),replace=True) for ix in ast.values()]);b=np.linalg.lstsq(AX[ids],np.log(at[ids]),rcond=None)[0];e=np.log(at[ids])-AX[ids]@b
  totals.append(float(np.sum(np.exp(PX@b)*np.mean(np.exp(e)))/3600))
 summary['all_attempt_budget']={'attempts':len(attempts),'intercept':float(ab[0]),'exponent':float(ab[1]),'campaign_cpu_hours':float(np.sum(np.exp(PX@ab)*np.mean(np.exp(ae)))/3600),'bootstrap_cpu_hours_p05_p50_p95':np.quantile(totals,[.05,.5,.95]).tolist(),'observed_total_attempt_hours':float(at.sum()/3600),'interpretation':'Cost of one attempt per structure under the pilot stopping rules, retaining elapsed cost of failures. This does not estimate retries or guarantee 5,833 successful results; timed-out completion times remain lower bounds.'}
workstation=json.loads((STATE/'workstation-anchors.json').read_text())
summary['anchors']=[]
for r in rows:
 if r['selection_role'] in workstation:
  base=workstation[r['selection_role']];summary['anchors'].append(dict(r,workstation_cpu=base['cpu_model_historical'],workstation_cpu_generation=base['cpu_generation'],workstation_geometry_cycles=base['stages']['opt']['geometry_cycles'],workstation_opt_seconds=base['stages']['opt']['orca_total_seconds'],workstation_cosmo_seconds=base['stages']['cosmo']['orca_total_seconds'],workstation_total_seconds=base['total_seconds'],milan_over_i7_13700_ratio=r['orca_internal_total_seconds']/base['total_seconds'] if r['orca_internal_total_seconds'] else None))
columns=list(dict.fromkeys(k for r in rows for k in r))
with (REPORT/'molecules.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)
(REPORT/'analysis.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ['anchors','failures']},indent=2))
