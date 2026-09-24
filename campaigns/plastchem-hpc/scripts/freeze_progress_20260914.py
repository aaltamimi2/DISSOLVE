"""Freeze the report cohort, not the running campaign, at the owner cutoff."""
import argparse
import datetime as dt
import hashlib
import json
import os
import signal
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = Path('/mnt/r/plastchem-euler/progress-2026-09-14/freeze')
CUTOFF = dt.datetime.fromisoformat('2026-09-15T01:30:00+00:00')

def freeze(monitor_pid, receipt_name='freeze-receipt.json'):
    capture_started = dt.datetime.now(dt.timezone.utc).isoformat()
    DEST.mkdir(parents=True, exist_ok=False)
    files = []
    sources = [ROOT / 'CHARTER.txt', ROOT / 'state/campaign-v1/summary.json',
               ROOT / 'state/campaign-v1/eligible.json',
               ROOT / 'state/campaign-v1/main_le80/manifest.json',
               ROOT / 'reports/pilot-v1/analysis.json',
               ROOT / 'state/campaign-v1/latest-snapshot.json',
               ROOT / 'state/solvent-library-v1/summary.json',
               ROOT / 'state/solvent-library-v1/latest-snapshot.json',
               ROOT / 'state/thermodynamics-v1/library-registry.json',
               ROOT / 'state/thermodynamics-v1/processing-summary.json']
    # The local collector is the only live writer of campaign records. Pause it
    # only for the in-memory read, never during R: I/O or chemistry processing.
    proc = Path('/proc') / str(monitor_pid)
    command = (proc/'cmdline').read_bytes().split(b'\0')
    assert b'scripts/watch_campaign.py' in command
    assert (proc/'cwd').resolve() == ROOT
    paused = False
    try:
        os.kill(monitor_pid, signal.SIGSTOP)
        paused = True
        deadline = time.monotonic() + 5
        while not any(line.startswith('State:') and 'T' in line.split()[1]
                      for line in (proc/'status').read_text().splitlines()):
            assert time.monotonic() < deadline, 'Collector did not stop for snapshot'
            time.sleep(.01)
        effective_cutoff = dt.datetime.now(dt.timezone.utc).isoformat()
        for folder in ['state/campaign-v1/records', 'state/solvent-library-v1/records']:
            sources.extend(sorted((ROOT / folder).glob('*.json')))
        captured = [(source, source.read_bytes()) for source in sources]
    finally:
        if paused:
            os.kill(monitor_pid, signal.SIGCONT)
    collector_resumed = dt.datetime.now(dt.timezone.utc).isoformat()
    for source, content in captured:
        relative = source.relative_to(ROOT)
        target = DEST / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        assert hashlib.sha256(target.read_bytes()).hexdigest() == digest
        files.append({'path': str(relative), 'sha256': digest, 'bytes': len(content)})
    cohort = []
    counts = {}
    frozen_records = {}
    for f in sorted((DEST / 'state/campaign-v1/records').glob('*.json')):
        r = json.loads(f.read_text())
        frozen_records[f.stem] = r
        status = r.get('status', 'unknown')
        counts[status] = counts.get(status, 0) + 1
        if status == 'converged':
            cohort.append({'inchikey': f.stem, 'surface_sha256': r['surface_sha256'],
                           'archive_path': r['archive_path']})
    eligible = json.loads((DEST/'state/campaign-v1/eligible.json').read_text())
    campaign_counts = dict(converged=0, failed=0, running=0, awaiting_verification=0, not_yet_run=0)
    for molecule in eligible:
        status = frozen_records.get(molecule['inchikey'], {}).get('status', 'not_yet_run')
        category = status if status in ['converged', 'failed', 'not_yet_run'] else 'awaiting_verification' if status=='converged_identity_pending' else 'running'
        campaign_counts[category] += 1
    assert len(eligible)==5824 and sum(campaign_counts.values())==5824
    support_counts = json.loads((DEST/'state/solvent-library-v1/summary.json').read_text())['counts']
    manifest = {'cutoff_utc': CUTOFF.isoformat(),
                'effective_collector_cutoff_utc': effective_cutoff,
                'collector_resumed_utc': collector_resumed,
                'collector_pid': monitor_pid,
                'capture_started_utc': capture_started,
                'captured_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'cohort': cohort, 'record_status_counts': counts,
                'campaign_counts_from_frozen_records': campaign_counts,
                'support_solvent_counts': support_counts,
                'campaign_denominator': 5824, 'support_solvent_denominator': 6,
                'files': files,
                'interpretation': 'Report membership frozen from locally returned records; scheduler timestamps retained. Later returns remain in live campaign but cannot enter this report cohort. Thermodynamic calculations may finish after cutoff for this fixed cohort.'}
    payload = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    snapshot_id = hashlib.sha256(payload).hexdigest()
    (DEST / 'manifest.json').write_bytes(payload)
    receipt = {'snapshot_id': snapshot_id, 'manifest': str(DEST / 'manifest.json'),
               'captured_utc': manifest['captured_utc'], 'cohort_count': len(cohort)}
    (ROOT / 'state/progress-2026-09-14' / receipt_name).write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt), flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--wait-until-cutoff', action='store_true')
    parser.add_argument('--monitor-pid', type=int, required=True)
    args = parser.parse_args()
    if not args.wait_until_cutoff:
        parser.error('Explicit --wait-until-cutoff required; no premature freeze')
    while dt.datetime.now(dt.timezone.utc) < CUTOFF:
        time.sleep(min(30, (CUTOFF-dt.datetime.now(dt.timezone.utc)).total_seconds()))
    freeze(args.monitor_pid)
