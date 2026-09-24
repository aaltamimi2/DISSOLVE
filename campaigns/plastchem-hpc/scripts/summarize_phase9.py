"""Stream newly collected A-9 production records into compact progress counts."""
import collections, datetime, gzip, hashlib, json
from pathlib import Path

D = Path('/mnt/r/plastchem-euler/phase9-v1')


def main():
    ledger_path = D / 'production-progress-ledger.json'
    ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(bundles={}, files={}, units={})
    manifest = json.loads((D.parent / 'phase8-v1/manifest.json').read_text())
    positions = {(s['name'], regime): i * 2 + j for i, s in enumerate(manifest['solvents']) for j, regime in enumerate(['RT', 'high'])}
    def accept(path, value):
        if not path.startswith(('production-results-v1/', 'chunk-probe-results-v1/')): return
        if '/partition/' in path:
            assert len(value) == 640 and len({r['unit'] for r in value}) == 1
            unit = value[0]['unit']; item = ledger['units'].setdefault(unit, dict(lle_mask=0, lle_statuses={}))
            assert 'partition_rows' not in item
            assert all(r['status'] == 'predicted' for r in value)
            item['partition_rows'] = len(value)
        elif '/lle/' in path:
            item = ledger['units'].setdefault(value['unit'], dict(lle_mask=0, lle_statuses={}))
            bit = 1 << positions[value['solvent'], value['regime']]
            assert not item['lle_mask'] & bit
            item['lle_mask'] |= bit
            status = value['status']; item['lle_statuses'][status] = item['lle_statuses'].get(status, 0) + 1
    for p in (D / 'collected').glob('*.json.gz'):
        if p.name in ledger['files']: continue
        if not p.name.startswith(('production-results-v1__', 'chunk-probe-results-v1__')): continue
        with gzip.open(p, 'rt') as f: accept('/'.join(p.name.removesuffix('.gz').split('__',3)), json.load(f))
        ledger['files'][p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    registry = json.loads((D / 'collection.json').read_text())
    for bundle in registry.get('compact_bundles', []):
        if bundle['path'] in ledger['bundles']:
            assert ledger['bundles'][bundle['path']] == bundle['sha256']; continue
        p = Path(bundle['path']); assert hashlib.sha256(p.read_bytes()).hexdigest() == bundle['sha256']
        with gzip.open(p, 'rt') as f:
            for line in f:
                row = json.loads(line); accept(row['path'], row['value'])
        ledger['bundles'][bundle['path']] = bundle['sha256']
    units = ledger['units']; assert set(units) <= {f'cohort-{i:05d}' for i in range(5830)}
    status_counts = collections.Counter()
    for u in units.values(): status_counts.update(u['lle_statuses'])
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), denominator=5830,
                   partition_molecules=sum(u.get('partition_rows') == 640 for u in units.values()),
                   partition_rows=sum(u.get('partition_rows', 0) for u in units.values()),
                   partition_row_denominator=5830*640, LLE_statuses=dict(status_counts),
                   LLE_systems=sum(status_counts.values()), LLE_denominator=5830*64,
                   fully_evaluated_molecules=sum(u.get('partition_rows') == 640 and u['lle_mask'] == (1 << 64)-1 for u in units.values()))
    snap = json.loads((D / 'latest-gate-status.json').read_text())
    summary['scheduler_snapshot_utc'] = snap['utc']
    summary['queue_counts'] = dict(collections.Counter(s.split('|')[2] for s in snap['queue'].splitlines() if '|contam-phase9-production-v1|' in s))
    summary['recovery_queue_counts'] = dict(collections.Counter(s.split('|')[2] for s in snap['queue'].splitlines() if '|contam-phase9-retry-' in s))
    summary['execution_errors'] = snap['errors']
    jobs = []
    for name in ['production-submission.json', 'chunk-probe-submission.json']:
        p = D / name
        if p.exists(): jobs.append(json.loads(p.read_text())['job_id'])
    for p in D.glob('production-retry-*-submission.json'):
        jobs.append(json.loads(p.read_text())['job_id'])
    summary['allocated_cpu_hours_production_and_reused_probe'] = sum(
        int(parts[2])*int(parts[3])/3600 for parts in (s.split('|') for s in snap['accounting'].splitlines())
        if len(parts) >= 4 and '.' not in parts[0] and parts[0].split('_')[0] in jobs and parts[0].split('_')[-1].isdigit())
    for p, value in [(ledger_path, ledger), (D / 'production-summary.json', summary)]:
        temp = p.with_suffix('.tmp'); temp.write_text(json.dumps(value, indent=2)+'\n'); temp.replace(p)
    print(json.dumps(summary))


if __name__ == '__main__': main()
