"""Cumulative mixed-source independent accuracy comparison; preserves all earlier packages."""
import csv,json,hashlib,math,datetime
from pathlib import Path
import numpy as np
R=Path('/mnt/r/plastchem-euler');D=R/'octanol-followup-2026-09-17';E=R/'combined-validation-references-2026-09-17-opera-refresh'
newaudit=json.loads((D/'numerical-audit.json').read_text());assert newaudit['failed']==0
prior_choices={r['input_inchikey']:r for r in csv.DictReader((R/'post1160-octanol-2026-09-15/opera-extension/cumulative-parity.csv').open())}
lookup={}
for row in csv.DictReader((R/'measured-expansion-2026-09-15/all-predictions.csv').open()):lookup[row['input_inchikey']]=(Path(row['result_path']),row['result_sha256'])
for p in [*R.glob('post*-octanol-2026-09-15/numerical-audit.json'),R/'octanol-catchup-2026-09-17/numerical-audit.json',D/'numerical-audit.json',R/'octanol-post3883-2026-09-17/numerical-audit.json',*[R/folder/'numerical-audit.json' for folder in ['octanol-post4060-2026-09-17','octanol-post4172-2026-09-17','octanol-post4234-2026-09-17','octanol-post4244-2026-09-17','octanol-post4261-2026-09-17']]]:
 audit=json.loads(p.read_text());assert audit['failed']==0
 for row in audit['rows']:
  if row['status']=='passed':lookup[row['inchikey']]=(p.parent/'octanol'/f"{row['inchikey']}.json",row['result_sha256'])
rows=[];unmatched=[]
for obs in csv.DictReader((E/'experimental-reference-candidates.csv').open()):
 k=obs['input_inchikey']
 if k not in lookup:unmatched.append({'inchikey':k,'reason':'No audited octanol result yet'});continue
 p,sha=lookup[k];raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==sha
 r=json.loads(raw);pred=r['prediction']
 if pred['status']!='predicted':unmatched.append({'inchikey':k,'reason':'Octanol prediction explicitly unavailable'});continue
 y=pred['log10_K_concentration'];x=float(obs['measured_logKow']);assert math.isfinite(y) and math.isfinite(x)
 rows.append({'input_inchikey':k,'name':obs['name'],'anchor':prior_choices.get(k,{}).get('anchor',''),'source_question':prior_choices.get(k,{}).get('source_question',''),'measured_logKow':x,'predicted_logKow':y,'residual':y-x,'source_url':obs['source_url'],'citation':obs['raw_reference_string'],'retrieved_utc':obs['retrieved_utc'],'result_path':str(p),'result_sha256':sha})
assert len({r['input_inchikey'] for r in rows})==len(rows)
assert {r['anchor'] for r in rows if r['anchor']}=={'DEP','DBP','BBP','DEHP'}
for r in rows:
 if r['input_inchikey'] in prior_choices:assert r['measured_logKow']==float(prior_choices[r['input_inchikey']]['measured_logKow'])
x=np.array([r['measured_logKow'] for r in rows]);y=np.array([r['predicted_logKow'] for r in rows]);error=y-x
slope,intercept=np.polyfit(x,y,1)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'Previous n64 choices retained; additional qualified PubChem, OPERA and CompTox values, matched to audited octanol results','n':len(rows),'MAE':float(abs(error).mean()),'RMSE':float(np.sqrt((error**2).mean())),'bias':float(error.mean()),'slope':float(slope),'intercept':float(intercept),'unmatched_reference_count':len(unmatched),'outliers':sorted(rows,key=lambda r:abs(r['residual']),reverse=True)[:10],'no_recalibration':True}
O=R/'validation-opera-refresh-2026-09-17';O.mkdir(exist_ok=False)
with (O/'anchors.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(r for r in rows if r['anchor'])
with (O/'parity.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
(O/'statistics.json').write_text(json.dumps(s,indent=2)+'\n');(O/'unmatched.json').write_text(json.dumps(unmatched,indent=2)+'\n')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,'xtick.labelsize':14,'ytick.labelsize':14,'legend.fontsize':14,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(9,9));ax.scatter(x,y,s=28,alpha=.75);lo=min(x.min(),y.min())-.5;hi=max(x.max(),y.max())+.5
ax.plot([lo,hi],[lo,hi],color='black',linestyle='--');ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Experimental logKow (cited references)',ylabel='Predicted logKow (dry octanol)',title='Octanol–water validation');ax.set_aspect('equal')
fig.text(.12,.03,f"n={len(rows)}; MAE={s['MAE']:.2f}; RMSE={s['RMSE']:.2f}; bias={s['bias']:+.2f}",fontsize=14,color='black');fig.tight_layout(rect=(0,.07,1,1));fig.savefig(O/'parity.png',dpi=300);plt.close(fig)
(O/'REPORT.md').write_text(f"# Cumulative mixed-source validation\n\nn={len(rows)}; MAE={s['MAE']:.4f}; RMSE={s['RMSE']:.4f}; bias={s['bias']:+.4f}; predicted-on-measured slope={s['slope']:.4f}, intercept={s['intercept']:.4f}.\n\nReferences without an audited available prediction: {len(unmatched)}. Per-row source citations and hashes are in parity.csv; named largest residuals are in statistics.json. This is dry-octanol modelling compared with curated experimental observations; measurement conditions and high-logKow uncertainty are not resolved for every source. No empirical correction. Prior released n=21 and cumulative n=64 results are unchanged. All previous 447 selected reference choices are preserved; only newly covered qualified OPERA observed LogP values are added independently of predictions. Reference coverage and numerical predictions remain incomplete. The four anchors are named in anchors.csv. Owner questions remain open: xylene identity and the didecyl-phthalate reference discrepancy (retained PubChem 9.05 versus primary abstract 8.83 +/-0.05); the latter is flagged on its parity-table row and no reference choice was changed.\n")
(O/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(O/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(O.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
print(json.dumps({k:v for k,v in s.items() if k!='outliers'}),flush=True)
