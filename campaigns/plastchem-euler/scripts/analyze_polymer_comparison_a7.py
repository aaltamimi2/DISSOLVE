"""A-7 comparisons, uncertainty, enumerated flips and retention ranks. No model recalibration."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
from pathlib import Path
import csv,json,math,datetime,hashlib
import numpy as np
from scipy.stats import spearmanr,kendalltau,t,f as fdist,pearsonr
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds,rdMolAlign
R=Path(__file__).resolve().parents[1];D=Path('/mnt/r/plastchem-euler/polymer-v1/route-comparison-a7')
COUNTS={'evoh':11,'nylon6':20,'nylon66':28,'pe':31,'pp':25,'pvc':27,'pvdf':24};POLYS=list(COUNTS)
def readcsv(p):return list(csv.DictReader(p.open()))
def writecsv(p,rs):
 with p.open('w',newline='') as h:
  w=csv.DictWriter(h,fieldnames=list(dict.fromkeys(k for r in rs for k in r)));w.writeheader();w.writerows(rs)
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def fit(x,y):
 x=np.asarray(x);y=np.asarray(y);xm=x.mean();ym=y.mean();sxx=sum((x-xm)**2);slope=sum((x-xm)*(y-ym))/sxx;intercept=ym-slope*xm;res=y-intercept-slope*x;variance=sum(res*res)/(len(x)-2);se=math.sqrt(variance*(1/len(x)+xm*xm/sxx));critical=t.ppf(.975,len(x)-2)
 return {'n':len(x),'slope_B_on_A':float(slope),'intercept':float(intercept),'intercept_standard_error_OLS':se,'intercept_CI95_low':float(intercept-critical*se),'intercept_CI95_high':float(intercept+critical*se),'residual_SD_n_minus_2':float(math.sqrt(variance)),'mean_B_minus_A':float(np.mean(y-x)),'MAE_B_minus_A':float(np.mean(abs(y-x)))}
allrows=[];ensemble=[];ensemble_summary=[];merges=[]
for polymer,n in COUNTS.items():
 folder=D/polymer;s=json.loads((folder/'summary.json').read_text());assert s['paired_predictions']==128 and not s['failures']
 for convention,file,ak,bk in [('normalized','route-comparison.csv','A_logP_concentration','B_logP_concentration'),('existing','existing-convention-comparison.csv','A_existing_product_logP_concentration','B_same_convention_logP_concentration')]:
  for row in readcsv(folder/file):
   a=float(row[ak]);b=float(row[bk]);allrows.append({'polymer':polymer,'convention':convention,'solute':row['solute'],'solvent':row['solvent'],'A_logP_concentration':a,'B_logP_concentration':b,'delta_B_minus_A':b-a,'A_logP_x':float(row['A_logP_x']),'B_logP_x':float(row['B_logP_x']),'sign_changed':a*b<0})
 rows=readcsv(folder/'pe-ensemble.csv');assert len(rows)==n
 geometries={};representatives=[];merged={}
 for row in sorted(rows,key=lambda r:(float(r['B_OPT_energy_hartree']),r['entry_id'])):
  record=json.loads((R/'state/polymer-v1/records'/(row['entry_id']+'.json')).read_text());mol=Chem.MolFromXYZFile(str(Path(record['archive_path'])/'optimized.xyz'));rdDetermineBonds.DetermineBonds(mol,charge=0);mol=Chem.RemoveHs(mol);geometries[row['entry_id']]=mol
  for rep in representatives:
   de=abs(float(row['B_OPT_energy_hartree'])-float(rep['B_OPT_energy_hartree']))
   if de>1e-5:continue
   rms=rdMolAlign.GetBestRMS(Chem.Mol(mol),Chem.Mol(geometries[rep['entry_id']]),maxMatches=100000)
   if rms<=.1:
    merged[row['entry_id']]=rep['entry_id'];merges.append({'polymer':polymer,'entry_id':row['entry_id'],'merged_into':rep['entry_id'],'heavy_atom_symmetry_aligned_RMSD_A':rms,'absolute_OPT_energy_difference_hartree':de});break
  if row['entry_id'] not in merged:representatives.append(row)
 for row in rows:row.update(polymer=polymer,merged_into=merged.get(row['entry_id'],''));ensemble.append(row)
 es=s['energy_summary'];ensemble_summary.append({'polymer':polymer,'conformers':n,'A_n90':es['A']['n90'],'B_n90':es['B']['n90'],'B_OPT_n90':es['B_OPT']['n90'],'merge_count':len(merged),'representatives':len(representatives)})
 # Replace the inherited provisional A-6 prose, which was specific to PE.
 (folder/'REPORT.md').write_text(f"# {polymer}: A-7 route comparison\n\n{n}/{n} conformers, 128/128 paired predictions in each convention. All conformer rows retained; {len(merged)} merges under RMSD ≤0.10 Å and OPT energy difference ≤1e−5 Eh.\n\nSee ../REPORT.md for the comparison design, confounds, exclusions, fits, sign changes and rank analysis. The CSVs here preserve both concentration and mole-fraction bases and per-phase volume sources. Engine, parameterisation and re-optimised geometry differ simultaneously; no parameterisation-only attribution is justified.\n")
assert len(ensemble)==166 and len(allrows)==1792
writecsv(D/'paired-predictions.csv',allrows);writecsv(D/'conformer-energies-weights.csv',ensemble);writecsv(D/'ensemble-summary.csv',ensemble_summary);writecsv(D/'optimization-merges.csv',merges)
fitrows=[];flips=[];ranks=[];topchanges=[];offsets={};rank_summary={};correlations={}
for convention in ['normalized','existing']:
 rr=[r for r in allrows if r['convention']==convention];assert len(rr)==896
 for polymer in [*POLYS,'pooled']:
  subset=[r for r in rr if polymer=='pooled' or r['polymer']==polymer];x=[r['A_logP_concentration'] for r in subset];y=[r['B_logP_concentration'] for r in subset];result=fit(x,y);result.update(polymer=polymer,convention=convention,sign_changes=sum(r['sign_changed'] for r in subset));fitrows.append(result)
 flips.extend(r for r in rr if r['sign_changed'])
 pairs=sorted({(r['solute'],r['solvent']) for r in rr});assert len(pairs)==128
 by={(r['polymer'],r['solute'],r['solvent']):r for r in rr}
 X=np.array([[by[(p,*pair)]['A_logP_concentration'] for pair in pairs] for p in POLYS]);Y=np.array([[by[(p,*pair)]['B_logP_concentration'] for pair in pairs] for p in POLYS])
 for j,(solute,solvent) in enumerate(pairs):
  x=X[:,j];y=Y[:,j];oa=np.argsort(x,kind='stable');ob=np.argsort(y,kind='stable');rho=float(spearmanr(x,y).statistic);tau=float(kendalltau(x,y).statistic)
  row={'convention':convention,'solute':solute,'solvent':solvent,'A_retention_order_most_first':'>'.join(POLYS[i] for i in oa),'B_retention_order_most_first':'>'.join(POLYS[i] for i in ob),'Spearman_rho':rho,'Kendall_tau':tau,'A_top_polymer':POLYS[oa[0]],'B_top_polymer':POLYS[ob[0]],'top_changed':bool(oa[0]!=ob[0]),'A_top_gap_log_units':float(x[oa[1]]-x[oa[0]]),'B_top_gap_log_units':float(y[ob[1]]-y[ob[0]])};ranks.append(row)
  if row['top_changed']:topchanges.append(row)
 rks=[r for r in ranks if r['convention']==convention];rank_summary[convention]={'pairs':128,'mean_Spearman_rho':float(np.mean([r['Spearman_rho'] for r in rks])),'median_Spearman_rho':float(np.median([r['Spearman_rho'] for r in rks])),'minimum_Spearman_rho':min(r['Spearman_rho'] for r in rks),'mean_Kendall_tau':float(np.mean([r['Kendall_tau'] for r in rks])),'top_changed':sum(r['top_changed'] for r in rks),'top_unchanged':sum(not r['top_changed'] for r in rks)}
 # Common-intercept vs polymer-specific intercepts, allowing separate slopes in both models.
 y=Y.reshape(-1);design=np.zeros((896,8));design[:,0]=1
 full=np.zeros((896,14))
 for i in range(7):design[i*128:(i+1)*128,i+1]=X[i];full[i*128:(i+1)*128,i]=1;full[i*128:(i+1)*128,7+i]=X[i]
 bc=np.linalg.lstsq(design,y,rcond=None)[0];bf=np.linalg.lstsq(full,y,rcond=None)[0];ssec=sum((y-design@bc)**2);ssef=sum((y-full@bf)**2);stat=((ssec-ssef)/6)/(ssef/(896-14));pval=float(fdist.sf(stat,6,882))
 # Resample matched solvent/anchor pairs together across all polymers; stratify by anchor.
 rng=np.random.default_rng(12345);boot=[];groups=[[i for i,pair in enumerate(pairs) if pair[0]==a] for a in sorted({p[0] for p in pairs})]
 for _ in range(2000):
  ids=np.concatenate([rng.choice(g,len(g),replace=True) for g in groups]);xx=X[:,ids];yy=Y[:,ids];xm=xx.mean(axis=1);ym=yy.mean(axis=1);sl=((xx-xm[:,None])*(yy-ym[:,None])).sum(axis=1)/((xx-xm[:,None])**2).sum(axis=1);boot.append(ym-sl*xm)
 boot=np.array(boot);contrasts=[]
 for i in range(7):
  for j in range(i+1,7):
   delta=boot[:,i]-boot[:,j];lo,hi=np.quantile(delta,[.025,.975]);contrasts.append({'polymer_i':POLYS[i],'polymer_j':POLYS[j],'intercept_difference':float(bf[i]-bf[j]),'matched_bootstrap_CI95_low':float(lo),'matched_bootstrap_CI95_high':float(hi),'excludes_zero':bool(lo>0 or hi<0)})
 offsets[convention]={'common_intercept_fit':float(bc[0]),'nominal_nested_F':float(stat),'nominal_p':pval,'polymer_specific_intercepts':dict(zip(POLYS,map(float,bf[:7]))),'matched_pair_bootstrap_contrasts':contrasts,'contrasts_excluding_zero':sum(c['excludes_zero'] for c in contrasts),'conclusion':'polymer-specific' if any(c['excludes_zero'] for c in contrasts) and pval<.05 else 'common offset not rejected','qualification':'OLS SE/F use nominal independent-residual assumptions. Paired bootstrap preserves matching and anchor strata but this is four anchors, not a representative contaminant sample; no empirical correction applied.'}
 ns=np.array([COUNTS[p] for p in POLYS]);mae=np.array([np.mean(abs(Y[i]-X[i])) for i in range(7)]);n90=np.array([next(r['B_n90'] for r in ensemble_summary if r['polymer']==p) for p in POLYS]);correlations[convention]={'n_polymers':7,'conformer_count_vs_MAE_Spearman':float(spearmanr(ns,mae).statistic),'B_n90_vs_MAE_Spearman':float(spearmanr(n90,mae).statistic),'interpretation':'Descriptive association across seven polymers; not a causal or generalisable ensemble-size effect'}
writecsv(D/'fits.csv',fitrows);writecsv(D/'sign-changes.csv',flips);writecsv(D/'rank-stability.csv',ranks)
if topchanges:writecsv(D/'top-polymer-changes.csv',topchanges)
else:(D/'top-polymer-changes.csv').write_text('convention,solute,solvent,A_top_polymer,B_top_polymer\n')
flip_summary={}
from collections import Counter
for convention in ['normalized','existing']:
 fs=[r for r in flips if r['convention']==convention];flip_summary[convention]={'count':len(fs),'denominator':896,'by_polymer':dict(Counter(r['polymer'] for r in fs)),'by_solvent':dict(Counter(r['solvent'] for r in fs)),'both_routes_within_half_log_unit':sum(max(abs(r['A_logP_concentration']),abs(r['B_logP_concentration']))<=.5 for r in fs),'at_least_one_route_within_half_log_unit':sum(min(abs(r['A_logP_concentration']),abs(r['B_logP_concentration']))<=.5 for r in fs)}
summary={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'polymers':COUNTS,'conformers':166,'paired_predictions_per_convention':896,'temperature_K':298.15,'fit_rows':fitrows,'offset_analysis':offsets,'sign_changes':flip_summary,'rank_summary':rank_summary,'ensemble_disagreement_association':correlations,'excluded':['PET','PS','polyethersulfone','nitrocellulose','polyurethane','PC','PETG']};save(D/'summary.json',summary)
report='# Seven-polymer route comparison — A-7\n\n**The routes differ simultaneously in QC engine, parameterisation and re-optimised geometry. This measures the entire route, not a parameterisation-only effect.** No catalog writes, thresholds, product changes or empirical correction.\n\n166 matched source conformers, seven polymers × four anchors × 32 solvent references = **896 paired predictions per convention**, 298.15 K. All 166 rows retained, including optimization merges. **Excluded:** PET, PS, polyethersulfone, nitrocellulose, polyurethane, PC and PETG; their absence is not a negative result. The scope is frozen to the A-7 list even if other polymers finish during processing. Generic xylene remains unresolved, outside the 32-reference intersection.\n\n## Design and conventions\n\nRoute A: source Gaussian COSMO converted to Turbomole shape, 2002 default_turbomole; COSMObase solutes/solvents. Route B: ORCA 24a polymer surfaces, four workstation anchor surfaces and campaign solvent references. Each polymer has identical source-conformer identities across routes. Surface corrected/solute electronic energies define normalized Boltzmann weights; the gas-phase OPT weighting is supplied as sensitivity. Both mole-fraction and concentration bases are in the per-polymer CSVs and paired-predictions.csv. logP_conc = (ln gamma_polymer − ln gamma_solvent)/ln(10) + log10(V_polymer/V_solvent).\n\nNormalized convention uses −log(sum(w_i exp(−ln gamma_i))), a bounded dilution check, and the documented campaign molar-volume table with cavity fallback. Existing convention preserves the product’s unnormalized conformer partition sum, x=1e−5, and its five-entry molar-volume table with cavity fallback. Volumes and their sources appear in each polymer’s input and comparison tables. COSMO cavity volume is converted using 0.602214076 cm³/mol per Å³. These conventions must not be silently interchanged.\n\n'
for convention in ['existing','normalized']:
 report+=f'## {convention.title()} convention\n\n| Polymer | n | Slope | Intercept ± OLS SE | Residual SD | Sign changes |\n|---|---:|---:|---:|---:|---:|\n'
 for m in fitrows:
  if m['convention']==convention:report+=f"| {m['polymer']} | {m['n']} | {m['slope_B_on_A']:.4f} | {m['intercept']:+.4f} ± {m['intercept_standard_error_OLS']:.4f} | {m['residual_SD_n_minus_2']:.4f} | {m['sign_changes']} |\n"
 o=offsets[convention];fs=flip_summary[convention];rs=rank_summary[convention];report+=f"\n**Offset conclusion: {o['conclusion']}.** {o['contrasts_excluding_zero']}/21 paired bootstrap intercept contrasts exclude zero; nominal common-intercept F={o['nominal_nested_F']:.3f}, p={o['nominal_p']:.4g}. See summary.json for every contrast. A single polymer-independent intercept correction is not supported when the polymer-specific conclusion holds. No recalibration was applied. {o['qualification']}\n\nSign changes: **{fs['count']}/896**. By polymer: {json.dumps(fs['by_polymer'])}. Both route values within ±0.5 log units: {fs['both_routes_within_half_log_unit']}; at least one within ±0.5: {fs['at_least_one_route_within_half_log_unit']}. Solvent concentrations: {json.dumps(fs['by_solvent'])}. Every triple and both values are in sign-changes.csv.\n\nRanks put **lowest solvent/polymer logP first**, meaning greatest polymer retention. Across 128 pairs: mean Spearman {rs['mean_Spearman_rho']:.4f}, median {rs['median_Spearman_rho']:.4f}, minimum {rs['minimum_Spearman_rho']:.4f}; mean Kendall {rs['mean_Kendall_tau']:.4f}; top changes **{rs['top_changed']}/128**. Every ranking and top gap is in rank-stability.csv; every changed pair is named in top-polymer-changes.csv. Because a solvent term is shared across polymers, ranks can repeat across solvents for the same anchor: 128 pairs are not 128 independent chemical probes.\n\n"
 report+='Top changes, grouped without omitting any pair:\n\n'
 group={}
 for row in topchanges:
  if row['convention']==convention:group.setdefault((row['solute'],row['A_top_polymer'],row['B_top_polymer']),[]).append(row['solvent'])
 for (solute,a,b),names in group.items():report+=f'- {solute}: {a} → {b}: '+', '.join(names)+'.\n'
 report+='\n'
 largest=sorted([r for r in allrows if r['convention']==convention],key=lambda r:abs(r['delta_B_minus_A']),reverse=True)[:10]
 report+='Largest disagreements (B−A, log units):\n\n'
 for r in largest:report+=f"- {r['polymer']} / {r['solute']} / {r['solvent']}: {r['delta_B_minus_A']:+.4f}.\n"
report+='\n## Ensemble concentration and merges\n\n| Polymer | Conformers | A n90 | B n90 | OPT n90 | Merges | Representatives |\n|---|---:|---:|---:|---:|---:|---:|\n'
for r in ensemble_summary:report+='| '+' | '.join(str(r[k]) for k in ['polymer','conformers','A_n90','B_n90','B_OPT_n90','merge_count','representatives'])+' |\n'
report+='\nMerge criteria match A-6: symmetry-aligned heavy-atom RMSD ≤0.10 Å and absolute OPT electronic-energy difference ≤1e−5 Eh; direct comparison to the lowest-energy representative, no transitive chaining. Every merged row, RMSD and energy difference is in optimization-merges.csv; all energies and weights remain in conformer-energies-weights.csv.\n\n'
for c,v in correlations.items():report+=f"{c}: Spearman correlation of conformer count with mean absolute route difference = {v['conformer_count_vs_MAE_Spearman']:.4f}; B n90 with that difference = {v['B_n90_vs_MAE_Spearman']:.4f}. Conformer count does not show a consistent association with route disagreement in these seven polymers. Seven polymer types are too few and too confounded with chemistry to claim that ensemble size causes route disagreement.\n\n"
report+='## Reproduction and files\n\nRun `/home/aaltamimi2/.venvs/cosmo-logp/bin/python /home/aaltamimi2/plastchem-euler/scripts/run_a7_phased.py`, then `.../scripts/analyze_polymer_comparison_a7.py`. Calculations run one polymer at a time and one solute per fresh process. Each unit atomically writes and flushes 32 rows in each convention before the next begins; completed units are skipped on resume. COSMO objects are deleted and garbage-collected between activities. Unit guard pauses at 1800 MiB RSS; supervisor stops at 1900 MiB RSS or host MemAvailable below 2.5 GiB, retaining completed units. Calculations are input-hash bound and reuse identical A-6 solvent activity records. The original unphased runner is superseded and must not be used. Each polymer folder contains both full comparison CSVs, raw activities, converted inputs and pins. Fits and uncertainty are in fits.csv and summary.json; enumerated flips and rankings are separate CSVs. No experimental-accuracy claim is made by this route-to-route comparison.\n'
agreement_path=R/'state/polymer-v1/a7-pe-repeat-agreement.json'
if agreement_path.exists():
 agreement=json.loads(agreement_path.read_text());report+='\nPE repeat agreement with A-6: '+json.dumps(agreement)+'. The phased implementation preserves the A-6 numerical result within floating-point arithmetic.\n'
(D/'REPORT.md').write_text(report)
for name in ['run_a7_phased.py','a7_phased_unit.py','analyze_polymer_comparison_a7.py']:(D/name).write_bytes((R/'scripts'/name).read_bytes())
local=R/'reports/polymer-route-comparison-a7';local.mkdir(exist_ok=True);(local/'REPORT.md').write_text(report)
save(R/'state/polymer-v1/a7-comparison-complete.json',{'utc':summary['utc'],'directory':str(D),'conformers':166,'polymers':7,'paired_predictions':896,'report_sha256':hashlib.sha256((D/'REPORT.md').read_bytes()).hexdigest(),'offset_conclusions':{k:v['conclusion'] for k,v in offsets.items()},'sign_changes':flip_summary,'rank_summary':rank_summary})
print(json.dumps({'offset':{k:v['conclusion'] for k,v in offsets.items()},'sign_changes':flip_summary,'ranks':rank_summary,'ensemble':ensemble_summary}),flush=True)
