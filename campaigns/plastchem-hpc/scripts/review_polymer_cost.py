"""A-5 first-30 timing review; no scheduler mutations or automatic release."""
import json,datetime,math
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[1];P=R/'state/polymer-v1'
models=sum([json.loads((P/g/'manifest.json').read_text())['molecules'] for g in ['body','large']],[])
records=[]
for f in (P/'records').glob('*.json'):
 r=json.loads(f.read_text())
 if r.get('status') in ['converged','failed']:records.append(r)
records.sort(key=lambda r:(r.get('finished_utc','9999'),r.get('entry_id','')))
review={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'total_terminal':len(records),'denominator':284,'prior_projection_cpu_hours':1544,'report_threshold_cpu_hours':3088,'scheduler_action':None}
if len(records)<30:
 review['status']='waiting_first_30_terminal';(P/'cost-review-pending.json').write_text(json.dumps(review,indent=2)+'\n');print(json.dumps(review));raise SystemExit(2)
cohort=records[:30];timings=[]
for r in cohort:
 walls=[r.get('stages',{}).get(s,{}).get('wall_seconds') for s in ['opt','cosmo']]
 if r.get('dft_status')=='converged' and all(isinstance(t,(float,int)) and t>0 for t in walls):
  assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor';timings.append((r['input']['atoms'],sum(walls)))
review.update(cohort_entries=[r['entry_id'] for r in cohort],usable_timings=len(timings),incomplete_timings=30-len(timings))
if len(timings)<20:review['status']='insufficient_timings_report_required'
else:
 fit=json.loads((R/'reports/pilot-v1/analysis.json').read_text())['fit']
 def prior(n):return math.exp(fit['intercept'])*n**fit['exponent']*fit['residual_smearing']
 ratios=np.array([t/prior(n) for n,t in timings]);scale=float(ratios.mean());rng=np.random.default_rng(12345);boot=np.array([rng.choice(ratios,len(ratios),replace=True).mean()*1544 for _ in range(2000)])
 review.update(projected_cpu_hours=1544*scale,bootstrap_95_percent_interval=np.quantile(boot,[.025,.975]).tolist(),scale_vs_pilot=scale,observed_atom_counts=sorted({n for n,t in timings}),fit_method='Multiplicative recalibration of timing projection only; pilot atom-count exponent held fixed',qualification='The PE-first cohort is at one atom count. It cannot identify a new size exponent or validate chemistry/size extrapolation to other polymers. Interval is conditional sampling uncertainty only; incomplete attempts remain explicit, not zero cost.',retained_pilot_exponent=fit['exponent'],exceeds_twice_projection=1544*scale>3088)
 if len({n for n,t in timings})>=2:
  from review_tier2_cost import project
  atoms=[n for n,t in timings];hours=[t/3600 for n,t in timings];all_atoms=[m['atoms'] for m in models]
  estimate,intercept,slope,residual=project(atoms,hours,all_atoms);boots=[]
  for _ in range(2000):
   ids=rng.integers(0,len(atoms),len(atoms))
   if len({atoms[i] for i in ids})>=2:boots.append(project([atoms[i] for i in ids],[hours[i] for i in ids],all_atoms)[0])
  review.update(projected_cpu_hours=estimate,bootstrap_95_percent_interval=np.quantile(boots,[.025,.975]).tolist(),fit_method='Refitted log(hours) on log(atom count), residual-smearing retransformation',fit_intercept=intercept,fit_exponent=slope,exceeds_twice_projection=estimate>3088,qualification='Conditional first-completion cohort fit: rapid molecules are overrepresented; uncompleted and larger polymers remain extrapolations. Bootstrap is not a guarantee against long stalls or censored attempts.')
 review['status']='report_before_continuing_required' if review['exceeds_twice_projection'] or len(timings)<30 else 'ready_for_operator_review'
D=Path('/mnt/r/plastchem-euler/polymer-v1');D.mkdir(parents=True,exist_ok=True);(D/'first30-cost-review.json').write_text(json.dumps(review,indent=2)+'\n');(P/'first30-cost-review.json').write_text(json.dumps(review,indent=2)+'\n');print(json.dumps(review))
