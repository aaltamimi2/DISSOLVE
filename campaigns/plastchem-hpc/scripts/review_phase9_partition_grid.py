"""Read-only numerical review of the complete partition grid, independent of LLE.

Checks thermodynamic cycles and solute-independent volume shifts, and describes
the predictions without treating extremes or convention choices as errors.
"""
import argparse
import csv
import datetime
import gzip
import hashlib
import json
import resource
import tempfile
from pathlib import Path

import duckdb


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def csv_query(con, query, path):
    result = con.execute(query)
    names = [d[0] for d in result.description]
    rows = result.fetchall()
    with path.open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(names)
        writer.writerows(rows)
    return [dict(zip(names, row)) for row in rows]


def cycles(con, view):
    # For each solute and convention, a rectangle around the PE/water reference
    # must sum to zero on either basis. Neither reference is experimental truth.
    return con.execute(f"""
        SELECT count(*),
          max(abs(p.logP_x-w.logP_x-e.logP_x+b.logP_x)),
          max(abs(p.logP_concentration-w.logP_concentration
                  -e.logP_concentration+b.logP_concentration))
        FROM {view} p
        JOIN {view} w ON p.input_inchikey=w.input_inchikey
          AND p.convention=w.convention AND p.campaign_polymer=w.campaign_polymer
          AND w.product_solvent_key='water'
        JOIN {view} e ON p.input_inchikey=e.input_inchikey
          AND p.convention=e.convention AND p.product_solvent_key=e.product_solvent_key
          AND e.campaign_polymer='pe'
        JOIN {view} b ON p.input_inchikey=b.input_inchikey
          AND p.convention=b.convention AND b.campaign_polymer='pe'
          AND b.product_solvent_key='water'
        WHERE p.campaign_polymer<>'pe' AND p.product_solvent_key<>'water'
    """).fetchone()


