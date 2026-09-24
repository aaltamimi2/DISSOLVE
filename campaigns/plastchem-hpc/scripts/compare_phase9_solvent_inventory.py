"""Connectivity overlap and surface versions; no thermodynamic calculation."""
import csv,datetime,hashlib,json
from pathlib import Path
D=Path('/mnt/r/plastchem-euler/phase9-solvent-library-v1');BASE=D.parent/'phase8-v1'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    registry=json.loads((D/'retrieved.json').read_text());common={}
    for key,pin in registry.items():
        p=D/'results'/key/'verified-result.json';assert sha(p)==pin['result_sha256']
        r=json.loads(p.read_text());block=key.split('-')[0];assert block not in common
        common[block]=dict(common_solvent_key=r['input']['name'],input_inchikey=key,new_surface_sha256=r['surface_sha256'])
    identities=json.loads((BASE/'validation-inputs.json').read_text())['solvent_identities']
    manifest=json.loads((BASE/'manifest.json').read_text());phases={s['name']:s for s in manifest['solvents']}
    panel={};rows=[]
    for name,identity in identities.items():
        block=identity['identity'].split('-')[0];assert block not in panel
        panel[block]=name
        if block in common:
            old=sha(BASE/phases[name]['B']);c=common[block]
            rows.append(dict(panel_solvent_key=name,**c,panel_input_inchikey=identity['identity'],old_surface_sha256=old,
                             connectivity_match=True,surface_bytes_identical=old==c['new_surface_sha256']))
    assert len(common)==69 and len(panel)==32
    path=D/'common-panel-connectivity-overlap.csv'
    with path.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),panel_identities=len(panel),common_identities=len(common),
                connectivity_overlap=len(rows),panel_only=[panel[k] for k in sorted(panel.keys()-common.keys())],
                common_only_count=len(common.keys()-panel.keys()),union_identities=len(common.keys()|panel.keys()),
                byte_identical_overlap_surfaces=sum(r['surface_bytes_identical'] for r in rows),
                overlap_table=str(path),overlap_table_sha256=sha(path),
                scope='Pinned panel identities and actual verified common-solvent identities matched on first InChIKey block. Matching identity does not authorize substituting a differently hashed surface or reusing its predictions. No generic-xylene policy decision.')
    (D/'common-panel-overlap-summary.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':main()
