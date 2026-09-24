"""Hash the reviewed final progress package; never include live preview outputs."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BULK = Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    target = BULK / 'release-manifest.json'
    if args.verify:
        manifest = json.loads(target.read_text())
        for item in manifest['files']:
            path = BULK / item['path']
            assert path.stat().st_size == item['bytes'], item['path']
            assert digest(path) == item['sha256'], item['path']
        print(json.dumps({'status': 'verified', 'files': len(manifest['files']),
                          'release_id': digest(target)}))
        return
    assert not target.exists(), 'Existing release must not be overwritten'
    review = json.loads((BULK / 'final-visual-review.json').read_text())
    required_figures = [
        'figures/campaign-progress.png', 'figures/walltime-vs-atoms.png',
        'figures/computed-value-distribution.png',
        'experimental-validation/predicted-vs-experimental.png',
    ]
    for name in required_figures:
        item = review['figures'][name]
        assert item['status'] == 'passed' and item['sha256'] == digest(BULK / name)
    assert review['report_status'] == 'passed'
    assert review['report_sha256'] == digest(BULK / 'REPORT.md')
    assert digest(ROOT / 'reports/progress-2026-09-14/REPORT.md') == review['report_sha256']
    audit = json.loads((BULK / 'table-audit.json').read_text())
    assert audit['frozen'] and audit['status'] == 'passed'
    assert audit['rows_checked'] == 5824 * 32
    assert audit['table_sha256'] == digest(BULK / 'partitioning-frozen.csv')
    files = []
    for name in ['freeze', 'sealed-thermodynamics', 'figures', 'experimental-validation']:
        directory = BULK / name
        assert directory.is_dir(), name
        files.extend(p for p in directory.rglob('*') if p.is_file())
    for name in ['REPORT.md', 'final-visual-review.json', 'table-audit.json',
                 'completed-surface-audit.json', 'support-solvent-surface-audit.json',
                 'production-anchor-agreement.csv', 'partitioning-frozen.csv',
                 'campaign-dispositions.csv', 'excluded-isotopologues.csv']:
        path = BULK / name
        assert path.is_file(), name
        files.append(path)
    manifest = {
        'released_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'cohort_snapshot_id': digest(BULK / 'freeze/manifest.json'),
        'numeric_snapshot_id': digest(BULK / 'sealed-thermodynamics/manifest.json'),
        'scope': 'Frozen progress package; full campaign and solvent panel remain separate completion requirements.',
        'files': [{'path': str(p.relative_to(BULK)), 'bytes': p.stat().st_size,
                   'sha256': digest(p)} for p in sorted(set(files))],
    }
    with target.open('x') as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    receipt = {'release_id': digest(target), 'manifest': str(target),
               'files': len(manifest['files']), 'released_utc': manifest['released_utc']}
    (ROOT / 'state/progress-2026-09-14/release-receipt.json').write_text(
        json.dumps(receipt, indent=2) + '\n')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
