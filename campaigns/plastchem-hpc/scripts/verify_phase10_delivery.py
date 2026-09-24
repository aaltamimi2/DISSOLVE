"""Independent read-only checks of the final extension Parquet delivery."""
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
from verify_phase9_delivery import verify_qualified_ranges

B=Path('/mnt/r/plastchem-euler')


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1048576),b''):h.update(b)
    return h.hexdigest()


def verify_partition_arithmetic(con):
    bad=con.execute('''SELECT count(*) FROM partition_rows p
        LEFT JOIN original o ON p.input_inchikey=o.ik AND p.campaign_polymer=o.polymer AND p.convention=o.convention
        LEFT JOIN volumes v ON p.product_solvent_key=v.solvent
        WHERE o.ik IS NULL OR v.solvent IS NULL
        OR p.primary_partition_sha256 IS DISTINCT FROM o.source_sha
        OR p.ln_gamma_polymer IS NULL OR p.ln_gamma_solvent IS NULL
        OR p.polymer_volume_cm3_mol IS NULL OR p.solvent_volume_cm3_mol IS NULL
        OR p.logP_x IS NULL OR p.logP_concentration IS NULL
        OR NOT isfinite(p.logP_x) OR NOT isfinite(p.logP_concentration)
        OR NOT isfinite(p.ln_gamma_polymer) OR NOT isfinite(p.ln_gamma_solvent)
        OR NOT isfinite(p.polymer_volume_cm3_mol) OR NOT isfinite(p.solvent_volume_cm3_mol)
        OR abs(p.ln_gamma_polymer-o.gp)>1e-12 OR abs(p.polymer_volume_cm3_mol-o.vp)>1e-12
        OR p.polymer_volume_cm3_mol<=0 OR p.solvent_volume_cm3_mol<=0
        OR abs(p.solvent_volume_cm3_mol-CASE WHEN p.convention='normalized' THEN v.physical ELSE v.cavity END)>1e-12
        OR abs(p.logP_x-(p.ln_gamma_polymer-p.ln_gamma_solvent)/ln(10))>1e-10
        OR abs(p.logP_concentration-p.logP_x-log10(p.polymer_volume_cm3_mol/p.solvent_volume_cm3_mol))>1e-10''').fetchone()[0]
    assert bad==0,('Independent partition arithmetic / original coefficient / volume reference',bad)
    # Every polymer/convention must reuse the same independently solved solvent
    # activity for a contaminant; prevents mutually compensating row alterations.
    assert con.execute('''SELECT count(*) FROM (SELECT input_inchikey,product_solvent_key,
        max(ln_gamma_solvent)-min(ln_gamma_solvent) spread FROM partition_rows GROUP BY ALL HAVING spread>1e-12)''').fetchone()[0]==0


