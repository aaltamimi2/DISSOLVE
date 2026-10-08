"""Mirror verified returned surfaces at the frozen remote manifest's local paths.

Bulk-only copies; no manifest, numerical code, source record or git data changes.
"""
import datetime
import hashlib
import json
from pathlib import Path

D=Path('/mnt/r/plastchem-euler/phase10-v1')
LIB=D.parent/'phase9-solvent-library-v1'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest=json.loads((D/'manifest.json').read_text())
    registry=json.loads((LIB/'retrieved.json').read_text())
    rows=[]
    for solvent in manifest['solvents']:
        key=solvent['inchikey'];entry=registry[key]
        source=LIB/'results'/key
        target=(D/solvent['surface']).resolve()
        assert target==LIB/'returns'/key/'surface.orcacosmo'
        assert entry['status']=='converged'
        assert sha(source/'verified-result.json')==entry['result_sha256']==solvent['verified_result_sha256']
        assert sha(source/'surface.orcacosmo')==entry['surface_sha256']==solvent['surface_sha256']
        record=json.loads((source/'verified-result.json').read_text())
        assert record['surface_sha256']==solvent['surface_sha256']
        target.parent.mkdir(parents=True,exist_ok=True)
        copied=[]
        for name in ['surface.orcacosmo','verified-result.json']:
            src=source/name;dst=target.parent/name
            if dst.exists():assert sha(dst)==sha(src),'Never overwrite a conflicting surface/reference'
            else:
                with dst.open('xb') as stream:stream.write(src.read_bytes())
                copied.append(name)
            assert sha(dst)==sha(src)
        rows.append(dict(name=solvent['name'],source=str(source),target=str(target.parent),copied=copied,
            surface_sha256=solvent['surface_sha256'],verified_result_sha256=solvent['verified_result_sha256']))
    assert len(rows)==39
    result=dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),status='all_manifest_surface_paths_verified',
        count=39,manifest_sha256=sha(D/'manifest.json'),registry_sha256=sha(LIB/'retrieved.json'),rows=rows,
        scope='Byte-identical bulk-only local layout repair. Frozen manifest and all original surfaces/records remain unchanged.')
    (D/'local-surface-path-staging.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2))


if __name__=='__main__':main()
