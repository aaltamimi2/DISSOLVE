"""Clarify preflight grouping using the saved figure data; no scientific values change."""
from pathlib import Path
import csv,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
D=Path('/mnt/r/plastchem-euler/cumulative-figures-2026-09-17');rows=list(csv.DictReader((D/'campaign-progress.csv').open()))
plt.rcParams.update({k:14 for k in ['font.size','axes.titlesize','axes.labelsize','xtick.labelsize','ytick.labelsize','legend.fontsize']});plt.rcParams.update({'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(12,9),layout='constrained');left=np.zeros(len(rows))
labels=[r['chunk'].replace('main_chunk_','Main ').replace('tail_gt80','Tail >80').replace('pilot_reuse','Pilot / preflight') for r in rows]
for status,col in zip(['converged','failed','running','not_yet_run'],['#46906b','#b95c4b','#dbb45d','#b5bbc3']):
 vals=np.array([int(r[status]) for r in rows]);ax.barh(labels,vals,left=left,color=col,label=status.replace('_',' '));left+=vals
ax.set(xlabel='Structures',title='Campaign progress / 5,824');ax.legend(loc='upper center',bbox_to_anchor=(.5,-.10),ncol=4);fig.savefig(D/'campaign-progress.png',dpi=300);plt.close(fig)
p=D/'REPORT.md';p.write_text(p.read_text()+'\nThe Pilot / preflight group contains 55 reused accepted pilot results and three preparation failures with no submitted array task. The initial figure-build attempt stopped explicitly on a live running_cosmo status; the successful build groups running_opt/running_cosmo as running.\n')
(D/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
(D/'artifacts.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(D.iterdir()) if p.is_file() and p.name!='artifacts.sha256'))