def main(root, out):
    out.mkdir(parents=True, exist_ok=False)
    manifest = json.loads((root / 'manifest.json').read_text())
    for name in ['partition.parquet', 'contaminants.csv.gz']:
        assert sha(root / name) == manifest['files'][name]['sha256'], name
    with tempfile.TemporaryDirectory(prefix='grid-review-', dir=out) as temp:
        con = duckdb.connect()
        con.execute('SET threads=1')
        con.execute("SET memory_limit='512MB'")
        con.execute('SET temp_directory=?', [temp])
        con.read_parquet(str(root / 'partition.parquet')).create_view('p')
        n, keys, bad = con.execute("""SELECT count(*), count(DISTINCT input_inchikey),
          count(*) FILTER (WHERE logP_x IS NULL OR logP_concentration IS NULL
            OR NOT isfinite(logP_x) OR NOT isfinite(logP_concentration)
            OR temperature_K<>298.15 OR solute_mole_fraction<>0 OR status<>'predicted')
          FROM p""").fetchone()
        assert (n, keys, bad) == (3731200, 5830, 0), (n, keys, bad)
        cycle = cycles(con, 'p')
        assert cycle[0] == 5830 * 2 * 9 * 31
        assert max(cycle[1:]) < 1e-9, cycle
        shifts = csv_query(con, """SELECT convention, campaign_polymer, product_solvent_key,
          count(*) AS n, min(logP_concentration-logP_x) AS minimum_volume_shift,
          max(logP_concentration-logP_x) AS maximum_volume_shift,
          max(logP_concentration-logP_x)-min(logP_concentration-logP_x) AS shift_spread
          FROM p GROUP BY ALL ORDER BY 1,2,3""", out / 'volume-shifts.csv')
        assert len(shifts) == 640 and all(r['n'] == 5830 for r in shifts)
        assert max(r['shift_spread'] for r in shifts) < 1e-9
        distributions = csv_query(con, """SELECT convention, count(*) AS n,
          min(logP_x) AS minimum_x, quantile_cont(logP_x,0.01) AS p01_x,
          median(logP_x) AS median_x, quantile_cont(logP_x,0.99) AS p99_x,
          max(logP_x) AS maximum_x,
          min(logP_concentration) AS minimum_concentration,
          quantile_cont(logP_concentration,0.01) AS p01_concentration,
          median(logP_concentration) AS median_concentration,
          quantile_cont(logP_concentration,0.99) AS p99_concentration,
          max(logP_concentration) AS maximum_concentration
          FROM p GROUP BY convention ORDER BY convention""", out / 'distributions.csv')
        csv_query(con, """SELECT convention, campaign_polymer, product_solvent_key,
          count(*) AS n, min(logP_concentration) AS minimum,
          quantile_cont(logP_concentration,0.01) AS p01,
          median(logP_concentration) AS median,
          quantile_cont(logP_concentration,0.99) AS p99,
          max(logP_concentration) AS maximum
          FROM p GROUP BY ALL ORDER BY 1,2,3""", out / 'phase-pair-distributions.csv')
        with gzip.open(root / 'contaminants.csv.gz', 'rt') as f:
            names = {r['input_inchikey']: r['name'] for r in csv.DictReader(f)}
        extremes = []
        for convention in ['normalized', 'existing']:
            for field in ['logP_x', 'logP_concentration']:
                for direction in ['ASC', 'DESC']:
                    for key, polymer, solvent, value in con.execute(f"""
                      SELECT input_inchikey,campaign_polymer,product_solvent_key,{field}
                      FROM p WHERE convention=? ORDER BY {field} {direction},1,2,3 LIMIT 5
                    """, [convention]).fetchall():
                        extremes.append(dict(convention=convention, basis=field,
                          end='minimum' if direction == 'ASC' else 'maximum',
                          input_inchikey=key, name=names[key], polymer=polymer,
                          solvent=solvent, value=value))
        with (out / 'named-extremes.csv').open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(extremes[0]))
            w.writeheader(); w.writerows(extremes)
        comparison = csv_query(con, """SELECT count(*) AS paired_rows,
          min(e.logP_concentration-n.logP_concentration) AS minimum_existing_minus_normalized,
          median(e.logP_concentration-n.logP_concentration) AS median_existing_minus_normalized,
          max(e.logP_concentration-n.logP_concentration) AS maximum_existing_minus_normalized,
          count(*) FILTER (WHERE sign(e.logP_concentration)<>sign(n.logP_concentration))
            AS concentration_sign_changes
          FROM p n JOIN p e USING (input_inchikey,campaign_polymer,product_solvent_key,temperature_K)
          WHERE n.convention='normalized' AND e.convention='existing'
        """, out / 'convention-comparison.csv')[0]
        assert comparison['paired_rows'] == 5830 * 320
        # A deliberately altered interior cell must break the cycle. The source
        # remains unchanged; only this temporary query view contains corruption.
        first = con.execute('SELECT min(input_inchikey) FROM p').fetchone()[0]
        con.execute("""CREATE TEMP VIEW perturbed AS SELECT * REPLACE
          (logP_x + CASE WHEN input_inchikey='%s' AND convention='normalized'
             AND campaign_polymer='nylon6' AND product_solvent_key='benzene'
             THEN 0.01 ELSE 0 END AS logP_x) FROM p""" % first)
        altered = cycles(con, 'perturbed')
        assert 0.009999 < altered[1] < 0.010001 and altered[2] < 1e-9, altered
        con.close()
    result = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
      status='full_partition_numerical_review_passed', partition_rows=n, contaminants=keys,
      source_path=str(root), source_manifest_sha256=sha(root / 'manifest.json'),
      partition_sha256=sha(root / 'partition.parquet'), script_sha256=sha(Path(__file__)),
      nontrivial_rectangles_per_basis=cycle[0], max_cycle_error_x=cycle[1],
      max_cycle_error_concentration=cycle[2],
      max_solute_dependent_volume_shift_spread=max(r['shift_spread'] for r in shifts),
      negative_control_detected_error_x=altered[1], distributions=distributions,
      convention_comparison=comparison, peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
      scope='Complete partition grid only. Cycle/arithmetic consistency and descriptive ranges are not experimental accuracy, independent COSMOspace reproduction, or full LLE completion. No cutoff, recalibration or convention selection.')
    result['files'] = {p.name: sha(p) for p in sorted(out.glob('*.csv'))}
    (out / 'summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    main(args.root, args.output)
