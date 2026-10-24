"""A-13 (owner decision 2026-10-12): give every D-IDENT rejection of the coverage campaign the third observation of
template_identity.py (bonds from the coordinates, bond orders from the input). A rejection whose bonded graph is the
input's becomes an accepted structure, with the earlier rejection kept in the record; every other rejection keeps its
status and gains the observation with the reason it failed. Returned digests are checked again before a record changes.
Idempotent: a record that already carries the observation is left alone. Run after each collection pass.

    python3 scripts/apply_template_identity.py [--dry-run]     (anaconda RDKit 2023.09.2, the identity environment)"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity_campaign import decide  # noqa: E402
from template_identity import METHOD, template_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'state/coverage-v1'
DEST = Path('/mnt/r/plastchem-euler/coverage-v1/results')
REJECTION = 'No perception engine produced the required first-block match'
AUTHORITY = ('Owner 2026-10-12: fix the identity rejections where justifiable, otherwise drop. Accepted only when the '
             'optimized geometry is bonded exactly as the input (RDKit and Open Babel connectivity, every hydrogen '
             'assigned); a moved hydrogen or a changed skeleton stays rejected.')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, obj):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=2) + '\n')
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    assert DEST.is_dir(), 'R: is not mounted'
    ledger_path = P / 'retrieved.json'
    ledger = json.loads(ledger_path.read_text())
    accepted, kept = [], []
    for path in sorted((P / 'records').glob('*.json')):
        r = json.loads(path.read_text())
        if r.get('failure_mode') != 'return_integrity_or_connectivity' or r.get('error') != REJECTION:
            continue
        if any(o.get('method') == METHOD for o in r['identity_observations']):
            continue
        key, group = r['inchikey'], r['group']
        folder = (DEST / 'retries' / group if (P / group / 'manifest.json').exists()
                  and json.loads((P / group / 'manifest.json').read_text()).get('retry_folders') else DEST) / key
        assert ledger[key]['group'] == group and ledger[key]['surface_sha256'] == r['surface_sha256'], key
        assert sha(folder / 'surface.orcacosmo') == r['surface_sha256'], key + ' surface digest'
        for stage, info in r['stages'].items():
            assert sha(folder / (stage + '.inp')) == info['input_sha256'], key + ' ' + stage + ' digest'
        row = template_row(folder / 'optimized.xyz', r['input']['smiles'], key)
        observations = [*r['identity_observations'], row]
        if 'full_inchikey' in row:
            authority = r['identity_authority']
            r.update(decide(key, observations))
            assert r['identity_verified'] and r['perceived_inchikey'] == row['full_inchikey'], key
            r.update(status='converged', archive_path=str(folder), identity_authority=authority,
                     identity_match_basis='connectivity_first_block',
                     identity_exception=dict(rule=METHOD, authority=AUTHORITY, earlier_failure_mode=r.pop('failure_mode'),
                                             earlier_error=r.pop('error'),
                                             utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            accepted.append(key)
        else:
            r['identity_observations'] = observations
            kept.append((key, row['error']))
        if not args.dry_run:
            write(folder / 'result.json', r)
            write(path, r)
            if 'full_inchikey' in row:
                ledger[key] = dict(ledger[key], status='converged')
                write(ledger_path, ledger)
    reasons = {}
    for _, reason in kept:
        reasons[reason] = reasons.get(reason, 0) + 1
    print(json.dumps(dict(dry_run=args.dry_run, accepted=len(accepted), still_rejected=len(kept), reasons=reasons, accepted_keys=accepted)))


if __name__ == '__main__':
    main()
