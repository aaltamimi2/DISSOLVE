"""Maintain versioned full-denominator CSV snapshots as tier-2 records change.

Read-only toward scientific outputs. No cluster access or activity calculations.
Stops on an unexpected error; resumes from the last published input signature.
"""
import argparse
import collections
import csv
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

R = Path(__file__).resolve().parents[1]
P = R / 'state/tier2-v1/thermodynamics'
D = Path('/mnt/r/plastchem-euler/tier2-v1/thermodynamics')
W = D / 'export-watch'
META = ['state/tier2-v1/tier2/manifest.json',
        'state/tier2-v1/thermodynamics/processing-ledger.json',
        'state/thermodynamics-v1/library-registry.json',
        'state/thermodynamics-v1/solvent-phase-review.json']


def sha_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def status(name, **kwargs):
    value = dict(kwargs)
    value.update(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status=name)
    save(W / 'status.json', value)
    print(json.dumps(value), flush=True)


def inputs():
    paths = [R / n for n in META] + sorted((R / 'state/tier2-v1/records').glob('*.json'))
    raw = {str(p.relative_to(R)): p.read_bytes() for p in paths}
    pins = {name: sha_bytes(value) for name, value in raw.items()}
    signature = sha_bytes(json.dumps(pins, sort_keys=True).encode())
    return raw, pins, signature


def verify_table(folder):
    summary = json.loads((folder / 'table-export.json').read_text())
    path = folder / 'partitioning-current.csv'
    assert path.stat().st_size == summary['csv_bytes'] and sha_bytes(path.read_bytes()) == summary['csv_sha256']
    counts = collections.Counter()
    keys = set()
    concentrations = 0
    with path.open() as f:
        for row in csv.DictReader(f):
            key = (row['input_inchikey'], row['solvent'], row['reference'])
            assert key not in keys
            keys.add(key)
            counts[row['status']] += 1
            if row['status'] == 'predicted':
                assert row['log10_K_mole_fraction'] != ''
                concentrations += row['log10_K_concentration'] != ''
            else:
                assert row['log10_K_mole_fraction'] == row['log10_K_concentration'] == ''
    assert len(keys) == summary['table_rows'] == 8640
    assert dict(counts) == summary['row_status_counts']
    assert concentrations == summary['concentration_prediction_rows']
    return summary


def capture_and_export(raw, pins, signature, publish=True):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    out = D / 'exports' / ('auto-' + stamp)
    freeze = out / 'freeze'
    freeze.mkdir(parents=True)
    record_bundle = {}
    for name, value in raw.items():
        if name.startswith('state/tier2-v1/records/'):
            record_bundle[Path(name).stem] = value.decode('utf-8')
            continue
        path = freeze / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        assert sha_bytes(path.read_bytes()) == pins[name]
    # Retain exact original JSON text in one file to avoid hundreds of small
    # network-filesystem writes for pending/running metadata each pass.
    save(freeze / 'captured-records.json', record_bundle)
    restored = json.loads((freeze / 'captured-records.json').read_text())
    for key, value in restored.items():
        assert sha_bytes(value.encode('utf-8')) == pins[f'state/tier2-v1/records/{key}.json']
    ledger_name = 'state/tier2-v1/thermodynamics/processing-ledger.json'
    ledger = json.loads(raw[ledger_name])
    for key, item in ledger.items():
        value = Path(item['result_path']).read_bytes()
        assert sha_bytes(value) == item['result_sha256'], ('Scientific result changed during capture', key)
        path = freeze / 'results' / (key + '.json')
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(value)
        assert sha_bytes(path.read_bytes()) == item['result_sha256']
        item['result_path'] = str(path)
    save(freeze / ledger_name, ledger)
    save(out / 'capture.json', dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        input_signature=signature, original_input_sha256=pins, captured_thermodynamic_records=len(ledger),
        scope='Per-file captured metadata and hash-bound result snapshot; not an instantaneous scheduler snapshot or scientific completion claim.'))
    script = R / 'scripts/export_tier2_thermodynamic_table.py'
    with (out / 'export.log').open('w') as log:
        subprocess.run([sys.executable, str(script), '--snapshot-root', str(freeze), '--output-dir', str(out)],
                       stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, check=True)
    summary = verify_table(out)
    files = {str(p.relative_to(out)): sha_bytes(p.read_bytes()) for p in sorted(out.rglob('*')) if p.is_file()}
    save(out / 'manifest.json', dict(status='csv_snapshot_verified', input_signature=signature,
        exporter_sha256=sha_bytes(script.read_bytes()), files=files,
        scope='Complete 270-by-32 requested-row table with explicit missingness; not complete scientific coverage.'))
    for name, digest in files.items():
        assert sha_bytes((out / name).read_bytes()) == digest
    receipt = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), input_signature=signature,
        snapshot_path=str(out), manifest_sha256=sha_bytes((out / 'manifest.json').read_bytes()), summary=summary)
    if publish:
        # The authoritative receipt points at an immutable versioned CSV.
        # The conventional filename is refreshed atomically for older readers.
        with (P / 'export.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            temp = D / 'partitioning-current.csv.export-tmp'
            shutil.copyfile(out / 'partitioning-current.csv', temp)
            assert sha_bytes(temp.read_bytes()) == summary['csv_sha256']
            temp.replace(D / 'partitioning-current.csv')
            save(P / 'table-export.json', summary)
            save(W / 'latest.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--no-publish', action='store_true')
    args = parser.parse_args()
    W.mkdir(exist_ok=True)
    lock = (P / 'export-watch.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    scripts = ['watch_tier2_exports.py', 'export_tier2_thermodynamic_table.py']
    code_pins = {name: sha_bytes((R / 'scripts' / name).read_bytes()) for name in scripts}
    charter = sha_bytes((R / 'CHARTER.txt').read_bytes())
    save(W / 'process.json', dict(pid=os.getpid(), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        script_pins=code_pins, charter_sha256=charter))
    previous = json.loads((W / 'latest.json').read_text())['input_signature'] if (W / 'latest.json').exists() else None
    while True:
        assert sha_bytes((R / 'CHARTER.txt').read_bytes()) == charter, 'Review changed charter'
        for name, digest in code_pins.items():
            assert sha_bytes((R / 'scripts' / name).read_bytes()) == digest, ('Changed exporter code', name)
        raw, pins, signature = inputs()
        available = int(next(s.split()[1] for s in Path('/proc/meminfo').read_text().splitlines() if s.startswith('MemAvailable:'))) * 1024
        if signature != previous and available >= 2.5 * 1024**3:
            receipt = capture_and_export(raw, pins, signature, publish=not args.no_publish)
            status('snapshot_verified' if args.no_publish else 'snapshot_published', **receipt)
            previous = signature
        else:
            status('waiting_for_input_change' if signature == previous else 'memory_guard', input_signature=signature)
        if args.once:
            return
        time.sleep(60)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        if W.exists():
            status('inspection_required', error=repr(exc))
        raise
