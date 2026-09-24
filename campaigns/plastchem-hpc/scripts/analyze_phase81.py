"""Analyze only collected, hash-verified Euler Phase8.1 outputs; no COSMO compute."""
import os
os.environ['OPENBLAS_NUM_THREADS']='1'
import csv,json,math,hashlib,datetime
from pathlib import Path
import numpy as np
from scipy.special import logsumexp
from scipy.stats import t,f,spearmanr,kendalltau
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds,rdMolAlign
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/phase8-v1');O=D/'reports/phase81';O.mkdir(parents=True,exist_ok=True)
m=json.loads((D/'manifest.json').read_text());polys=list(m['polymers']);N=len(polys);den=N*128
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,rows):
 with (O/name).open('w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(dict.fromkeys(k for r in rows for k in r)) or ['status']);w.writeheader();w.writerows(rows)
def fit(x,y):
 x=np.asarray(x);y=np.asarray(y);design=np.c_[np.ones(len(x)),x];b=np.linalg.lstsq(design,y,rcond=None)[0];res=y-design@b;var=float(res@res/(len(x)-2));se=math.sqrt(var*np.linalg.inv(design.T@design)[0,0])
 return {'n':len(x),'slope':float(b[1]),'intercept':float(b[0]),'intercept_SE':se,'intercept_CI95_low':float(b[0]-t.ppf(.975,len(x)-2)*se),'intercept_CI95_high':float(b[0]+t.ppf(.975,len(x)-2)*se),'residual_SD':math.sqrt(var),'MAE':float(np.mean(abs(y-x))),'RMSE':float(np.sqrt(np.mean((y-x)**2))),'bias':float(np.mean(y-x))}
raw=[];executions=[]
for unit in m['phase81_units']:
 p=D/'phase81'/unit['polymer']/unit['solute'];c=json.loads((p/'complete.json').read_text());assert c['manifest_sha256']==sha(D/'manifest.json') and c['output_sha256']==sha(p/'predictions.csv');raw.extend(csv.DictReader((p/'predictions.csv').open()));executions.append(c)
assert len(raw)==den*4
write('executions.csv',[{k:v for k,v in r.items() if not isinstance(v,(dict,list))} for r in executions]);write('all-route-rows.csv',raw)
lookup={(r['polymer'],r['solute'],r['solvent'],r['convention'],r['route']):r for r in raw};paired=[]
for key in sorted({k[:-1] for k in lookup}):
 a=lookup[key+('A',)];b=lookup[key+('B',)];r=dict(zip(['polymer','solute','solvent','convention'],key));r['status']='paired' if a['status']==b['status']=='predicted' else 'failed'
 if r['status']=='paired':
  x=float(a['logP_concentration']);y=float(b['logP_concentration']);r.update(A=x,B=y,delta=y-x,A_logP_x=float(a['logP_x']),B_logP_x=float(b['logP_x']),sign_changed=x*y<0)
 paired.append(r)
write('paired-predictions.csv',paired);fits=[];flips=[];ranks=[];offsets={}
old=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7/paired-predictions.csv');repeat=[]
oldrows={(r['polymer'],r['solute'],r['solvent'],r['convention']):r for r in csv.DictReader(old.open())}
for r in paired:
 key=(r['polymer'],r['solute'],r['solvent'],r['convention'])
 if key in oldrows and r['status']=='paired':
  prior=oldrows[key];repeat.append(dict(zip(['polymer','solute','solvent','convention'],key),A_new_minus_A7=r['A']-float(prior['A_logP_concentration']),B_new_minus_A7=r['B']-float(prior['B_logP_concentration'])))
write('A7-repeat-agreement.csv',repeat)
for convention in ['normalized','existing']:
 rr=[r for r in paired if r['convention']==convention and r['status']=='paired']
 for polymer in [*polys,'pooled']:
  rows=[r for r in rr if polymer=='pooled' or r['polymer']==polymer];v=fit([r['A'] for r in rows],[r['B'] for r in rows]);v.update(polymer=polymer,convention=convention,sign_changes=sum(r['sign_changed'] for r in rows));fits.append(v)
 flips.extend(r for r in rr if r['sign_changed'])
 pairs=sorted({(r['solute'],r['solvent']) for r in rr});by={(r['polymer'],r['solute'],r['solvent']):r for r in rr};fullpairs=[p for p in pairs if all((q,*p) in by for q in polys)]
 X=np.array([[by[(p,*pair)]['A'] for pair in fullpairs] for p in polys]);Y=np.array([[by[(p,*pair)]['B'] for pair in fullpairs] for p in polys]);K=len(fullpairs)
 for j,(solute,solvent) in enumerate(fullpairs):
  x=X[:,j];y=Y[:,j];ia=np.argsort(x);ib=np.argsort(y);ranks.append({'convention':convention,'solute':solute,'solvent':solvent,'A_most_retaining_first':'>'.join(polys[i] for i in ia),'B_most_retaining_first':'>'.join(polys[i] for i in ib),'A_top':polys[ia[0]],'B_top':polys[ib[0]],'top_changed':bool(ia[0]!=ib[0]),'Spearman_rho':float(spearmanr(x,y).statistic),'Kendall_tau':float(kendalltau(x,y).statistic)})
 y=Y.ravel();common=np.zeros((N*K,N+1));common[:,0]=1;separate=np.zeros((N*K,2*N))
 for i in range(N):common[i*K:(i+1)*K,i+1]=X[i];separate[i*K:(i+1)*K,i]=1;separate[i*K:(i+1)*K,N+i]=X[i]
 bc=np.linalg.lstsq(common,y,rcond=None)[0];bs=np.linalg.lstsq(separate,y,rcond=None)[0];ssec=float(np.sum((y-common@bc)**2));sses=float(np.sum((y-separate@bs)**2));F=((ssec-sses)/(N-1))/(sses/(N*K-2*N));pval=float(f.sf(F,N-1,N*K-2*N))
 rng=np.random.default_rng(12345);groups=[[i for i,p in enumerate(fullpairs) if p[0]==a] for a in sorted({p[0] for p in fullpairs})];boot=[]
 for _ in range(2000):
  idx=np.concatenate([rng.choice(g,len(g),replace=True) for g in groups]);xx=X[:,idx];yy=Y[:,idx];xm=xx.mean(axis=1);ym=yy.mean(axis=1);slope=np.sum((xx-xm[:,None])*(yy-ym[:,None]),axis=1)/np.sum((xx-xm[:,None])**2,axis=1);boot.append(ym-slope*xm)
 boot=np.array(boot);contrasts=[]
 for i in range(N):
  for j in range(i+1,N):
   lo,hi=np.quantile(boot[:,i]-boot[:,j],[.025,.975]);contrasts.append({'polymer_i':polys[i],'polymer_j':polys[j],'difference':float(bs[i]-bs[j]),'CI95_low':float(lo),'CI95_high':float(hi),'excludes_zero':bool(lo>0 or hi<0)})
 offsets[convention]={'nominal_F':F,'nominal_p':pval,'common_intercept':float(bc[0]),'conclusion':'polymer-specific' if pval<.05 and any(r['excludes_zero'] for r in contrasts) else 'common offset not rejected','bootstrap_contrasts':contrasts,'complete_rank_pairs':K}
write('fits.csv',fits);write('sign-changes.csv',flips);write('rank-stability.csv',ranks);write('top-polymer-changes.csv',[r for r in ranks if r['top_changed']])
ensembles=[];esummaries=[];merges=[]
for p,rows in m['polymers'].items():
 esum={'polymer':p,'conformers':len(rows)};weights={}
 for route in ['A','B','B_OPT']:
  es=np.array([r[route+'_energy_hartree'] for r in rows]);de=(es-es.min())*2625.4996394799;lnw=-de/(.00831446261815324*298.15);w=np.exp(lnw-logsumexp(lnw));weights[route]=(de,w);esum[route+'_n90']=int(np.searchsorted(np.cumsum(sorted(w,reverse=True)),.9)+1)
 reps=[];merged={}
 for row in sorted(rows,key=lambda r:(r['B_OPT_energy_hartree'],r['entry_id'])):
  mol=Chem.MolFromXYZFile(str(D/row['optimized_xyz']));rdDetermineBonds.DetermineBonds(mol,charge=0);mol=Chem.RemoveHs(mol)
  for rep,other in reps:
   de=abs(row['B_OPT_energy_hartree']-rep['B_OPT_energy_hartree'])
   if de>1e-5:continue
   rms=rdMolAlign.GetBestRMS(Chem.Mol(mol),Chem.Mol(other),maxMatches=100000)
   if rms<=.1:merged[row['entry_id']]=rep['entry_id'];merges.append({'polymer':p,'entry_id':row['entry_id'],'merged_into':rep['entry_id'],'heavy_atom_RMSD_A':rms,'OPT_difference_hartree':de});break
  if row['entry_id'] not in merged:reps.append((row,mol))
 for i,row in enumerate(rows):
  r=dict(row,polymer=p,merged_into=merged.get(row['entry_id'],''))
  for route,(de,w) in weights.items():r[route+'_relative_energy_kj_mol']=float(de[i]);r[route+'_weight_298K']=float(w[i])
  ensembles.append(r)
 esum['merge_count']=len(merged);esummaries.append(esum)
write('conformer-energies-weights.csv',ensembles);write('ensemble-summary.csv',esummaries);write('optimization-merges.csv',merges)
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'complete':m['complete'],'excluded':m['excluded'],'denominator_per_convention':den,'paired_per_convention':{c:sum(r['convention']==c and r['status']=='paired' for r in paired) for c in ['normalized','existing']},'offsets':offsets,'fits':fits,'cpu_models':sorted({r['cpu_model'] for r in executions}),'total_unit_wall_seconds':sum(r['wall_seconds'] for r in executions),'max_rss_kib':max(r['peak_rss_kib'] for r in executions)}
(O/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
report='# Phase 8.1 — complete-polymer route comparison\n\nThe routes differ simultaneously in QC engine, parameterisation and re-optimised geometry. This measures the route as a whole; it does not isolate a parameterisation effect. Route A is openCOSMO-RS 2002 on converted Gaussian/COSMObase surfaces, **not the commercial COSMOtherm19 workbook**. Route B is openCOSMO-RS 24a on ORCA surfaces. No recalibration, catalog writes or product changes.\n\n'
report+=f"Scope frozen {m['utc']}: {N} complete polymers, {len(ensembles)} conformers, four anchors × 32 solvents = {den} paired predictions per convention at 298.15 K. Complete: {json.dumps(m['complete'])}. Excluded because incomplete: {json.dumps(m['excluded'])}. All source conformers retained, including merges.\n\n"
report+='Normalized convention uses normalized surface-energy Boltzmann weights, −ln Σ(w exp(−lnγ)), dilution plateau ≤0.005 log units, and documented molar-volume table with cavity fallback. Existing convention retains the unnormalized partition sum, x=1e−5, exact legacy constants, and five-entry volume table with cavity fallback. Both mole-fraction and concentration bases are supplied. logP(solvent/polymer) = (lnγ_polymer−lnγ_solvent)/ln10 + log10(V_polymer/V_solvent). Negative values mean greater polymer retention.\n\n'
report+=f"Repeat check against the frozen A-7 tables: {len(repeat)} paired rows across both conventions, maximum absolute A difference {max(abs(r['A_new_minus_A7']) for r in repeat):.6g}, B difference {max(abs(r['B_new_minus_A7']) for r in repeat):.6g} log units. All differences enumerated in A7-repeat-agreement.csv; the original A-7 results are untouched.\n\n"
from collections import Counter
for c in ['normalized','existing']:
 report+=f'## {c.title()} convention\n\n| Polymer | n | Slope | Intercept ± SE | Residual SD | Sign flips |\n|---|---:|---:|---:|---:|---:|\n'
 for r in fits:
  if r['convention']==c:report+=f"| {r['polymer']} | {r['n']} | {r['slope']:.4f} | {r['intercept']:.4f} ± {r['intercept_SE']:.4f} | {r['residual_SD']:.4f} | {r['sign_changes']} |\n"
 ff=[r for r in flips if r['convention']==c];rk=[r for r in ranks if r['convention']==c];off=offsets[c];nexclude=sum(x['excludes_zero'] for x in off['bootstrap_contrasts'])
 report+=f"\nOffset: **{off['conclusion']}**; {nexclude}/{N*(N-1)//2} matched, anchor-stratified bootstrap contrasts exclude zero. OLS uncertainty and nested F are nominal; the 128 pairs share four solutes and are not independent chemical probes. No correction applied.\n\nSign changes: **{len(ff)}/{den}**, enumerated in sign-changes.csv. By polymer: {dict(Counter(r['polymer'] for r in ff))}; by solvent: {dict(Counter(r['solvent'] for r in ff))}. At least one route within ±0.5 log unit for {sum(min(abs(r['A']),abs(r['B']))<=.5 for r in ff)} flips.\n\nRetention ranking uses lowest logP first. Top changes: **{sum(r['top_changed'] for r in rk)}/128**; mean Spearman {np.mean([r['Spearman_rho'] for r in rk]):.4f}, mean Kendall {np.mean([r['Kendall_tau'] for r in rk]):.4f}. Every changed pair is named in top-polymer-changes.csv; every full ranking is in rank-stability.csv.\n\nLargest route disagreements:\n\n"
 for r in sorted([r for r in paired if r['convention']==c and r['status']=='paired'],key=lambda r:abs(r['delta']),reverse=True)[:10]:report+=f"- {r['polymer']} / {r['solute']} / {r['solvent']}: {r['delta']:+.4f} log units.\n"
 ns=[len(m['polymers'][p]) for p in polys];maes=[next(r['MAE'] for r in fits if r['polymer']==p and r['convention']==c) for p in polys];n90=[next(r['B_n90'] for r in esummaries if r['polymer']==p) for p in polys]
 report+=f"\nEnsemble size versus mean absolute route disagreement: Spearman {spearmanr(ns,maes).statistic:.4f}; B n90 versus disagreement: {spearmanr(n90,maes).statistic:.4f}. Descriptive across {N} polymer chemistries, not evidence that ensemble size causes disagreement.\n\n"
report+='Across the two conventions, ensemble size does not consistently track route disagreement: the conformer-count association changes sign, and the n90 association is convention-dependent. These ten polymer chemistries do not isolate an ensemble-size effect.\n\n## Ensembles and execution\n\nElectronic-energy weights are used without vibrational/rotational free-energy or degeneracy corrections, matching A-7.\n\n| Polymer | n | A n90 | B n90 | OPT n90 | Merges |\n|---|---:|---:|---:|---:|---:|\n'
for r in esummaries:report+='| '+' | '.join(str(r[k]) for k in ['polymer','conformers','A_n90','B_n90','B_OPT_n90','merge_count'])+' |\n'
report+=f"\nMerge criterion: heavy-atom symmetry RMSD ≤0.1 Å and |ΔOPT energy| ≤1e−5 Eh to a representative; no rows removed. All relative energies and weights in conformer-energies-weights.csv.\n\nEuler CPU: {summary['cpu_models']}; total unit wall time {summary['total_unit_wall_seconds']/3600:.3f} CPU-h, peak RSS {summary['max_rss_kib']/1024:.1f} MiB. Inputs and worker hashes in every completed-unit record. Original workstation-anchor surfaces are retained as explicitly required by A-6/A-7; activity calculations run on Milan.\n\nReproduce: prepare with `{R}/scripts/prepare_phase8.py`; Euler array uses phase81.sbatch and phase8_worker.py in ~/plastchem-euler/phase8-v1; collect via scp; analyze with `/home/aaltamimi2/.venvs/cosmo-logp/bin/python {R}/scripts/analyze_phase81.py`. Raw activities and per-unit CSVs: {D}/phase81. Inputs: {D}/manifest.json. Phase 8.2 validates separately against the workbook; 8.3 remains held.\n"
(O/'REPORT.md').write_text(report);print(json.dumps({k:v for k,v in summary.items() if k not in ['offsets','fits']},indent=2))
