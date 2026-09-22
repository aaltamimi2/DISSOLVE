"""New dated progress figures; never modify a released or batch-2 package."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['OMP_NUM_THREADS']='1'
import csv,json,hashlib,datetime,math
from pathlib import Path
from collections import Counter,defaultdict
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1];R=Path('/mnt/r/plastchem-euler')
assert json.loads((R/'octanol-catchup-2026-09-17/numerical-audit.json').read_text())['failed']==0
D=R/'cumulative-figures-2026-09-17T1132';D.mkdir(exist_ok=False)
def read(p):return json.loads(p.read_bytes())
def sha(b):return hashlib.sha256(b).hexdigest()
def table(name,rows):
 with (D/name).open('w',newline='') as f:
  w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
sraw=(ROOT/'state/campaign-v1/latest-snapshot.json').read_bytes();snap=json.loads(sraw)
(D/'scheduler-snapshot.json').write_bytes(sraw)
fitraw=(ROOT/'reports/pilot-v1/analysis.json').read_bytes();fit=json.loads(fitraw)['fit'];(D/'pilot-analysis.json').write_bytes(fitraw)
records={};hashes={}
for p in (ROOT/'state/campaign-v1/records').glob('*.json'):
 b=p.read_bytes();records[p.stem]=json.loads(b);hashes[p.stem]=sha(b)
running={l.split('|')[0] for l in snap['squeue'].splitlines() if '|RUNNING|' in l}
location={};tasks={}
for g in ['main_le80','tail_gt80']:
 for m in read(ROOT/f'state/campaign-v1/{g}/manifest.json')['molecules']:
  k=m['inchikey'];idx=m['array_index']
  for chunk,indices in snap['array_indices'].items():
   if (chunk=='tail_gt80')!=(g=='tail_gt80'):continue
   if idx in indices:location[k]=chunk;tasks[k]=f"{snap['groups'][chunk]}_{idx}"
counts=defaultdict(Counter);dispositions=[];walls=[]
for m in read(ROOT/'state/campaign-v1/eligible.json'):
 k=m['inchikey'];r=records.get(k,{});status=r.get('status','running' if tasks.get(k) in running else 'not_yet_run')
 if status.startswith('running_'):status='running'
 assert status in ['converged','failed','running','not_yet_run'],status
 chunk=location.get(k,'pilot_reuse');counts[chunk][status]+=1
 dispositions.append({'inchikey':k,'name':m['name'],'chunk':chunk,'status':status,'failure_mode':r.get('failure_mode',''),'record_sha256':hashes.get(k,'')})
 if status=='converged':
  seconds=sum(r['stages'][s]['wall_seconds'] for s in ['opt','cosmo']);atoms=m['atoms'];pred=math.exp(fit['intercept'])*atoms**fit['exponent']*fit['residual_smearing']
  assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  walls.append({'inchikey':k,'name':m['name'],'group':m['group'],'atoms':atoms,'wall_hours':seconds/3600,'pilot_mean_hours':pred/3600,'residual_hours':(seconds-pred)/3600,'record_sha256':hashes[k],'cpu_model':r['cpu_model']})
assert len(dispositions)==5824
cats=['converged','failed','running','not_yet_run'];progress=[{'chunk':g,**{s:counts[g][s] for s in cats}} for g in sorted(counts)]
table('campaign-dispositions.csv',dispositions);table('campaign-progress.csv',progress);table('walltime-vs-atoms.csv',walls)
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(12,9),layout='constrained');left=np.zeros(len(progress))
for s,col in zip(cats,['#46906b','#b95c4b','#dbb45d','#b5bbc3']):
 vals=np.array([r[s] for r in progress]);ax.barh([r['chunk'].replace('main_chunk_','Main ').replace('tail_gt80','Tail >80').replace('pilot_reuse','Pilot / preflight') for r in progress],vals,left=left,color=col,label=s.replace('_',' '));left+=vals
ax.set(xlabel='Structures',title='Campaign progress / 5,824');ax.legend(loc='upper center',bbox_to_anchor=(.5,-.10),ncol=4);fig.savefig(D/'campaign-progress.png',dpi=300);plt.close(fig)
fig,ax=plt.subplots(figsize=(11,7),layout='constrained')
for group,color,label in [('main_le80','#3274a1','Main ≤80 atoms'),('tail_gt80','#c47c26','Tail >80 atoms')]:
 rs=[r for r in walls if r['group']==group];ax.scatter([r['atoms'] for r in rs],[r['wall_hours'] for r in rs],s=22,alpha=.55,color=color,label=label)
x=np.linspace(15,80,200);ax.plot(x,np.exp(fit['intercept'])*x**fit['exponent']*fit['residual_smearing']/3600,color='black',label='Milan pilot mean fit (15–80 atoms)');ax.set(xlabel='Atom count (including hydrogen)',ylabel='OPT + COSMORS wall time (hours)',title=f'Accepted ORCA timings (n = {len(walls):,})');ax.legend();fig.savefig(D/'walltime-vs-atoms.png',dpi=300);plt.close(fig)
# Distribution uses only results with completed audit evidence, never an in-progress batch.
old=read(R/'octanol-catchup-2026-09-17/numerical-audit.json');assert old['failed']==0
lookup={}
for row in csv.DictReader((R/'measured-expansion-2026-09-15/all-predictions.csv').open()):lookup[row['input_inchikey']]=(Path(row['result_path']),row['result_sha256'])
for p in [*R.glob('post*-octanol-2026-09-15/numerical-audit.json'),R/'octanol-catchup-2026-09-17/numerical-audit.json',*[R/folder/'numerical-audit.json' for folder in ['octanol-followup-2026-09-17','octanol-post3883-2026-09-17','octanol-post4060-2026-09-17','octanol-post4172-2026-09-17','octanol-post4234-2026-09-17','octanol-post4244-2026-09-17','octanol-post4261-2026-09-17']]]:
 a=read(p);assert a['failed']==0
 for row in a['rows']:
  if row['status']=='passed':lookup[row['inchikey']]=(p.parent/'octanol'/f"{row['inchikey']}.json",row['result_sha256'])
values=[]
for k,(p,h) in lookup.items():
 raw=p.read_bytes();assert sha(raw)==h;r=json.loads(raw)
 if r['prediction']['status']=='predicted':values.append({'inchikey':k,'name':r['name'],'logKow':r['prediction']['log10_K_concentration'],'result_path':str(p),'result_sha256':h})
table('computed-value-distribution.csv',values)
fig,ax=plt.subplots(figsize=(10,7),layout='constrained');ax.hist([r['logKow'] for r in values],bins=40,color='#3274a1',edgecolor='white');ax.set(xlabel='Predicted logKow (dry octanol/water)',ylabel='Structures',title=f'Audited octanol predictions (n = {len(values):,})');fig.savefig(D/'computed-value-distribution.png',dpi=300);plt.close(fig)
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scheduler_snapshot_utc':snap['utc'],'counts':dict(Counter(r['status'] for r in dispositions)),'denominator':5824,'audited_octanol':len(values),'accepted_timing_n':len(walls),'figures_require_visual_review':True}
(D/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
(D/'REPORT.md').write_text('# Cumulative progress figures\n\n'+json.dumps(summary,indent=2)+'\n\nFigures are 300 dpi with CSV data. Counts combine the saved scheduler snapshot and per-entry records read shortly afterward; this is a dated progress snapshot, not the historical release freeze. Timing points are accepted completed ORCA runs; failures remain in campaign counts but are excluded from the timing scatter. The pilot curve is drawn only over its sampled 15–80 atom range. The distribution contains independently audited, unadjusted dry-octanol predictions, not experimental values. Parity and experimental accuracy are reported separately in `/mnt/r/plastchem-euler/validation-opera-refresh-2026-09-17/`. No released or batch-2 artifacts were modified.\n')
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(sha(p.read_bytes())+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
print(json.dumps(summary),flush=True)
