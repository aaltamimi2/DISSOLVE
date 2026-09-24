"""Same-solvent experimental comparisons, with missing predictions and conditions explicit."""
import os
os.environ['OMP_NUM_THREADS']='1'
os.environ['OPENBLAS_NUM_THREADS']='1'
import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parents[1]
BULK=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');args=parser.parse_args()
    source=BULK/'freeze' if args.frozen else ROOT
    if args.frozen:assert (source/'manifest.json').exists()
    records={f.stem:json.loads(f.read_text()) for f in (source/'state/campaign-v1/records').glob('*.json')}
    cohort={k for k,r in records.items() if r.get('status')=='converged'}
    numeric_source=BULK/'sealed-thermodynamics' if args.frozen else ROOT/'state/thermodynamics-v1'
    ledger=json.loads((numeric_source/'processing-ledger.json').read_text())
    out=BULK/('experimental-validation' if args.frozen else 'preview-experimental-validation');out.mkdir(exist_ok=True)
    candidates=list(csv.DictReader(((numeric_source if args.frozen else BULK)/'experimental-reference-candidates.csv').open()))
    cache={};comparisons=[]
    for row in candidates:
        if row['inchikey'] not in cohort:continue
        r=dict(row);r.update(predicted_log10_partition='',residual_predicted_minus_observed='',comparison_status='prediction_missing',result_sha256='')
        key=row['inchikey']
        if row['ambiguous_campaign_CAS']=='True':
            r['comparison_status']='ambiguous_identity';comparisons.append(r);continue
        if row.get('observed_operator', '=') != '=':
            r['comparison_status']='censored_observation_excluded_from_point_error_statistics';comparisons.append(r);continue
        if key not in cache and key in ledger:
            content=Path(ledger[key]['result_path']).read_bytes()
            if hashlib.sha256(content).hexdigest()==ledger[key]['result_sha256']:
                cache[key]=json.loads(content)
        result=cache.get(key)
        if result:
            assert result['solute_surface_sha256']==records[key]['surface_sha256']
            pair=next((p for p in result['partitions_against_water'] if p['solvent']==row['solvent']),None)
            if pair and pair['status']=='predicted' and pair.get('log10_K_concentration') is not None:
                value=pair['log10_K_concentration'];observed=float(row['observed_log10_partition'])
                assert math.isfinite(value) and math.isfinite(observed)
                r.update(predicted_log10_partition=value,residual_predicted_minus_observed=value-observed,
                         comparison_status='same_pair_condition_qualified',result_sha256=ledger[key]['result_sha256'])
        comparisons.append(r)
    assert comparisons,'No reference overlap; report missing rather than fabricate a parity plot'
    with (out/'reference-match-dispositions.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(comparisons[0]));w.writeheader();w.writerows(comparisons)
    matched=[r for r in comparisons if r['comparison_status']=='same_pair_condition_qualified']
    residuals=[r['residual_predicted_minus_observed'] for r in matched]
    stats={'utc':dt.datetime.now(dt.timezone.utc).isoformat(),'frozen':args.frozen,
           'cohort_molecules':len(cohort),'reference_observations':len(comparisons),
           'reference_molecules':len({r['inchikey'] for r in comparisons}),
           'censored_reference_observations':sum(r.get('observed_operator','=')!='=' for r in comparisons),
           'point_reference_molecules':len({r['inchikey'] for r in comparisons if r.get('observed_operator','=')=='='}),
           'matched_observations':len(matched),'matched_molecules':len({r['inchikey'] for r in matched}),
           'MAE':sum(map(abs,residuals))/len(residuals) if residuals else None,
           'RMSE':math.sqrt(sum(x*x for x in residuals)/len(residuals)) if residuals else None,
           'bias_predicted_minus_observed':sum(residuals)/len(residuals) if residuals else None,
           'outliers_abs_residual_gt_1':[r for r in matched if abs(r['residual_predicted_minus_observed'])>1],
           'conditions':'Concentration-basis prediction at 298.15 K with pure-solvent surfaces. Source temperature and dry/wet labels retained per row. Unspecified experimental temperature and water saturation prevent claiming exact-condition validation. No adjustment or interpolation applied.',
           'interpretation':'Descriptive agreement with experimental values for identical named solvent/water pairs; not experimental proof across the campaign. Multiple observations of one molecule are not independent molecules.'}
    (out/'statistics.json').write_text(json.dumps(stats,indent=2)+'\n')
    if matched:
        plt.rcParams.update({'font.size':14,'axes.titlesize':14,'axes.labelsize':14,
                             'xtick.labelsize':14,'ytick.labelsize':14,'legend.fontsize':14,
                             'text.color':'black','axes.labelcolor':'black','xtick.color':'black','ytick.color':'black'})
        x=[float(r['observed_log10_partition']) for r in matched];y=[r['predicted_log10_partition'] for r in matched]
        low=min(x+y)-.5;high=max(x+y)+.5
        fig,ax=plt.subplots(figsize=(9,9),layout='constrained')
        ax.plot([low,high],[low,high],color='black',label='1:1')
        ax.scatter(x,y,color='#3274a1',s=65)
        ax.set(xlim=(low,high),ylim=(low,high),xlabel='Experimental log₁₀ K (solvent/water)',
               ylabel='Predicted log₁₀ K (solvent/water)',title='Experimental comparison; condition-qualified')
        ax.set_aspect('equal');ax.legend()
        fig.savefig(out/'predicted-vs-experimental.png',dpi=300);plt.close(fig)
        caption=(f"n = {stats['matched_molecules']} molecules / {len(matched)} observations; "
                 f"MAE = {stats['MAE']:.3f}, RMSE = {stats['RMSE']:.3f}, "
                 f"bias (predicted − observed) = {stats['bias_predicted_minus_observed']:+.3f} log units. "
                 "Black line: 1:1. Predictions use concentration basis at 298.15 K. "
                 "Experimental temperatures may be unspecified and phase saturation may differ; "
                 "see the row-level source and conditions. Small n does not establish broad predictive accuracy.\n")
        (out/'parity-caption.txt').write_text(caption)
    print(json.dumps({k:v for k,v in stats.items() if k!='outliers_abs_residual_gt_1'}))

if __name__=='__main__':main()
