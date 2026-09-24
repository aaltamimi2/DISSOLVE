"""Negative controls against a copy of the real partial delivery rehearsal."""
import datetime
import json
import shutil
import tempfile
from pathlib import Path

import duckdb
from verify_phase9_delivery import BULK, digest, verify


def main():
    source = BULK / 'phase9-v1/release-preview-v1'
    results = []

    def rejected(name, path, preview, expected):
        try:
            verify(path, preview)
        except AssertionError as exc:
            assert expected in str(exc), (name, str(exc))
            results.append(dict(test=name, status='passed', rejection=str(exc)))
        else:
            raise AssertionError('Verifier accepted negative control: ' + name)

    def reseal(path, filename):
        p = path / filename
        manifest = json.loads((path / 'manifest.json').read_text())
        manifest['files'][filename] = dict(bytes=p.stat().st_size, sha256=digest(p))
        manifest['payload_bytes'] = sum(v['bytes'] for v in manifest['files'].values())
        (path / 'manifest.json').write_text(json.dumps(manifest))

    rejected('preview_is_not_final', source, False, 'Incomplete preview')
    with tempfile.TemporaryDirectory(prefix='delivery-negative-', dir=BULK / 'phase9-v1') as tmp:
        root = Path(tmp) / 'fixture'
        # R: does not support copytree's copystat operations. Copy bytes only.
        root.mkdir()
        for original in source.rglob('*'):
            dest = root / original.relative_to(source)
            if original.is_dir():
                dest.mkdir(exist_ok=True)
            elif original.is_file():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, dest)
        readme = root / 'README.md'
        readme.write_text(readme.read_text() + '\nTampered payload\n')
        rejected('altered_payload_digest', root, True, 'README.md')
        shutil.copyfile(source / 'README.md', readme)
        con = duckdb.connect()
        con.execute("SET threads=1; SET memory_limit='256MB'")
        con.execute('SET temp_directory=?', [tmp])
        con.execute('''COPY (SELECT * REPLACE (
            CASE WHEN row_number() OVER ()=1 THEN 'not-a-panel-solvent'
            ELSE product_solvent_key END AS product_solvent_key)
            FROM read_parquet($source)) TO $destination (FORMAT PARQUET)''',
            {'source': str(source / 'partition.parquet'), 'destination': str(root / 'partition.parquet')})
        con.close()
        assert digest(source / 'partition.parquet') == json.loads((source / 'manifest.json').read_text())['files']['partition.parquet']['sha256']
        reseal(root, 'partition.parquet')
        rejected('unknown_solvent_even_after_resealing', root, True, 'partition_rows sources')
        shutil.copyfile(source / 'partition.parquet', root / 'partition.parquet')
        reseal(root, 'partition.parquet')
        summary = json.loads((root / 'summary.json').read_text())
        summary['status'] = 'complete'
        (root / 'summary.json').write_text(json.dumps(summary))
        reseal(root, 'summary.json')
        manifest = json.loads((root / 'manifest.json').read_text())
        manifest['status'] = 'complete'
        (root / 'manifest.json').write_text(json.dumps(manifest))
        rejected('partial_rows_falsely_labelled_complete', root, False, 'per-molecule coverage')
    out = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
               passed=len(results), results=results,
               scope='Copied preview only; no source checkpoint, scheduler job or sealed delivery changed.')
    (BULK / 'phase9-v1/delivery-verifier-negative-controls.json').write_text(json.dumps(out, indent=2) + '\n')
    print(json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
