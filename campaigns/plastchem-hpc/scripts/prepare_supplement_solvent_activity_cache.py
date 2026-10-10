"""Recover frozen x=0 solvent activities for supplementary polymer work.

No COSMO solve. The 32-panel values are reconstructed from original ensemble
activities and logP_x; the 39 additions already export ln_gamma_solvent. Every
value must agree across ten polymers and both conventions before it is cached.
"""
import argparse
import csv
import datetime
import gzip
import hashlib
import json
import math
from pathlib import Path
import resource

import duckdb

R = Path(__file__).resolve().parents[1]
B = Path('/mnt/r/plastchem-euler')


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', required=True, type=Path)
    out = ap.parse_args().output.resolve()
    assert out.is_relative_to(B / 'coverage-plans') and not out.exists()
    sources = {}
    for release, phase in [('promotion-v1', 'phase9-v1'),
                           ('promotion-ext39-v1', 'phase10-v1')]:
        mpath = B / release / 'manifest.json'
        manifest = json.loads(mpath.read_text())
        proof = json.loads((B / phase / 'delivery-verification.json').read_text())
        assert manifest['status'] == 'complete' and sha(mpath) == proof['manifest_sha256']
        assert proof['status'] == 'complete_delivery_verified'
        p = B / release / 'partition.parquet'
        assert sha(p) == manifest['files'][p.name]['sha256']
        sources[str(mpath)] = sha(mpath)
        sources[str(p)] = sha(p)
    receipt_path = B / 'phase10-v1/primary-polymer-reference-receipt.json'
    receipt = json.loads(receipt_path.read_text())
    assert receipt['status'] == 'complete_original_coefficients_verified' and receipt['units'] == 5830
    reference = Path(receipt['reference_path'])
    assert sha(reference) == receipt['reference_sha256']
    assert receipt['primary_release_manifest_sha256'] == sources[str(B / 'promotion-v1/manifest.json')]
    sources[str(reference)] = sha(reference)
    sources[str(receipt_path)] = sha(receipt_path)
    out.mkdir(parents=True)
    refs = out / 'original-ensemble-coefficients.csv'
    controls = out / 'original-solvent-controls.csv'
    keys = set()
    with refs.open('w', newline='') as rf, controls.open('w', newline='') as cf, gzip.open(reference, 'rt') as src:
        rw = csv.writer(rf)
        cw = csv.writer(cf)
        rw.writerow(['input_inchikey', 'campaign_polymer', 'convention', 'ln_gamma_polymer', 'solute_surface_sha256'])
        cw.writerow(['input_inchikey', 'product_solvent_key', 'ln_gamma_solvent'])
        for line in src:
            r = json.loads(line)
            assert r['inchikey'] not in keys and len(r['coefficients']) == 20
            keys.add(r['inchikey'])
            seen = set()
            for c in r['coefficients']:
                identity = (c['polymer'], c['convention'])
                assert identity not in seen and math.isfinite(c['ln_gamma_polymer'])
                seen.add(identity)
                rw.writerow([r['inchikey'], *identity, c['ln_gamma_polymer'], r['surface_sha256']])
            assert set(r['control_activities']) == {'water', 'hexane'}
            for solvent, value in r['control_activities'].items():
                cw.writerow([r['inchikey'], solvent, value])
    assert len(keys) == 5830
    con = duckdb.connect(':memory:')
    con.execute("SET threads=1")
    con.execute("SET memory_limit='384MB'")
    temp = str(out / 'spill').replace("'", "''")
    con.execute("SET temp_directory='" + temp + "'")
    con.read_csv(str(refs), header=True).create_view('refs')
    con.read_csv(str(controls), header=True).create_view('controls')
    con.read_parquet(str(B / 'promotion-v1/partition.parquet')).create_view('primary_rows')
    con.read_parquet(str(B / 'promotion-ext39-v1/partition.parquet')).create_view('extra_rows')
    con.execute('''CREATE TEMP VIEW reconstructed AS
        SELECT p.input_inchikey,p.product_solvent_key,p.campaign_polymer,p.convention,
               p.solute_surface_sha256,p.solvent_surface_sha256,
               r.ln_gamma_polymer-ln(10.0)*p.logP_x AS value
        FROM primary_rows p JOIN refs r
        USING(input_inchikey,campaign_polymer,convention,solute_surface_sha256)
        WHERE p.status='predicted' AND p.temperature_K=298.15 AND p.solute_mole_fraction=0''')
    assert con.execute('SELECT count(*) FROM reconstructed').fetchone()[0] == 3731200
    con.execute('''CREATE TEMP TABLE cache AS
        SELECT input_inchikey,product_solvent_key,solute_surface_sha256,solvent_surface_sha256,
               max(CASE WHEN campaign_polymer='pe' AND convention='normalized' THEN value END) AS ln_gamma_solvent,
               max(value)-min(value) AS cross_polymer_convention_spread,
               count(*) AS comparison_count,
               'reconstructed_from_original_polymer_activity_and_logP_x' AS construction
        FROM reconstructed GROUP BY ALL''')
    con.execute('''INSERT INTO cache
        SELECT input_inchikey,product_solvent_key,solute_surface_sha256,solvent_surface_sha256,
               max(CASE WHEN campaign_polymer='pe' AND convention='normalized' THEN ln_gamma_solvent END),
               max(ln_gamma_solvent)-min(ln_gamma_solvent),count(*),
               'exported_original_solvent_activity'
        FROM extra_rows WHERE status='predicted' AND temperature_K=298.15 AND solute_mole_fraction=0
        GROUP BY input_inchikey,product_solvent_key,solute_surface_sha256,solvent_surface_sha256''')
    n, maxspread, invalid = con.execute('''SELECT count(*),max(cross_polymer_convention_spread),
        count(*) FILTER(WHERE comparison_count<>20 OR ln_gamma_solvent IS NULL OR
          NOT isfinite(ln_gamma_solvent) OR cross_polymer_convention_spread>1e-9)
        FROM cache''').fetchone()
    assert n == 5830 * 71 and invalid == 0
    assert con.execute('''SELECT count(*) FROM (
        SELECT input_inchikey,product_solvent_key,count(*) n FROM cache GROUP BY ALL HAVING n<>1)''').fetchone()[0] == 0
    assert con.execute('''SELECT count(*) FROM (
        SELECT input_inchikey,count(*) n FROM cache GROUP BY ALL HAVING n<>71)''').fetchone()[0] == 0
    ncontrols, control_error = con.execute('''SELECT count(*),max(abs(c.ln_gamma_solvent-r.ln_gamma_solvent))
        FROM cache c JOIN controls r USING(input_inchikey,product_solvent_key)''').fetchone()
    assert ncontrols == 11660 and control_error < 1e-9
    target = out / 'solvent-activities.parquet'
    con.sql('SELECT * FROM cache ORDER BY input_inchikey,product_solvent_key').write_parquet(str(target), compression='zstd')
    con.read_parquet(str(target)).create_view('written')
    assert con.execute('SELECT count(*) FROM written').fetchone()[0] == n
    assert con.execute('SELECT count(*) FROM ((SELECT * FROM cache EXCEPT SELECT * FROM written) UNION ALL (SELECT * FROM written EXCEPT SELECT * FROM cache))').fetchone()[0] == 0
    con.close()
    summary = dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='frozen_solvent_activity_cache_verified_no_new_solve',
        contaminants=5830, solvents=71, rows=n, temperature_K=298.15, solute_mole_fraction=0,
        comparison_values=8278600, max_cross_polymer_convention_spread=maxspread,
        independent_raw_water_hexane_controls=ncontrols, max_control_error=control_error,
        selected_value='PE normalized row; no fitting, mean adjustment or recalibration',
        numerical_tolerance=1e-9,
        source_sha256=sources, script_sha256=sha(Path(__file__)),
        peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        limitations='Arithmetic/provenance cache only, not a new COSMOspace solve or experimental validation. Reuse only for the same solute and phase surfaces, temperature, pure reference state, parameterization and package pins. No later contaminant or new polymer activity is supplied here.')
    (out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    (out / Path(__file__).name).write_bytes(Path(__file__).read_bytes())
    files = {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}
    (out / 'manifest.json').write_text(json.dumps(dict(status=summary['status'], files=files), indent=2) + '\n')
    assert all(sha(out / name) == digest for name, digest in files.items())
    print(json.dumps(dict(summary=summary, manifest_sha256=sha(out/'manifest.json'))))


if __name__ == '__main__':
    main()
