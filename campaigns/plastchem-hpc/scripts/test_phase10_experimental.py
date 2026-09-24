"""Full-cohort synthetic join control; never writes a real release."""
import gzip
import json
import math
import tempfile
from pathlib import Path

import duckdb

import validate_phase10_experimental as v


def main():
    refs,_=v.references()
    measured={r['input_inchikey']:float(r['measured_logKow']) for r in refs}
    cohort=json.loads((v.B/'phase83-v1/cohort.json').read_text())['rows']
    assert len(cohort)==5830
    correction=math.log10(18.07/158.4)
    with tempfile.TemporaryDirectory(prefix='experimental-control-',dir=v.D) as tmp:
        root=Path(tmp);primary=root/'promotion-v1';extension=root/'promotion-ext39-v1'
        for p in [primary,extension]:
            (p/'provenance').mkdir(parents=True)
            (p/'manifest.json').write_text('{"synthetic_test_only":true}')
        for p,name in [(primary,'phase9-v1'),(extension,'phase10-v1')]:
            (root/name).mkdir()
            (root/name/'delivery-verification.json').write_text(json.dumps(dict(
                status='complete_delivery_verified',manifest_sha256=v.sha(p/'manifest.json'))))
        (primary/'provenance/phase8-manifest.json').write_text(json.dumps(dict(solvents=[dict(
            name='water',B='surfaces/water.orcacosmo',B_volume=18.07,B_legacy_volume=18.07)])))
        (extension/'provenance/manifest.json').write_text(json.dumps(dict(solvents=[dict(
            name='1-octanol',surface_sha256='octanol',cavity_volume_cm3_mol=100)])))
        (extension/'provenance/qualified-physical-volumes-v4.json').write_text(json.dumps(dict(
            entries=[dict(solvent='1-octanol',molar_volume_cm3_mol=158.4)])))
        refpath=extension/'provenance/primary-polymer-reference.json.gz'
        values=[]
        with gzip.open(refpath,'wt') as f:
            for r in cohort:
                key=r['inchikey'];target=2*measured.get(key,0)+1
                gw=(target-correction)*math.log(10)
                values.append((key,gw))
                f.write(json.dumps(dict(inchikey=key,surface_sha256='solute',control_activities={'water':gw}))+'\n')
        (extension/'provenance/primary-polymer-reference-receipt.json').write_text(json.dumps(dict(
            reference_sha256=v.sha(refpath),primary_release_manifest_sha256=v.sha(primary/'manifest.json'))))
        db=duckdb.connect();db.execute('SET threads=1');db.execute("SET memory_limit='128MB'")
        db.execute('CREATE TABLE cohort(input_inchikey VARCHAR,gw DOUBLE)')
        db.executemany('INSERT INTO cohort VALUES (?,?)',values)
        db.execute("CREATE TABLE polymers AS SELECT CASE WHEN range=0 THEN 'pe' ELSE 'polymer-'||range END AS campaign_polymer FROM range(10)")
        db.execute("CREATE TABLE conventions AS SELECT 'normalized' convention,2.0 gp UNION ALL SELECT 'existing',3.0")
        for path,solvent in [(primary,'water'),(extension,'1-octanol')]:
            gamma='gw' if solvent=='water' else 'CAST(0.0 AS DOUBLE)'
            volume='18.07' if solvent=='water' else "CASE WHEN convention='normalized' THEN 158.4 ELSE 100.0 END"
            query=f'''SELECT input_inchikey,campaign_polymer,convention,298.15 temperature_K,
                '{solvent}' product_solvent_key,(gp-({gamma}))/ln(10) logP_x,
                (gp-({gamma}))/ln(10)+log10(200.0/({volume})) logP_concentration,
                {gamma} ln_gamma_solvent,'{'water' if solvent=='water' else 'octanol'}' solvent_surface_sha256,
                'solute' solute_surface_sha256 FROM cohort CROSS JOIN polymers CROSS JOIN conventions'''
            db.execute('COPY ('+query+') TO ? (FORMAT PARQUET)',[str(path/'partition.parquet')])
        db.close()
        old_b,old_d=v.B,v.D;v.B=root;v.D=root/'phase10-v1'
        try:
            out=root/'output';out.mkdir();summary,rows=v.compare_releases(out)
            s=summary['statistics']
            assert len(rows)==1179 and summary['reference_connectivity_blocks']==1143
            assert abs(s['predicted_on_measured_slope']-2)<1e-12 and abs(s['intercept']-1)<1e-12
            assert summary['maximum_cross_polymer_convention_logK_x_difference']<1e-12
            assert summary['maximum_direct_activity_reconstruction_error']<1e-12
            bad=dict(solute_sha='solute',water_sha='wrong',octanol_sha='octanol',gamma_octanol=0,
                     logK_x=1,logK_concentration=1+correction)
            try:v.check_pair(bad,dict(surface_sha256='solute',control_activities={'water':math.log(10)}),'water','octanol',correction)
            except AssertionError:pass
            else:raise AssertionError('Changed water identity accepted')
        finally:v.B,v.D=old_b,old_d
    print('PASS: 116,600 paired synthetic rows, 5,830 direct activity reconstructions, 1,179 references, exact slope 2/intercept 1, wrong surface rejected.')


if __name__=='__main__':main()
