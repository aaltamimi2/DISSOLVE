"""Descriptive solvent exclusion sensitivity; no exclusions applied to A-7."""
import csv,json,hashlib
from pathlib import Path
import numpy as np
D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7');O=D/'solvent-sensitivity';O.mkdir(exist_ok=True)
rows=list(csv.DictReader((D/'paired-predictions.csv').open()))
def fit(rs):
 x=np.array([float(r['A_logP_concentration']) for r in rs]);y=np.array([float(r['B_logP_concentration']) for r in rs]);s,b=np.polyfit(x,y,1);e=y-(s*x+b)
 return dict(n=len(rs),slope=float(s),intercept=float(b),residual_SD=float(np.sqrt(sum(e*e)/(len(x)-2))),R2=float(1-sum(e*e)/sum((y-y.mean())**2)),MAE=float(np.mean(abs(y-x))))
def save(name,rs):
 with (O/name).open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=list(rs[0]));w.writeheader();w.writerows(rs)
rank=[];scenarios=[]
for c in ['normalized','existing']:
 rr=[r for r in rows if r['convention']==c];base=fit(rr)
 for solvent in sorted({r['solvent'] for r in rr}):
  sr=[r for r in rr if r['solvent']==solvent];v=fit([r for r in rr if r['solvent']!=solvent]);delta=np.array([float(r['delta_B_minus_A']) for r in sr])
  rank.append(dict(convention=c,solvent=solvent,n=28,mean_B_minus_A=float(delta.mean()),MAE=float(np.mean(abs(delta))),RMSE=float(np.sqrt(np.mean(delta*delta))),residual_SD_after_removal=v['residual_SD'],residual_SD_reduction=base['residual_SD']-v['residual_SD']))
 for excluded in [[],['water'],['triethylamine'],['chloroform'],['water','triethylamine'],['water','chloroform'],['water','chloroform','triethylamine']]:
  subset=[r for r in rr if r['solvent'] not in excluded];v=fit(subset);ss=0;df=0
  for p in sorted({r['polymer'] for r in subset}):
   z=fit([r for r in subset if r['polymer']==p]);ss+=z['residual_SD']**2*(z['n']-2);df+=z['n']-2
  scenarios.append(dict(convention=c,excluded=';'.join(excluded) or 'none',solvents=32-len(excluded),**v,within_polymer_residual_SD=float(np.sqrt(ss/df))))
rank.sort(key=lambda r:(r['convention'],-r['residual_SD_reduction']));save('solvent-influence.csv',rank);save('exclusion-scenarios.csv',scenarios)
(O/'README.md').write_text('Exploratory sensitivity only. All 32 solvents remain in the authoritative A-7 results. Solvent removal can improve in-sample agreement between routes; it does not establish experimental accuracy or justify excluding a solvent. Each excluded solvent removes 28 paired rows (seven polymers × four anchors). Pooled residual SD and residual SD from separate polymer regressions are reported; these are fitted scatter, distinct from uncorrected route MAE. Only four anchors were evaluated. The routes differ in engine, parameterisation and geometry simultaneously.\n')
for c in ['normalized','existing']:
 print(c,'TOP',json.dumps([r for r in rank if r['convention']==c][:5]));print('SCENARIOS',json.dumps([r for r in scenarios if r['convention']==c]))
