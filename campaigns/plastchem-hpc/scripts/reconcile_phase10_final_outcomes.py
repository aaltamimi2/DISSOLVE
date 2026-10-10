"""Bind terminal exceptions, raw audits and both final releases without mutation."""
import collections
import csv
import datetime
import hashlib
import json
from pathlib import Path

import duckdb

B = Path('/mnt/r/plastchem-euler')
D = B / 'phase10-v1'


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def match_exceptions(expected, actual):
    """Compare identity, solvent, temperature and mode, including multiplicity."""
    assert len(expected) == len(set(expected)), 'Duplicate terminal exception'
    assert len(actual) == len(set(actual)), 'Duplicate release exception'
    assert set(expected) == set(actual), 'Terminal/release exceptions differ'


def main():
    out = D / 'final-outcome-reconciliation'
    assert not out.exists(), 'Reconcile existing output before rerunning'
    inventory = D / 'terminal-outcome-inventory'
    terminal = json.loads((inventory / 'summary.json').read_text())
    source = inventory / 'solver-reported-unresolved.csv'
    assert sha(source) == terminal['unresolved_csv_sha256']
    assert sha(inventory / 'footer-snapshot.json') == terminal['snapshot_sha256']
    rows = list(csv.DictReader(source.open()))
    expected = [(r['input_inchikey'], r['product_solvent_key'], float(r['temperature_K']), r['status']) for r in rows]
    con = duckdb.connect()
    con.execute('SET threads=1')
    con.execute("SET memory_limit='256MB'")
    pins = {}
    statuses = {}
    for alias, folder, release in [('primary', 'phase9-v1', 'promotion-v1'), ('extension', 'phase10-v1', 'promotion-ext39-v1')]:
        root = B / release
        verification = json.loads((B / folder / 'delivery-verification.json').read_text())
        assert verification['status'] == 'complete_delivery_verified'
        assert verification['manifest_sha256'] == sha(root / 'manifest.json')
        manifest = json.loads((root / 'manifest.json').read_text())
        data = root / 'binary-lle.parquet'
        assert sha(data) == manifest['files']['binary-lle.parquet']['sha256']
        con.read_parquet(str(data)).create_view(f'{alias}_lle')
        statuses[alias] = dict(con.execute(f'SELECT status,count(*) FROM {alias}_lle GROUP BY status').fetchall())
        raw_path = B / folder / ('raw-lle-audit-v2/summary.json' if alias == 'primary' else 'raw-lle-audit-v1/summary.json')
        raw = json.loads(raw_path.read_text())
        assert raw['status'] == 'complete' and raw['fully_evaluated_contaminants'] == 5830
        assert raw['statuses'] == statuses[alias]
        audit = json.loads((root / 'provenance/results-audit.json').read_text())
        assert raw['registry_snapshot_sha256'] == audit['collection_snapshot_sha256']
        pins[alias] = dict(manifest_sha256=sha(root / 'manifest.json'), lle_sha256=sha(data),
                           raw_audit_sha256=sha(raw_path), verification_sha256=sha(B / folder / 'delivery-verification.json'))
    actual = con.execute('SELECT input_inchikey,product_solvent_key,temperature_K,status FROM extension_lle WHERE NOT value_validated').fetchall()
    match_exceptions(expected, actual)
    assert len(actual) == terminal['unresolved_systems'] == 76
    assert statuses['extension'] == terminal['reported_statuses']
    assert sum(statuses['primary'].values()) == 373120
    assert sum(statuses['extension'].values()) == 227370
    primary_solvents = {r[0] for r in con.execute('SELECT DISTINCT product_solvent_key FROM primary_lle').fetchall()}
    extension_solvents = {r[0] for r in con.execute('SELECT DISTINCT product_solvent_key FROM extension_lle').fetchall()}
    assert len(primary_solvents) == 32 and len(extension_solvents) == 39 and not primary_solvents & extension_solvents
    con.execute('CREATE VIEW combined AS SELECT input_inchikey,product_solvent_key,temperature_regime,temperature_K,status,value_validated FROM primary_lle UNION ALL SELECT input_inchikey,product_solvent_key,temperature_regime,temperature_K,status,value_validated FROM extension_lle')
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,temperature_regime,temperature_K,count(*) n FROM combined GROUP BY ALL HAVING n<>1)').fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM (SELECT input_inchikey,count(*) n FROM combined GROUP BY input_inchikey HAVING n<>103)').fetchone()[0] == 0
    n, affected = con.execute('SELECT count(DISTINCT input_inchikey),count(DISTINCT CASE WHEN NOT value_validated THEN input_inchikey END) FROM combined').fetchone()
    assert n == 5830
    combined_statuses = dict(con.execute('SELECT status,count(*) FROM combined GROUP BY status').fetchall())
    assert sum(v for k,v in combined_statuses.items() if k not in ('single_liquid_phase','two_liquid_phases')) == 327
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), status='final_outcomes_reconciled',
        contaminants=5830, unique_solvents=71, common_solvents=69,
        LLE_rows=600490, extension_exceptions_matched=76, combined_unresolved_rows=327,
        contaminants_with_any_unresolved_LLE=affected, contaminants_with_all_LLE_qualified=n-affected,
        combined_statuses=combined_statuses, release_pins=pins, terminal_csv_sha256=sha(source),
        script_sha256=sha(Path(__file__)),
        scope='Exact terminal exception identities/modes match final export; both full raw audits bind to released snapshots. Combined LLE coverage is 32 solvents at two temperatures plus 39 at room temperature. Qualification is numerical, not experimental accuracy; no release or product was modified.')
    out.mkdir()
    con.execute('COPY (SELECT * FROM combined WHERE NOT value_validated ORDER BY input_inchikey,product_solvent_key,temperature_regime) TO ? (HEADER, DELIMITER \',\')', [str(out / 'combined-unresolved.csv')])
    summary['combined_unresolved_csv_sha256'] = sha(out / 'combined-unresolved.csv')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    con.close()
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
