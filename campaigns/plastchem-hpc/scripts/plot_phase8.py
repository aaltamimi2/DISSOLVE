"""Comparison figures only; uniformly sized black text, PNG 300 dpi and CSV sources."""
import os
os.environ['MPLBACKEND']='Agg';os.environ['OPENBLAS_NUM_THREADS']='1';os.environ['MPLCONFIGDIR']='/home/aaltamimi2/plastchem-euler/state/matplotlib'
import csv,json,sys
from pathlib import Path
import numpy as np
import matplotlib.pyplot as plt
D=Path('/mnt/r/plastchem-euler/phase8-v1/reports')
plt.rcParams.update({'font.size':11,'axes.titlesize':11,'axes.labelsize':11,'xtick.labelsize':11,'ytick.labelsize':11,'legend.fontsize':11,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','savefig.dpi':300})
def read(p):return list(csv.DictReader(p.open()))
if '--only-phase82' not in sys.argv and (D/'phase81/paired-predictions.csv').exists():
 rows=read(D/'phase81/paired-predictions.csv');polys=sorted({r['polymer'] for r in rows});fig,axes=plt.subplots(1,2,figsize=(11,5.7),layout='constrained')
 for ax,c in zip(axes,['normalized','existing']):
  subset=[r for r in rows if r['convention']==c and r['status']=='paired'];values=[float(r[k]) for r in subset for k in ['A','B']];lo,hi=min(values)-.5,max(values)+.5
  for i,p in enumerate(polys):
   rr=[r for r in subset if r['polymer']==p];ax.scatter([float(r['A']) for r in rr],[float(r['B']) for r in rr],s=13,color=plt.cm.tab10(i),alpha=.7,label=p)
  ax.plot([lo,hi],[lo,hi],color='black',lw=1,ls='--');ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Route A: log₁₀ P(solvent/polymer)',ylabel='Route B: log₁₀ P(solvent/polymer)',title=c.title());ax.set_aspect('equal');ax.grid(alpha=.15)
 axes[1].legend(loc='upper left',bbox_to_anchor=(1.02,1),frameon=False);fig.savefig(D/'phase81/route-parity.png',dpi=300);plt.close(fig)
if (D/'phase82/logP-workbook-parity.csv').exists():
 rows=read(D/'phase82/logP-workbook-parity.csv');stats=json.loads((D/'phase82/summary.json').read_text())['logP_metrics'];solutes=list(dict.fromkeys(r['solute'] for r in rows));fig,axes=plt.subplots(1,2,figsize=(11,5.7),layout='constrained')
 for ax,c in zip(axes,['normalized','existing']):
  sub=[r for r in rows if r['convention']==c and r['status']=='predicted'];values=[float(r[k]) for r in sub for k in ['value','predicted']];lo,hi=min(values)-.5,max(values)+.5
  for i,s in enumerate(solutes):
   rr=[r for r in sub if r['solute']==s];ax.scatter([float(r['value']) for r in rr],[float(r['predicted']) for r in rr],s=18,color=plt.cm.tab10(i),alpha=.8,label=s)
  ax.plot([lo,hi],[lo,hi],color='black',ls='--',lw=1);ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Workbook log₁₀ P(solvent/PVC)',ylabel='New 24a log₁₀ P(solvent/PVC)',title=c.title());ax.set_aspect('equal');ax.grid(alpha=.15)
 axes[1].legend(loc='upper left',bbox_to_anchor=(1.02,1),frameon=False);fig.savefig(D/'phase82/workbook-logP-parity.png',dpi=300);plt.close(fig)
 captions='Route comparison: each point is a polymer/anchor/solvent combination. Dashed line is 1:1; neither route is experimental truth. Engine, parameters and geometry change together.\n\nWorkbook comparison: neutral phthalates at 298.15 K with PVC; workbook values are commercial COSMOtherm calculations, not measurements.\n\n'+json.dumps(stats,indent=2)+'\n'
 (D/'phase82/FIGURE_CAPTIONS.md').write_text(captions)
 cms=read(D/'phase82/verdict-confusion-matrices.csv');fig,axes=plt.subplots(2,2,figsize=(8,8),layout='constrained')
 for ax,(layout,basis) in zip(axes.flat,[(l,b) for l in ['blocked','paired'] for b in ['mol','wt']]):
  r=next(r for r in cms if r['layout']==layout and r['basis']==basis and r['regime']=='all');arr=np.array([[int(r['true_negative']),int(r['false_positive'])],[int(r['false_negative']),int(r['true_positive'])]]);ax.imshow(arr,cmap='Blues',vmin=0,vmax=max(1,arr.max())*2)
  for (i,j),value in np.ndenumerate(arr):ax.text(j,i,str(value),ha='center',va='center',color='black')
  ax.set(xticks=[0,1],yticks=[0,1],xticklabels=['No','Yes'],yticklabels=['No','Yes'],xlabel='24a verdict',ylabel='Workbook verdict',title=f'{layout.title()}, 15 {basis}%\nScored {r["scored"]}/{r["denominator"]}')
 fig.savefig(D/'phase82/miscibility-confusion.png',dpi=300);plt.close(fig)
