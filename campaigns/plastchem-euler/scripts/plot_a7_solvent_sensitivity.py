"""Scatterplots of exploratory solvent exclusions, preserving authoritative A-7 rows."""
import os
os.environ['MPLBACKEND']='Agg';os.environ['OPENBLAS_NUM_THREADS']='1'
from pathlib import Path
import csv,shutil,json,hashlib
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7');O=D/'solvent-sensitivity';F=O/'figures';F.mkdir(exist_ok=True)
LOCAL=Path('/home/aaltamimi2/plastchem-euler/a7-solvent-exclusion-plots');LOCAL.mkdir(exist_ok=True)
rows=list(csv.DictReader((D/'paired-predictions.csv').open()));scenarios=list(csv.DictReader((O/'exclusion-scenarios.csv').open()))
polys=['evoh','nylon6','nylon66','pe','pp','pvc','pvdf'];labels=['EVOH','Nylon 6','Nylon 6,6','PE','PP','PVC','PVDF'];colors=['#0072B2','#E69F00','#009E73','#CC79A7','#D55E00','#56B4E9','#555555']
plt.rcParams.update({'font.size':12,'axes.titlesize':12,'axes.labelsize':12,'xtick.labelsize':12,'ytick.labelsize':12,'legend.fontsize':12,'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','savefig.facecolor':'white'})
vals=[float(r[k]) for r in rows for k in ['A_logP_concentration','B_logP_concentration']];lo=np.floor(min(vals))-.3;hi=np.ceil(max(vals))+.3
handles=[Line2D([],[],marker='o',ls='',color=c,label=p,markersize=6) for c,p in zip(colors,labels)]+[Line2D([],[],color='black',ls='--',label='1:1'),Line2D([],[],color='black',label='OLS fit')]
artifacts=[]
def panel(ax,rr,s):
 for p,c in zip(polys,colors):
  z=[r for r in rr if r['polymer']==p];ax.scatter([float(r['A_logP_concentration']) for r in z],[float(r['B_logP_concentration']) for r in z],s=16,c=c,alpha=.65,edgecolors='none',rasterized=True)
 ax.plot([lo,hi],[lo,hi],'--',color='black',lw=1);xx=np.array([lo,hi]);ax.plot(xx,float(s['slope'])*xx+float(s['intercept']),color='black',lw=1.3)
 ax.set(xlim=(lo,hi),ylim=(lo,hi),xlabel='Route A: log₁₀ P(solvent/polymer)',ylabel='Route B: log₁₀ P(solvent/polymer)');ax.set_aspect('equal',adjustable='box');ax.grid(alpha=.15)
 name='All 32 solvents' if s['excluded']=='none' else 'Exclude '+s['excluded'].replace(';',' + ')
 ax.set_title(name,pad=12)
 ax.text(.04,.96,f"n = {int(s['n'])}   |   solvents = {s['solvents']}\nSlope = {float(s['slope']):.3f}   Intercept = {float(s['intercept']):+.3f}\nR² = {float(s['R2']):.3f}   Residual SD = {float(s['residual_SD']):.3f}",transform=ax.transAxes,va='top',fontsize=12,bbox=dict(facecolor='white',alpha=.93,edgecolor='none',pad=4))
def save(fig,stem):
 for ext in ['png','pdf']:
  p=F/(stem+'.'+ext);fig.savefig(p,dpi=300);artifacts.append(str(p))
  if ext=='png':shutil.copy2(p,LOCAL/p.name)
 plt.close(fig)
for convention in ['normalized','existing']:
 ss=[s for s in scenarios if s['convention']==convention];title='Normalized convention' if convention=='normalized' else 'Existing convention'
 for s in ss:
  ex=set(s['excluded'].split(';'));rr=[r for r in rows if r['convention']==convention and r['solvent'] not in ex]
  fig,ax=plt.subplots(figsize=(8.5,9));fig.subplots_adjust(left=.13,right=.97,bottom=.23,top=.90);panel(ax,rr,s);fig.suptitle(title,y=.965,fontsize=12)
  fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.065),ncol=3,frameon=False)
  fig.text(.5,.025,'Exploratory route comparison; exclusions do not establish experimental accuracy.',ha='center',fontsize=12)
  save(fig,convention+'--'+s['excluded'].replace(';','-and-').replace(' ','-'))
 # Six principal scenarios; the three-solvent exclusion has its own figure.
 fig,axs=plt.subplots(2,3,figsize=(20,14));fig.subplots_adjust(left=.055,right=.985,bottom=.14,top=.94,hspace=.30,wspace=.22)
 for ax,s in zip(axs.flat,ss[:6]):
  ex=set(s['excluded'].split(';'));rr=[r for r in rows if r['convention']==convention and r['solvent'] not in ex];panel(ax,rr,s)
 fig.suptitle(title+' — solvent exclusion sensitivity',y=.98,fontsize=12);fig.legend(handles=handles,loc='lower center',bbox_to_anchor=(.5,.055),ncol=9,frameon=False)
 fig.text(.5,.025,'Same axis limits throughout. Four anchors × seven polymers at 298.15 K. Full A-7 results remain unchanged.',ha='center',fontsize=12)
 save(fig,convention+'--scenario-overview')
(O/'plot-manifest.json').write_text(json.dumps({'source_sha256':hashlib.sha256((D/'paired-predictions.csv').read_bytes()).hexdigest(),'dpi':300,'artifacts':artifacts,'local_png_copies':str(LOCAL)},indent=2)+'\n')
print(json.dumps({'figures':len(artifacts)//2,'directory':str(F),'local_png_copies':str(LOCAL)}))
