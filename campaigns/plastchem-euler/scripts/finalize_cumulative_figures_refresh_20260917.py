"""Reconcile nonterminal figure rows with the pinned scheduler snapshot, then seal."""
from pathlib import Path
import csv,json,hashlib,collections,datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
ROOT=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/cumulative-figures-2026-09-17T1132')
snap=json.loads((D/'scheduler-snapshot.json').read_text());rows=list(csv.DictReader((D/'campaign-dispositions.csv').open()));running={x.split('|')[0] for x in snap['squeue'].splitlines() if '|RUNNING|' in x};acct={x.split('|')[0]:x.split('|')[3] for x in snap['sacct'].splitlines() if x and len(x.split('|'))>3};tasks={}
for group in ['main_le80','tail_gt80']:
 for m in json.loads((ROOT/f'state/campaign-v1/{group}/manifest.json').read_text())['molecules']:
  for chunk,indices in snap['array_indices'].items():
   if (chunk=='tail_gt80')==(group=='tail_gt80') and m['array_index'] in indices:tasks[m['inchikey']]=f"{snap['groups'][chunk]}_{m['array_index']}"
for row in rows:
 if row['status'] in ['converged','failed']:continue
 task=tasks.get(row['inchikey']);state=acct.get(task,'')
 if task in running:row['status']='running'
 elif state and state not in ['PENDING','RUNNING']:row['status']='awaiting_verification'
 elif row['status']=='running':row['status']='awaiting_verification'
 else:row['status']='not_yet_run'
counts=collections.defaultdict(collections.Counter)
for row in rows:counts[row['chunk']][row['status']]+=1
cats=['converged','failed','running','awaiting_verification','not_yet_run'];progress=[{'chunk':chunk,**{k:counts[chunk][k] for k in cats}} for chunk in sorted(counts)]
for filename,data in [('campaign-dispositions.csv',rows),('campaign-progress.csv',progress)]:
 with (D/filename).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(12,10),layout='constrained');left=np.zeros(len(progress));labels=[r['chunk'].replace('main_chunk_','Main ').replace('tail_gt80','Tail >80').replace('pilot_reuse','Pilot / preflight') for r in progress]
for status,color in zip(cats,['#46906b','#b95c4b','#dbb45d','#9b83bd','#b5bbc3']):
 vals=np.array([r[status] for r in progress]);ax.barh(labels,vals,left=left,color=color,label=status.replace('_',' '));left+=vals
ax.set(xlabel='Structures',title='Campaign progress / 5,824');ax.legend(loc='upper center',bbox_to_anchor=(.5,-.10),ncol=3);fig.savefig(D/'campaign-progress.png',dpi=300);plt.close(fig)
s=json.loads((D/'summary.json').read_text());s['counts']=dict(collections.Counter(r['status'] for r in rows));s['scheduler_running_lines']=len(running);assert s['counts']['running']<=len(running)<=64;assert sum(s['counts'].values())==5824
s['reconciled_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat();(D/'summary.json').write_text(json.dumps(s,indent=2)+'\n')
(D/'REPORT.md').write_text('# Cumulative progress figures\n\n'+json.dumps(s,indent=2)+'\n\nThe pinned scheduler snapshot determines running status. Accepted/failed records read shortly afterward retain their verified dispositions; scheduler-terminal returns not yet verified are explicitly awaiting verification. This is a progress snapshot, not an atomic freeze. It replaces the draft chart that combined stale per-record running states with the newer queue. Pilot / preflight contains 55 reused accepted pilot results and three preparation failures.\n\nFigures are 300 dpi with CSV data. Timings cover accepted ORCA runs on AMD EPYC 7763; failed attempts remain in campaign counts but are omitted from the scatter. The pilot curve is limited to its sampled 15–80 atom range. Distribution values are audited dry-octanol predictions. Experimental parity is separate at /mnt/r/plastchem-euler/validation-opera-refresh-2026-09-17/. Earlier released and batch-2 packages remain unchanged. Reproduce with the saved build script followed by the saved finalizer.\n')
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
print(json.dumps(s))
