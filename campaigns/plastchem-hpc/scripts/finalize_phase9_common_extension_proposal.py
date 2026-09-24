"""Add measured job overhead to the planning-only common-solvent proposal."""
import csv
import datetime
import json
import statistics
from pathlib import Path

from audit_phase9_results import sha

D = Path('/mnt/r/plastchem-euler/phase9-v1')
BASE = D / 'common-extension-planning-v1'
OUT = D / 'common-extension-proposal-v2'
SNAPSHOT = D / 'common-extension-proposal-v1/scheduler-footer-snapshot.json'


def main():
    OUT.mkdir(exist_ok=False)
    base = json.loads((BASE / 'summary.json').read_text())
    for name, digest in base['files'].items():
        assert sha(BASE / name) == digest, name
    with (BASE / 'existing-solvent-timing-reference.csv').open() as stream:
        phases = list(csv.DictReader(stream))
    assert len(phases) == 32
    raw = SNAPSHOT.read_bytes()
    (OUT / 'scheduler-footer-snapshot.json').write_bytes(raw)
    snapshot = json.loads(raw)
    accounting = {r[0]: r for r in (s.split('|') for s in snapshot['accounting'].splitlines())
                  if len(r) >= 4 and '.' not in r[0]}
    array = str(json.loads((D / 'production-submission.json').read_text())['job_id'])
    overhead = []
    for path, value in snapshot['complete'].items():
        if not path.startswith('production-results-v1/'): continue
        index = int(path.split('/')[1])
        if index in [2, 31]: continue  # Explicit tail pause/helpers and checkpoint retry.
        job = f'{array}_{index}'
        if job not in accounting or accounting[job][1] != 'COMPLETED': continue
        row = accounting[job]
        assert int(row[3]) == 1
        units = len(value['unit_ids'])
        assert value['parse_count'] == units + 268
        extra = value['wall_seconds'] - sum(value[k] for k in
                    ['parse_seconds', 'partition_seconds', 'lle_seconds'])
        assert extra >= -1e-6
        overhead.append(dict(job=job, units=units,
          parse_seconds_per_profile=value['parse_seconds']/value['parse_count'],
          checkpoint_seconds_per_partition_row=max(0., extra)/(units*640),
          startup_and_teardown_seconds=max(0., int(row[2])-value['wall_seconds']),
          cpu_model=value['execution']['cpu_model']))
    assert len(overhead) >= 40
    with (OUT / 'measured-job-overhead.csv').open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(overhead[0]))
        w.writeheader(); w.writerows(overhead)
    def spread(values): return dict(low=min(values), mean=statistics.mean(values), high=max(values))
    observed = {k: spread([r[k] for r in overhead]) for k in
                ['parse_seconds_per_profile', 'checkpoint_seconds_per_partition_row',
                 'startup_and_teardown_seconds']}
    phase = spread([float(r['measured_full_cohort_phase_CPU_h']) for r in phases])
    rt = spread([float(r['RT_stratum_weighted_full_cohort_CPU_h']) for r in phases])
    options = []
    for n in [39, 69]:
        output_rows = 5830*n*10*2
        profile_reads = 5830+59*n
        parts = {}
        for endpoint in ['low', 'mean', 'high']:
            parts[endpoint] = dict(
              new_solvent_phase_CPU_h=n*phase[endpoint],
              profile_parsing_CPU_h=profile_reads*observed['parse_seconds_per_profile'][endpoint]/3600,
              checkpoint_recombination_CPU_h=output_rows*observed['checkpoint_seconds_per_partition_row'][endpoint]/3600,
              startup_teardown_CPU_h=59*observed['startup_and_teardown_seconds'][endpoint]/3600,
              RT_LLE_CPU_h=n*rt[endpoint])
        for with_lle in [False, True]:
            totals={k:sum(v for part,v in components.items() if with_lle or part!='RT_LLE_CPU_h')
                    for k,components in parts.items()}
            options.append(dict(new_solvent_surface_conditions=n,
              quantities='partition_and_RT_LLE' if with_lle else 'partition_only',
              new_partition_rows=output_rows, new_RT_LLE_rows=5830*n if with_lle else 0,
              planned_100_solute_chunks=59, required_profile_parses=profile_reads,
              baseline_components_CPU_h={k:v for k,v in parts['mean'].items()
                                          if with_lle or k!='RT_LLE_CPU_h'},
              baseline_CPU_h=totals['mean'],
              planning_CPU_h_with_25_percent_allowance=1.25*totals['mean'],
              engineering_range_CPU_h=[1.25*totals['low'],1.25*totals['high']]))
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
      status='proposal_only_extension_not_authorized', measurement_summary_sha256=sha(BASE/'summary.json'),
      measurement_directory=str(BASE), scheduler_snapshot_utc=snapshot['utc'],
      scheduler_snapshot_sha256=sha(OUT/'scheduler-footer-snapshot.json'),
      overhead_completed_chunks=len(overhead), overhead_cpu_models=sorted({r['cpu_model'] for r in overhead}),
      overhead_excluded_chunks=[2,31], overhead_observations=observed,
      timing_molecules=base['timing_molecules'],timing_LLE_systems=base['timing_LLE_systems'],
      timing_LLE_statuses=base['timing_LLE_statuses'],options=options,
      range_definition='Componentwise observed min/max overhead plus fastest/slowest observed panel-solvent weighted costs, all with 25% allowance. Planning envelope, not a statistical confidence interval or bound for untested chemistry.',
      limitations=base['limitations']+[
        'End-to-end parsing/startup/partition-checkpoint overhead is scaled from ordinary completed chunks; tail and retry chunks are excluded only from this timing model, not campaign accounting.',
        'The 25% allowance is additional to measured overhead; package assembly/transfer are not calibrated by this projection.',
        'Stored polymer activities are reused, not recomputed; a changed temperature or polymer/surface/solute/package signature invalidates that reuse.'],
      script_sha256=sha(Path(__file__)), overhead_csv_sha256=sha(OUT/'measured-job-overhead.csv'))
    (OUT/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