def verify(root=None):
    root=root or B/'promotion-ext39-v1';primary=B/'promotion-v1';provenance=root/'provenance'
    manifest=json.loads((root/'manifest.json').read_text());summary=json.loads((root/'summary.json').read_text())
    assert manifest['status']==summary['status']=='complete'
    files=set()
    for p in root.rglob('*'):
        assert not p.is_symlink()
        if p.is_file() and p!=root/'manifest.json':files.add(str(p.relative_to(root)))
    assert files==set(manifest['files'])
    for name,pin in manifest['files'].items():
        p=Path(name);assert not p.is_absolute() and '..' not in p.parts
        assert (root/p).stat().st_size==pin['bytes'] and sha(root/p)==pin['sha256'],name
    assert sum(v['bytes'] for v in manifest['files'].values())==manifest['payload_bytes']<200_000_000
    original_manifest=json.loads((primary/'manifest.json').read_text());assert original_manifest['status']=='complete'
    pv=json.loads((provenance/'primary-delivery-verification.json').read_text())
    assert pv['status']=='complete_delivery_verified' and pv['manifest_sha256']==sha(primary/'manifest.json')
    assert sha(provenance/'primary-manifest.json')==sha(primary/'manifest.json')==summary['primary_release_manifest_sha256']
    for name in ['contaminants.csv.gz','polymer-product-map.csv']:
        assert sha(root/name)==sha(primary/name)==original_manifest['files'][name]['sha256']
    model=json.loads((provenance/'manifest.json').read_text());cohort=json.loads((provenance/'cohort.json').read_text())['rows']
    assert len(cohort)==5830 and model['cohort_sha256']==sha(provenance/'cohort.json')==summary['cohort_sha256']==manifest['cohort_sha256']
    volumes_path=provenance/'qualified-physical-volumes-v4.json';volume_pin=sha(volumes_path)
    assert volume_pin==summary['volume_references_sha256']=='839d30e199fc0f1504506d1a44923d918dcafab8242a3530297f15db641d71fe'
    refs=json.loads(volumes_path.read_text());assert refs['qualified']==39 and not refs['missing']
    refs={r['solvent']:r for r in refs['entries']};solvents={s['name']:s for s in model['solvents']}
    assert len(solvents)==39 and set(solvents)==set(refs)
    original_model=json.loads((provenance/'phase8-manifest.json').read_text())
    assert model['primary_manifest_sha256']==sha(provenance/'phase8-manifest.json')
    assert not set(solvents)&{s['name'] for s in original_model['solvents']}
    assert set(model['polymers'])==set(original_model['polymers']) and len(model['polymers'])==10
    receipt=json.loads((provenance/'primary-polymer-reference-receipt.json').read_text())
    assert receipt['primary_release_manifest_sha256']==sha(primary/'manifest.json')
    assert receipt['reference_sha256']==sha(provenance/'primary-polymer-reference.json.gz')
    gate=json.loads((provenance/'calibration-clearance.json').read_text());assert gate['status']=='passed'
    assert gate['projected_CPU_h']<=gate['limit_CPU_h']==510
    assert gate['calibration_molecules']==40 and gate['LLE_systems']==1560
    assert gate['exact_batch_control_comparisons']==80 and gate['max_control_difference']<=1e-9
    assert gate['measured_observation_sha256']==sha(provenance/'calibration-v2-observation.json')
    assert gate['finalizer_sha256']==sha(provenance/'code/finalize_phase10_calibration.py')
    for name,pin in gate['file_pins'].items():
        if (provenance/name).exists():actual=sha(provenance/name)
        elif (provenance/(name+'.gz')).exists():
            with gzip.open(provenance/(name+'.gz'),'rb') as f:actual=hashlib.sha256(f.read()).hexdigest()
        else:actual=sha(provenance/'code'/name)
        assert actual==pin,('Gate input changed in delivery',name)
    audit=json.loads((provenance/'results-audit.json').read_text());assert audit['status']=='complete'
    assert audit['fully_evaluated_molecules']==5830 and audit['counts']['partition_rows_checked']==4547400
    assert audit['counts']['LLE_systems_checked']==227370 and audit['counts']['primary_exact_zero_controls']==11660
    assert audit['maximum_control_error']<=1e-9
    with tempfile.TemporaryDirectory(dir=B/'phase10-v1',prefix='delivery-verify-') as tmp:
        con=duckdb.connect();con.execute('SET threads=1');con.execute("SET memory_limit='256MB'")
        con.execute('SET temp_directory=?',[tmp])
        con.execute('CREATE VIEW partition_rows AS SELECT * FROM read_parquet(?)',[str(root/'partition.parquet')])
        con.execute('CREATE VIEW lle_rows AS SELECT * FROM read_parquet(?)',[str(root/'binary-lle.parquet')])
        assert con.execute('SELECT count(*) FROM partition_rows').fetchone()[0]==4547400==summary['partition_rows']
        assert con.execute('SELECT count(*) FROM lle_rows').fetchone()[0]==227370==summary['LLE_rows']
        con.execute('CREATE TABLE cohort(ik VARCHAR,mw DOUBLE,source_sha VARCHAR)')
        con.executemany('INSERT INTO cohort VALUES (?,?,?)',[(r['inchikey'],r['molecular_weight_g_mol'],r['surface_sha256']) for r in cohort])
        con.execute('CREATE TABLE polymers(name VARCHAR)');con.executemany('INSERT INTO polymers VALUES (?)',[(p,) for p in model['polymers']])
        con.execute('CREATE TABLE volumes(solvent VARCHAR,physical DOUBLE,cavity DOUBLE,mw DOUBLE,source_sha VARCHAR,qualification VARCHAR)')
        con.executemany('INSERT INTO volumes VALUES (?,?,?,?,?,?)',[(s,refs[s]['molar_volume_cm3_mol'],v['cavity_volume_cm3_mol'],v['molecular_weight_g_mol'],v['surface_sha256'],refs[s]['qualification']) for s,v in solvents.items()])
        refcsv=Path(tmp)/'original.csv'
        with refcsv.open('w',newline='') as out,gzip.open(provenance/'primary-polymer-reference.json.gz','rt') as src:
            w=csv.writer(out);w.writerow(['ik','polymer','convention','gp','vp','source_sha']);seen=set()
            for r in map(json.loads,src):
                assert r['inchikey'] not in seen;seen.add(r['inchikey'])
                for c in r['coefficients']:w.writerow([r['inchikey'],c['polymer'],c['convention'],c['ln_gamma_polymer'],c['polymer_volume_cm3_mol'],r['primary_sha256']])
            assert seen=={r['inchikey'] for r in cohort}
        con.execute('CREATE TABLE original AS SELECT * FROM read_csv_auto(?)',[str(refcsv)])
        assert con.execute('SELECT count(*) FROM original').fetchone()[0]==116600
        verify_partition_arithmetic(con)
        for table,n,keys in [('partition_rows',780,'input_inchikey,product_solvent_key,campaign_polymer,temperature_K,convention'),('lle_rows',39,'input_inchikey,product_solvent_key,temperature_K')]:
            assert con.execute(f'SELECT count(*) FROM (SELECT {keys},count(*) n FROM {table} GROUP BY ALL HAVING n<>1)').fetchone()[0]==0
            assert con.execute(f'SELECT count(*) FROM (SELECT input_inchikey,count(*) n FROM {table} GROUP BY ALL HAVING n<>?)',[n]).fetchone()[0]==0
            assert con.execute(f'SELECT count(*) FROM {table} t LEFT JOIN cohort c ON t.input_inchikey=c.ik LEFT JOIN volumes v ON t.product_solvent_key=v.solvent WHERE c.ik IS NULL OR v.solvent IS NULL OR t.temperature_K IS DISTINCT FROM 298.15 OR t.solute_surface_sha256 IS DISTINCT FROM c.source_sha OR t.solvent_surface_sha256 IS DISTINCT FROM v.source_sha').fetchone()[0]==0
        assert con.execute("SELECT count(*) FROM partition_rows WHERE convention NOT IN ('normalized','existing') OR status IS DISTINCT FROM 'predicted' OR solute_mole_fraction IS DISTINCT FROM 0 OR reference_state IS DISTINCT FROM 'pure_component' OR parameterization IS DISTINCT FROM 'openCOSMO-RS 24a'").fetchone()[0]==0
        assert con.execute('SELECT count(*) FROM partition_rows p LEFT JOIN polymers x ON p.campaign_polymer=x.name WHERE x.name IS NULL').fetchone()[0]==0
        assert con.execute("SELECT count(*) FROM partition_rows p JOIN volumes v ON p.product_solvent_key=v.solvent WHERE (convention='normalized' AND (volume_reference_sha256 IS DISTINCT FROM ? OR volume_reference_qualification IS DISTINCT FROM v.qualification)) OR (convention='existing' AND volume_reference_sha256 IS DISTINCT FROM v.source_sha)",[volume_pin]).fetchone()[0]==0
        assert con.execute("SELECT count(*) FROM lle_rows WHERE temperature_regime IS DISTINCT FROM 'RT' OR value_validated IS DISTINCT FROM (status IN ('single_liquid_phase','two_liquid_phases')) OR (NOT value_validated AND (above_15_wt_percent IS NOT NULL OR above_15_mol_percent IS NOT NULL))").fetchone()[0]==0
        range_roundoff=verify_qualified_ranges(con)
        assert con.execute('''SELECT count(*) FROM lle_rows l JOIN cohort c ON l.input_inchikey=c.ik JOIN volumes v ON l.product_solvent_key=v.solvent
            WHERE value_validated AND abs(solute_wt_percent_solubility-100*solute_mole_fraction_solubility*c.mw/(solute_mole_fraction_solubility*c.mw+(1-solute_mole_fraction_solubility)*v.mw))>1e-9''').fetchone()[0]==0
        for basis,number in [('wt','solute_wt_percent_solubility'),('mol','100*solute_mole_fraction_solubility')]:
            assert con.execute(f'''SELECT count(*) FROM lle_rows WHERE value_validated AND above_15_{basis}_percent IS DISTINCT FROM (CASE WHEN abs({number}-15)<=0.01 THEN NULL ELSE {number}>15 END)''').fetchone()[0]==0
        numerical=['solute_mole_fraction_solubility','solute_wt_percent_solubility','x_contaminant_solvent_rich','x_solvent_solvent_rich','x_contaminant_solute_rich','x_solvent_solute_rich','wt_percent_contaminant_solvent_rich','wt_percent_contaminant_solute_rich','grid_change_mol_percentage_points','grid_change_wt_percentage_points','max_chemical_potential_residual_RT','minimum_tangent_distance_RT']
        assert con.execute("SELECT count(*) FROM lle_rows WHERE status='activity_nonconvergence' AND ("+' OR '.join(f+' IS NOT NULL' for f in numerical)+')').fetchone()[0]==0
        failure_count=0;handler=sha(provenance/'code/phase9_failure_policy.py')
        for ties,grids,encoded in con.execute("SELECT tie_lines_json,grid_checks_json,failure_evidence_json FROM lle_rows WHERE status='activity_nonconvergence'").fetchall():
            e=json.loads(encoded);assert json.loads(ties)==json.loads(grids)==[]
            assert e['failure_policy_sha256']==handler and e['exception_type']=='ValueError'
            assert e['exception_message']=='COSMOspace did not converge for binary grid'
            assert 'ValueError: '+e['exception_message'] in e['traceback'];failure_count+=1
        actual=dict(con.execute('SELECT status,count(*) FROM lle_rows GROUP BY status').fetchall());assert actual==audit['LLE_statuses']
        assert all(summary['counts']['lle_'+k]==v for k,v in actual.items())
        con.execute('CREATE VIEW quality AS SELECT * FROM read_csv_auto(?)',[str(root/'extension-quality.csv')])
        assert con.execute('SELECT count(*),count(DISTINCT input_inchikey) FROM quality').fetchone()==(5830,5830)
        assert con.execute('''SELECT count(*) FROM quality q FULL JOIN (SELECT input_inchikey,count(*) total,sum(CASE WHEN value_validated THEN 1 ELSE 0 END) good FROM lle_rows GROUP BY input_inchikey) l USING(input_inchikey)
            WHERE q.input_inchikey IS NULL OR l.input_inchikey IS NULL OR q.partition_predicted_rows<>780 OR q.lle_qualified_rows<>l.good OR q.lle_unresolved_rows<>39-l.good OR q.all_requested_quantities_evaluated IS DISTINCT FROM true OR q.all_requested_quantities_qualified IS DISTINCT FROM (l.good=39)''').fetchone()[0]==0
        con.close()
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='complete_delivery_verified',path=str(root),
        manifest_sha256=sha(root/'manifest.json'),partition_rows=4547400,LLE_rows=227370,frozen_contaminants=5830,
        fully_qualified_contaminants=summary['fully_qualified_contaminants'],LLE_statuses=actual,
        unresolved_activity_rows_with_null_numbers=failure_count,qualified_upper_endpoint_roundoff_rows=range_roundoff,
        verifier_sha256=sha(Path(__file__)),scope='Independent payload digests, unchanged primary cohort/map, exact coverage/unique keys, original coefficients, both volume/sign conventions, status/threshold/mass arithmetic and failure-null preservation. No independent experimental-accuracy claim.')
    (B/'phase10-v1/delivery-verification.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


if __name__=='__main__':print(json.dumps(verify(),indent=2))
