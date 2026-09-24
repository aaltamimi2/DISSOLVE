"""Read-only calibration progress and sealed timings; no scientific solve or submit."""
import collections
import datetime
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

D = Path.home() / 'plastchem-euler/phase10-v1'


def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def call(args):
    p = subprocess.run(args, capture_output=True, text=True)
    assert p.returncode == 0, (args, p.stderr)
    return p.stdout


def sealed(p, plan_digest):
    stamp = json.loads(p.with_suffix('.json.sha256.json').read_text())
    assert stamp['signature']['plan_sha256'] == plan_digest
    assert stamp['sha256'] == sha(p)
    return json.loads(p.read_text())


def main():
    version = sys.argv[1] if len(sys.argv) > 1 else 'v2'
    assert version in ['v1', 'v2']
    plan_path = D / ('calibration-plan.json' if version == 'v1' else 'calibration-plan-v2.json')
    plan_digest = sha(plan_path)
    plan = json.loads(plan_path.read_text())
    manifest = json.loads((D / 'manifest.json').read_text())
    prefix = 'calibration' if version == 'v1' else 'calibration-v2'
    receipt = json.loads((D / (prefix + '-submission.json')).read_text())
    job = receipt['job_id']
    queue = call(['squeue', '-h', '-r', '-u', 'aaltamimi2', '-p', 'research',
                  '-o', '%i|%T|%C|%j|%R'])
    accounting = call(['sacct', '-nP', '-j', job,
        '--format=JobID%40,State%30,ElapsedRaw,TotalCPU,MaxRSS,NodeList,ExitCode'])
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        job_id=job, queue=queue, accounting=accounting, chunks=[], failures=[], status='in_progress')
    for row in accounting.splitlines():
        cols = row.split('|')
        if len(cols) > 1 and '.' not in cols[0] and cols[1] in ['FAILED', 'TIMEOUT', 'OUT_OF_MEMORY', 'CANCELLED', 'NODE_FAIL']:
            error = D / 'logs' / (prefix + '-' + cols[0] + '.err')
            result['failures'].append(dict(task=cols[0], state=cols[1],
                error_tail=error.read_text()[-5000:] if error.exists() else None))
    complete = []
    for i, units in enumerate(plan['chunks']):
        p = D / plan['output'] / f'{i:04d}'
        starts = list(p.glob('started-*.json'))
        comparisons = [json.loads(q.read_text()) for q in p.glob('CONTROL__*-comparison.json')]
        row = dict(chunk=i, units=len(units),
            started=[json.loads(q.read_text()) for q in starts],
            activity_seals=len(list((p / 'activities').glob('*.sha256.json'))),
            LLE_seals=len(list((p / 'lle').glob('*.sha256.json'))),
            control_comparisons=comparisons, complete=(p / 'complete.json').exists())
        if row['complete']:
            footer = json.loads((p / 'complete.json').read_text())
            assert footer['signature']['plan_sha256'] == plan_digest
            assert footer['unit_ids'] == [u['id'] for u in units]
            assert len(footer['lle_statuses']) == len(units) * 39
            assert row['activity_seals'] == 41 and row['LLE_seals'] == len(units) * 39
            assert len(comparisons) == 2 and all(r['passed'] and r['max_abs_ln_gamma'] <= 1e-9 for r in comparisons)
            row['footer'] = footer
            complete.append((p, units, footer))
        result['chunks'].append(row)
    result['completed_chunks'] = len(complete)
    if result['failures']:
        result['status'] = 'inspection_required'
    elif len(complete) == len(plan['chunks']):
        measured = D / (prefix + '-timing-evidence.json')
        if measured.exists():
            evidence = json.loads(measured.read_text())
            assert evidence['plan_sha256'] == plan_digest
        else:
            timings = []
            for p, units, footer in complete:
                for u in units:
                    for s in manifest['solvents']:
                        value = sealed(p / 'lle' / (u['id'] + '__' + s['name'] + '__RT.json'), plan_digest)
                        assert value['unit'] == u['id'] and value['solvent'] == s['name']
                        assert value['temperature_K'] == 298.15
                        timings.append(dict(unit=u['id'], stratum=u['stratum'], solvent=s['name'],
                            wall_seconds=value['wall_seconds'], status=value['status'],
                            cpu_model=value['execution']['cpu_model']))
            evidence = dict(plan_sha256=plan_digest,
                measured_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), timings=timings)
            temp = measured.with_suffix('.tmp')
            temp.write_text(json.dumps(evidence, separators=(',', ':')) + '\n')
            temp.replace(measured)
        times = collections.defaultdict(list)
        strata = collections.defaultdict(set)
        for r in evidence['timings']:
            times[r['stratum']].append(r['wall_seconds'])
            strata[r['stratum']].add(r['unit'])
        assert sum(map(len, times.values())) == 1560
        assert set(times) == set(manifest['population_strata'])
        lle = sum(manifest['population_strata'][s] * sum(v) / len(strata[s]) for s, v in times.items())
        footers = [x[2] for x in complete]
        n = sum(len(x[1]) for x in complete)
        chunks = (5830 + plan['production_chunk_size'] - 1) // plan['production_chunk_size']
        parse_rates = [f['parse_seconds']/f['parse_count'] for f in footers]
        parsing = statistics.mean(parse_rates) * (5830 + 41*chunks)
        # Future activity sub-batches have ten solutes, versus 3--7 here.
        # An n-squared engine cannot be projected by naive per-solute scaling.
        # Apply the conservative batch-size ratio to the whole partition/reuse
        # component (including its actually linear overhead) before scaling.
        remainder = sum((f['polymer_reuse_seconds'] + f['partition_seconds']) *
                        max(1, plan['subbatch']/len(f['unit_ids'])) for f in footers) * 5830/n
        # Include measured calibration, a 25% engineering allowance, and an
        # explicit one-minute-per-production-job import/scheduler allowance.
        calibration = sum(f['wall_seconds'] for f in footers) + (524 if version == 'v2' else 0)
        projected = (1.25*(lle+parsing+remainder+60*chunks)+calibration)/3600
        result.update(status='measurement_complete_review_required',
            timing_evidence=evidence, timing_evidence_sha256=sha(measured),
            projection=dict(stratum_weighted_LLE_CPU_h=lle/3600,
                scaled_parse_CPU_h=parsing/3600, scaled_reuse_partition_CPU_h=remainder/3600,
                calibration_wall_CPU_h=calibration/3600, startup_allowance_CPU_h=chunks/60,
                activity_batch_size_correction='Entire measured partition/reuse component multiplied by max(1,10/sample_chunk_size) before per-solute scaling; deliberately conservative for its linear parts',
                planning_CPU_h=projected, limit_CPU_h=510, inside_cost_gate=projected <= 510,
                interpretation='Deliberate difficult-case sample; engineering projection, not a confidence interval. Includes calibration plus full-cohort work conservatively before reuse.'))
    print(json.dumps(result))


if __name__ == '__main__': main()
