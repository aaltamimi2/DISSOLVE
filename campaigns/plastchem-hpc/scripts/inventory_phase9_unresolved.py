"""Name every terminal-footer LLE exception; optionally bind to final Parquet.

Footer-only output is explicitly provisional until the independent payload audit
and the optional final-table comparison pass. No values are recalculated.
"""
import argparse
import collections
import csv
import datetime
import gzip
import hashlib
import json
from pathlib import Path

D = Path('/mnt/r/plastchem-euler/phase9-v1')


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''): h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--delivery', type=Path)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    source = D / 'compute-completion-v1'
    evidence = json.loads((source / 'summary.json').read_text())
    for name, digest in evidence['files'].items(): assert sha(source / name) == digest, name
    with gzip.open(source / 'scheduler-footer-snapshot.json.gz', 'rt') as stream:
        snapshot = json.load(stream)
    cohort_path = D.parent / 'phase83-v1/cohort.json'
    cohort = json.loads(cohort_path.read_text())
    byunit = {f"cohort-{r['index']:05d}": r for r in cohort['rows']}
    validation_path = D.parent / 'phase8-v1/validation-inputs.json'
    validation = json.loads(validation_path.read_text())
    temperatures = {}
    for unit in validation['lle_units']:
        key = unit['solvent'], unit['regime']
        previous = temperatures.setdefault(key, unit['temperature_K'])
        assert previous == unit['temperature_K']
    assert len(temperatures) == 64
    rows, totals = [], collections.Counter()
    for chunk, footer in snapshot['complete'].items():
        if not chunk.startswith(('production-results-v1/', 'chunk-probe-results-v1/')): continue
        for key, status in footer['lle_statuses'].items():
            totals[status] += 1
            if status in ['single_liquid_phase', 'two_liquid_phases']: continue
            unit, solvent, regime = key.split('__')
            c = byunit[unit]
            rows.append(dict(unit=unit, input_inchikey=c['inchikey'], name=c['input']['name'],
                smiles=c['input']['smiles'], cas=c['input'].get('cas', ''), tier=c['tier'],
                product_solvent_key=solvent, temperature_regime=regime,
                temperature_K=temperatures[solvent, regime], status=status,
                evidence_kind='terminal_completion_footer', chunk=chunk))
    assert dict(totals) == evidence['LLE_footer_statuses']
    assert len(rows) == 251
    rows.sort(key=lambda r: (r['input_inchikey'], r['product_solvent_key'], r['temperature_regime']))
    output = args.output / 'unresolved-systems.csv'
    with output.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    affected = {r['input_inchikey'] for r in rows}
    status_by_regime = collections.Counter((r['status'], r['temperature_regime']) for r in rows)
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='footer_inventory_pending_full_payload_audit',
        denominator=373120, unresolved_systems=len(rows), affected_contaminants=len(affected),
        unaffected_contaminants=5830-len(affected),
        statuses=dict(totals),
        status_by_regime=[dict(status=k[0], regime=k[1], count=v) for k,v in sorted(status_by_regime.items())],
        by_solvent=dict(collections.Counter(r['product_solvent_key'] for r in rows).most_common()),
        source_summary_sha256=sha(source/'summary.json'), cohort_sha256=sha(cohort_path),
        validation_sha256=sha(validation_path), csv_sha256=sha(output),
        script_sha256=sha(Path(__file__)),
        limitation='Footer census is execution evidence, not raw-grid validation. Unresolved systems do not supply qualified predictions or automatic screening verdicts.')
    if args.delivery:
        import duckdb
        con = duckdb.connect()
        con.execute('SET threads=1')
        con.execute("SET memory_limit='256MB'")
        con.execute('SET temp_directory=?', [str(args.output/'duckdb-temp')])
        data = args.delivery / 'binary-lle.parquet'
        actual = con.execute('SELECT input_inchikey, product_solvent_key, temperature_regime, temperature_K, status FROM read_parquet(?) WHERE NOT value_validated', [str(data)]).fetchall()
        expected = [(r['input_inchikey'], r['product_solvent_key'], r['temperature_regime'], r['temperature_K'], r['status']) for r in rows]
        assert len(actual) == len(expected) and set(actual) == set(expected)
        counts = dict(con.execute('SELECT status,count(*) FROM read_parquet(?) GROUP BY status', [str(data)]).fetchall())
        assert counts == dict(totals)
        con.close()
        summary.update(status='footer_inventory_matches_final_LLE_table',
                       delivery_LLE_sha256=sha(data), matched_exception_rows=len(actual))
    (args.output/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__': main()
