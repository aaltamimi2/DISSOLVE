"""Bind measured calibration, conservative costs and controls to a launch gate.

No submission. The primary release remains independent and has priority. The
engineering sensitivity is not a statistical confidence interval.
"""
import collections
import datetime
import hashlib
import json
import statistics
from pathlib import Path

R = Path(__file__).resolve().parents[1]
D = Path('/mnt/r/plastchem-euler/phase10-v1')


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    obs_path = D / 'calibration-v2-observation.json'
    obs = json.loads(obs_path.read_text())
    assert obs['status'] == 'measurement_complete_review_required' and not obs['failures']
    assert obs['completed_chunks'] == 9
    cal = json.loads((D / 'calibration-plan-v2.json').read_text())
    plan = json.loads((D / 'production-plan.json').read_text())
    manifest = json.loads((D / 'manifest.json').read_text())
    assert plan['calibration_plan_sha256'] == sha(D / 'calibration-plan-v2.json')
    expected = {u['id'] for c in cal['chunks'] for u in c}
    assert len(expected) == 40 and set(plan['reused_calibration_units']) == expected
    remaining = [u['id'] for c in plan['chunks'] for u in c]
    assert len(remaining) == len(set(remaining)) == 5790
    assert not expected & set(remaining)
    assert expected | set(remaining) == {f'cohort-{i:05d}' for i in range(5830)}
    assert sum(c['LLE_seals'] for c in obs['chunks']) == 1560
    comparisons = [r for c in obs['chunks'] for r in c['control_comparisons']]
    assert len(comparisons) == 18 and sum(r['n'] for r in comparisons) == 80
    assert all(r['passed'] and r['max_abs_ln_gamma'] <= 1e-9 for r in comparisons)
    timings = obs['timing_evidence']['timings']
    assert obs['timing_evidence']['plan_sha256'] == sha(D / 'calibration-plan-v2.json')
    assert len(timings) == 1560
    seen = set()
    byunit = collections.defaultdict(float)
    strata = {}
    for r in timings:
        k = (r['unit'], r['solvent'])
        assert k not in seen and r['unit'] in expected and r['wall_seconds'] >= 0
        seen.add(k)
        byunit[r['unit']] += r['wall_seconds']
        strata[r['unit']] = r['stratum']
    assert seen == {(u, s['name']) for u in expected for s in manifest['solvents']}
    bystratum = collections.defaultdict(list)
    for unit, seconds in byunit.items(): bystratum[strata[unit]].append(seconds)
    population = manifest['population_strata']
    assert set(population) == set(bystratum)
    lle = sum(population[s] * statistics.mean(v) for s, v in bystratum.items())
    low_lle = sum(population[s] * min(v) for s, v in bystratum.items())
    high_lle = sum(population[s] * max(v) for s, v in bystratum.items())
    footers = [c['footer'] for c in obs['chunks']]
    assert all(f['peak_rss_kib'] < 3000*1024 for f in footers)
    # Actual planned parser ownership includes extra original-batch control
    # surfaces. Do not underestimate that overhead with 5830+39*chunks.
    parse_count = sum(len({m['surface_sha256'] for u in c for m in u['control_original_batch']})
                      + 41 for c in plan['chunks'])
    rates = [f['parse_seconds']/f['parse_count'] for f in footers]
    parsing = parse_count * statistics.mean(rates)
    remainder = sum((f['polymer_reuse_seconds']+f['partition_seconds']) *
        max(1, cal['subbatch']/len(f['unit_ids'])) for f in footers) * 5830/40
    # Include all elapsed allocation from both attempts, conservatively at
    # least the sum of successful-worker wall times plus 524 failed seconds.
    allocated = 0
    tasks = set()
    for line in obs['accounting'].splitlines():
        cols = line.split('|')
        if '.' not in cols[0] and cols[0].startswith(obs['job_id']+'_'):
            assert cols[1] == 'COMPLETED', 'Wait for terminal scheduler accounting'
            assert cols[0] not in tasks, 'Duplicate scheduler task'
            tasks.add(cols[0])
            allocated += int(cols[2])
    assert tasks == {obs['job_id']+'_'+str(i) for i in range(9)}
    assert allocated > 0
    calibration = max(allocated, sum(f['wall_seconds'] for f in footers)) + 524
    startup = 60 * len(plan['chunks'])
    projected = (1.25*(lle+parsing+remainder+startup)+calibration)/3600
    sensitivity = [(1.25*(l+parse_count*r+remainder+startup)+calibration)/3600
                   for l, r in [(low_lle, min(rates)), (high_lle, max(rates))]]
    files = ['manifest.json', 'calibration-plan-v2.json', 'production-plan.json',
             'worker-pins-v2.json', 'phase10_worker_v2.py', 'phase9_worker_cpu.py',
             'phase9_profiles.py', 'phase9_grid.py', 'phase9_failure_policy.py']
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='passed' if projected <= 510 else 'above_cost_gate_stop',
        authority='A-10: <=510 CPU-hours after measured calibration; original release has priority',
        charter_sha256=sha(R/'CHARTER.txt'), measured_observation_sha256=sha(obs_path),
        finalizer_sha256=sha(Path(__file__)), file_pins={p: sha(D/p) for p in files},
        calibration_molecules=40, LLE_systems=1560, exact_batch_control_comparisons=80,
        max_control_difference=max(r['max_abs_ln_gamma'] for r in comparisons),
        LLE_statuses=dict(collections.Counter(r['status'] for r in timings)),
        CPU_models=sorted({r['cpu_model'] for r in timings}),
        peak_RSS_MiB=max(f['peak_rss_kib'] for f in footers)/1024,
        projected_CPU_h=projected, limit_CPU_h=510, engineering_sensitivity_CPU_h=sensitivity,
        sensitivity_interpretation='Observed per-stratum minimum/maximum molecule costs and parse rates; not a confidence interval or bound on unmeasured chemistry.',
        costs_CPU_h=dict(LLE_full_cohort_stratum_weighted=lle/3600,
            parse_actual_production_profile_count=parsing/3600,
            reuse_partition_with_batch_size_correction=remainder/3600,
            one_minute_per_chunk_startup=startup/3600,
            all_calibration_allocated_including_failed_first_attempt=calibration/3600),
        planned_profile_parses=parse_count, allowance_fraction=.25,
        projection_note='Full 5830 LLE/partition cost plus measured calibration is conservative: 40 calibrated units are actually reused.',
        production_units=5790, reused_units=40, chunks=58)
    target = D / 'calibration-clearance.json'
    assert not target.exists(), 'Preserve existing gate; inspect instead of silently regenerating'
    target.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__': main()
