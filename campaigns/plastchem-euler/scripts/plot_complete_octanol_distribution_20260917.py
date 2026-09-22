"""Distribution of all audited octanol predictions; no recalculation or truncated tails."""
import csv,json,hashlib,datetime,shutil
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
R=Path('/home/aaltamimi2/plastchem-euler');B=Path('/mnt/r/plastchem-euler');D=B/'progress-2026-09-17/octanol-distribution-5803';D.mkdir(parents=True,exist_ok=False)
source=B/'progress-2026-09-17/contaminant-overview-draft-v1/predictions.csv';index=B/'cumulative-result-index/results-index.csv';indexed={r['input_inchikey']:r for r in csv.DictReader(index.open())};rows=[]
for r in csv.DictReader(source.open()):
 if r['solvent']!='1-octanol':continue
 i=indexed[r['input_inchikey']];assert r['source_sha256']==i['octanol_result_sha256'];assert float(r['log10_K_concentration'])==float(i['octanol_logKow'])
 rows.append({'input_inchikey':r['input_inchikey'],'name':i['name'],'predicted_logKow':float(r['log10_K_concentration']),'temperature_K':298.15,'result_sha256':r['source_sha256'],'audit_evidence':i['octanol_audit_evidence']})
assert len(rows)==len({r['input_inchikey'] for r in rows})==5803
values=np.array([r['predicted_logKow'] for r in rows]);assert np.isfinite(values).all();counts,edges=np.histogram(values,bins=80);assert counts.sum()==5803
with (D/'predictions.csv').open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
with (D/'histogram-bins.csv').open('w',newline='') as f:
 w=csv.writer(f);w.writerow(['lower_logKow','upper_logKow','count']);w.writerows(zip(edges[:-1],edges[1:],counts))
plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,'xtick.labelsize':14,'ytick.labelsize':14,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(9,6));ax.hist(values,bins=edges,color='#287f72',edgecolor='white',linewidth=.35);ax.set(xlabel='Predicted logKow (dry octanol, 25°C)',ylabel='Contaminants',title='Computed octanol–water distribution');fig.text(.12,.025,'n = 5,803 audited predictions; full range shown; no recalibration.',fontsize=14,color='black');fig.tight_layout(rect=(0,.06,1,1));fig.savefig(D/'distribution.png',dpi=300);fig.savefig(D/'distribution.pdf');plt.close(fig)
s={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'n':5803,'min':float(values.min()),'max':float(values.max()),'median':float(np.median(values)),'q25':float(np.quantile(values,.25)),'q75':float(np.quantile(values,.75)),'histogram_count':int(counts.sum()),'source_csv_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'index_sha256':hashlib.sha256(index.read_bytes()).hexdigest(),'interpretation':'All accepted contaminant entries with audited dry-octanol predictions; distribution of model outputs, not experimental measurements'};(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n');(D/'REPORT.md').write_text('# Complete accepted-cohort octanol distribution\n\nAll 5,803 accepted contaminants are represented once, with full output range retained. Every plotted value and result hash matches the cumulative audited index. No new scientific calculation or recalibration. These are dry-octanol model predictions at 298.15 K, not measured values.\n\n![Distribution](distribution.png)\n\nData: predictions.csv and histogram-bins.csv; summary.json includes statistics and source hashes.\n')
shutil.copyfile(__file__,D/Path(__file__).name);shutil.copyfile(D/'distribution.png',R/'2026-09-17-complete-octanol-distribution-5803.png');(D/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='SHA256SUMS'));print(json.dumps(s))
