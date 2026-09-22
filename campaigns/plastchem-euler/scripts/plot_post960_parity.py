"""Cumulative 53-entry parity artifact; preserves all earlier report figures."""
import csv,json,hashlib,datetime
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
D=Path('/mnt/r/plastchem-euler/post960-octanol-2026-09-15');P=Path('/mnt/r/plastchem-euler/measured-expansion-2026-09-15')
old=list(csv.DictReader((P/'best-measured-parity.csv').open()));new=list(csv.DictReader((D/'opera-parity.csv').open()));rows=old+new
fields=list(dict.fromkeys(k for r in rows for k in r))
with (D/'cumulative-parity.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
s=json.loads((D/'experimental-statistics.json').read_text())['cumulative_1040'];assert len(rows)==s['n']==53
plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,'xtick.labelsize':14,'ytick.labelsize':14,'legend.fontsize':14,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
fig,ax=plt.subplots(figsize=(10,10));fig.subplots_adjust(left=.13,right=.96,top=.92,bottom=.28)
for group,label,color in [(old,'Previous 45 entries','#4676A9'),(new,'8 additional OPERA matches','#D27624')]:
 ax.scatter([float(r['measured_logKow']) for r in group],[float(r['predicted_logKow']) for r in group],s=60,c=color,label=label,edgecolors='black',linewidths=.5)
ax.plot([-2,14],[-2,14],color='black',linewidth=1.2,label='1:1');ax.set(xlim=(-2,14),ylim=(-2,14),xlabel='Measured logKow',ylabel='Predicted dry-octanol/water logKow',title='Cumulative measured comparison');ax.set_aspect('equal');ax.legend(loc='upper left',frameon=False)
fig.text(.13,.19,f"n = 53 entries (48 connectivity skeletons)\nMAE {s['MAE']:.2f}; RMSE {s['RMSE']:.2f}; bias {s['bias']:+.2f} log units\nPredicted-on-measured slope {s['slope']:.3f}; intercept {s['intercept']:.3f}",va='top',fontsize=14,color='black')
fig.text(.13,.065,'Measured subset of 1,040 predictions; source citations in cumulative-parity.csv.\nShared-connectivity entries are not independent. No recalibration.',va='top',fontsize=14,color='black')
fig.savefig(D/'cumulative-parity.png',dpi=300);plt.close(fig)
(D/'software-sources'/Path(__file__).name).write_bytes(Path(__file__).read_bytes())
print('Wrote 53-entry CSV and 300 dpi PNG')
