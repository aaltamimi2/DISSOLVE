"""Compare later completed DFT timings with the unchanged first-cohort fit.

Read-only campaign analysis. No refit, scheduler edits, or extrapolation from
the completion-selected validation subset.
"""
import argparse
import csv
import datetime
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler/tier2-v1')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    assert out.is_relative_to(BULK) and not out.exists()
    pins = {}

    def read(path):
        raw = path.read_bytes()
        pins[str(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    state = ROOT / 'state/tier2-v1'
    review = read(state / 'first30-cost-review.json')
    training = {r['inchikey'] for r in review['rows']}
    fit = review['fit']
    smear = sum(math.exp(x) for x in fit['residuals_ln_hours']) / len(fit['residuals_ln_hours'])
    rows = []
    disposition = {}
    for path in sorted((state / 'records').glob('*.json')):
        rec = read(path)
        if rec['inchikey'] in training:
            continue
        status = rec.get('status', 'unknown')
        disposition[status] = disposition.get(status, 0) + 1
        stages = rec.get('stages', {})
        times = [stages.get(s, {}).get('wall_seconds') for s in ['opt', 'cosmo']]
        if rec.get('dft_status') != 'converged' or not all(isinstance(t, (int, float)) and t > 0 for t in times):
            continue
        assert rec['cpu_model'] == 'AMD EPYC 7763 64-Core Processor'
        actual = sum(times) / 3600
        atoms = rec['input']['atoms']
        pred = math.exp(fit['intercept'] + fit['slope'] * math.log(atoms)) * smear
        rows.append(dict(inchikey=rec['inchikey'], name=rec['input']['name'], atoms=atoms,
                         identity_disposition=status, actual_DFT_hours=actual,
                         predicted_DFT_hours=pred, residual_hours=actual-pred,
                         actual_over_predicted=actual/pred, cpu_model=rec['cpu_model']))
    assert rows, 'No later complete timings'
    n = len(rows)
    stats = dict(n=n, MAE_hours=sum(abs(r['residual_hours']) for r in rows)/n,
                 RMSE_hours=math.sqrt(sum(r['residual_hours']**2 for r in rows)/n),
                 bias_actual_minus_predicted_hours=sum(r['residual_hours'] for r in rows)/n,
                 observed_total_hours=sum(r['actual_DFT_hours'] for r in rows),
                 predicted_total_hours=sum(r['predicted_DFT_hours'] for r in rows),
                 largest_actual_over_predicted=max(rows, key=lambda r:r['actual_over_predicted']),
                 DFT_complete_identity_rejected=sum(r['identity_disposition']!='converged' for r in rows))
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   fit_utc=review['utc'], fit_usable_count=review['usable_timing_count'],
                   unchanged_projection_CPU_hours=review['projected_cpu_hours'],
                   unchanged_conditional_bootstrap_interval=review['bootstrap_95_percent_interval'],
                   later_record_dispositions=disposition, statistics=stats,
                   limitations='Later completed runs only: selection by completion can favour cheaper molecules. '
                   'Running and not-started targets are not zero cost. This is not an unbiased campaign cost '
                   'estimate, a refit, a new confidence interval, or evidence that atom count bounds every runtime. '
                   'Fully executed identity-policy rejections remain usable timing observations and are labelled.',
                   source_sha256=pins)
    out.mkdir(parents=True)
    with (out/'completed-timing-comparison.csv').open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    (out/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    files = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir()}
    files[str(Path(__file__).resolve())] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    (out/'manifest.json').write_text(json.dumps(dict(utc=summary['utc'], files=files), indent=2)+'\n')
    print(json.dumps({k:v for k,v in summary.items() if k!='source_sha256'}, indent=2))


if __name__ == '__main__':
    main()
