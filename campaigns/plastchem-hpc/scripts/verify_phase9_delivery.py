"""Read-only, post-export A-8.4 delivery checks independent of the exporter.

Default rejects a preview. --allow-preview verifies present rows, never asserts
full-cohort completion. DuckDB spills to lane bulk storage, not local root.
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import json
import math
import tempfile
from pathlib import Path

import duckdb

BULK = Path('/mnt/r/plastchem-euler')
PINS = {
    'cohort.json': '400e809c9adbaf41ce69dc1686106673ad8522aa6fc8967930c6f10a32e63f5c',
    'phase8-manifest.json': 'a24d1c80b2cae8bc19a23edcc7c0a6b3431b3dc46a6fbe15bf1f419929a61272',
    'phase8-validation-inputs.json': '4e89584c59701383ddc044295d0d833dd8bb5c4040dfadd5f597a4b219b9dc21',
}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()




def verify_qualified_ranges(con):
    # Preserve the raw value: 100 * MW / MW can round one ULP above 100.
    # This is not a physical tolerance or an empirical model correction.
    upper=math.nextafter(100.0,math.inf)
    bad=con.execute("""SELECT count(*) FROM lle_rows WHERE value_validated AND
        (solute_mole_fraction_solubility IS NULL OR solute_wt_percent_solubility IS NULL
        OR NOT isfinite(solute_mole_fraction_solubility) OR NOT isfinite(solute_wt_percent_solubility)
        OR solute_mole_fraction_solubility NOT BETWEEN 0 AND 1
        OR solute_wt_percent_solubility NOT BETWEEN 0 AND ?)""",[upper]).fetchone()[0]
    assert bad==0,('qualified LLE range',bad)
    return con.execute('SELECT count(*) FROM lle_rows WHERE value_validated AND solute_wt_percent_solubility>100').fetchone()[0]


def verify_failure_exports(con, code_dir, provenance_dir):
    """Check the delivered values independently of raw-worker/exporter code."""
    allowed=('single_liquid_phase','two_liquid_phases','grid_or_tie_line_unresolved','grid_not_converged','activity_nonconvergence')
    bad=con.execute('SELECT count(*) FROM lle_rows WHERE status NOT IN (?,?,?,?,?)',allowed).fetchone()[0]
    assert bad==0,('Unknown exported LLE status',bad)
    bad=con.execute("""SELECT count(*) FROM lle_rows WHERE NOT value_validated AND
        (above_15_mol_percent IS NOT NULL OR above_15_wt_percent IS NOT NULL)""").fetchone()[0]
    assert bad==0,('Unresolved exported LLE has a threshold verdict',bad)
    fields=['solute_mole_fraction_solubility','solute_wt_percent_solubility',
            'x_contaminant_solvent_rich','x_solvent_solvent_rich','x_contaminant_solute_rich','x_solvent_solute_rich',
            'wt_percent_contaminant_solvent_rich','wt_percent_contaminant_solute_rich',
            'grid_change_mol_percentage_points','grid_change_wt_percentage_points',
            'max_chemical_potential_residual_RT','minimum_tangent_distance_RT']
    bad=con.execute("SELECT count(*) FROM lle_rows WHERE status='activity_nonconvergence' AND ("+
                    ' OR '.join(field+' IS NOT NULL' for field in fields)+")").fetchone()[0]
    assert bad==0,('Nonconvergence exported a numerical prediction',bad)
    count=0
    if con.execute("SELECT count(*) FROM lle_rows WHERE status='activity_nonconvergence'").fetchone()[0]==0:
        return 0  # Earlier previews can have no such rows and no evidence column.
    rows=con.execute("""SELECT input_inchikey,failure_mode,tie_lines_json,grid_checks_json,failure_evidence_json
        FROM lle_rows WHERE status='activity_nonconvergence'""").fetchall()
    if rows:
        handler=digest(code_dir/'phase9_failure_policy.py')
        assert handler=='e77434846fa4db3fc033d4f79c3293a0ba7d7f8fbc972d06fe7b652c589db321'
        entries={digest(code_dir/'phase9_retry_entry.py'):'retry'}
        tail=code_dir/'phase9_tail_helper.py'
        if tail.exists():entries[digest(tail)]='tail'
        for key,mode,ties,grids,encoded in rows:
            assert mode=='cosmospace_binary_grid_nonconvergence'
            assert json.loads(ties)==[] and json.loads(grids)==[]
            evidence=json.loads(encoded or '{}')
            assert evidence.get('exception_type')=='ValueError','Missing nonconvergence exception evidence'
            assert evidence.get('exception_message')=='COSMOspace did not converge for binary grid'
            assert 'ValueError: COSMOspace did not converge for binary grid' in evidence.get('traceback','')
            assert evidence.get('failure_policy_sha256')==handler
            policy=evidence['recovery_policy']
            assert policy['failure_policy_sha256']==handler
            assert policy['entry_sha256'] in entries
            if entries[policy['entry_sha256']]=='tail':
                assignment=provenance_dir/'assignment.json'
                assert policy['assignment_sha256']==digest(assignment)
                a=json.loads(assignment.read_text());cohort=json.loads((provenance_dir/'cohort.json').read_text())['rows']
                unit=next('cohort-'+format(r['index'],'05d') for r in cohort if r['inchikey']==key)
                assert unit!=a['retained_unit'] and sum(unit in group for group in a['groups'])==1
            count+=1
    return count


def verify(root, allow_preview=False):
    root = root.resolve()
    manifest = json.loads((root / 'manifest.json').read_text())
    summary = json.loads((root / 'summary.json').read_text())
    complete = summary['status'] == 'complete'
    assert complete or allow_preview, 'Incomplete preview is not a final delivery'
    assert manifest['status'] == summary['status']
    actual = set()
    for p in root.rglob('*'):
        assert not p.is_symlink(), ('Unexpected symlink', str(p))
        if p.is_file() and p != root / 'manifest.json':
            actual.add(str(p.relative_to(root)))
    assert actual == set(manifest['files']), 'Missing or unmanifested payload files'
    for name, pin in manifest['files'].items():
        rel = Path(name)
        assert not rel.is_absolute() and '..' not in rel.parts
        p = root / rel
        assert p.stat().st_size == pin['bytes'] and digest(p) == pin['sha256'], name
    size = sum(p['bytes'] for p in manifest['files'].values())
    assert size == manifest['payload_bytes'] and size < 200_000_000
    for name, pin in PINS.items():
        assert digest(root / 'provenance' / name) == pin, name
    cohort = json.loads((root / 'provenance/cohort.json').read_text())['rows']
    phase = json.loads((root / 'provenance/phase8-manifest.json').read_text())
    validation = json.loads((root / 'provenance/phase8-validation-inputs.json').read_text())
    bykey = {r['inchikey']: r for r in cohort}
    assert len(cohort) == len(bykey) == 5830
    temperatures = {(r['solvent'], r['regime'], r['temperature_K']) for r in validation['lle_units']}
    assert len(temperatures) == 64
    with gzip.open(root / 'contaminants.csv.gz', 'rt', newline='') as f:
        identities = list(csv.DictReader(f))
    assert len(identities) == len({r['input_inchikey'] for r in identities}) == 6103
    assert collections.Counter(r['campaign_status_at_snapshot'] for r in identities) == {
        'converged': 5830, 'failed': 28, 'not_yet_run': 236, 'excluded_isotope': 9}
    included = {r['input_inchikey']: r for r in identities if r['in_phase83_snapshot'] == 'True'}
    assert set(included) == set(bykey)
    for key, r in included.items():
        c = bykey[key]
        assert r['surface_sha256'] == c['surface_sha256']
        assert r['perceived_inchikey'] == c['perceived_inchikey']
        assert key.split('-')[0] == r['perceived_inchikey'].split('-')[0]
        assert r['name'] == c['input']['name'] and r['smiles'] == c['input']['smiles']
    with (root / 'polymer-product-map.csv').open() as f:
        mapping = list(csv.DictReader(f))
    assert {r['campaign_polymer'] for r in mapping} == set(phase['polymers'])
    assert {r['product_polymer_key'] for r in mapping if r['campaign_polymer'] == 'pe'} == {'LDPE', 'HDPE'}
    assert len(mapping) == 11
    for r in mapping:
        assert int(r['conformer_count']) == len(phase['polymers'][r['campaign_polymer']])
    with tempfile.TemporaryDirectory(prefix='delivery-check-', dir=BULK / 'phase9-v1') as tmp:
        con = duckdb.connect()
        con.execute("SET threads=1; SET memory_limit='256MB'")
        con.execute('SET temp_directory=?', [tmp])
        con.execute('CREATE TABLE expected_solutes (key VARCHAR, sha VARCHAR)')
        con.executemany('INSERT INTO expected_solutes VALUES (?,?)', [(r['inchikey'], r['surface_sha256']) for r in cohort])
        con.execute('CREATE TABLE expected_solvents (key VARCHAR, sha VARCHAR)')
        con.executemany('INSERT INTO expected_solvents VALUES (?,?)', [(r['name'], Path(r['B']).stem) for r in phase['solvents']])
        con.execute('CREATE TABLE expected_polymers (key VARCHAR)')
        con.executemany('INSERT INTO expected_polymers VALUES (?)', [(p,) for p in phase['polymers']])
        con.execute('CREATE TABLE expected_temperatures (solvent VARCHAR, regime VARCHAR, temperature DOUBLE)')
        con.executemany('INSERT INTO expected_temperatures VALUES (?,?,?)', sorted(temperatures))
        con.read_parquet(str(root / 'partition.parquet')).create_view('partition_rows')
        con.read_parquet(str(root / 'binary-lle.parquet')).create_view('lle_rows')

        def zero(sql, label):
            n = con.execute(sql).fetchone()[0]
            assert n == 0, (label, n)

        counts = {}
        for table, key in [('partition_rows', 'input_inchikey,product_solvent_key,campaign_polymer,convention,temperature_K'),
                           ('lle_rows', 'input_inchikey,product_solvent_key,temperature_regime,temperature_K')]:
            counts[table] = con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            zero(f'SELECT count(*) FROM (SELECT {key},count(*) n FROM {table} GROUP BY ALL HAVING n<>1)', table + ' duplicates')
            zero(f'''SELECT count(*) FROM {table} r LEFT JOIN expected_solutes c ON r.input_inchikey=c.key
                LEFT JOIN expected_solvents s ON r.product_solvent_key=s.key
                WHERE c.key IS NULL OR s.key IS NULL OR r.solute_surface_sha256 IS DISTINCT FROM c.sha
                OR r.solvent_surface_sha256 IS DISTINCT FROM s.sha
                OR r.parameterization IS DISTINCT FROM 'openCOSMO-RS 24a' OR r.status IS NULL''', table + ' sources')
        zero('''SELECT count(*) FROM partition_rows r LEFT JOIN expected_polymers p ON r.campaign_polymer=p.key
            WHERE p.key IS NULL OR r.convention IS NULL OR r.convention NOT IN ('normalized','existing')
            OR r.temperature_K IS DISTINCT FROM 298.15 OR r.solute_mole_fraction IS DISTINCT FROM 0.0
            OR r.reference_state IS DISTINCT FROM 'pure_component'
            OR (r.status='predicted' AND (r.logP_x IS NULL OR r.logP_concentration IS NULL
            OR NOT isfinite(r.logP_x) OR NOT isfinite(r.logP_concentration)))''', 'partition dimensions/values')
        zero('''SELECT count(*) FROM lle_rows r LEFT JOIN expected_temperatures t
            ON r.product_solvent_key=t.solvent AND r.temperature_regime=t.regime AND r.temperature_K=t.temperature
            WHERE t.solvent IS NULL OR r.value_validated IS DISTINCT FROM
            (r.status IN ('single_liquid_phase','two_liquid_phases'))''', 'LLE temperatures/qualification')
        endpoint_roundoff_rows=verify_qualified_ranges(con)
        failure_rows=verify_failure_exports(con,root/'provenance/code',root/'provenance')
        for table, per_unit in [('partition_rows', 640), ('lle_rows', 64)]:
            comparator = '<>' if complete else '>'
            zero(f'SELECT count(*) FROM (SELECT input_inchikey,count(*) n FROM {table} GROUP BY 1 HAVING n {comparator} {per_unit})', table + ' per-molecule coverage')
        assert counts['partition_rows'] == summary['partition_rows']
        assert counts['lle_rows'] == summary['LLE_rows']
        if complete:
            assert counts == {'partition_rows': 3_731_200, 'lle_rows': 373_120}
            assert summary['fully_evaluated_contaminants'] == 5830
        statuses = {table: dict(con.execute(f'SELECT status,count(*) FROM {table} GROUP BY 1').fetchall()) for table in counts}
        per_partition = {key: (good, bad) for key, good, bad in con.execute('''
            SELECT input_inchikey,count(*) FILTER (WHERE status='predicted'),
            count(*) FILTER (WHERE status<>'predicted') FROM partition_rows GROUP BY 1''').fetchall()}
        per_lle = {key: (good, bad) for key, good, bad in con.execute('''
            SELECT input_inchikey,count(*) FILTER (WHERE value_validated),
            count(*) FILTER (WHERE NOT value_validated) FROM lle_rows GROUP BY 1''').fetchall()}
        evaluated = qualified = 0
        for row in identities:
            key = row['input_inchikey']
            p = per_partition.get(key, (0, 0)); l = per_lle.get(key, (0, 0))
            for field, expected in zip(['partition_predicted_rows', 'partition_failed_rows',
                                       'lle_qualified_rows', 'lle_unresolved_or_failed_rows'], p + l):
                assert int(row[field] or 0) == expected, (key, field)
            done = sum(p) == 640 and sum(l) == 64
            good = p[0] == 640 and l[0] == 64
            assert (row['all_requested_quantities_evaluated'] == 'True') == done
            assert (row['all_requested_quantities_qualified'] == 'True') == good
            evaluated += done; qualified += good
        assert evaluated == summary['fully_evaluated_contaminants']
        assert qualified == summary['fully_qualified_contaminants']
        con.close()
    return dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                status='complete_delivery_verified' if complete else 'partial_preview_verified_NOT_complete',
                path=str(root), manifest_sha256=digest(root / 'manifest.json'),
                payload_files=len(actual), payload_bytes=size, counts=counts, statuses=statuses, nonconvergence_export_rows_verified=failure_rows,endpoint_roundoff_rows_preserved=endpoint_roundoff_rows,
                scope='Post-export hashes, frozen source pins, identities, dimensions, unique keys, coverage and status qualification. Does not independently solve COSMOspace or establish experimental accuracy.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', nargs='?', type=Path, default=BULK / 'promotion-v1')
    parser.add_argument('--allow-preview', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        assert not args.output.resolve().is_relative_to(args.directory.resolve()), 'Do not modify a sealed delivery'
    result = verify(args.directory, args.allow_preview)
    text = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(text)
    print(text, end='')
