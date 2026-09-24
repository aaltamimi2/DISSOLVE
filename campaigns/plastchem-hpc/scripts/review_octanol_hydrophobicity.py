"""Describe the uncorrected residuals without excluding inconvenient observations."""
import csv,datetime,hashlib,json,math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];B=Path('/mnt/r/plastchem-euler/progress-2026-09-14');O=B/'octanol-hydrophobicity-review';O.mkdir(exist_ok=True)
source=B/'octanol-sign-review/best-measured-parity.csv';rows=list(csv.DictReader(source.open()))
stats=json.loads((B/'octanol-sign-review/sign-and-regression.json').read_text())['regression']
for r in rows:
 if r['input_inchikey']=='PGIBJVOPLXHHGS-UHFFFAOYSA-N':
  r['conditions_note'] += ' Source discrepancy: PubChem 9.05 versus cited primary abstract slow-stir 8.83 +/-0.05; full text unavailable; primary comparison unchanged.'
groups={}
for label,predicate in [('measured_below_4',lambda x:x<4),('measured_4_to_7',lambda x:4<=x<=7),('measured_above_7',lambda x:x>7)]:
 subset=[r for r in rows if predicate(float(r['measured_logKow']))];res=[float(r['residual_predicted_minus_measured']) for r in subset]
 groups[label]={'n':len(res),'bias':sum(res)/len(res),'MAE':sum(abs(v) for v in res)/len(res),'RMSE':math.sqrt(sum(v*v for v in res)/len(res)),'within_0_3':sum(abs(v)<=.3 for v in res),'positive_residuals':sum(v>0 for v in res)}
 for r in subset:r['measured_range_group']=label
with (O/'residuals-by-measured-range.csv').open('w',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
result={'utc':now,'n':21,'groups':groups,'regression':stats,'source_csv_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'empirical_correction_applied':False,'sources':[{'url':'https://doi.org/10.1787/9789264015845-en','reviewed_utc':now,'supports':'Mutually saturated phases; shake-flask aqueous microdroplet bias increases at high hydrophobicity; accurate slow-stir determinations documented up to 8.2, not an automatic failure threshold.'},{'url':'https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=30003VNC.TXT','reviewed_utc':now,'supports':'Measured slow-stir phthalate values and broad historical DEHP literature range; range alone does not identify method-specific measurement uncertainty.'}]}
alternative=[float(r['predicted_logKow'])-(8.83 if r['input_inchikey']=='PGIBJVOPLXHHGS-UHFFFAOYSA-N' else float(r['measured_logKow'])) for r in rows]
result['didecyl_source_sensitivity']={'source_url':'https://doi.org/10.1021/je990149u','primary_abstract_measured_value':8.83,'primary_abstract_uncertainty':0.05,'pubchem_value':9.05,'primary_comparison_changed':False,'alternative_MAE':sum(abs(v) for v in alternative)/21,'alternative_RMSE':math.sqrt(sum(v*v for v in alternative)/21),'alternative_bias':sum(alternative)/21,'full_text_retrieval':'ACS403; mirror no route to host; source disagreement not reconciled'}
(O/'review.json').write_text(json.dumps(result,indent=2)+'\n')
text='''## Hydrophobicity-dependent residuals and limits

No recalibration was applied. For the 21 matched molecules, predicted logKow = **1.103 × measured + 0.824**; residual = **0.103 × measured + 0.824**. The slope standard error is 0.099, so the small heterogeneous sample does not establish that the population slope exceeds one. This fitted trend is descriptive, not a correction or an attribution of cause.

The ten molecules with measured logKow >7 all have positive residuals, averaging **+1.898**. The six below 4 have bias **+0.912**, MAE **0.950**, and only **2/6** within ±0.3. Low-logKow agreement is better on average than the high group, but is not uniformly good: DEP is +0.803 and deoxycholic acid is +3.017. The latter retains its pH/speciation qualification; it is not removed from the primary n=21 statistics. The point estimates do not imply a monotonic increase for every molecule.

Two potential contributors remain unresolved. First, the model uses **dry, pure-component octanol and water references**, whereas standard experimental logKow uses **mutually saturated phases**. Second, high-logKow measurement is difficult: octanol microdroplets in sampled water can increase apparent aqueous concentration and lower apparent logKow in shake-flask measurements. Slow stirring addresses this artifact; high measured values should not be dismissed automatically. OECD reports successful slow-stir determinations up to logKow 8.2. We have not calculated the wet-phase correction or determined which contributor dominates. [OECD Test Guideline 123](https://doi.org/10.1787/9789264015845-en)

The EPA phthalate report documents a broad historical DEHP literature range (5.11–9.61), alongside its own measured slow-stir value 7.27. That heterogeneous range is not a universal ±1 uncertainty, nor proof of a particular slow-stir versus generator-column discrepancy for every row. The selected PubChem DEHP value remains 7.60 from its cited 1989 source, with the EPA value retained separately. Every parity row retains its source URL and raw citation; method, phase and speciation are not inferred where missing. [EPA phthalate measurements](https://nepis.epa.gov/Exe/ZyPURL.cgi?Dockey=30003VNC.TXT)

Reproduce with `python3 scripts/review_octanol_hydrophobicity.py`. Group membership, individual residuals and unchanged source citations are in `residuals-by-measured-range.csv` beside this review. The four named anchors remain in the sign-review parity tables.
'''
alt=result['didecyl_source_sensitivity']
text+=f"\nA source discrepancy remains for didecyl phthalate: PubChem lists 9.05, while the cited [primary abstract](https://doi.org/10.1021/je990149u) reports slow-stir 8.83 ±0.05. Full-text access failed, so the difference is unresolved. The primary n=21 comparison is preserved. Substituting only the abstract's measured value as an explicit sensitivity analysis gives MAE {alt['alternative_MAE']:.3f}, RMSE {alt['alternative_RMSE']:.3f}, bias +{alt['alternative_bias']:.3f}; predictions are unchanged. This is a source-selection sensitivity, not recalibration.\n"
(O/'REPORT.md').write_text(text)
manifest={'utc':now,'files':[{'path':p.name,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(O.iterdir()) if p.is_file() and p.name!='manifest.json']}
(O/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
base=(B/'octanol-sign-review/PROGRESS-REPORT.md').read_text();(ROOT/'reports/progress-2026-09-14/REPORT.md').write_text(base+'\n'+text)
(ROOT/'state/progress-2026-09-14/octanol-hydrophobicity-review.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
