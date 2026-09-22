"""Full-snapshot return provenance, surface geometry and numerical structure checks."""
import os
os.environ['OMP_NUM_THREADS']='1';os.environ['OPENBLAS_NUM_THREADS']='1'
import argparse,gc,hashlib,json,time
from pathlib import Path
import numpy as np
from opencosmorspy.input_parsers import SigmaProfileParser
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'state/thermodynamics-v1';rows=[]
parser=argparse.ArgumentParser();parser.add_argument('--frozen',action='store_true');parser.add_argument('--support-solvents',action='store_true');args=parser.parse_args()
source=ROOT
if args.frozen:
 source=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14')) / 'freeze'
 P=Path(os.environ.get('PLASTCHEM_PROGRESS_ROOT', '/mnt/r/plastchem-euler/progress-2026-09-14'))
record_folder='state/solvent-library-v1/records' if args.support_solvents else 'state/campaign-v1/records'
for f in sorted((source/record_folder).glob('*.json')):
 r=json.loads(f.read_text())
 if r.get('status')!='converged':continue
 row={'inchikey':f.stem,'name':r['input']['name'],'cpu_model':r.get('cpu_model'),'source_record_sha256':hashlib.sha256(f.read_bytes()).hexdigest()}
 try:
  d=Path(r['archive_path']);surface=d/'surface.orcacosmo';sha=hashlib.sha256(surface.read_bytes()).hexdigest();assert sha==r['surface_sha256'],'Surface digest'
  for stage,data in r['stages'].items():assert hashlib.sha256((d/(stage+'.inp')).read_bytes()).hexdigest()==data['input_sha256'],'Input deck digest'
  assert r['orca_version']=='6.1.1' and r['orca_git']=='487d211c'
  assert r['cpu_model']=='AMD EPYC 7763 64-Core Processor'
  assert r['connectivity_match'] and r['perceived_inchikey'].split('-')[0]==f.stem.split('-')[0]
  assert r['perceived_keys_by_engine'] and r['perception_engines_agreeing_on_perceived_key']
  parsed=SigmaProfileParser(str(surface));xyz=(d/'optimized.xyz').read_text().splitlines();atoms=[line.split() for line in xyz[2:] if line.strip()];pos=np.array([[float(v) for v in a[1:]] for a in atoms])
  assert len(atoms)==r['input']['atoms']==len(parsed['atm_elmnt'])
  assert list(parsed['atm_elmnt'])==[a[0] for a in atoms],'Element ordering'
  delta=float(np.max(np.abs(pos-parsed['atm_pos'])));assert delta<=1e-5,'Surface / optimized geometry mismatch'
  areas=np.asarray(parsed['seg_area']);assert len(areas)>0 and np.all(np.isfinite(areas)) and np.all(areas>0)
  assert np.isfinite(parsed['area']) and parsed['area']>0 and np.isfinite(parsed['volume']) and parsed['volume']>0
  relative=abs(float(areas.sum())-parsed['area'])/parsed['area'];assert relative<1e-4,'Surface area sum mismatch'
  sigma=np.asarray(parsed['seg_sigma_raw']);assert np.all(np.isfinite(sigma))
  row.update(status='passed',surface_sha256=sha,atoms=len(atoms),segments=len(areas),surface_geometry_max_delta_angstrom=delta,area_relative_sum_error=relative,integrated_surface_charge_e=float(np.dot(areas,sigma)))
 except Exception as exc:row.update(status='failed',error=str(exc))
 rows.append(row);gc.collect()
out={'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'completed_snapshot':len(rows),'passed':sum(r['status']=='passed' for r in rows),'failed':sum(r['status']=='failed' for r in rows),'rows':rows}
output_name='support-solvent-surface-audit.json' if args.support_solvents else 'completed-surface-audit.json'
(P/output_name).write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k!='rows'}))
for r in rows:
 if r['status']=='failed':print(json.dumps(r))
