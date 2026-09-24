"""Fetch only newly finished return bundles via the prescribed multiplexed scp route."""
import json,subprocess
from pathlib import Path
from euler_transport import run
ROOT=Path(__file__).resolve().parents[1];STATE=ROOT/'state/pilot-v1'
snap=json.loads((STATE/'latest-snapshot.json').read_text());dest=Path('/mnt/r/plastchem-euler/results');dest.mkdir(parents=True,exist_ok=True)
ledger=STATE/'retrieved.json';done=json.loads(ledger.read_text()) if ledger.exists() else {}
ready=[key for key,r in snap['results'].items() if r['status'] in ['converged_identity_pending','failed'] and key not in done]
if not ready:print('No newly completed bundles.');raise SystemExit(0)
subprocess.run(['df','-h','/','/mnt/r'],check=True)
args=['-rq',*[f'euler:plastchem-euler/pilot-v1/returns/{key}' for key in ready],str(dest)];r=run('scp',args,capture_output=True,text=True)
if r.returncode:raise RuntimeError(f'scp failed; do not mark retrieval complete: {r.stderr}')
for key in ready:
 r=json.loads((dest/key/'result.json').read_text())
 assert r['array_job_id']==snap['array_job_id'] and r['inchikey']==key
 if r['status']=='converged_identity_pending':assert {f.name for f in (dest/key).iterdir()}=={'surface.orcacosmo','result.json','opt.inp','cosmo.inp','optimized.xyz'}
 done[key]={'remote_status':r['status'],'array_job_id':snap['array_job_id'],'retrieved_at_snapshot_utc':snap['utc']}
ledger.write_text(json.dumps(done,indent=2)+'\n');print('Retrieved:',len(ready),'new bundles;',len(done),'total')
