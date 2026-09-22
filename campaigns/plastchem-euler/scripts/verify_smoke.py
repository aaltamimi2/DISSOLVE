from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
import hashlib, json
p=Path('/mnt/r/plastchem-euler/phase0')
r=json.loads((p/'result.json').read_text())
m=Chem.MolFromXYZFile(str(p/'water.opt.xyz'))
rdDetermineBonds.DetermineBonds(m,charge=0)
key=Chem.MolToInchiKey(m)
assert key=='XLYOFNOQVPJJNP-UHFFFAOYSA-N',key
surf=p/'water_cosmo.solute.orcacosmo'
assert hashlib.sha256(surf.read_bytes()).hexdigest()==r['surface_sha256']
for stage in ['opt','cosmo']:
    out=(p/f'water_{stage}.out').read_text()
    assert 'ORCA TERMINATED NORMALLY' in out
    assert 'Program Version 6.1.1' in out and '487d211c' in out
r.update(status='converged',inchikey_after_optimization=key,expected_inchikey=key,identity_verified=True,archive_path=str(p),execution_context='login node; no scheduler submission',archive_sha256={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.iterdir() if f.is_file() and f.name!='result.json'})
(p/'result.json').write_text(json.dumps(r,indent=2)+'\n')
Path('state/phase0-smoke.json').write_text(json.dumps(r,indent=2)+'\n')
print('Post-optimisation identity and surface digest verified:',key)
