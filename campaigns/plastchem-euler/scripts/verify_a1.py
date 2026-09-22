from pathlib import Path
import hashlib,json
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
root=Path(__file__).resolve().parents[1]
state=json.loads((root/'state/a1-status.json').read_text())
for key,row in state['diagnostics'].items():
 p=Path('/mnt/r/plastchem-euler/results')/key
 result=json.loads((p/'result.json').read_text())
 assert result['status']=='converged_identity_pending'
 assert result['partition']=='research' and result['cpu_generation_constraint']=='milan'
 assert result['job_id']==row['job_id'] and 'avx2' in result['cpu_flags']
 assert hashlib.sha256((p/'surface.orcacosmo').read_bytes()).hexdigest()==result['surface_sha256']
 for stage in ['opt','cosmo']:
  assert hashlib.sha256((p/f'{stage}.inp').read_bytes()).hexdigest()==result['stages'][stage]['input_sha256']
 m=Chem.MolFromXYZFile(str(p/'optimized.xyz'));rdDetermineBonds.DetermineBonds(m,charge=0)
 actual=Chem.MolToInchiKey(m);assert actual==key,(actual,key)
 result.update(status='converged',inchikey_after_optimization=actual,identity_verified=True,archive_path=str(p),scope='A-1 diagnostic, not campaign')
 result['returned_payload_bytes_excluding_json']=sum(f.stat().st_size for f in p.iterdir() if f.is_file() and f.name!='result.json')
 (p/'result.json').write_text(json.dumps(result,indent=2)+'\n')
 (root/'state'/f'a1-{key}.json').write_text(json.dumps(result,indent=2)+'\n')
 row.update(status='converged',identity_verified=True,node=result['node'],cpu_model=result['cpu_model'],cpu_generation='milan',surface_bytes=result['surface_bytes'],surface_sha256=result['surface_sha256'],returned_bytes=sum(f.stat().st_size for f in p.iterdir() if f.is_file()),stages=result['stages'],children_maxrss_kib=result['children_maxrss_kib'])
 print(result['input']['name'],row['job_id'],row['node'],row['cpu_model'],{s:round(v['wall_seconds'],3) for s,v in row['stages'].items()},row['surface_bytes'],row['returned_bytes'])
state.update(status='diagnostics_verified',counts={'converged':2,'failed':0,'not_yet_run':0,'denominator':2},phase1_started=False)
(root/'state/a1-status.json').write_text(json.dumps(state,indent=2)+'\n')
