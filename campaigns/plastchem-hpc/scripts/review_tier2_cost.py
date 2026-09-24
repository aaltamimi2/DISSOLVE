"""A-4 cost review for the fixed first-30 cohort; never releases jobs."""
import json,datetime,argparse
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/tier2-v1'
def project(atoms,hours,all_atoms):
    x=np.log(np.asarray(atoms,float));y=np.log(np.asarray(hours,float))
    design=np.column_stack([np.ones(len(x)),x])
    assert len(x)>=3 and len(set(x))>=2 and np.all(np.isfinite(y))
    intercept,slope=np.linalg.lstsq(design,y,rcond=None)[0]
    residual=y-(intercept+slope*x)
    smear=float(np.mean(np.exp(residual)))
    prediction=np.exp(intercept+slope*np.log(np.asarray(all_atoms,float)))*smear
    return float(prediction.sum()),float(intercept),float(slope),residual.tolist()
def main():
    manifest=json.loads((P/'tier2/manifest.json').read_text());molecules=manifest['molecules']
    selection=P/'tier2/first30-indices.json'
    if not selection.exists():print('First cohort not released');return 2
    chosen=json.loads(selection.read_text());assert len(chosen)==len(set(chosen))==30
    rows=[]
    for i in chosen:
        m=molecules[i];p=P/'records'/f"{m['inchikey']}.json";r=json.loads(p.read_text()) if p.exists() else {}
        stages=r.get('stages',{});seconds=[stages.get(s,{}).get('wall_seconds') for s in ['opt','cosmo']]
        full=all(isinstance(t,(int,float)) and t>0 for t in seconds) and r.get('dft_status')=='converged'
        rows.append({'index':i,'inchikey':m['inchikey'],'atoms':m['atoms'],'status':r.get('status','not_yet_run'),'failure_mode':r.get('failure_mode'),'cpu_model':r.get('cpu_model'),'full_dft_wall_hours':sum(seconds)/3600 if full else None,'terminal_attempt_hours':r.get('elapsed_seconds',0)/3600,'timing_usable':full})
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    review={'utc':now,'denominator':270,'cohort_denominator':30,'terminal':sum(r['status'] in ['converged','failed'] for r in rows),'rows':rows,'prior_projection_cpu_hours':1770,'report_before_continuing_threshold_cpu_hours':3540,'release_authorised_by_this_script':False}
    if review['terminal']<30:
        review['status']='waiting_for_fixed_cohort';(P/'cost-review-pending.json').write_text(json.dumps(review,indent=2)+'\n');print(json.dumps({k:v for k,v in review.items() if k!='rows'}));return 2
    usable=[r for r in rows if r['timing_usable']]
    assert all(r['cpu_model']=='AMD EPYC 7763 64-Core Processor' for r in usable)
    review['usable_timing_count']=len(usable);review['incomplete_dft_count']=30-len(usable)
    if len(usable)<20:review['status']='insufficient_complete_timings_report_required'
    else:
        x=[r['atoms'] for r in usable];y=[r['full_dft_wall_hours'] for r in usable];all_x=[m['atoms'] for m in molecules]
        estimate,intercept,slope,residual=project(x,y,all_x)
        rng=np.random.default_rng(12345);boots=[]
        for _ in range(2000):
            ids=rng.integers(0,len(x),len(x))
            if len({x[i] for i in ids})<2:continue
            boots.append(project([x[i] for i in ids],[y[i] for i in ids],all_x)[0])
        review.update(projected_cpu_hours=estimate,projection_ratio=estimate/1770,bootstrap_95_percent_interval=np.quantile(boots,[.025,.975]).tolist(),fit={'form':'ln(hours) = intercept + slope * ln(atoms including H), with mean exp(residual) retransformation','intercept':intercept,'slope':slope,'residuals_ln_hours':residual,'bootstrap_seed':12345,'bootstrap_samples':len(boots)},exceeds_twice_projection=estimate>3540,uncertainty_limit='Bootstrap conditional on the fixed cohort and log-linear form; not a bound on long-tail stalls. Incomplete DFT attempts are separately reported, never treated as zero cost.')
        review['status']='report_before_continuing_required' if estimate>3540 or len(usable)<30 else 'cost_review_ready_for_operator_release'
    out=Path('/mnt/r/plastchem-euler/tier2-v1');out.mkdir(parents=True,exist_ok=True)
    (out/'first30-cost-review.json').write_text(json.dumps(review,indent=2)+'\n');(P/'first30-cost-review.json').write_text(json.dumps(review,indent=2)+'\n')
    print(json.dumps({k:v for k,v in review.items() if k!='rows'}));return 0
if __name__=='__main__':raise SystemExit(main())
