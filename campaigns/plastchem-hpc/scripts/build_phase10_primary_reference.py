"""Extract validated original polymer activities for independent extension checks.

Only after priority-one release verification. This reads its exact frozen
collection snapshot, never a changing preview or an extension worker result.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path

from audit_phase9_results import records, sha

D=Path('/mnt/r/plastchem-euler/phase10-v1')
A=D.parent/'phase9-v1'
P=D.parent/'promotion-v1'


def extract(rows,unit,polymers):
    assert len(rows)==640
    coefficients={};solvents={};keys=set()
    for r in rows:
        assert r['unit']==unit and r['status']=='predicted' and r['temperature_K']==298.15
        key=(r['polymer'],r['convention']);triple=(*key,r['solvent'])
        assert triple not in keys;keys.add(triple)
        assert key[0] in polymers and key[1] in ['normalized','existing']
        value=(r['ln_gamma_polymer'],r['polymer_volume_cm3_mol'])
        assert all(math.isfinite(v) for v in value) and value[1]>0
        assert key not in coefficients or coefficients[key]==value
        assert r['solvent'] not in solvents or solvents[r['solvent']]==r['ln_gamma_solvent']
        coefficients[key]=value;solvents[r['solvent']]=r['ln_gamma_solvent']
        x=(value[0]-r['ln_gamma_solvent'])/math.log(10)
        assert abs(x-r['logP_x'])<=1e-12
        assert abs(x+math.log10(value[1]/r['solvent_volume_cm3_mol'])-r['logP_concentration'])<=1e-12
    assert set(coefficients)=={(p,c) for p in polymers for c in ['normalized','existing']}
    assert len(solvents)==32
    assert keys=={(*k,s) for k in coefficients for s in solvents}
    return dict(coefficients=[dict(polymer=p,convention=c,ln_gamma_polymer=v[0],polymer_volume_cm3_mol=v[1])
                              for (p,c),v in sorted(coefficients.items())],
                control_activities={s:solvents[s] for s in ['water','hexane']})


def main():
    target=D/'primary-polymer-reference.json.gz'
    assert not target.exists(),'Preserve existing audited reference'
    verification=json.loads((A/'delivery-verification.json').read_text())
    assert verification['status']=='complete_delivery_verified'
    assert verification['manifest_sha256']==sha(P/'manifest.json')
    audit=json.loads((P/'provenance/results-audit.json').read_text())
    assert audit['status']=='complete' and audit['fully_evaluated_molecules']==5830
    registry=json.loads((A/'collection.json').read_text())
    snapshot=hashlib.sha256(json.dumps(registry,sort_keys=True).encode()).hexdigest()
    assert snapshot==audit['collection_snapshot_sha256']
    manifest=json.loads((D/'manifest.json').read_text())
    cohort=json.loads((D.parent/'phase83-v1/cohort.json').read_text())['rows']
    units={f"cohort-{c['index']:05d}":c for c in cohort};seen=set()
    temp=target.with_suffix('.tmp')
    with gzip.open(temp,'wt',compresslevel=3) as stream:
        for path,rows in records(registry):
            if '/partition/' not in path:continue
            unit=rows[0]['unit'];assert unit not in seen;seen.add(unit)
            c=units[unit]
            assert all(r['inchikey']==c['inchikey'] and r['solute_surface_sha256']==c['surface_sha256'] for r in rows)
            reference=extract(rows,unit,manifest['polymers'])
            reference.update(unit=unit,inchikey=c['inchikey'],surface_sha256=c['surface_sha256'],
                primary_path=path,primary_sha256=registry['files'][path]['sha256'])
            stream.write(json.dumps(reference,separators=(',',':'))+'\n')
    assert seen==set(units) and len(seen)==5830
    temp.replace(target)
    receipt=dict(status='complete_original_coefficients_verified',units=len(seen),
        primary_release_manifest_sha256=sha(P/'manifest.json'),primary_audit_sha256=sha(P/'provenance/results-audit.json'),
        collection_snapshot_sha256=snapshot,reference_path=str(target),reference_sha256=sha(target),
        extractor_sha256=sha(Path(__file__)),scope='Independent extraction from original validated raw partition checkpoints, not extension reuse outputs.')
    (D/'primary-polymer-reference-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(receipt))


if __name__=='__main__':main()
