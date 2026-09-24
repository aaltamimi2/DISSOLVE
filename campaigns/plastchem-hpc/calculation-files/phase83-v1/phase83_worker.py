"""8.3 one contaminant per task; identical 8.2 LLE, factorized 8.1 activities.

No recalibration, no new DFT, no product writes. Each unit is durable and input-bound.
"""
import os,sys,json,time,math,hashlib,traceback,gzip,shutil
from pathlib import Path
for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS']:os.environ[k]='1'
D=Path(__file__).resolve().parent
BASE=D.parent/'phase8-v1'
sys.path.insert(0,str(BASE))
import phase8_worker as w
import phase82_worker as lle

def pack_grid(out):
 p=out/'activity-grid.json'
 if p.exists():
  dest=p.with_suffix('.json.gz');tmp=dest.with_suffix('.tmp')
  with p.open('rb') as src,gzip.open(tmp,'wb',compresslevel=3) as dst:shutil.copyfileobj(src,dst)
  tmp.replace(dest);p.unlink()

def main(idx):
 if not os.environ.get('SLURM_JOB_ID'):raise SystemExit('Euler allocation required')
 start=time.monotonic();cohort=json.loads((D/'cohort.json').read_text());c=cohort['rows'][idx]
 assert c['index']==idx
 signature={'cohort_sha256':w.sha(D/'cohort.json'),'driver_sha256':w.sha(__file__),
  'activity_worker_sha256':w.sha(BASE/'phase8_worker.py'),'lle_worker_sha256':w.sha(BASE/'phase82_worker.py'),
  'solver_sha256':w.sha(BASE/'phase8_lle.py')}
 assert signature['solver_sha256']=='1fee9216925b91877eaf79840e010330801b93adf41b0b116c774358df0296f9'
 assert signature['activity_worker_sha256']=='a48dd38ebf1f71b53abf448a740b0c73c1aff2f2090368b4da0fd4e56284a1fb'
 assert signature['lle_worker_sha256']=='bdb4149ad4748fa6998fd210e510512a086af849591c4dff2bbd5a1725df1f31'
 manifest=json.loads((BASE/'manifest.json').read_text());validation=json.loads((BASE/'validation-inputs.json').read_text())
 assert w.sha(BASE/'manifest.json')==cohort['phase81_manifest_sha256']
 assert w.sha(BASE/'validation-inputs.json')==cohort['phase82_validation_sha256']
 solute=str(D/c['B']);assert w.sha(solute)==c['surface_sha256']
 for s in manifest['solvents']:s['B']=str(BASE/s['B'])
 out=D/'results'/f'{idx:05d}';out.mkdir(parents=True,exist_ok=True)
 if (out/'signature.json').exists():assert json.loads((out/'signature.json').read_text())==signature
 else:w.save(out/'signature.json',signature)
 if (out/'complete.json').exists():return
 activities=out/'activities';solvents={}
 for s in manifest['solvents']:
  try:solvents[s['name']]=w.activity('B',solute,s['B'],activities/('solvent-'+s['name']+'.json'))
  except Exception as e:solvents[s['name']]={'status':'failed','error':str(e)}
 for polymer,ensemble in manifest['polymers'].items():
  csvpath=out/(polymer+'.csv')
  if csvpath.exists():continue
  e=None;failure=None
  try:
   acts=[w.activity('B',solute,r['B'],activities/('polymer-'+r['entry_id']+'.json')) for r in ensemble]
   if all(a['status']=='converged' for a in acts):e=w.ensemble_calc('B',ensemble,acts)
   else:failure='polymer_activity_not_converged'
   del acts
  except Exception as ex:failure=type(ex).__name__+': '+str(ex)
  w.cleanup();rows=[]
  for s in manifest['solvents']:
   a=solvents[s['name']]
   for convention in ['normalized','existing']:
    row={'inchikey':c['inchikey'],'tier':c['tier'],'polymer':polymer,'solvent':s['name'],'convention':convention,'temperature_K':298.15,
     'status':'failed','solute_surface_sha256':c['surface_sha256'],'solvent_surface_sha256':w.sha(s['B']),
     'polymer_ensemble_manifest_sha256':cohort['phase81_manifest_sha256']}
    if e is not None and a['status']=='converged':
     lng=a['ln_gamma'] if convention=='normalized' else a['samples'][0]['ln_gamma'];vp=e[convention+'_volume'];vs=s['B_volume' if convention=='normalized' else 'B_legacy_volume'];kx=(e[convention+'_gamma']-lng)/math.log(10)
     row.update(status='predicted',logP_x=kx,logP_concentration=kx+math.log10(vp/vs),ln_gamma_polymer=e[convention+'_gamma'],ln_gamma_solvent=lng,polymer_volume_cm3_mol=vp,solvent_volume_cm3_mol=vs)
    else:row['failure_mode']=failure or a['status']
    rows.append(row)
  w.csvsave(csvpath,rows);print('POLYMER_DONE',idx,polymer,flush=True)
 partition_seconds=time.monotonic()-start
 # Invoke the unmodified validated worker; only input selection and output root differ.
 lle.D=D;validation['solutes']={c['inchikey']:dict(c,B=solute)}
 units=[]
 for s in manifest['solvents']:
  source=next(u for u in validation['lle_units'] if u['solvent']==s['name'] and u['regime']=='high')
  for regime,T in [('RT',298.15),('high',source['temperature_K'])]:
   units.append({'solute':c['inchikey'],'solvent':s['name'],'regime':regime,'temperature_K':T,'literal_high_temperature_C':source['literal_high_temperature_C']})
 class Units:
  def __getitem__(self,n):assert idx*64<=n<(idx+1)*64;return units[n-idx*64]
 validation['lle_units']=Units();statuses={};lle_start=time.monotonic()
 for local,u in enumerate(units):
  n=idx*64+local;folder=D/'phase82/lle'/f'{n:03d}'
  try:
   lle.lle_unit(manifest,validation,n)
   result=json.loads((folder/'result.json').read_text());statuses[str(n)]=result['status'];pack_grid(folder)
  except Exception as e:
   statuses[str(n)]='failed';w.save(folder/'failure.json',dict(u,status='failed',error=type(e).__name__+': '+str(e),traceback=traceback.format_exc(),signature=signature,execution=w.provenance()))
  w.cleanup();print('LLE_DONE',n,statuses[str(n)],flush=True)
 w.save(out/'complete.json',dict(signature=signature,index=idx,inchikey=c['inchikey'],tier=c['tier'],stratum=c['stratum'],
  wall_seconds=time.monotonic()-start,partition_seconds=partition_seconds,lle_seconds=time.monotonic()-lle_start,lle_statuses=statuses,
  outputs={p.name:w.sha(p) for p in out.glob('*.csv')},execution=w.provenance()))
if __name__=='__main__':
 idx=int(sys.argv[1])
 if len(sys.argv)>2:idx=json.loads(Path(sys.argv[2]).read_text())[idx]
 try:main(idx)
 except Exception as e:w.save(D/'results'/f'{idx:05d}'/'task-failure.json',dict(error=str(e),traceback=traceback.format_exc(),execution=w.provenance()));raise
