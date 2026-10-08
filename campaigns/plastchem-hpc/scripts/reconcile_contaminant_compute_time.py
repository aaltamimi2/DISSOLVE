"""Reconcile serial contaminant allocation and stage time, without scheduler writes."""
import argparse
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BULK = Path('/mnt/r/plastchem-euler')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    out = args.output.resolve()
    assert out.is_relative_to(BULK / 'validation-reviews') and not out.exists()
    pins, cpu_evidence = {}, {}

    def read(p):
        raw = p.read_bytes()
        pins[str(p)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    snapshot_times = {}
    for tier in ['pilot-v1', 'campaign-v1', 'tier2-v1']:
        path = ROOT / 'state' / tier / 'latest-snapshot.json'
        snap = read(path)
        snapshot_times[tier] = snap['utc']
        for line in snap['sacct'].splitlines():
            fields = line.split('|')
            if len(fields) >= 11 and '.' not in fields[0] and fields[6]:
                cpu_evidence[fields[0]] = int(fields[6])

    rows, no_allocation = [], []
    seen_tasks = set()
    counts = collections.Counter()
    for tier in ['campaign-v1', 'tier2-v1']:
        paths = sorted((ROOT / 'state' / tier / 'records').glob('*.json'))
        for path in paths:
            r = read(path)
            counts[f'{tier}:{r["status"]}'] += 1
            attempts = [('current', r, path)]
            if r.get('previous_attempt'):
                prior = r['previous_attempt']
                prior_path = Path(prior['original_record'])
                old = read(prior_path)
                assert pins[str(prior_path)] == prior['original_record_sha256']
                assert not old.get('previous_attempt'), 'Nested retry needs explicit accounting'
                attempts.append(('preserved_original', old, prior_path))
            for attempt, record, source in attempts:
                acct = record.get('slurm_accounting', {})
                elapsed = acct.get('elapsed_seconds')
                if elapsed is None:
                    no_allocation.append(dict(tier=tier, key=path.stem, attempt=attempt,
                                              status=record['status'], dft_ran=record.get('dft_ran'),
                                              record_path=str(source), record_sha256=pins[str(source)]))
                    continue
                task = f"{record['array_job_id']}_{record['array_task_id']}"
                assert task not in seen_tasks, f'Double counting {task}'
                seen_tasks.add(task)
                requested = int(record['resources']['cpus'])
                cpus = cpu_evidence.get(task, requested)
                assert cpus == requested == 1, (task, cpus, requested)
                stages = record.get('stages', {})
                opt = stages.get('opt', {}).get('wall_seconds')
                cosmo = stages.get('cosmo', {}).get('wall_seconds')
                state = acct['state'].split()[0]
                bucket = 'live' if state in ['RUNNING', 'COMPLETING', 'SUSPENDED'] else 'terminal'
                assert state not in ['PENDING', 'REQUEUED'], state
                rows.append(dict(tier=tier, input_inchikey=path.stem, attempt=attempt,
                                 task=task, state=state, time_bucket=bucket, elapsed_seconds=elapsed,
                                 allocated_cpus=cpus, cpu_evidence='sacct_AllocCPUS' if task in cpu_evidence else 'recorded_serial_resource_request',
                                 allocated_cpu_hours=elapsed * cpus / 3600,
                                 available_opt_stage_seconds=opt, available_cosmo_stage_seconds=cosmo,
                                 completed_pair_stage_hours=(opt + cosmo) / 3600 if opt is not None and cosmo is not None else None,
                                 record_path=str(source), record_sha256=pins[str(source)]))
    sums = collections.defaultdict(float)
    for row in rows:
        sums[f"{row['tier']}:{row['attempt']}:{row['time_bucket']}:allocated_cpu_hours"] += row['allocated_cpu_hours']
        for stage in ['opt', 'cosmo']:
            seconds = row[f'available_{stage}_stage_seconds']
            if seconds is not None:
                sums[f"{row['tier']}:{row['attempt']}:available_{stage}_stage_hours"] += seconds / 3600
        if row['completed_pair_stage_hours'] is not None:
            sums[f"{row['tier']}:{row['attempt']}:completed_pair_stage_hours"] += row['completed_pair_stage_hours']
    # The measured-pair headline intentionally excludes partial stages.
    tier_summary = read(ROOT / 'state/tier2-v1/summary.json')
    paired = sums['tier2-v1:current:completed_pair_stage_hours']
    difference = paired - tier_summary['measured_orca_hours']
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   task_attempts=len(rows), unique_tasks=len(seen_tasks), record_counts=dict(counts),
                   sums=dict(sums), terminal_allocated_cpu_hours=sum(r['allocated_cpu_hours'] for r in rows if r['time_bucket']=='terminal'),
                   live_allocated_cpu_hours_at_capture=sum(r['allocated_cpu_hours'] for r in rows if r['time_bucket']=='live'),
                   no_allocation_records=len(no_allocation),
                   no_allocation_records_all_dft_not_run=all(r['dft_ran'] is False for r in no_allocation),
                   tier2_pair_headline_difference_cpu_hours=difference,
                   tier2_summary_utc=tier_summary['utc'], scheduler_snapshot_times=snapshot_times,
                   cpu_evidence_counts=dict(collections.Counter(r['cpu_evidence'] for r in rows)),
                   source_sha256=pins,
                   scope='Contaminant main tier including reused pilot once, tier2, and preserved previous attempts. Excludes polymer jobs, solvent references, activity/LLE calculations, diagnostics, workstation work and local preparation. Allocation is reserved CPU time, not measured process CPU utilization. Running values are per-file captured elapsed time, not an atomic current total. Stage values include only available recorded durations; paired-stage headline omits partial stages.')
    out.mkdir(parents=True)
    for name, data in [('task-attempts.csv', rows), ('records-without-allocation.csv', no_allocation)]:
        with (out / name).open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]))
            w.writeheader()
            w.writerows(data)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    manifest = dict(status='allocation_and_stage_accounting_captured', files={
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()})
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for name, pin in manifest['files'].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == pin
    print(json.dumps({k:v for k,v in summary.items() if k not in ['source_sha256', 'scope']}, indent=2))
    print('manifest_sha256', hashlib.sha256((out / 'manifest.json').read_bytes()).hexdigest())


if __name__ == '__main__':
    main()
