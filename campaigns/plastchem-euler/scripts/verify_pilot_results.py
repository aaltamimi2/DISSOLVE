"""Verify retrieved payload and geometry identity; never invent missing results."""
import csv,hashlib,json,sys
from pathlib import Path
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds
from geometry_identity import verify as verify_geometry
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'state/pilot-v1/manifest.json').read_text())
snapshot=json.loads((ROOT/'state/pilot-v1/latest-snapshot.json').read_text())
# Explicit user-approved policy file may permit preserving unspecified input stereo.
policy_path=ROOT/'state/pilot-v1/identity-policy.json'
policy=json.loads(policy_path.read_text()) if policy_path.exists() else {'mode':'exact_full_inchikey'}
accounting={}
for line in snapshot['sacct'].splitlines():
 parts=line.split('|')
 if len(parts)>=11:accounting[parts[0]]=parts
terminal_failures={'FAILED','TIMEOUT','OUT_OF_MEMORY','CANCELLED','NODE_FAIL','PREEMPTED','BOOT_FAIL','DEADLINE'}
exception_path=ROOT/'state/pilot-v1/preflight-exceptions.json'
exceptions=json.loads(exception_path.read_text()) if exception_path.exists() else {}
cache_path=ROOT/'state/pilot-v1/identity-verification-cache.json'
cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
method_sha=hashlib.sha256((ROOT/'scripts/geometry_identity.py').read_bytes()).hexdigest()
records={};counts={'converged':0,'failed':0,'not_yet_run':0,'running':0,'awaiting_verification':0,'denominator':56}
def perceived_xyz(path):
 m=Chem.MolFromXYZFile(str(path));rdDetermineBonds.DetermineBonds(m,charge=0);return m
for mol in manifest['molecules']:
 key=mol['inchikey'];r=snapshot['results'].get(key)
 task=str(snapshot['array_job_id'])+'_'+str(mol['array_index']);acct=accounting.get(task)
 scheduler_failed=bool(acct and acct[3].split()[0] in terminal_failures)
 if key in exceptions:
  r=dict(exceptions[key],input=mol,cpu_model=None,node=None)
 if scheduler_failed and (r is None or r.get('status') not in ['failed','converged_identity_pending','converged']):
  r=dict(r or {'input':mol,'inchikey':key,'cpu_model':None})
  r.update(status='failed',failure_mode='slurm_'+acct[3].lower().replace(' ','_'),error='Scheduler terminal failure; elapsed measurement may be censored',slurm_accounting_raw=acct)
 if r is None:
  records[key]={'status':'not_yet_run','input':mol};counts['not_yet_run']+=1;continue
 r=dict(r);p=Path('/mnt/r/plastchem-euler/results')/key
 batch=accounting.get(task+'.batch')
 if acct:
  r['slurm_accounting']={'state':acct[3],'exit_code':acct[4],'elapsed_seconds':int(acct[5]) if acct[5] else None,'node_list':acct[8]}
  if batch and batch[7]:r['slurm_accounting']['maxrss_kib']=float(batch[7].rstrip('K'));r['slurm_accounting']['maxrss_source']='sacct batch step'
 if r['status']=='converged_identity_pending' and (p/'surface.orcacosmo').exists():
  r['dft_status']='converged'
  try:
   for file,digest in [('surface.orcacosmo',r['surface_sha256']),*[(s+'.inp',v['input_sha256']) for s,v in r['stages'].items()]]:
    assert hashlib.sha256((p/file).read_bytes()).hexdigest()==digest,file+' digest mismatch'
   signature=hashlib.sha256(json.dumps({'xyz_sha256':hashlib.sha256((p/'optimized.xyz').read_bytes()).hexdigest(),'expected':key,'smiles':mol['smiles'],'policy':policy,'method_sha':method_sha},sort_keys=True).encode()).hexdigest()
   if cache.get(key,{}).get('signature')==signature:observation=cache[key]['observation']
   else:
    observation=verify_geometry(p/'optimized.xyz',key,mol['smiles'],policy['mode']);cache[key]={'signature':signature,'observation':observation}
   r.update(observation)
   if not observation['identity_verified']:
    keys=[x.get('full_inchikey') for x in observation['identity_observations'] if x.get('full_inchikey')]
    r['failure_mode']='identity_unresolved_stereochemistry' if keys and any(x.split('-')[0]==key.split('-')[0] for x in keys) else 'identity_unresolved_connectivity'
    r['dft_status']='converged'
    raise ValueError('Geometry identity not accepted under current policy; see identity_observations')
   r.update(status='converged',identity_verified=True,archive_path=str(p))
  except Exception as exc:r.update(status='failed',failure_mode=r.get('failure_mode','return_integrity_or_geometry_parse'),error=str(exc),identity_verified=False)
  (p/'result.json').write_text(json.dumps(r,indent=2)+'\n')
  r['returned_bytes']=sum(f.stat().st_size for f in p.iterdir() if f.is_file())
 if p.exists():r['returned_bytes']=sum(f.stat().st_size for f in p.iterdir() if f.is_file())
 status=r['status']
 if status=='converged':counts['converged']+=1
 elif status=='failed':counts['failed']+=1
 elif status=='converged_identity_pending':counts['awaiting_verification']+=1
 else:counts['running']+=1
 records[key]=r
out={'phase':1,'identity_policy':policy,'counts':counts,'records':records}
(ROOT/'state/pilot-v1/verified-state.json').write_text(json.dumps(out,indent=2)+'\n')
campaign_path=ROOT/'state/campaign-status.json'
campaign=json.loads(campaign_path.read_text())
for key,r in records.items():
 entry=campaign['structures'][key]
 entry.update(status='pilot_pending' if r['status']=='not_yet_run' else r['status'],phase=1,array_job_id=snapshot['array_job_id'],array_task_id=r['input']['array_index'])
 for field in ['failure_mode','dft_status','cpu_model','node','archive_path','inchikey_after_optimization','identity_verified']:
  if field in r:entry[field]=r[field]
  elif field in entry:entry.pop(field)
campaign.update(phase=1,pilot_counts=counts,full_campaign_jobs_submitted=0)
overall={'converged':0,'failed':0,'running':0,'awaiting_verification':0,'pilot_pending':0,'not_yet_run':0,'denominator':5833}
for entry in campaign['structures'].values():
 status=entry['status']
 category='awaiting_verification' if status=='converged_identity_pending' else status if status in ['converged','failed','pilot_pending','not_yet_run'] else 'running'
 overall[category]+=1
campaign['counts']=overall
campaign_path.write_text(json.dumps(campaign,indent=2)+'\n')
cache_path.write_text(json.dumps(cache,indent=2)+'\n')
print(json.dumps(counts))
