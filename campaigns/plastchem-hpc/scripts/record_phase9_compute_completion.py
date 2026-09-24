"""Record terminal scheduler/footer evidence, without claiming payload acceptance.

Uses an immutable local copy of the collector's scheduler snapshot. No cluster
command, scientific calculation, release mutation or product write is performed.
"""
import collections
import csv
import datetime
import gzip
import hashlib
import json
from pathlib import Path

D = Path('/mnt/r/plastchem-euler/phase9-v1')
OUT = D / 'compute-completion-v1'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    OUT.mkdir(exist_ok=False)
    raw = (D / 'latest-gate-status.json').read_bytes()
    snapshot = json.loads(raw)
    with gzip.open(OUT / 'scheduler-footer-snapshot.json.gz', 'wb') as stream:
        stream.write(raw)
    manifest_raw = (D.parent / 'phase8-v1/manifest.json').read_bytes()
    solvents = [s['name'] for s in json.loads(manifest_raw)['solvents']]
    all_units, rows, totals, pins = set(), [], collections.Counter(), {}
    for name, root in [('chunk-probe-plan.json', 'chunk-probe-results-v1'),
                       ('production-plan.json', 'production-results-v1')]:
        plan_raw = (D / name).read_bytes()
        (OUT / name).write_bytes(plan_raw)
        pins[name] = digest(plan_raw)
        for index, chunk in enumerate(json.loads(plan_raw)['chunks']):
            key = f'{root}/{index:04d}'
            footer = snapshot['complete'][key]
            units = {u['id'] for u in chunk}
            assert len(units) == len(chunk)
            assert set(footer['unit_ids']) == units
            assert len(footer['unit_ids']) == len(units)
            assert not all_units & units
            all_units.update(units)
            expected = {f'{u}__{s}__{regime}' for u in units
                        for s in solvents for regime in ['RT', 'high']}
            assert set(footer['lle_statuses']) == expected, key
            statuses = collections.Counter(footer['lle_statuses'].values())
            totals.update(statuses)
            rows.append(dict(chunk=key, molecules=len(units), LLE_systems=len(expected),
                             cpu_model=footer['execution']['cpu_model'],
                             worker_wall_seconds=footer['wall_seconds'],
                             peak_rss_kib=footer['peak_rss_kib'],
                             status_counts=json.dumps(dict(statuses), sort_keys=True)))
    assert all_units == {f'cohort-{i:05d}' for i in range(5830)}
    assert sum(totals.values()) == 373120 and len(rows) == 59
    receipts = [D / 'production-submission.json', D / 'chunk-probe-submission.json',
                *D.glob('production-retry-*-submission.json')]
    job_ids = {str(json.loads(p.read_text())['job_id']) for p in receipts}
    # Collector queue format is JobID|JobName|State, unlike recovery watcher.
    assert not any(line.split('|')[0].split('_')[0] in job_ids
                   for line in snapshot['queue'].splitlines() if line)
    accounting = []
    for line in snapshot['accounting'].splitlines():
        fields = line.split('|')
        if len(fields) < 4 or '.' in fields[0]: continue
        if fields[0].split('_')[0] not in job_ids: continue
        assert fields[1] in ['COMPLETED', 'FAILED'], fields
        accounting.append(dict(job_id=fields[0], state=fields[1],
                               elapsed_seconds=int(fields[2]), allocated_cpus=int(fields[3]),
                               allocated_CPU_hours=int(fields[2])*int(fields[3])/3600))
    assert len(accounting) == 63
    assert [r['job_id'] for r in accounting if r['state'] == 'FAILED'] == ['68503_31']
    for filename, values in [('chunk-footers.csv', rows), ('allocated-cost.csv', accounting)]:
        with (OUT / filename).open('w', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(values[0]))
            writer.writeheader(); writer.writerows(values)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='compute_complete_payload_collection_and_full_audit_pending',
        scheduler_snapshot_utc=snapshot['utc'],
        scheduler_snapshot_uncompressed_sha256=digest(raw),
        manifest_sha256=digest(manifest_raw), plan_sha256=pins,
        chunks=len(rows), molecules=len(all_units), LLE_systems=sum(totals.values()),
        LLE_footer_statuses=dict(totals),
        accounted_tasks=len(accounting),
        scheduler_states=dict(collections.Counter(r['state'] for r in accounting)),
        allocated_CPU_hours_production_probe_recovery_helpers=sum(r['allocated_CPU_hours'] for r in accounting),
        excluded_costs='Other gate/diagnostic jobs, solvent DFT, local analysis and collection.',
        qualification='Completion footers and terminal accounting establish execution coverage only. Every raw payload still requires digest collection, full independent audit and post-export verification.',
        files={p.name: digest(p.read_bytes()) for p in OUT.iterdir() if p.is_file()},
        script_sha256=digest(Path(__file__).read_bytes()))
    (OUT / 'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
