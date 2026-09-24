"""Read-only A-10 identity/surface union audit; does not claim prediction completion."""
import csv
import datetime
import hashlib
import json
from pathlib import Path

B=Path('/mnt/r/plastchem-euler')

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    primary=B/'promotion-v1'
    extension=B/'phase10-v1'
    library=B/'phase9-solvent-library-v1'
    sources={
        'primary_release':primary/'manifest.json',
        'primary_model':primary/'provenance/phase8-manifest.json',
        'extension_model':extension/'manifest.json',
        'common_inventory':library/'common-name-inventory.json',
        'overlap':library/'common-panel-connectivity-overlap.csv',
    }
    # The primary provenance filename is explicit, never guessed from another run.
    if not sources['primary_model'].exists():
        sources['primary_model']=primary/'provenance/manifest.json'
    sealed=json.loads(sources['primary_release'].read_text())
    receipt=json.loads((B/'phase9-v1/delivery-verification.json').read_text())
    assert receipt['status']=='complete_delivery_verified'
    assert receipt['manifest_sha256']==sha(sources['primary_release'])
    rel=str(sources['primary_model'].relative_to(primary))
    assert sealed['files'][rel]['sha256']==sha(sources['primary_model'])
    old=json.loads(sources['primary_model'].read_text())
    new=json.loads(sources['extension_model'].read_text())
    assert new['primary_manifest_sha256']==sha(sources['primary_model'])
    old={s['name']:s for s in old['solvents']}
    new={s['name']:s for s in new['solvents']}
    common=[r['solvent_key'].lower() for r in json.loads(sources['common_inventory'].read_text())['rows']]
    overlap=list(csv.DictReader(sources['overlap'].open()))
    assert len(common)==len(set(common))==69
    assert len(old)==32 and len(new)==39 and not set(old)&set(new)
    assert len(overlap)==30
    aliases={r['panel_solvent_key']:r for r in overlap}
    common_old={r['common_solvent_key'] for r in overlap}
    assert len(aliases)==len(common_old)==30
    assert set(common)==common_old|set(new) and not common_old&set(new)
    assert set(aliases)<=set(old)
    assert set(old)-set(aliases)=={'water','acetic-acid'} or set(old)-set(aliases)=={'water','acetic acid'}
    rows=[]
    for key,s in sorted(old.items()):
        a=aliases.get(key)
        pin=Path(s['B']).stem
        if a:
            assert a['connectivity_match']=='True'
            assert a['input_inchikey'].split('-')[0]==a['panel_input_inchikey'].split('-')[0]
            assert a['old_surface_sha256']==pin
        rows.append(dict(product_solvent_key=key,common_solvent_alias=a['common_solvent_key'] if a else '',
            release='promotion-v1',surface_sha256=pin,identity_basis='retained panel surface',
            input_inchikey=a['panel_input_inchikey'] if a else '',replacement_surface_used=False))
    results={}
    for p in (library/'results').glob('*/verified-result.json'):
        value=json.loads(p.read_text());results[value['input']['name'].lower()]=(p,value)
    for key,s in sorted(new.items()):
        assert key==key.lower() and key in common
        p,r=results[key]
        assert sha(p)==s['verified_result_sha256']
        assert r['status']=='converged' and r['connectivity_match']
        assert r['inchikey']==s['inchikey'] and r['surface_sha256']==s['surface_sha256']
        assert sha(p.parent/'surface.orcacosmo')==s['surface_sha256']
        rows.append(dict(product_solvent_key=key,common_solvent_alias=key,
            release='promotion-ext39-v1',surface_sha256=s['surface_sha256'],
            identity_basis=s['identity_match_basis'],input_inchikey=s['inchikey'],replacement_surface_used=False))
    assert len(rows)==len({r['product_solvent_key'] for r in rows})==71
    out=extension/'solvent-union-inventory';out.mkdir(exist_ok=False)
    with (out/'solvent-union.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        status='identity_surface_union_verified_predictions_pending',common_identities=69,
        retained_panel=32,common_identities_in_panel=30,new_common_identities=39,union_solvent_keys=71,
        extra_panel_keys=sorted(set(old)-set(aliases)),
        source_pins={k:dict(path=str(p),sha256=sha(p)) for k,p in sources.items()},
        inventory_sha256=sha(out/'solvent-union.csv'),script_sha256=sha(Path(__file__)),
        caveats=['This inventory does not certify extension prediction completion or experimental accuracy.',
                 'Original xylene identity question remains open; no surface or key was changed.',
                 'Dipentene uses the computed limonene stereoisomer, not a commercial mixture.',
                 'See qualified liquid-volume references for subcooled-liquid phase caveats.'])
    (out/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
