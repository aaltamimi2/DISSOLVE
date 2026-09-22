"""Apply authorised A-3 acceptance to existing pilot surfaces; preserve prior JSON evidence."""
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/campaign-v1'
history=P/'reused-original-result-json';history.mkdir(exist_ok=True)
pilot=json.loads((ROOT/'state/pilot-v1/verified-state.json').read_text())
count=0
for file in (P/'records').glob('*.json'):
    r=json.loads(file.read_text())
    if r.get('reused_from')!='phase1_pilot':continue
    key=r['inchikey'];folder=Path('/mnt/r/plastchem-euler/results')/key;original=history/f'{key}.json'
    if not original.exists():original.write_bytes((folder/'result.json').read_bytes())
    r.update(archive_path=str(folder),original_phase1_status=pilot['records'][key]['status'],original_result_json_sha256=hashlib.sha256(original.read_bytes()).hexdigest())
    assert r['identity_verified'] and r['perceived_inchikey'].split('-')[0]==key.split('-')[0]
    assert hashlib.sha256((folder/'surface.orcacosmo').read_bytes()).hexdigest()==r['surface_sha256']
    (folder/'result.json').write_text(json.dumps(r,indent=2)+'\n')
    r['returned_bytes']=sum(p.stat().st_size for p in folder.iterdir() if p.is_file())
    file.write_text(json.dumps(r,indent=2)+'\n');count+=1
assert count==55
print(f'Adopted {count} pilot results under A-3; old JSON preserved locally, no DFT rerun.')
