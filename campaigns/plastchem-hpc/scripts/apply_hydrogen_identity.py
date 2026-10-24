"""A-13 (owner decision 2026-10-13, "add the hydrogen check"): give every accepted structure of the coverage campaign
the observation of hydrogen_identity.py (every hydrogen nearest the heavy atom it is bonded to in the input). An accepted
structure in which a hydrogen moved becomes a D-IDENT rejection, with the earlier acceptance kept in the record; every
other accepted structure keeps its status and gains the observation. Returned digests are checked again before a record
changes. Idempotent: a record that already carries the observation is left alone. Run after each collection pass
(structures collected from now on get the observation in the collection itself).

    python3 scripts/apply_hydrogen_identity.py [--dry-run]     (anaconda RDKit 2023.09.2, the identity environment)"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from identity_campaign import decide  # noqa: E402
from hydrogen_identity import METHOD, hydrogen_row  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / 'state/coverage-v1'
AUTHORITY = ('Owner 2026-10-13: "drop acid fuchsine, keep the three bridged, add the hydrogen check". An accepted '
             'structure is withdrawn when a hydrogen of the optimized geometry is nearer another heavy atom than the '
             'one it is bonded to in the input.')


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
    assert Path('/mnt/r/plastchem-euler/coverage-v1/results').is_dir(), 'R: is not mounted'
    ledger_path = P / 'retrieved.json'
    ledger = json.loads(ledger_path.read_text())
    kept, withdrawn = 0, []
    for path in sorted((P / 'records').glob('*.json')):
        r = json.loads(path.read_text())
        if r.get('status') != 'converged' or any(o.get('method') == METHOD for o in r['identity_observations']):
            continue
        key, folder = r['inchikey'], Path(r['archive_path'])
        assert ledger[key]['status'] == 'converged' and ledger[key]['surface_sha256'] == r['surface_sha256'], key
        assert sha(folder / 'surface.orcacosmo') == r['surface_sha256'], key + ' surface digest'
        for stage, info in r['stages'].items():
            assert sha(folder / (stage + '.inp')) == info['input_sha256'], key + ' ' + stage + ' digest'
        row = hydrogen_row(folder / 'optimized.xyz', r['input']['smiles'])
        authority, exception = r['identity_authority'], r.get('identity_exception')
        decision = decide(key, [*r['identity_observations'], row])
        assert decision['connectivity_match'] and decision['perceived_inchikey'] == r['perceived_inchikey'], key
        r.update(decision, identity_authority=authority)
        if exception:
            r['identity_exception'] = exception
        if r['identity_verified']:
            kept += 1
        else:
            r.update(status='failed', failure_mode='return_integrity_or_connectivity', error=r['identity_rejection'],
                     identity_withdrawn=dict(rule=METHOD, authority=AUTHORITY, earlier_status='converged',
                                             earlier_archive_path=r.pop('archive_path'),
                                             utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())))
            withdrawn.append((key, r['error']))
        if not args.dry_run:
            write(folder / 'result.json', r)
            write(path, r)
            if not r['identity_verified']:
                ledger[key] = dict(ledger[key], status='failed')
                write(ledger_path, ledger)
    print(json.dumps(dict(dry_run=args.dry_run, kept=kept, withdrawn=withdrawn), indent=1))


if __name__ == '__main__':
    main()
